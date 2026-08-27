"""Deterministic executable-identity snapshots with fail-closed secret handling."""

from __future__ import annotations

import base64
import io
import json
import locale
import math
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import zstandard

from autoresearch.trace.atomic import canonical_json, sha256_bytes

REDACTION_RULE_VERSION = 2
_REDACTED = "[REDACTED]"
_SECRET_KEY_RE = re.compile(
    r"token|secret|password|authorization|cookie|api[_-]?key", re.IGNORECASE
)
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}")
_SLACK_TOKEN_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9])xox[bpar]-(?:[0-9]{1,3}-)?"
    r"(?:[0-9]{8,16}-){1,3}[A-Za-z0-9]{20,80}(?![A-Za-z0-9])"
)
_KEYLIKE_RE = re.compile(r"(?i)\b(?:sk|pk|rk|api)[-_](?:live[-_])?[A-Za-z0-9_-]{16,}\b")
_AWS_KEY_RE = re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")
_URI_CREDENTIAL_RE = re.compile(
    r"(?i)\b(?:postgres(?:ql)?|mysql|mariadb|mongodb(?:\+srv)?|redis|rediss|http|https)"
    r"://[^\s/@:]+:[^\s/@]+@[^\s]+"
)
_PEM_PRIVATE_KEY_RE = re.compile(
    r"(?is)-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----.*?"
    r"(?:-----END (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----|\Z)"
)
_PROVIDER_TOKEN_RES = (
    re.compile(r"(?i)(?<![A-Za-z0-9])gh[pousr]_[A-Za-z0-9]{36,255}(?![A-Za-z0-9])"),
    re.compile(r"(?i)(?<![A-Za-z0-9])github_pat_[A-Za-z0-9_]{80,255}(?![A-Za-z0-9_])"),
    re.compile(r"(?i)(?<![A-Za-z0-9])glpat-[A-Za-z0-9_-]{20,255}(?![A-Za-z0-9_-])"),
    re.compile(r"(?<![A-Za-z0-9])AIza[0-9A-Za-z_-]{32,45}(?![A-Za-z0-9_-])"),
    re.compile(r"(?i)(?<![A-Za-z0-9])(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,255}"),
    re.compile(r"(?<![A-Za-z0-9])SK[0-9a-fA-F]{32}(?![0-9a-fA-F])"),
    re.compile(r"(?<![A-Za-z0-9])SG\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{20,}"),
    re.compile(r"(?i)(?<![A-Za-z0-9])npm_[A-Za-z0-9]{36,255}(?![A-Za-z0-9])"),
)
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(?:[A-Za-z0-9_-]*(?:token|secret|password|authorization|cookie)"
    r"|[A-Za-z0-9_-]*api[_-]?key)\b[\"']?\s*[:=]\s*[^\s,;{\[]{4,}"
)
_CONTEXT_CREDENTIAL_RE = re.compile(
    r"(?i)\b(?:credential|credentials|client[_-]?secret|[A-Za-z0-9_-]*dsn|database[_-]?url|"
    r"mongo(?:db)?[_-]?(?:uri|url)|redis[_-]?(?:uri|url)|connection[_-]?string)"
    r"\b[\"']?\s*[:=]\s*[\"']?[^\s\"',;}{\]]{6,}"
)
_OPAQUE_RE = re.compile(rb"(?<![A-Za-z0-9+/=_-])[A-Za-z0-9+/=_-]{24,}(?![A-Za-z0-9+/=_-])")
_JWT_RE = re.compile(
    rb"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\."
    rb"[A-Za-z0-9_-]{16,}(?![A-Za-z0-9_-])"
)
_OPAQUE_CLASSES = tuple(
    re.compile(pattern) for pattern in (rb"[a-z]", rb"[A-Z]", rb"[0-9]", rb"[+/=_-]")
)
_HASH_LENGTHS = frozenset({32, 40, 64, 96, 128})
_RUN_ID_RE = re.compile(rb"[0-9]{8}T[0-9]{12}Z")
_UUID_RE = re.compile(
    rb"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_SOURCE_DIRS = (
    Path("autoresearch"),
    Path(".claude/agents"),
    Path(".claude/skills"),
    Path(".claude/workflows"),
)
_PROMPT_DIRS = (
    Path(".claude/agents"),
    Path(".claude/skills"),
    Path(".claude/workflows"),
)
_ROOT_SOURCES = (
    Path("pyproject.toml"),
    Path("uv.lock"),
    Path("AGENTS.md"),
    Path("CLAUDE.md"),
)
_KNOWN_SECRET_ENV = frozenset(
    {
        "ANTHROPIC_API_KEY",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "FRED_API_KEY",
        "OPENAI_API_KEY",
        "TUSHARE_TOKEN",
    }
)
_SECRET_CONNECTION_ENV_RE = re.compile(
    r"(?i)^(?:DATABASE_URL|DB_URL|[A-Z0-9_]*_DSN|MONGO(?:DB)?_(?:URI|URL)|"
    r"REDIS_(?:URI|URL|TLS_URL)|[A-Z0-9_]*CONNECTION_STRING)$"
)
_SNAPSHOT_LOCKS: dict[str, threading.Lock] = {}
_SNAPSHOT_LOCKS_GUARD = threading.Lock()
_OWNED_FILES = frozenset(
    {
        "code.patch",
        "dependencies.txt",
        "environment.json",
        "snapshot_result.json",
        "source_manifest.json",
        "submodules.json",
        "untracked_sources.tar.zst",
    }
)


