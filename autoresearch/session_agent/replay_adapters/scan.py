"""Offline replay adapters for every executable scan-market operation."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from autoresearch.common.atomic import atomic_write_bytes, atomic_write_json, sha256_bytes
from autoresearch.session_agent import artifacts, domain_ops
from autoresearch.session_agent.replay_adapters.common import (
    export_outputs,
    input_path,
    session_request,
    source_snapshot,
    stage_inputs,
    virtual_handle,
)
from autoresearch.session_agent.workflows import scan as scan_workflow

_BUNDLES = (
    ("scan.prelude.bundle", "prelude"),
    ("scan.sector.source.bundle", "sector"),
    ("scan.l3.source.bundle", "l3_source"),
    ("scan.l3.context.bundle", "l3_context"),
    ("scan.l3.final.bundle", "l3_final"),
    ("scan.l4.source.bundle", "l4_source"),
    ("scan.l4.final.bundle", "l4_final"),
    ("scan.report.build.bundle", "report"),
    ("scan.report.used.bundle", "used_report"),
)

_COMPUTE = {
    "scan.gate1": domain_ops.scan_gate1,
    "scan.sector.skip": domain_ops.scan_sector_skip,
    "scan.l3.lint": domain_ops.scan_l3_lint,
    "scan.l3.repair.skip": domain_ops.scan_l3_repair_skip,
    "scan.l3.repair.apply": domain_ops.scan_l3_repair_apply,
    "scan.l3.merge": domain_ops.scan_l3_merge,
    "scan.gate2.skip": domain_ops.scan_gate2_skip,
    "scan.l4.skip": domain_ops.scan_l4_skip,
    "scan.l4.intel.status": domain_ops.scan_l4_intel_status,
    "scan.l4.intel.disabled": domain_ops.scan_l4_intel_disabled,
    "scan.review.plan": domain_ops.scan_review_plan,
    "scan.review.none": domain_ops.scan_review_none,
    "scan.review.decide": domain_ops.scan_review_decide,
    "scan.review.skip": domain_ops.scan_review_skip,
    "scan.review3.skip": domain_ops.scan_review3_skip,
    "scan.l4.finalize": domain_ops.scan_l4_finalize,
    "scan.l4.complete": domain_ops.scan_l4_complete,
    "scan.assemble": domain_ops.scan_assemble,
    "scan.gate4": domain_ops.scan_gate4,
}


def _scan_handle(context):
    request = session_request(context)
    handle = virtual_handle(context, request)
    handle.staging = Path(handle.workspace) / "staging" / request["analysis_date"]
    handle.staging.mkdir(parents=True, exist_ok=True)
    return request, handle


def _registered_ids(handle) -> set[str]:
    path = Path(handle.workspace) / "session/artifacts.json"
    if not path.is_file():
        return set()
    return set(json.loads(path.read_text(encoding="utf-8"))["artifacts"])


def _register(request: dict, handle, unit: dict) -> None:
    scan_workflow.register_scan_artifacts(request, handle, {})
    registered = _registered_ids(handle)
    produced = {ref["artifact_id"] for ref in unit["expected_outputs"]}
    for artifact_id in [
        *(ref["artifact_id"] for ref in unit["input_refs"]),
        *produced,
    ]:
        if artifact_id in registered or artifact_id in {"session.request"} or artifact_id.startswith(
            "operation.request:"
        ):
            continue
        path, access = scan_workflow._paths_for_artifact(handle, unit, artifact_id)
        artifacts.register_artifact(
            handle,
            artifact_id,
            path,
            "WRITE" if artifact_id in produced else access,
        )
        registered.add(artifact_id)


def _restore_input_bundles(context, handle) -> None:
    available = {ref["artifact_id"] for ref in context.unit["input_refs"]}
    for artifact_id, phase in _BUNDLES:
        if artifact_id not in available:
            continue
        bundle = json.loads(input_path(context, artifact_id).read_text(encoding="utf-8"))
        domain_ops.restore_scan_staging_bundle(
            bundle,
            handle.staging,
            expected_phase=phase,
        )


def _prepare(context):
    request, handle = _scan_handle(context)
    _restore_input_bundles(context, handle)
    _register(request, handle, context.unit)
    stage_inputs(context, handle, lambda: None)
    from autoresearch.scan import user_config

    handle._replay_scan_config_paths = (
        user_config.DEFAULT_PATH,
        user_config.DEFAULT_PINNED_PATH,
    )
    _configure_frozen_paths(handle)
    return request, handle


def _configure_frozen_paths(handle) -> None:
    """Resolve mutable scan config only from the declared prelude bundle."""
    domain_ops._use_frozen_scan_runtime_inputs(handle)


def _restore_config_paths(handle) -> None:
    original = getattr(handle, "_replay_scan_config_paths", None)
    if original is None:
        return
    from autoresearch.scan import user_config

    user_config.DEFAULT_PATH, user_config.DEFAULT_PINNED_PATH = original


def _write_bundle_artifact(handle, artifact_id: str, bundle: dict) -> None:
    registry = json.loads(
        (Path(handle.workspace) / "session/artifacts.json").read_text(encoding="utf-8")
    )["artifacts"]
    target = Path(handle.workspace) / registry[artifact_id]["relative_path"]
    atomic_write_json(target, bundle)


def _source_bundle(context, endpoint: str, phase: str, artifact_id: str, *, wrapper=None):
    request, handle = _scan_handle(context)
    snapshot = source_snapshot(context, endpoint)
    bundle = snapshot if wrapper is None else snapshot[wrapper]
    domain_ops.restore_scan_staging_bundle(bundle, handle.staging, expected_phase=phase)
    _register(request, handle, context.unit)
    if wrapper == "bundle":
        if "sector_list" in snapshot:
            atomic_write_json(
                Path(handle.staging) / "session_outputs/sector.list.json",
                snapshot["sector_list"],
            )
        if "plan" in snapshot:
            atomic_write_json(
                Path(handle.staging) / "session_outputs/l4.plan.json",
                snapshot["plan"],
            )
    _write_bundle_artifact(handle, artifact_id, bundle)
    export_outputs(context, handle)
    return []


def _attempt(unit: dict) -> int:
    parent = unit.get("parent_task") or {}
    if type(parent.get("attempt")) is int:
        return int(parent["attempt"])
    match = re.search(r"\.a(\d+)(?:\.|$)", unit["task_id"])
    return int(match.group(1)) if match else int(unit.get("attempt") or 1)


def _rebase_taskbook(handle, unit: dict, *, complete: bool = False) -> None:
    path = Path(handle.staging) / "_l4_tasks.json"
    if not path.is_file():
        return
    payload = json.loads(path.read_text(encoding="utf-8"))
    tasks = payload.get("tasks") or {}
    attempt = _attempt(unit)
    subject = unit.get("subject")
    for code, task in tasks.items():
        code = str(code).zfill(6)
        from autoresearch.dataflows.symbol_utils import normalize_symbol

        prompt = Path(handle.staging) / f"_l4_prompt_{code}.md"
        slim = (
            Path(handle.staging)
            / "_external_inputs"
            / f"{normalize_symbol(code)}_{handle.analysis_date}_slim.md"
        )
        card = Path(handle.staging) / "details" / f"{code}.md"
        task_attempt = attempt if subject == code else max(1, int(task.get("attempt") or 1))
        task["attempt"] = task_attempt
        task["status"] = "SUCCEEDED" if complete else (
            "RUNNING" if subject == code else str(task.get("status") or "PENDING")
        )
        task.setdefault("artifacts", {})
        for name, target in {"prompt": prompt, "slim": slim, "card": card}.items():
            task["artifacts"][name] = {
                "path": str(target),
                "status": "PRESENT" if target.is_file() else "MISSING",
                "content_hash": sha256_bytes(target.read_bytes()) if target.is_file() else None,
            }
    atomic_write_json(path, payload)


def _promote_retry_inputs(handle, unit: dict) -> None:
    attempt = _attempt(unit)
    code = str(unit.get("subject") or "").zfill(6)
    if attempt < 2 or len(code) != 6:
        return
    retry = Path(handle.staging) / "session_attempts" / code / f"a{attempt}"
    primary = {
        "card.md": Path(handle.staging) / "details" / f"{code}.md",
        "intel.md": Path(handle.staging) / f"_l4_intel_{code}.md",
        "slim.md": None,
    }
    for name, target in primary.items():
        source = retry / name
        if not source.is_file():
            continue
        if target is None:
            from autoresearch.dataflows.symbol_utils import normalize_symbol

            target = (
                Path(handle.staging)
                / "_external_inputs"
                / f"{normalize_symbol(code)}_{handle.analysis_date}_slim.md"
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)


def _materialize_ticket_files_from_context(context, handle) -> None:
    for ref in context.unit["input_refs"]:
        artifact_id = ref["artifact_id"]
        if not artifact_id.endswith(".ticket"):
            continue
        ticket = json.loads(input_path(context, artifact_id).read_text(encoding="utf-8"))
        for raw_relative, encoded in (ticket.get("files") or {}).items():
            relative = Path(raw_relative)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("unsafe scan ticket file path")
            atomic_write_bytes(
                Path(handle.staging) / relative,
                domain_ops._decode_payload(encoded),
            )


def _execute_compute(context):
    operation = context.unit["operation"]
    _, handle = _prepare(context)
    try:
        if operation in {
            "scan.l4.intel.status",
            "scan.l4.intel.disabled",
            "scan.review.none",
            "scan.l4.finalize",
        }:
            _promote_retry_inputs(handle, context.unit)
            _rebase_taskbook(handle, context.unit)
        elif operation == "scan.review.plan":
            for ref in context.unit["input_refs"]:
                match = re.fullmatch(
                    r"scan\.l4\.(\d{6})\.a(\d+)\.card", ref["artifact_id"]
                )
                if match:
                    synthetic = {
                        **context.unit,
                        "subject": match.group(1),
                        "task_id": f"x.a{match.group(2)}",
                    }
                    _promote_retry_inputs(handle, synthetic)
                    _rebase_taskbook(handle, synthetic)
        elif operation == "scan.l4.complete":
            _materialize_ticket_files_from_context(context, handle)
            _rebase_taskbook(handle, context.unit, complete=True)
        subject_operations = {
            "scan.l4.intel.status",
            "scan.l4.intel.disabled",
            "scan.review.none",
            "scan.l4.finalize",
        }
        kwargs = (
            {"code": context.unit.get("subject")}
            if operation in subject_operations
            else {}
        )
        _COMPUTE[operation](handle, **kwargs)
        export_outputs(context, handle)
        return []
    finally:
        _restore_config_paths(handle)


def execute(unit: dict, context) -> list[dict]:
    operation = unit["operation"]
    if operation == "scan.frame":
        snapshot = source_snapshot(context, "scan.frame.snapshot.v1")
        rendered = domain_ops.render_scan_frame_snapshot(snapshot, output_dir=context.work / "frame")
        mapping = {"scan.market.pack": "market_pack", "scan.strategist.pack": "strategist_pack"}
        for ref in unit["expected_outputs"]:
            artifact_id = ref["artifact_id"]
            context.output_path(artifact_id).write_bytes(rendered[mapping[artifact_id]].read_bytes())
        return []
    if operation == "scan.prelude":
        return _source_bundle(
            context,
            "scan.prelude.snapshot.v1",
            "prelude",
            "scan.prelude.bundle",
            wrapper="bundle",
        )
    if operation == "scan.sector.prepare":
        return _source_bundle(
            context,
            "scan.sector.snapshot.v1",
            "sector",
            "scan.sector.source.bundle",
            wrapper="bundle",
        )
    if operation == "scan.l3.prepare":
        return _source_bundle(
            context,
            "scan.l3.prepare.snapshot.v1",
            "l3_source",
            "scan.l3.source.bundle",
        )
    if operation == "scan.l4.prepare":
        return _source_bundle(
            context,
            "scan.l4.prepare.snapshot.v1",
            "l4_source",
            "scan.l4.source.bundle",
            wrapper="bundle",
        )
    if operation == "scan.l4.slim":
        snapshot = source_snapshot(context, "scan.l4.slim.snapshot.v1")
        output = next(ref["artifact_id"] for ref in unit["expected_outputs"])
        domain_ops.render_scan_l4_slim_snapshot(
            snapshot,
            output_path=context.output_path(output),
        )
        return []
    if operation == "scan.usage":
        _, handle = _prepare(context)
        try:
            snapshot = source_snapshot(context, "scan.usage.snapshot.v1")
            domain_ops.render_scan_usage_snapshot(snapshot, staging_root=handle.staging)
            export_outputs(context, handle)
            return []
        finally:
            _restore_config_paths(handle)
    if operation == "scan.observe":
        _, handle = _prepare(context)
        try:
            snapshot = source_snapshot(context, "scan.observe.snapshot.v1")
            effects = domain_ops.render_scan_observation_snapshot(handle, snapshot)
            export_outputs(context, handle)
            return effects
        finally:
            _restore_config_paths(handle)
    if operation in _COMPUTE:
        return _execute_compute(context)
    raise KeyError(f"unsupported scan replay operation: {operation}")


__all__ = ["execute"]
