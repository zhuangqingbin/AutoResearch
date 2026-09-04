"""Transcript adapter registry."""

from __future__ import annotations

from autoresearch.trace.transcripts.base import (
    LOCAL_TOOL_NAMES,
    NormalizedItem,
    NormalizedTranscript,
    RunIdentity,
    StatsTranscriptAdapter,
    TranscriptAdapter,
    TranscriptRef,
    TranscriptStats,
    TranscriptUnreadable,
    UsageRecord,
    is_external_tool,
    tool_call_id,
)
from autoresearch.trace.transcripts.claude import ClaudeTranscriptAdapter
from autoresearch.trace.transcripts.codex import (
    CodexTranscriptAdapter,
    locate_candidates,
)

_ADAPTERS = {
    "claude": ClaudeTranscriptAdapter,
    "codex": CodexTranscriptAdapter,
}


def adapter_for(engine: str) -> TranscriptAdapter:
    """Return the adapter for one engine; unknown engines are never guessed."""
    factory = _ADAPTERS.get(str(engine).lower())
    if factory is None:
        raise ValueError(f"unsupported engine: {engine}")
    return factory()


__all__ = [
    "LOCAL_TOOL_NAMES",
    "ClaudeTranscriptAdapter",
    "CodexTranscriptAdapter",
    "NormalizedItem",
    "NormalizedTranscript",
    "RunIdentity",
    "StatsTranscriptAdapter",
    "TranscriptAdapter",
    "TranscriptRef",
    "TranscriptStats",
    "TranscriptUnreadable",
    "UsageRecord",
    "adapter_for",
    "is_external_tool",
    "locate_candidates",
    "tool_call_id",
]
