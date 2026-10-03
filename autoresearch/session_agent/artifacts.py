"""Run-scoped artifact identities with path and content checks."""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import re
import stat as stat_module
import uuid
from collections.abc import Iterator
from contextvars import ContextVar
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
    workspace = Path(handle.workspace)
    root = workspace.resolve(strict=True)
    candidate = Path(path)
    if not candidate.is_absolute():
        # Handles may use a cwd-relative workspace; their joined paths already
        # carry that prefix. Bare artifact paths remain workspace-relative.
        if not workspace.is_absolute() and candidate.is_relative_to(workspace):
            candidate = candidate.relative_to(workspace)
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
    if layout_version(handle) >= 2:
        registry = _read_registry(_registry_path(handle), handle)
        current = registry['artifacts'].get(artifact_id)
        if current is not None:
            _, relative = _safe_relative(handle, path, may_not_exist=True)
            if current['relative_path'] != relative or (current['access'] == 'READ' and access != 'READ'):
                raise ArtifactConflict('artifact declaration changed')
            return current
    resolved, relative = _safe_relative(handle, path, may_not_exist=access == "WRITE")
    if access == "READ" and not resolved.is_file():
        raise ValueError("read artifact does not exist")
    if resolved.exists() and not resolved.is_file():
        raise ValueError("artifact must be a regular file")
    digest = None
    device = None
    inode = None
    if resolved.is_file() and not (layout_version(handle) >= 2 and access == "WRITE"):
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
    try:
        descriptor = _effective_descriptor(handle, artifact_id)
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
    if layout_version(handle) >= 2:
        with open_artifact(handle, artifact_id):
            pass
        return _effective_descriptor(handle, artifact_id)
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
    if layout_version(handle) >= 2:
        try:
            return snapshot_artifact(handle, artifact_id)['sha256']
        except ArtifactConflict:
            if artifact_id not in _committed(handle):
                return None
            raise
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
    descriptor = _effective_descriptor(handle, artifact_id)
    return _safe_relative(handle, descriptor['relative_path'], may_not_exist=layout_version(handle) < 2)[0]


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


# Layout version is owned by tasks.json after begin. An unversioned historical owner
# always stays v1, even if a stray storage declaration appears beside it.
def layout_version(handle) -> int:
    session = Path(handle.workspace) / 'session'
    owner = session / 'tasks.json'
    if owner.is_file():
        payload = json.loads(owner.read_text())
        if payload.get('run_id') != handle.run_id or payload.get('engine') != handle.engine:
            raise ArtifactConflict('artifact owner run identity changed')
        value = payload.get('output_layout_version', 1)
        # A historical owner never acquires a version from a sidecar added later.
    else:
        declaration = session / 'storage.json'
        payload = json.loads(declaration.read_text()) if declaration.is_file() else {}
        value = payload.get('output_layout_version', 1)
        if payload.get('run_id', handle.run_id) != handle.run_id:
            raise ArtifactConflict('output storage run identity changed')
    if type(value) is not int or value not in {1, 2}:
        raise ArtifactConflict('unsupported output layout version')
    if getattr(handle, 'capsule', None):
        frozen = Path(handle.capsule) / 'identity/session/storage.json'
        if frozen.is_file():
            identity = json.loads(frozen.read_text())
            if (identity.get('run_id') != handle.run_id
                    or identity.get('output_layout_version') != value
                    or (owner.is_file() and identity.get('plan_hash') != payload.get('plan_hash'))):
                raise ArtifactConflict('frozen output storage identity changed')
    return value


def declared_path(handle, artifact_id: str) -> Path:
    descriptor = _read_registry(_registry_path(handle), handle)['artifacts'][artifact_id]
    return _safe_relative(handle, descriptor['relative_path'], may_not_exist=True)[0]


def _committed(handle) -> dict:
    path = Path(handle.workspace) / 'session/tasks.json'
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text())
    if payload['run_id'] != handle.run_id or payload['engine'] != handle.engine:
        raise ArtifactConflict('artifact owner run identity changed')
    result = {}
    for entry in sorted(payload['tasks'].values(), key=lambda row: row.get('accepted_revision', 0)):
        if entry['state'] in {'SUCCEEDED', 'SUPERSEDED'}:
            result.update(entry.get('accepted_artifacts', {}))
    return result


_candidate_descriptors = ContextVar('session_candidate_descriptors', default=None)


@contextlib.contextmanager
def candidate_view(handle, descriptors):
    token = _candidate_descriptors.set((str(Path(handle.workspace).resolve()), descriptors))
    try:
        yield
    finally:
        _candidate_descriptors.reset(token)


def _effective_descriptor(handle, artifact_id):
    candidate = _candidate_descriptors.get()
    if (candidate is not None and candidate[0] == str(Path(handle.workspace).resolve())
            and artifact_id in candidate[1]):
        return candidate[1][artifact_id]
    registry = _read_registry(_registry_path(handle), handle)
    descriptor = registry['artifacts'][artifact_id]
    if layout_version(handle) >= 2:
        committed = _committed(handle)
        if artifact_id in committed:
            return committed[artifact_id]
        if descriptor['access'] == 'WRITE':
            raise ArtifactConflict(f'artifact has no accepted generation: {artifact_id}')
    return descriptor


