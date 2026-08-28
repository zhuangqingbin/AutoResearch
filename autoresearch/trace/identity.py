"""Deterministic executable-identity snapshots with fail-closed secret handling."""

from __future__ import annotations

import ast
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
_URI_CREDENTIAL_RE = re.compile(r"(?i)\b[A-Za-z][A-Za-z0-9+.-]*://[^\s/@:]*:[^\s/@]+@[^\s]+")
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
_SECRET_TARGET_RE = re.compile(
    r"(?i)^(?:token|secret|password|authorization|cookie|api[_-]?key|"
    r"(?:access|auth|refresh|session|bearer|github|gitlab|openai|anthropic|tushare|"
    r"fred|slack|stripe|npm)[_-]token|[A-Za-z0-9_-]+[_-](?:password|secret|api[_-]?key))$"
)
_CONTEXT_TARGET_RE = re.compile(
    r"(?i)^(?:credential|credentials|client[_-]?secret|[A-Za-z0-9_-]*dsn|database[_-]?url|"
    r"mongo(?:db)?[_-]?(?:uri|url)|redis[_-]?(?:uri|url)|connection[_-]?string)$"
)
_FALLBACK_ASSIGNMENT_RE = re.compile(
    r"""^[ \t]*(?:[+\-](?![+\-]))?[ \t]*(?:[{,][ \t]*)?["']?"""
    r"(?P<key>[A-Za-z_][A-Za-z0-9_-]*)[\"']?[ \t]*(?P<delimiter>:|(?<![=!<>])=(?!=))"
    r"[ \t]*(?P<rhs>.+?)?[ \t]*(?:[,}]?[ \t]*)$"
)
_PLACEHOLDER_RE = re.compile(
    r"(?i)^(?:none|null|true|false|changeme|change[_-]?me|replace[_-]?me|"
    r"placeholder|redacted|\[redacted\]|your[_-][A-Za-z0-9_-]+|"
    r"<[A-Za-z0-9_.:-]+>|\$\{[A-Za-z_][A-Za-z0-9_]*\})$"
)
_OPAQUE_RE = re.compile(rb"(?<![A-Za-z0-9+/_-])[A-Za-z0-9+/_-]{24,}={0,2}(?![A-Za-z0-9+/=_-])")
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
_SNAPSHOT_SIBLING_SUFFIX_RE = re.compile(r"[a-z0-9_]{8}")
_CLEANUP_WARNING = "snapshot_cleanup_warning.json"
_TRANSACTION_JOURNAL = "snapshot_transaction.json"
_SNAPSHOT_INVENTORY = "snapshot_inventory.json"
_OWNED_FILES = frozenset(
    {
        "code.patch",
        "dependencies.txt",
        "environment.json",
        _CLEANUP_WARNING,
        _SNAPSHOT_INVENTORY,
        "snapshot_result.json",
        "source_manifest.json",
        "source_links.json",
        "submodules.json",
        "untracked_sources.tar.zst",
    }
)
_OWNED_DIRS = frozenset({"links", "prompts"})


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

        def replace_assignments(current: str) -> str:
            nonlocal hits
            ranges = _credential_assignment_ranges(current)
            for _kind, start, end in reversed(ranges):
                current = current[:start] + _REDACTED + current[end:]
            hits += len(ranges)
            return current

        text = replace(_BEARER_RE, text)
        text = replace(_SLACK_TOKEN_RE, text)
        text = replace(_URI_CREDENTIAL_RE, text)
        text = replace(_PEM_PRIVATE_KEY_RE, text)
        for pattern in _PROVIDER_TOKEN_RES:
            text = replace(pattern, text)
        text = replace(_KEYLIKE_RE, text)
        text = replace(_AWS_KEY_RE, text)
        text = replace_assignments(text)
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


def _placeholder_literal(value: str) -> bool:
    return not value or bool(_PLACEHOLDER_RE.fullmatch(value.strip()))


def _credential_kind(name: str) -> str | None:
    if _SECRET_TARGET_RE.fullmatch(name):
        return "secret_assignment"
    if _CONTEXT_TARGET_RE.fullmatch(name):
        return "contextual_credential"
    return None


def _target_names(target: ast.expr) -> tuple[str, ...]:
    if isinstance(target, ast.Name):
        return (target.id,)
    if isinstance(target, ast.Attribute):
        return (target.attr,)
    if isinstance(target, ast.Subscript) and isinstance(target.slice, ast.Constant):
        return (str(target.slice.value),)
    if isinstance(target, (ast.List, ast.Tuple)):
        return tuple(name for child in target.elts for name in _target_names(child))
    return ()


def _attribute_parts(node: ast.expr) -> tuple[str, ...]:
    if isinstance(node, ast.Name):
        return (node.id,)
    if isinstance(node, ast.Attribute):
        return (*_attribute_parts(node.value), node.attr)
    return ()


def _is_environment_lookup(node: ast.Call) -> bool:
    parts = _attribute_parts(node.func)
    return parts in {
        ("getenv",),
        ("os", "getenv"),
        ("environ", "get"),
        ("os", "environ", "get"),
    }


def _literal_value_is_secret(value: Any) -> bool:
    if value is None or isinstance(value, bool):
        return False
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="ignore")
    if isinstance(value, str):
        return not _placeholder_literal(value) and bool(re.search(r"[A-Za-z0-9]", value))
    return isinstance(value, (int, float, complex))


