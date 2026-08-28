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
from pathlib import Path

from autoresearch.common import workspace as ws
from autoresearch.trace.identity import redact_value
from autoresearch.trace.transcripts.base import (
    NormalizedItem,
    NormalizedTranscript,
    RunIdentity,
    TranscriptRef,
    TranscriptUnreadable,
    UsageRecord,
)

_SKIPPED_RESPONSE_ITEMS = frozenset(
    {"reasoning", "web_search_call", "encrypted_reasoning"}
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


class CodexTranscriptAdapter:
    """Adapter for Codex rollout JSONL bound explicitly to run invocations."""

    engine = "codex"

    def __init__(self, bindings_path: Path | str | None = None):
        self._bindings_path = None if bindings_path is None else Path(bindings_path)

    # -- location ---------------------------------------------------------

    def bindings_path(self, run_id: str) -> Path:
        if self._bindings_path is not None:
            return self._bindings_path
        return ws.scan_run_root(run_id) / "capsule" / "agents" / "bindings.jsonl"

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

    def _rows(self, ref: TranscriptRef) -> list[dict]:
        if ref.engine != "codex":
            raise ValueError(f"Codex adapter cannot read engine {ref.engine!r}")
        if ref.path is None or ref.status != "PRESENT":
            raise TranscriptUnreadable("Codex transcript is not PRESENT")
        path = Path(ref.path)
        if not path.is_file():
            raise TranscriptUnreadable(f"Codex transcript is gone: {path}")
        return list(_iter_rows(path))

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

    # -- protocol ---------------------------------------------------------

    def normalize(self, ref: TranscriptRef) -> NormalizedTranscript:
        """Keep visible messages, tool traffic and errors; drop model internals."""
        rows = self._segment(self._rows(ref), ref)
        model, effort = self._context(rows)
        lifecycle = self._lifecycle(rows)
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

    def usage(self, ref: TranscriptRef) -> UsageRecord:
        """Sample the last cumulative snapshot; difference it for role segments."""
        all_rows = self._rows(ref)
        rows = self._segment(all_rows, ref)
        model, effort = self._context(rows)
        lifecycle = self._lifecycle(rows)
        snapshots = self._snapshots(rows)
        if not snapshots:
            return UsageRecord(
                ref=ref,
                messages=0,
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

        def field(snapshot: dict, name: str) -> int:
            return int(snapshot.get(name) or 0)

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

        def delta(name: str) -> int:
            return max(field(last, name) - field(baseline, name), 0)

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


__all__ = ["CodexTranscriptAdapter", "locate_candidates"]
