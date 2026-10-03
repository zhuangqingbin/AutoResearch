"""Hash-bound references for derived, same-engine offline research products."""
from __future__ import annotations

import json
import re
from pathlib import Path

from autoresearch.common import workspace as ws
from autoresearch.common.atomic import sha256_bytes


def read_ref(ref: dict) -> bytes:
    if (not isinstance(ref, dict) or set(ref) != {"path", "sha256"}
            or not isinstance(ref["path"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", str(ref["sha256"]))):
        raise ValueError("invalid evidence reference")
    path = Path(ref["path"]).resolve()
    roots = (ws.context_root().resolve(), ws.reports_root().resolve())
    if not any(path.is_relative_to(root) for root in roots):
        raise ValueError("evidence reference outside engine scope")
    data = path.read_bytes()
    if sha256_bytes(data) != ref["sha256"]:
        raise ValueError("evidence reference hash mismatch")
    return data


def read_json_ref(ref: dict) -> dict:
    value = json.loads(read_ref(ref))
    if not isinstance(value, dict):
        raise ValueError("evidence must contain an object")
    return value


def read_request(path: str) -> dict:
    """CLI manifests may live in the repository, but never another engine root."""
    from autoresearch.scan.research_provenance import safe_path

    value = json.loads(safe_path(path).read_text())
    if not isinstance(value, dict):
        raise ValueError("request must contain an object")
    return value


def write_derived(path: Path, value: dict) -> dict:
    """Exclusive output, never replace an already frozen observation."""
    from autoresearch.common.atomic import canonical_json

    path = Path(path)
    if not path.resolve().is_relative_to(ws.context_root().resolve()):
        raise ValueError("derived output outside engine scope")
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (canonical_json(value) + "\n").encode()
    with path.open("xb") as stream:
        stream.write(data)
    return {"path": str(path), "sha256": sha256_bytes(data)}