def _literal_node_has_secret(node: ast.AST, source: str) -> bool:
    if isinstance(node, ast.Constant):
        return _literal_value_is_secret(node.value)
    if isinstance(node, ast.JoinedStr):
        return any(
            (
                isinstance(child, ast.Constant)
                and isinstance(child.value, str)
                and _literal_value_is_secret(child.value)
            )
            or (
                isinstance(child, ast.FormattedValue)
                and _literal_node_has_secret(child.value, source)
            )
            for child in node.values
        )
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return any(_literal_node_has_secret(child, source) for child in node.elts)
    if isinstance(node, ast.Dict):
        return any(
            child is not None and _literal_node_has_secret(child, source) for child in node.values
        )
    if isinstance(node, ast.BinOp):
        if isinstance(node.op, ast.Add):
            return _literal_node_has_secret(node.left, source) or _literal_node_has_secret(
                node.right, source
            )
        segment = ast.get_source_segment(source, node) or ""
        return " " not in segment and bool(re.fullmatch(r"[A-Za-z0-9._~+/=-]+", segment))
    if isinstance(node, ast.Call):
        if not _is_environment_lookup(node):
            return False
        defaults = list(node.args[1:])
        defaults.extend(
            keyword.value
            for keyword in node.keywords
            if keyword.arg is not None and keyword.arg.lower() in {"default", "fallback"}
        )
        return any(_literal_node_has_secret(child, source) for child in defaults)
    if isinstance(node, ast.IfExp):
        return _literal_node_has_secret(node.body, source) or _literal_node_has_secret(
            node.orelse, source
        )
    if isinstance(node, ast.UnaryOp):
        return _literal_node_has_secret(node.operand, source)
    return False


def _node_span(source: str, node: ast.AST) -> tuple[int, int]:
    lines = source.splitlines(keepends=True) or [source]
    starts: list[int] = []
    cursor = 0
    for line in lines:
        starts.append(cursor)
        cursor += len(line)

    def character_column(line: str, byte_column: int) -> int:
        encoded = line.encode("utf-8")[:byte_column]
        return len(encoded.decode("utf-8", errors="ignore"))

    start_line = max(int(getattr(node, "lineno", 1)) - 1, 0)
    end_line = max(int(getattr(node, "end_lineno", start_line + 1)) - 1, start_line)
    start = starts[start_line] + character_column(lines[start_line], int(node.col_offset))
    end_column = int(getattr(node, "end_col_offset", node.col_offset + 1))
    end = starts[end_line] + character_column(lines[end_line], end_column)
    return start, max(end, start + 1)


def _python_credential_ranges(source: str) -> tuple[bool, bool, list[tuple[str, int, int]]]:
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, TypeError):
        return False, False, []
    recognized = False
    ranges: list[tuple[str, int, int]] = []
    for node in ast.walk(tree):
        targets: tuple[ast.expr, ...] = ()
        value: ast.AST | None = None
        if isinstance(node, ast.Assign):
            targets = tuple(node.targets)
            value = node.value
        elif isinstance(node, (ast.AnnAssign, ast.NamedExpr)) and node.value is not None:
            targets = (node.target,)
            value = node.value
        if targets and value is not None:
            kinds = [
                kind
                for target in targets
                for name in _target_names(target)
                if (kind := _credential_kind(name)) is not None
            ]
            if kinds:
                recognized = True
                compact_chained_literal = (
                    isinstance(node, ast.Assign)
                    and len(node.targets) > 1
                    and " " not in (ast.get_source_segment(source, node) or "")
                )
                if compact_chained_literal or _literal_node_has_secret(value, source):
                    start, end = _node_span(source, node)
                    ranges.append((kinds[0], start, end))
        if isinstance(node, ast.Dict):
            for key, child in zip(node.keys, node.values, strict=True):
                if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
                    continue
                kind = _credential_kind(key.value)
                if kind is None:
                    continue
                recognized = True
                if _literal_node_has_secret(child, source):
                    start, end = _node_span(source, node)
                    ranges.append((kind, start, end))
                    break
    return True, recognized, ranges


def _strip_inline_comment(value: str) -> str:
    quote = ""
    escaped = False
    depth = 0
    for index, char in enumerate(value):
        if escaped:
            escaped = False
            continue
        if char == "\\" and quote:
            escaped = True
            continue
        if quote:
            if char == quote:
                quote = ""
            continue
        if char in {'"', "'"}:
            quote = char
            continue
        if char in "([{":
            depth += 1
            continue
        if char in ")]}" and depth:
            depth -= 1
            continue
        if depth == 0 and char == "#":
            return value[:index].rstrip()
        if depth == 0 and value[index : index + 2] == "//":
            return value[:index].rstrip()
    return value.strip()


def _fallback_rhs_has_secret(value: str) -> bool:
    right_hand_side = _strip_inline_comment(value).rstrip(",}").strip()
    if _placeholder_literal(right_hand_side):
        return False
    try:
        expression = ast.parse(right_hand_side, mode="eval").body
    except (SyntaxError, ValueError):
        return bool(re.fullmatch(r"[A-Za-z0-9._~+/=-]+", right_hand_side))
    return _literal_node_has_secret(expression, right_hand_side)


def _merge_credential_ranges(
    ranges: list[tuple[str, int, int]],
) -> list[tuple[str, int, int]]:
    merged: list[tuple[str, int, int]] = []
    for kind, start, end in sorted(ranges, key=lambda item: (item[1], item[2])):
        if merged and start < merged[-1][2]:
            old_kind, old_start, old_end = merged[-1]
            merged[-1] = (old_kind, old_start, max(old_end, end))
        else:
            merged.append((kind, start, end))
    return merged


