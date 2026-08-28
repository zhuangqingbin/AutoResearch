"""Transcript adapter registry."""

from __future__ import annotations

from autoresearch.trace.transcripts.base import (
    NormalizedItem,
    NormalizedTranscript,
    RunIdentity,
    TranscriptAdapter,
    TranscriptRef,
    UsageRecord,
)
from autoresearch.trace.transcripts.claude import ClaudeTranscriptAdapter


def adapter_for(engine: str) -> TranscriptAdapter:
    if engine.lower() == "claude":
        return ClaudeTranscriptAdapter()
    raise ValueError(f"unsupported engine: {engine}")


__all__ = [
    "ClaudeTranscriptAdapter",
    "NormalizedItem",
    "NormalizedTranscript",
    "RunIdentity",
    "TranscriptAdapter",
    "TranscriptRef",
    "UsageRecord",
    "adapter_for",
]
