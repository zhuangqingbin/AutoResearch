"""Offline stock FULL/LITE operation adapters."""

from __future__ import annotations

from autoresearch.analyze.harvest import render_harvest_snapshot
from autoresearch.common.atomic import atomic_write_json
from autoresearch.session_agent import artifacts, domain_ops
from autoresearch.session_agent.replay_adapters.common import (
    export_outputs,
    session_request,
    source_snapshot,
    stage_inputs,
    virtual_handle,
)
from autoresearch.session_agent.workflows.stock import register_stock_artifacts
from autoresearch.trace.operation_clock import operation_clock

_OPERATIONS = {
    "stock.validate": domain_ops.stock_validate,
    "stock.publish": domain_ops.stock_prepare_publication,
    "stock.full.validate": domain_ops.stock_full_validate,
    "stock.full.assemble": domain_ops.stock_full_assemble,
}


def execute(unit: dict, context) -> list[dict]:
    operation = unit["operation"]
    if operation == "stock.harvest":
        snapshot = source_snapshot(context, "stock.harvest.snapshot.v1")
        rendered = render_harvest_snapshot(
            snapshot,
            output_dir=context.work / "stock_harvest",
            clock=operation_clock(),
        )
        mapping = (
            {"stock.slim": "primary", "stock.deep": "deep"}
            if snapshot["slim"]
            else {"stock.context": "primary", "stock.indicators": "indicators"}
        )
        for ref in unit["expected_outputs"]:
            artifact_id = ref["artifact_id"]
            key = mapping[artifact_id]
            context.output_path(artifact_id).write_bytes(rendered[key].read_bytes())
        return []
    if operation not in _OPERATIONS and operation != "stock.evidence_bundle":
        raise KeyError(f"unsupported stock replay operation: {operation}")
    request = session_request(context)
    handle = virtual_handle(context, request)
    stage_inputs(context, handle, lambda: register_stock_artifacts(request, handle, {}))
    from autoresearch.session_agent.card_semantics_context import restore

    # Frozen card rules/owner/claims the live operation consumed; claims bind to their own handle.
    handle.card_claim_handle = restore(context, handle)
    if operation == "stock.evidence_bundle":
        from autoresearch.session_agent.evidence_bundle import build_bundle

        source_ids = [ref["artifact_id"] for ref in unit["input_refs"]
                      if ref["artifact_id"] == "research.frame" or ref["artifact_id"].startswith("stock.")]
        atomic_write_json(artifacts.declared_path(handle, "stock.evidence_bundle"), build_bundle(handle, source_ids))
    else:
        _OPERATIONS[operation](handle)
    export_outputs(context, handle)
    return []
