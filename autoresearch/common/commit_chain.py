"""Durable local hash chain for publication commit receipts."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from autoresearch.common.atomic import canonical_json
from autoresearch.contracts.publication import (
    publication_receipt_hash,
    validate_publication_receipt,
)


def read_publication_chain(path: Path | str) -> list[dict[str, Any]]:
    target = Path(path)
    if not target.is_file():
        return []
    rows: list[dict[str, Any]] = []
    previous: str | None = None
    for line in target.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            validate_publication_receipt(row)
        except (json.JSONDecodeError, TypeError, ValueError):
            break
        if row["previous_receipt_hash"] != previous:
            break
        rows.append(row)
        previous = row["receipt_hash"]
    return rows


def append_publication_receipt(path: Path | str, value: dict[str, Any]) -> dict[str, Any]:
    """Append once under a file lock and supply the authoritative chain link."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a+b") as stream:
        if os.name != "nt":
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            stream.seek(0)
            physical_rows = [
                line for line in stream.read().decode("utf-8").splitlines() if line.strip()
            ]
            rows = read_publication_chain(target)
            if len(rows) != len(physical_rows):
                raise RuntimeError("publication commit chain is corrupt")
            for row in rows:
                if (
                    row["engine"],
                    row["run_id"],
                    row["publication_id"],
                ) == (value["engine"], value["run_id"], value["publication_id"]):
                    candidate = dict(value)
                    candidate["previous_receipt_hash"] = row["previous_receipt_hash"]
                    candidate["receipt_hash"] = publication_receipt_hash(candidate)
                    if candidate != row:
                        raise RuntimeError("publication receipt identity conflict")
                    return row
            result = dict(value)
            result["previous_receipt_hash"] = rows[-1]["receipt_hash"] if rows else None
            result["receipt_hash"] = publication_receipt_hash(result)
            validate_publication_receipt(result)
            stream.seek(0, os.SEEK_END)
            stream.write((canonical_json(result) + "\n").encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
            return result
        finally:
            if os.name != "nt":
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


__all__ = ["append_publication_receipt", "read_publication_chain"]
