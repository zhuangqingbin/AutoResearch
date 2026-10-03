"""Single-source, one-time transcript snapshot.

design: docs/superpowers/specs/2026-09-12-scene-reconstruction-transcript-
binding-design.md §5.1 ("单源一次快照")

**Why this exists**: before this module, three different consumers of one
harness transcript -- the archive step, the normalize/usage step, and the
hash step -- each independently opened and re-read the same path (see
`autoresearch.trace.capsule._archive_bound_transcripts`, pre-Task-2: it called
``adapter.normalize(ref)``, ``adapter.usage(ref)``, ``_raw_archive_bytes(source)``
and ``sha256_file(source)`` as four separate live reads of one file). A
transcript still being appended to therefore yielded three inconsistent
views, and two invocations bound to the same underlying source file each paid
for -- and archived -- their own separate copy.

``capture_snapshot`` fixes this by reading a source file's stable, complete-
JSONL-lines-only prefix exactly once into memory. Every later fact --
redacted archive bytes, adapter normalization, adapter usage, all four §3.2
hash families -- is derived in memory from that one immutable
:class:`TranscriptSnapshot`, never by touching the file again.

This module is purely a bytes/rows layer: it knows nothing about scan
stages, runs, bindings, or roles (spec §9: "trace/transcripts 负责 harness
解析与源快照"). Callers (``claude.py``/``codex.py``'s ``stats_from_rows``,
``autoresearch.trace.capsule``, ``autoresearch.trace.usage_harvest``) own
caching one snapshot per unique path and deciding what a captured
``source_changed`` diagnostic should do to a run's evidence status.
"""

from __future__ import annotations

import gzip
import io
import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from autoresearch.trace.atomic import canonical_json, sha256_bytes
from autoresearch.trace.identity import redact_residual_secrets, redact_value
from autoresearch.trace.transcripts.base import ArchiveDigest, SourcePrefixDigest

NATIVE_USAGE_ARCHIVE_VERSION = 'native-numeric-usage-v1'


def _stat(path: Path) -> os.stat_result:
    """Path-based stat -- a thin, monkeypatchable seam.

    Deliberately separate from the fd-based stat in :func:`_read_prefix` so a
    test can simulate a concurrent identity shift (a `rename()`-based replace
    landing in the window around the read) deterministically, without real
    multi-threading: fake this function's return value and the *opened fd's*
    own `os.fstat` -- untouched -- will disagree with it.
    """
    return path.stat()


def _read_prefix(path: Path) -> tuple[bytes, os.stat_result]:
    """Open once, fix the read length off the *opened* file descriptor's own
    fstat, and read exactly that many bytes.

    Fixing the length from the fd (not a separate path-based stat call)
    means a concurrent writer appending to this same path *after* we opened
    it can never change how many bytes we read: "appended while reading"
    (spec §5.1 "固定读取时的字节长度") is handled by construction, not by a
    race we have to detect and paper over. What this alone cannot rule out is
    the path denoting a *different* inode by the time we opened it (a
    `rename()`-based replace) -- that is `capture_snapshot`'s job, comparing
    this fd-bound stat against two path-based `_stat` calls taken around it.
    """
    with path.open("rb") as handle:
        fd_stat = os.fstat(handle.fileno())
        raw = handle.read(fd_stat.st_size)
    return raw, fd_stat


def _parse_prefix(
    raw: bytes,
) -> tuple[list[Mapping[str, object]], int, int | None, int, int]:
    """Parse JSONL object rows out of *raw*, judging a newline-less tail by
    whether it parses -- never by newline presence alone.

    Many fully-written, statically-saved JSONL files simply omit a final
    trailing newline (verified: this repo's own `tests/trace/test_usage_harvest.py`
    fixtures do exactly this via ``"\\n".join(...)`` with no trailing
    separator) -- that is not spec §5.1's "尾半行" (a source still being
    written that ends mid-row at read time). The two are only distinguishable
    by attempting to parse the tail: a newline-less tail that parses as one
    complete JSON object is a real row that simply had not been
    newline-terminated yet, and joins the trusted prefix; a newline-less tail
    that fails to parse (spec's own example: a value cut off mid-string) is
    the genuine half line -- excluded from rows, never counted as a "bad
    line" (it never claimed to be a complete, terminated line to begin with).

    Returns ``(rows, bad_lines, last_ordinal, cutoff_bytes, trailing_partial_bytes)``.
    """
    if raw.endswith(b"\n"):
        terminated, tail = raw, b""
    else:
        index = raw.rfind(b"\n")
        terminated, tail = (
            (raw[: index + 1], raw[index + 1 :]) if index != -1 else (b"", raw)
        )

    rows: list[Mapping[str, object]] = []
    bad_lines = 0
    last_ordinal: int | None = None

    def track_ordinal(parsed: Mapping[str, object]) -> None:
        nonlocal last_ordinal
        ordinal = parsed.get("ordinal")
        if isinstance(ordinal, int) and not isinstance(ordinal, bool):
            last_ordinal = ordinal if last_ordinal is None else max(last_ordinal, ordinal)

    for line in terminated.split(b"\n"):
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except Exception:  # noqa: BLE001 - a bad line is a fact, not a crash
            bad_lines += 1
            continue
        if not isinstance(parsed, dict):
            bad_lines += 1
            continue
        rows.append(MappingProxyType(parsed))
        track_ordinal(parsed)

    cutoff_bytes = len(terminated)
    trailing_partial_bytes = len(tail)
    if tail.strip():
        try:
            parsed_tail = json.loads(tail)
        except Exception:  # noqa: BLE001 - a genuinely truncated tail is a fact
            parsed_tail = None
        if isinstance(parsed_tail, dict):
            rows.append(MappingProxyType(parsed_tail))
            track_ordinal(parsed_tail)
            cutoff_bytes = len(raw)
            trailing_partial_bytes = 0

    return rows, bad_lines, last_ordinal, cutoff_bytes, trailing_partial_bytes


