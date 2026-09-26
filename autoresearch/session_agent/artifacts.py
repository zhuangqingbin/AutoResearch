"""Run-scoped artifact identities with path and content checks."""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import re
from collections.abc import Iterator
from pathlib import Path

from autoresearch.common.atomic import atomic_write_bytes, atomic_write_json

_ARTIFACT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", re.ASCII)
_ACCESS = frozenset({"READ", "WRITE"})


class ArtifactConflict(RuntimeError):
    """The registered path identity or content no longer matches."""


def _registry_path(handle) -> Path:
    return Path(handle.workspace) / "session" / "artifacts.json"


@contextlib.contextmanager
def _locked(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(f"{path.suffix}.lock").open("a+", encoding="utf-8") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _hash_stream(stream) -> str:
    digest = hashlib.sha256()
    for block in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(block)
    stream.seek(0)
    return digest.hexdigest()


def _safe_relative(handle, path: Path | str, *, may_not_exist: bool) -> tuple[Path, str]:
    root = Path(handle.workspace).resolve(strict=True)
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = root / candidate
    current = candidate
    while current != root:
        if (current.exists() or current.is_symlink()) and current.is_symlink():
            raise ValueError("artifact path contains symlink")
        current = current.parent
        if root not in (current, *current.parents):
            break
    resolved = candidate.resolve(strict=not may_not_exist)
    try:
        relative = resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("artifact is outside run workspace") from exc
    if not relative.parts:
        raise ValueError("artifact must be a file below run workspace")
    return resolved, relative.as_posix()


def _read_registry(path: Path, handle) -> dict:
    if not path.is_file():
        return {
            "schema_version": 1,
            "engine": handle.engine,
            "run_id": handle.run_id,
            "artifacts": {},
        }
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("engine") != handle.engine or value.get("run_id") != handle.run_id:
        raise ArtifactConflict("artifact registry run identity changed")
    return value


def register_artifact(handle, artifact_id: str, path: Path | str, access: str) -> dict:
    if type(artifact_id) is not str or not _ARTIFACT_RE.fullmatch(artifact_id):
        raise ValueError("invalid artifact_id")
    if access not in _ACCESS:
        raise ValueError("invalid artifact access")
    resolved, relative = _safe_relative(handle, path, may_not_exist=access == "WRITE")
    if access == "READ" and not resolved.is_file():
        raise ValueError("read artifact does not exist")
    if resolved.exists() and not resolved.is_file():
        raise ValueError("artifact must be a regular file")
    digest = None
    device = None
    inode = None
    if resolved.is_file():
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(resolved, flags)
        try:
            stat = os.fstat(fd)
            with os.fdopen(os.dup(fd), "rb") as stream:
                digest = _hash_stream(stream)
            device, inode = stat.st_dev, stat.st_ino
        finally:
            os.close(fd)
    descriptor = {
        "artifact_id": artifact_id,
        "engine": handle.engine,
        "run_id": handle.run_id,
        "relative_path": relative,
        "sha256": digest,
        "access": access,
        "source_ref": "run",
        "device": device,
        "inode": inode,
    }
    registry_path = _registry_path(handle)
    with _locked(registry_path):
        registry = _read_registry(registry_path, handle)
        current = registry["artifacts"].get(artifact_id)
        if current is not None and current != descriptor:
            comparable_current = {key: value for key, value in current.items() if key != "access"}
            comparable_new = {key: value for key, value in descriptor.items() if key != "access"}
            if not (
                current.get("access") == "WRITE"
                and access == "READ"
                and comparable_current == comparable_new
            ):
                raise ArtifactConflict("artifact identity already registered differently")
            return current
        registry["artifacts"][artifact_id] = descriptor
        atomic_write_json(registry_path, registry)
    return descriptor


def _descriptor(handle, artifact_id: str) -> tuple[Path, dict]:
    registry = _read_registry(_registry_path(handle), handle)
    try:
        descriptor = registry["artifacts"][artifact_id]
    except KeyError as exc:
        raise KeyError(f"unregistered artifact: {artifact_id}") from exc
    path, relative = _safe_relative(
        handle,
        Path(handle.workspace) / descriptor["relative_path"],
        may_not_exist=False,
    )
    if relative != descriptor["relative_path"]:
        raise ArtifactConflict("artifact relative path changed")
    return path, descriptor


def open_artifact(handle, artifact_id: str):
    path, descriptor = _descriptor(handle, artifact_id)
    if descriptor["sha256"] is None:
        raise ArtifactConflict("artifact has not been bound to content")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        current = os.fstat(fd)
        if (current.st_dev, current.st_ino) != (descriptor["device"], descriptor["inode"]):
            raise ArtifactConflict("artifact file identity changed")
        stream = os.fdopen(fd, "rb")
        fd = -1
        if _hash_stream(stream) != descriptor["sha256"]:
            stream.close()
            raise ArtifactConflict("artifact content changed")
        return stream
    finally:
        if fd >= 0:
            os.close(fd)


def bind_artifact_hash(handle, artifact_id: str) -> dict:
    registry_path = _registry_path(handle)
    with _locked(registry_path):
        registry = _read_registry(registry_path, handle)
        if artifact_id not in registry["artifacts"]:
            raise KeyError(artifact_id)
        descriptor = registry["artifacts"][artifact_id]
        path, relative = _safe_relative(
            handle,
            Path(handle.workspace) / descriptor["relative_path"],
            may_not_exist=False,
        )
        if relative != descriptor["relative_path"] or not path.is_file():
            raise ArtifactConflict("artifact output is missing")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags)
        try:
            stat = os.fstat(fd)
            with os.fdopen(os.dup(fd), "rb") as stream:
                digest = _hash_stream(stream)
        finally:
            os.close(fd)
        if descriptor["sha256"] is not None and (
            descriptor["sha256"] != digest
            or (descriptor["device"], descriptor["inode"]) != (stat.st_dev, stat.st_ino)
        ):
            raise ArtifactConflict("artifact output changed after binding")
        descriptor.update({"sha256": digest, "device": stat.st_dev, "inode": stat.st_ino})
        atomic_write_json(registry_path, registry)
        return descriptor


