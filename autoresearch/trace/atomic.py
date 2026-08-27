"""Stable serialization, hashing, and atomic JSON persistence primitives."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def canonical_json(value: object) -> str:
    """Serialize a JSON-compatible value into its stable compact form."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_bytes(value: bytes) -> str:
    """Return the SHA-256 hexadecimal digest of exact bytes."""
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path | str) -> str:
    """Hash a file without loading the complete evidence artifact into memory."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_write_json(path: Path | str, value: object) -> Path:
    """Atomically replace *path* with stable, human-readable UTF-8 JSON."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f"{target.name}.tmp")
    temp.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    temp.replace(target)
    return target
