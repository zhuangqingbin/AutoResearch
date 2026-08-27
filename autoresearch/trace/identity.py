"""Deterministic executable-identity snapshots with fail-closed secret handling."""

from __future__ import annotations

import io
import locale
import math
import os
import platform
import re
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import zstandard

from autoresearch.trace.atomic import canonical_json, sha256_bytes, sha256_file

REDACTION_RULE_VERSION = 1
_REDACTED = "[REDACTED]"
_SECRET_KEY_RE = re.compile(
    r"token|secret|password|authorization|cookie|api[_-]?key", re.IGNORECASE
)
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}")
_KEYLIKE_RE = re.compile(r"(?i)\b(?:sk|pk|rk|api)[-_](?:live[-_])?[A-Za-z0-9_-]{16,}\b")
_AWS_KEY_RE = re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(?:[A-Za-z0-9_-]*(?:token|secret|password|authorization|cookie)"
    r"|[A-Za-z0-9_-]*api[_-]?key)\b[\"']?\s*[:=]\s*[^\s,;{\[]{4,}"
)
_OPAQUE_RE = re.compile(rb"(?<![A-Za-z0-9+/=_-])[A-Za-z0-9+/=_-]{24,}(?![A-Za-z0-9+/=_-])")
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
        if value and len(value) >= 4 and (key in _KNOWN_SECRET_ENV or _SECRET_KEY_RE.search(key))
    }
    return tuple(sorted(values, key=lambda item: (-len(item), item)))


def redact_value(value: Any, *, environ: dict[str, str] | None = None) -> RedactionResult:
    """Recursively redact secret-bearing keys, token formats, and known env values."""
    env = dict(os.environ if environ is None else environ)
    known_values = _secret_values(env)
    hits = 0

    def redact(item: Any, *, secret_key: bool = False) -> Any:
        nonlocal hits
        if secret_key:
            hits += 1
            return _REDACTED
        if isinstance(item, dict):
            return {
                str(key): redact(
                    child,
                    secret_key=bool(_SECRET_KEY_RE.search(str(key))),
                )
                for key, child in item.items()
            }
        if isinstance(item, list):
            return [redact(child) for child in item]
        if isinstance(item, tuple):
            return tuple(redact(child) for child in item)
        if not isinstance(item, str):
            return item

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
        text = replace(_KEYLIKE_RE, text)
        text = replace(_AWS_KEY_RE, text)
        text = replace(_SECRET_ASSIGNMENT_RE, text)
        return text

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

    text = payload.decode("utf-8", errors="replace")
    char_to_byte = [0]
    total = 0
    for character in text:
        total += len(character.encode("utf-8"))
        char_to_byte.append(total)
    for kind, pattern in (
        ("bearer", _BEARER_RE),
        ("key_like", _KEYLIKE_RE),
        ("cloud_key", _AWS_KEY_RE),
        ("secret_assignment", _SECRET_ASSIGNMENT_RE),
    ):
        for match in pattern.finditer(text):
            add(kind, char_to_byte[match.start()], char_to_byte[match.end()])

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
        if _looks_like_stable_identifier(candidate):
            continue
        prefix = payload[max(0, match.start() - 32) : match.start()].lower()
        if re.search(rb"(?:sha(?:256)?|contract_hash|event_hash|prev_hash)=$", prefix):
            continue
        classes = sum(bool(pattern.search(candidate)) for pattern in _OPAQUE_CLASSES)
        if classes >= 2 and _entropy(candidate) >= 4.2:
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
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
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


def _git_paths(repo: Path, *args: str) -> tuple[str, ...]:
    raw = bytes(_run(repo, ["git", *args]))
    return tuple(
        sorted(item.decode("utf-8", errors="replace") for item in raw.split(b"\0") if item)
    )


