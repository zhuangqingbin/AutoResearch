"""Stable, immutable transcript adapter contracts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from autoresearch.trace.atomic import canonical_json, sha256_bytes


class TranscriptUnreadable(RuntimeError):
    """The bound transcript cannot be read, so nothing may be inferred from it."""


# Tool names a harness executes locally.  Everything else is treated as an
# external, evidence-bearing call: an unknown tool must fail *open* into the
# lineage record rather than silently vanish from the capsule.
LOCAL_TOOL_NAMES = frozenset(
    {
        "apply_patch",
        "bash",
        "edit",
        "edit_file",
        "exec",
        "followup_task",
        "glob",
        "grep",
        "list_agents",
        "local_shell",
        "notebookedit",
        "read",
        "read_file",
        "send_message",
        "shell",
        "spawn_agent",
        "task",
        "todowrite",
        "todo_write",
        "update_plan",
        "view_image",
        "wait",
        "wait_agent",
        "write",
        "write_file",
    }
)


def is_external_tool(name: object) -> bool:
    """True when a tool call is external evidence rather than a local action."""
    if not isinstance(name, str) or not name.strip():
        return False
    return name.strip().lower() not in LOCAL_TOOL_NAMES


def tool_call_id(payload: Mapping) -> str | None:
    """Read the engine-neutral correlation id out of a normalized payload."""
    for key in ("tool_call_id", "call_id", "tool_use_id"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return None


class _FrozenDict(dict):
    def _immutable(self, *args, **kwargs):
        raise TypeError("frozen mapping does not support mutation")

    __setitem__ = _immutable
    __delitem__ = _immutable
    __ior__ = _immutable
    clear = _immutable
    pop = _immutable
    popitem = _immutable
    setdefault = _immutable
    update = _immutable


def _freeze(value):
    if isinstance(value, Mapping):
        return _FrozenDict({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


@dataclass(frozen=True)
class RunIdentity:
    """The explicit run/session facts an adapter may use for location."""

    run_id: str
    engine: str
    session_ref: str | None = None
    cwd: Path | None = None
    started_at: str | None = None

    def __post_init__(self) -> None:
        if self.cwd is not None:
            object.__setattr__(self, "cwd", Path(self.cwd))


@dataclass(frozen=True)
class TranscriptRef:
    """One explicitly identified transcript or one truthful missing-state row.

    ``start_ordinal``/``end_ordinal`` bound one role segment inside a session a
    harness reuses for several roles; both ``None`` means the whole file.
    """

    engine: str
    path: Path | None = None
    status: str = "PRESENT"
    role: str = "subagent"
    subject: str | None = None
    invocation_id: str | None = None
    session_ref: str | None = None
    start_ordinal: int | None = None
    end_ordinal: int | None = None

    def __post_init__(self) -> None:
        if self.path is not None:
            object.__setattr__(self, "path", Path(self.path))
        for field_name in ("start_ordinal", "end_ordinal"):
            value = getattr(self, field_name)
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError(f"{field_name} must be a non-negative integer or None")
        if (
            self.start_ordinal is not None
            and self.end_ordinal is not None
            and self.end_ordinal < self.start_ordinal
        ):
            raise ValueError("end_ordinal must not precede start_ordinal")


@dataclass(frozen=True)
class NormalizedItem:
    index: int
    kind: str
    payload: Mapping[str, object] = field(default_factory=dict)
    timestamp: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", _freeze(self.payload))


@dataclass(frozen=True)
class NormalizedTranscript:
    ref: TranscriptRef
    items: tuple[NormalizedItem, ...]
    status: str
    agent: str
    model: str
    effort: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "items", tuple(self.items))


@dataclass(frozen=True)
class UsageRecord:
    ref: TranscriptRef
    messages: int
    input: int
    output: int
    cache_read: int
    cache_create: int
    cache_create_1h: int
    cache_create_5m: int
    role: str
    agent: str
    effort: str
    model: str
    speed: str
    status: str
    failure_count: int
    retry_count: int
    discarded: bool
    reasoning_output: int = 0


@dataclass(frozen=True)
class TranscriptStats:
    """One immutable, adapter-owned view of a transcript's measurable facts."""

    normalized: NormalizedTranscript
    usage: UsageRecord
    started_at: str | None
    ended_at: str | None
    context_tokens: tuple[int, ...]
    first_context_tokens: int | None
    compact_pre_tokens: tuple[int, ...]
    suspected_tail: int
    tool_requests: Mapping[str, int] = field(default_factory=dict)
    tool_results: Mapping[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "context_tokens", tuple(self.context_tokens))
        object.__setattr__(self, "compact_pre_tokens", tuple(self.compact_pre_tokens))
        object.__setattr__(self, "tool_requests", _freeze(self.tool_requests))
        object.__setattr__(self, "tool_results", _freeze(self.tool_results))


