"""Offline macro FULL/LITE operation adapters."""

from __future__ import annotations

from autoresearch.macro.harvest import render_harvest_snapshot
from autoresearch.session_agent import domain_ops
from autoresearch.session_agent.replay_adapters.common import (
    export_outputs,
    input_path,
    session_request,
    source_snapshot,
    stage_inputs,
    virtual_handle,
)
from autoresearch.session_agent.workflows.macro import register_macro_artifacts
from autoresearch.trace.operation_clock import operation_clock

_OPERATIONS = {
    "macro.intel.prepare": domain_ops.macro_intel_prepare,
    "macro.lite.validate": domain_ops.macro_lite_validate,
    "macro.publish": domain_ops.macro_prepare_publication,
    "macro.full.validate": domain_ops.macro_full_validate,
    "macro.full.assemble": domain_ops.macro_full_assemble,
}


def execute(unit: dict, context) -> list[dict]:
    operation = unit["operation"]
    if operation == "macro.harvest":
        snapshot = source_snapshot(context, "macro.harvest.snapshot.v1")
        rendered = render_harvest_snapshot(
            snapshot,
            output_dir=context.work / "macro_harvest",
            clock=operation_clock(),
        )
        mapping = {
            "macro.data": "data",
            "macro.global_tape": "global_tape",
            "macro.scan_meta": "scan_meta",
        }
        for ref in unit["expected_outputs"]:
            artifact_id = ref["artifact_id"]
            context.output_path(artifact_id).write_bytes(
                rendered[mapping[artifact_id]].read_bytes()
            )
        return []
    if operation == "macro.lite.frame":
        snapshot = source_snapshot(context, "macro.lite.frame.snapshot.v1")
        root = context.work / "macro_lite"
        domain_ops.render_macro_lite_snapshot(snapshot, output_dir=root)
        mapping = {
            "macro.market_pack": root / "market_pack.json",
            "macro.strategist_pack": root / "strategist_pack.json",
        }
        for ref in unit["expected_outputs"]:
            artifact_id = ref["artifact_id"]
            context.output_path(artifact_id).write_bytes(mapping[artifact_id].read_bytes())
        return []
    if operation not in _OPERATIONS:
        raise KeyError(f"unsupported macro replay operation: {operation}")
    request = session_request(context)
    handle = virtual_handle(context, request)
    # Seed the immutable policy before registration: a nondefault frozen cap must
    # not be reinterpreted from the replay process's empty user_config.
    if any(ref["artifact_id"] == "macro.intel.policy" for ref in unit["input_refs"]):
        target = handle.staging / "session_outputs/macro.intel.policy.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(input_path(context, "macro.intel.policy").read_bytes())
    stage_inputs(
        context,
        handle,
        lambda: register_macro_artifacts(request, handle, {}),
    )
    _OPERATIONS[operation](handle)
    export_outputs(context, handle)
    return []
