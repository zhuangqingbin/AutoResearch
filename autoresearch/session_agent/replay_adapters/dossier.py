"""Offline dossier initialization operation adapters."""

from __future__ import annotations

from pathlib import Path

from autoresearch.session_agent import domain_ops
from autoresearch.session_agent.replay_adapters.common import (
    export_outputs,
    input_path,
    session_request,
    source_snapshot,
    stage_inputs,
    virtual_handle,
)
from autoresearch.session_agent.workflows.dossier import register_dossier_artifacts


def _handle(context):
    request = session_request(context)
    handle = virtual_handle(context, request)
    stage_inputs(
        context,
        handle,
        lambda: register_dossier_artifacts(request, handle, {}),
    )
    return handle


def execute(unit: dict, context) -> list[dict]:
    operation = unit["operation"]
    if operation == "dossier.prefetch":
        snapshot = source_snapshot(context, "dossier.prefetch.snapshot.v1")
        target = context.output_path("dossier.prefetch")
        domain_ops.render_dossier_prefetch_snapshot(snapshot, output_path=target)
        return []
    if operation == "dossier.skeleton":
        snapshot = source_snapshot(context, "dossier.skeleton.snapshot.v1")
        handle = _handle(context)
        output = Path(handle.staging) / "session_outputs"
        domain_ops.render_dossier_skeleton_snapshot(
            snapshot,
            prefetch_path=input_path(context, "dossier.prefetch"),
            output_dir=output,
            scratch_root=context.work / "dossier_inputs",
        )
        export_outputs(context, handle)
        return []
    if operation == "dossier.validate":
        handle = _handle(context)
        domain_ops.dossier_validate(handle)
        export_outputs(context, handle)
        return []
    if operation == "dossier.publish":
        snapshot = source_snapshot(context, "dossier.pool.snapshot.v1")
        handle = _handle(context)
        effects = domain_ops.render_dossier_publication_snapshot(handle, snapshot)
        export_outputs(context, handle)
        return effects
    raise KeyError(f"unsupported dossier replay operation: {operation}")


__all__ = ["execute"]
