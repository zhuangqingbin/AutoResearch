"""Codex rollout transcript binding, normalization, and truthful usage.

Schema facts below were read off real ``~/.codex/sessions/**/rollout-*.jsonl``
files, not inferred from the harness docs:

- one JSONL row per event, with ``type`` in ``session_meta`` / ``turn_context`` /
  ``response_item`` / ``event_msg`` / ``world_state`` / ``compacted``;
- the model is ``turn_context.payload.model`` and the reasoning effort lives in
  ``turn_context.payload.collaboration_mode.settings.reasoning_effort``;
- ``event_msg.payload.type == "token_count"`` carries **cumulative**
  ``info.total_token_usage`` snapshots, so they must be sampled or differenced,
  never summed;
- 🚨 ``total_token_usage.input_tokens`` **includes** ``cached_input_tokens``
  (verified: ``total_tokens == input_tokens + output_tokens`` while
  ``cached_input_tokens <= input_tokens``).  Reporting the raw field as uncached
  input double-counts every cached prefix, so ``UsageRecord.input`` is
  ``input_tokens - cached_input_tokens`` — the same meaning the Claude adapter
  gives that field.
"""

from __future__ import annotations

import json
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from autoresearch.common import workspace as ws
from autoresearch.trace.identity import redact_value
from autoresearch.trace.transcripts.base import (
    NormalizedItem,
    NormalizedTranscript,
    RunIdentity,
    TranscriptRef,
    TranscriptStats,
    TranscriptUnreadable,
    UsageRecord,
    extract_operations,
    parse_token_count,
)
from autoresearch.trace.transcripts.snapshot import capture_snapshot

_SKIPPED_RESPONSE_ITEMS = frozenset(
    {"reasoning", "encrypted_reasoning"}
)
_REQUEST_ITEMS = frozenset({"custom_tool_call", "function_call"})
_RESULT_ITEMS = frozenset({"custom_tool_call_output", "function_call_output"})
_MESSAGE_ITEMS = frozenset({"message", "agent_message"})


def _iter_rows(path: Path):
    """Yield JSON objects, tolerating one truncated tail line."""
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception:  # noqa: BLE001 - a partial line must not discard the file
            continue
        if isinstance(row, dict):
            yield row


def locate_candidates(
    run_identity: RunIdentity,
    candidates: list[Path] | tuple[Path, ...],
) -> list[TranscriptRef]:
    """Turn enumerated rollout candidates into truthful, never-guessed refs.

    Zero candidates is ``GONE``; more than one is ``AMBIGUOUS`` for every row;
    exactly one is ``CANDIDATE`` and still requires an explicit
    :func:`autoresearch.trace.capsule.bind_transcript` before it counts as
    evidence.  This function never promotes a file to ``PRESENT`` and never
    breaks a tie by mtime.
    """
    paths = [Path(candidate) for candidate in candidates]
    if not paths:
        return [
            TranscriptRef(
                engine="codex",
                path=None,
                status="GONE",
                role="unbound",
                session_ref=run_identity.session_ref,
            )
        ]
    status = "CANDIDATE" if len(paths) == 1 else "AMBIGUOUS"
    return [
        TranscriptRef(
            engine="codex",
            path=path,
            status=status,
            role="unbound",
            session_ref=run_identity.session_ref,
        )
        for path in sorted(paths)
    ]


# ---------------------------------------------------------------------------
# Scene reconstruction (2026-09-12 design §4.3/§4.4, Task 3): candidate
# *discovery* across ``~/.codex/sessions/**`` (never attempted before this
# task -- `CodexTranscriptAdapter.locate` only replays *already-bound* rows
# from `bindings.jsonl`, it never walks the filesystem) and mapping a known
# wall-clock window onto one rollout's own ordinals. Both are pure discovery/
# narrowing helpers: neither one *decides* an attribution -- that is
# `scan.transcript_binder.assign`'s job (spec §4.5, ruling 2: never a
# position/time-order guess).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RolloutSearch:
    """One location pass's honest self-report.

    Spec §4.3: "显式记录搜索范围、候选数与耗时" -- a caller must be able to tell
    "genuinely absent" from "the search window was too narrow", so every
    field describing *how wide* phase 1 looked is explicit, never implied.
    """

    candidates: tuple[Path, ...]
    searched_from: str
    searched_to: str
    scanned_files: int
    elapsed_seconds: float