def _credential_assignment_ranges(text: str) -> list[tuple[str, int, int]]:
    parsed, recognized, ranges = _python_credential_ranges(text)
    if parsed and recognized:
        return _merge_credential_ranges(ranges)

    cursor = 0
    findings: list[tuple[str, int, int]] = []
    for raw_line in text.splitlines(keepends=True):
        line = raw_line.rstrip("\r\n")
        stripped = line.lstrip()
        if not stripped or stripped.startswith(("#", "//", "/*", "*", "+++", "---")):
            cursor += len(raw_line)
            continue
        prefix = len(line) - len(stripped)
        if stripped[:1] in {"+", "-"} and not stripped.startswith(("++", "--")):
            prefix += 1
            stripped = stripped[1:].lstrip()
            prefix = len(line) - len(stripped)
        line_parsed, recognized, line_ranges = _python_credential_ranges(stripped)
        if line_parsed and recognized:
            findings.extend(
                (kind, cursor + prefix + start, cursor + prefix + end)
                for kind, start, end in line_ranges
            )
            cursor += len(raw_line)
            continue
        match = _FALLBACK_ASSIGNMENT_RE.match(stripped)
        if match is not None:
            kind = _credential_kind(match.group("key"))
            right_hand_side = match.group("rhs") or ""
            if kind is not None and _fallback_rhs_has_secret(right_hand_side):
                findings.append((kind, cursor + prefix, cursor + len(line)))
        cursor += len(raw_line)
    return _merge_credential_ranges(findings)


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
    if re.fullmatch(rb"[A-Z][A-Z0-9]*(?:_[A-Z0-9]+){2,}", value):
        return True
    if re.fullmatch(rb"[a-z][a-z0-9]*(?:_[a-z0-9]+)+", value):
        return True
    if not digits and re.fullmatch(rb"(?:[A-Z][a-z]{2,}){3,}", value):
        return True
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
    ):
        for match in pattern.finditer(text):
            add(kind, match.start(), match.end())
    for pattern in _PROVIDER_TOKEN_RES:
        for match in pattern.finditer(text):
            add("provider_token", match.start(), match.end())
    for kind, start, end in _credential_assignment_ranges(text):
        add(kind, start, end)

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


def _changed_tracked_paths(repo: Path) -> tuple[tuple[bytes | None, bytes | None], ...]:
    """Return HEAD/worktree path pairs from NUL-delimited Git output."""
    fields = bytes(
        _run(
            repo,
            ["git", "diff", "--name-status", "-z", "--find-renames", "HEAD", "--"],
        )
    ).split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()
    rows: list[tuple[bytes | None, bytes | None]] = []
    cursor = 0
    while cursor < len(fields):
        status = fields[cursor]
        cursor += 1
        if not status:
            raise ValueError("invalid NUL-delimited git diff status")
        code = chr(status[0])
        if code in {"R", "C"}:
            if cursor + 1 >= len(fields):
                raise ValueError("truncated NUL-delimited git rename")
            old_path, new_path = fields[cursor], fields[cursor + 1]
            cursor += 2
            rows.append((old_path, new_path))
            continue
        if cursor >= len(fields):
            raise ValueError("truncated NUL-delimited git diff path")
        path = fields[cursor]
        cursor += 1
        rows.append((None if code == "A" else path, None if code == "D" else path))
    return tuple(rows)


def _head_blob(repo: Path, raw_path: bytes) -> bytes:
    return bytes(_run(repo, ["git", "cat-file", "blob", f"HEAD:{os.fsdecode(raw_path)}"]))


def _worktree_blob(repo: Path, raw_path: bytes) -> bytes:
    relative = Path(os.fsdecode(raw_path))
    info = (repo / relative).lstat()
    if stat.S_ISLNK(info.st_mode):
        return os.fsencode(os.readlink(repo / relative))
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("changed tracked source is not regular")
    payload, _mode = _read_regular(repo, relative)
    return payload


def _scan_changed_tracked_blobs(repo: Path, *, environ: dict[str, str]) -> None:
    """Fail closed if either side of any changed tracked blob contains credentials."""
    for old_path, new_path in _changed_tracked_paths(repo):
        for raw_path, reader in ((old_path, _head_blob), (new_path, _worktree_blob)):
            if raw_path is None:
                continue
            payload = reader(repo, raw_path)
            scan = scan_for_secrets(payload, environ=environ)
            if not scan["ok"]:
                raise SecretMaterialDetected(("tracked_blob",))


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


def _source_state_sha256(repo: Path) -> str:
    rows: list[dict[str, Any]] = []
    for source_root in _SOURCE_DIRS:
        try:
            root_info = (repo / source_root).lstat()
            rows.append(
                {
                    "path_hex": _path_bytes(source_root).hex(),
                    "kind": "root",
                    "mode": stat.S_IMODE(root_info.st_mode),
                    "type": stat.S_IFMT(root_info.st_mode),
                }
            )
        except FileNotFoundError:
            rows.append({"path_hex": _path_bytes(source_root).hex(), "kind": "missing_root"})
        files, excluded = _walk_regular(repo, source_root)
        for relative in files:
            try:
                payload, _mode, metadata = _read_source(repo, relative)
                info = (repo / relative).lstat()
                rows.append(
                    {
                        "path_hex": _path_bytes(relative).hex(),
                        "kind": "symlink" if stat.S_ISLNK(info.st_mode) else "regular",
                        "mode": (
                            metadata.get("resolved_mode")
                            if stat.S_ISLNK(info.st_mode)
                            else stat.S_IMODE(info.st_mode)
                        ),
                        "sha256": sha256_bytes(payload),
                        "link_target_hex": metadata.get("link_target_hex"),
                        "resolved_source_hex": metadata.get("resolved_source_hex"),
                    }
                )
            except Exception as exc:
                rows.append(
                    {
                        "path_hex": _path_bytes(relative).hex(),
                        "kind": "read_error",
                        "error_type": type(exc).__name__,
                    }
                )
        for row in excluded:
            raw = bytes.fromhex(row["raw_path_hex"])
            relative = Path(os.fsdecode(raw))
            entry: dict[str, Any] = {
                "path_hex": raw.hex(),
                "kind": str(row["reason"]),
            }
            try:
                info = (repo / relative).lstat()
                entry["mode"] = stat.S_IMODE(info.st_mode)
                entry["type"] = stat.S_IFMT(info.st_mode)
                if stat.S_ISLNK(info.st_mode):
                    entry["link_target_hex"] = os.fsencode(os.readlink(repo / relative)).hex()
            except OSError as exc:
                entry["error_type"] = type(exc).__name__
            rows.append(entry)
    for relative in _ROOT_SOURCES:
        try:
            payload, _mode, metadata = _read_source(repo, relative)
            info = (repo / relative).lstat()
            rows.append(
                {
                    "path_hex": _path_bytes(relative).hex(),
                    "kind": "symlink" if stat.S_ISLNK(info.st_mode) else "regular",
                    "mode": (
                        metadata.get("resolved_mode")
                        if stat.S_ISLNK(info.st_mode)
                        else stat.S_IMODE(info.st_mode)
                    ),
                    "sha256": sha256_bytes(payload),
                    "link_target_hex": metadata.get("link_target_hex"),
                    "resolved_source_hex": metadata.get("resolved_source_hex"),
                }
            )
        except FileNotFoundError:
            rows.append({"path_hex": _path_bytes(relative).hex(), "kind": "missing"})
        except Exception as exc:
            rows.append(
                {
                    "path_hex": _path_bytes(relative).hex(),
                    "kind": "read_error",
                    "error_type": type(exc).__name__,
                }
            )
    rows.sort(key=lambda row: (row["path_hex"], row["kind"]))
    return sha256_bytes(canonical_json(rows).encode("utf-8"))