@runtime_checkable
class TranscriptAdapter(Protocol):
    def locate(self, run_identity: RunIdentity) -> list[TranscriptRef]: ...

    def normalize(self, ref: TranscriptRef) -> NormalizedTranscript: ...

    def usage(self, ref: TranscriptRef) -> UsageRecord: ...


@runtime_checkable
class StatsTranscriptAdapter(TranscriptAdapter, Protocol):
    """Transcript adapter that also exposes extended metering statistics."""

    def stats(self, ref: TranscriptRef) -> TranscriptStats: ...


# =============================================================================
# Scene reconstruction (design: docs/superpowers/specs/2026-09-12-scene-
# reconstruction-transcript-binding-design.md §3.1/§3.2).  Task 1 owns this
# vocabulary and one pure classifier; later tasks call these rather than
# re-deriving observation kinds or hash conventions from raw transcript text.
# =============================================================================


# ---------------------------------------------------------------- §3.1 kinds

#: Every fact a bound tool round trip may assert about what a researcher did
#: (spec §3.1's table, verbatim).  Deliberately excludes ``NOT_OBSERVED``: that
#: is a *view's* judgment about an absence (spec §3.1 last paragraph, the
#: Task 5 chain_view render's job), never a transcript event -- a classifier
#: only ever reports something that happened in a round trip, never the
#: absence of one.
OBSERVATION_KINDS: tuple[str, ...] = (
    "DISCOVERED",
    "READ_REQUESTED",
    "READ_SUCCEEDED",
    "READ_PARTIAL",
    "READ_FAILED",
    "WRITE_REQUESTED",
    "WRITE_SUCCEEDED",
    "WRITE_FAILED",
    "SEARCH_REQUESTED",
    "SEARCH_SUCCEEDED",
    "SEARCH_FAILED",
)

#: Glob only ever enumerates paths or names; per spec it can never rise to a
#: claim of having read content, so it has exactly one state, not a
#: REQUESTED/SUCCEEDED/FAILED trio.
_DISCOVERY_TOOL_NAMES: frozenset[str] = frozenset({"glob"})

#: A grep hit proves only that its matched lines existed -- never that the
#: rest of the file was seen -- so a successful grep can rise no higher than
#: READ_PARTIAL (spec §3.1 READ_PARTIAL row: "grep/sed/分页/截断/多文件混合输出
#: 仅能证明部分内容").  There is no local ``sed`` name to classify the same way:
#: that verb only ever appears inside a `bash`/`exec` command string, and
#: reading command text to guess intent is exactly what this helper must not
#: do (see `_UNCLASSIFIED_LOCAL_TOOL_NAMES`).
_PARTIAL_READ_TOOL_NAMES: frozenset[str] = frozenset({"grep"})
_READ_TOOL_NAMES: frozenset[str] = frozenset({"read", "read_file"}) | _PARTIAL_READ_TOOL_NAMES
_WRITE_TOOL_NAMES: frozenset[str] = frozenset(
    {"write", "write_file", "edit", "edit_file", "apply_patch", "notebookedit"}
)

#: Local tools this helper refuses to classify: dispatch/bookkeeping/execution
#: verbs (`bash`, `task`, `todowrite`, `wait`, ...) whose content would have to
#: be interpreted to guess a read/write/search meaning.  Guessing is exactly
#: what `classify_observation` exists to refuse -- a caller that reaches one of
#: these is asking a question this helper has no evidence-shaped answer for.
_UNCLASSIFIED_LOCAL_TOOL_NAMES: frozenset[str] = frozenset(LOCAL_TOOL_NAMES) - (
    _DISCOVERY_TOOL_NAMES | _READ_TOOL_NAMES | _WRITE_TOOL_NAMES
)


