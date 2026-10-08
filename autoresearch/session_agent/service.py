"""Application service for subscription-session research task graphs."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path

from autoresearch.common import workspace as ws
from autoresearch.common.atomic import (
    atomic_write_json,
    canonical_json,
    sha256_bytes,
    sha256_file,
)
from autoresearch.contracts.session_task import (
    validate_begin_request,
    validate_submission,
    validate_tool_result,
)
from autoresearch.session_agent import artifacts, executor, plan as plan_service, store
from autoresearch.session_agent.hosts.base import render_request, validate_receipt
from autoresearch.session_agent.publication import publish as publish_run, session_profile
from autoresearch.session_agent.roles import role_manifest, role_stage
from autoresearch.session_agent.validation import (
    validate_registered_contract,
    validate_submission_outputs,
)


def _session_dir(handle) -> Path:
    return Path(handle.workspace) / "session"


def _plan_path(handle) -> Path:
    return _session_dir(handle) / "plan.json"


def _store_path(handle) -> Path:
    return _session_dir(handle) / "tasks.json"


def _read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def _freeze_json(path: Path, value: dict) -> Path:
    if path.is_file():
        current = _read_json(path)
        if canonical_json(current) != canonical_json(value):
            raise RuntimeError(f"frozen session input changed: {path.name}")
        return path
    return atomic_write_json(path, value)


def _mirror_identity(handle, name: str, value: dict) -> None:
    _freeze_json(_session_dir(handle) / name, value)
    _freeze_json(Path(handle.capsule) / "identity" / "session" / name, value)


def _load_plan(handle) -> dict:
    value = _read_json(_plan_path(handle))
    from autoresearch.contracts.session_plan import validate_plan

    return validate_plan(value)


def _all_tasks(handle, frozen_plan: dict | None = None) -> list[dict]:
    current = frozen_plan or _load_plan(handle)
    tasks = list(current["tasks"])
    paths = []
    for folder in ("expansions", "recoveries"):
        root = _session_dir(handle) / folder
        if root.is_dir():
            paths.extend(sorted(root.glob("*.json")))
    pending = [_read_json(path) for path in paths]
    while pending:
        known = {task["task_id"] for task in tasks}
        ready = []
        waiting = []
        for expansion in pending:
            own = {task["task_id"] for task in expansion.get("tasks", [])}
            required = {
                dependency
                for task in expansion.get("tasks", [])
                for dependency in task.get("dependencies", [])
            } - own
            (ready if required <= known else waiting).append(expansion)
        if not ready:
            missing = sorted(
                {
                    dependency
                    for expansion in waiting
                    for task in expansion.get("tasks", [])
                    for dependency in task.get("dependencies", [])
                    if dependency not in known
                    and dependency not in {item["task_id"] for item in expansion.get("tasks", [])}
                }
            )
            raise ValueError(f"expanded task has missing dependencies: {missing}")
        for expansion in ready:
            tasks = plan_service.apply_expansion(current, expansion, existing_tasks=tasks)
        pending = waiting
    return tasks


def _task(handle, task_id: str) -> dict:
    matches = [task for task in _all_tasks(handle) if task["task_id"] == task_id]
    if len(matches) != 1:
        raise KeyError(task_id)
    return matches[0]


def _result(
    command: str,
    run_id: str,
    state: str,
    *,
    tasks: list[dict] | None = None,
    result: object = None,
    errors: list[dict] | None = None,
) -> dict:
    value = {
        "schema_version": 1,
        "command": command,
        "run_id": run_id,
        "state": state,
        "tasks": tasks or [],
        "result": result,
        "errors": errors or [],
    }
    return validate_tool_result(value)


def _default_begin_capsule(request: dict):
    kind = request["kind"]
    if kind == "stock-research":
        from functools import partial

        from autoresearch.analyze import runctl
        from autoresearch.analyze.run_bootstrap import prepare_analyze_run
        from autoresearch.session_agent.config import load_orchestration_config
        from autoresearch.trace.capsule import begin_run

        bootstrap = partial(
            prepare_analyze_run,
            orchestration_config=load_orchestration_config(engine=ws.ENGINE),
        )
        if ws.ENGINE == "codex":
            runctl._warn_if_codex_rollout_missing()
        handle = begin_run(
            kind,
            request["analysis_date"],
            request["host_profile"]["engine"],
            {
                "mode": request["requested_mode"],
                "ticker": request["subject"],
                "peers": request["peers"],
                "asset_type": request["asset_type"],
                **({"name": request["name"]} if request["name"] else {}),
            },
            session_ref=request["host_profile"]["session_ref"],
            bootstrap=bootstrap,
        )
        if ws.ENGINE == "codex":
            runctl._record_codex_escape_hatch(handle)
        return handle
    if kind == "macro-research":
        from autoresearch.macro.run_bootstrap import prepare_macro_run
        from autoresearch.trace.capsule import begin_run

        return begin_run(
            kind,
            request["analysis_date"],
            request["host_profile"]["engine"],
            {"mode": request["requested_mode"]},
            session_ref=request["host_profile"]["session_ref"],
            bootstrap=prepare_macro_run,
        )
    if kind == "sector-research":
        from autoresearch.sector.run_bootstrap import prepare_sector_run
        from autoresearch.trace.capsule import begin_run

        return begin_run(
            kind,
            request["analysis_date"],
            request["host_profile"]["engine"],
            {"mode": request["requested_mode"], "industry": request["subject"]},
            session_ref=request["host_profile"]["session_ref"],
            bootstrap=prepare_sector_run,
        )
    if kind == "dossier-init":
        from autoresearch.dossier.run_bootstrap import prepare_dossier_run
        from autoresearch.trace.capsule import begin_run

        config = {"mode": "INIT", "code": request["subject"]}
        if request.get("name"):
            config["name"] = request["name"]
        return begin_run(
            kind,
            request["analysis_date"],
            request["host_profile"]["engine"],
            config,
            session_ref=request["host_profile"]["session_ref"],
            bootstrap=prepare_dossier_run,
        )
    if kind != "scan-market":
        raise ValueError(f"run kind is not registered with workspace yet: {kind}")
    from autoresearch.trace.capsule import begin_run

    return begin_run(
        kind,
        request["analysis_date"],
        request["host_profile"]["engine"],
        None,
        session_ref=request["host_profile"]["session_ref"],
    )


def _default_planner(request: dict, handle) -> dict:
    from autoresearch.session_agent.workflows import build_plan

    return build_plan(request, handle)


def _predecessor_evidence(request: dict, loader=None) -> dict | None:
    predecessor_id = request["predecessor_run_id"]
    if predecessor_id is None:
        return None
    if loader is None:
        from autoresearch.trace.capsule import load_run

        loader = load_run
    predecessor = loader(predecessor_id)
    if predecessor.engine != ws.ENGINE:
        raise ValueError("predecessor engine does not match current engine")
    state_path = Path(getattr(predecessor, "workspace", "")) / "state.json"
    if state_path.is_file():
        state = _read_json(state_path)
        business_status = str(state.get("business_status") or "")
    else:
        business_status = str(getattr(predecessor, "business_status", ""))
    if business_status == "ACTIVE" or not business_status:
        raise ValueError("predecessor must be a terminal run")
    value = {
        "schema_version": 1,
        "run_id": predecessor_id,
        "engine": predecessor.engine,
        "business_status": business_status,
    }

    if request.get("schema_version", 1) >= 3:
        from autoresearch.contracts.execution import validate_decision_frame
        from autoresearch.session_agent import artifacts

        prior_request = _read_json(Path(predecessor.workspace) / "session/request.json")
        if prior_request["subject"] != request["subject"] or prior_request["kind"] != request["kind"]:
            raise ValueError("review subject differs from predecessor")
        snapshot = artifacts.snapshot_artifact(predecessor, "research.frame")
        with artifacts.open_artifact(predecessor, "research.frame") as stream:
            raw = stream.read()
        frame = validate_decision_frame(json.loads(raw))
        if frame["analysis_session"] != request["analysis_date"]:
            raise ValueError("review must retain predecessor analysis anchor")
        if frame["venue"] != request["research_context"]["venue"]:
            raise ValueError("review venue differs from predecessor")
        value.update(frame_json=raw.decode("utf-8"), frame_sha256=snapshot["sha256"])
    return value


def begin(
    request: dict,
    *,
    begin_capsule: Callable[[dict], object] | None = None,
    planner: Callable[[dict, object], dict] | None = None,
    predecessor_loader=None,
    artifact_registrar=None,
    executor: str = "mailbox",
) -> dict:
    """Validate and freeze a request before exposing its first ready task.

    ``executor`` declares who will dispatch the inference tasks (``mailbox`` = the host's
    native agent tool, the default; ``headless`` = ``claude -p`` with explicit model/effort).
    It is frozen in ``role_support.json`` and reused by later expansions.
    """
    validate_begin_request(request, expected_engine=ws.ENGINE)
    from autoresearch.session_agent.origin import (
        freeze_session_origin,
        preflight_session_host,
    )

    preflight_session_host(request)
    predecessor = _predecessor_evidence(request, predecessor_loader)
    handle = (begin_capsule or _default_begin_capsule)(request)
    if handle.engine != ws.ENGINE:
        raise ValueError("capsule engine does not match process engine")
    from autoresearch.trace.completeness import freeze_card_rules

    freeze_card_rules(handle.capsule, kind=request["kind"], config=getattr(handle.contract, "user_config", None))
    from autoresearch.session_agent.research_profile import freeze_research_profile

    freeze_research_profile(handle, request)
    _mirror_identity(handle, "request.json", request)
    _mirror_identity(handle, "host_profile.json", request["host_profile"])
    from autoresearch.session_agent.host_evidence import register_main_context

    register_main_context(handle, request["host_profile"])
    if predecessor is not None:
        _mirror_identity(handle, "predecessor.json", predecessor)
    frozen_plan = (planner or _default_planner)(request, handle)
    from autoresearch.session_agent.preflight import preflight_plan

    role_support = preflight_plan(handle, frozen_plan, request["host_profile"], executor=executor)
    _freeze_json(Path(handle.capsule) / "identity/session/role_support.json", role_support)
    plan_service.freeze_plan(_plan_path(handle), frozen_plan)
    _freeze_json(Path(handle.capsule) / "identity" / "session" / "plan.json", frozen_plan)
    freeze_session_origin(handle, request, frozen_plan)
    role_ids = [task["role"] for task in frozen_plan["tasks"] if task["kind"] == "INFERENCE"]
    role_ids.extend(
        role for template in frozen_plan["task_templates"] for role in template["allowed_roles"]
    )
    _freeze_json(
        Path(handle.capsule) / "identity" / "session" / "roles.json",
        role_manifest(role_ids),
    )
    _mirror_identity(handle, "storage.json", {"schema_version": 1, "output_layout_version": 2,
                                               "run_id": handle.run_id, "plan_hash": frozen_plan['plan_hash']})
    if artifact_registrar is not None:
        artifact_registrar(request, handle, frozen_plan)
    elif planner is None:
        from autoresearch.session_agent.workflows import register_artifacts

        register_artifacts(request, handle, frozen_plan)
    store.initialize(_store_path(handle), frozen_plan)
    return status(handle.run_id, handle_loader=lambda unused: handle, command="begin")


def _begin_executor(handle) -> str:
    """The executor declared at ``begin`` (frozen in role_support.json); older runs = mailbox."""
    try:
        frozen = _read_json(Path(handle.capsule) / "identity/session/role_support.json")
    except (OSError, ValueError, RuntimeError):
        return "mailbox"
    return str(frozen.get("executor") or "mailbox")


def _sync_expansion(handle, request: dict, expansion: dict) -> None:
    from autoresearch.session_agent.preflight import preflight_plan
    from autoresearch.session_agent.workflows import register_expansion_artifacts

    preflight_plan(handle, expansion, request["host_profile"], executor=_begin_executor(handle))
    register_expansion_artifacts(request, handle, expansion)
    _freeze_json(
        Path(handle.capsule)
        / "identity/session/expansions"
        / f"{expansion['expansion_id']}.json",
        expansion,
    )
    store.register_tasks(
        _store_path(handle),
        expansion["tasks"],
        plan_hash=_load_plan(handle)["plan_hash"],
    )


def _activate_after_task(handle, task: dict) -> list[str]:
    from autoresearch.session_agent.workflows import expansions_after_task

    request = _read_json(_session_dir(handle) / "request.json")
    frozen_plan = _load_plan(handle)
    activated = []
    for expansion in expansions_after_task(request, handle, frozen_plan, task):
        path = plan_service.persist_expansion(
            _session_dir(handle),
            frozen_plan,
            expansion,
            existing_tasks=_all_tasks(handle, frozen_plan),
        )
        _sync_expansion(handle, request, expansion)
        activated.append(path.name)
    return activated


def _failure_scope(task: dict) -> str:
    subject = str(task.get("subject") or "")
    return "STOCK" if (task.get("parent_task") or re.fullmatch(r"[0-9]{6}(?:\.(?:SS|SZ|BJ))?", subject)
                       or (subject and str(task.get("role") or "").startswith("stock."))) else "GLOBAL"


def _input_failure_scope(task: dict, artifact_id: str, tasks: list[dict]) -> str:
    consumers = {item.get("subject") for item in tasks if artifact_id in item["input_artifact_ids"]}
    if len(consumers) > 1 or _failure_scope(task) == "GLOBAL":
        return "GLOBAL"
    # A shared pack remains global when only one stock currently consumes it.
    # Per-stock scan outputs retain their declared stock identity.
    if (not re.match(r"scan\.l4\.[0-9]{6}\.", artifact_id)
            and any(artifact_id in item["output_artifact_ids"] and _failure_scope(item) == "GLOBAL"
                    for item in tasks)):
        return "GLOBAL"
    return "STOCK"


def _dependency_errors(tasks: list[dict], states: dict, entries: dict, roots: dict) -> list[dict]:
    """Project blockage without changing task ownership or terminal history."""
    for task in tasks:
        task_id = task["task_id"]
        if states.get(task_id) in {"FAILED", "BLOCKED"} and task_id not in roots:
            error = (entries.get(task_id) or {}).get("error") or {}
            roots[task_id] = {"code": "TASK_BLOCKED", "task_id": task_id,
                              "reason": error.get("code", "PARENT_TICKET_FAILED"),
                              "message": error.get("message", ""),
                              "scope": _failure_scope(task)}
    blocked_by = {key: {key} for key in roots}
    changed = True
    while changed:
        changed = False
        for task in tasks:
            task_id = task["task_id"]
            if task_id in roots or states.get(task_id) in {"SUCCEEDED", "SUPERSEDED", "RUNNING"}:
                continue
            dependencies = list(task["dependencies"])
            parent = task.get("parent_task")
            if parent:
                dependencies.append(f"l4.{parent['subject']}.a{parent['attempt']}")
            causes = set().union(*(blocked_by.get(dep, set()) for dep in dependencies))
            if causes and causes != blocked_by.get(task_id):
                blocked_by[task_id] = causes
                changed = True
    return [*roots.values(), *[
        {"code": "DEPENDENCY_BLOCKED", "task_id": task_id, "blocked_by": sorted(causes),
         "scope": "GLOBAL" if any(roots[cause]["scope"] == "GLOBAL" for cause in causes) else "STOCK"}
        for task_id, causes in sorted(blocked_by.items()) if task_id not in roots
    ]]


def _state(handle) -> tuple[str, list[dict], list[dict]]:
    frozen_plan = _load_plan(handle)
    tasks = _all_tasks(handle, frozen_plan)
    states = store.read_states(_store_path(handle))
    owner_tasks = [task for task in tasks if task["owner"] == "L4_TASKBOOK"]
    if owner_tasks:
        from autoresearch.session_agent import legacy_scan

        if legacy_scan.taskbook_path(handle).is_file():
            states.update(legacy_scan.ticket_states(handle, owner_tasks))
        else:
            states.update({task["task_id"]: "PENDING" for task in owner_tasks})
    ready = plan_service.ready_tasks(tasks, states)
    roots = {}
    checked_inputs = {}
    for task in ready:
        for artifact_id in task["input_artifact_ids"]:
            if artifact_id not in checked_inputs:
                try:
                    artifacts.snapshot_artifact(handle, artifact_id)
                    checked_inputs[artifact_id] = None
                except (KeyError, OSError, ValueError, RuntimeError) as exc:
                    checked_inputs[artifact_id] = exc
            exc = checked_inputs[artifact_id]
            if exc is not None:
                roots[task["task_id"]] = {
                    "code": "INPUT_UNAVAILABLE", "task_id": task["task_id"],
                    "reason": "NO_DATA" if isinstance(exc, (KeyError, FileNotFoundError)) else "DATA_INTEGRITY",
                    "artifact_id": artifact_id, "message": str(exc),
                    "scope": _input_failure_scope(task, artifact_id, tasks),
                }
                break
    errors = _dependency_errors(tasks, states, store.read_entries(_store_path(handle)), roots)
    unavailable = {row["task_id"] for row in errors}
    ready = [task for task in ready if task["task_id"] not in unavailable]
    if ready:
        return "READY", ready, errors
    if errors:
        return "BLOCKED", [], errors
    if tasks and all(states.get(task["task_id"]) in {"SUCCEEDED", "SUPERSEDED"} for task in tasks):
        expanded = {
            _read_json(path)["template_id"]
            for path in sorted((_session_dir(handle) / "expansions").glob("*.json"))
        }
        from autoresearch.session_agent.workflows import inapplicable_templates

        not_applicable = inapplicable_templates(frozen_plan, handle)
        missing = [
            template["template_id"]
            for template in frozen_plan["task_templates"]
            if template["template_id"] not in expanded
            and template["template_id"] not in not_applicable
        ]
        if not missing:
            return "DONE", [], []
        return (
            "WAITING",
            [],
            [{"code": "EXPANSION_PENDING", "template_id": item} for item in missing],
        )
    running = [task for task in tasks if states.get(task["task_id"]) == "RUNNING"]
    return "WAITING", running, []


def status(
    run_id: str,
    *,
    handle_loader: Callable[[str], object] | None = None,
    command: str = "status",
) -> dict:
    from autoresearch.trace.capsule import require_active_run

    handle = (handle_loader or require_active_run)(run_id)
    from autoresearch.session_agent.host_evidence import observe_activity
    observe_activity(handle, event=command)
    state, tasks, errors = _state(handle)
    progress = None
    if getattr(getattr(handle, "contract", None), "run_kind", None) == "scan-market":
        from autoresearch.session_agent.progress import scan_progress

        progress = scan_progress(handle, graph_state=state)
    return _result(
        command,
        handle.run_id,
        state,
        tasks=tasks,
        result=progress,
        errors=errors,
    )


def next(run_id: str, *, handle_loader: Callable[[str], object] | None = None) -> dict:
    return status(run_id, handle_loader=handle_loader, command="next")


def _degrade_optional_l3_repair(handle, task_id: str, error: dict) -> bool:
    if task_id not in {"scan.l3.repair", "scan.l3.repair.apply"}:
        return False
    from autoresearch.session_agent.domain_ops import scan_l3_repair_degraded

    scan_l3_repair_degraded(error, handle=handle)
    manifest = None
    keys = ['scan.l3.repair.result']
    effective = Path(handle.staging) / '_l3_effective_judged.json'
    if effective.is_file():
        keys.append('scan.l3.effective.judged')
    if artifacts.layout_version(handle) >= 2:
        spec = _task(handle, 'scan.l3.repair.apply')
        manifest = artifacts.capture_outputs(handle, {**spec, 'output_artifact_ids': keys},
                                             max(1, store.read_entry(_store_path(handle), task_id)['attempt']))
    else:
        for key in keys:
            artifacts.bind_artifact_hash(handle, key)
    task_ids = [task_id]
    if task_id == "scan.l3.repair":
        task_ids.append("scan.l3.repair.apply")
    store.supersede_optional_failure(_store_path(handle), task_ids, error, accepted_artifacts=manifest)
    return True


def fail(
    run_id: str,
    task_id: str,
    attempt: int,
    error_class: str,
    message: str,
    *,
    handle_loader: Callable[[str], object] | None = None,
) -> dict:
    """Record an observed host failure without fabricating a task result."""
    from autoresearch.contracts.retry import TASK_ATTEMPT
    from autoresearch.trace.capsule import require_active_run

    handle = (handle_loader or require_active_run)(run_id)
    task = _task(handle, task_id)
    if task["owner"] != "SESSION":
        raise ValueError("fail applies to a claimed SESSION task")
    kind = str(error_class).strip().upper()
    if not kind or not message:
        raise ValueError("error_class and message are required")
    retryable = kind in TASK_ATTEMPT
    store.mark_failed(
        _store_path(handle),
        task_id,
        attempt,
        {"code": kind, "message": message},
        retryable=retryable,
    )
    from autoresearch.session_agent.evidence import freeze_failure

    freeze_failure(
        handle,
        task,
        attempt,
        {"code": kind, "message": message},
    )
    _degrade_optional_l3_repair(
        handle,
        task_id,
        {"code": kind, "message": message},
    )
    if task["parent_task"] is not None and task.get("role") != "scan.l4.review":
        from autoresearch.session_agent import legacy_scan

        parent = task["parent_task"]
        legacy_scan.fail_ticket(
            handle,
            parent["subject"],
            parent["attempt"],
            kind,
            message,
        )
    current = status(run_id, handle_loader=lambda unused: handle, command="fail")
    current["result"] = {"task_id": task_id, "attempt": attempt, "error_class": kind}
    return current


def retry_l4(
    run_id: str,
    code: str,
    expected_attempt: int,
    *,
    handle_loader: Callable[[str], object] | None = None,
) -> dict:
    """Freeze a new L4 child subtree while leaving attempt ownership in l4_tasks."""
    from autoresearch.contracts.retry import TASK_ATTEMPT
    from autoresearch.session_agent.workflows.scan import l4_retry_expansion
    from autoresearch.trace.capsule import require_active_run

    handle = (handle_loader or require_active_run)(run_id)
    frozen_plan = _load_plan(handle)
    if frozen_plan["run_kind"] != "scan-market":
        raise ValueError("retry-l4 only applies to scan-market runs")
    code6 = str(code).zfill(6)
    if not code6.isdigit() or len(code6) != 6 or expected_attempt < 2:
        raise ValueError("invalid L4 retry identity")
    from autoresearch.session_agent import legacy_scan

    payload = legacy_scan._payload(handle)
    ticket = (payload.get("tasks") or {}).get(code6)
    if ticket is None:
        raise KeyError(code6)
    previous_attempt = expected_attempt - 1
    if (
        ticket.get("status") != "FAILED"
        or int(ticket.get("attempt") or 0) != previous_attempt
        or str(ticket.get("last_error_class") or "") not in TASK_ATTEMPT
    ):
        raise RuntimeError("L4 ticket is not eligible for the requested retry")
    if not any(
        task["task_id"] == f"l4.{code6}.a{previous_attempt}.card"
        for task in _all_tasks(handle, frozen_plan)
    ):
        raise RuntimeError("previous L4 attempt subtree is missing")
    previous_tasks = [
        task
        for task in _all_tasks(handle, frozen_plan)
        if (task.get("parent_task") or {}).get("subject") == code6
        and (task.get("parent_task") or {}).get("attempt") == previous_attempt
    ]
    states = store.read_states(_store_path(handle))
    if any(states.get(task["task_id"]) == "RUNNING" for task in previous_tasks):
        raise RuntimeError("L4 retry requires every previous child to be quiescent")
    request = _read_json(_session_dir(handle) / "request.json")
    intel_enabled = any(task.get("role") == "scan.l4.intel" for task in previous_tasks)
    initial_tasks = [task for task in previous_tasks
                     if task["expected_output_contract"] == "research.card.initial.v1"]
    successful_initials = [task for task in initial_tasks if states.get(task["task_id"]) == "SUCCEEDED"]
    retained_initial = successful_initials[0] if successful_initials else None
    prompt_snapshot = artifacts.snapshot_artifact(handle, f"scan.l4.{code6}.a1.prompt")
    snapshots = [{"artifact_id": prompt_snapshot["artifact_id"], "sha256": prompt_snapshot["sha256"]}]
    if retained_initial is not None:
        for artifact_id in [*retained_initial["input_artifact_ids"], *retained_initial["output_artifact_ids"]]:
            frozen = artifacts.snapshot_artifact(handle, artifact_id)
            snapshots.append({"artifact_id": artifact_id, "sha256": frozen["sha256"]})
    expansion = l4_retry_expansion(
        frozen_plan,
        code6,
        expected_attempt,
        snapshots,
        intel_enabled=intel_enabled,
        retained_initial=retained_initial,
        holding=any(any(key.endswith(".deep") for key in task["input_artifact_ids"])
                    for task in initial_tasks),
    )
    from autoresearch.session_agent.decision_frame import attach_expansion, frame_in_plan
    # Same rule as workflows.expansions_after_task: a frozen frame rides on every expansion, so
    # a retried card keeps its decision window, card contract and claim population.
    if frame_in_plan(frozen_plan):
        expansion = attach_expansion(expansion, artifacts.snapshot_artifact(handle, "research.frame"))
    root = _session_dir(handle) / "recoveries"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{expansion['expansion_id']}.json"
    existing_tasks = _all_tasks(handle, frozen_plan)
    if path.is_file():
        if canonical_json(_read_json(path)) != canonical_json(expansion):
            raise RuntimeError("frozen L4 recovery changed")
    else:
        plan_service.apply_expansion(frozen_plan, expansion, existing_tasks=existing_tasks)
        atomic_write_json(path, expansion)
    _sync_expansion(handle, request, expansion)
    store.prepare_l4_retry(_store_path(handle), code6, previous_attempt)
    current = status(run_id, handle_loader=lambda unused: handle, command="retry-l4")
    current["result"] = {
        "code": code6,
        "attempt": expected_attempt,
        "expansion": path.name,
    }
    return current


def _subject_kwargs(task: dict) -> dict:
    subject = task.get("subject")
    if subject is None or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", subject):
        return {"subject": subject}
    return {"subject_display": subject}


def _boundary_recorder(recorder):
    if recorder is not None:
        return recorder
    from autoresearch.trace.capsule import record_agent_boundary

    return record_agent_boundary


def _record_completion(handle, task: dict, attempt: int, payload: dict, recorder) -> None:
    _boundary_recorder(recorder)(
        handle.run_id,
        "AGENT_COMPLETED",
        role=task["role"],
        invocation_id=f"session-{task['task_id']}-a{attempt}",
        attempt=attempt,
        result=payload,
        stage=role_stage(task["role"]),
        **_subject_kwargs(task),
    )


def _recover_inference_completion(handle, task, receipt, recorder):
    entry = store.read_entry(_store_path(handle), task['task_id'])
    submission = entry.get('accepted_submission')
    if submission is None:
        return
    receipt_path = Path(handle.capsule) / 'agents/session/receipts' / f"{receipt['receipt_id']}.json"
    _freeze_json(receipt_path, receipt)
    completion = {
        'receipt_ref': receipt_path.relative_to(handle.capsule).as_posix(),
        'receipt_id': receipt['receipt_id'], 'outputs': submission['outputs'],
        'host_receipt_id': submission['host_receipt_id'],
    }
    completion_path = Path(handle.capsule) / 'agents/session/completions' / f"{task['task_id']}-a{receipt['attempt']}.json"
    _freeze_json(completion_path, completion)
    _record_completion(handle, task, receipt['attempt'], completion, recorder)


def _promote_l4_retry_output(handle, task: dict) -> None:
    if artifacts.layout_version(handle) >= 2:
        artifacts.materialize_outputs(handle)
        return
    match = re.fullmatch(
        r"l4\.(\d{6})\.a(\d+)\.(intel|card|review2|review3)",
        task["task_id"],
    )
    if match is None or int(match.group(2)) < 2:
        return
    code, attempt_text, kind = match.groups()
    if kind == "intel":
        # Review I4 / N2: every attempt's intel (a1 included) is bound at its own
        # session_attempts/<code>/a<n>/intel.md and never rewritten; the legacy canonical
        # `_l4_intel_<code>.md` (what the card reads) is derived from it by the
        # intel_status / finalize operations, as in the legacy flow.
        return
    with artifacts.open_artifact(handle, task["output_artifact_ids"][0]) as stream:
        content = stream.read()
    staging = Path(handle.staging)
    targets = {
        "card": staging / "details" / f"{code}.md",
        "review2": staging / "ensemble" / f"{code}.run2.md",
        "review3": staging / "ensemble" / f"{code}.run3.md",
    }
    target = targets[kind]
    target.parent.mkdir(parents=True, exist_ok=True)
    if kind == "card":
        previous = int(attempt_text) - 1
        original_id = f"scan.l4.{code}.a{previous}.card"
        original = store.read_entry(_store_path(handle), f"l4.{code}.a{previous}.card")
        if original["state"] == "SUPERSEDED":
            # resume may repeat a completed promotion. Verify the accepted alias
            # byte-for-byte; do not repair or conceal a later write to that path.
            with artifacts.open_artifact(handle, original_id) as stream:
                if stream.read() != content:
                    raise RuntimeError("promoted L4 card alias changed after acceptance")
            return
        if original["state"] != "WAITING_RETRY":
            raise RuntimeError("original L4 card is not waiting for retry promotion")
        expected_sha256 = artifacts.binding_sha256(handle, original_id)
        artifacts.replace_failed_output(
            handle,
            original_id,
            content,
            expected_sha256=expected_sha256,
        )
        store.complete_l4_retry_alias(_store_path(handle), code, previous)
        return
    if not target.is_file() or target.read_bytes() != content:
        temp = target.with_name(f".{target.name}.a{attempt_text}.tmp")
        temp.write_bytes(content)
        temp.replace(target)


def _verify_frozen_inputs(handle, task: dict, entry: dict) -> None:
    frozen = {
        item["artifact_id"]: item["sha256"] for item in entry["claim_receipt"]["input_snapshots"]
    }
    current = {
        artifact_id: artifacts.snapshot_artifact(handle, artifact_id)["sha256"]
        for artifact_id in task["input_artifact_ids"]
    }
    if current != frozen:
        raise ValueError("task inputs changed after claim")


def claim(
    run_id: str,
    task_id: str,
    expected_attempt: int,
    *,
    handle_loader: Callable[[str], object] | None = None,
    event_recorder=None,
) -> dict:
    from autoresearch.trace.capsule import require_active_run

    handle = (handle_loader or require_active_run)(run_id)
    task = _task(handle, task_id)
    if task["owner"] == "L4_TASKBOOK":
        from autoresearch.session_agent import legacy_scan
        from autoresearch.session_agent.evidence import freeze_claim

        receipt = legacy_scan.claim_ticket(handle, task["subject"], expected_attempt)
        snapshots = [
            artifacts.snapshot_artifact(handle, artifact_id)
            for artifact_id in task["input_artifact_ids"]
        ]
        freeze_claim(
            handle,
            task,
            expected_attempt,
            {
                "schema_version": 1,
                "task_id": task_id,
                "attempt": expected_attempt,
                "spec": task,
                "input_snapshots": snapshots,
                "owner_receipt": receipt,
            },
        )
        return _result("claim", handle.run_id, "WAITING", result={"claim_receipt": receipt})
    if task["parent_task"] is not None:
        from autoresearch.session_agent import legacy_scan

        legacy_scan.validate_parent(handle, task["parent_task"])
    host_profile = _read_json(_session_dir(handle) / "host_profile.json")
    input_snapshots = [
        artifacts.snapshot_artifact(handle, artifact_id)
        for artifact_id in task["input_artifact_ids"]
    ]
    receipt = store.claim(
        _store_path(handle),
        task_id,
        expected_attempt,
        host_profile["session_ref"],
        input_snapshots,
    )
    from autoresearch.session_agent.evidence import freeze_claim

    if artifacts.layout_version(handle) >= 2 and task['kind'] == 'INFERENCE':
        receipt = {**receipt, 'output_paths': artifacts.output_paths(handle, task, expected_attempt)}
    freeze_claim(handle, task, expected_attempt, receipt)
    claim_result: dict = {"claim_receipt": receipt}
    if task["kind"] == "INFERENCE":
        envelope = {
            "schema_version": 1,
            "engine": handle.engine,
            "run_id": handle.run_id,
            "task_id": task["task_id"],
            "role": task["role"],
            "input_artifact_ids": task["input_artifact_ids"],
            "input_contract_hash": handle.contract.contract_hash,
            "expected_output_contract": task["expected_output_contract"],
            "attempt": expected_attempt,
        }
        claim_result.update(
            {
                "envelope": envelope,
                "plan_hash": _load_plan(handle)["plan_hash"],
                "request": render_request(task, host_profile),
            }
        )
        invocation_id = f"session-{task['task_id']}-a{expected_attempt}"
        if artifacts.layout_version(handle) >= 2:
            from autoresearch.session_agent.dispatch import build_request
            claim_result['dispatch_request'] = build_request(
                handle, task, expected_attempt, host_profile=host_profile).to_json()
            from autoresearch.session_agent.task_access import activate_claim_access, manifest_path
            activate_claim_access(Path(handle.workspace) / 'session/dispatch' / f"{task['task_id']}-a{expected_attempt}.json")
            access_path = manifest_path(Path(handle.workspace) / 'session/dispatch' / f"{task['task_id']}-a{expected_attempt}.json")
            claim_result['access_manifest'] = ({'path': str(access_path), 'sha256': sha256_file(access_path), 'enforcement': 'UNVERIFIED'}
                                               if access_path.is_file() else {'status': 'MISSING_LEGACY_BINDING'})
        handoff = {
            "schema_version": 1,
            "envelope": envelope,
            "plan_hash": claim_result["plan_hash"],
            "request": claim_result["request"],
            "claim_receipt": receipt,
        }
        if 'dispatch_request' in claim_result:
            handoff['dispatch_request'] = claim_result['dispatch_request']
            handoff['access_manifest'] = claim_result['access_manifest']
        request_path = Path(handle.capsule) / "agents/session/requests" / f"{invocation_id}.json"
        _freeze_json(request_path, handoff)
        _boundary_recorder(event_recorder)(
            handle.run_id,
            "AGENT_DISPATCHED",
            role=task["role"],
            invocation_id=invocation_id,
            attempt=expected_attempt,
            result={
                "request_ref": request_path.relative_to(handle.capsule).as_posix(),
                "request_sha256": sha256_file(request_path),
            },
            stage=role_stage(task["role"]),
            **_subject_kwargs(task),
        )
    return _result("claim", handle.run_id, "WAITING", result=claim_result)


def execute(
    run_id: str,
    task_id: str,
    attempt: int,
    params: dict,
    *,
    handle_loader: Callable[[str], object] | None = None,
    runner=None,
    owner_callback=None,
) -> dict:
    from autoresearch.trace.capsule import require_active_run

    handle = (handle_loader or require_active_run)(run_id)
    task = _task(handle, task_id)
    if task["kind"] != "DETERMINISTIC":
        raise ValueError("execute requires a deterministic task")
    if task["parent_task"] is not None:
        from autoresearch.session_agent import legacy_scan

        legacy_scan.validate_parent(handle, task["parent_task"])
    entry = store.read_entry(_store_path(handle), task_id)
    if entry["state"] != "RUNNING" or entry["attempt"] != attempt:
        raise RuntimeError("deterministic task attempt is not claimed")
    _verify_frozen_inputs(handle, task, entry)
    request = _read_json(_session_dir(handle) / "request.json")
    if task["operation"] != "test.noop":
        from autoresearch.session_agent.workflows import validate_operation_params

        validate_operation_params(request, task, params)
    kwargs = {} if runner is None else {"runner": runner}
    if owner_callback is not None and task["operation"] == "research.card.facts" and runner is None:
        kwargs["owner_callback"] = owner_callback
    artifacts.materialize_outputs(handle)
    try:
        execution = executor.execute_operation(handle, task, attempt, params, **kwargs)
    except KeyboardInterrupt:
        fail(run_id, task_id, attempt, "INTERRUPTED", "deterministic execution interrupted",
             handle_loader=lambda unused: handle)
        raise
    if execution["status"] != "SUCCEEDED":
        failure = {
            "code": "OPERATION_FAILED",
            "execution": execution,
        }
        store.mark_failed(
            _store_path(handle),
            task_id,
            attempt,
            failure,
            retryable=bool(executor.operation_spec(task["operation"])["idempotent"]),
        )
        from autoresearch.session_agent.evidence import freeze_failure

        freeze_failure(handle, task, attempt, failure)
        _degrade_optional_l3_repair(
            handle,
            task_id,
            {
                "code": "OPERATION_FAILED",
                "message": f"deterministic operation failed: {task_id}",
            },
        )
        if task["parent_task"] is not None:
            from autoresearch.session_agent import legacy_scan

            parent = task["parent_task"]
            legacy_scan.fail_ticket(
                handle,
                parent["subject"],
                parent["attempt"],
                "OPERATION_FAILED",
                f"child operation failed: {task_id}",
            )
        return status(run_id, handle_loader=lambda unused: handle, command="execute")
    if "prepared_output" in execution:
        current = store.read_entry(_store_path(handle), task_id)
        if current["state"] != "RUNNING" or current["attempt"] != attempt:
            raise RuntimeError("prepared result lost task ownership")
        if task["parent_task"] is not None:
            legacy_scan.validate_parent(handle, task["parent_task"])
        _verify_frozen_inputs(handle, task, current)
        if task["operation"] != "research.card.facts" or len(task["output_artifact_ids"]) != 1:
            raise ValueError("prepared result requires facts output")
        atomic_write_json(artifacts.declared_path(handle, task["output_artifact_ids"][0]),
                          execution.pop("prepared_output"))
    manifest = None
    if artifacts.layout_version(handle) >= 2:
        manifest = artifacts.capture_outputs(handle, task, attempt)
        outputs = [{'artifact_id': key, 'sha256': row['sha256']} for key, row in manifest.items()]
    else:
        outputs = []
        for artifact_id in task['output_artifact_ids']:
            descriptor = artifacts.bind_artifact_hash(handle, artifact_id)
            outputs.append({'artifact_id': artifact_id, 'sha256': descriptor['sha256']})
    receipt = store.complete_deterministic(
        _store_path(handle), task_id, attempt, outputs, execution, accepted_artifacts=manifest
    )
    from autoresearch.session_agent.evidence import freeze_receipt

    freeze_receipt(handle, task, attempt, receipt)
    activated = _activate_after_task(handle, task)
    current = status(run_id, handle_loader=lambda unused: handle, command="execute")
    current["result"] = {
        "execution": execution,
        "receipt": receipt,
        "activated_expansions": activated,
    }
    return current


def calculate(
    run_id: str,
    task_id: str,
    attempt: int,
    request: dict,
    *,
    handle_loader: Callable[[str], object] | None = None,
) -> dict:
    """Run a bounded pure calculator inside one claimed inference attempt."""
    from autoresearch.contracts.session_task import require_exact_fields
    from autoresearch.trace.capsule import require_active_run

    handle = (handle_loader or require_active_run)(run_id)
    task = _task(handle, task_id)
    if task["kind"] != "INFERENCE" or task["owner"] != "SESSION":
        raise ValueError("bounded calculation requires a SESSION inference task")
    entry = store.read_entry(_store_path(handle), task_id)
    if entry["state"] != "RUNNING" or entry["attempt"] != attempt:
        raise RuntimeError("calculation parent task attempt is not running")
    _verify_frozen_inputs(handle, task, entry)
    require_exact_fields(
        request,
        frozenset({"calculator_id", "input_artifact_ids", "parameters"}),
    )
    from autoresearch.session_agent.operations import build_argv

    build_argv("research.calculate", request)
    if not set(request["input_artifact_ids"]) <= set(task["input_artifact_ids"]):
        raise ValueError("calculation inputs are outside the claimed inference task")
    from autoresearch.session_agent.domain_ops import research_calculate

    calculation = research_calculate(
        request["calculator_id"],
        request["input_artifact_ids"],
        request["parameters"],
        handle=handle,
        task_id=task_id,
        attempt=attempt,
        raise_on_failure=False,
    )
    path = Path(handle.capsule) / "evidence/calculations" / f"{calculation['calculation_id']}.json"
    from autoresearch.trace.events import append_event

    append_event(
        Path(handle.capsule) / "events/events.jsonl",
        run_id=handle.run_id,
        engine=handle.engine,
        stage=role_stage(task["role"]),
        invocation_id=f"session-{task_id}-a{attempt}",
        attempt=attempt,
        subject=task["subject"],
        event_type="CALCULATION_COMPLETED",
        payload={
            "calculation_id": calculation["calculation_id"],
            "calculator_id": calculation["calculator_id"],
            "status": calculation["status"],
            "evidence_ref": path.relative_to(handle.capsule).as_posix(),
            "code_hash": calculation["code_hash"],
        },
    )
    return _result("calculate", run_id, "WAITING", result=calculation)


def source_fields(
    run_id: str, task_id: str, attempt: int, request: dict, *, handle_loader=None,
) -> dict:
    """Root-only field verification over an existing receipt in the admitted task graph.

    Same current-attempt and frozen-input gates as calculate. The registered intel
    status operation also calls this service; research file brokers do not execute it.
    """
    from datetime import datetime, timezone

    from autoresearch.contracts.session_task import require_exact_fields
    from autoresearch.news import source_fields as fields
    from autoresearch.trace.capsule import require_active_run
    from autoresearch.trace.events import append_event
    from autoresearch.trace.source_receipts import read_receipts, record_response

    handle = (handle_loader or require_active_run)(run_id)
    if handle.run_id != run_id:
        raise ValueError("source-fields run mismatch")
    task = _task(handle, task_id)
    if task["owner"] != "SESSION" or not (
        task["kind"] == "INFERENCE" or task["operation"] == "scan.l4.intel.status"
    ):
        raise ValueError("source-fields requires inference or registered intel status owner")
    entry = store.read_entry(_store_path(handle), task_id)
    if entry["state"] != "RUNNING" or entry["attempt"] != attempt:
        raise RuntimeError("source-fields task attempt is not running")
    if task["parent_task"] is not None:
        from autoresearch.session_agent import legacy_scan
        legacy_scan.validate_parent(handle, task["parent_task"])
    _verify_frozen_inputs(handle, task, entry)
    require_exact_fields(request, frozenset({"source_receipt_id", "adapter_id", "selector"}))
    receipts = {row["receipt_id"]: row for row in read_receipts(handle.capsule)}
    source = receipts[request["source_receipt_id"]]
    allowed = fields.admissible_attempts(store.read_entries(_store_path(handle)), task_id, attempt)
    fields.require_source_identity(source, engine=handle.engine, run_id=handle.run_id, allowed=allowed)
    frame = json.loads(artifacts.read_bytes(handle, "research.frame"))
    fields.require_timing(source, frame)
    # A source superseded by an available (or undated) correction stays unavailable.
    for row in receipts.values():
        if source["receipt_id"] in row.get("supersedes_receipt_ids", []):
            try:
                fields.require_timing(row, frame)
            except ValueError:
                times = row.get("source_timing")
                if times and times.get("first_available_at") is not None:
                    continue
            raise ValueError("source receipt has been superseded")
    payload, paths = fields.derive_fields(source, fields.checked_payload(Path(handle.capsule), source), request["adapter_id"], request["selector"])
    subject = str(task["subject"] or "").split(".")[0]
    if payload["event"]["subject_code"] != subject:
        raise ValueError("source row subject differs from task")
    params = {"producer_version": fields.PRODUCER_VERSION, **request,
              "field_paths": paths, "frame_sha256": sha256_bytes(canonical_json(frame).encode()),
              "input_snapshots": entry["claim_receipt"]["input_snapshots"]}
    for row in receipts.values():
        if row["task_id"] == task_id and row["attempt"] == attempt and row["endpoint"] == "claim_fields.v1" and row["normalized_params"] == params:
            fields.replay_review(Path(handle.capsule), row, source, frame)
            return _result("source-fields", run_id, "WAITING", result=row)
    timestamp = datetime.now(timezone.utc).isoformat()
    review = record_response(handle, {
        "engine": handle.engine, "run_id": handle.run_id, "task_id": task_id, "attempt": attempt,
        "provider": "deterministic", "endpoint": "claim_fields.v1", "normalized_params": params,
        "started_at": timestamp, "ended_at": timestamp, "as_of": source["as_of"],
        "available_at": None, "consumer_refs": [],
    }, payload)
    append_event(Path(handle.capsule) / "events/events.jsonl", run_id=handle.run_id,
        engine=handle.engine, stage="intel", invocation_id=f"session-{task_id}-a{attempt}",
        attempt=attempt, subject=task["subject"], event_type="SOURCE_FIELDS_VERIFIED",
        payload={"review_receipt_id": review["receipt_id"], "source_receipt_id": source["receipt_id"],
                 "task_id": task_id, "attempt": attempt})
    return _result("source-fields", run_id, "WAITING", result=review)


def _validate_submission_host_receipt(
    handle, submission: dict, task: dict, host_receipt: dict, *, _entry_reader=None,
) -> str:
    """Read-only receipt validation shared by precheck and formal submit."""
    host_profile = _read_json(_session_dir(handle) / "host_profile.json")
    validate_receipt(task, host_receipt, host_profile)
    if task["expected_output_contract"] == "research.card.decision.v1":
        from autoresearch.session_agent.card_facts import validate_distinct_context
        validate_distinct_context(handle, task, host_receipt, _entry_reader=_entry_reader)
    if host_receipt["attempt"] != submission["envelope"]["attempt"]:
        raise ValueError("host receipt attempt mismatch")
    receipt_id = sha256_bytes(canonical_json(host_receipt).encode("utf-8"))
    if submission["host_receipt_id"] != receipt_id:
        raise ValueError("host_receipt_id does not match the verified receipt")
    from autoresearch.session_agent.host_evidence import resolve_receipt_evidence
    resolve_receipt_evidence(handle, task, host_receipt)
    return receipt_id


def precheck(run_id: str, submission: dict, *, handle_loader=None, host_receipt=None) -> dict:
    """Inspect a candidate; this never accepts, binds, publishes, or freezes receipts.

    One output hashes its captured bytes. Multiple outputs hash canonical JSON of
    the artifact-id -> captured SHA256 mapping (UTF-8, no trailing newline).
    """
    import contextlib
    from autoresearch.trace.capsule import require_active_run
    from autoresearch.session_agent import host_evidence
    from autoresearch.session_agent.executors import mailbox
    from autoresearch.session_agent.validation import (
        DomainValidationError, validate_deep_read_evidence, validate_registered_domain_contract,
    )

    validate_submission(submission)
    handle = (handle_loader or require_active_run)(run_id)
    if handle.run_id != run_id or submission["envelope"]["run_id"] != handle.run_id:
        raise ValueError("submission run_id does not match command run_id")
    if artifacts.layout_version(handle) < 2:
        raise ValueError("precheck requires output layout version 2")
    task = _task(handle, submission["envelope"]["task_id"])
    if task["kind"] != "INFERENCE":
        raise ValueError("precheck requires an inference task")

    def inspect(value, spec, entry, read_locked_entry):
        attempt = value["envelope"]["attempt"]
        if mailbox.is_abandoned(handle.staging, spec["task_id"], attempt):
            raise store.TaskConflict("submission attempt was abandoned")
        _verify_frozen_inputs(handle, spec, entry)
        manifest = artifacts.capture_outputs(handle, spec, attempt, purpose="precheck")
        hashes = {key: row["sha256"] for key, row in manifest.items()}
        digest = (list(hashes.values())[0] if len(hashes) == 1
                  else sha256_bytes(canonical_json(hashes).encode("utf-8")))
        errors, deep_ids = [], []
        domain_status, host_status = "PASS", "PENDING_FINAL_BINDING"
        with artifacts.candidate_view(handle, manifest):
            try:
                validate_submission_outputs(handle, value, spec)
                deep_ids = validate_registered_domain_contract(handle, value, spec)
            except (DomainValidationError, ValueError, KeyError, RuntimeError, OSError) as exc:
                domain_status = "FAIL"
                errors.append(f"domain: {exc}")
            try:
                if host_receipt is not None:
                    _validate_submission_host_receipt(
                        handle, value, spec, host_receipt, _entry_reader=read_locked_entry)
                    host_status = "VERIFIED"
                elif value["host_receipt_id"] is not None:
                    raise ValueError("host_receipt_id requires the matching host receipt")
                else:
                    # An absent final binding is pending. A corrupt existing one
                    # is invalid, even when the caller omits its receipt.
                    binding = host_evidence._existing_binding(handle, spec["task_id"], attempt)
                    if binding is not None:
                        host_evidence._load_binding_ref(handle, f"host-binding:{binding['binding_id']}")
                        identity = {"engine": handle.engine, "run_id": handle.run_id,
                                    "task_id": spec["task_id"], "attempt": attempt,
                                    "role": spec["role"], "subject": spec["subject"]}
                        if any(binding.get(key) != expected for key, expected in identity.items()):
                            raise ValueError("host evidence task identity mismatch")
                        if domain_status == "PASS":
                            validate_deep_read_evidence(handle, value, spec, deep_ids)
                if host_status == "VERIFIED" and domain_status == "PASS":
                    validate_deep_read_evidence(handle, value, spec, deep_ids)
            except (ValueError, KeyError, RuntimeError, OSError, TypeError) as exc:
                host_status = "INVALID"
                errors.append(f"host evidence: {exc}")
        return {"schema_version": 1, "run_id": run_id, "task_id": spec["task_id"], "attempt": attempt,
                "candidate_sha256": digest, "domain_status": domain_status,
                "host_evidence_status": host_status, "errors": errors,
                "can_submit": domain_status == "PASS" and host_status == "VERIFIED"}

    parent_guard = contextlib.nullcontext()
    if task["parent_task"] is not None:
        from autoresearch.scan import l4_tasks
        from autoresearch.session_agent import legacy_scan
        parent_guard = l4_tasks._locked(legacy_scan.taskbook_path(handle))
    with mailbox._locked(handle.staging), parent_guard:
        if task["parent_task"] is not None:
            legacy_scan.verify_child_handoff(handle, task, submission["envelope"])
        result = store.precheck(_store_path(handle), submission, inspect)
    return _result("precheck", run_id, "WAITING", result=result)


def submit(
    run_id: str,
    submission: dict,
    *,
    handle_loader: Callable[[str], object] | None = None,
    validator: Callable[[dict, dict], object] | None = None,
    host_receipt: dict | None = None,
    event_recorder=None,
) -> dict:
    from autoresearch.trace.capsule import require_active_run

    validate_submission(submission)
    handle = (handle_loader or require_active_run)(run_id)
    if submission["envelope"]["run_id"] != handle.run_id:
        raise ValueError("submission run_id does not match command run_id")
    task = _task(handle, submission["envelope"]["task_id"])
    if task["kind"] != "INFERENCE":
        raise ValueError("submit requires an inference task")
    from autoresearch.session_agent.host_evidence import observe_activity
    observe_activity(handle, task_id=task["task_id"], attempt=submission["envelope"]["attempt"], event="submit")
    existing = store.read_entry(_store_path(handle), task['task_id'])
    if existing['state'] == 'SUCCEEDED' and artifacts.layout_version(handle) >= 2:
        receipt = store.accept(_store_path(handle), submission, lambda *_: None)
        from autoresearch.session_agent.evidence import freeze_receipt
        freeze_receipt(handle, task, receipt['attempt'], receipt)
        _recover_inference_completion(handle, task, receipt, event_recorder)
        artifacts.materialize_outputs(handle)
        current = status(run_id, handle_loader=lambda unused: handle, command='submit')
        current['result'] = {'receipt': receipt}
        return current
    if task["parent_task"] is not None:
        from autoresearch.session_agent import legacy_scan

        legacy_scan.verify_child_handoff(handle, task, submission["envelope"])
    entry = store.read_entry(_store_path(handle), task["task_id"])
    _verify_frozen_inputs(handle, task, entry)
    if host_receipt is not None:
        host_receipt_id = _validate_submission_host_receipt(handle, submission, task, host_receipt)
        _freeze_json(
            _session_dir(handle) / "receipts" / f"{host_receipt_id}.json",
            host_receipt,
        )
        _freeze_json(
            Path(handle.capsule) / "agents/session/host_receipts" / f"{host_receipt_id}.json",
            host_receipt,
        )
    elif submission["host_receipt_id"] is not None:
        raise ValueError("host_receipt_id requires the matching host receipt")
    elif task["independent_context"]:
        raise ValueError("independent task requires a verified host receipt")

    from autoresearch.session_agent.executors import mailbox

    def require_not_abandoned(value):
        envelope = value['envelope']
        if mailbox.is_abandoned(handle.staging, envelope['task_id'], envelope['attempt']):
            raise store.TaskConflict('submission attempt was abandoned')

    def checked(value: dict, spec: dict) -> None:
        require_not_abandoned(value)
        domain_validator = validator
        if domain_validator is None:

            def domain_validator(submitted, task):
                return validate_registered_contract(handle, submitted, task)

        try:
            validate_submission_outputs(handle, value, spec, domain_validator=domain_validator)
        except Exception:
            observe_activity(handle, task_id=spec['task_id'], attempt=value['envelope']['attempt'],
                             event='submit_rejected')
            raise

    def prepare(value, spec):
        require_not_abandoned(value)
        manifest = artifacts.capture_outputs(handle, spec, value['envelope']['attempt'], expected=value['outputs'])
        with artifacts.candidate_view(handle, manifest):
            checked(value, spec)
        return manifest

    import contextlib
    parent_guard = contextlib.nullcontext()
    if task['parent_task'] is not None:
        from autoresearch.scan import l4_tasks
        from autoresearch.session_agent import legacy_scan
        parent_guard = l4_tasks._locked(legacy_scan.taskbook_path(handle))
    with mailbox._locked(handle.staging), parent_guard:
        if task['parent_task'] is not None:
            legacy_scan.verify_child_handoff(handle, task, submission['envelope'])
        if artifacts.layout_version(handle) >= 2:
            receipt = store.accept(_store_path(handle), submission, lambda *_: None, prepare=prepare)
        else:
            receipt = store.accept(_store_path(handle), submission, checked)
    from autoresearch.session_agent.evidence import freeze_receipt

    freeze_receipt(
        handle,
        task,
        submission["envelope"]["attempt"],
        receipt,
    )
    receipt_path = (
        Path(handle.capsule) / "agents/session/receipts" / f"{receipt['receipt_id']}.json"
    )
    _freeze_json(receipt_path, receipt)
    attempt = submission["envelope"]["attempt"]
    completion = {
        "receipt_ref": receipt_path.relative_to(handle.capsule).as_posix(),
        "receipt_id": receipt["receipt_id"],
        "outputs": submission["outputs"],
        "host_receipt_id": submission["host_receipt_id"],
    }
    completion_path = (
        Path(handle.capsule) / "agents/session/completions" / f"{task['task_id']}-a{attempt}.json"
    )
    _freeze_json(completion_path, completion)
    _record_completion(handle, task, attempt, completion, event_recorder)
    _promote_l4_retry_output(handle, task)
    current = status(run_id, handle_loader=lambda unused: handle, command="submit")
    current["result"] = {"receipt": receipt}
    return current


def resume(
    run_id: str,
    *,
    handle_loader: Callable[[str], object] | None = None,
    event_recorder=None,
) -> dict:
    from autoresearch.trace.capsule import require_active_run

    handle = (handle_loader or require_active_run)(run_id)
    from autoresearch.session_agent.host_evidence import observe_activity
    observe_activity(handle, event='recovery')
    artifacts.materialize_outputs(handle)
    request = _read_json(_session_dir(handle) / "request.json")
    expansion_root = _session_dir(handle) / "expansions"
    if expansion_root.is_dir():
        for path in sorted(expansion_root.glob("*.json")):
            _sync_expansion(handle, request, _read_json(path))
    states = store.read_states(_store_path(handle))
    recovered = []
    running = []
    for task in _all_tasks(handle):
        state = states.get(task["task_id"])
        if task["owner"] == "L4_TASKBOOK":
            continue
        if state == "SUCCEEDED":
            if task["kind"] == "INFERENCE":
                _promote_l4_retry_output(handle, task)
            if task["kind"] == "DETERMINISTIC":
                _activate_after_task(handle, task)
            receipt = store.recover_receipt(_store_path(handle), task["task_id"])
            if receipt is not None:
                from autoresearch.session_agent.evidence import freeze_receipt
                freeze_receipt(handle, task, receipt['attempt'], receipt)
                recovered.append(receipt)
                if task["kind"] == "INFERENCE":
                    _recover_inference_completion(handle, task, receipt, event_recorder)
                    completion_path = (
                        Path(handle.capsule)
                        / "agents/session/completions"
                        / f"{task['task_id']}-a{receipt['attempt']}.json"
                    )
                    if completion_path.is_file() and artifacts.layout_version(handle) < 2:
                        _record_completion(
                            handle,
                            task,
                            receipt["attempt"],
                            _read_json(completion_path),
                            event_recorder,
                        )
        elif state == "RUNNING" and task["kind"] == "DETERMINISTIC":
            entry = store.read_entry(_store_path(handle), task["task_id"])
            running.append(executor.probe_execution(handle, task["task_id"], entry["attempt"]))
    current = status(run_id, handle_loader=lambda unused: handle, command="resume")
    current["result"] = {"recovered_receipts": recovered, "running": running}
    return current


def _default_finalizer(handle, report):
    from autoresearch.trace.capsule import BusinessStatus, finalize

    report_dir = report if isinstance(report, (str, Path)) else None
    return finalize(
        handle.run_id,
        BusinessStatus.SUCCEEDED,
        report_dir=report_dir,
        profile=session_profile(handle),
    )


def finish(
    run_id: str,
    *,
    handle_loader: Callable[[str], object] | None = None,
    publisher: Callable[[object], object] | None = None,
    finalizer: Callable[[object, object], object] | None = None,
) -> dict:
    from autoresearch.trace.capsule import require_active_run

    transactional = handle_loader is None and publisher is None and finalizer is None
    if transactional:
        try:
            handle = require_active_run(run_id)
        except RuntimeError:
            from autoresearch.trace.capsule import load_run
            from autoresearch.trace.publication import load_publication_journal

            handle = load_run(run_id)
            journal = load_publication_journal(handle.workspace)
            if journal["state"] not in {"VIEWS_APPLIED", "COMMITTED"}:
                raise
    else:
        handle = (handle_loader or require_active_run)(run_id)
    current = status(run_id, handle_loader=lambda unused: handle)
    if current["state"] != "DONE":
        raise RuntimeError("cannot finish an incomplete task graph")
    from autoresearch.session_agent.evidence import materialize_evidence

    if transactional:
        journal_path = Path(handle.workspace) / "publication/journal.json"
        if journal_path.is_file():
            evidence_path = Path(handle.capsule) / "verification/evidence_closure.json"
            evidence = _read_json(evidence_path) if evidence_path.is_file() else {"resumed": True}
        else:
            evidence = materialize_evidence(handle)
        from autoresearch.session_agent.publication import transactional_finish

        publication = transactional_finish(handle)
        return _result(
            "finish",
            handle.run_id,
            "DONE",
            result={
                "evidence": evidence,
                "publication": publication,
                "finalization": {
                    "root_hash": publication["receipt"]["capsule_root_hash"],
                    "final_path": publication["canonical_path"],
                },
            },
        )
    evidence = materialize_evidence(handle)
    report = (publisher or publish_run)(handle)
    finalized = (finalizer or _default_finalizer)(handle, report)
    return _result(
        "finish",
        handle.run_id,
        "DONE",
        result={
            "evidence": evidence,
            "publication": report,
            "finalization": finalized,
        },
    )


__all__ = [
    "begin",
    "calculate",
    "source_fields",
    "claim",
    "execute",
    "fail",
    "finish",
    "next",
    "precheck",
    "resume",
    "retry_l4",
    "status",
    "submit",
]
