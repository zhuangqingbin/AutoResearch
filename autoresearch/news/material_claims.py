"""Sidecar links material claims to existing source, quote, and calculation evidence."""

from __future__ import annotations

import json
from pathlib import Path

from autoresearch.common.atomic import atomic_write_json, canonical_json, sha256_bytes
from autoresearch.trace.blobs import blob_path
from autoresearch.trace.source_receipts import read_receipts

_FIELDS = {
    "schema_version", "sidecar_id", "engine", "run_id", "task_id", "attempt",
    "claim_id", "statement_sha256", "source_receipt_ids", "quote_refs",
    "calculation_ids",
}
_QUOTE_FIELDS = {"blob_hash", "start", "end", "text_sha256"}


def _id(value: dict) -> str:
    return sha256_bytes(
        canonical_json({key: item for key, item in value.items() if key != "sidecar_id"})
        .encode("utf-8")
    )


def _validate(root: Path, value: dict) -> list[str]:
    missing = []
    if not isinstance(value, dict) or set(value) != _FIELDS:
        return ["INVALID_SIDECAR_FIELDS"]
    if value["schema_version"] != 1 or value["sidecar_id"] != _id(value):
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
    for receipt_id in value["source_receipt_ids"]:
        receipt = receipts.get(receipt_id)
        if receipt is None:
            missing.append(f"SOURCE_RECEIPT_MISSING:{receipt_id}")
        elif not blob_path(root, receipt["payload_hash"]).is_file():
            missing.append(f"SOURCE_PAYLOAD_MISSING:{receipt_id}")
    for calculation_id in value["calculation_ids"]:
        path = root / "evidence/calculations" / f"{calculation_id}.json"
        if not path.is_file():
            missing.append(f"CALCULATION_MISSING:{calculation_id}")
        else:
            calculation = json.loads(path.read_text(encoding="utf-8"))
            from autoresearch.research.calculations import validate_calculation

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
            text = path.read_text(encoding="utf-8")
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
    if not (
        value["source_receipt_ids"]
        or value["quote_refs"]
        or value["calculation_ids"]
    ):
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
    value["sidecar_id"] = _id(value)
    missing = _validate(root, value)
    if missing:
        raise ValueError(f"material claim evidence is incomplete: {missing}")
    target = root / "evidence/material_claims" / f"{value['sidecar_id']}.json"
    if target.is_file() and json.loads(target.read_text(encoding="utf-8")) != value:
        raise RuntimeError("material claim sidecar changed")
    atomic_write_json(target, value)
    return value


def verify_material_claims(capsule: Path | str) -> dict:
    root = Path(capsule)
    folder = root / "evidence/material_claims"
    files = sorted(folder.glob("*.json")) if folder.is_dir() else []
    missing = []
    for path in files:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            missing.extend(_validate(root, value))
            if path.stem != value.get("sidecar_id"):
                missing.append(f"SIDECAR_FILENAME_MISMATCH:{path.name}")
        except Exception as exc:
            missing.append(f"SIDECAR_INVALID:{path.name}:{type(exc).__name__}")
    return {
        "claims": len(files),
        "ok": bool(files) and not missing,
        "missing": sorted(set(missing)),
    }


__all__ = ["bind_material_claim", "verify_material_claims"]
