"""Stable, engine-aware transcript adapter contracts."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from autoresearch.trace.transcripts import adapter_for
from autoresearch.trace.transcripts.base import (
    NormalizedItem,
    RunIdentity,
    TranscriptAdapter,
    TranscriptRef,
)
from autoresearch.trace.transcripts.claude import ClaudeTranscriptAdapter

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def claude_ref() -> TranscriptRef:
    return TranscriptRef(
        engine="claude",
        path=FIXTURES / "claude" / "agent-l4-card.jsonl",
        role="subagent",
        invocation_id="agent-l4-card-fixture-1",
    )


def test_claude_adapter_preserves_usage_dedup_and_retry_status(claude_ref):
    adapter = ClaudeTranscriptAdapter()

    normalized = adapter.normalize(claude_ref)
    usage = adapter.usage(claude_ref)

    assert usage.messages == 2
    assert usage.input == 34
    assert usage.output == 42
    assert usage.cache_read == 48
    assert usage.cache_create == 56
    assert usage.status == "RETRIED_SUCCEEDED"
    assert usage.failure_count == 1
    assert usage.retry_count == 1
    assert usage.agent == "l4-card"
    assert usage.model == "claude-opus-5"
    assert usage.effort == "xhigh"
    assert [item.kind for item in normalized.items] == [
        "message",
        "tool_request",
        "tool_result",
        "error",
        "message",
    ]


def test_normalized_items_keep_only_last_stream_update(claude_ref):
    normalized = ClaudeTranscriptAdapter().normalize(claude_ref)

    messages = [item for item in normalized.items if item.kind == "message"]
    assert messages[0].payload["text"] == "Synthetic analysis complete."
    assert all("draft" not in str(item.payload).lower() for item in normalized.items)


def test_adapter_registry_selects_by_engine():
    assert isinstance(adapter_for("claude"), ClaudeTranscriptAdapter)
    with pytest.raises(ValueError, match="unsupported engine"):
        adapter_for("unknown")


def test_protocol_values_are_immutable(claude_ref):
    identity = RunIdentity(run_id="fixture-run", engine="claude", session_ref="fixture")
    item = NormalizedItem(index=0, kind="message", payload={"nested": {"value": 1}})

    assert isinstance(ClaudeTranscriptAdapter(), TranscriptAdapter)
    with pytest.raises(FrozenInstanceError):
        identity.run_id = "changed"
    with pytest.raises(TypeError):
        item.payload["nested"]["value"] = 2
    with pytest.raises(FrozenInstanceError):
        claude_ref.role = "main"


def test_claude_locator_uses_explicit_session_ref(tmp_path):
    slug = tmp_path / "project"
    session_id = "session-fixture"
    subagents = slug / session_id / "subagents" / "workflows" / "wf_fixture"
    subagents.mkdir(parents=True)
    main = slug / f"{session_id}.jsonl"
    agent = subagents / "agent-one.jsonl"
    main.write_text("", encoding="utf-8")
    agent.write_text("", encoding="utf-8")

    refs = ClaudeTranscriptAdapter(projects_root=tmp_path).locate(
        RunIdentity(run_id="fixture-run", engine="claude", session_ref=session_id)
    )

    assert [(ref.path, ref.role, ref.status) for ref in refs] == [
        (main, "main", "PRESENT"),
        (agent, "subagent", "PRESENT"),
    ]