@dataclass(frozen=True)
class RedactionResult:
    value: Any
    hits: int


class SecretMaterialDetected(ValueError):
    """Artifact bytes failed the secret probe; raw bytes must not be persisted."""

    def __init__(self, kinds: tuple[str, ...]):
        self.kinds = kinds
        super().__init__("suspected secret material detected")


def _secret_values(environ: dict[str, str]) -> tuple[str, ...]:
    values = {
        value
        for key, value in environ.items()
        if value
        and len(value) >= 4
        and (
            key in _KNOWN_SECRET_ENV
            or _SECRET_KEY_RE.search(key)
            or _SECRET_CONNECTION_ENV_RE.fullmatch(key)
        )
    }
    return tuple(sorted(values, key=lambda item: (-len(item), item)))


def redact_value(value: Any, *, environ: dict[str, str] | None = None) -> RedactionResult:
    """Recursively redact secret-bearing keys, token formats, and known env values."""
    env = dict(os.environ if environ is None else environ)
    known_values = _secret_values(env)
    hits = 0

    def redact_text(item: str) -> str:
        nonlocal hits
        text = item
        for secret in known_values:
            count = text.count(secret)
            if count:
                hits += count
                text = text.replace(secret, _REDACTED)

        def replace(pattern: re.Pattern[str], current: str) -> str:
            nonlocal hits
            matches = list(pattern.finditer(current))
            hits += len(matches)
            return pattern.sub(_REDACTED, current)

        text = replace(_BEARER_RE, text)
        text = replace(_SLACK_TOKEN_RE, text)
        text = replace(_URI_CREDENTIAL_RE, text)
        text = replace(_PEM_PRIVATE_KEY_RE, text)
        for pattern in _PROVIDER_TOKEN_RES:
            text = replace(pattern, text)
        text = replace(_KEYLIKE_RE, text)
        text = replace(_AWS_KEY_RE, text)
        text = replace(_SECRET_ASSIGNMENT_RE, text)
        text = replace(_CONTEXT_CREDENTIAL_RE, text)
        return text

    def redact(item: Any, *, secret_key: bool = False) -> Any:
        nonlocal hits
        if secret_key:
            hits += 1
            return _REDACTED
        if isinstance(item, dict):
            result: dict[str, Any] = {}
            collisions: dict[str, int] = {}
            ordered = sorted(item.items(), key=lambda row: (str(row[0]), type(row[0]).__name__))
            for key, child in ordered:
                original_key = str(key)
                base_key = redact_text(original_key)
                ordinal = collisions.get(base_key, 0) + 1
                safe_key = base_key if ordinal == 1 else f"{base_key}#{ordinal}"
                while safe_key in result:
                    ordinal += 1
                    safe_key = f"{base_key}#{ordinal}"
                collisions[base_key] = ordinal
                result[safe_key] = redact(
                    child,
                    secret_key=bool(_SECRET_KEY_RE.search(original_key)),
                )
            return result
        if isinstance(item, list):
            return [redact(child) for child in item]
        if isinstance(item, tuple):
            return tuple(redact(child) for child in item)
        if not isinstance(item, str):
            return item
        return redact_text(item)

    return RedactionResult(value=redact(value), hits=hits)


def _entropy(value: bytes) -> float:
    if not value:
        return 0.0
    counts: dict[int, int] = {}
    for byte in value:
        counts[byte] = counts.get(byte, 0) + 1
    size = len(value)
    return -sum((count / size) * math.log2(count / size) for count in counts.values())


def _looks_like_stable_identifier(value: bytes) -> bool:
    if _RUN_ID_RE.fullmatch(value) or _UUID_RE.fullmatch(value):
        return True
    if len(value) in _HASH_LENGTHS and re.fullmatch(rb"[0-9a-fA-F]+", value):
        return True
    return bool(
        re.fullmatch(
            rb"(?:(?:contract_hash|event_hash|prev_hash)=[0-9a-fA-F]{16,})+",
            value,
        )
    )


def _looks_like_source_identifier(value: bytes) -> bool:
    """Recognize human source paths/slugs without weakening opaque credentials."""
    lower = sum(byte in b"abcdefghijklmnopqrstuvwxyz" for byte in value)
    upper = sum(byte in b"ABCDEFGHIJKLMNOPQRSTUVWXYZ" for byte in value)
    digits = sum(byte in b"0123456789" for byte in value)
    separators = sum(byte in b"-_" for byte in value)
    if b"/" in value and b"+" not in value and b"=" not in value:
        return separators >= 1 and lower >= (3 * max(upper, 1))
    if b"/" not in value and b"+" not in value and b"=" not in value:
        return separators >= 2 and upper == 0 and lower > digits
    return False


def _is_jwt_candidate(value: bytes) -> bool:
    segments = value.split(b".")
    if len(segments) != 3:
        return False
    try:
        decoded = []
        for segment in segments[:2]:
            padding = b"=" * (-len(segment) % 4)
            decoded.append(json.loads(base64.urlsafe_b64decode(segment + padding)))
    except (ValueError, TypeError, json.JSONDecodeError):
        return False
    return all(isinstance(item, dict) for item in decoded)


