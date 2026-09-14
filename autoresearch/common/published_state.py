"""Versioned state whose pending heads are invisible until publication commit."""

from __future__ import annotations

import contextlib
import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from autoresearch.common.atomic import atomic_write_bytes, atomic_write_json, sha256_bytes
from autoresearch.common.commit_chain import read_publication_chain
from autoresearch.contracts.publication import validate_publication_receipt


def _key_hash(target_key: str) -> str:
    if not isinstance(target_key, str) or not target_key:
        raise ValueError("target_key is required")
    return sha256_bytes(target_key.encode("utf-8"))


def _head_path(root: Path, target_key: str) -> Path:
    return root / "heads" / f"{_key_hash(target_key)}.json"


def _pointer_path(root: Path, target_key: str, run_id: str, publication_id: str) -> Path:
    return root / "pointers" / _key_hash(target_key) / f"{run_id}-{publication_id}.json"


def _version_path(root: Path, target_key: str, digest: str) -> Path:
    return root / "versions" / _key_hash(target_key) / f"{digest}.bin"


def _receipt_path(reports_root: Path, run_id: str, publication_id: str) -> Path:
    return reports_root / "_publications" / run_id / f"{publication_id}.json"


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _committed(pointer: dict[str, Any], reports_root: Path) -> bool:
    publication = pointer.get("publication")
    if not isinstance(publication, dict):
        return False
    receipt_scope = publication.get("receipt_scope")
    receipt_root = reports_root
    if isinstance(receipt_scope, str) and receipt_scope:
        if Path(receipt_scope).name != receipt_scope:
            return False
        if receipt_root.name != receipt_scope:
            receipt_root = receipt_root.parent / receipt_scope
    receipt = _read_json(
        _receipt_path(
            receipt_root,
            str(publication.get("run_id")),
            str(publication.get("publication_id")),
        )
    )
    if receipt is None:
        return False
    try:
        validate_publication_receipt(receipt)
    except (TypeError, ValueError):
        return False
    if receipt not in read_publication_chain(receipt_root / "_publications/receipts.jsonl"):
        return False
    return all(
        receipt.get(field) == publication.get(field)
        for field in ("engine", "run_id", "publication_id", "bundle_hash")
    )


def _read_pointer(root: Path, relative: str | None) -> dict[str, Any] | None:
    if not relative:
        return None
    path = root / relative
    try:
        path.resolve(strict=True).relative_to(root.resolve(strict=True))
    except (FileNotFoundError, OSError, ValueError):
        return None
    return _read_json(path)


def committed_pointer(
    target_key: str,
    *,
    state_root: Path | str,
    reports_root: Path | str,
) -> dict[str, Any] | None:
    root = Path(state_root)
    head = _read_json(_head_path(root, target_key))
    pointer = _read_pointer(root, str((head or {}).get("pointer") or ""))
    seen: set[str] = set()
    while pointer is not None:
        identity = str(pointer.get("pointer_id") or "")
        if not identity or identity in seen or pointer.get("target_key") != target_key:
            return None
        seen.add(identity)
        if _committed(pointer, Path(reports_root)):
            return pointer
        pointer = _read_pointer(root, pointer.get("predecessor"))
    return None


def read_committed_bytes(
    target_key: str,
    *,
    state_root: Path | str,
    reports_root: Path | str,
) -> bytes | None:
    root = Path(state_root)
    pointer = committed_pointer(
        target_key,
        state_root=root,
        reports_root=reports_root,
    )
    if pointer is None:
        return None
    path = root / str(pointer["version"])
    try:
        payload = path.read_bytes()
    except OSError:
        return None
    return payload if sha256_bytes(payload) == pointer.get("after_hash") else None