def output_paths(handle, task: dict, attempt: int) -> dict[str, str]:
    if layout_version(handle) < 2 or task['kind'] != 'INFERENCE':
        return {key: str(declared_path(handle, key)) for key in task['output_artifact_ids']}
    task_id = task['task_id']
    if not _ARTIFACT_RE.fullmatch(task_id) or type(attempt) is not int or attempt < 1:
        raise ValueError('invalid output attempt identity')
    root = Path(handle.staging) / 'session_outputs/attempts' / task_id / f'a{attempt:04d}' / 'outputs'
    _safe_relative(handle, root, may_not_exist=True)
    root.mkdir(parents=True, exist_ok=True)
    frozen = Path(handle.workspace) / 'session/dispatch' / f'{task_id}-a{attempt}.json'
    if frozen.is_file():
        paths = json.loads(frozen.read_text())['output_paths']
        if set(paths) != set(task['output_artifact_ids']):
            raise ArtifactConflict('frozen dispatch output set changed')
        for path in paths.values():
            safe, _ = _safe_relative(handle, path, may_not_exist=True)
            if safe.parent != root.resolve():
                raise ArtifactConflict('frozen output escapes private attempt directory')
        return paths
    return {key: str(root / (key + declared_path(handle, key).suffix)) for key in task['output_artifact_ids']}


def _source_identity(value):
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def capture_outputs(handle, task: dict, attempt: int, *, expected=None, purpose='accepted') -> dict:
    """Copy a whole candidate set; no visibility is granted until the owner commits."""
    if purpose not in {'accepted', 'precheck'}:
        raise ValueError('unsupported candidate capture purpose')
    if purpose == 'precheck' and layout_version(handle) < 2:
        raise ValueError('precheck requires output layout version 2')
    sources = output_paths(handle, task, attempt)
    private = task['kind'] == 'INFERENCE'
    if private:
        directory = Path(next(iter(sources.values()))).parent
        actual = set(directory.iterdir())
        if actual != {Path(value) for value in sources.values()}:
            raise ValueError('missing or undeclared attempt output')
    generation = uuid.uuid4().hex
    destination = Path(handle.staging) / 'session_outputs' / purpose / task['task_id'] / f'a{attempt:04d}' / generation
    _safe_relative(handle, destination, may_not_exist=True)
    destination.mkdir(parents=True)
    result = {}
    signatures = {}
    expected_hashes = None if expected is None else {row['artifact_id']: row['sha256'] for row in expected}
    if expected_hashes is not None and set(expected_hashes) != set(sources):
        raise ValueError('candidate output set mismatch')
    for key, source in sources.items():
        path, _ = _safe_relative(handle, source, may_not_exist=False)
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
        with os.fdopen(fd, 'rb') as stream:
            before = os.fstat(stream.fileno())
            if not stat_module.S_ISREG(before.st_mode) or before.st_nlink != 1:
                raise ValueError('output must be a regular file with no links')
            data = stream.read()
            if _source_identity(before) != _source_identity(os.fstat(stream.fileno())):
                raise ValueError('output changed during capture')
        signatures[path] = _source_identity(before)
        digest = hashlib.sha256(data).hexdigest()
        if expected_hashes is not None and digest != expected_hashes[key]:
            raise ValueError(f'candidate hash mismatch: {key}')
        target = destination / (key + Path(source).suffix)
        atomic_write_bytes(target, data)
        target.chmod(0o444)
        info = target.stat()
        declared = _read_registry(_registry_path(handle), handle)['artifacts'][key]
        result[key] = {**declared, 'relative_path': target.relative_to(Path(handle.workspace)).as_posix(),
                       'sha256': digest, 'device': info.st_dev, 'inode': info.st_ino}
    for path, signature in signatures.items():
        _safe_relative(handle, path, may_not_exist=False)
        if signature != _source_identity(path.stat()):
            raise ValueError('output changed during capture')
    if private and set(directory.iterdir()) != {Path(value) for value in sources.values()}:
        raise ValueError('attempt output set changed during capture')
    with candidate_view(handle, result):
        for key in result:
            with open_artifact(handle, key):
                pass
    return result


def materialize_outputs(handle, artifact_ids=None) -> None:
    """Rebuild disposable legacy working copies exclusively from committed bytes."""
    if layout_version(handle) < 2:
        return
    committed = _committed(handle)
    for key in committed if artifact_ids is None else artifact_ids:
        if key not in committed:
            continue
        with open_artifact(handle, key) as stream:
            data = stream.read()
        target = declared_path(handle, key)
        if not target.is_file() or target.read_bytes() != data:
            atomic_write_bytes(target, data)
        review = re.fullmatch(r'scan\.l4\.(\d{6})\.a(\d+)\.review([23])', key)
        if review and int(review.group(2)) > 1:
            canonical = Path(handle.staging) / 'ensemble' / f'{review.group(1)}.run{review.group(3)}.md'
            _safe_relative(handle, canonical, may_not_exist=True)
            if not canonical.is_file() or canonical.read_bytes() != data:
                atomic_write_bytes(canonical, data)


def read_bytes(handle, artifact_id):
    with open_artifact(handle, artifact_id) as stream:
        return stream.read()


def open_artifact_version(handle, artifact_id: str, expected_hash: str):
    """Resolve a frozen claim/receipt to its committed generation, including aliases."""
    if layout_version(handle) < 2:
        return open_artifact(handle, artifact_id)
    owner = json.loads((Path(handle.workspace) / 'session/tasks.json').read_text())
    for entry in owner['tasks'].values():
        manifests = [entry.get('accepted_artifacts', {}), *entry.get('accepted_history', [])]
        for manifest in manifests:
            descriptor = manifest.get(artifact_id)
            if descriptor and descriptor['sha256'] == expected_hash:
                with candidate_view(handle, {artifact_id: descriptor}):
                    return open_artifact(handle, artifact_id)
    return open_artifact(handle, artifact_id)
