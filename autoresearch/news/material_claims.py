"""Sidecar links material claims to existing source, quote, and calculation evidence."""

from __future__ import annotations

import json
from pathlib import Path

from autoresearch.common.atomic import atomic_write_json, canonical_json, sha256_bytes
from autoresearch.contracts.calculation import validate_calculation
from autoresearch.trace.blobs import blob_path
from autoresearch.trace.source_receipts import read_receipts

_FIELDS = {
    "schema_version",
    "sidecar_id",
    "engine",
    "run_id",
    "task_id",
    "attempt",
    "claim_id",
    "statement_sha256",
    "source_receipt_ids",
    "quote_refs",
    "calculation_ids",
}
_V2_FIELDS = _FIELDS | {"claim_event", "review_receipt_ids"}
_QUOTE_FIELDS = {"blob_hash", "start", "end", "text_sha256"}


def _id(value: dict) -> str:
    return sha256_bytes(
        canonical_json({key: item for key, item in value.items() if key != "sidecar_id"}).encode(
            "utf-8"
        )
    )


def _validate(root: Path, value: dict) -> list[str]:
    missing = []
    if not isinstance(value, dict) or set(value) != (_V2_FIELDS if value.get("schema_version") == 2 else _FIELDS):
        return ["INVALID_SIDECAR_FIELDS"]
    if type(value["schema_version"]) is not int or value["schema_version"] not in {1, 2} or value["sidecar_id"] != _id(value):
        missing.append("INVALID_SIDECAR_IDENTITY")
    if value["engine"] not in {"codex", "claude"}:
        missing.append("INVALID_ENGINE")
    if type(value["attempt"]) is not int or value["attempt"] < 1:
        missing.append("INVALID_ATTEMPT")
    try:
        receipts = {row["receipt_id"]: row for row in read_receipts(root)}
    except Exception:
        receipts = {}
        missing.append("SOURCE_RECEIPTS_INVALID")
    if value["schema_version"] == 2:
        from autoresearch.contracts.claim_evidence import validate_event
        if value["claim_event"] is not None:
            validate_event(value["claim_event"])
        if type(value["review_receipt_ids"]) is not list:
            return ["INVALID_REVIEW_RECEIPTS"]
    for receipt_id in [*value["source_receipt_ids"], *value.get("review_receipt_ids", [])]:
        receipt = receipts.get(receipt_id)
        if receipt is None:
            missing.append(f"SOURCE_RECEIPT_MISSING:{receipt_id}")
        elif receipt["engine"] != value["engine"] or receipt["run_id"] != value["run_id"]:
            missing.append(f"SOURCE_RUN_MISMATCH:{receipt_id}")
        elif not blob_path(root, receipt["payload_hash"]).is_file():
            missing.append(f"SOURCE_PAYLOAD_MISSING:{receipt_id}")
        elif sha256_bytes(blob_path(root, receipt["payload_hash"]).read_bytes()) != receipt["payload_hash"]:
            missing.append(f"SOURCE_PAYLOAD_HASH_MISMATCH:{receipt_id}")
    for calculation_id in value["calculation_ids"]:
        path = root / "evidence/calculations" / f"{calculation_id}.json"
        if not path.is_file():
            missing.append(f"CALCULATION_MISSING:{calculation_id}")
        else:
            calculation = json.loads(path.read_text(encoding="utf-8"))
            try:
                validate_calculation(calculation)
            except (TypeError, ValueError):
                missing.append(f"CALCULATION_INVALID:{calculation_id}")
            if calculation.get("calculation_id") != calculation_id:
                missing.append(f"CALCULATION_IDENTITY_MISMATCH:{calculation_id}")
    for quote in value["quote_refs"]:
        if not isinstance(quote, dict) or set(quote) != _QUOTE_FIELDS:
            missing.append("QUOTE_REF_INVALID")
            continue
        path = blob_path(root, str(quote.get("blob_hash") or ""))
        if not path.is_file():
            missing.append(f"QUOTE_BLOB_MISSING:{quote.get('blob_hash')}")
            continue
        try:
            payload = path.read_bytes()
            if sha256_bytes(payload) != quote["blob_hash"]:
                missing.append(f"QUOTE_BLOB_HASH_MISMATCH:{quote['blob_hash']}")
            text = payload.decode("utf-8")
            start, end = quote["start"], quote["end"]
            if (
                type(start) is not int
                or type(end) is not int
                or not 0 <= start < end <= len(text)
                or sha256_bytes(text[start:end].encode("utf-8")) != quote["text_sha256"]
            ):
                missing.append(f"QUOTE_SPAN_MISMATCH:{quote['blob_hash']}")
        except (OSError, UnicodeDecodeError):
            missing.append(f"QUOTE_BLOB_INVALID:{quote['blob_hash']}")
    if value["schema_version"] == 1 and not (value["source_receipt_ids"] or value["quote_refs"] or value["calculation_ids"]):
        missing.append("MATERIAL_CLAIM_HAS_NO_EVIDENCE")
    return sorted(set(missing))