def _walk_regular(repo: Path, relative_root: Path) -> tuple[list[Path], list[dict]]:
    root = repo / relative_root
    files: list[Path] = []
    excluded: list[dict] = []
    try:
        root_info = root.lstat()
    except FileNotFoundError:
        return files, excluded
    if stat.S_ISLNK(root_info.st_mode) or not stat.S_ISDIR(root_info.st_mode):
        excluded.append({"path": relative_root.as_posix(), "reason": "unsafe_root"})
        return files, excluded

    stack = [root]
    while stack:
        directory = stack.pop()
        with os.scandir(directory) as entries:
            ordered = sorted(entries, key=lambda entry: entry.name)
        child_dirs: list[Path] = []
        for entry in ordered:
            path = Path(entry.path)
            relative = path.relative_to(repo)
            info = entry.stat(follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                child_dirs.append(path)
            elif stat.S_ISREG(info.st_mode):
                files.append(relative)
            else:
                reason = "symlink" if stat.S_ISLNK(info.st_mode) else "special"
                excluded.append({"path": relative.as_posix(), "reason": reason})
        stack.extend(reversed(child_dirs))
    return sorted(files, key=lambda path: path.as_posix()), sorted(
        excluded, key=lambda row: (row["path"], row["reason"])
    )


def _read_regular(repo: Path, relative: Path) -> tuple[bytes, int]:
    path = repo / relative
    before = path.lstat()
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise ValueError("source is not a regular non-symlink file")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
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


def _prompt_destination(relative: Path) -> Path:
    return Path("prompts") / relative.relative_to(".claude")


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
    secret_keys.update(key for key in environ if _SECRET_KEY_RE.search(key))
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
    redacted["secret_environment"] = secret_presence
    return redacted


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
    tracked: set[str] = set()
    untracked: tuple[str, ...] = ()

    try:
        git_metadata = {
            "head": _git_text(repo, "rev-parse", "HEAD"),
            "branch": _git_text(repo, "branch", "--show-current"),
            "porcelain_status": str(
                _run(
                    repo,
                    ["git", "status", "--porcelain=v1", "--untracked-files=all"],
                    text=True,
                )
            ).rstrip("\n"),
        }
        tracked = set(
            _git_paths(
                repo,
                "ls-files",
                "-z",
                "--",
                *(path.as_posix() for path in (*_PROMPT_DIRS, *_ROOT_SOURCES)),
            )
        )
        untracked = _git_paths(
            repo,
            "ls-files",
            "--others",
            "--exclude-standard",
            "-z",
            "--",
            *(path.as_posix() for path in (*_SOURCE_DIRS, *_ROOT_SOURCES)),
        )
        components["git_metadata"] = _component("SUCCESS")
    except Exception as exc:
        error = _safe_error(exc, environ=env)
        components["git_metadata"] = _component(
            "MISSING", errors=(error,), missing=("git metadata",)
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
    archive_errors: list[str] = []
    for name in untracked:
        relative = Path(name)
        try:
            path_scan = scan_for_secrets(relative.as_posix().encode("utf-8"), environ=env)
            if not path_scan["ok"]:
                raise SecretMaterialDetected(
                    tuple(sorted({str(row["kind"]) for row in path_scan["findings"]}))
                )
            info = (repo / relative).lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                reason = "symlink" if stat.S_ISLNK(info.st_mode) else "special"
                excluded.append({"path": relative.as_posix(), "reason": reason})
                continue
            payload, mode = _read_regular(repo, relative)
            scan = scan_for_secrets(payload, environ=env)
            if not scan["ok"]:
                raise SecretMaterialDetected(
                    tuple(sorted({str(row["kind"]) for row in scan["findings"]}))
                )
            archive_entries.append((relative.as_posix(), payload, mode))
        except (FileNotFoundError, ValueError, OSError, SecretMaterialDetected) as exc:
            archive_errors.append(f"{relative.as_posix()}: {_safe_error(exc, environ=env)}")
    if archive_errors:
        components["untracked_sources"] = _component(
            "MISSING",
            errors=tuple(archive_errors),
            missing=("untracked_sources.tar.zst",),
        )
    else:
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
            components["untracked_sources"] = _component(
                "SUCCESS", artifacts=("untracked_sources.tar.zst",)
            )
        except Exception as exc:
            (output / "untracked_sources.tar.zst").unlink(missing_ok=True)
            components["untracked_sources"] = _component(
                "MISSING",
                errors=(_safe_error(exc, environ=env),),
                missing=("untracked_sources.tar.zst",),
            )

    prompt_rows: list[dict] = []
    prompt_errors: list[str] = []
    for prompt_root in _PROMPT_DIRS:
        files, skipped = _walk_regular(repo, prompt_root)
        excluded.extend(skipped)
        for relative in files:
            destination = _prompt_destination(relative)
            try:
                path_scan = scan_for_secrets(relative.as_posix().encode("utf-8"), environ=env)
                if not path_scan["ok"]:
                    raise SecretMaterialDetected(
                        tuple(sorted({str(row["kind"]) for row in path_scan["findings"]}))
                    )
                payload, mode = _read_regular(repo, relative)
                _write_scanned(output / destination, payload, mode=mode, environ=env)
                prompt_rows.append(
                    {
                        "source": relative.as_posix(),
                        "snapshot": destination.as_posix(),
                        "classification": (
                            "TRACKED" if relative.as_posix() in tracked else "UNTRACKED"
                        ),
                        "sha256": sha256_bytes(payload),
                        "bytes": len(payload),
                    }
                )
            except Exception as exc:
                (output / destination).unlink(missing_ok=True)
                prompt_errors.append(f"{relative.as_posix()}: {_safe_error(exc, environ=env)}")
    prompt_artifacts = tuple(row["snapshot"] for row in prompt_rows)
    components["prompts"] = _component(
        "PARTIAL" if prompt_errors else "SUCCESS",
        artifacts=prompt_artifacts,
        errors=tuple(prompt_errors),
        missing=tuple("prompt source" for _ in prompt_errors),
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
        path = repo / relative
        try:
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                raise ValueError("project source is not a regular non-symlink file")
            project_files[relative.as_posix()] = {
                "sha256": sha256_file(path),
                "classification": ("TRACKED" if relative.as_posix() in tracked else "UNTRACKED"),
            }
        except FileNotFoundError:
            project_files[relative.as_posix()] = {
                "sha256": None,
                "classification": "MISSING",
            }
    manifest = redact_value(
        {
            "schema_version": 1,
            "git": git_metadata,
            "untracked": list(untracked),
            "prompts": sorted(prompt_rows, key=lambda row: row["source"]),
            "project_files": project_files,
            "excluded": sorted(excluded, key=lambda row: (row["path"], row["reason"])),
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
        safe_result["errors"] = [{"component": "snapshot_result", "messages": [_REDACTED]}]
        safe_result["ok"] = False
        if "snapshot_result" not in safe_result["missing"]:
            safe_result["missing"].append("snapshot_result")
        result_payload = (canonical_json(safe_result) + "\n").encode("utf-8")
    _write_scanned(output / "snapshot_result.json", result_payload, environ=env)
    return safe_result