def read_committed_state(
    target_key: str,
    *,
    state_root: Path | str,
    reports_root: Path | str,
) -> dict[str, Any] | None:
    payload = read_committed_bytes(
        target_key,
        state_root=state_root,
        reports_root=reports_root,
    )
    if payload is None:
        return None
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("committed state is not JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("committed state must be a JSON object")
    return value


@contextlib.contextmanager
def target_locks(state_root: Path | str, target_keys: list[str]) -> Iterator[None]:
    root = Path(state_root)
    streams = []
    try:
        for target_key in sorted(set(target_keys)):
            path = root / "locks" / f"{_key_hash(target_key)}.lock"
            path.parent.mkdir(parents=True, exist_ok=True)
            stream = path.open("a+b")
            if os.name != "nt":
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            streams.append(stream)
        yield
    finally:
        for stream in reversed(streams):
            if os.name != "nt":
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            stream.close()


def _ordering(payload: bytes, run_id: str) -> tuple[str, str]:
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return "", run_id
    if not isinstance(value, dict):
        return "", run_id
    return str(value.get("as_of") or ""), str(value.get("session_run_id") or run_id)


def stage_state_mutation(
    mutation: dict[str, Any],
    payload: bytes,
    *,
    publication: dict[str, str],
    state_root: Path | str,
    reports_root: Path | str,
) -> dict[str, Any]:
    root = Path(state_root)
    target_key = mutation["target_key"]
    after_hash = sha256_bytes(payload)
    if after_hash != mutation["after_hash"]:
        raise RuntimeError(f"state mutation payload mismatch: {target_key}")
    current = committed_pointer(
        target_key,
        state_root=root,
        reports_root=reports_root,
    )
    before_hash = current.get("after_hash") if current else mutation.get("expected_before_hash")
    if after_hash == before_hash:
        return {
            "target_key": target_key,
            "status": "ALREADY_APPLIED",
            "before_hash": before_hash,
            "after_hash": after_hash,
        }
    pointer_path = _pointer_path(
        root,
        target_key,
        publication["run_id"],
        publication["publication_id"],
    )
    existing_pointer = _read_json(pointer_path)
    if existing_pointer is not None:
        version = _version_path(root, target_key, after_hash)
        if (
            existing_pointer.get("target_key") != target_key
            or existing_pointer.get("after_hash") != after_hash
            or existing_pointer.get("publication") != publication
            or existing_pointer.get("version") != version.relative_to(root).as_posix()
            or not version.is_file()
            or version.read_bytes() != payload
        ):
            raise RuntimeError(f"state pointer identity conflict: {target_key}")
        atomic_write_json(
            _head_path(root, target_key),
            {
                "schema_version": 1,
                "target_key": target_key,
                "pointer": pointer_path.relative_to(root).as_posix(),
            },
        )
        return {
            "target_key": target_key,
            "status": "APPLIED",
            "before_hash": before_hash,
            "after_hash": after_hash,
        }
    policy = mutation["apply_policy"]
    expected = mutation["expected_before_hash"]
    if policy in {"CAS_REPLACE", "IDEMPOTENT_APPEND"} and before_hash != expected:
        raise RuntimeError(f"state mutation conflict: {target_key}")
    if policy == "ADVANCE_IF_NEWER" and current is not None:
        current_payload = read_committed_bytes(
            target_key,
            state_root=root,
            reports_root=reports_root,
        )
        assert current_payload is not None
        if _ordering(payload, publication["run_id"]) <= _ordering(
            current_payload, str(current["publication"]["run_id"])
        ):
            return {
                "target_key": target_key,
                "status": "SUPERSEDED_BY_NEWER",
                "before_hash": before_hash,
                "after_hash": after_hash,
            }
    version = _version_path(root, target_key, after_hash)
    if version.is_file() and version.read_bytes() != payload:
        raise RuntimeError(f"state version hash collision: {target_key}")
    if not version.is_file():
        atomic_write_bytes(version, payload)
    predecessor = None
    head = _read_json(_head_path(root, target_key))
    if head is not None:
        predecessor = head.get("pointer")
    pointer = {
        "schema_version": 1,
        "pointer_id": sha256_bytes(
            (
                target_key + publication["run_id"] + publication["publication_id"] + after_hash
            ).encode("utf-8")
        ),
        "target_key": target_key,
        "after_hash": after_hash,
        "version": version.relative_to(root).as_posix(),
        "predecessor": predecessor,
        "publication": publication,
    }
    if pointer_path.is_file() and _read_json(pointer_path) != pointer:
        raise RuntimeError(f"state pointer identity conflict: {target_key}")
    if not pointer_path.is_file():
        atomic_write_json(pointer_path, pointer)
    atomic_write_json(
        _head_path(root, target_key),
        {
            "schema_version": 1,
            "target_key": target_key,
            "pointer": pointer_path.relative_to(root).as_posix(),
        },
    )
    return {
        "target_key": target_key,
        "status": "APPLIED",
        "before_hash": before_hash,
        "after_hash": after_hash,
    }


__all__ = [
    "committed_pointer",
    "read_committed_bytes",
    "read_committed_state",
    "stage_state_mutation",
    "target_locks",
]