def _repository_epoch(repo: Path, *, patch: bytes | None = None) -> dict[str, str]:
    head = bytes(_run(repo, ["git", "rev-parse", "HEAD"])).strip()
    status = bytes(
        _run(
            repo,
            ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        )
    )
    untracked = bytes(_run(repo, ["git", "ls-files", "--others", "--exclude-standard", "-z"]))
    patch_bytes = (
        patch
        if patch is not None
        else bytes(_run(repo, ["git", "diff", "--binary", "--no-ext-diff", "HEAD"]))
    )
    return {
        "head": head.decode("ascii"),
        "status_sha256": sha256_bytes(status),
        "untracked_sha256": sha256_bytes(untracked),
        "patch_sha256": sha256_bytes(patch_bytes),
        "source_state_sha256": _source_state_sha256(repo),
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
    for name in sorted(_OWNED_DIRS):
        directory = output / name
        try:
            info = directory.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            directory.unlink()
            continue
        shutil.rmtree(directory)


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _replace_promoted_path(source: Path, destination: Path) -> None:
    os.replace(source, destination)


def _remove_owned_path(path: Path) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode):
        shutil.rmtree(path)
    else:
        path.unlink()


def _remove_snapshot_sibling(path: Path) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode):
        shutil.rmtree(path)
    else:
        path.unlink()


def _owned_snapshot_siblings(output: Path) -> list[Path]:
    prefixes = (f".{output.name}.generation-", f".{output.name}.backup-")

    def is_owned(child: Path) -> bool:
        return any(
            child.name.startswith(prefix)
            and _SNAPSHOT_SIBLING_SUFFIX_RE.fullmatch(child.name[len(prefix) :])
            for prefix in prefixes
        )

    return sorted(
        (child for child in output.parent.iterdir() if is_owned(child)),
        key=lambda child: child.name,
    )


def _read_regular_json(path: Path) -> dict:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ValueError("identity JSON artifact must be a regular file")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("identity JSON artifact must contain an object")
    return payload


def _claimed_artifact_path(output: Path, relative: str) -> Path:
    logical = PurePosixPath(relative)
    if (
        logical.is_absolute()
        or not logical.parts
        or any(part in ("", ".", "..") for part in logical.parts)
    ):
        raise ValueError("unsafe identity artifact path")
    current = output
    for part in logical.parts:
        current = current / part
        info = current.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise ValueError("identity artifact path contains a symlink")
    if not stat.S_ISREG(current.lstat().st_mode):
        raise ValueError("claimed identity artifact is not regular")
    current.resolve(strict=True).relative_to(output.resolve(strict=True))
    return current


def _validate_manifest_hashes(output: Path) -> None:
    manifest_path = output / "source_manifest.json"
    if manifest_path.exists():
        manifest = _read_regular_json(manifest_path)
        for row in manifest.get("prompts", []):
            if not isinstance(row, dict):
                raise ValueError("invalid prompt manifest row")
            snapshot = row.get("snapshot")
            digest = row.get("sha256")
            if (
                isinstance(snapshot, str)
                and isinstance(digest, str)
                and sha256_bytes(_claimed_artifact_path(output, snapshot).read_bytes()) != digest
            ):
                raise ValueError("prompt snapshot hash mismatch")
        epoch = manifest.get("repository_epoch", {})
        before = epoch.get("before") if isinstance(epoch, dict) else None
        patch_digest = before.get("patch_sha256") if isinstance(before, dict) else None
        patch_path = output / "code.patch"
        if (
            patch_path.exists()
            and isinstance(patch_digest, str)
            and sha256_bytes(_claimed_artifact_path(output, "code.patch").read_bytes())
            != patch_digest
        ):
            raise ValueError("identity patch hash mismatch")
        archive_path = output / "untracked_sources.tar.zst"
        if archive_path.exists():
            raw_tar = zstandard.ZstdDecompressor().decompress(
                _claimed_artifact_path(output, archive_path.name).read_bytes()
            )
            with tarfile.open(fileobj=io.BytesIO(raw_tar), mode="r:") as archive:
                archive_rows = archive.getmembers()
                if any(not member.isfile() for member in archive_rows):
                    raise ValueError("untracked archive contains a non-regular member")
                members = {
                    member.name: archive.extractfile(member).read() for member in archive_rows
                }
            expected_members = {
                row["archive_member"]
                for row in manifest.get("untracked_paths", [])
                if isinstance(row, dict) and isinstance(row.get("archive_member"), str)
            }
            if set(members) != expected_members:
                raise ValueError("untracked archive membership mismatch")
            for row in manifest.get("untracked_paths", []):
                if not isinstance(row, dict):
                    raise ValueError("invalid untracked manifest row")
                member = row.get("archive_member")
                digest = row.get("sha256")
                if (
                    isinstance(member, str)
                    and isinstance(digest, str)
                    and (member not in members or sha256_bytes(members[member]) != digest)
                ):
                    raise ValueError("untracked source hash mismatch")
    links_path = output / "source_links.json"
    if links_path.exists():
        links = _read_regular_json(links_path)
        for row in links.get("source_links", []):
            if not isinstance(row, dict):
                raise ValueError("invalid source link row")
            snapshot = row.get("snapshot")
            digest = row.get("sha256")
            if (
                isinstance(snapshot, str)
                and isinstance(digest, str)
                and sha256_bytes(_claimed_artifact_path(output, snapshot).read_bytes()) != digest
            ):
                raise ValueError("source link snapshot hash mismatch")