def bind_material_claim(
    capsule: Path | str,
    *,
    engine: str,
    run_id: str,
    task_id: str,
    attempt: int,
    claim_id: str,
    statement: str,
    source_receipt_ids: list[str],
    quote_refs: list[dict],
    calculation_ids: list[str],
    claim_event: dict | None = None,
    review_receipt_ids: list[str] | None = None,
) -> dict:
    root = Path(capsule)
    quotes = [
        {
            "blob_hash": row["blob_hash"],
            "start": row["start"],
            "end": row["end"],
            "text_sha256": sha256_bytes(str(row["text"]).encode("utf-8")),
        }
        for row in quote_refs
    ]
    value = {
        "schema_version": 1,
        "sidecar_id": "0" * 64,
        "engine": engine,
        "run_id": run_id,
        "task_id": task_id,
        "attempt": attempt,
        "claim_id": claim_id,
        "statement_sha256": sha256_bytes(statement.encode("utf-8")),
        "source_receipt_ids": sorted(set(source_receipt_ids)),
        "quote_refs": quotes,
        "calculation_ids": sorted(set(calculation_ids)),
    }
    if review_receipt_ids is not None:
        value.update(schema_version=2, claim_event=claim_event,
                     review_receipt_ids=sorted(set(review_receipt_ids)))
    value["sidecar_id"] = _id(value)
    missing = _validate(root, value)
    if missing and (value["schema_version"] == 1 or any(item.startswith("INVALID_") for item in missing)):
        raise ValueError(f"material claim evidence is incomplete: {missing}")
    target = root / "evidence/material_claims" / f"{value['sidecar_id']}.json"
    if target.is_file() and json.loads(target.read_text(encoding="utf-8")) != value:
        raise RuntimeError("material claim sidecar changed")
    atomic_write_json(target, value)
    return value


def verify_material_claims(capsule: Path | str, *, decision_frame: dict | None = None) -> dict:
    """Count logical claims once; retain all immutable versions for audit.

    There is no trusted 'latest' pointer here. Different statement/event versions
    of one claim therefore remain UNKNOWN rather than selecting by mtime/hash.
    """
    root = Path(capsule)
    folder = root / "evidence/material_claims"
    files = sorted(folder.glob("*.json")) if folder.is_dir() else []
    missing, groups = [], {}
    for path in files:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            missing.extend(_validate(root, value))
            key = (value["engine"], value["run_id"], value["claim_id"])
            groups.setdefault(key, []).append(value)
            if path.stem != value.get("sidecar_id"):
                missing.append(f"SIDECAR_FILENAME_MISMATCH:{path.name}")
        except Exception as exc:
            missing.append(f"SIDECAR_INVALID:{path.name}:{type(exc).__name__}")
            groups.setdefault(("invalid", "invalid", path.name), []).append(None)
    results = []
    if decision_frame is not None:
        for key, versions in groups.items():
            identities = {canonical_json({"statement": row["statement_sha256"],
                                          "event": row.get("claim_event")})
                          for row in versions if row is not None}
            unknown = {"claim_id": key[2], "verdict": "UNKNOWN", "source": "UNKNOWN",
                       "semantic": "UNKNOWN", "timing": "UNKNOWN", "conflict": "UNKNOWN",
                       "received_by_cutoff": "UNKNOWN", "versions": len(versions)}
            if len(identities) != 1 or any(row is None for row in versions):
                results.append({**unknown, "reason": "CURRENT_VERSION_AMBIGUOUS"})
                continue
            checked = []
            for value in versions:
                try:
                    checked.append(evaluate_material_claim(root, value, decision_frame=decision_frame))
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    checked.append({**unknown, "reason": "CLAIM_VERIFICATION_FAILED", "detail": str(exc)})
            combined = dict(checked[0], versions=len(versions))
            for field in ("verdict", "source", "semantic", "timing", "conflict", "received_by_cutoff"):
                values = {row.get(field, "UNKNOWN") for row in checked}
                combined[field] = next(iter(values)) if len(values) == 1 else "UNKNOWN"
            if len({row["verdict"] for row in checked}) > 1:
                combined["reason"] = "CURRENT_EVIDENCE_AMBIGUOUS"
            results.append(combined)
    n = len(groups)
    extra = {} if decision_frame is None else {
        "supported": sum(row["verdict"] == "PASS" for row in results),
        "refuted": sum(row["verdict"] == "FAIL" for row in results),
        "unknown": sum(row["verdict"] == "UNKNOWN" for row in results),
        "results": results,
    }
    return {**extra, "claims": n, "history_sidecars": len(files),
            "coverage_basis": "run/claim_id; conflicting versions remain UNKNOWN",
            "ok": bool(files) and not missing, "missing": sorted(set(missing))}


__all__ = ["bind_material_claim", "verify_material_claims", "evaluate_material_claim"]


