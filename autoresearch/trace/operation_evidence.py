"""Immutable evidence and scratch-only replay for standalone deterministic tools."""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from autoresearch.common import workspace as ws
from autoresearch.common.atomic import (
    atomic_write_bytes,
    atomic_write_json,
    canonical_json,
    sha256_bytes,
)
from autoresearch.contracts.forensic import validate_operation_evidence

SERVICE_OPERATIONS = frozenset(
    {
        "prewarm",
        "dossier.reconcile",
        "broker.ingest",
        "broker.reconcile",
        "research.evaluate",
    }
)
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}")


def _payload(value) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, bytearray):
        return bytes(value)
    if isinstance(value, Path):
        return value.read_bytes()
    if isinstance(value, str):
        return value.encode("utf-8")
    return (canonical_json(value) + "\n").encode("utf-8")


def _safe_name(artifact_id: str) -> str:
    if not _SAFE_ID.fullmatch(str(artifact_id)):
        raise ValueError(f"invalid service artifact id:{artifact_id!r}")
    return re.sub(r"[^A-Za-z0-9_.-]", "_", artifact_id)


def _refs(kind: str, values: dict[str, object]) -> tuple[list[dict], dict[str, bytes]]:
    refs = []
    payloads = {}
    for index, (artifact_id, value) in enumerate(sorted(values.items())):
        data = _payload(value)
        relative = f"{kind}/{index:03d}-{_safe_name(artifact_id)}"
        refs.append(
            {
                "artifact_id": artifact_id,
                "sha256": sha256_bytes(data),
                "captured_path": relative,
            }
        )
        payloads[relative] = data
    return refs, payloads


def _code_hash(paths: list[Path | str]) -> str:
    rows = []
    for raw in sorted({str(Path(path).resolve()) for path in paths}):
        path = Path(raw)
        rows.append(
            {
                "name": path.name,
                "sha256": sha256_bytes(path.read_bytes()),
            }
        )
    return sha256_bytes(canonical_json(rows).encode("utf-8"))


def operation_evidence_id(value: dict) -> str:
    return sha256_bytes(
        canonical_json({key: item for key, item in value.items() if key != "operation_id"}).encode(
            "utf-8"
        )
    )


def record_operation_evidence(
    operation: str,
    *,
    parameters: dict,
    inputs: dict[str, object],
    outputs: dict[str, object],
    effects: list[dict],
    code_paths: list[Path | str],
    evidence_root: Path | str | None = None,
    engine: str | None = None,
    status: str = "SUCCEEDED",
    error: dict | None = None,
) -> dict:
    """Write one content-addressed sidecar without creating a research run."""
    if operation not in SERVICE_OPERATIONS:
        raise ValueError(f"unsupported standalone operation:{operation}")
    input_refs, input_payloads = _refs("inputs", inputs)
    output_refs, output_payloads = _refs("outputs", outputs)
    value = {
        "schema_version": 1,
        "operation_id": "0" * 64,
        "engine": engine or ws.ENGINE,
        "operation": operation,
        "input_refs": input_refs,
        "code_hash": _code_hash(code_paths),
        "parameters": parameters,
        "output_refs": output_refs,
        "effects": effects,
        "status": status,
        "error": error,
    }
    value["operation_id"] = operation_evidence_id(value)
    validate_operation_evidence(value)
    base = Path(evidence_root or (ws.context_root() / "operation_evidence"))
    target = base / value["operation_id"]
    if target.exists():
        stored = json.loads((target / "evidence.json").read_text(encoding="utf-8"))
        if stored != value:
            raise RuntimeError("operation evidence identity collision")
        return {**value, "evidence_root": str(target)}
    scratch = base / f".{value['operation_id']}.tmp"
    if scratch.exists():
        shutil.rmtree(scratch)
    for relative, data in {**input_payloads, **output_payloads}.items():
        atomic_write_bytes(scratch / relative, data)
    atomic_write_json(scratch / "evidence.json", value)
    target.parent.mkdir(parents=True, exist_ok=True)
    scratch.replace(target)
    return {**value, "evidence_root": str(target)}


def load_operation_evidence(path: Path | str) -> tuple[Path, dict]:
    root = Path(path)
    if root.is_file():
        root = root.parent
    value = json.loads((root / "evidence.json").read_text(encoding="utf-8"))
    validate_operation_evidence(value)
    if value["operation_id"] != operation_evidence_id(value):
        raise ValueError("operation evidence id mismatch")
    for ref in [*value["input_refs"], *value["output_refs"]]:
        candidate = (root / ref["captured_path"]).resolve(strict=True)
        try:
            candidate.relative_to(root.resolve(strict=True))
        except ValueError as exc:
            raise ValueError("operation evidence artifact escaped root") from exc
        if sha256_bytes(candidate.read_bytes()) != ref["sha256"]:
            raise ValueError(f"operation evidence artifact hash mismatch:{ref['artifact_id']}")
    return root, value


def operation_output_reference(path: Path | str, artifact_id: str) -> dict:
    """Return the immutable provenance link a later research run should freeze."""
    root, value = load_operation_evidence(path)
    matches = [ref for ref in value["output_refs"] if ref["artifact_id"] == artifact_id]
    if len(matches) != 1:
        raise KeyError(f"operation output not found:{artifact_id}")
    return {
        "operation_id": value["operation_id"],
        "artifact_id": artifact_id,
        "sha256": matches[0]["sha256"],
        "evidence_root": str(root.resolve()),
    }


__all__ = [
    "SERVICE_OPERATIONS",
    "load_operation_evidence",
    "operation_evidence_id",
    "operation_output_reference",
    "record_operation_evidence",
]