def _load_base_snapshot_result(output: Path) -> dict:
    result = _read_regular_json(output / "snapshot_result.json")
    if result.get("schema_version") != 1 or not isinstance(result.get("components"), dict):
        raise ValueError("invalid identity snapshot result")
    if not scan_for_secrets(canonical_json(result).encode("utf-8"), environ={})["ok"]:
        raise ValueError("identity snapshot result failed safety validation")
    for component in result["components"].values():
        if not isinstance(component, dict) or not isinstance(component.get("artifacts", []), list):
            raise ValueError("invalid identity component result")
        for relative in component.get("artifacts", []):
            if not isinstance(relative, str):
                raise ValueError("invalid identity artifact claim")
            _claimed_artifact_path(output, relative)
    _validate_manifest_hashes(output)
    _validate_snapshot_inventory(output)
    return result


def _apply_cleanup_warning(result: dict, warning: dict) -> dict:
    failures = warning.get("failures")
    if warning.get("schema_version") != 1 or warning.get("status") != "PARTIAL":
        raise ValueError("invalid identity cleanup warning")
    if not scan_for_secrets(canonical_json(warning).encode("utf-8"), environ={})["ok"]:
        raise ValueError("identity cleanup warning failed safety validation")
    if not isinstance(failures, list) or any(
        not isinstance(item, dict)
        or not isinstance(item.get("phase"), str)
        or not isinstance(item.get("error_type"), str)
        for item in failures
    ):
        raise ValueError("invalid identity cleanup warning failures")
    degraded = dict(result)
    components = dict(result.get("components", {}))
    components["snapshot_cleanup"] = _component(
        "PARTIAL",
        artifacts=(_CLEANUP_WARNING,),
        errors=tuple(item["error_type"] for item in failures),
        missing=("snapshot_cleanup",),
    )
    degraded["components"] = components
    degraded["ok"] = False
    degraded["missing"] = sorted({*result.get("missing", []), "snapshot_cleanup"})
    degraded["errors"] = [
        *(
            item
            for item in result.get("errors", [])
            if not isinstance(item, dict) or item.get("component") != "snapshot_cleanup"
        ),
        {
            "component": "snapshot_cleanup",
            "messages": [item["error_type"] for item in failures],
        },
    ]
    return degraded


def load_snapshot_result(out: Path | str) -> dict:
    """Load and validate a snapshot, applying durable cleanup warnings."""
    output = Path(out)
    result = _load_base_snapshot_result(output)
    warning_path = output / _CLEANUP_WARNING
    try:
        warning = _read_regular_json(warning_path)
    except FileNotFoundError:
        return result
    return _apply_cleanup_warning(result, warning)


def _has_valid_live_snapshot(output: Path) -> bool:
    try:
        load_snapshot_result(output)
    except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return False
    return True


def _owned_names() -> tuple[str, ...]:
    return tuple(sorted({*(_OWNED_FILES - {_CLEANUP_WARNING}), *_OWNED_DIRS}))


def _append_inventory_tree(path: Path, logical: PurePosixPath, rows: list[dict[str, Any]]) -> None:
    info = path.lstat()
    common = {"path": logical.as_posix(), "mode": stat.S_IMODE(info.st_mode)}
    if stat.S_ISLNK(info.st_mode):
        raise ValueError("identity inventory cannot contain a symlink")
    if stat.S_ISREG(info.st_mode):
        rows.append({**common, "type": "file", "sha256": sha256_bytes(path.read_bytes())})
        return
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError("identity inventory cannot contain a special file")
    rows.append({**common, "type": "directory"})
    for child in sorted(path.iterdir(), key=lambda item: os.fsencode(item.name)):
        _append_inventory_tree(child, logical / child.name, rows)


def _owned_inventory(root: Path, *, include_inventory: bool) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for name in _owned_names():
        if name == _SNAPSHOT_INVENTORY and not include_inventory:
            continue
        path = root / name
        try:
            path.lstat()
        except FileNotFoundError:
            continue
        _append_inventory_tree(path, PurePosixPath(name), rows)
    rows.sort(key=lambda row: (str(row["path"]), str(row["type"])))
    return rows


def _validate_inventory_rows(rows: Any) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        raise ValueError("invalid identity snapshot inventory")
    allowed = set(_owned_names())
    validated: list[dict[str, Any]] = []
    paths: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("invalid identity snapshot inventory row")
        relative = row.get("path")
        artifact_type = row.get("type")
        mode = row.get("mode")
        if not isinstance(relative, str) or not isinstance(mode, int):
            raise ValueError("invalid identity snapshot inventory row")
        logical = PurePosixPath(relative)
        if (
            logical.is_absolute()
            or not logical.parts
            or logical.parts[0] not in allowed
            or any(part in {"", ".", ".."} for part in logical.parts)
            or relative in paths
        ):
            raise ValueError("invalid identity snapshot inventory path")
        paths.add(relative)
        if artifact_type == "file":
            digest = row.get("sha256")
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise ValueError("invalid identity snapshot inventory digest")
            if set(row) != {"path", "type", "mode", "sha256"}:
                raise ValueError("invalid identity snapshot inventory file row")
        elif artifact_type == "directory":
            if set(row) != {"path", "type", "mode"}:
                raise ValueError("invalid identity snapshot inventory directory row")
        else:
            raise ValueError("invalid identity snapshot inventory type")
        validated.append(dict(row))
    return sorted(validated, key=lambda row: (str(row["path"]), str(row["type"])))


def _write_snapshot_inventory(output: Path, *, environ: dict[str, str]) -> None:
    inventory = {
        "schema_version": 1,
        "artifacts": _owned_inventory(output, include_inventory=False),
    }
    _write_scanned(
        output / _SNAPSHOT_INVENTORY,
        (canonical_json(inventory) + "\n").encode("utf-8"),
        environ=environ,
    )


