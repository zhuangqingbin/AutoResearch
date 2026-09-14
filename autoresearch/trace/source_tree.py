"""Self-contained executable source trees and offline runtime identity.

The source archive contains the bytes that actually existed in the allowed
behavioral roots at capture time.  Restoration is deliberately independent of
Git and never fills gaps from the current checkout.
"""

from __future__ import annotations

import io
import json
import locale
import os
import platform
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path, PurePosixPath
from typing import Any

import zstandard

from autoresearch.trace.atomic import canonical_json, sha256_bytes
from autoresearch.trace.identity import SecretMaterialDetected, scan_for_secrets

SOURCE_TREE_BUNDLE = "source_tree.tar.zst"
SOURCE_TREE_MANIFEST = "source_tree_manifest.json"
RUNTIME_MANIFEST = "runtime_manifest.json"
PORTABLE_RUNTIME = "portable_runtime.tar.zst"

SOURCE_DIRECTORIES = (
    PurePosixPath("autoresearch"),
    PurePosixPath(".claude/agents"),
    PurePosixPath(".claude/skills"),
    PurePosixPath(".claude/workflows"),
)
ROOT_SOURCES = (
    PurePosixPath("pyproject.toml"),
    PurePosixPath("uv.lock"),
    PurePosixPath("AGENTS.md"),
    PurePosixPath("CLAUDE.md"),
)

DEFAULT_MAX_MEMBERS = 50_000
DEFAULT_MAX_MEMBER_BYTES = 128 * 1024 * 1024
DEFAULT_MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
DEFAULT_MAX_TAR_BYTES = 4 * 1024 * 1024 * 1024

_IGNORED_DIRECTORIES = frozenset({"__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache"})
_IGNORED_SUFFIXES = (".pyc", ".pyo")


class SourceTreeError(ValueError):
    """The source tree cannot be captured or restored without ambiguity."""


def _reject_path_ancestors(path: Path) -> None:
    absolute = path.absolute()
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode):
            raise SourceTreeError(f"path contains a symlink: {current}")
        if current != absolute and not stat.S_ISDIR(info.st_mode):
            raise SourceTreeError(f"path parent is not a directory: {current}")


def _atomic_write(path: Path, payload: bytes, *, mode: int = 0o600) -> None:
    _reject_path_ancestors(path.parent)
    path.parent.mkdir(parents=True, exist_ok=True)
    _reject_path_ancestors(path.parent)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _is_allowed_member(name: str) -> bool:
    return name in {path.as_posix() for path in ROOT_SOURCES} or any(
        name.startswith(f"{root.as_posix()}/") for root in SOURCE_DIRECTORIES
    )


def _validated_member_path(name: str) -> PurePosixPath:
    logical = PurePosixPath(name)
    if (
        not name
        or logical.is_absolute()
        or logical.as_posix() != name
        or any(part in {"", ".", ".."} for part in logical.parts)
        or not _is_allowed_member(name)
    ):
        raise SourceTreeError(f"unsafe source tree member: {name!r}")
    return logical


