"""Stable hashing and atomic JSON persistence for forensic evidence."""
from __future__ import annotations

import hashlib
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from autoresearch.trace import atomic
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
    assert path.read_bytes() == b'{"a":1,"b":2}\n'
    assert [
        entry for entry in tmp_path.iterdir() if entry.is_file() and entry != path
    ] == []


def test_atomic_json_write_creates_parent_directory(tmp_path):
    path = tmp_path / "capsule" / "state.json"
    assert atomic_write_json(path, {}) == path
    assert path.read_text(encoding="utf-8") == "{}\n"


def test_concurrent_atomic_writers_use_distinct_temps_during_overlap(
    tmp_path, monkeypatch
):
    path = tmp_path / "state.json"
    barrier = threading.Barrier(2)
    sources = []
    source_lock = threading.Lock()
    real_replace = os.replace

    def overlapping_replace(source, target):
        with source_lock:
            sources.append(os.fspath(source))
        barrier.wait(timeout=5)
        real_replace(source, target)

    monkeypatch.setattr(os, "replace", overlapping_replace)
    values = ({"writer": 1}, {"writer": 2})
    with ThreadPoolExecutor(max_workers=2) as pool:
        written = list(pool.map(lambda value: atomic_write_json(path, value), values))

    assert written == [path, path]
    assert len(set(sources)) == 2
    assert json.loads(path.read_text(encoding="utf-8")) in values
    assert [
        entry for entry in tmp_path.iterdir() if entry.is_file() and entry != path
    ] == []


def test_atomic_write_cleans_temp_after_injected_write_failure(tmp_path, monkeypatch):
    path = tmp_path / "state.json"

    def partial_write_then_fail(fd, payload):
        os.write(fd, payload[:2])
        raise OSError("disk full")

    monkeypatch.setattr(atomic, "_write_all", partial_write_then_fail, raising=False)
    with pytest.raises(OSError, match="disk full"):
        atomic_write_json(path, {"a": 1})

    assert not path.exists()
    assert [entry for entry in tmp_path.iterdir() if entry.is_file()] == []


def test_atomic_write_cleans_temp_after_replace_failure(tmp_path, monkeypatch):
    path = tmp_path / "state.json"

    def fail_replace(source, target):
        raise OSError("replace failed")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        atomic_write_json(path, {"a": 1})

    assert not path.exists()
    assert [entry for entry in tmp_path.iterdir() if entry.is_file()] == []