#: `binding_status` (spec §4.5): one of the two orthogonal fields a binding
#: report row carries.  BOUND requires a unique call identity *and* a
#: verifiable correlation; UNVERIFIED_BY_PRODUCT is for a call identity that is
#: clear but whose product write is unproven (never just an external run's
#: input filename); AMBIGUOUS is for competing, unresolved evidence for the
#: same invocation; GONE/ERROR are absence and failure.  Whether a product
#: exists and whether the segment is complete are deliberately independent of
#: this -- BOUND can still be `segment_quality="partial"`.
BINDING_STATUSES: tuple[str, ...] = (
    "BOUND",
    "UNVERIFIED_BY_PRODUCT",
    "AMBIGUOUS",
    "GONE",
    "ERROR",
)

#: `segment_quality` (spec §4.5): the other orthogonal field -- how much of the
#: bound region is actually covered by identifiable evidence, independent of
#: whether the call identity itself resolved cleanly.
SEGMENT_QUALITIES: tuple[str, ...] = ("complete", "partial", "interleaved", "unknown")

#: The minimum keys a binding report's `coverage` block must carry (spec
#: §4.5: "报告 coverage 至少包含" -- a later task may add more, never fewer).
#: `accounted == expected`, and the five status-count keys
#: (`bound`/`unverified`/`ambiguous`/`gone`/`errors`) must sum to `expected`;
#: `unexpected` is never counted into that denominator.
COVERAGE_KEYS: tuple[str, ...] = (
    "expected",
    "accounted",
    "bound",
    "unverified",
    "ambiguous",
    "gone",
    "errors",
    "unexpected",
    "denominator_quality",
)


def classify_observation(
    request: NormalizedItem, result: NormalizedItem | None = None
) -> str:
    """Map one normalized tool request and its correlated result to exactly
    one ``observation.kind`` (spec §3.1).

    Pure: no file IO, no correlation lookup (the caller has already matched
    ``result`` by :func:`tool_call_id`, or passed ``None`` when nothing
    correlates), and it never executes or interprets shell/JS request text --
    a `bash`/`exec` call is refused (see `_UNCLASSIFIED_LOCAL_TOOL_NAMES`)
    rather than guessed at by scanning its command string.

    The two required behaviours this helper exists to make testable: an
    errored result can never classify as ``READ_SUCCEEDED``, and ``glob`` can
    only ever classify as ``DISCOVERED``.
    """
    if not isinstance(request, NormalizedItem) or request.kind != "tool_request":
        raise ValueError(
            f"classify_observation requires a tool_request item, got {request!r}"
        )
    if result is not None and (
        not isinstance(result, NormalizedItem) or result.kind != "tool_result"
    ):
        raise ValueError(
            f"classify_observation requires a tool_result item or None, got {result!r}"
        )
    tool_name = request.payload.get("tool_name")
    if not isinstance(tool_name, str) or not tool_name.strip():
        raise ValueError("classify_observation requires a non-blank tool_name")
    name = tool_name.strip().lower()

    if name in _DISCOVERY_TOOL_NAMES:
        return "DISCOVERED"
    if name in _UNCLASSIFIED_LOCAL_TOOL_NAMES:
        raise ValueError(
            f"classify_observation has no observation.kind for local tool {tool_name!r}"
        )
    if name in _WRITE_TOOL_NAMES:
        family = "WRITE"
    elif name in _READ_TOOL_NAMES:
        family = "READ"
    else:
        # Not a recognized local verb: the same "external, evidence-bearing"
        # bucket `is_external_tool` already fails unknown names open into
        # (module docstring above) -- never guessed as a successful read/write.
        family = "SEARCH"

    if result is None:
        return f"{family}_REQUESTED"
    if bool(result.payload.get("is_error")):
        return f"{family}_FAILED"
    if family == "READ" and name in _PARTIAL_READ_TOOL_NAMES:
        return "READ_PARTIAL"
    return f"{family}_SUCCEEDED"


# ---------------------------------------------------------------- §3.2 hashes

#: `tool_response_sha256`/`tool_response_bytes` -- a raw tool response, hashed
#: under one of two representation rules recorded in ``encoding`` (spec §3.2
#: requires the rule be recorded, not just silently applied).
@dataclass(frozen=True)
class ToolResponseDigest:
    sha256: str
    byte_count: int
    encoding: str  # "utf8_text" | "canonical_json"