def scan_for_secrets(payload: bytes, *, environ: dict[str, str] | None = None) -> dict:
    """Find likely unredacted secrets without returning or hashing their values."""
    if not isinstance(payload, bytes):
        raise TypeError("payload must be bytes")
    findings: list[dict[str, int | str]] = []
    occupied: list[tuple[int, int]] = []
    env = dict(os.environ if environ is None else environ)

    def add(kind: str, start: int, end: int) -> None:
        if any(
            start < existing_end and end > existing_start
            for existing_start, existing_end in occupied
        ):
            return
        occupied.append((start, end))
        findings.append({"kind": kind, "offset": start, "length": end - start})

    text = payload.decode("latin-1")
    for kind, pattern in (
        ("bearer", _BEARER_RE),
        ("slack_token", _SLACK_TOKEN_RE),
        ("uri_userinfo", _URI_CREDENTIAL_RE),
        ("private_key", _PEM_PRIVATE_KEY_RE),
        ("key_like", _KEYLIKE_RE),
        ("cloud_key", _AWS_KEY_RE),
        ("secret_assignment", _SECRET_ASSIGNMENT_RE),
        ("contextual_credential", _CONTEXT_CREDENTIAL_RE),
    ):
        for match in pattern.finditer(text):
            add(kind, match.start(), match.end())
    for pattern in _PROVIDER_TOKEN_RES:
        for match in pattern.finditer(text):
            add("provider_token", match.start(), match.end())

    for match in _JWT_RE.finditer(payload):
        if _is_jwt_candidate(match.group()):
            add("jwt", match.start(), match.end())

    for secret in _secret_values(env):
        encoded = secret.encode("utf-8")
        offset = 0
        while True:
            start = payload.find(encoded, offset)
            if start < 0:
                break
            add("known_secret", start, start + len(encoded))
            offset = start + len(encoded)

    for match in _OPAQUE_RE.finditer(payload):
        candidate = match.group()
        if _looks_like_stable_identifier(candidate) or _looks_like_source_identifier(candidate):
            continue
        prefix = payload[max(0, match.start() - 32) : match.start()].lower()
        if re.search(rb"(?:sha(?:256)?|contract_hash|event_hash|prev_hash)=$", prefix):
            continue
        classes = sum(bool(pattern.search(candidate)) for pattern in _OPAQUE_CLASSES)
        if classes >= 2 and _entropy(candidate) >= 4.5:
            add("high_entropy", match.start(), match.end())

    findings.sort(key=lambda row: (int(row["offset"]), str(row["kind"])))
    return {"ok": not findings, "hits": len(findings), "findings": findings}


def _safe_error(exc: BaseException | str, *, environ: dict[str, str]) -> str:
    raw = str(exc) or (type(exc).__name__ if isinstance(exc, BaseException) else "error")
    redacted = str(redact_value(raw, environ=environ).value)
    if not scan_for_secrets(redacted.encode("utf-8"), environ=environ)["ok"]:
        return _REDACTED
    return redacted


def _reject_symlink_ancestors(path: Path) -> None:
    """Reject existing symlink/non-directory components without resolving them."""
    absolute = path.absolute()
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode):
            raise ValueError(f"identity destination contains a symlink: {current}")
        if current != absolute and not stat.S_ISDIR(info.st_mode):
            raise ValueError(f"identity destination parent is not a directory: {current}")


def _atomic_write_bytes(path: Path, payload: bytes, *, mode: int = 0o600) -> Path:
    _reject_symlink_ancestors(path.parent)
    path.parent.mkdir(parents=True, exist_ok=True)
    _reject_symlink_ancestors(path.parent)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb", closefd=True) as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            directory_fd = os.open(
                path.parent,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
            )
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def _write_scanned(
    path: Path,
    payload: bytes,
    *,
    mode: int = 0o600,
    environ: dict[str, str] | None = None,
) -> Path:
    scan = scan_for_secrets(payload, environ=environ)
    if not scan["ok"]:
        kinds = tuple(sorted({str(row["kind"]) for row in scan["findings"]}))
        raise SecretMaterialDetected(kinds)
    return _atomic_write_bytes(path, payload, mode=mode)


def _run(repo: Path, argv: list[str], *, text: bool = False):
    return subprocess.run(
        argv,
        cwd=repo,
        check=True,
        capture_output=True,
        text=text,
    ).stdout


def _git_text(repo: Path, *args: str) -> str:
    return str(_run(repo, ["git", *args], text=True)).strip()


def _git_paths(repo: Path, *args: str) -> tuple[Path, ...]:
    raw = bytes(_run(repo, ["git", *args]))
    return tuple(Path(os.fsdecode(item)) for item in sorted(raw.split(b"\0")) if item)


def _path_bytes(relative: Path) -> bytes:
    return os.fsencode(relative.as_posix())


def _path_display(relative: Path) -> str:
    raw = _path_bytes(relative)
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("utf-8", errors="backslashreplace")


def _path_record(relative: Path, **extra: Any) -> dict:
    raw = _path_bytes(relative)
    return {
        "path": _path_display(relative),
        "raw_path_hex": raw.hex(),
        **extra,
    }


def _sanitize_path_record(row: dict, *, environ: dict[str, str]) -> dict:
    raw_hex = row.get("raw_path_hex")
    if not isinstance(raw_hex, str):
        return row
    raw = bytes.fromhex(raw_hex)
    if scan_for_secrets(raw, environ=environ)["ok"]:
        return row
    sanitized = dict(row)
    sanitized.pop("raw_path_hex", None)
    sanitized["path"] = str(redact_value(str(row.get("path", "")), environ=environ).value)
    if not scan_for_secrets(sanitized["path"].encode("utf-8"), environ=environ)["ok"]:
        sanitized["path"] = _REDACTED
    return sanitized