def _validate_snapshot_inventory(output: Path) -> None:
    payload = _read_regular_json(output / _SNAPSHOT_INVENTORY)
    if payload.get("schema_version") != 1:
        raise ValueError("invalid identity snapshot inventory")
    expected = _validate_inventory_rows(payload.get("artifacts"))
    actual = _owned_inventory(output, include_inventory=False)
    if actual != expected:
        raise ValueError("identity snapshot inventory mismatch")


def _inventory_matches(root: Path, expected: Any, *, include_inventory: bool = True) -> bool:
    try:
        validated = _validate_inventory_rows(expected)
        return _owned_inventory(root, include_inventory=include_inventory) == validated
    except (FileNotFoundError, OSError, ValueError, TypeError):
        return False


def _combined_old_inventory(backup: Path, output: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for name in _owned_names():
        saved = backup / name
        source = saved if saved.exists() or saved.is_symlink() else output / name
        try:
            source.lstat()
        except FileNotFoundError:
            continue
        _append_inventory_tree(source, PurePosixPath(name), rows)
    return sorted(rows, key=lambda row: (str(row["path"]), str(row["type"])))


def _write_transaction_journal(
    backup: Path,
    output: Path,
    generation: Path,
) -> dict:
    journal = {
        "schema_version": 1,
        "output_name": output.name,
        "old_artifacts": _owned_inventory(output, include_inventory=True),
        "new_artifacts": _owned_inventory(generation, include_inventory=True),
    }
    payload = (canonical_json(journal) + "\n").encode("utf-8")
    if not scan_for_secrets(payload, environ={})["ok"]:
        raise ValueError("identity transaction journal failed safety validation")
    _atomic_write_bytes(backup / _TRANSACTION_JOURNAL, payload)
    _fsync_directory(backup)
    _fsync_directory(output.parent)
    return journal


def _load_transaction_journal(backup: Path, output: Path) -> dict:
    backup_info = backup.lstat()
    if stat.S_ISLNK(backup_info.st_mode) or not stat.S_ISDIR(backup_info.st_mode):
        raise ValueError("identity transaction backup must be a real directory")
    backup.resolve(strict=True).relative_to(output.parent.resolve(strict=True))
    journal = _read_regular_json(backup / _TRANSACTION_JOURNAL)
    if (
        journal.get("schema_version") != 1
        or journal.get("output_name") != output.name
        or not isinstance(journal.get("old_artifacts"), list)
        or not isinstance(journal.get("new_artifacts"), list)
    ):
        raise ValueError("invalid identity transaction journal")
    for inventory_name in ("old_artifacts", "new_artifacts"):
        journal[inventory_name] = _validate_inventory_rows(journal[inventory_name])
    return journal


def _backup_can_restore(backup: Path, output: Path) -> bool:
    try:
        journal = _load_transaction_journal(backup, output)
        old = journal["old_artifacts"]
        if not any(row.get("path") == "snapshot_result.json" for row in old):
            return False
        if _combined_old_inventory(backup, output) != old:
            return False
    except (FileNotFoundError, OSError, ValueError, TypeError):
        return False
    return True


def _restore_snapshot_backup(backup: Path, output: Path) -> None:
    journal = _load_transaction_journal(backup, output)
    expected_names = {str(row["path"]).split("/", 1)[0] for row in journal["old_artifacts"]}
    if _combined_old_inventory(backup, output) != journal["old_artifacts"]:
        raise RuntimeError("identity rollback inventory is incomplete")
    for name in _owned_names():
        live = output / name
        saved = backup / name
        if saved.exists() or saved.is_symlink():
            _remove_owned_path(live)
            _replace_promoted_path(saved, live)
        elif name not in expected_names:
            _remove_owned_path(live)
    _fsync_directory(output)
    _fsync_directory(output.parent)
    if not _has_valid_live_snapshot(output):
        raise RuntimeError("restored identity snapshot failed validation")


def _recover_or_scavenge_snapshot_siblings(output: Path) -> None:
    siblings = _owned_snapshot_siblings(output)
    if not siblings:
        return
    backups = [child for child in siblings if child.name.startswith(f".{output.name}.backup-")]
    recovered = not backups
    for backup in backups:
        try:
            journal = _load_transaction_journal(backup, output)
        except (FileNotFoundError, OSError, ValueError):
            continue
        if _inventory_matches(output, journal["new_artifacts"]):
            recovered = True
            break
        if _inventory_matches(output, journal["old_artifacts"]):
            recovered = True
            break
        if _backup_can_restore(backup, output):
            _restore_snapshot_backup(backup, output)
            recovered = True
            break
    if not recovered:
        raise RuntimeError("identity snapshot has no valid rollback generation")
    if not _has_valid_live_snapshot(output):
        raise RuntimeError("identity snapshot recovery failed validation")
    for child in siblings:
        _remove_snapshot_sibling(child)
    _fsync_directory(output.parent)


def _cleanup_failure(phase: str, exc: BaseException) -> dict[str, str]:
    return {"phase": phase, "error_type": type(exc).__name__}


def _promote_snapshot_generation(generation: Path, output: Path) -> list[dict[str, str]]:
    output.mkdir(parents=True, mode=0o700, exist_ok=True)
    info = output.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise ValueError("identity output must be a real directory")
    backup = Path(tempfile.mkdtemp(prefix=f".{output.name}.backup-", dir=output.parent))
    owned_names = _owned_names()
    moved_old: list[tuple[Path, Path]] = []
    promoted: list[Path] = []
    committed = False
    try:
        _write_transaction_journal(backup, output, generation)
        for name in owned_names:
            destination = output / name
            old = backup / name
            if destination.exists() or destination.is_symlink():
                old.parent.mkdir(parents=True, exist_ok=True)
                _replace_promoted_path(destination, old)
                moved_old.append((old, destination))
        _fsync_directory(backup)
        _fsync_directory(output)
        for name in owned_names:
            source = generation / name
            destination = output / name
            if source.exists() or source.is_symlink():
                destination.parent.mkdir(parents=True, exist_ok=True)
                _replace_promoted_path(source, destination)
                promoted.append(destination)
        if not _has_valid_live_snapshot(output):
            raise RuntimeError("promoted identity generation failed validation")
        _fsync_directory(output.parent)
        _fsync_directory(output)
        committed = True
    except Exception:
        for destination in reversed(promoted):
            _remove_owned_path(destination)
        for old, destination in reversed(moved_old):
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(old, destination)
        _fsync_directory(output)
        _fsync_directory(output.parent)
        try:
            _remove_snapshot_sibling(backup)
            _fsync_directory(output.parent)
        except Exception:
            print("identity snapshot rollback cleanup degraded", file=sys.stderr)
        raise
    if not committed:  # pragma: no cover - defensive state assertion
        raise RuntimeError("identity generation was not committed")

    cleanup_failures: list[dict[str, str]] = []
    for phase, sibling in (("backup_removal", backup), ("generation_removal", generation)):
        try:
            _remove_snapshot_sibling(sibling)
        except Exception as exc:
            cleanup_failures.append(_cleanup_failure(phase, exc))
    try:
        _fsync_directory(output.parent)
    except Exception as exc:
        cleanup_failures.append(_cleanup_failure("cleanup_fsync", exc))
    if not cleanup_failures:
        try:
            _remove_owned_path(output / _CLEANUP_WARNING)
            _fsync_directory(output)
        except Exception as exc:
            cleanup_failures.append(_cleanup_failure("cleanup_warning_removal", exc))
    return cleanup_failures


def _record_snapshot_cleanup_degradation(
    output: Path,
    result: dict,
    failures: list[dict[str, str]],
    *,
    environ: dict[str, str],
) -> dict:
    marker = {
        "schema_version": 1,
        "status": "PARTIAL",
        "failures": failures,
        "stale_backup_count": sum(
            child.name.startswith(f".{output.name}.backup-")
            for child in _owned_snapshot_siblings(output)
        ),
        "stale_generation_count": sum(
            child.name.startswith(f".{output.name}.generation-")
            for child in _owned_snapshot_siblings(output)
        ),
    }
    marker_written = False
    try:
        _write_scanned(
            output / _CLEANUP_WARNING,
            (canonical_json(marker) + "\n").encode("utf-8"),
            environ=environ,
        )
        marker_written = True
    except Exception as exc:
        failures = [*failures, _cleanup_failure("cleanup_marker", exc)]

    degraded = _apply_cleanup_warning(result, marker) if marker_written else dict(result)
    if not marker_written:
        degraded["ok"] = False
    print("identity snapshot cleanup degraded", file=sys.stderr)
    if marker_written:
        return load_snapshot_result(output)
    return degraded


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
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
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
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
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
    exact_mode = stat.S_IMODE(resolved.lstat().st_mode)
    return (
        payload,
        mode,
        {
            "link_target": _path_display(Path(os.fsdecode(raw_target))),
            "link_target_hex": raw_target.hex(),
            "resolved_source": _path_display(resolved_relative),
            "resolved_source_hex": _path_bytes(resolved_relative).hex(),
            "resolved_sha256": sha256_bytes(payload),
            "resolved_mode": exact_mode,
        },
    )


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
            head = bytes(
                _run(repo, ["git", "-C", os.fsdecode(raw_path), "rev-parse", "HEAD"])
            ).strip()
            status = bytes(
                _run(
                    repo,
                    ["git", "-C", os.fsdecode(raw_path), "status", "--porcelain=v1", "-z"],
                )
            )
            diff = bytes(
                _run(
                    repo,
                    [
                        "git",
                        "-C",
                        os.fsdecode(raw_path),
                        "diff",
                        "--binary",
                        "--no-ext-diff",
                        "HEAD",
                    ],
                )
            )
            nested_index = bytes(
                _run(
                    repo,
                    ["git", "-C", os.fsdecode(raw_path), "ls-files", "--stage", "-z"],
                )
            )
            if not scan_for_secrets(
                status + b"\n" + diff + b"\n" + nested_index,
                environ=environ,
            )["ok"]:
                raise SecretMaterialDetected(("submodule",))
            nested_gitlinks = []
            for nested_record in sorted(item for item in nested_index.split(b"\0") if item):
                nested_metadata, nested_path = nested_record.split(b"\t", 1)
                nested_mode, nested_sha, _nested_stage = nested_metadata.split(b" ", 2)
                if nested_mode == b"160000":
                    nested_gitlinks.append(
                        {
                            "path": nested_path.decode("utf-8", errors="backslashreplace"),
                            "raw_path_hex": nested_path.hex(),
                            "gitlink_sha": nested_sha.decode("ascii"),
                        }
                    )
            checked_out_head = head.decode("ascii")
            dirty = bool(status or diff)
            head_mismatch = checked_out_head != gitlink_sha.decode("ascii")
            nested_incomplete = bool(nested_gitlinks)
            recursive_state = (
                "UNARCHIVED_DIRTY_STATE"
                if dirty
                else (
                    "HEAD_MISMATCH"
                    if head_mismatch
                    else ("UNARCHIVED_NESTED_SUBMODULES" if nested_incomplete else "CLEAN")
                )
            )
            row.update(
                {
                    "checked_out_head": checked_out_head,
                    "status": "CAPTURED" if recursive_state == "CLEAN" else "PARTIAL",
                    "status_hex": status.hex(),
                    "diff_hex": diff.hex(),
                    "recursive_state": recursive_state,
                    "nested_gitlinks": nested_gitlinks,
                }
            )
            if recursive_state != "CLEAN":
                errors.append(f"{row['path']}: {recursive_state}")
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


def _is_behavioral_path(raw_path: bytes) -> bool:
    return any(
        raw_path == _path_bytes(root) or raw_path.startswith(_path_bytes(root) + b"/")
        for root in (*_SOURCE_DIRS, *_ROOT_SOURCES)
    )


def _source_link_snapshot(repo: Path, output: Path, *, environ: dict[str, str]) -> dict:
    raw = bytes(_run(repo, ["git", "ls-files", "--stage", "-z"]))
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    link_artifacts: list[str] = []
    for record in sorted(item for item in raw.split(b"\0") if item):
        metadata, raw_path = record.split(b"\t", 1)
        mode, blob_sha, _stage = metadata.split(b" ", 2)
        if mode != b"120000" or not _is_behavioral_path(raw_path):
            continue
        relative = Path(os.fsdecode(raw_path))
        row = _path_record(relative, git_blob_sha=blob_sha.decode("ascii"))
        try:
            link_info = (repo / relative).lstat()
            if not stat.S_ISLNK(link_info.st_mode):
                raise ValueError("tracked link is missing or not a symlink")
            raw_target = os.fsencode(os.readlink(repo / relative))
            if not scan_for_secrets(raw_target, environ=environ)["ok"]:
                raise SecretMaterialDetected(("link_target",))
            row.update(
                {
                    "link_target": _path_display(Path(os.fsdecode(raw_target))),
                    "link_target_hex": raw_target.hex(),
                }
            )
            payload, resolved_mode, source_metadata = _read_source(repo, relative)
            if not scan_for_secrets(payload, environ=environ)["ok"]:
                raise SecretMaterialDetected(("link_payload",))
            if not scan_for_secrets(
                canonical_json(source_metadata).encode("utf-8"), environ=environ
            )["ok"]:
                raise SecretMaterialDetected(("link_metadata",))
            destination = Path("links") / _archive_member(relative)
            _write_scanned(
                output / destination,
                payload,
                mode=resolved_mode,
                environ=environ,
            )
            link_artifacts.append(destination.as_posix())
            row.update(
                {
                    **source_metadata,
                    "status": "CAPTURED",
                    "sha256": sha256_bytes(payload),
                    "bytes": len(payload),
                    "mode": int(source_metadata["resolved_mode"]),
                    "snapshot": destination.as_posix(),
                }
            )
        except SecretMaterialDetected:
            row = _sanitize_path_record(row, environ=environ)
            row["status"] = "SECRET_DETECTED"
            errors.append(f"{row['path']}: SECRET_DETECTED")
        except Exception:
            row["status"] = "UNSAFE_TARGET"
            errors.append(f"{row['path']}: UNSAFE_TARGET")
        rows.append(row)
    payload = {"schema_version": 1, "source_links": rows}
    _write_scanned(
        output / "source_links.json",
        (canonical_json(payload) + "\n").encode("utf-8"),
        environ=environ,
    )
    return _component(
        "PARTIAL" if errors else "SUCCESS",
        artifacts=("source_links.json", *link_artifacts),
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
    patch_before: bytes | None = None

    try:
        patch_before = bytes(_run(repo, ["git", "diff", "--binary", "--no-ext-diff", "HEAD"]))
        epoch_before = _repository_epoch(repo, patch=patch_before)
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
        tracked_all = {_path_bytes(path) for path in _git_paths(repo, "ls-files", "-z")}
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
        components["source_links"] = _source_link_snapshot(repo, output, environ=env)
    except Exception as exc:
        (output / "source_links.json").unlink(missing_ok=True)
        components["source_links"] = _component(
            "MISSING",
            errors=(_safe_error(exc, environ=env),),
            missing=("source_links.json",),
        )

    try:
        patch = (
            patch_before
            if patch_before is not None
            else bytes(_run(repo, ["git", "diff", "--binary", "--no-ext-diff", "HEAD"]))
        )
        _scan_changed_tracked_blobs(repo, environ=env)
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
            untracked_rows.append(_path_record(relative, archive_member=member, **source_metadata))
        except (
            FileNotFoundError,
            ValueError,
            OSError,
            RuntimeError,
            SecretMaterialDetected,
        ) as exc:
            archive_rejected.append(_path_record(relative, reason=_member_rejection_reason(exc)))
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
                        "mode": mode,
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
    prompt_errors = tuple(f"{row['path']}: {row['reason']}" for row in prompt_rejected_rows)
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
                "classification": ("TRACKED" if _path_bytes(relative) in tracked else "UNTRACKED"),
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
    safe_archive_rejected = [_sanitize_path_record(row, environ=env) for row in archive_rejected]
    safe_prompt_rejected = [_sanitize_path_record(row, environ=env) for row in prompt_rejected_rows]
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
            "errors": [{"component": "snapshot_result", "messages": [_REDACTED]}],
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
        try:
            _recover_or_scavenge_snapshot_siblings(output)
        except Exception as exc:
            if not _has_valid_live_snapshot(output):
                raise
            env = dict(os.environ if environ is None else environ)
            return _record_snapshot_cleanup_degradation(
                output,
                load_snapshot_result(output),
                [_cleanup_failure("stale_sibling_scavenge", exc)],
                environ=env,
            )
        generation = Path(
            tempfile.mkdtemp(
                prefix=f".{output.name}.generation-",
                dir=output.parent,
            )
        )
        try:
            result = _snapshot_identity_locked(
                repo,
                generation,
                engine=engine,
                model=model,
                effort=effort,
                service_tier=service_tier,
                environ=environ,
            )
            env = dict(os.environ if environ is None else environ)
            _write_snapshot_inventory(generation, environ=env)
            if not _has_valid_live_snapshot(generation):
                raise RuntimeError("identity generation is incomplete")
            cleanup_failures = _promote_snapshot_generation(generation, output)
            if cleanup_failures:
                env = dict(os.environ if environ is None else environ)
                return _record_snapshot_cleanup_degradation(
                    output,
                    result,
                    cleanup_failures,
                    environ=env,
                )
            return load_snapshot_result(output)
        except Exception:
            try:
                _remove_snapshot_sibling(generation)
                _fsync_directory(output.parent)
            except Exception:
                print("identity snapshot generation cleanup degraded", file=sys.stderr)
            raise