def replace_failed_output(
    handle,
    artifact_id: str,
    payload: bytes,
    *,
    expected_sha256: str | None,
) -> dict:
    """Replace a failed WRITE output while rejecting stale or changed bindings."""
    if not isinstance(payload, bytes):
        raise TypeError("replacement payload must be bytes")
    registry_path = _registry_path(handle)
    with _locked(registry_path):
        registry = _read_registry(registry_path, handle)
        descriptor = registry["artifacts"].get(artifact_id)
        if descriptor is None:
            raise KeyError(artifact_id)
        if descriptor["access"] != "WRITE":
            raise ArtifactConflict("only failed WRITE outputs can be replaced")
        if descriptor["sha256"] != expected_sha256:
            raise ArtifactConflict("failed output does not match expected binding")
        target, relative = _safe_relative(
            handle,
            Path(handle.workspace) / descriptor["relative_path"],
            may_not_exist=descriptor["sha256"] is None,
        )
        if relative != descriptor["relative_path"]:
            raise ArtifactConflict("artifact relative path changed")
        if descriptor["sha256"] is not None:
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(target, flags)
            try:
                stat = os.fstat(fd)
                with os.fdopen(os.dup(fd), "rb") as stream:
                    digest = _hash_stream(stream)
            finally:
                os.close(fd)
            if (
                digest != descriptor["sha256"]
                or (stat.st_dev, stat.st_ino)
                != (descriptor["device"], descriptor["inode"])
            ):
                raise ArtifactConflict("failed output changed before replacement")
        atomic_write_bytes(target, payload)
        stat = target.stat()
        descriptor.update(
            {
                "sha256": hashlib.sha256(payload).hexdigest(),
                "device": stat.st_dev,
                "inode": stat.st_ino,
            }
        )
        atomic_write_json(registry_path, registry)
        return descriptor


def binding_sha256(handle, artifact_id: str) -> str | None:
    """Return the current binding after verifying it when content is already bound.

    A WRITE output that was never bound (e.g. a timed-out attempt wrote nothing) has
    no file to verify: ``None`` without touching the filesystem.
    """
    registry = _read_registry(_registry_path(handle), handle)
    if artifact_id not in registry["artifacts"]:
        raise KeyError(f"unregistered artifact: {artifact_id}")
    if registry["artifacts"][artifact_id]["sha256"] is None:
        return None
    _, descriptor = _descriptor(handle, artifact_id)
    if descriptor["sha256"] is None:
        return None
    with open_artifact(handle, artifact_id):
        pass
    return str(descriptor["sha256"])


def artifact_path(handle, artifact_id: str) -> Path:
    """Registered location of an artifact; the file may not exist yet (WRITE outputs)."""
    registry = _read_registry(_registry_path(handle), handle)
    try:
        descriptor = registry["artifacts"][artifact_id]
    except KeyError as exc:
        raise KeyError(f"unregistered artifact: {artifact_id}") from exc
    return Path(handle.workspace) / descriptor["relative_path"]


def snapshot_artifact(handle, artifact_id: str) -> dict:
    """Verify a bound artifact and return the immutable task handoff identity."""
    with open_artifact(handle, artifact_id):
        pass
    _, descriptor = _descriptor(handle, artifact_id)
    return {
        "artifact_id": artifact_id,
        "sha256": descriptor["sha256"],
        "relative_path": descriptor["relative_path"],
    }


__all__ = [
    "ArtifactConflict", "artifact_path", "bind_artifact_hash", "binding_sha256", "open_artifact",
    "register_artifact", "replace_failed_output", "snapshot_artifact",
]