def _archive_member(relative: Path) -> str:
    raw = _path_bytes(relative)
    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError:
        return f"__raw_path__/{raw.hex()}"
    if decoded.startswith("/") or ".." in PurePosixPath(decoded).parts:
        return f"__raw_path__/{raw.hex()}"
    return decoded


def _repository_epoch(repo: Path) -> dict[str, str]:
    head = bytes(_run(repo, ["git", "rev-parse", "HEAD"])).strip()
    status = bytes(
        _run(
            repo,
            ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        )
    )
    untracked = bytes(
        _run(repo, ["git", "ls-files", "--others", "--exclude-standard", "-z"])
    )
    return {
        "head": head.decode("ascii"),
        "status_sha256": sha256_bytes(status),
        "untracked_sha256": sha256_bytes(untracked),
    }


@contextmanager
def _snapshot_lock(output: Path):
    key = str(output.absolute())
    with _SNAPSHOT_LOCKS_GUARD:
        process_lock = _SNAPSHOT_LOCKS.setdefault(key, threading.Lock())
    lock_path = output.parent / f".{output.name}.lock"
    _reject_symlink_ancestors(lock_path.parent)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with process_lock, lock_path.open("a+b") as stream:
        if os.name != "nt":
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name != "nt":
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _clean_owned_artifacts(output: Path) -> None:
    for name in sorted(_OWNED_FILES):
        path = output / name
        try:
            info = path.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode):
            raise ValueError("identity-owned artifact unexpectedly became a directory")
        path.unlink()
    prompts = output / "prompts"
    try:
        info = prompts.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        prompts.unlink()
        return
    shutil.rmtree(prompts)