def _archive_bytes(rows: Sequence[Mapping[str, object]]) -> bytes:
    """Deterministic, redacted, gzip-compressed bytes of parsed JSONL rows.

    Same recipe `capsule._raw_archive_bytes` used before this task (value
    redaction, canonical JSON per row, byte-level residual redaction pass,
    ``mtime=0`` gzip for byte-identical re-materialization) -- moved here so
    it operates on the rows this module already parsed once, instead of a
    second, independent read+parse of the source file. The byte-level
    residual pass (fix round 1, 2026-09-13) now calls
    `trace.identity.redact_residual_secrets` -- shared with `capsule.py`'s
    own `_archive_plain_bytes` instead of each module keeping its own
    byte-identical copy of the same blanking loop.
    """
    def redact_row(row):
        redacted = redact_value(dict(row)).value
        # Exact native numeric counters are measurements, not credential tokens.
        # This archive recipe preserves no arbitrary field or nonnumeric value.
        fields = {'input_tokens', 'output_tokens', 'cached_input_tokens',
                  'cache_write_input_tokens', 'cache_read_input_tokens',
                  'cache_creation_input_tokens', 'reasoning_output_tokens', 'total_tokens'}
        paths = []
        if row.get('type') == 'event_msg' and (row.get('payload') or {}).get('type') == 'token_count':
            paths = [('payload', 'info', name) for name in ('total_token_usage', 'last_token_usage')]
        elif row.get('type') == 'assistant':
            paths = [('message', 'usage')]
        for path in paths:
            original = row
            for key in path:
                original = original.get(key) if isinstance(original, Mapping) else None
            if not isinstance(original, Mapping):
                continue
            counters = {k:v for k,v in original.items() if k in fields and type(v) is int and v >= 0}
            parent = redacted
            for key in path[:-1]:
                if not isinstance(parent.get(key), dict):
                    parent[key] = {}
                parent = parent[key]
            parent[path[-1]] = counters
        return redacted
    lines = [canonical_json(redact_row(row)) for row in rows]
    body = redact_residual_secrets(
        ("\n".join(lines) + "\n" if lines else "").encode("utf-8")
    )
    buffer = io.BytesIO()
    # mtime=0 keeps a re-materialized archive byte-identical across captures
    # of byte-identical content.
    with gzip.GzipFile(fileobj=buffer, mode="wb", mtime=0) as archive:
        archive.write(body)
    return buffer.getvalue()


@dataclass(frozen=True)
class TranscriptSnapshot:
    """One immutable, in-memory view of a harness transcript's stable prefix.

    Every later fact a caller needs -- redacted archive, per-adapter
    normalization/usage, all four §3.2 hash families -- must derive from
    ``rows``/``archive_bytes`` here, never from a fresh read of ``path``.
    """

    engine: str
    path: Path
    snapshot_id: str
    rows: tuple[Mapping[str, object], ...]
    cutoff_bytes: int
    last_ordinal: int | None
    source_prefix: SourcePrefixDigest
    archive: ArchiveDigest
    archive_bytes: bytes
    bad_lines: int
    trailing_partial_bytes: int
    source_changed: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", Path(self.path))
        object.__setattr__(self, "rows", tuple(self.rows))
        if type(self.archive_bytes) is not bytes:
            raise TypeError("TranscriptSnapshot.archive_bytes must be bytes")


