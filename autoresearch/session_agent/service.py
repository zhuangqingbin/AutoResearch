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
        from autoresearch.analyze import runctl
        from autoresearch.analyze.run_bootstrap import prepare_analyze_run
        from autoresearch.trace.capsule import begin_run

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
            bootstrap=prepare_analyze_run,
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
    return {
        "schema_version": 1,
        "run_id": predecessor_id,
        "engine": predecessor.engine,
        "business_status": business_status,
    }


def begin(
    request: dict,
    *,
    begin_capsule: Callable[[dict], object] | None = None,
    planner: Callable[[dict, object], dict] | None = None,
    predecessor_loader=None,
    artifact_registrar=None,
) -> dict:
    """Validate and freeze a request before exposing its first ready task."""
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
    _mirror_identity(handle, "request.json", request)
    _mirror_identity(handle, "host_profile.json", request["host_profile"])
    from autoresearch.session_agent.host_evidence import register_main_context

    register_main_context(handle, request["host_profile"])
    if predecessor is not None:
        _mirror_identity(handle, "predecessor.json", predecessor)
    frozen_plan = (planner or _default_planner)(request, handle)
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
    if artifact_registrar is not None:
        artifact_registrar(request, handle, frozen_plan)
    elif planner is None:
        from autoresearch.session_agent.workflows import register_artifacts

        register_artifacts(request, handle, frozen_plan)
    store.initialize(_store_path(handle), frozen_plan)
    return status(handle.run_id, handle_loader=lambda unused: handle, command="begin")


def _sync_expansion(handle, request: dict, expansion: dict) -> None:
    from autoresearch.session_agent.workflows import register_expansion_artifacts

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
    if ready:
        return "READY", ready, []
    blocked = [task for task in tasks if states.get(task["task_id"]) in {"BLOCKED", "FAILED"}]
    if blocked:
        return (
            "BLOCKED",
            [],
            [{"code": "TASK_BLOCKED", "task_id": task["task_id"]} for task in blocked],
        )
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
    state, tasks, errors = _state(handle)
    progress = None
    if getattr(getattr(handle, "contract", None), "run_kind", None) == "scan-market":
        from autoresearch.session_agent.progress import scan_progress

        progress = scan_progress(handle)
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
    artifacts.bind_artifact_hash(handle, "scan.l3.repair.result")
    effective = Path(handle.staging) / "_l3_effective_judged.json"
    if effective.is_file():
        artifacts.bind_artifact_hash(handle, "scan.l3.effective.judged")
    task_ids = [task_id]
    if task_id == "scan.l3.repair":
        task_ids.append("scan.l3.repair.apply")
    store.supersede_optional_failure(_store_path(handle), task_ids, error)
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
    if task["parent_task"] is not None:
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
    config = getattr(handle.contract, "user_config", {}) or {}
    intel_enabled = bool((config.get("l4_intel") or {}).get("enabled"))
    prompt_snapshot = artifacts.snapshot_artifact(handle, f"scan.l4.{code6}.a1.prompt")
    expansion = l4_retry_expansion(
        frozen_plan,
        code6,
        expected_attempt,
        [
            {
                "artifact_id": prompt_snapshot["artifact_id"],
                "sha256": prompt_snapshot["sha256"],
            }
        ],
        intel_enabled=intel_enabled,
    )
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


def _promote_l4_retry_output(handle, task: dict) -> None:
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
        handoff = {
            "schema_version": 1,
            "envelope": envelope,
            "plan_hash": claim_result["plan_hash"],
            "request": claim_result["request"],
            "claim_receipt": receipt,
        }
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
    execution = executor.execute_operation(handle, task, attempt, params, **kwargs)
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
    outputs = []
    for artifact_id in task["output_artifact_ids"]:
        descriptor = artifacts.bind_artifact_hash(handle, artifact_id)
        outputs.append({"artifact_id": artifact_id, "sha256": descriptor["sha256"]})
    receipt = store.complete_deterministic(
        _store_path(handle), task_id, attempt, outputs, execution
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
    if task["parent_task"] is not None:
        from autoresearch.session_agent import legacy_scan

        legacy_scan.verify_child_handoff(handle, task, submission["envelope"])
    host_profile = _read_json(_session_dir(handle) / "host_profile.json")
    entry = store.read_entry(_store_path(handle), task["task_id"])
    _verify_frozen_inputs(handle, task, entry)
    if host_receipt is not None:
        validate_receipt(task, host_receipt, host_profile)
        if host_receipt["attempt"] != submission["envelope"]["attempt"]:
            raise ValueError("host receipt attempt mismatch")
        host_receipt_id = sha256_bytes(canonical_json(host_receipt).encode("utf-8"))
        if submission["host_receipt_id"] != host_receipt_id:
            raise ValueError("host_receipt_id does not match the verified receipt")
        from autoresearch.session_agent.host_evidence import resolve_receipt_evidence

        resolve_receipt_evidence(handle, task, host_receipt)
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

    def checked(value: dict, spec: dict) -> None:
        domain_validator = validator
        if domain_validator is None:

            def domain_validator(submitted, task):
                return validate_registered_contract(handle, submitted, task)

        validate_submission_outputs(handle, value, spec, domain_validator=domain_validator)

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
                recovered.append(receipt)
                if task["kind"] == "INFERENCE":
                    completion_path = (
                        Path(handle.capsule)
                        / "agents/session/completions"
                        / f"{task['task_id']}-a{receipt['attempt']}.json"
                    )
                    if completion_path.is_file():
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
    "claim",
    "execute",
    "fail",
    "finish",
    "next",
    "resume",
    "retry_l4",
    "status",
    "submit",
]
