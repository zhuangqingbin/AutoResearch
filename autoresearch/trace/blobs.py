"""Private content-addressed evidence blobs for one forensic capsule."""

from __future__ import annotations

import contextlib
import hashlib
import io
import os
import re
import secrets
import stat
from collections.abc import Iterable
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_BLOCK_SIZE = 1024 * 1024


def _require_real_directory(path: Path) -> Path:
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"capsule root is missing: {path}") from exc
    if stat.S_ISLNK(info.st_mode):
        raise ValueError(f"capsule root is a symlink: {path}")
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError(f"capsule root is not a directory: {path}")
    return path


def _secure_directory(root: Path, parts: tuple[str, ...]) -> Path:
    root = _require_real_directory(root)
    resolved_root = root.resolve(strict=True)
    current = root
    for part in parts:
        candidate = current / part
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            with contextlib.suppress(FileExistsError):
                candidate.mkdir(mode=0o700)
            info = candidate.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise ValueError(f"blob directory contains a symlink: {candidate}")
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError(f"blob directory component is not a directory: {candidate}")
        try:
            candidate.resolve(strict=True).relative_to(resolved_root)
        except ValueError as exc:
            raise ValueError(f"blob directory escapes capsule: {candidate}") from exc
        current = candidate
    return current


def _fsync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | _DIRECTORY | _NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_all(fd: int, chunks: Iterable[bytes], digest: hashlib._Hash) -> int:
    total = 0
    for chunk in chunks:
        if not chunk:
            continue
        digest.update(chunk)
        view = memoryview(chunk)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError("short write while persisting evidence blob")
            total += written
            view = view[written:]
    return total


def _read_fd(fd: int) -> Iterable[bytes]:
    while True:
        chunk = os.read(fd, _BLOCK_SIZE)
        if not chunk:
            return
        yield chunk


def _verify_existing(target: Path, digest: str, *, expected: bytes | None = None) -> None:
    try:
        info = target.lstat()
    except FileNotFoundError:
        raise
    if stat.S_ISLNK(info.st_mode):
        raise ValueError(f"blob target is a symlink: {target}")
    if not stat.S_ISREG(info.st_mode):
        raise ValueError(f"blob target is not a regular file: {target}")
    fd = os.open(target, os.O_RDONLY | _NOFOLLOW)
    try:
        actual = bytearray() if expected is not None else None
        hasher = hashlib.sha256()
        for chunk in _read_fd(fd):
            hasher.update(chunk)
            if actual is not None:
                actual.extend(chunk)
    finally:
        os.close(fd)
    if hasher.hexdigest() != digest or (expected is not None and bytes(actual or b"") != expected):
        raise RuntimeError(f"blob collision or corrupt existing content: {target}")


def blob_path(capsule: Path | str, digest: str) -> Path:
    """Return the canonical path for a full lowercase SHA-256 digest."""
    root = _require_real_directory(Path(capsule))
    if type(digest) is not str or not _DIGEST_RE.fullmatch(digest):
        raise ValueError("blob digest must be a full lowercase SHA-256 digest")
    target = root / "blobs" / "sha256" / digest[:2] / digest
    for component in (root / "blobs", root / "blobs/sha256", target.parent):
        try:
            info = component.lstat()
        except FileNotFoundError:
            break
        if stat.S_ISLNK(info.st_mode):
            raise ValueError(f"blob path contains a symlink: {component}")
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError(f"blob path component is not a directory: {component}")
    try:
        info = target.lstat()
    except FileNotFoundError:
        return target
    if stat.S_ISLNK(info.st_mode):
        raise ValueError(f"blob target is a symlink: {target}")
    if not stat.S_ISREG(info.st_mode):
        raise ValueError(f"blob target is not a regular file: {target}")
    return target


def _publish(
    capsule: Path,
    digest: str,
    chunks: Iterable[bytes],
    *,
    expected: bytes | None = None,
) -> str:
    directory = _secure_directory(capsule, ("blobs", "sha256", digest[:2]))
    target = directory / digest
    try:
        _verify_existing(target, digest, expected=expected)
        os.chmod(target, 0o600, follow_symlinks=False)
        return digest
    except FileNotFoundError:
        pass

    temp: Path | None = None
    fd = -1
    try:
        for _ in range(128):
            candidate = directory / f".{digest}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
            try:
                fd = os.open(
                    candidate,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW,
                    0o600,
                )
                temp = candidate
                break
            except FileExistsError:
                continue
        if temp is None or fd < 0:  # pragma: no cover - random namespace exhaustion
            raise FileExistsError("could not allocate exclusive blob temp")
        written_hash = hashlib.sha256()
        _write_all(fd, chunks, written_hash)
        os.fchmod(fd, 0o600)
        os.fsync(fd)
        os.close(fd)
        fd = -1
        if written_hash.hexdigest() != digest:
            raise RuntimeError("source changed while copying evidence blob")
        try:
            os.link(temp, target, follow_symlinks=False)
            _fsync_directory(directory)
        except FileExistsError:
            _verify_existing(target, digest, expected=expected)
        os.chmod(target, 0o600, follow_symlinks=False)
        return digest
    finally:
        if fd >= 0:
            os.close(fd)
        if temp is not None:
            temp.unlink(missing_ok=True)
            with contextlib.suppress(OSError):
                _fsync_directory(directory)


def put_bytes(capsule: Path | str, payload: bytes) -> str:
    """Publish exact bytes once and return their content digest."""
    if not isinstance(payload, bytes):
        raise TypeError("blob payload must be bytes")
    digest = hashlib.sha256(payload).hexdigest()
    return _publish(Path(capsule), digest, (payload,), expected=payload)


def _file_digest(path: Path) -> str:
    try:
        info = path.lstat()
    except FileNotFoundError:
        raise
    if stat.S_ISLNK(info.st_mode):
        raise ValueError(f"blob source is a symlink: {path}")
    if not stat.S_ISREG(info.st_mode):
        raise ValueError(f"blob source is not a regular file: {path}")
    fd = os.open(path, os.O_RDONLY | _NOFOLLOW)
    try:
        digest = hashlib.sha256()
        for chunk in _read_fd(fd):
            digest.update(chunk)
        return digest.hexdigest()
    finally:
        os.close(fd)


def put_file(capsule: Path | str, path: Path | str) -> str:
    """Copy a regular source file into the capsule without following symlinks."""
    source = Path(path)
    digest = _file_digest(source)
    fd = os.open(source, os.O_RDONLY | _NOFOLLOW)
    try:
        return _publish(Path(capsule), digest, _read_fd(fd))
    finally:
        os.close(fd)


def dataframe_bytes(frame: pd.DataFrame) -> bytes:
    """Serialize a validated frame with stable parquet writer options."""
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("frame must be a pandas DataFrame")
    table = pa.Table.from_pandas(frame, preserve_index=False)
    stream = io.BytesIO()
    pq.write_table(
        table,
        stream,
        compression="zstd",
        use_dictionary=False,
        write_statistics=True,
        version="2.6",
        data_page_version="1.0",
        store_schema=True,
        write_page_index=False,
    )
    return stream.getvalue()


def put_dataframe(capsule: Path | str, frame: pd.DataFrame) -> str:
    """Serialize a fileless validated frame and publish it as a parquet blob."""
    return put_bytes(capsule, dataframe_bytes(frame))