def _default_sessions_root() -> Path:
    return Path.home() / ".codex" / "sessions"


def _session_meta_row(path: Path) -> Mapping[str, object] | None:
    """Read just *path*'s own ``session_meta`` row (real rollouts: row 0).

    Never the full rollout -- phase 2 only needs `session_id`/`cwd` to
    narrow, and reading one row per phase-1 hit keeps discovery cheap even
    across a wide date range. A missing/malformed first row (or one that
    is not `session_meta`) yields ``None``: this file was still *scanned*
    (counted in `RolloutSearch.scanned_files`), it simply cannot be *narrowed*
    by content, an honest distinction spec §4.3 requires.
    """
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:  # noqa: BLE001 - one bad first line is a fact
                    return None
                if isinstance(row, dict) and row.get("type") == "session_meta":
                    payload = row.get("payload")
                    return payload if isinstance(payload, dict) else {}
                return None
    except OSError:
        return None
    return None


def discover_rollout_candidates(
    run_identity: RunIdentity,
    *,
    sessions_root: Path | str | None = None,
    lookback_days: int = 10,
    now: datetime | None = None,
) -> RolloutSearch:
    """Enumerate rollout files that could plausibly evidence *run_identity*.

    Spec §4.3 ("支持跨日恢复"): a session created on an earlier day and
    resumed today must be findable, so the date range searched is anchored on
    *today* (``now``), walking backward ``lookback_days`` -- never anchored
    on ``run_identity.started_at`` (a *resumed* session's rollout file was
    created on some earlier day this run's own start time says nothing
    about). ``lookback_days=10`` is a deliberate, documented judgment call --
    generous enough for a long-weekend resume without becoming an unbounded
    directory walk; flagged for the reviewer as a constant with no other
    source of truth in this codebase (no config knob, no prior convention to
    derive it from).

    Two phases, per spec: phase 1 (below) only *narrows* -- a directory/
    filename-shaped glob across ``[today - lookback_days, today]`` for
    ``rollout-*.jsonl``. Phase 2 reads each phase-1 hit's own
    :func:`_session_meta_row` and keeps a file only when its `session_id`
    matches ``run_identity.session_ref`` (the priority order spec §4.3
    states: "session_ref 优先"); without a `session_ref`, narrows instead by
    `cwd` (when known). Neither phase promotes a result to certainty by
    position, count, or mtime -- an unnarrowed multi-candidate result (no
    `session_ref` and no `cwd` to check) is returned as-is; what that means
    for attribution is `assign()`'s decision, never this function's.
    """
    started = time.monotonic()
    root = Path(sessions_root) if sessions_root is not None else _default_sessions_root()
    anchor = now.date() if now is not None else datetime.now(timezone.utc).date()
    start_date = anchor - timedelta(days=max(int(lookback_days), 0))

    scanned: list[Path] = []
    cursor = start_date
    while cursor <= anchor:
        day_dir = root / f"{cursor.year:04d}" / f"{cursor.month:02d}" / f"{cursor.day:02d}"
        if day_dir.is_dir():
            scanned.extend(sorted(day_dir.glob("rollout-*.jsonl")))
        cursor += timedelta(days=1)

    session_ref = run_identity.session_ref
    cwd = str(run_identity.cwd) if run_identity.cwd is not None else None
    decided: list[Path] = []
    for path in scanned:
        meta = _session_meta_row(path)
        if meta is None:
            continue
        if session_ref is not None:
            if str(meta.get("session_id") or "") == session_ref:
                decided.append(path)
            continue
        if cwd is not None:
            if str(meta.get("cwd") or "") == cwd:
                decided.append(path)
            continue
        decided.append(path)

    elapsed = time.monotonic() - started
    return RolloutSearch(
        candidates=tuple(sorted(decided)),
        searched_from=start_date.isoformat(),
        searched_to=anchor.isoformat(),
        scanned_files=len(scanned),
        elapsed_seconds=elapsed,
    )


