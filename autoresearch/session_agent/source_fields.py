"""Root orchestration and frozen replay inputs for deterministic claim fields."""
from __future__ import annotations

import json
from pathlib import Path

from autoresearch.common.atomic import canonical_json
from autoresearch.news.source_fields import _REGISTRY, ADAPTER_ID, checked_payload
from autoresearch.trace.blobs import blob_path
from autoresearch.trace.source_receipts import read_receipts


def intel_source_context(scan_dir):
    from autoresearch.session_agent.service import source_fields
    from autoresearch.trace.frozen_sources import intel_claim_sources

    frozen = intel_claim_sources(scan_dir)
    if frozen is None:
        return None
    handle = frozen["handle"]

    def produce(request):
        return source_fields(handle.run_id, frozen["task_id"], frozen["attempt"],
            request, handle_loader=lambda _: handle)["result"]

    return {**frozen, "produce_fields": produce,
            "freeze_context": lambda: freeze_intel_context(frozen)}


def freeze_intel_context(frozen):
    """Reuse immutable attempt records for offline intel-status verification inputs."""
    from autoresearch.session_agent import service, store
    from autoresearch.session_agent.evidence import _freeze_attempt_record
    from autoresearch.trace.events import verify_event_chain

    if frozen.get("replay"):
        return
    handle, task_id, attempt = frozen["handle"], frozen["task_id"], frozen["attempt"]
    if not hasattr(handle, "workspace"):
        return
    owner = Path(handle.workspace) / "session/tasks.json"
    if not owner.is_file():
        return  # Historical non-session guard runs never had root authorization.
    entry = store.read_entry(owner, task_id)
    if entry["state"] != "RUNNING" or entry["attempt"] != attempt:
        raise ValueError("intel source context attempt is not running")
    service._verify_frozen_inputs(handle, service._task(handle, task_id), entry)
    events = Path(handle.capsule) / "events/events.jsonl"
    if not verify_event_chain(events)["ok"]:
        raise ValueError("intel source context event chain invalid")
    receipts = read_receipts(handle.capsule)
    # Retain registered candidates (including ambiguity), exact bound text sources,
    # their reviews/corrections and occurrence predecessors; unrelated market blobs
    # are not needed to replay this stock's material claims.
    wanted = {
        row["receipt_id"]
        for row in receipts
        if (row["provider"], row["endpoint"], row["codec"]) == _REGISTRY[ADAPTER_ID][:3]
    }
    for path in (Path(handle.capsule) / "evidence/material_claims").glob("*.json"):
        claim = json.loads(path.read_bytes())
        if claim["task_id"] == task_id and claim["attempt"] == attempt:
            wanted.update(claim["source_receipt_ids"])
            wanted.update(claim.get("review_receipt_ids", []))
    changed = True
    while changed:
        before = set(wanted)
        keys = {
            (
                row["task_id"],
                row["attempt"],
                row["provider"],
                row["endpoint"],
                canonical_json(row["normalized_params"]),
            )
            for row in receipts
            if row["receipt_id"] in wanted
        }
        for row in receipts:
            key = (
                row["task_id"],
                row["attempt"],
                row["provider"],
                row["endpoint"],
                canonical_json(row["normalized_params"]),
            )
            if key in keys or wanted.intersection(row.get("supersedes_receipt_ids", [])):
                wanted.add(row["receipt_id"])
        changed = before != wanted
    value = {
        "schema_version": 1,
        "engine": handle.engine,
        "run_id": handle.run_id,
        "task_id": task_id,
        "attempt": attempt,
        "frame": frozen["frame"],
        "task_store": json.loads(owner.read_bytes()),
        "events": [json.loads(line) for line in events.read_text().splitlines() if line.strip()],
        "source_receipt_ids": [
            row["receipt_id"] for row in receipts if row["receipt_id"] in wanted
        ],
    }
    _freeze_attempt_record(handle, task_id, attempt, "claim_source_context", value)


def restore_replay_context(context):
    """Materialize only captured owners and receipts in replay scratch; no live workspace."""
    from types import SimpleNamespace

    from autoresearch.common.atomic import atomic_write_json
    from autoresearch.session_agent.replay_adapters.common import input_path
    from autoresearch.trace.replay import REPLAY_ENV

    value = json.loads(input_path(context, "claim.source_context").read_bytes())
    if value["schema_version"] != 1:
        raise ValueError("unsupported claim source context")
    receipts = {row["receipt_id"]: row for row in context.source_receipts}
    if set(value["source_receipt_ids"]) - receipts.keys():
        raise ValueError("claim source replay lacks frozen receipts")
    workspace = Path(context.work) / "claim_source_context"
    root = workspace / "capsule"
    root.mkdir(parents=True, exist_ok=True)
    atomic_write_json(workspace / "session/tasks.json", value["task_store"])
    (root / "events").mkdir(exist_ok=True)
    (root / "events/events.jsonl").write_text(
        "".join(canonical_json(row) + "\n" for row in value["events"])
    )
    selected = [receipts[key] for key in value["source_receipt_ids"]]
    (root / "lineage").mkdir(exist_ok=True)
    (root / "lineage/source_receipts.jsonl").write_text(
        "".join(canonical_json(row) + "\n" for row in selected)
    )
    source_root = Path(context.env[REPLAY_ENV])
    for receipt in selected:
        checked_payload(source_root, receipt)
        for key in ("payload_hash", "raw_hash"):
            digest = receipt[key]
            if digest is not None:
                target = blob_path(root, digest)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(blob_path(source_root, digest).read_bytes())
    handle = SimpleNamespace(
        workspace=workspace, capsule=root, run_id=value["run_id"], engine=value["engine"]
    )
    return {
        "handle": handle,
        "frame": value["frame"],
        "receipts": selected,
        "task_id": value["task_id"],
        "attempt": value["attempt"],
        "replay": True,
    }