#: `artifact_sha256`/`artifact_bytes` -- exact bytes of a file whose content is
#: explicitly attributed.  Never a tool-wrapped, line-numbered, or truncated
#: rendering of that content (spec §3.2): only bytes a caller can point at as
#: *the file itself*.
@dataclass(frozen=True)
class ArtifactDigest:
    sha256: str
    byte_count: int


#: `source_prefix_sha256` -- the stable read-to prefix of one raw transcript
#: source.  A value object only: computing one means reading and bounding a
#: live transcript source, which is a later task's `snapshot.capture_snapshot`
#: job, not this module's (`autoresearch/trace/transcripts/snapshot.py` does
#: not exist yet, and Task 1 does not create it).
@dataclass(frozen=True)
class SourcePrefixDigest:
    sha256: str
    byte_count: int


#: `archive_sha256` -- bytes of one redacted, archived transcript copy.  A
#: value object only, for the same reason as `SourcePrefixDigest`: building the
#: archive is a later task's job.
@dataclass(frozen=True)
class ArchiveDigest:
    sha256: str
    byte_count: int


def hash_tool_response(value: object) -> ToolResponseDigest:
    """Digest one raw tool response under the spec §3.2 representation rule.

    A ``str`` response is hashed as its UTF-8 bytes (``encoding="utf8_text"``);
    anything else (a dict, list, number, bool, or ``None``) is hashed as fixed
    canonical JSON (:func:`autoresearch.trace.atomic.canonical_json` -- sorted
    keys, compact separators; ``encoding="canonical_json"``), so semantically
    identical structured responses always agree regardless of key order.
    """
    if isinstance(value, str):
        body = value.encode("utf-8")
        encoding = "utf8_text"
    else:
        body = canonical_json(value).encode("utf-8")
        encoding = "canonical_json"
    return ToolResponseDigest(
        sha256=sha256_bytes(body), byte_count=len(body), encoding=encoding
    )


def hash_artifact_bytes(data: bytes) -> ArtifactDigest:
    """Digest exact file bytes explicitly attributed to one artifact.

    Requires ``bytes`` -- never a tool response, a `str`, or anything that
    would need canonicalizing first -- so this and :func:`hash_tool_response`
    cannot be swapped by accident: their input types alone make the two
    families incompatible at the call site, not just their output field names.
    """
    if type(data) is not bytes:
        raise TypeError(f"hash_artifact_bytes requires bytes, got {type(data)!r}")
    return ArtifactDigest(sha256=sha256_bytes(data), byte_count=len(data))


# ------------------------------------------------------------ schema version

#: The transcript schema this codebase currently writes into
#: `agents/bindings.jsonl`, `agents/normalized/*.json` and `agents/index.json`.
#: `autoresearch.trace.capsule` imports this rather than holding its own
#: literal, so the writer and `require_known_transcript_schema_version` below
#: can never silently disagree about what "current" means.
CURRENT_TRANSCRIPT_SCHEMA_VERSION = 2

#: Every transcript schema version a reader must still accept.  Derived, not
#: hand-listed: schema 2 added observation fields but never orphaned schema
#: 1's shape (spec: "旧版 normalized/index 可读"), so every version up to the
#: current one stays readable.
KNOWN_TRANSCRIPT_SCHEMA_VERSIONS: frozenset[int] = frozenset(
    range(1, CURRENT_TRANSCRIPT_SCHEMA_VERSION + 1)
)


def require_known_transcript_schema_version(payload: Mapping) -> int:
    """Read ``payload["schema_version"]``, refusing to guess at an unknown one.

    A missing or unrecognized version must never be treated as "nothing
    here" -- that is exactly the false-empty-success this capsule exists to
    prevent.  Not yet wired into a reader: a later task's `agents/index.json`
    and `agents/normalized/*.json` consumers (`transcript_binder.py`,
    `chain_view.py` -- neither exists yet) call this instead of re-deriving
    the same version check.
    """
    version = payload.get("schema_version")
    if type(version) is not int or version not in KNOWN_TRANSCRIPT_SCHEMA_VERSIONS:
        raise TranscriptUnreadable(
            f"unsupported transcript schema_version: {version!r}"
        )
    return version