def ordinal_window_for_timestamps(
    rows: Sequence[Mapping[str, object]],
    *,
    start_ts: str | None,
    end_ts: str | None,
) -> tuple[int | None, int | None, str]:
    """Map an authoritative wall-clock window onto *this* rollout's own ordinals.

    Spec §4.4 ("区段"): both edges of the window must come from a genuine
    per-attempt fact (a TASK/AGENT event's own timestamp) supplied by the
    caller -- this function never sorts *rows* by time and hands out
    positions (ruling 2); it only answers "given that I already know this
    attempt's [start, end], which of *this file's own* rows fall inside it".
    Which file/attempt a candidate belongs to is never decided here.

    Returns ``(start_ordinal, end_ordinal, quality)``:

    - ``quality="complete"`` -- both edges land on a real row's ordinal.
    - ``quality="partial"`` -- only one edge does (the window's other end is
      open, e.g. a claimed-but-not-yet-terminal attempt).
    - ``quality="unknown"`` -- neither timestamp was supplied, no row in
      *rows* carries a parseable ``timestamp``/``ordinal`` pair, or the
      resolved window would end before it starts (an inconsistent claim,
      never guessed away).
    """
    if not start_ts and not end_ts:
        return None, None, "unknown"

    def _parse(value: str) -> datetime | None:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            return None

    start_dt = _parse(start_ts) if start_ts else None
    end_dt = _parse(end_ts) if end_ts else None

    ordinals: list[tuple[datetime, int]] = []
    for row in rows:
        raw_ts = row.get("timestamp")
        ordinal = row.get("ordinal")
        if not isinstance(raw_ts, str) or type(ordinal) is not int:
            continue
        parsed = _parse(raw_ts)
        if parsed is None:
            continue
        ordinals.append((parsed, ordinal))
    if not ordinals:
        return None, None, "unknown"
    ordinals.sort(key=lambda item: item[0])

    start_ordinal: int | None = None
    if start_dt is not None:
        for parsed, ordinal in ordinals:
            if parsed >= start_dt:
                start_ordinal = ordinal
                break
    end_ordinal: int | None = None
    if end_dt is not None:
        for parsed, ordinal in reversed(ordinals):
            if parsed <= end_dt:
                end_ordinal = ordinal
                break

    if start_ordinal is not None and end_ordinal is not None:
        if end_ordinal < start_ordinal:
            return None, None, "unknown"
        return start_ordinal, end_ordinal, "complete"
    if start_ordinal is not None or end_ordinal is not None:
        return start_ordinal, end_ordinal, "partial"
    return None, None, "unknown"


