"""Claude harness transcript location, normalization, and usage parsing."""

from __future__ import annotations

import contextlib
import json
from pathlib import Path

from autoresearch.trace.identity import redact_value
from autoresearch.trace.transcripts.base import (
    NormalizedItem,
    NormalizedTranscript,
    RunIdentity,
    TranscriptRef,
    UsageRecord,
)


class ClaudeTranscriptAdapter:
    """Adapter for Claude JSONL transcripts, including streaming deduplication."""

    def __init__(self, projects_root: Path | str | None = None):
        self.projects_root = Path(projects_root or (Path.home() / ".claude" / "projects"))

    @staticmethod
    def _iter_rows(path: Path):
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
    def _summary(rows: list[dict], ref: TranscriptRef) -> dict:
        latest: dict[str, dict] = {}
        agent = effort = model = None
        speed = "standard"
        failures: list[int] = []
        terminals: list[int] = []
        for idx, row in enumerate(rows):
            failed = bool(row.get("error") or row.get("isApiErrorMessage"))
            if failed:
                failures.append(idx)
            agent = agent or row.get("attributionAgent")
            effort = effort or row.get("effort")
            msg = row.get("message") or {}
            if not isinstance(msg, dict):
                msg = {}
            candidate_model = msg.get("model")
            if candidate_model and candidate_model != "<synthetic>":
                model = model or candidate_model
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
            "speed": speed,
            "status": status,
            "failure_count": failure_count,
            "retry_count": (failure_count if terminal_after_failure else max(failure_count - 1, 0)),
        }

    @staticmethod
    def _safe_payload(payload: dict) -> dict:
        redacted = redact_value(payload).value
        return redacted if isinstance(redacted, dict) else {}

    def normalize(self, ref: TranscriptRef) -> NormalizedTranscript:
        if ref.engine != "claude":
            raise ValueError(f"Claude adapter cannot normalize engine {ref.engine!r}")
        if ref.path is None or ref.status != "PRESENT":
            raise FileNotFoundError("Claude transcript is not PRESENT")
        rows = list(self._iter_rows(ref.path))
        summary = self._summary(rows, ref)
        last_message_row: dict[str, int] = {}
        for idx, row in enumerate(rows):
            msg = row.get("message") or {}
            if (
                isinstance(msg, dict)
                and msg.get("id")
                and not (row.get("error") or row.get("isApiErrorMessage"))
            ):
                last_message_row[str(msg["id"])] = idx

        items: list[NormalizedItem] = []

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
            msg = row.get("message") or {}
            if not isinstance(msg, dict):
                continue
            message_id = msg.get("id")
            if message_id and last_message_row.get(str(message_id)) != row_idx:
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
                    add(
                        "tool_request",
                        {
                            "message_id": message_id,
                            "tool_use_id": block.get("id"),
                            "tool_name": block.get("name"),
                            "input": block.get("input") or {},
                        },
                        timestamp,
                    )
                elif block_type == "tool_result":
                    add(
                        "tool_result",
                        {
                            "tool_use_id": block.get("tool_use_id"),
                            "content": block.get("content"),
                            "is_error": bool(block.get("is_error")),
                        },
                        timestamp,
                    )
        return NormalizedTranscript(
            ref=ref,
            items=tuple(items),
            status=summary["status"],
            agent=summary["agent"],
            model=summary["model"],
            effort=summary["effort"],
        )

    def usage(self, ref: TranscriptRef) -> UsageRecord:
        if ref.engine != "claude":
            raise ValueError(f"Claude adapter cannot meter engine {ref.engine!r}")
        if ref.path is None or ref.status != "PRESENT":
            raise FileNotFoundError("Claude transcript is not PRESENT")
        rows = list(self._iter_rows(ref.path))
        summary = self._summary(rows, ref)
        totals = {
            "input": 0,
            "output": 0,
            "cache_read": 0,
            "cache_create": 0,
            "cache_create_1h": 0,
            "cache_create_5m": 0,
        }
        latest = summary["latest"]
        for usage in latest.values():
            totals["input"] += int(usage.get("input_tokens") or 0)
            totals["output"] += int(usage.get("output_tokens") or 0)
            totals["cache_read"] += int(usage.get("cache_read_input_tokens") or 0)
            cache_total = int(usage.get("cache_creation_input_tokens") or 0)
            totals["cache_create"] += cache_total
            cache_split = usage.get("cache_creation") or {}
            c1h = int(cache_split.get("ephemeral_1h_input_tokens") or 0)
            c5m = int(cache_split.get("ephemeral_5m_input_tokens") or 0)
            totals["cache_create_1h"] += c1h
            totals["cache_create_5m"] += c5m if c5m else max(cache_total - c1h, 0)
        return UsageRecord(
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
            status=summary["status"],
            failure_count=summary["failure_count"],
            retry_count=summary["retry_count"],
            discarded=summary["status"] == "FAILED",
        )
