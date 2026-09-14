"""Offline sector FULL/LITE operation adapters."""

from __future__ import annotations

from autoresearch.session_agent import domain_ops
from autoresearch.session_agent.replay_adapters.common import (
    export_outputs,
    session_request,
    source_snapshot,
    stage_inputs,
    virtual_handle,
)
from autoresearch.session_agent.workflows.sector import register_sector_artifacts

_OPERATIONS = {
    "sector.validate": domain_ops.sector_validate,
    "sector.publish": domain_ops.sector_prepare_publication,
}


def execute(unit: dict, context) -> list[dict]:
    operation = unit["operation"]
    if operation == "sector.prepare":
        snapshot = source_snapshot(context, "sector.prepare.snapshot.v1")
        rendered = domain_ops.render_sector_snapshot(
            snapshot, staging_root=context.work / "sector"
        )
        mapping = {
            "sector.input.manifest": "manifest",
            "sector.pack": "pack",
            "sector.reuse": "reuse",
        }
        for ref in unit["expected_outputs"]:
            artifact_id = ref["artifact_id"]
            context.output_path(artifact_id).write_bytes(
                rendered[mapping[artifact_id]].read_bytes()
            )
        return []
    if operation not in _OPERATIONS:
        raise KeyError(f"unsupported sector replay operation: {operation}")
    request = session_request(context)
    handle = virtual_handle(context, request)
    stage_inputs(
        context,
        handle,
        lambda: register_sector_artifacts(request, handle, {}),
    )
    _OPERATIONS[operation](handle)
    export_outputs(context, handle)
    return []


__all__ = ["execute"]
