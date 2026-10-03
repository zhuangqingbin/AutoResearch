"""Freeze the root-owned context that card semantics consume, for offline replay.

A publish/assemble operation reads the frozen card-rules profile, the owner task
store (which attempt produced the card) and the run's material claims. None of
these are task artifacts, so a replay scratch handle would silently fall back to
legacy rules. The live operation freezes exactly these bytes as an attempt
record; replay restores them into its virtual handle and nothing else.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from autoresearch.common.atomic import atomic_write_json, canonical_json
from autoresearch.trace.blobs import blob_path
from autoresearch.trace.source_receipts import read_receipts

ARTIFACT_ID = "card.semantics_context"
RECORD_KIND = "card_semantics_context"
#: Operations whose outputs embed registered card semantics.
OPERATIONS = frozenset({"stock.publish", "stock.full.assemble"})


def _read_json(path: Path) -> dict:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def freeze(handle) -> Path | None:
    """Called inside the live session operation; a non-session call freezes nothing."""
    from autoresearch.session_agent.evidence import _freeze_attempt_record
    from autoresearch.trace.replay import REPLAY_ENV

    task_id = str(os.environ.get("AUTORESEARCH_TASK_ID", "")).strip()
    owner = Path(handle.workspace) / "session/tasks.json"
    if not task_id or not owner.is_file() or os.environ.get(REPLAY_ENV):
        return None  # replay consumes the frozen record; it never re-freezes one
    attempt = int(os.environ.get("AUTORESEARCH_ATTEMPT", "1"))
    capsule = Path(handle.capsule)
    profile_path = capsule / "verification/profile.json"
    claims = [_read_json(path) for path in
              sorted((capsule / "evidence/material_claims").glob("*.json"))]
    wanted = {rid for claim in claims
              for rid in (*claim.get("source_receipt_ids", []), *claim.get("review_receipt_ids", []))}
    rows = {row["receipt_id"]: row for row in read_receipts(capsule)} if wanted else {}
    pending, selected = list(wanted), set()
    while pending:  # a claim's source keeps its supersession chain for evaluation
        receipt_id = pending.pop()
        if receipt_id in selected:
            continue
        selected.add(receipt_id)
        pending.extend((rows.get(receipt_id) or {}).get("supersedes_receipt_ids", []))
    value = {
        "schema_version": 1, "engine": handle.engine, "run_id": handle.run_id,
        "task_id": task_id, "attempt": attempt,
        "profile": _read_json(profile_path) if profile_path.is_file() else None,
        "task_store": _read_json(owner),
        "material_claims": claims,
        "source_receipt_ids": sorted(selected),
    }
    return _freeze_attempt_record(handle, task_id, attempt, RECORD_KIND, value)


def claim_handle(handle):
    """The handle whose owner store/capsule card claims are bound to (replay-restored or live)."""
    return getattr(handle, "card_claim_handle", None) or handle


def restore(context, handle):
    """Materialize the frozen context in replay scratch; return the claim-context handle.

    The card-rules profile goes to the artifact handle's capsule. The owner store
    and claims go to a separate workspace: the live store names accepted artifacts
    by live paths and must never steer the virtual artifact registry.
    """
    if not any(ref.get("artifact_id") == ARTIFACT_ID for ref in context.unit["input_refs"]):
        return None
    from types import SimpleNamespace

    from autoresearch.session_agent.replay_adapters.common import input_path
    from autoresearch.trace.replay import REPLAY_ENV

    value = _read_json(input_path(context, ARTIFACT_ID))
    if value.get("schema_version") != 1:
        raise ValueError("unsupported card semantics context")
    if (value["engine"], value["run_id"]) != (handle.engine, handle.run_id):
        raise ValueError("card semantics context identity differs from replay run")
    if value["profile"] is not None:
        atomic_write_json(Path(handle.capsule) / "verification/profile.json", value["profile"])
    workspace = Path(context.work) / "card_semantics_context"
    capsule = workspace / "capsule"
    capsule.mkdir(parents=True, exist_ok=True)
    atomic_write_json(workspace / "session/tasks.json", value["task_store"])
    for claim in value["material_claims"]:
        atomic_write_json(capsule / "evidence/material_claims" / f"{claim['sidecar_id']}.json", claim)
    receipts = {row["receipt_id"]: row for row in context.source_receipts}
    if set(value["source_receipt_ids"]) - receipts.keys():
        raise ValueError("card semantics replay lacks frozen source receipts")
    if value["source_receipt_ids"]:
        selected = [receipts[key] for key in value["source_receipt_ids"]]
        lineage = capsule / "lineage/source_receipts.jsonl"
        lineage.parent.mkdir(parents=True, exist_ok=True)
        lineage.write_text("".join(canonical_json(row) + "\n" for row in selected),
                           encoding="utf-8")
        source_root = Path(context.env[REPLAY_ENV])
        for receipt in selected:
            for key in ("payload_hash", "raw_hash"):
                digest = receipt.get(key)
                if digest:
                    target = blob_path(capsule, digest)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(blob_path(source_root, digest).read_bytes())
    return SimpleNamespace(workspace=workspace, capsule=capsule,
                           run_id=value["run_id"], engine=value["engine"])


__all__ = ["ARTIFACT_ID", "OPERATIONS", "RECORD_KIND", "claim_handle", "freeze", "restore"]