def _snapshot_from_raw(
    raw: bytes, *, engine: str, path: Path, source_changed: bool
) -> TranscriptSnapshot:
    """Every fact on :class:`TranscriptSnapshot` derived, once, from already-
    in-memory *raw* bytes. Shared by :func:`capture_snapshot` (raw comes from
    one live read) and :func:`snapshot_from_archive_bytes` (raw comes from
    decompressing an already-closed gzip archive) -- one line parser, one
    archive recipe, never two (spec §9).
    """
    rows, bad_lines, last_ordinal, cutoff_bytes, trailing_partial_bytes = _parse_prefix(
        raw
    )
    trusted_prefix = raw[:cutoff_bytes]

    prefix_digest = SourcePrefixDigest(
        sha256=sha256_bytes(trusted_prefix), byte_count=cutoff_bytes
    )
    archive_bytes = _archive_bytes(rows)
    archive_digest = ArchiveDigest(
        sha256=sha256_bytes(archive_bytes), byte_count=len(archive_bytes)
    )

    return TranscriptSnapshot(
        engine=str(engine),
        path=path,
        snapshot_id=prefix_digest.sha256,
        rows=tuple(rows),
        cutoff_bytes=cutoff_bytes,
        last_ordinal=last_ordinal,
        source_prefix=prefix_digest,
        archive=archive_digest,
        archive_bytes=archive_bytes,
        bad_lines=bad_lines,
        trailing_partial_bytes=trailing_partial_bytes,
        source_changed=source_changed,
    )


def capture_snapshot(path: Path | str, *, engine: str) -> TranscriptSnapshot:
    """Read *path*'s stable, complete-JSONL-lines-only prefix exactly once.

    Fixes the byte length at read time (growth after this call is simply not
    included -- "appended while reading" is handled by construction, not
    retried). Detects a path-identity shift around the read (a `rename()`-
    based replace) as ``source_changed`` -- a truthful diagnostic, never a
    silent mixed-content retry ("无法保证同一前缀时 SOURCE_CHANGED，不混合重试
    内容"): the returned snapshot is still the single, self-consistent read
    one open file descriptor actually saw, just flagged as not provably the
    same file the caller's path-based bookkeeping expected a moment earlier.

    Every other fact on the returned :class:`TranscriptSnapshot` -- rows,
    hash families, redacted archive bytes -- is derived from that one
    in-memory prefix; nothing here reads ``path`` a second time, and nothing
    here writes an unredacted copy to disk.

    Raises ``FileNotFoundError`` for a missing path (propagated, not
    swallowed -- callers already gate on ``TranscriptRef.status`` before
    reaching here) and ``ValueError`` for a symlink source (this module's own
    defense-in-depth; existing call sites already reject symlinks earlier,
    but this function must be safe to call directly).
    """
    resolved = Path(path)
    if resolved.is_symlink():
        raise ValueError(f"transcript source is a symlink: {resolved}")
    before = _stat(resolved)
    raw, fd_stat = _read_prefix(resolved)
    after = _stat(resolved)
    source_changed = (
        fd_stat.st_ino != before.st_ino
        or fd_stat.st_dev != before.st_dev
        or after.st_ino != fd_stat.st_ino
        or after.st_dev != fd_stat.st_dev
    )
    return _snapshot_from_raw(raw, engine=engine, path=resolved, source_changed=source_changed)


def snapshot_from_archive_bytes(
    data: bytes, *, engine: str, path: Path | str
) -> TranscriptSnapshot:
    """Build a :class:`TranscriptSnapshot` from an *already-closed* gzip
    JSONL archive's bytes -- e.g. the pre-existing, pre-Task-2 retention
    mechanism's ``trace/transcripts/*.jsonl.gz`` or a ``scan.salvage`` blob
    (2026-09-12 scene-reconstruction Task 8, spec §6.1: "旧 gz 用同一 rows
    API" -- no second parsing/archiving path).

    Reuses the exact same line parser (`_parse_prefix`) and redaction/archive
    recipe (`_archive_bytes`) :func:`capture_snapshot` uses on a live read.
    There is no live file here to compare a before/after stat against --
    this content is, by definition, already fully written and closed -- so
    ``source_changed`` is always ``False``; *path* is a caller-supplied
    label only (where the archive/blob actually lives on disk), never read a
    second time by this function.

    Raises ``gzip.BadGzipFile``/``OSError`` for bytes that are not a valid
    gzip stream -- propagated, not swallowed, so a caller can record *why*
    one specific archived file could not be read rather than silently
    treating it as zero evidence (ruling 7: an unreadable/corrupt archive is
    a fact to report per item, not an empty success).
    """
    raw = gzip.decompress(data)
    return _snapshot_from_raw(raw, engine=str(engine), path=Path(path), source_changed=False)


__all__ = ["TranscriptSnapshot", "capture_snapshot", "snapshot_from_archive_bytes"]