def evaluate_material_claim(capsule: Path | str, value: dict, *, decision_frame: dict) -> dict:
    """Integrity is separate from support; every saved claim retains its denominator seat.

    A field review requires a registered root producer and recomputation from the
    original source. Legacy provider/reviewer strings cannot authorize fields.
    Human review awaits a separately authorized import, never a model-filled name.
    """
    from autoresearch.contracts.execution import parse_aware, validate_decision_frame
    from autoresearch.contracts.source_time import latest_possible
    from autoresearch.news.claim_support import merge_sources

    root = Path(capsule)
    cutoff = parse_aware(validate_decision_frame(decision_frame)["knowledge_cutoff"])
    result = {"claim_id": value.get("claim_id"), "verdict": "UNKNOWN", "source": "UNKNOWN",
              "semantic": "UNKNOWN", "timing": "UNKNOWN", "conflict": "UNKNOWN",
              "received_by_cutoff": "UNKNOWN", "reason": "SOURCE_NOT_BOUND", "related_receipt_ids": []}
    missing = _validate(root, value)
    if missing:
        return {**result, "reasons": missing}
    receipts = {row["receipt_id"]: row for row in read_receipts(root)}
    ids = value["source_receipt_ids"]
    if not ids:
        return result
    result["source"] = "PASS"
    observations, texts = {}, {}
    received = []
    for rid in ids:
        row = receipts[rid]
        if row["status"] != "SUCCEEDED":
            return {**result, "source": "UNKNOWN", "reason": "SOURCE_UNAVAILABLE"}
        times = row.get("source_timing")
        available = (latest_possible(times["first_available_at"], times["timestamp_precision"]["first_available_at"])
                     if times else parse_aware(row["available_at"]))
        published = (latest_possible(times["published_at"], times["timestamp_precision"]["published_at"])
                     if times else None)
        reception = parse_aware(times["received_at"] if times else row["ended_at"])
        received.append(reception is not None and reception <= cutoff)
        if available is None or available > cutoff or (published is not None and published > cutoff):
            return {**result, "reason": "NOT_AVAILABLE_AT_DECISION"}
        observations[rid] = {"available_at": available}
        if row["codec"] == "dataframe.parquet.v1":
            continue  # Typed fields are verified against original bytes below, never decoded as text.
        try:
            texts[rid] = blob_path(root, row["payload_hash"]).read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return {**result, "reason": "SOURCE_TEXT_UNAVAILABLE"}
    result.update(timing="PASS", received_by_cutoff="PASS" if all(received) else "UNKNOWN")
    # A correction/retraction is append-only. A future correction does not rewrite history.
    changes = []
    for row in receipts.values():
        if not set(row.get("supersedes_receipt_ids", [])).intersection(ids):
            continue
        times = row.get("source_timing")
        available = (latest_possible(times["first_available_at"], times["timestamp_precision"]["first_available_at"])
                     if times else parse_aware(row["available_at"]))
        if available is None or available <= cutoff:
            changes.append(row["receipt_id"])
    if changes or any(receipts[rid].get("source_status") in {"RETRACTED", "CORRECTED"} for rid in ids):
        return {**result, "reason": "SOURCE_NOT_CURRENT", "related_receipt_ids": changes}
    result["conflict"] = "PASS"
    claim_event = value.get("claim_event")
    if claim_event is None:
        return {**result, "reason": "SEMANTIC_NOT_VERIFIED"}
    results = []
    for rid in value.get("review_receipt_ids", []):
        review_receipt = receipts[rid]
        if (review_receipt["provider"] not in {"deterministic", "human_review"}
                or review_receipt["endpoint"] != "claim_fields.v1"
                or review_receipt["status"] != "SUCCEEDED"):
            continue
        # No provider string or model-supplied reviewer name can authorize fields.
        # Human review requires a separate authorized import owner; until present,
        # those legacy receipts remain readable but cannot establish semantic PASS.
        if review_receipt["provider"] != "deterministic":
            continue
        from autoresearch.news.claim_support import compare_events
        from autoresearch.news.source_fields import (
            admissible_attempts,
            linked_claim,
            matching_requests,
            owner_entries,
            replay_review,
            require_source_identity,
        )
        try:
            source_id = review_receipt["normalized_params"]["source_receipt_id"]
            if source_id not in ids:
                continue
            entries = owner_entries(root)
            allowed = admissible_attempts(entries, value["task_id"], value["attempt"])
            require_source_identity(review_receipt, engine=value["engine"], run_id=value["run_id"], allowed=allowed)
            require_source_identity(receipts[source_id], engine=value["engine"], run_id=value["run_id"], allowed=allowed)
            matches = matching_requests(root, list(receipts.values()), claim_event, frame=decision_frame,
                owner={"engine":value["engine"],"run_id":value["run_id"],"allowed":allowed})
            if not any(row["source_receipt_id"] == source_id and row["selector"] == review_receipt["normalized_params"]["selector"] for row in matches):
                continue
            review = replay_review(root, review_receipt, receipts[source_id], decision_frame)
            # Match identity from the independently unique raw source row, before
            # comparing semantics. Amount and lifecycle never define the identity.
            results.append(compare_events(linked_claim(claim_event, review["event"]),
                review["event"], checked_fields=review["checked_fields"]))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    merged = merge_sources(results)
    return {**result, "verdict": merged["verdict"], "semantic": merged["verdict"],
            "reason": merged["reason"], "conflict": "UNKNOWN" if merged["reason"] == "source_conflict" else "PASS"}
