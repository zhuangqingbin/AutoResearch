"""Stable, engine-aware transcript adapter contracts."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from autoresearch.trace.blobs import blob_path
from autoresearch.trace.capsule import (
    bind_transcript,
    materialize_agent_index,
    materialize_transcripts,
    record_agent_boundary,
)
from autoresearch.trace.transcripts import adapter_for
from autoresearch.trace.transcripts.base import (
    NormalizedItem,
    RunIdentity,
    TranscriptAdapter,
    TranscriptRef,
)
from autoresearch.trace.transcripts.claude import ClaudeTranscriptAdapter
from autoresearch.trace.transcripts.codex import (
    CodexTranscriptAdapter,
    locate_candidates,
)

FIXTURES = Path(__file__).parent / "fixtures"


def read_jsonl(path) -> list[dict]:
    import json

    text = Path(path).read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


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


@pytest.fixture
def codex_ref() -> TranscriptRef:
    return TranscriptRef(
        engine="codex",
        path=FIXTURES / "codex" / "rollout.jsonl",
        role="l4-card",
        subject="600000",
        invocation_id="agent-l4-card-600000-1",
    )


def test_codex_usage_uses_last_cumulative_snapshot_not_sum(codex_ref):
    usage = CodexTranscriptAdapter().usage(codex_ref)

    assert usage.model == "gpt-5.6-sol"
    assert usage.effort == "high"
    assert usage.input == 207681
    assert usage.cache_read == 200448
    assert usage.cache_create == 4096
    assert usage.output == 1671
    assert usage.reasoning_output == 1119
    assert usage.status == "RETRIED_SUCCEEDED"
    assert usage.failure_count == 1
    assert usage.retry_count == 1
    assert usage.role == "l4-card"


def test_codex_input_excludes_cached_input_tokens(codex_ref):
    """`input_tokens` in a Codex rollout INCLUDES `cached_input_tokens`.

    Verified against real rollouts: ``total_tokens == input_tokens + output_tokens``
    while ``cached_input_tokens <= input_tokens``.  Reporting the raw field as
    uncached input would double-count every cached prefix.
    """
    raw = read_jsonl(codex_ref.path)
    final = [
        row
        for row in raw
        if row.get("payload", {}).get("type") == "token_count"
    ][-1]["payload"]["info"]["total_token_usage"]
    usage = CodexTranscriptAdapter().usage(codex_ref)

    assert final["input_tokens"] == 408129
    assert usage.input == final["input_tokens"] - final["cached_input_tokens"]
    assert usage.input + usage.cache_read == final["input_tokens"]


def test_codex_normalize_keeps_visible_items_only(codex_ref):
    normalized = CodexTranscriptAdapter().normalize(codex_ref)

    assert [item.kind for item in normalized.items] == [
        "message",
        "tool_request",
        "tool_result",
        "message",
        "error",
        "message",
    ]
    blob = str([dict(item.payload) for item in normalized.items])
    assert "encrypted_content" not in blob
    assert "ZmFrZS1lbmNyeXB0ZWQ" not in blob
    assert normalized.model == "gpt-5.6-sol"
    assert normalized.effort == "high"


def test_codex_normalize_redacts_secret_material(codex_ref):
    normalized = CodexTranscriptAdapter().normalize(codex_ref)
    request = next(item for item in normalized.items if item.kind == "tool_request")

    assert "FIXTUREONLYNOTREAL0000" not in str(dict(request.payload))
    assert "[REDACTED]" in str(dict(request.payload))


@pytest.mark.parametrize(
    "candidates,status",
    [([], "GONE"), (["a.jsonl", "b.jsonl"], "AMBIGUOUS")],
)
def test_codex_locator_never_guesses_latest_mtime(tmp_path, candidates, status):
    paths = []
    for name in candidates:
        path = tmp_path / name
        path.write_text("", encoding="utf-8")
        paths.append(path)
    identity = RunIdentity(run_id="20260827T010203456789Z", engine="codex")

    refs = locate_candidates(identity, paths)

    assert refs[0].status == status
    assert all(ref.status != "PRESENT" for ref in refs)


def test_codex_single_candidate_is_not_promoted_to_present(tmp_path):
    path = tmp_path / "only.jsonl"
    path.write_text("", encoding="utf-8")
    identity = RunIdentity(run_id="20260827T010203456789Z", engine="codex")

    refs = locate_candidates(identity, [path])

    assert [ref.status for ref in refs] == ["CANDIDATE"]


def test_explicit_binding_is_authoritative(codex_run):
    handle, source = codex_run

    bind_transcript(
        handle.run_id,
        source,
        role="l4-card",
        subject="600000",
        invocation_id="agent-l4-card-600000-1",
    )
    refs = CodexTranscriptAdapter().locate(
        RunIdentity(run_id=handle.run_id, engine="codex")
    )

    assert [(ref.status, ref.role, ref.subject) for ref in refs] == [
        ("PRESENT", "l4-card", "600000")
    ]


def test_visible_tool_calls_are_indexed_and_results_are_blobbed(codex_run):
    handle, source = codex_run
    bind_transcript(
        handle.run_id,
        source,
        role="l4-intel",
        subject="600000",
        invocation_id="agent-l4-intel-600000-1",
    )

    materialize_transcripts(handle.run_id)

    rows = read_jsonl(handle.capsule / "lineage/external_tools.jsonl")
    assert rows[0]["tool_name"] == "web.search_query"
    assert rows[0]["capture_level"] == "HARNESS_RESPONSE"
    assert rows[0]["role"] == "l4-intel"
    assert rows[0]["invocation_id"] == "agent-l4-intel-600000-1"
    assert rows[0]["status"] == "COMPLETED"
    assert blob_path(handle.capsule, rows[0]["result_hash"]).is_file()
    assert "FIXTUREONLYNOTREAL0000" not in rows[0]["request"]


def test_incomplete_tool_request_stays_an_explicit_row(codex_run):
    handle, source = codex_run
    lines = source.read_text(encoding="utf-8").splitlines()
    trimmed = [line for line in lines if "custom_tool_call_output" not in line]
    source.write_text("\n".join(trimmed) + "\n", encoding="utf-8")
    bind_transcript(
        handle.run_id,
        source,
        role="l4-intel",
        subject="600000",
        invocation_id="agent-l4-intel-600000-1",
    )

    materialize_transcripts(handle.run_id)

    rows = read_jsonl(handle.capsule / "lineage/external_tools.jsonl")
    assert [row["status"] for row in rows] == ["INCOMPLETE"]
    assert rows[0]["result_hash"] is None


def test_raw_archive_is_redacted_and_deterministic(codex_run):
    import gzip

    handle, source = codex_run
    bind_transcript(
        handle.run_id,
        source,
        role="l4-card",
        subject="600000",
        invocation_id="agent-l4-card-600000-1",
    )

    materialize_transcripts(handle.run_id)
    archive = handle.capsule / "agents/raw/agent-l4-card-600000-1.jsonl.gz"
    first = archive.read_bytes()
    materialize_transcripts(handle.run_id)

    assert archive.read_bytes() == first
    body = gzip.decompress(first).decode("utf-8")
    assert "FIXTUREONLYNOTREAL0000" not in body
    assert "session_meta" in body
    assert first[4:8] == b"\x00\x00\x00\x00"


def test_agent_index_reports_bound_invocations(codex_run):
    import json

    handle, source = codex_run
    bind_transcript(
        handle.run_id,
        source,
        role="l4-card",
        subject="600000",
        invocation_id="agent-l4-card-600000-1",
    )

    materialize_transcripts(handle.run_id)

    index = json.loads(
        (handle.capsule / "agents/index.json").read_text(encoding="utf-8")
    )
    row = index["invocations"][0]
    assert row["status"] == "PRESENT"
    assert row["role"] == "l4-card"
    assert row["usage"]["output"] == 1671
    assert index["coverage"] == {"expected": 1, "present": 1, "missing": 0}


def test_missing_source_at_materialize_is_gone_not_silent(codex_run):
    import json

    handle, source = codex_run
    bind_transcript(
        handle.run_id,
        source,
        role="l4-card",
        subject="600000",
        invocation_id="agent-l4-card-600000-1",
    )
    source.unlink()

    materialize_transcripts(handle.run_id)

    index = json.loads(
        (handle.capsule / "agents/index.json").read_text(encoding="utf-8")
    )
    assert index["invocations"][0]["status"] == "GONE"
    assert index["coverage"] == {"expected": 1, "present": 0, "missing": 1}


def test_conflicting_binding_for_one_invocation_is_rejected(codex_run, tmp_path):
    handle, source = codex_run
    other = tmp_path / "harness" / "other.jsonl"
    other.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    bind_transcript(
        handle.run_id,
        source,
        role="l4-card",
        subject="600000",
        invocation_id="agent-l4-card-600000-1",
    )

    with pytest.raises(ValueError, match="conflicting transcript binding"):
        bind_transcript(
            handle.run_id,
            other,
            role="l4-card",
            subject="600000",
            invocation_id="agent-l4-card-600000-1",
        )


def test_identical_binding_is_idempotent(codex_run):
    handle, source = codex_run
    first = bind_transcript(
        handle.run_id,
        source,
        role="l4-card",
        subject="600000",
        invocation_id="agent-l4-card-600000-1",
    )
    second = bind_transcript(
        handle.run_id,
        source,
        role="l4-card",
        subject="600000",
        invocation_id="agent-l4-card-600000-1",
    )

    assert first == second
    assert len(read_jsonl(handle.capsule / "agents/bindings.jsonl")) == 1


def test_binding_rejects_paths_inside_the_capsule(codex_run):
    handle, _ = codex_run
    inside = handle.capsule / "identity/run_contract.json"

    with pytest.raises(ValueError, match="inside the capsule"):
        bind_transcript(
            handle.run_id,
            inside,
            role="l4-card",
            subject="600000",
            invocation_id="agent-l4-card-600000-2",
        )


def test_adapter_registry_selects_codex():
    assert isinstance(adapter_for("codex"), CodexTranscriptAdapter)


# --- Task 11: every reached invocation is accounted for ---------------------


_REACHED = (
    ("strategist", None, None, "strategist-market-1"),
    ("sector-brief", None, "银行", "sector-brief-yinhang-1"),
    ("l3-rank", None, None, "l3-rank-market-1"),
    ("l4-card", "600000", None, "l4-card-600000-1"),
    ("l4-intel", "600000", None, "l4-intel-600000-1"),
)


def _dispatch_all(handle, source, *, bind=True):
    for role, subject, display, invocation_id in _REACHED:
        record_agent_boundary(
            handle.run_id,
            "AGENT_DISPATCHED",
            role=role,
            subject=subject,
            subject_display=display,
            invocation_id=invocation_id,
            attempt=1,
        )
        record_agent_boundary(
            handle.run_id,
            "AGENT_COMPLETED",
            role=role,
            subject=subject,
            subject_display=display,
            invocation_id=invocation_id,
            attempt=1,
        )
        if bind:
            bind_transcript(
                handle.run_id,
                source,
                role=role,
                subject=subject or "market",
                invocation_id=invocation_id,
            )


def test_agent_index_has_one_explicit_row_per_reached_invocation(codex_run):
    handle, source = codex_run
    _dispatch_all(handle, source)

    index = materialize_agent_index(handle.run_id)

    keys = {
        (row["role"], row["subject"], row["attempt"]) for row in index["invocations"]
    }
    assert ("strategist", None, 1) in keys
    assert ("sector-brief", "银行", 1) in keys
    assert ("l3-rank", None, 1) in keys
    assert ("l4-card", "600000", 1) in keys
    assert ("l4-intel", "600000", 1) in keys
    assert index["coverage"] == {"expected": 5, "present": 5, "missing": 0}


def test_reached_dispatch_without_transcript_is_gone_not_absent(codex_run):
    handle, source = codex_run
    _dispatch_all(handle, source, bind=False)

    index = materialize_agent_index(handle.run_id)

    assert index["coverage"] == {"expected": 5, "present": 0, "missing": 5}
    assert {row["status"] for row in index["invocations"]} == {"GONE"}
    assert all(row["dispatched"] for row in index["invocations"])


def test_failed_dispatch_still_owns_a_row(codex_run):
    handle, _ = codex_run
    record_agent_boundary(
        handle.run_id,
        "AGENT_DISPATCHED",
        role="l4-card",
        subject="600000",
        invocation_id="l4-card-600000-1",
        attempt=1,
    )
    record_agent_boundary(
        handle.run_id,
        "AGENT_FAILED",
        role="l4-card",
        subject="600000",
        invocation_id="l4-card-600000-1",
        attempt=1,
        error={"status": "threw"},
    )

    index = materialize_agent_index(handle.run_id)

    row = index["invocations"][0]
    assert row["terminal"] == "FAILED"
    assert row["status"] == "GONE"
    assert index["coverage"]["expected"] == 1


def test_deterministic_relays_are_not_expected_to_have_transcripts(codex_run):
    handle, source = codex_run
    record_agent_boundary(
        handle.run_id,
        "AGENT_DISPATCHED",
        role="trace-control",
        subject="600000",
        invocation_id="trace-control-l4-card-600000-1",
        attempt=1,
        result={"target_role": "l4-card"},
    )
    bind_transcript(
        handle.run_id,
        source,
        role="l4-card",
        subject="600000",
        invocation_id="l4-card-600000-1",
    )

    index = materialize_agent_index(handle.run_id)

    relay = next(r for r in index["invocations"] if r["role"] == "trace-control")
    assert relay["status"] == "NOT_EXPECTED"
    assert relay["expected"] is False
    assert index["coverage"] == {"expected": 1, "present": 1, "missing": 0}


def test_subject_key_is_stable_and_display_survives_into_the_index(codex_run):
    from autoresearch.trace.capsule import subject_key

    handle, source = codex_run
    record_agent_boundary(
        handle.run_id,
        "AGENT_DISPATCHED",
        role="sector-brief",
        subject_display="银行",
        invocation_id=f"sector-brief-{subject_key('银行')}-1",
        attempt=1,
    )

    index = materialize_agent_index(handle.run_id)
    row = index["invocations"][0]

    assert subject_key("银行") == subject_key("银行")
    assert len(subject_key("银行")) == 12
    assert row["subject"] == "银行"