def _walk_regular(repo: Path, relative_root: Path) -> tuple[list[Path], list[dict]]:
    files: list[Path] = []
    excluded: list[dict] = []
    try:
        root_fd, root_info = _open_relative_fd(
            repo,
            relative_root,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
    except FileNotFoundError:
        return files, excluded
    except (NotADirectoryError, OSError, ValueError):
        excluded.append(_path_record(relative_root, reason="unsafe_root"))
        return files, excluded
    if not stat.S_ISDIR(root_info.st_mode):
        os.close(root_fd)
        excluded.append(_path_record(relative_root, reason="unsafe_root"))
        return files, excluded

    stack: list[tuple[int, bytes]] = [(root_fd, _path_bytes(relative_root))]
    directory_flags = (
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    )
    while stack:
        directory_fd, raw_directory = stack.pop()
        child_dirs: list[tuple[int, bytes]] = []
        try:
            try:
                with os.scandir(directory_fd) as entries:
                    ordered = sorted(entries, key=lambda entry: os.fsencode(entry.name))
            except OSError:
                excluded.append(
                    _path_record(
                        Path(os.fsdecode(raw_directory)),
                        reason="walk_error",
                    )
                )
                continue
            for entry in ordered:
                raw_name = os.fsencode(entry.name)
                raw_relative = raw_directory + b"/" + raw_name
                relative = Path(os.fsdecode(raw_relative))
                try:
                    info = os.stat(
                        raw_name,
                        dir_fd=directory_fd,
                        follow_symlinks=False,
                    )
                except OSError:
                    excluded.append(_path_record(relative, reason="walk_error"))
                    continue
                if stat.S_ISDIR(info.st_mode):
                    try:
                        child_fd = os.open(raw_name, directory_flags, dir_fd=directory_fd)
                    except OSError:
                        excluded.append(_path_record(relative, reason="unsafe_directory"))
                    else:
                        child_dirs.append((child_fd, raw_relative))
                elif stat.S_ISREG(info.st_mode):
                    files.append(relative)
                elif stat.S_ISLNK(info.st_mode):
                    try:
                        _read_source(repo, relative)
                        files.append(relative)
                    except (FileNotFoundError, OSError, RuntimeError, ValueError):
                        excluded.append(_path_record(relative, reason="symlink_escape"))
                else:
                    excluded.append(_path_record(relative, reason="special"))
        finally:
            os.close(directory_fd)
        stack.extend(reversed(child_dirs))
    return sorted(files, key=lambda path: path.as_posix()), sorted(
        excluded, key=lambda row: (row["path"], row["reason"])
    )


def _open_relative_fd(repo: Path, relative: Path, flags: int) -> tuple[int, os.stat_result]:
    parts = _path_bytes(relative).split(b"/")
    if not parts or any(part in (b"", b".", b"..") for part in parts):
        raise ValueError("unsafe relative source path")
    directory_flags = (
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    )
    directory_fd = os.open(repo, directory_flags)
    try:
        for part in parts[:-1]:
            child_fd = os.open(part, directory_flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = child_fd
        before = os.stat(parts[-1], dir_fd=directory_fd, follow_symlinks=False)
        fd = os.open(
            parts[-1],
            flags | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory_fd,
        )
        return fd, before
    finally:
        os.close(directory_fd)


def _read_regular(repo: Path, relative: Path) -> tuple[bytes, int]:
    fd, before = _open_relative_fd(repo, relative, os.O_RDONLY)
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode):
            raise ValueError("source is not a regular file")
        chunks = []
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(fd)
    finally:
        os.close(fd)
    identity_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    identity_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    if identity_before != identity_after:
        raise RuntimeError("source changed during snapshot")
    mode = 0o755 if before.st_mode & stat.S_IXUSR else 0o644
    return b"".join(chunks), mode


def _read_source(repo: Path, relative: Path) -> tuple[bytes, int, dict[str, str]]:
    path = repo / relative
    info = path.lstat()
    if stat.S_ISREG(info.st_mode):
        payload, mode = _read_regular(repo, relative)
        return payload, mode, {}
    if not stat.S_ISLNK(info.st_mode):
        raise ValueError("source is not a regular file or safe symlink")
    raw_target = os.fsencode(os.readlink(path))
    resolved = path.resolve(strict=True)
    repo_resolved = repo.resolve(strict=True)
    try:
        resolved_relative = resolved.relative_to(repo_resolved)
    except ValueError as exc:
        raise ValueError("source symlink escapes repository") from exc
    payload, mode = _read_regular(repo, resolved_relative)
    return payload, mode, {
        "link_target": _path_display(Path(os.fsdecode(raw_target))),
        "resolved_source": _path_display(resolved_relative),
        "resolved_sha256": sha256_bytes(payload),
    }


def _tar_bytes(files: list[tuple[str, bytes, int]]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.GNU_FORMAT) as archive:
        for name, payload, mode in sorted(files, key=lambda row: row[0]):
            info = tarfile.TarInfo(name=PurePosixPath(name).as_posix())
            info.size = len(payload)
            info.mode = mode
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            info.mtime = 0
            archive.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()


def _component(
    status: str,
    *,
    artifacts: tuple[str, ...] = (),
    errors: tuple[str, ...] = (),
    missing: tuple[str, ...] = (),
) -> dict:
    return {
        "status": status,
        "artifacts": list(artifacts),
        "errors": list(errors),
        "missing": list(missing),
    }


def _member_rejection_reason(error: BaseException) -> str:
    if isinstance(error, SecretMaterialDetected):
        return "SECRET_DETECTED"
    if isinstance(error, PermissionError):
        return "UNREADABLE"
    if isinstance(error, FileNotFoundError):
        return "MISSING"
    if isinstance(error, ValueError):
        return "UNSAFE_FILE_TYPE"
    return "READ_ERROR"


def _prompt_destination(relative: Path) -> Path:
    raw = _path_bytes(relative)
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError:
        return Path("prompts/__raw_path__") / raw.hex()
    return Path("prompts") / relative.relative_to(".claude")


def _submodule_snapshot(repo: Path, output: Path, *, environ: dict[str, str]) -> dict:
    raw = bytes(_run(repo, ["git", "ls-files", "--stage", "-z"]))
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for record in sorted(item for item in raw.split(b"\0") if item):
        metadata, raw_path = record.split(b"\t", 1)
        mode, gitlink_sha, _stage = metadata.split(b" ", 2)
        if mode != b"160000":
            continue
        relative = Path(os.fsdecode(raw_path))
        row = _path_record(relative, gitlink_sha=gitlink_sha.decode("ascii"))
        checkout = repo / relative
        try:
            info = checkout.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise ValueError("unsafe submodule checkout")
            head = bytes(_run(repo, ["git", "-C", os.fsdecode(raw_path), "rev-parse", "HEAD"])).strip()
            status = bytes(
                _run(
                    repo,
                    ["git", "-C", os.fsdecode(raw_path), "status", "--porcelain=v1", "-z"],
                )
            )
            diff = bytes(
                _run(
                    repo,
                    ["git", "-C", os.fsdecode(raw_path), "diff", "--binary", "--no-ext-diff", "HEAD"],
                )
            )
            if not scan_for_secrets(status + b"\n" + diff, environ=environ)["ok"]:
                raise SecretMaterialDetected(("submodule",))
            row.update(
                {
                    "checked_out_head": head.decode("ascii"),
                    "status": "CAPTURED",
                    "status_hex": status.hex(),
                    "diff_hex": diff.hex(),
                }
            )
        except FileNotFoundError:
            row["status"] = "MISSING_CHECKOUT"
            errors.append(f"{row['path']}: MISSING_CHECKOUT")
        except Exception as exc:
            row["status"] = _member_rejection_reason(exc)
            errors.append(f"{row['path']}: {row['status']}")
        rows.append(row)
    payload = {"schema_version": 1, "submodules": rows}
    _write_scanned(
        output / "submodules.json",
        (canonical_json(payload) + "\n").encode("utf-8"),
        environ=environ,
    )
    return _component(
        "PARTIAL" if errors else "SUCCESS",
        artifacts=("submodules.json",),
        errors=tuple(errors),
        missing=tuple(errors),
    )


def _environment(
    *,
    repo: Path,
    engine: str,
    model: str | None,
    effort: str | None,
    service_tier: str | None,
    environ: dict[str, str],
) -> dict:
    try:
        uv_version = str(_run(repo, ["uv", "--version"], text=True)).strip()
    except Exception as exc:
        uv_version = f"unavailable:{type(exc).__name__}"
    secret_keys = set(_KNOWN_SECRET_ENV)
    secret_keys.update(
        key
        for key in environ
        if _SECRET_KEY_RE.search(key) or _SECRET_CONNECTION_ENV_RE.fullmatch(key)
    )
    secret_presence = {key: {"present": bool(environ.get(key))} for key in sorted(secret_keys)}
    result = {
        "schema_version": 1,
        "engine": engine,
        "python": {
            "executable": sys.executable,
            "implementation": platform.python_implementation(),
            "version": platform.python_version(),
        },
        "uv": uv_version,
        "os": platform.system(),
        "os_release": platform.release(),
        "architecture": platform.machine(),
        "timezone": {"TZ": environ.get("TZ"), "names": list(time.tzname)},
        "locale": {
            "LANG": environ.get("LANG"),
            "LC_ALL": environ.get("LC_ALL"),
            "preferred_encoding": locale.getpreferredencoding(False),
        },
    }
    if model is not None:
        result["model"] = model
    if effort is not None:
        result["effort"] = effort
    if service_tier is not None:
        result["service_tier"] = service_tier
    redacted = redact_value(result, environ=environ).value
    safe_presence: dict[str, dict[str, bool]] = {}
    for key, presence in secret_presence.items():
        safe_key = key
        if not scan_for_secrets(key.encode("utf-8"), environ={})["ok"]:
            safe_key = _REDACTED
            ordinal = 1
            while safe_key in safe_presence:
                ordinal += 1
                safe_key = f"{_REDACTED}#{ordinal}"
        safe_presence[safe_key] = presence
    redacted["secret_environment"] = safe_presence
    return redacted


def _snapshot_identity_locked(
    repo_root: Path | str,
    out: Path | str,
    *,
    engine: str,
    model: str | None = None,
    effort: str | None = None,
    service_tier: str | None = None,
    environ: dict[str, str] | None = None,
) -> dict:
    """Capture all executable identity components, preserving partial evidence."""
    repo = Path(repo_root)
    output = Path(out)
    env = dict(os.environ if environ is None else environ)
    repo_info = repo.lstat()
    if stat.S_ISLNK(repo_info.st_mode) or not stat.S_ISDIR(repo_info.st_mode):
        raise ValueError("repo_root must be a real directory")
    _reject_symlink_ancestors(output)
    if output.exists():
        output_info = output.lstat()
        if stat.S_ISLNK(output_info.st_mode) or not stat.S_ISDIR(output_info.st_mode):
            raise ValueError("identity output must be a real directory")
    else:
        output.mkdir(parents=True, mode=0o700)

    components: dict[str, dict] = {}
    excluded: list[dict] = []
    git_metadata: dict[str, Any] = {}
    tracked: set[bytes] = set()
    tracked_all: set[bytes] = set()
    untracked: tuple[Path, ...] = ()
    epoch_before: dict[str, str] | None = None

    try:
        epoch_before = _repository_epoch(repo)
    except Exception as exc:
        components["repository_epoch"] = _component(
            "MISSING",
            errors=(_safe_error(exc, environ=env),),
            missing=("repository epoch",),
        )

    try:
        raw_status = bytes(
            _run(
                repo,
                ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
            )
        )
        git_metadata = {
            "head": (
                epoch_before["head"]
                if epoch_before is not None
                else _git_text(repo, "rev-parse", "HEAD")
            ),
            "branch": _git_text(repo, "branch", "--show-current"),
            "porcelain_status_sha256": sha256_bytes(raw_status),
        }
        status_safe = scan_for_secrets(raw_status, environ=env)["ok"]
        if status_safe:
            git_metadata.update(
                {
                    "porcelain_status_hex": raw_status.hex(),
                    "porcelain_status_display": raw_status.decode(
                        "utf-8", errors="backslashreplace"
                    ),
                }
            )
        else:
            git_metadata["porcelain_status_redacted"] = True
        tracked = {
            _path_bytes(path)
            for path in _git_paths(
                repo,
                "ls-files",
                "-z",
                "--",
                *(path.as_posix() for path in (*_PROMPT_DIRS, *_ROOT_SOURCES)),
            )
        }
        tracked_all = {
            _path_bytes(path) for path in _git_paths(repo, "ls-files", "-z")
        }
        untracked = _git_paths(
            repo,
            "ls-files",
            "--others",
            "--exclude-standard",
            "-z",
            "--",
            *(path.as_posix() for path in (*_SOURCE_DIRS, *_ROOT_SOURCES)),
        )
        components["git_metadata"] = _component(
            "SUCCESS" if status_safe else "PARTIAL",
            errors=() if status_safe else ("git status contains secret material",),
            missing=() if status_safe else ("raw git status",),
        )
    except Exception as exc:
        error = _safe_error(exc, environ=env)
        components["git_metadata"] = _component(
            "MISSING", errors=(error,), missing=("git metadata",)
        )

    try:
        components["submodules"] = _submodule_snapshot(repo, output, environ=env)
    except Exception as exc:
        (output / "submodules.json").unlink(missing_ok=True)
        components["submodules"] = _component(
            "MISSING",
            errors=(_safe_error(exc, environ=env),),
            missing=("submodules.json",),
        )

    try:
        patch = bytes(_run(repo, ["git", "diff", "--binary", "--no-ext-diff", "HEAD"]))
        _write_scanned(output / "code.patch", patch, environ=env)
        components["git_patch"] = _component("SUCCESS", artifacts=("code.patch",))
    except Exception as exc:
        (output / "code.patch").unlink(missing_ok=True)
        error = _safe_error(exc, environ=env)
        components["git_patch"] = _component("MISSING", errors=(error,), missing=("code.patch",))

    archive_entries: list[tuple[str, bytes, int]] = []
    archive_rejected: list[dict[str, str]] = []
    untracked_rows: list[dict[str, Any]] = []
    for relative in untracked:
        try:
            path_scan = scan_for_secrets(_path_bytes(relative), environ=env)
            if not path_scan["ok"]:
                raise SecretMaterialDetected(
                    tuple(sorted({str(row["kind"]) for row in path_scan["findings"]}))
                )
            payload, mode, source_metadata = _read_source(repo, relative)
            scan = scan_for_secrets(payload, environ=env)
            if not scan["ok"]:
                raise SecretMaterialDetected(
                    tuple(sorted({str(row["kind"]) for row in scan["findings"]}))
                )
            metadata_scan = scan_for_secrets(
                canonical_json(source_metadata).encode("utf-8"),
                environ=env,
            )
            if not metadata_scan["ok"]:
                raise SecretMaterialDetected(("source_metadata",))
            member = _archive_member(relative)
            archive_entries.append((member, payload, mode))
            untracked_rows.append(
                _path_record(relative, archive_member=member, **source_metadata)
            )
        except (
            FileNotFoundError,
            ValueError,
            OSError,
            RuntimeError,
            SecretMaterialDetected,
        ) as exc:
            archive_rejected.append(
                _path_record(relative, reason=_member_rejection_reason(exc))
            )
    rejected_raw = {bytes.fromhex(row["raw_path_hex"]) for row in archive_rejected}
    for source_root in _SOURCE_DIRS:
        _files, skipped = _walk_regular(repo, source_root)
        for row in skipped:
            raw_path = bytes.fromhex(row["raw_path_hex"])
            if raw_path in tracked_all or raw_path in rejected_raw:
                continue
            archive_rejected.append(row)
            rejected_raw.add(raw_path)
    archive_rejected.sort(key=lambda row: bytes.fromhex(row["raw_path_hex"]))
    try:
        tar_payload = _tar_bytes(archive_entries)
        compressed = zstandard.ZstdCompressor(
            level=19,
            threads=0,
            write_checksum=True,
            write_content_size=True,
        ).compress(tar_payload)
        # Each member was scanned before compression.  Compressed bytes are
        # intentionally high entropy and cannot be meaningfully probed raw.
        _atomic_write_bytes(output / "untracked_sources.tar.zst", compressed)
        rejected = tuple(f"{row['path']}: {row['reason']}" for row in archive_rejected)
        components["untracked_sources"] = _component(
            "PARTIAL" if archive_rejected else "SUCCESS",
            artifacts=("untracked_sources.tar.zst",),
            errors=rejected,
            missing=rejected,
        )
    except Exception as exc:
        (output / "untracked_sources.tar.zst").unlink(missing_ok=True)
        components["untracked_sources"] = _component(
            "MISSING",
            errors=(_safe_error(exc, environ=env),),
            missing=("untracked_sources.tar.zst",),
        )

    prompt_rows: list[dict] = []
    prompt_rejected: dict[bytes, dict[str, str]] = {}
    for prompt_root in _PROMPT_DIRS:
        files, skipped = _walk_regular(repo, prompt_root)
        excluded.extend(skipped)
        for row in skipped:
            prompt_rejected.setdefault(bytes.fromhex(row["raw_path_hex"]), row)
        for relative in files:
            destination = _prompt_destination(relative)
            try:
                path_scan = scan_for_secrets(_path_bytes(relative), environ=env)
                if not path_scan["ok"]:
                    raise SecretMaterialDetected(
                        tuple(sorted({str(row["kind"]) for row in path_scan["findings"]}))
                    )
                payload, mode, source_metadata = _read_source(repo, relative)
                metadata_scan = scan_for_secrets(
                    canonical_json(source_metadata).encode("utf-8"),
                    environ=env,
                )
                if not metadata_scan["ok"]:
                    raise SecretMaterialDetected(("source_metadata",))
                _write_scanned(output / destination, payload, mode=mode, environ=env)
                prompt_rows.append(
                    {
                        "source": _path_display(relative),
                        "raw_path_hex": _path_bytes(relative).hex(),
                        "snapshot": destination.as_posix(),
                        "classification": (
                            "TRACKED" if _path_bytes(relative) in tracked else "UNTRACKED"
                        ),
                        "sha256": sha256_bytes(payload),
                        "bytes": len(payload),
                        **source_metadata,
                    }
                )
            except Exception as exc:
                (output / destination).unlink(missing_ok=True)
                prompt_rejected.setdefault(
                    _path_bytes(relative),
                    _path_record(relative, reason=_member_rejection_reason(exc)),
                )
    prompt_rejected_rows = [prompt_rejected[key] for key in sorted(prompt_rejected)]
    prompt_errors = tuple(
        f"{row['path']}: {row['reason']}" for row in prompt_rejected_rows
    )
    prompt_artifacts = tuple(row["snapshot"] for row in prompt_rows)
    components["prompts"] = _component(
        "PARTIAL" if prompt_errors else "SUCCESS",
        artifacts=prompt_artifacts,
        errors=prompt_errors,
        missing=prompt_errors,
    )

    try:
        environment = _environment(
            repo=repo,
            engine=engine,
            model=model,
            effort=effort,
            service_tier=service_tier,
            environ=env,
        )
        environment_payload = (canonical_json(environment) + "\n").encode("utf-8")
        _write_scanned(output / "environment.json", environment_payload, environ=env)
        components["environment"] = _component("SUCCESS", artifacts=("environment.json",))
    except Exception as exc:
        (output / "environment.json").unlink(missing_ok=True)
        components["environment"] = _component(
            "MISSING",
            errors=(_safe_error(exc, environ=env),),
            missing=("environment.json",),
        )

    try:
        dependencies = str(
            _run(
                repo,
                ["uv", "pip", "freeze", "--python", sys.executable],
                text=True,
            )
        ).rstrip("\n")
        dependency_payload = (dependencies + "\n").encode("utf-8")
        _write_scanned(output / "dependencies.txt", dependency_payload, environ=env)
        components["dependencies"] = _component("SUCCESS", artifacts=("dependencies.txt",))
    except Exception as exc:
        (output / "dependencies.txt").unlink(missing_ok=True)
        components["dependencies"] = _component(
            "MISSING",
            errors=(_safe_error(exc, environ=env),),
            missing=("dependencies.txt",),
        )

    project_files: dict[str, dict] = {}
    for relative in _ROOT_SOURCES:
        try:
            payload, _mode = _read_regular(repo, relative)
            project_files[_path_display(relative)] = {
                "sha256": sha256_bytes(payload),
                "classification": (
                    "TRACKED" if _path_bytes(relative) in tracked else "UNTRACKED"
                ),
            }
        except FileNotFoundError:
            project_files[_path_display(relative)] = {
                "sha256": None,
                "classification": "MISSING",
            }
    epoch_after: dict[str, str] | None = None
    if epoch_before is not None:
        try:
            epoch_after = _repository_epoch(repo)
            if epoch_after == epoch_before:
                components["repository_epoch"] = _component("SUCCESS")
            else:
                components["repository_epoch"] = _component(
                    "PARTIAL",
                    errors=("repository changed during identity snapshot",),
                    missing=("coherent repository epoch",),
                )
        except Exception as exc:
            components["repository_epoch"] = _component(
                "MISSING",
                errors=(_safe_error(exc, environ=env),),
                missing=("repository epoch after snapshot",),
            )
    safe_archive_rejected = [
        _sanitize_path_record(row, environ=env) for row in archive_rejected
    ]
    safe_prompt_rejected = [
        _sanitize_path_record(row, environ=env) for row in prompt_rejected_rows
    ]
    safe_excluded = [_sanitize_path_record(row, environ=env) for row in excluded]
    manifest = redact_value(
        {
            "schema_version": 1,
            "git": git_metadata,
            "repository_epoch": {"before": epoch_before, "after": epoch_after},
            "untracked": [_path_display(path) for path in untracked],
            "untracked_paths": sorted(
                untracked_rows,
                key=lambda row: bytes.fromhex(row["raw_path_hex"]),
            ),
            "untracked_included": [row["path"] for row in untracked_rows],
            "untracked_rejected": safe_archive_rejected,
            "prompts": sorted(prompt_rows, key=lambda row: row["source"]),
            "prompt_rejected": safe_prompt_rejected,
            "project_files": project_files,
            "excluded": sorted(
                safe_excluded,
                key=lambda row: (row["path"], row["reason"]),
            ),
        },
        environ=env,
    ).value
    try:
        manifest_payload = (canonical_json(manifest) + "\n").encode("utf-8")
        _write_scanned(output / "source_manifest.json", manifest_payload, environ=env)
        components["source_manifest"] = _component("SUCCESS", artifacts=("source_manifest.json",))
    except Exception as exc:
        (output / "source_manifest.json").unlink(missing_ok=True)
        components["source_manifest"] = _component(
            "MISSING",
            errors=(_safe_error(exc, environ=env),),
            missing=("source_manifest.json",),
        )

    missing = sorted(
        name for name, component in components.items() if component["status"] != "SUCCESS"
    )
    errors = [
        {"component": name, "messages": component["errors"]}
        for name, component in sorted(components.items())
        if component["errors"]
    ]
    result = {
        "schema_version": 1,
        "ok": not missing,
        "redaction_rule_version": REDACTION_RULE_VERSION,
        "components": components,
        "missing": missing,
        "errors": errors,
    }
    safe_result = redact_value(result, environ=env).value
    result_payload = (canonical_json(safe_result) + "\n").encode("utf-8")
    if not scan_for_secrets(result_payload, environ=env)["ok"]:
        safe_components = {
            name: {
                "status": str(component.get("status", "MISSING")),
                "artifacts": [],
                "errors": [_REDACTED] if component.get("errors") else [],
                "missing": [_REDACTED] if component.get("missing") else [],
            }
            for name, component in components.items()
            if scan_for_secrets(name.encode("utf-8"), environ=env)["ok"]
        }
        safe_result = {
            "schema_version": 1,
            "ok": False,
            "redaction_rule_version": REDACTION_RULE_VERSION,
            "components": safe_components,
            "missing": sorted({*missing, "snapshot_result"}),
            "errors": [
                {"component": "snapshot_result", "messages": [_REDACTED]}
            ],
        }
        result_payload = (canonical_json(safe_result) + "\n").encode("utf-8")
    _write_scanned(output / "snapshot_result.json", result_payload, environ=env)
    return safe_result


def snapshot_identity(
    repo_root: Path | str,
    out: Path | str,
    *,
    engine: str,
    model: str | None = None,
    effort: str | None = None,
    service_tier: str | None = None,
    environ: dict[str, str] | None = None,
) -> dict:
    """Serialize one replacement snapshot while preserving the RunContract."""
    repo = Path(repo_root)
    output = Path(out)
    repo_info = repo.lstat()
    if stat.S_ISLNK(repo_info.st_mode) or not stat.S_ISDIR(repo_info.st_mode):
        raise ValueError("repo_root must be a real directory")
    with _snapshot_lock(output):
        if output.exists():
            info = output.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise ValueError("identity output must be a real directory")
        _clean_owned_artifacts(output)
        return _snapshot_identity_locked(
            repo,
            output,
            engine=engine,
            model=model,
            effort=effort,
            service_tier=service_tier,
            environ=environ,
        )
