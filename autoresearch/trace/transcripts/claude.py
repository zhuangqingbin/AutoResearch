"""Claude harness transcript location, normalization, and usage parsing."""

from __future__ import annotations

import contextlib
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path

from autoresearch.trace.atomic import sha256_bytes
from autoresearch.trace.identity import redact_value
from autoresearch.trace.transcripts.base import (
    NormalizedItem,
    NormalizedTranscript,
    RunIdentity,
    TranscriptRef,
    TranscriptStats,
    UsageRecord,
    extract_operations,
    parse_token_count,
)
from autoresearch.trace.transcripts.snapshot import capture_snapshot


class ClaudeTranscriptAdapter:
    """Adapter for Claude JSONL transcripts, including streaming deduplication."""

    def __init__(self, projects_root: Path | str | None = None):
        self.projects_root = Path(projects_root or (Path.home() / ".claude" / "projects"))

    @staticmethod
    def _meta_agent(path: Path) -> str | None:
        meta = path.with_name(path.name.replace(".jsonl", ".meta.json"))
        if not meta.is_file():
            return None
        with contextlib.suppress(Exception):
            payload = json.loads(meta.read_text(encoding="utf-8")) or {}
            if isinstance(payload, dict):
                return payload.get("agentType") or None
        return None

    def locate(self, run_identity: RunIdentity) -> list[TranscriptRef]:
        if run_identity.engine != "claude" or not run_identity.session_ref:
            return []
        if not self.projects_root.is_dir():
            return []
        session_id = run_identity.session_ref
        for slug in sorted(self.projects_root.iterdir()):
            main = slug / f"{session_id}.jsonl"
            sub_dir = slug / session_id / "subagents"
            if not main.is_file() and not sub_dir.is_dir():
                continue
            refs: list[TranscriptRef] = []
            if main.is_file():
                refs.append(
                    TranscriptRef(
                        engine="claude",
                        path=main,
                        role="main",
                        session_ref=session_id,
                    )
                )
            if sub_dir.is_dir():
                refs.extend(
                    TranscriptRef(
                        engine="claude",
                        path=path,
                        role="subagent",
                        session_ref=session_id,
                    )
                    for path in sorted(sub_dir.rglob("agent-*.jsonl"))
                )
            return refs
        return []

    @staticmethod
    def _summary(
        rows: list[dict],
        ref: TranscriptRef,
        last_message_row: dict[str, int],
    ) -> dict:
        latest: dict[str, dict] = {}
        agent = effort = model = None
        models: list[str] = []
        host_version = None
        speed = "standard"
        failures: list[int] = []
        terminals: list[int] = []
        for idx, row in enumerate(rows):
            msg = row.get("message") or {}
            if not isinstance(msg, dict):
                msg = {}
            message_id = msg.get("id")
            if message_id and last_message_row.get(str(message_id)) != idx:
                continue
            failed = bool(row.get("error") or row.get("isApiErrorMessage"))
            if failed:
                failures.append(idx)
            agent = agent or row.get("attributionAgent")
            effort = effort or row.get("effort")
            host_version = host_version or row.get("version")
            candidate_model = msg.get("model")
            if candidate_model and candidate_model != "<synthetic>":
                model = model or candidate_model
                if candidate_model not in models:
                    models.append(str(candidate_model))
            usage = msg.get("usage")
            if isinstance(usage, dict) and msg.get("id"):
                latest[str(msg["id"])] = usage
                speed = usage.get("speed") or speed
            if not failed and msg.get("stop_reason") in {"end_turn", "stop_sequence"}:
                terminals.append(idx)

        failure_count = len(failures)
        terminal_after_failure = bool(terminals and (not failures or terminals[-1] > failures[-1]))
        if failure_count and terminal_after_failure:
            status = "RETRIED_SUCCEEDED"
        elif failure_count:
            status = "FAILED"
        elif terminals:
            status = "SUCCEEDED"
        else:
            status = "INCOMPLETE"
        path = ref.path
        resolved_agent = (
            "(主会话)"
            if ref.role == "main"
            else (
                agent or (ClaudeTranscriptAdapter._meta_agent(path) if path else None) or "(未标注)"
            )
        )
        return {
            "latest": latest,
            "agent": resolved_agent,
            "effort": effort or "—",
            "model": model or "—",
            "models": tuple(models),
            "host_version": str(host_version) if host_version else "—",
            "speed": speed,
            "status": status,
            "failure_count": failure_count,
            "retry_count": (failure_count if terminal_after_failure else max(failure_count - 1, 0)),
        }

    @staticmethod
    def _safe_payload(payload: dict) -> dict:
        redacted = redact_value(payload).value
        return redacted if isinstance(redacted, dict) else {}

    @staticmethod
    def _host_read(row: Mapping, blocks: list) -> dict | None:
        """Digest of the host's raw Read record (``toolUseResult.file``).

        The model-visible result is line-numbered text, so a complete read can
        only be proven from this harness-written record. A row carrying more
        than one tool_result cannot be attributed and yields nothing.
        """
        results = [b for b in blocks if isinstance(b, dict) and b.get("type") == "tool_result"]
        record = row.get("toolUseResult")
        if len(results) != 1 or not isinstance(record, dict) or record.get("type") != "text":
            return None
        meta = record.get("file")
        if not isinstance(meta, dict):
            return None
        path, content = meta.get("filePath"), meta.get("content")
        lines = [meta.get(key) for key in ("startLine", "numLines", "totalLines")]
        if (not isinstance(path, str) or not path or not isinstance(content, str)
                or any(type(value) is not int or value < 0 for value in lines)):
            return None
        raw = content.encode("utf-8")
        return {"file_path": path, "content_sha256": sha256_bytes(raw), "byte_count": len(raw),
                "start_line": lines[0], "num_lines": lines[1], "total_lines": lines[2]}

    @staticmethod
    def _is_user_turn(row: dict) -> bool:
        """Distinguish a human/user turn from harness tool-result envelopes."""
        if row.get("type") != "user":
            return False
        message = row.get("message") or {}
        if not isinstance(message, dict):
            return False
        content = message.get("content")
        if isinstance(content, str):
            return bool(content)
        if not isinstance(content, list):
            return False
        return any(
            not isinstance(block, dict) or block.get("type") != "tool_result"
            for block in content
        )

    @staticmethod
    def _content_chars(content: object) -> int:
        if content is None:
            return 0
        if isinstance(content, str):
            return len(content)
        return len(
            json.dumps(
                content,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )

    @staticmethod
    def _timestamp_bounds(rows: list[dict]) -> tuple[str | None, str | None]:
        """Return chronological bounds from timezone-aware ISO timestamps.

        Missing, malformed, and timezone-naive values cannot establish a reliable
        absolute event time, so they are excluded. If none remain, both bounds are
        ``None``.
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
        return min(valid, key=lambda item: (item[0], item[1]))[2], max(
            valid, key=lambda item: (item[0], item[1])
        )[2]

    @staticmethod
    def _validate_engine(ref: TranscriptRef, action: str) -> None:
        if ref.engine != "claude":
            raise ValueError(f"Claude adapter cannot {action} engine {ref.engine!r}")

    @staticmethod
    def _validate_ref(ref: TranscriptRef, action: str) -> None:
        ClaudeTranscriptAdapter._validate_engine(ref, action)
        if ref.path is None or ref.status != "PRESENT":
            raise FileNotFoundError("Claude transcript is not PRESENT")

    def stats(self, ref: TranscriptRef) -> TranscriptStats:
        """Capture one snapshot of ``ref.path``, then delegate to :meth:`stats_from_rows`.

        The file-level entry point (task-2-brief §3): reads the source
        exactly once via `snapshot.capture_snapshot`, never via a second,
        independent parse -- everything else this method used to compute
        directly now comes from the rows-level entry point below.
        """
        self._validate_ref(ref, "inspect")
        snapshot = capture_snapshot(ref.path, engine="claude")
        return self.stats_from_rows(snapshot.rows, ref)

    def stats_from_rows(
        self, rows: Sequence[Mapping[str, object]], ref: TranscriptRef
    ) -> TranscriptStats:
        """Build normalization, usage, and diagnostics from already-read rows.

        The rows-level entry point (task-2-brief §3): pure with respect to
        ``ref.path`` -- it never touches the filesystem, so a caller holding
        one `snapshot.TranscriptSnapshot`'s ``rows`` (shared across several
        invocations bound to the same source) can call this once per
        invocation/segment without a second file read.
        """
        self._validate_engine(ref, "inspect")
        rows = list(rows)
        if ref.start_ordinal is not None or ref.end_ordinal is not None:
            # Claude rows carry no native ordinal, so a bound segment is positional
            # (as bound by runner/host_evidence): later appends never change it.
            start = ref.start_ordinal or 0
            end = len(rows) - 1 if ref.end_ordinal is None else ref.end_ordinal
            rows = rows[start:end + 1]
        last_message_row: dict[str, int] = {}
        for idx, row in enumerate(rows):
            msg = row.get("message") or {}
            if isinstance(msg, dict) and msg.get("id"):
                last_message_row[str(msg["id"])] = idx
        summary = self._summary(rows, ref, last_message_row)

        items: list[NormalizedItem] = []
        tool_requests_by_id: dict[str, str] = {}
        tool_results_by_id: dict[str, object] = {}
        for row in rows:
            message = row.get("message") or {}
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                tool_id = block.get("id")
                tool_name = block.get("name")
                if (
                    block.get("type") == "tool_use"
                    and isinstance(tool_id, str)
                    and tool_id
                    and isinstance(tool_name, str)
                    and tool_name
                ):
                    tool_requests_by_id[tool_id] = tool_name
                result_id = block.get("tool_use_id")
                if (
                    block.get("type") == "tool_result"
                    and isinstance(result_id, str)
                    and result_id
                ):
                    tool_results_by_id[result_id] = block.get("content")

        def add(kind: str, payload: dict, timestamp: str | None) -> None:
            items.append(
                NormalizedItem(
                    index=len(items),
                    kind=kind,
                    payload=self._safe_payload(payload),
                    timestamp=timestamp,
                )
            )

        for row_idx, row in enumerate(rows):
            timestamp = row.get("timestamp")
            msg = row.get("message") or {}
            if not isinstance(msg, dict):
                msg = {}
            message_id = msg.get("id")
            if message_id and last_message_row.get(str(message_id)) != row_idx:
                continue
            if row.get("error") or row.get("isApiErrorMessage"):
                add(
                    "error",
                    {
                        "error": row.get("error"),
                        "is_api_error": bool(row.get("isApiErrorMessage")),
                    },
                    timestamp,
                )
                continue
            content = msg.get("content")
            blocks = content if isinstance(content, list) else []
            text_parts = [
                str(block.get("text"))
                for block in blocks
                if isinstance(block, dict)
                and block.get("type") == "text"
                and block.get("text") is not None
            ]
            if isinstance(content, str):
                text_parts = [content]
            if text_parts:
                add(
                    "message",
                    {
                        "message_id": message_id,
                        "role": row.get("type"),
                        "text": "\n".join(text_parts),
                        "stop_reason": msg.get("stop_reason"),
                    },
                    timestamp,
                )
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                block_type = block.get("type")
                if block_type == "tool_use":
                    tool_name = block.get("name")
                    request_id = block.get("id")
                    add(
                        "tool_request",
                        {
                            "message_id": message_id,
                            "tool_use_id": request_id,
                            "tool_name": tool_name,
                            "input": block.get("input") or {},
                        },
                        timestamp,
                    )
                elif block_type == "tool_result":
                    request_id = block.get("tool_use_id")
                    result = {
                        "tool_use_id": request_id,
                        "content": block.get("content"),
                        "is_error": bool(block.get("is_error")),
                    }
                    host_read = self._host_read(row, blocks)
                    if host_read is not None:
                        result["host_read"] = host_read
                    add("tool_result", result, timestamp)
        normalized = NormalizedTranscript(
            ref=ref,
            items=tuple(items),
            status=summary["status"],
            agent=summary["agent"],
            model=summary["model"],
            effort=summary["effort"],
        )

        totals = {
            "input": 0,
            "output": 0,
            "cache_read": 0,
            "cache_create": 0,
            "cache_create_1h": 0,
            "cache_create_5m": 0,
        }
        latest = summary["latest"]
        # fix round 2: a redacted usage figure (see `parse_token_count`) must
        # flip the whole record to UNMEASURED, not crash and not silently
        # contribute a partial, misleading sum from whatever *other* fields
        # happened to still be numeric.
        usage_unmeasured = False
        for usage in latest.values():
            input_tokens = parse_token_count(usage.get("input_tokens"))
            output_tokens = parse_token_count(usage.get("output_tokens"))
            cache_read = parse_token_count(usage.get("cache_read_input_tokens"))
            cache_total = parse_token_count(usage.get("cache_creation_input_tokens"))
            cache_split = usage.get("cache_creation") or {}
            c1h = parse_token_count(cache_split.get("ephemeral_1h_input_tokens"))
            c5m = parse_token_count(cache_split.get("ephemeral_5m_input_tokens"))
            if None in (input_tokens, output_tokens, cache_read, cache_total, c1h, c5m):
                usage_unmeasured = True
                continue
            totals["input"] += input_tokens
            totals["output"] += output_tokens
            totals["cache_read"] += cache_read
            totals["cache_create"] += cache_total
            totals["cache_create_1h"] += c1h
            totals["cache_create_5m"] += c5m if c5m else max(cache_total - c1h, 0)
        if usage_unmeasured:
            # Discard any partial sum from other, cleanly-parsed messages --
            # a mix of real and zero-filled numbers would misrepresent
            # itself as a near-complete total. All-zero is the same
            # documented UNMEASURED placeholder `unmeasured_row`/Codex's
            # empty-snapshots branch already use, never a claim of zero
            # spend.
            totals = dict.fromkeys(totals, 0)
        usage_status = "UNMEASURED" if usage_unmeasured else summary["status"]
        usage_record = UsageRecord(
            ref=ref,
            messages=len(latest),
            input=totals["input"],
            output=totals["output"],
            cache_read=totals["cache_read"],
            cache_create=totals["cache_create"],
            cache_create_1h=totals["cache_create_1h"],
            cache_create_5m=totals["cache_create_5m"],
            role=ref.role,
            agent=summary["agent"],
            effort=summary["effort"],
            model=summary["model"],
            speed=summary["speed"],
            status=usage_status,
            failure_count=summary["failure_count"],
            retry_count=summary["retry_count"],
            discarded=summary["status"] == "FAILED",
            models=summary["models"],
            host_version=summary["host_version"],
        )

        started_at, ended_at = self._timestamp_bounds(rows)
        context_tokens: list[int] = []
        assistant_rows: list[int] = []
        for row_idx, row in enumerate(rows):
            if row.get("error") or row.get("isApiErrorMessage"):
                continue
            message = row.get("message") or {}
            if not isinstance(message, dict):
                continue
            message_id = message.get("id")
            if message_id and last_message_row.get(str(message_id)) != row_idx:
                continue
            if row.get("type") == "assistant" and message_id:
                assistant_rows.append(row_idx)
            message_usage = message.get("usage")
            if row.get("type") != "assistant" or not isinstance(message_usage, dict):
                continue
            # fix round 2: one unreadable (e.g. redacted) field omits this
            # sample from the series rather than crashing or fabricating a
            # partial sum -- context_tokens is already a "however many
            # samples we could measure" series, so a missing sample is a
            # normal, representable state.
            context_parts = (
                parse_token_count(message_usage.get("input_tokens")),
                parse_token_count(message_usage.get("cache_read_input_tokens")),
                parse_token_count(message_usage.get("cache_creation_input_tokens")),
            )
            if None not in context_parts:
                context_tokens.append(sum(context_parts))

        compact_pre_tokens: list[int] = []
        for row in rows:
            for key in ("compact_boundary", "compactMetadata"):
                boundary = row.get(key)
                if not isinstance(boundary, dict):
                    continue
                pre_tokens = boundary.get("preTokens")
                if type(pre_tokens) is int and pre_tokens >= 0:
                    compact_pre_tokens.append(pre_tokens)

        last_user_row = max(
            (row_idx for row_idx, row in enumerate(rows) if self._is_user_turn(row)),
            default=-1,
        )
        assistant_after_last_user = (
            sum(row_idx > last_user_row for row_idx in assistant_rows)
            if last_user_row >= 0
            else 0
        )
        tool_requests = Counter(tool_requests_by_id.values())
        tool_results: Counter[str] = Counter()
        for tool_id, content in tool_results_by_id.items():
            tool_name = tool_requests_by_id.get(tool_id)
            if tool_name:
                tool_results[tool_name] += self._content_chars(content)
        return TranscriptStats(
            normalized=normalized,
            usage=usage_record,
            started_at=started_at,
            ended_at=ended_at,
            context_tokens=tuple(context_tokens),
            first_context_tokens=context_tokens[0] if context_tokens else None,
            compact_pre_tokens=tuple(compact_pre_tokens),
            suspected_tail=max(assistant_after_last_user - 1, 0),
            tool_requests=tool_requests,
            tool_results=tool_results,
            operations=extract_operations(normalized.items),
        )

    def normalize(self, ref: TranscriptRef) -> NormalizedTranscript:
        self._validate_ref(ref, "normalize")
        return self.stats(ref).normalized

    def usage(self, ref: TranscriptRef) -> UsageRecord:
        self._validate_ref(ref, "meter")
        return self.stats(ref).usage
