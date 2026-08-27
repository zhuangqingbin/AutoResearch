"""Stable hashing and atomic JSON persistence for forensic evidence."""
from __future__ import annotations

import hashlib
import json

from autoresearch.trace.atomic import (
    atomic_write_json,
    canonical_json,
    sha256_bytes,
    sha256_file,
)


def test_canonical_json_is_compact_sorted_and_unicode_preserving():
    assert canonical_json({"z": "现场", "a": [2, 1]}) == '{"a":[2,1],"z":"现场"}'


def test_sha256_helpers_hash_exact_bytes(tmp_path):
    content = "现场\n".encode()
    path = tmp_path / "evidence.bin"
    path.write_bytes(content)
    expected = hashlib.sha256(content).hexdigest()

    assert sha256_bytes(content) == expected
    assert sha256_file(path) == expected


def test_atomic_json_write_replaces_complete_document_and_removes_temp(tmp_path):
    path = tmp_path / "state.json"
    path.write_text('{"stale":true}\n', encoding="utf-8")

    written = atomic_write_json(path, {"b": 2, "a": 1})

    assert written == path
    assert json.loads(path.read_text(encoding="utf-8")) == {"a": 1, "b": 2}
    assert path.read_text(encoding="utf-8").endswith("\n")
    assert path.read_text(encoding="utf-8").index('"a"') < path.read_text(
        encoding="utf-8"
    ).index('"b"')
    assert not (tmp_path / "state.json.tmp").exists()


def test_atomic_json_write_creates_parent_directory(tmp_path):
    path = tmp_path / "capsule" / "state.json"
    assert atomic_write_json(path, {}) == path
    assert path.read_text(encoding="utf-8") == "{}\n"