def _git_paths(repo: Path, *args: str) -> set[str]:
    try:
        raw = subprocess.run(
            ["git", *args],
            cwd=repo,
            check=True,
            capture_output=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise SourceTreeError("source tree capture requires a readable Git worktree") from exc
    result: set[str] = set()
    for item in raw.split(b"\0"):
        if item:
            result.add(os.fsdecode(item))
    return result


def _read_regular(path: Path) -> tuple[bytes, int]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        before = path.lstat()
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise SourceTreeError(f"unreadable source file: {path.name}") from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or not stat.S_ISREG(opened.st_mode):
            raise SourceTreeError(f"source tree accepts regular files only: {path.name}")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    before_identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    after_identity = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    if before_identity != after_identity:
        raise SourceTreeError(f"source changed during capture: {path.name}")
    return b"".join(chunks), 0o755 if before.st_mode & stat.S_IXUSR else 0o644


def _walk_source_root(repo: Path, root: PurePosixPath) -> list[tuple[str, bytes, int]]:
    base = repo.joinpath(*root.parts)
    try:
        base_info = base.lstat()
    except FileNotFoundError:
        return []
    if stat.S_ISLNK(base_info.st_mode) or not stat.S_ISDIR(base_info.st_mode):
        raise SourceTreeError(f"unsafe source root: {root.as_posix()}")
    rows: list[tuple[str, bytes, int]] = []
    stack = [base]
    while stack:
        directory = stack.pop()
        try:
            children = sorted(directory.iterdir(), key=lambda item: os.fsencode(item.name))
        except OSError as exc:
            raise SourceTreeError(f"unreadable source directory: {directory.name}") from exc
        child_directories: list[Path] = []
        for child in children:
            if child.name in _IGNORED_DIRECTORIES:
                continue
            relative = child.relative_to(repo).as_posix()
            _validated_member_path(relative)
            try:
                info = child.lstat()
            except OSError as exc:
                raise SourceTreeError(f"unreadable source member: {relative}") from exc
            if stat.S_ISLNK(info.st_mode):
                raise SourceTreeError(f"source tree rejects symlink: {relative}")
            if stat.S_ISDIR(info.st_mode):
                child_directories.append(child)
                continue
            if not stat.S_ISREG(info.st_mode):
                raise SourceTreeError(f"source tree rejects special file: {relative}")
            if child.name.endswith(_IGNORED_SUFFIXES):
                continue
            payload, mode = _read_regular(child)
            rows.append((relative, payload, mode))
        stack.extend(reversed(child_directories))
    return rows


def _classifications(repo: Path) -> tuple[set[str], set[str]]:
    tracked = _git_paths(repo, "ls-files", "-z")
    dirty = _git_paths(repo, "diff", "--name-only", "-z", "HEAD")
    return tracked, dirty


def _tar_payload(rows: list[tuple[str, bytes, int]]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.GNU_FORMAT) as archive:
        for name, payload, mode in rows:
            member = tarfile.TarInfo(name)
            member.size = len(payload)
            member.mode = mode
            member.uid = 0
            member.gid = 0
            member.uname = ""
            member.gname = ""
            member.mtime = 0
            archive.addfile(member, io.BytesIO(payload))
    return buffer.getvalue()


def capture_source_tree(
    repo_root: Path | str,
    output_dir: Path | str,
    *,
    environ: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Capture the complete current behavioral tree into a deterministic archive."""
    repo = Path(repo_root)
    output = Path(output_dir)
    env = dict(os.environ if environ is None else environ)
    repo_info = repo.lstat()
    if stat.S_ISLNK(repo_info.st_mode) or not stat.S_ISDIR(repo_info.st_mode):
        raise SourceTreeError("repo_root must be a real directory")
    tracked, dirty = _classifications(repo)
    rows: list[tuple[str, bytes, int]] = []
    try:
        for root in SOURCE_DIRECTORIES:
            rows.extend(_walk_source_root(repo, root))
        for source in ROOT_SOURCES:
            relative = source.as_posix()
            path = repo.joinpath(*source.parts)
            try:
                info = path.lstat()
            except FileNotFoundError as exc:
                raise SourceTreeError(f"required source is missing: {relative}") from exc
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                raise SourceTreeError(f"required source must be regular: {relative}")
            payload, mode = _read_regular(path)
            rows.append((relative, payload, mode))
        rows.sort(key=lambda row: os.fsencode(row[0]))
        names = [name for name, _payload, _mode in rows]
        if len(names) != len(set(names)):
            raise SourceTreeError("duplicate source tree member")
        manifest_rows: list[dict[str, Any]] = []
        total_bytes = 0
        for name, payload, mode in rows:
            path_scan = scan_for_secrets(name.encode("utf-8"), environ=env)
            if not path_scan["ok"]:
                raise SecretMaterialDetected(("source_tree",))
            content_scan = scan_for_secrets(payload, environ=env)
            if not content_scan["ok"]:
                raise SourceTreeError(f"source tree contains secret material: {name}")
            total_bytes += len(payload)
            manifest_rows.append(
                {
                    "path": name,
                    "sha256": sha256_bytes(payload),
                    "bytes": len(payload),
                    "mode": mode,
                    "classification": (
                        "TRACKED_DIRTY"
                        if name in dirty
                        else "TRACKED"
                        if name in tracked
                        else "UNTRACKED"
                    ),
                }
            )
        code_tree_hash = sha256_bytes(canonical_json(manifest_rows).encode("utf-8"))
        manifest = {
            "schema_version": 1,
            "files": manifest_rows,
            "file_count": len(manifest_rows),
            "total_bytes": total_bytes,
            "code_tree_hash": code_tree_hash,
        }
        manifest_payload = (canonical_json(manifest) + "\n").encode("utf-8")
        if not scan_for_secrets(manifest_payload, environ=env)["ok"]:
            raise SecretMaterialDetected(("source_tree_manifest",))
        archive = zstandard.ZstdCompressor(
            level=19,
            threads=0,
            write_checksum=True,
            write_content_size=True,
        ).compress(_tar_payload(rows))
        _atomic_write(output / SOURCE_TREE_BUNDLE, archive)
        try:
            _atomic_write(output / SOURCE_TREE_MANIFEST, manifest_payload)
        except Exception:
            (output / SOURCE_TREE_BUNDLE).unlink(missing_ok=True)
            raise
        return manifest
    except Exception as exc:
        (output / SOURCE_TREE_BUNDLE).unlink(missing_ok=True)
        (output / SOURCE_TREE_MANIFEST).unlink(missing_ok=True)
        if isinstance(exc, SourceTreeError):
            raise
        if isinstance(exc, SecretMaterialDetected):
            raise SourceTreeError("source tree contains secret material") from exc
        raise SourceTreeError(f"source tree capture failed: {type(exc).__name__}") from exc


def _decompress_limited(path: Path, *, max_bytes: int) -> bytes:
    try:
        info = path.lstat()
    except OSError as exc:
        raise SourceTreeError("source tree bundle is unavailable") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise SourceTreeError("source tree bundle must be a regular file")
    chunks: list[bytes] = []
    total = 0
    try:
        with (
            path.open("rb") as compressed,
            zstandard.ZstdDecompressor().stream_reader(compressed) as reader,
        ):
            while True:
                chunk = reader.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise SourceTreeError("source tree tar exceeds size limit")
                chunks.append(chunk)
    except zstandard.ZstdError as exc:
        raise SourceTreeError("invalid source tree compression") from exc
    return b"".join(chunks)


def _load_manifest(path: Path) -> dict[str, Any]:
    try:
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise SourceTreeError("source tree manifest must be a regular file")
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SourceTreeError("invalid source tree manifest") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise SourceTreeError("invalid source tree manifest")
    return payload


def _validate_archive(
    raw_tar: bytes,
    *,
    max_members: int,
    max_member_bytes: int,
    max_total_bytes: int,
) -> tuple[list[tuple[tarfile.TarInfo, bytes]], list[dict[str, Any]]]:
    extracted: list[tuple[tarfile.TarInfo, bytes]] = []
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    total = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(raw_tar), mode="r:") as archive:
            members = archive.getmembers()
            if len(members) > max_members:
                raise SourceTreeError("source tree member count exceeds limit")
            for member in members:
                _validated_member_path(member.name)
                if member.name in seen:
                    raise SourceTreeError(f"duplicate source tree member: {member.name}")
                seen.add(member.name)
                if not member.isfile():
                    raise SourceTreeError("source tree archive accepts regular files only")
                if member.size < 0 or member.size > max_member_bytes:
                    raise SourceTreeError("source tree member size exceeds limit")
                total += member.size
                if total > max_total_bytes:
                    raise SourceTreeError("source tree total size exceeds limit")
                stream = archive.extractfile(member)
                if stream is None:
                    raise SourceTreeError("source tree member is unreadable")
                payload = stream.read(max_member_bytes + 1)
                if len(payload) != member.size:
                    raise SourceTreeError("source tree member size mismatch")
                mode = 0o755 if member.mode & 0o100 else 0o644
                extracted.append((member, payload))
                rows.append(
                    {
                        "path": member.name,
                        "sha256": sha256_bytes(payload),
                        "bytes": len(payload),
                        "mode": mode,
                    }
                )
    except tarfile.TarError as exc:
        raise SourceTreeError("invalid source tree archive") from exc
    return extracted, rows


def _validated_manifest_rows(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    raw_rows = manifest.get("files")
    if not isinstance(raw_rows, list):
        raise SourceTreeError("invalid source tree manifest files")
    rows: list[dict[str, Any]] = []
    names: set[str] = set()
    for raw in raw_rows:
        if not isinstance(raw, dict):
            raise SourceTreeError("invalid source tree manifest row")
        name = raw.get("path")
        digest = raw.get("sha256")
        size = raw.get("bytes")
        mode = raw.get("mode")
        if not isinstance(name, str):
            raise SourceTreeError("invalid source tree manifest path")
        _validated_member_path(name)
        if name in names:
            raise SourceTreeError(f"duplicate source tree manifest member: {name}")
        names.add(name)
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
            or not isinstance(size, int)
            or size < 0
            or mode not in {0o644, 0o755}
        ):
            raise SourceTreeError("invalid source tree manifest row")
        row = {"path": name, "sha256": digest, "bytes": size, "mode": mode}
        if "classification" in raw:
            classification = raw["classification"]
            if classification not in {"TRACKED", "TRACKED_DIRTY", "UNTRACKED"}:
                raise SourceTreeError("invalid source tree classification")
            row["classification"] = classification
        rows.append(row)
    if manifest.get("file_count") != len(rows):
        raise SourceTreeError("source tree manifest count mismatch")
    if manifest.get("total_bytes") != sum(row["bytes"] for row in rows):
        raise SourceTreeError("source tree manifest total mismatch")
    if manifest.get("code_tree_hash") != sha256_bytes(canonical_json(rows).encode("utf-8")):
        raise SourceTreeError("source tree manifest hash mismatch")
    return rows


def _verify_bundle(
    bundle_path: Path,
    manifest_path: Path,
    *,
    max_members: int,
    max_member_bytes: int,
    max_total_bytes: int,
    max_tar_bytes: int,
) -> tuple[list[tuple[tarfile.TarInfo, bytes]], dict[str, Any], list[dict[str, Any]]]:
    raw_tar = _decompress_limited(bundle_path, max_bytes=max_tar_bytes)
    extracted, archive_rows = _validate_archive(
        raw_tar,
        max_members=max_members,
        max_member_bytes=max_member_bytes,
        max_total_bytes=max_total_bytes,
    )
    manifest = _load_manifest(manifest_path)
    manifest_rows = _validated_manifest_rows(manifest)
    archive_by_name = {row["path"]: row for row in archive_rows}
    manifest_by_name = {row["path"]: row for row in manifest_rows}
    if set(archive_by_name) != set(manifest_by_name):
        raise SourceTreeError("source tree membership mismatch")
    for name, archive_row in archive_by_name.items():
        expected = manifest_by_name[name]
        if any(archive_row[key] != expected[key] for key in ("sha256", "bytes", "mode")):
            raise SourceTreeError(f"source tree content mismatch: {name}")
    return extracted, manifest, manifest_rows


def verify_source_tree_bundle(
    bundle: Path | str,
    *,
    manifest_path: Path | str | None = None,
    max_members: int = DEFAULT_MAX_MEMBERS,
    max_member_bytes: int = DEFAULT_MAX_MEMBER_BYTES,
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
    max_tar_bytes: int = DEFAULT_MAX_TAR_BYTES,
) -> dict[str, Any]:
    """Validate archive structure, limits, membership, modes, sizes, and hashes."""
    bundle_path = Path(bundle)
    selected_manifest = (
        Path(manifest_path)
        if manifest_path is not None
        else bundle_path.with_name(SOURCE_TREE_MANIFEST)
    )
    _extracted, manifest, _rows = _verify_bundle(
        bundle_path,
        selected_manifest,
        max_members=max_members,
        max_member_bytes=max_member_bytes,
        max_total_bytes=max_total_bytes,
        max_tar_bytes=max_tar_bytes,
    )
    return {
        "schema_version": 1,
        "code_tree_hash": manifest["code_tree_hash"],
        "file_count": manifest["file_count"],
        "total_bytes": manifest["total_bytes"],
    }


def restore_source_tree(
    bundle: Path | str,
    target: Path | str,
    *,
    manifest_path: Path | str | None = None,
    max_members: int = DEFAULT_MAX_MEMBERS,
    max_member_bytes: int = DEFAULT_MAX_MEMBER_BYTES,
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
    max_tar_bytes: int = DEFAULT_MAX_TAR_BYTES,
) -> dict[str, Any]:
    """Validate completely, then atomically restore a captured source tree."""
    bundle_path = Path(bundle)
    target_path = Path(target)
    selected_manifest = (
        Path(manifest_path)
        if manifest_path is not None
        else bundle_path.with_name(SOURCE_TREE_MANIFEST)
    )
    extracted, manifest, manifest_rows = _verify_bundle(
        bundle_path,
        selected_manifest,
        max_members=max_members,
        max_member_bytes=max_member_bytes,
        max_total_bytes=max_total_bytes,
        max_tar_bytes=max_tar_bytes,
    )
    _reject_path_ancestors(target_path.parent)
    if target_path.exists() or target_path.is_symlink():
        raise SourceTreeError("source tree target must not already exist")
    target_path.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{target_path.name}.restore-", dir=target_path.parent))
    try:
        for member, payload in extracted:
            logical = _validated_member_path(member.name)
            destination = staging.joinpath(*logical.parts)
            _atomic_write(destination, payload, mode=0o755 if member.mode & 0o100 else 0o644)
        restored_rows: list[dict[str, Any]] = []
        for expected in manifest_rows:
            path = staging.joinpath(*PurePosixPath(expected["path"]).parts)
            payload, mode = _read_regular(path)
            restored = {
                "path": expected["path"],
                "sha256": sha256_bytes(payload),
                "bytes": len(payload),
                "mode": mode,
            }
            if "classification" in expected:
                restored["classification"] = expected["classification"]
            restored_rows.append(restored)
        if restored_rows != manifest_rows:
            raise SourceTreeError("restored source tree manifest mismatch")
        os.replace(staging, target_path)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return {
        "schema_version": 1,
        "code_tree_hash": manifest["code_tree_hash"],
        "file_count": manifest["file_count"],
        "total_bytes": manifest["total_bytes"],
        "uses_current_checkout": False,
    }


def _platform_identity() -> dict[str, Any]:
    return {
        "python_implementation": platform.python_implementation(),
        "python_version": platform.python_version(),
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "byteorder": sys.byteorder,
    }


def build_runtime_manifest(
    repo_root: Path | str,
    *,
    dependencies: bytes | None,
    portable_runtime_sha256: str | None = None,
) -> dict[str, Any]:
    """Describe the exact local runtime without resolving anything online."""
    repo = Path(repo_root)
    lock_path = repo / "uv.lock"
    try:
        lock_payload, _mode = _read_regular(lock_path)
    except (FileNotFoundError, SourceTreeError):
        lock_payload = None
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "platform": _platform_identity(),
        "dependency_snapshot": {
            "sha256": sha256_bytes(dependencies) if dependencies is not None else None,
            "available": dependencies is not None,
        },
        "lock": {
            "path": "uv.lock",
            "sha256": sha256_bytes(lock_payload) if lock_payload is not None else None,
            "available": lock_payload is not None,
        },
        "locale": {
            "preferred_encoding": locale.getpreferredencoding(False),
        },
        "timezone": list(time.tzname),
        "network_install_allowed": False,
    }
    if portable_runtime_sha256 is not None:
        if len(portable_runtime_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in portable_runtime_sha256
        ):
            raise SourceTreeError("invalid portable runtime digest")
        manifest["portable_runtime"] = {
            "artifact": PORTABLE_RUNTIME,
            "sha256": portable_runtime_sha256,
        }
    return manifest


def _regular_digest(path: Path) -> str | None:
    try:
        info = path.lstat()
    except OSError:
        return None
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        return None
    return sha256_bytes(path.read_bytes())


def check_runtime_availability(
    manifest: dict[str, Any],
    *,
    dependencies: bytes | None,
    portable_runtime: Path | str | None = None,
) -> dict[str, str]:
    """Return availability only; this function never installs dependencies."""
    if manifest.get("schema_version") != 1 or not isinstance(manifest.get("platform"), dict):
        raise SourceTreeError("invalid runtime manifest")
    if manifest["platform"] != _platform_identity():
        return {"status": "UNAVAILABLE", "reason": "PLATFORM_MISMATCH"}
    dependency_snapshot = manifest.get("dependency_snapshot")
    if not isinstance(dependency_snapshot, dict):
        raise SourceTreeError("invalid runtime dependency snapshot")
    expected = dependency_snapshot.get("sha256")
    if (
        dependencies is not None
        and isinstance(expected, str)
        and sha256_bytes(dependencies) == expected
    ):
        return {"status": "LOCAL_ENV_MATCHED", "reason": "EXACT_DEPENDENCY_SNAPSHOT"}
    portable = manifest.get("portable_runtime")
    if portable_runtime is not None and isinstance(portable, dict):
        actual = _regular_digest(Path(portable_runtime))
        if actual is not None and actual == portable.get("sha256"):
            return {"status": "PACKAGED", "reason": "VERIFIED_PORTABLE_RUNTIME"}
    if dependencies is None:
        return {"status": "UNAVAILABLE", "reason": "DEPENDENCIES_UNAVAILABLE"}
    return {"status": "UNAVAILABLE", "reason": "DEPENDENCIES_MISMATCH"}


def import_portable_runtime(
    source: Path | str,
    output_dir: Path | str,
    *,
    authorized_root: Path | str,
    expected_sha256: str,
) -> dict[str, str]:
    """Copy an explicitly authorized offline runtime package after hash verification."""
    source_path = Path(source)
    root = Path(authorized_root)
    try:
        resolved = source_path.resolve(strict=True)
        resolved.relative_to(root.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise SourceTreeError("portable runtime is outside the authorized path") from exc
    digest = _regular_digest(source_path)
    if digest is None or digest != expected_sha256:
        raise SourceTreeError("portable runtime digest mismatch")
    destination = Path(output_dir) / PORTABLE_RUNTIME
    _atomic_write(destination, source_path.read_bytes())
    return {"artifact": PORTABLE_RUNTIME, "sha256": digest}