class CodexTranscriptAdapter:
    """Adapter for Codex rollout JSONL bound explicitly to run invocations."""

    engine = "codex"

    def __init__(self, bindings_path: Path | str | None = None):
        self._bindings_path = None if bindings_path is None else Path(bindings_path)

    # -- location ---------------------------------------------------------

    def bindings_path(self, run_id: str) -> Path:
        if self._bindings_path is not None:
            return self._bindings_path
        root = ws.find_run_root(run_id) or ws.scan_run_root(run_id)
        return root / "capsule" / "agents" / "bindings.jsonl"

    def locate(self, run_identity: RunIdentity) -> list[TranscriptRef]:
        """Return one ref per explicit binding; bindings are the only authority."""
        if run_identity.engine != "codex":
            return []
        path = self.bindings_path(run_identity.run_id)
        if not path.is_file():
            return []
        refs: list[TranscriptRef] = []
        for row in _iter_rows(path):
            source = Path(str(row.get("path")))
            present = source.is_file()
            refs.append(
                TranscriptRef(
                    engine="codex",
                    path=source if present else None,
                    status="PRESENT" if present else "GONE",
                    role=str(row.get("role") or "subagent"),
                    subject=row.get("subject"),
                    invocation_id=row.get("invocation_id"),
                    session_ref=row.get("session_ref"),
                    start_ordinal=row.get("start_ordinal"),
                    end_ordinal=row.get("end_ordinal"),
                )
            )
        return refs

    # -- shared parsing ---------------------------------------------------

    @staticmethod
    def _validate_engine(ref: TranscriptRef, action: str) -> None:
        if ref.engine != "codex":
            raise ValueError(f"Codex adapter cannot {action} engine {ref.engine!r}")

    @staticmethod
    def _validate_ref(ref: TranscriptRef, action: str) -> Path:
        """Validate *ref* and return its path -- never reads the file."""
        CodexTranscriptAdapter._validate_engine(ref, action)
        if ref.path is None or ref.status != "PRESENT":
            raise TranscriptUnreadable("Codex transcript is not PRESENT")
        path = Path(ref.path)
        if not path.is_file():
            raise TranscriptUnreadable(f"Codex transcript is gone: {path}")
        return path

    @staticmethod
    def _ordinal(row: dict) -> int | None:
        value = row.get("ordinal")
        return value if isinstance(value, int) else None

    def _segment(self, rows: list[dict], ref: TranscriptRef) -> list[dict]:
        """Restrict rows to the bound role segment, when one was declared."""
        start, end = ref.start_ordinal, ref.end_ordinal
        if start is None and end is None:
            return rows
        selected = []
        for row in rows:
            ordinal = self._ordinal(row)
            if ordinal is None:
                continue
            if start is not None and ordinal < start:
                continue
            if end is not None and ordinal > end:
                continue
            selected.append(row)
        return selected

    @staticmethod
    def _payload(row: dict) -> dict:
        payload = row.get("payload")
        return payload if isinstance(payload, dict) else {}

    def _context(self, rows: list[dict]) -> tuple[str, str]:
        model = effort = None
        for row in rows:
            if row.get("type") != "turn_context":
                continue
            payload = self._payload(row)
            candidate_model = payload.get("model")
            if candidate_model:
                model = str(candidate_model)
            settings = (payload.get("collaboration_mode") or {}).get("settings") or {}
            candidate_effort = (
                settings.get("reasoning_effort")
                or payload.get("reasoning_effort")
                or payload.get("effort")
            )
            if candidate_effort:
                effort = str(candidate_effort)
        return model or "—", effort or "—"

    def _snapshots(self, rows: list[dict]) -> list[dict]:
        snapshots = []
        for row in rows:
            payload = self._payload(row)
            if row.get("type") != "event_msg" or payload.get("type") != "token_count":
                continue
            info = payload.get("info")
            total = (info or {}).get("total_token_usage")
            if isinstance(total, dict):
                snapshots.append(total)
        return snapshots

    def _lifecycle(self, rows: list[dict]) -> dict:
        failures: list[int] = []
        terminals: list[int] = []
        for index, row in enumerate(rows):
            payload = self._payload(row)
            if row.get("type") != "event_msg":
                continue
            kind = payload.get("type")
            if kind == "task_complete":
                if payload.get("error"):
                    failures.append(index)
                else:
                    terminals.append(index)
            elif kind in {"error", "stream_error"}:
                failures.append(index)
        failure_count = len(failures)
        retried = bool(terminals and (not failures or terminals[-1] > failures[-1]))
        if failure_count and retried:
            status = "RETRIED_SUCCEEDED"
        elif failure_count:
            status = "FAILED"
        elif terminals:
            status = "SUCCEEDED"
        else:
            status = "INCOMPLETE"
        return {
            "status": status,
            "failure_count": failure_count,
            "retry_count": failure_count if retried else max(failure_count - 1, 0),
        }

    @staticmethod
    def _safe(value: object) -> object:
        return redact_value(value).value

    @staticmethod
    def _text(content: object) -> str:
        if isinstance(content, str):
            return content
        if not isinstance(content, list):
            return ""
        parts = [
            str(block.get("text"))
            for block in content
            if isinstance(block, dict) and block.get("text") is not None
        ]
        return "\n".join(parts)

    @staticmethod
    def _timestamp_bounds(rows: list[dict]) -> tuple[str | None, str | None]:
        """Chronological bounds from timezone-aware ISO timestamps.

        Same discipline as `ClaudeTranscriptAdapter._timestamp_bounds`:
        missing, malformed, or timezone-naive values cannot establish a
        reliable absolute event time and are excluded. Every real Codex row
        carries a top-level ``timestamp`` (verified against the packaged
        rollout fixture), so this is expected to succeed in practice, not
        merely defensive.
        """
        valid: list[tuple[datetime, int, str]] = []
        for index, row in enumerate(rows):
            raw = row.get("timestamp")
            if not isinstance(raw, str) or not raw:
                continue
            try:
                parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                continue
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                continue
            valid.append((parsed, index, raw))
        if not valid:
            return None, None
        return (
            min(valid, key=lambda item: (item[0], item[1]))[2],
            max(valid, key=lambda item: (item[0], item[1]))[2],
        )

    @staticmethod
    def _content_chars(content: object) -> int:
        if content is None:
            return 0
        if isinstance(content, str):
            return len(content)
        return len(
            json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        )

    @staticmethod
    def _tool_tallies(
        items: tuple[NormalizedItem, ...],
    ) -> tuple[Counter[str], Counter[str]]:
        """Count tool_request occurrences by name, and tool_result content
        size tallied under the *requesting* tool's name -- correlated via
        `tool_call_id`, mirroring `ClaudeTranscriptAdapter`'s convention."""
        from autoresearch.trace.transcripts.base import tool_call_id

        names_by_call: dict[str, str] = {}
        tool_requests: Counter[str] = Counter()
        for item in items:
            if item.kind != "tool_request":
                continue
            name = item.payload.get("tool_name")
            if not isinstance(name, str) or not name:
                continue
            tool_requests[name] += 1
            call_id = tool_call_id(item.payload)
            if call_id is not None:
                names_by_call[call_id] = name
        tool_results: Counter[str] = Counter()
        for item in items:
            if item.kind != "tool_result":
                continue
            call_id = tool_call_id(item.payload)
            name = names_by_call.get(call_id) if call_id is not None else None
            if name:
                tool_results[name] += CodexTranscriptAdapter._content_chars(
                    item.payload.get("content")
                )
        return tool_requests, tool_results

    @staticmethod
    def _suspected_tail(items: tuple[NormalizedItem, ...]) -> int:
        """Trailing agent/assistant message items after the last user
        message -- the same "possibly wasted tail" diagnostic
        `ClaudeTranscriptAdapter._is_user_turn`-based logic computes,
        expressed over Codex's already-normalized ``message`` items."""
        messages = [item for item in items if item.kind == "message"]
        user_indices = [
            item.index for item in messages if item.payload.get("role") == "user"
        ]
        if not user_indices:
            return 0
        last_user = max(user_indices)
        after = sum(1 for item in messages if item.index > last_user)
        return max(after - 1, 0)

    # -- protocol ---------------------------------------------------------

    def stats(self, ref: TranscriptRef) -> TranscriptStats:
        """Capture one snapshot of ``ref.path``, then delegate to :meth:`stats_from_rows`.

        The file-level entry point (task-2-brief §3): reads the source
        exactly once via `snapshot.capture_snapshot`, never via a second,
        independent parse -- everything `normalize()`/`usage()` used to
        compute by separately re-reading the file now comes from one shared
        rows-level pass below.
        """
        path = self._validate_ref(ref, "inspect")
        snapshot = capture_snapshot(path, engine="codex")
        return self.stats_from_rows(snapshot.rows, ref)

    def stats_from_rows(
        self, rows: Sequence[Mapping[str, object]], ref: TranscriptRef
    ) -> TranscriptStats:
        """Build normalization, usage, and diagnostics from already-read rows.

        The rows-level entry point (task-2-brief §3): ``rows`` is the
        *whole*, unsegmented captured snapshot -- a Codex session file is
        routinely shared by several role segments (spec §5.1's "两个
        invocation 共享源"). This restricts to ``ref``'s own
        ``[start_ordinal, end_ordinal]`` window for normalize/diagnostics
        while keeping the full rows available for usage's preceding-snapshot
        baseline lookup -- the same two-views split `usage()` used before
        this task, now computed once per call instead of by two independent,
        each-re-reading-the-file calls.
        """
        self._validate_engine(ref, "inspect")
        all_rows = list(rows)
        segment = self._segment(all_rows, ref)
        model, effort = self._context(segment)
        lifecycle = self._lifecycle(segment)

        normalized = self._normalize_segment(segment, ref, model, effort, lifecycle)
        usage_record = self._usage_from_rows(all_rows, segment, ref, model, effort, lifecycle)
        started_at, ended_at = self._timestamp_bounds(segment)
        # fix round 2: a redacted (e.g. "[REDACTED]") input_tokens value must
        # not crash context_tokens -- omit that sample from the series
        # rather than fabricate or crash (see `parse_token_count`).
        context_tokens = tuple(
            value
            for value in (
                parse_token_count(snapshot.get("input_tokens"))
                for snapshot in self._snapshots(segment)
            )
            if value is not None
        )
        tool_requests, tool_results = self._tool_tallies(normalized.items)

        return TranscriptStats(
            normalized=normalized,
            usage=usage_record,
            started_at=started_at,
            ended_at=ended_at,
            context_tokens=context_tokens,
            first_context_tokens=context_tokens[0] if context_tokens else None,
            # No evidence of a Codex compaction-boundary event shape
            # analogous to Claude's `compact_boundary`/`compactMetadata` --
            # left empty rather than guessed (documented gap, Task 2 report).
            compact_pre_tokens=(),
            suspected_tail=self._suspected_tail(normalized.items),
            tool_requests=tool_requests,
            tool_results=tool_results,
            operations=extract_operations(normalized.items),
        )

    def _normalize_segment(
        self,
        rows: list[dict],
        ref: TranscriptRef,
        model: str,
        effort: str,
        lifecycle: dict,
    ) -> NormalizedTranscript:
        """Keep visible messages, tool traffic and errors; drop model internals."""
        items: list[NormalizedItem] = []

        def add(kind: str, payload: dict, timestamp: object) -> None:
            safe = self._safe(payload)
            items.append(
                NormalizedItem(
                    index=len(items),
                    kind=kind,
                    payload=safe if isinstance(safe, dict) else {},
                    timestamp=timestamp if isinstance(timestamp, str) else None,
                )
            )

        for row in rows:
            payload = self._payload(row)
            timestamp = row.get("timestamp")
            kind = payload.get("type")
            if row.get("type") == "event_msg":
                if kind == "task_complete" and payload.get("error"):
                    error = payload.get("error")
                    add(
                        "error",
                        {
                            "turn_id": payload.get("turn_id"),
                            "error": error if isinstance(error, dict) else {"message": error},
                        },
                        timestamp,
                    )
                elif kind in {"error", "stream_error"}:
                    add("error", {"error": payload.get("message") or payload}, timestamp)
                elif kind == "web_search_end":
                    # Sibling of `web_search_call` below: the raw row carries no
                    # `name` field (there is nothing to search for it in), so the
                    # normalized `tool_name` is the literal "web_search" — matching
                    # `web_budget._SEARCH_NAMES` and what `_external_tool_rows`
                    # (`payload.get("tool_name")`) actually reads.  `content` (not
                    # the brief's literal "output") mirrors `_RESULT_ITEMS` above so
                    # the same consumer can hash/blob it.
                    add(
                        "tool_result",
                        {
                            "tool_call_id": payload.get("call_id"),
                            "content": {
                                "query": payload.get("query"),
                                "results": payload.get("results"),
                            },
                        },
                        timestamp,
                    )
                continue
            if row.get("type") != "response_item" or kind in _SKIPPED_RESPONSE_ITEMS:
                continue
            if kind in _MESSAGE_ITEMS:
                text = self._text(payload.get("content"))
                if not text:
                    continue
                add(
                    "message",
                    {
                        "message_id": payload.get("id"),
                        "role": payload.get("role")
                        or ("agent" if kind == "agent_message" else "assistant"),
                        "text": text,
                    },
                    timestamp,
                )
            elif kind == "web_search_call":
                # Codex's hosted web_search has no dedicated `name` field in the
                # raw row (unlike function_call/custom_tool_call) — "web_search" is
                # a literal here, not read off the payload.  `tool_name` (not the
                # brief's literal "name") is what `_external_tool_rows` actually
                # reads (`payload.get("tool_name")`); without it `is_external_tool`
                # sees `None` and the row is silently dropped — the exact "built
                # but not wired" failure mode this task exists to close.
                add(
                    "tool_request",
                    {
                        "tool_call_id": payload.get("call_id") or payload.get("id"),
                        "tool_name": "web_search",
                        "input": payload.get("action") or {},
                    },
                    timestamp,
                )
            elif kind in _REQUEST_ITEMS:
                add(
                    "tool_request",
                    {
                        "tool_call_id": payload.get("call_id"),
                        "call_id": payload.get("call_id"),
                        "tool_name": payload.get("name"),
                        "namespace": payload.get("namespace"),
                        "input": payload.get("input")
                        if payload.get("input") is not None
                        else payload.get("arguments"),
                        "status": payload.get("status"),
                    },
                    timestamp,
                )
            elif kind in _RESULT_ITEMS:
                add(
                    "tool_result",
                    {
                        "tool_call_id": payload.get("call_id"),
                        "call_id": payload.get("call_id"),
                        "content": self._text(payload.get("output")),
                        "is_error": bool(payload.get("is_error")),
                    },
                    timestamp,
                )
        return NormalizedTranscript(
            ref=ref,
            items=tuple(items),
            status=lifecycle["status"],
            agent=ref.role,
            model=model,
            effort=effort,
        )

    def _usage_from_rows(
        self,
        all_rows: list[dict],
        rows: list[dict],
        ref: TranscriptRef,
        model: str,
        effort: str,
        lifecycle: dict,
    ) -> UsageRecord:
        """Sample the last cumulative snapshot; difference it for role segments.

        ``all_rows`` is the *full*, unsegmented capture (needed for the
        preceding-snapshot baseline lookup below); ``rows`` is already
        restricted to ``ref``'s own segment.
        """
        def unmeasured(*, messages: int = 0) -> UsageRecord:
            return UsageRecord(
                ref=ref,
                messages=messages,
                input=0,
                output=0,
                cache_read=0,
                cache_create=0,
                cache_create_1h=0,
                cache_create_5m=0,
                role=ref.role,
                agent=ref.role,
                effort=effort,
                model=model,
                speed="standard",
                status="UNMEASURED",
                failure_count=lifecycle["failure_count"],
                retry_count=lifecycle["retry_count"],
                discarded=False,
                reasoning_output=0,
            )

        snapshots = self._snapshots(rows)
        if not snapshots:
            return unmeasured()

        last = snapshots[-1]
        baseline: dict = {}
        if ref.start_ordinal is not None:
            # A session shared by several roles is differenced against the last
            # cumulative snapshot taken before this role's segment started.
            preceding = self._snapshots(
                [
                    row
                    for row in all_rows
                    if isinstance(self._ordinal(row), int)
                    and self._ordinal(row) < ref.start_ordinal
                ]
            )
            baseline = preceding[-1] if preceding else {}

        # fix round 2: every field either snapshot can carry is parsed
        # defensively up front. A redacted (e.g. "[REDACTED]") value in
        # *either* the last or the baseline snapshot -- not just the last --
        # must flip the whole record to UNMEASURED rather than crash or
        # compute a delta against a value silently treated as 0.
        names = (
            "input_tokens",
            "cached_input_tokens",
            "output_tokens",
            "cache_write_input_tokens",
            "reasoning_output_tokens",
        )
        parsed = {
            name: (
                parse_token_count(last.get(name)),
                parse_token_count(baseline.get(name)),
            )
            for name in names
        }
        if any(last_value is None or base_value is None for last_value, base_value in parsed.values()):
            return unmeasured(messages=len(snapshots))

        def delta(name: str) -> int:
            last_value, base_value = parsed[name]
            return max(last_value - base_value, 0)

        raw_input = delta("input_tokens")
        cached = delta("cached_input_tokens")
        return UsageRecord(
            ref=ref,
            messages=len(snapshots),
            input=max(raw_input - cached, 0),
            output=delta("output_tokens"),
            cache_read=cached,
            cache_create=delta("cache_write_input_tokens"),
            cache_create_1h=0,
            cache_create_5m=delta("cache_write_input_tokens"),
            role=ref.role,
            agent=ref.role,
            effort=effort,
            model=model,
            speed="standard",
            status=lifecycle["status"],
            failure_count=lifecycle["failure_count"],
            retry_count=lifecycle["retry_count"],
            discarded=lifecycle["status"] == "FAILED",
            reasoning_output=delta("reasoning_output_tokens"),
        )

    def normalize(self, ref: TranscriptRef) -> NormalizedTranscript:
        return self.stats(ref).normalized

    def usage(self, ref: TranscriptRef) -> UsageRecord:
        return self.stats(ref).usage


__all__ = [
    "CodexTranscriptAdapter",
    "RolloutSearch",
    "discover_rollout_candidates",
    "locate_candidates",
    "ordinal_window_for_timestamps",
]
