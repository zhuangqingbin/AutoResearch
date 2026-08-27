"""Stable serialization, hashing, and atomic JSON persistence primitives."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
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


def _write_all(fd: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(fd, view)
        if written == 0:
            raise OSError("short write while persisting atomic JSON")
        view = view[written:]


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    fd = os.open(path, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write_json(path: Path | str, value: object) -> Path:
    """Durably replace *path* using an exclusive same-directory temp file."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = (canonical_json(value) + "\n").encode("utf-8")
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    temp = Path(temp_name)
    try:
        try:
            _write_all(fd, payload)
            os.fsync(fd)
        finally:
            open_fd = fd
            fd = -1
            os.close(open_fd)
        os.replace(temp, target)
        _fsync_directory(target.parent)
    finally:
        try:
            if fd >= 0:
                os.close(fd)
        finally:
            temp.unlink(missing_ok=True)
    return target
