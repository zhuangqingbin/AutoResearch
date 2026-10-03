"""Stable, immutable transcript adapter contracts."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
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
    #: Extracted, classified tool round trips (Task 2: spec §5.1 + §3.1) --
    #: every one of these got its ``kind`` from `classify_observation`, never
    #: a second parser.  Defaulted last so existing positional/keyword
    #: `TranscriptStats(...)` construction (pre-Task-2 call sites, tests)
    #: keeps working unchanged.
    operations: tuple[ObservedOperation, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "context_tokens", tuple(self.context_tokens))
        object.__setattr__(self, "compact_pre_tokens", tuple(self.compact_pre_tokens))
        object.__setattr__(self, "tool_requests", _freeze(self.tool_requests))
        object.__setattr__(self, "tool_results", _freeze(self.tool_results))
        object.__setattr__(self, "operations", tuple(self.operations))


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


def complete_host_read(
    host_read: object, *, path: str, sha256: str, byte_count: int | None = None
) -> bool:
    """True only for a host-recorded whole-file read of exactly these bytes.

    ``host_read`` is the digest an adapter copies from the harness's own
    structured result record (Claude ``toolUseResult.file``), never from
    model-visible text. Paged (start line not 1) or line-capped
    (``num_lines != total_lines``) reads are not complete.
    """
    if not isinstance(host_read, Mapping):
        return False
    lines = [host_read.get(key) for key in ("start_line", "num_lines", "total_lines")]
    if any(type(value) is not int for value in lines):
        return False
    return (
        host_read.get("file_path") == path
        and host_read.get("content_sha256") == sha256
        and (byte_count is None or host_read.get("byte_count") == byte_count)
        and lines[0] == 1
        and lines[1] == lines[2]
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


# -------------------------------------------------------- observed operations
# (Task 2: docs/superpowers/specs/2026-09-12-scene-reconstruction-transcript-
# binding-design.md §5.1 + §3.1.  `snapshot.capture_snapshot` (new module,
# Task 2) fixes one stable rows prefix; each adapter's `stats_from_rows(rows,
# ref)` walks those *same* rows exactly once to build this tuple -- never a
# second, competing parser for "what operation was this".)

_PATH_SOURCES: frozenset[str] = frozenset({"tool_input", "unknown"})


@dataclass(frozen=True)
class ObservedOperation:
    """One classified tool round trip, bundling the five facts task-2 brief
    bullet 2 requires travel together ("保留 call_id、操作结果、部分输出标志、
    路径来源、快照引用") so a later task cannot silently drop one:

    - ``call_id``: the correlation id (:func:`tool_call_id`'s return for the
      request payload) -- ``None`` only when the raw row truly carried none.
    - ``kind``: the operation result, one of `OBSERVATION_KINDS`, always
      produced by :func:`classify_observation` -- never re-derived here. The
      partial-output flag lives *inside* this value as ``READ_PARTIAL``
      (matching spec §3.1's own table and Task 1's own §10.2 interpretation),
      not as a second, bolted-on boolean field.
    - ``path`` / ``path_source``: where a concrete file/resource path came
      from, when one is knowable from a *structured* request field --
      ``"tool_input"`` for a mapping-shaped ``input`` carrying an explicit
      path-like key, ``"unknown"`` otherwise.  Never guessed by scanning
      free-text shell/JS command strings (brief bullet 5's hard line).
    - ``item_index``: the position of this operation's request in the
      *same* ``NormalizedTranscript.items`` sequence this
      :class:`ObservedOperation` was extracted from (equal to that request
      item's own ``NormalizedItem.index``). This is **not** an index into a
      `snapshot.TranscriptSnapshot`'s ``rows`` -- a raw transcript row and a
      normalized item are different sequences of different lengths: some
      rows produce zero items (a superseded streaming update, keyed out by
      `ClaudeTranscriptAdapter`'s ``last_message_row`` dedup) and some rows
      produce several (one Claude row with a text block *and* one or more
      tool_use blocks becomes one "message" item plus N "tool_request"
      items). `snapshot.rows[item_index]` is therefore not guaranteed to be
      the row that produced this operation, and may not even be in range.
      Fix-round-1 correction (2026-09-13): an earlier revision of this
      docstring claimed ``row_index`` aliased ``snapshot.rows`` directly --
      false; both adapters set it from ``NormalizedItem.index``, an item-list
      position, not a row position. Renamed to ``item_index`` to make the
      field's actual meaning unmistakable, since it is persisted verbatim
      into ``agents/normalized/*.json`` (frozen evidence a later task -- the
      view under construction alongside this fix -- reads to trace an
      observation back to its source).
      **The "snapshot reference" spec bullet 2 asks for is not delivered by
      this field at all.** `stats_from_rows(rows, ref)`'s fixed signature
      (task-2-brief §3, used verbatim) takes plain rows, not a snapshot
      object, so nothing at this layer has a snapshot_id to attach, and
      `item_index` cannot substitute for one (see above -- it doesn't even
      alias ``rows``). A caller that needs to correlate this operation back
      to an exact snapshot/row **must reattach that correlation itself** --
      e.g. by holding the same `TranscriptSnapshot` this call's ``rows``
      came from and re-deriving which row produced which item, or by a
      future revision that threads a genuine row pointer through
      `NormalizedItem` end to end. This is not optional caller-side
      convenience; it is a real, currently-unfilled gap between what bullet
      2 asks for and what this layer alone can provide.

    ``response``/``artifact`` are optional because not every kind earns one:
    a bare ``DISCOVERED``/``*_REQUESTED`` has no result to hash yet, and an
    artifact hash is withheld whenever the source/byte-range is not provably
    exact (spec §3.2; brief bullet 6).
    """

    kind: str
    call_id: str | None
    tool_name: str
    path: str | None
    path_source: str
    item_index: int
    response: ToolResponseDigest | None = None
    artifact: ArtifactDigest | None = None

    def __post_init__(self) -> None:
        if self.kind not in OBSERVATION_KINDS:
            raise ValueError(
                f"ObservedOperation.kind must be one of OBSERVATION_KINDS, got {self.kind!r}"
            )
        if self.path_source not in _PATH_SOURCES:
            raise ValueError(
                "ObservedOperation.path_source must be one of "
                f"{sorted(_PATH_SOURCES)!r}, got {self.path_source!r}"
            )
        if type(self.item_index) is not int or self.item_index < 0:
            raise ValueError("ObservedOperation.item_index must be a non-negative integer")


# ------------------------------------------------------- extraction helpers
# One shared walker for both engines (Task 2 ruling 1: "do not re-derive
# observation kinds" -- a single `extract_operations` means there is exactly
# one place that turns NormalizedItem pairs into ObservedOperations, not one
# per adapter).  Safe to share: by the time either adapter's `normalize()`
# has run, `tool_request`/`tool_result` items already carry the same payload
# shape (`tool_name`, `input`, `is_error`, `content`) regardless of source
# engine.

#: Structured-input keys this module will read a path out of -- *only* when
#: the request's own ``input`` is a mapping (or a JSON string that decodes to
#: one) carrying one of these keys verbatim.  Never a guess from prose: a
#: tool whose ``input`` is free-text shell/JS (e.g. `exec`) never reaches
#: this helper in the first place (see `_UNCLASSIFIABLE_VALUE_ERROR` handling
#: in `extract_operations` below).
_PATH_INPUT_KEYS: tuple[str, ...] = ("file_path", "path", "notebook_path")

#: Tool names whose successful result can stand in for the artifact's exact
#: bytes -- because the *request* itself already declared the complete
#: content it wrote, not a diff.  `edit`/`edit_file`/`apply_patch` are
#: deliberately excluded (spec §3.2: "apply_patch/Edit 只有 diff 时，不声称得到
#:了完整文件 hash").
_FULL_CONTENT_WRITE_TOOL_NAMES: frozenset[str] = frozenset({"write", "write_file"})


def _decode_structured_input(payload: Mapping) -> Mapping | None:
    """Return ``input`` as a mapping, decoding one JSON-string layer only.

    Codex's harness commonly double-encodes tool arguments as a JSON string
    inside ``input`` (verified against the real rollout fixture: a
    `custom_tool_call`'s ``input`` is the literal string
    ``'{"query":"...","authorization":...}'``, not a mapping). Parsing that
    string as JSON is reading a *declared, structured* format, not
    interpreting shell/JS text -- categorically different from the refusal
    this module enforces for `bash`/`exec` command strings. A value that is
    neither a mapping nor a JSON-string-of-a-mapping yields ``None``: no
    further guessing.
    """
    value = payload.get("input")
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except Exception:  # noqa: BLE001 - not JSON is a fact, not a crash
            return None
        value = decoded
    return value if isinstance(value, Mapping) else None


def extract_structured_path(payload: Mapping) -> tuple[str | None, str]:
    """Read a concrete path off a tool request's *structured* input only.

    Returns ``(path, path_source)`` where ``path_source`` is
    ``"tool_input"`` when a known key was found, else ``(None, "unknown")``.
    Never scans free text for something that looks like a path (brief bullet
    5's hard line): a request whose ``input`` cannot be read as a mapping at
    all yields ``(None, "unknown")`` rather than a best-effort text search.
    """
    structured = _decode_structured_input(payload)
    if structured is None:
        return None, "unknown"
    for key in _PATH_INPUT_KEYS:
        value = structured.get(key)
        if isinstance(value, str) and value:
            return value, "tool_input"
    return None, "unknown"


def parse_token_count(value: object) -> int | None:
    """Parse one usage token count, refusing to fabricate a number for a
    non-numeric value (fix round 2, 2026-09-13).

    ``None``, ``0``, ``""`` and other falsy values are the ordinary "field
    absent" case and become ``0`` -- the historical ``int(x.get(key) or 0)``
    both adapters used before this fix, preserved verbatim for every
    legitimate shape. A *present*, non-numeric value is not the same as
    absent and must not silently become 0 (spec §5.1: never invent
    precision) or crash the bare ``int(...)`` call both adapters used to
    make on it.

    The concrete case this exists for: ``trace.identity.redact_value``
    blanks any dict value whose *key* looks secret-shaped
    (``_SECRET_KEY_RE`` matches ``"token"`` anywhere in a key name,
    case-insensitive) -- and every one of these usage field names contains
    it: ``input_tokens``, ``cached_input_tokens``, ``cache_write_input_tokens``,
    ``cache_creation_input_tokens``, ``output_tokens``,
    ``reasoning_output_tokens``, ``total_tokens``. A transcript that has been
    through that redaction pass (verified in practice: the salvage store
    archives redacted bytes, and a real backfill of run
    ``20260911-0912_1248`` crashed 183 times reading it back) therefore has
    every one of these *values* replaced with the literal string
    ``"[REDACTED]"``, regardless of whether the original value was ever a
    number.

    Returns ``None`` as an explicit "cannot parse" signal; callers flip the
    whole usage record to ``UNMEASURED`` rather than guess at a partial or
    zero-filled total (matching the existing ``UNMEASURED`` vocabulary
    ``CodexTranscriptAdapter`` already uses for "zero snapshots available").
    """
    if not value:
        return 0
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def artifact_digest_for_write(tool_name: str, payload: Mapping) -> ArtifactDigest | None:
    """Hash a WRITE's exact declared content, never a diff or a read-back.

    Only ``write``/``write_file`` requests carry a *complete* content field
    the tool itself declared it would persist -- treated as the artifact's
    bytes on the theory that a successful write's declared content is what
    landed on disk. ``edit``/``edit_file``/``apply_patch`` never reach a
    non-``None`` result here, matching spec §3.2's explicit "只有可核对的前镜像
    与补丁结果...才能计算完整后镜像" restriction: a diff alone never earns a
    full-file artifact hash.
    """
    if tool_name.strip().lower() not in _FULL_CONTENT_WRITE_TOOL_NAMES:
        return None
    structured = _decode_structured_input(payload)
    if structured is None:
        return None
    content = structured.get("content")
    if not isinstance(content, str):
        return None
    return hash_artifact_bytes(content.encode("utf-8"))


def _read_is_structurally_partial(payload: Mapping) -> bool:
    """Narrow, structural signal that downgrades a clean READ_SUCCEEDED to
    READ_PARTIAL (task-2-brief bullet 6: "carries line numbers, is
    paged/segmented/truncated, or mixes several files").

    Deliberately *not* a second classifier competing with
    `classify_observation`: this only ever narrows an already-SUCCEEDED READ
    down to PARTIAL, and only off a structured signal -- an explicit
    ``offset``/``limit`` on the request (a deliberate page of the file), the
    one real, reproducible pagination fact available without sniffing
    response prose. A file that silently hit an implicit line cap with
    neither key set is a known, documented gap (see the Task 2 report).
    """
    structured = _decode_structured_input(payload)
    if structured is None:
        return False
    return structured.get("offset") is not None or structured.get("limit") is not None


def extract_operations(items: Sequence[NormalizedItem]) -> tuple[ObservedOperation, ...]:
    """Walk one adapter's already-normalized items into classified operations.

    Pure and engine-agnostic: no file IO, no re-parsing of raw transcript
    rows -- every fact here comes from the ``NormalizedItem``s an adapter's
    ``normalize()``/``stats_from_rows()`` already built from one captured
    snapshot's rows. Every ``kind`` comes from :func:`classify_observation`;
    a request this module's classifier refuses (a local orchestration verb
    such as `bash`/`exec`) is skipped here, not guessed at -- its raw
    `NormalizedItem` round trip still exists in the caller's ``items``, just
    without an elevated ``ObservedOperation`` (brief bullet 5 / task-2-brief
    context: "this is base.is_external_tool's existing fail-open
    philosophy" -- the row is never hidden, only left unclassified).
    """
    results_by_call: dict[str, NormalizedItem] = {}
    for item in items:
        if item.kind != "tool_result":
            continue
        call_id = tool_call_id(item.payload)
        if call_id is not None:
            results_by_call[call_id] = item

    operations: list[ObservedOperation] = []
    for item in items:
        if item.kind != "tool_request":
            continue
        tool_name = item.payload.get("tool_name")
        if not isinstance(tool_name, str) or not tool_name.strip():
            continue
        call_id = tool_call_id(item.payload)
        result_item = results_by_call.get(call_id) if call_id is not None else None
        try:
            kind = classify_observation(item, result_item)
        except ValueError:
            # A local orchestration/execution verb `classify_observation`
            # refuses on purpose (never guessed from shell/JS text) -- not an
            # operation this layer can honestly report.
            continue
        if kind == "READ_SUCCEEDED" and _read_is_structurally_partial(item.payload):
            kind = "READ_PARTIAL"
        path, path_source = extract_structured_path(item.payload)
        response = (
            hash_tool_response(result_item.payload.get("content"))
            if result_item is not None
            else None
        )
        artifact = (
            artifact_digest_for_write(tool_name, item.payload)
            if kind == "WRITE_SUCCEEDED"
            else None
        )
        operations.append(
            ObservedOperation(
                kind=kind,
                call_id=call_id,
                tool_name=tool_name,
                path=path,
                path_source=path_source,
                item_index=item.index,
                response=response,
                artifact=artifact,
            )
        )
    return tuple(operations)
