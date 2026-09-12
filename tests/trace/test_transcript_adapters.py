"""Stable, engine-aware transcript adapter contracts."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

import autoresearch.trace.transcripts as transcripts_mod
from autoresearch.common import workspace as ws
from autoresearch.trace import capsule as capsule_mod
from autoresearch.trace.blobs import blob_path
from autoresearch.trace.capsule import (
    bind_transcript,
    materialize_agent_index,
    materialize_transcripts,
    record_agent_boundary,
)
from autoresearch.trace.transcripts import adapter_for
from autoresearch.trace.transcripts.base import (
    BINDING_STATUSES,
    COVERAGE_KEYS,
    CURRENT_TRANSCRIPT_SCHEMA_VERSION,
    KNOWN_TRANSCRIPT_SCHEMA_VERSIONS,
    OBSERVATION_KINDS,
    SEGMENT_QUALITIES,
    ArchiveDigest,
    ArtifactDigest,
    NormalizedItem,
    RunIdentity,
    SourcePrefixDigest,
    ToolResponseDigest,
    TranscriptAdapter,
    TranscriptRef,
    TranscriptStats,
    TranscriptUnreadable,
    classify_observation,
    hash_artifact_bytes,
    hash_tool_response,
    require_known_transcript_schema_version,
)
from autoresearch.trace.transcripts.claude import ClaudeTranscriptAdapter
from autoresearch.trace.transcripts.codex import (
    CodexTranscriptAdapter,
    locate_candidates,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def claude_run(tmp_path, monkeypatch):
    """One active Claude-engine run, rooted under a fake `~/.claude/projects` layout.

    Mirrors `tests.forensic_fixtures.redirect_roots` but keeps `engine="claude"`
    (that helper hardcodes `"codex"`) and points `ClaudeTranscriptAdapter` at a
    throwaway `projects_root` instead of the real `~/.claude/projects`.
    """
    monkeypatch.setattr(ws, "ENGINE", "claude")
    monkeypatch.setattr(ws, "context_root", lambda: tmp_path / "context_claude")
    monkeypatch.setattr(ws, "reports_root", lambda: tmp_path / "reports_claude")
    monkeypatch.delenv("AUTORESEARCH_RUN_ID", raising=False)
    monkeypatch.setattr(
        "autoresearch.scan.user_config.DEFAULT_PINNED_PATH",
        tmp_path / "missing-pinned.jsonc",
    )
    monkeypatch.setattr(
        capsule_mod,
        "snapshot_identity",
        lambda *args, **kwargs: {
            "ok": True,
            "components": {},
            "missing": [],
            "errors": [],
        },
    )
    session_id = "session-claude-fixture"
    projects_root = tmp_path / "projects"
    main = projects_root / "proj" / f"{session_id}.jsonl"
    main.parent.mkdir(parents=True)
    main.write_text(
        (FIXTURES / "claude" / "agent-l4-card.jsonl").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    monkeypatch.setitem(
        transcripts_mod._ADAPTERS,
        "claude",
        lambda: ClaudeTranscriptAdapter(projects_root=projects_root),
    )
    handle = capsule_mod.begin_run(
        "scan-market", "2026-08-27", "claude", {}, session_ref=session_id,
    )
    return handle, main, session_id


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


def test_claude_stats_exposes_single_parse_contract(tmp_path):
    import json

    path = tmp_path / "agent-stats.jsonl"
    rows = [
        {
            "type": "user",
            "timestamp": "2026-09-04T01:00:00Z",
            "message": {"content": "Start the analysis."},
        },
        {
            "type": "assistant",
            "timestamp": "2026-09-04T01:00:01Z",
            "message": {
                "id": "msg-1",
                "model": "claude-opus-5",
                "usage": {
                    "input_tokens": 1,
                    "output_tokens": 2,
                    "cache_read_input_tokens": 3,
                    "cache_creation_input_tokens": 4,
                },
                "content": [{"type": "text", "text": "discarded stream update"}],
            },
        },
        {
            "type": "assistant",
            "timestamp": "2026-09-04T01:00:02Z",
            "attributionAgent": "l4-card",
            "effort": "high",
            "message": {
                "id": "msg-1",
                "model": "claude-opus-5",
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 11,
                    "cache_read_input_tokens": 20,
                    "cache_creation_input_tokens": 7,
                },
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tool-1",
                        "name": "Bash",
                        "input": {"command": "printf fixture"},
                    }
                ],
            },
        },
        {
            "type": "user",
            "timestamp": "2026-09-04T01:00:03Z",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tool-1",
                        "content": "Synthetic tool output.",
                    }
                ]
            },
        },
        {
            "type": "system",
            "timestamp": "2026-09-04T01:00:04Z",
            "compact_boundary": {"preTokens": 210_000},
            "preTokens": 999_999,
        },
        {
            "type": "assistant",
            "timestamp": "2026-09-04T01:00:06Z",
            "message": {
                "id": "msg-2",
                "model": "claude-opus-5",
                "stop_reason": "end_turn",
                "usage": {
                    "input_tokens": 30,
                    "output_tokens": 12,
                    "cache_read_input_tokens": 50,
                    "cache_creation_input_tokens": 11,
                },
                "content": [{"type": "text", "text": "Finished."}],
            },
        },
    ]
    path.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n",
        encoding="utf-8",
    )
    ref = TranscriptRef(engine="claude", path=path, role="subagent")
    adapter = ClaudeTranscriptAdapter(projects_root=tmp_path)

    stats = adapter.stats(ref)

    assert stats.normalized == adapter.normalize(ref)
    assert stats.usage == adapter.usage(ref)
    assert stats.first_context_tokens == 37
    assert stats.context_tokens == (37, 91)
    assert stats.compact_pre_tokens == (210_000,)
    assert stats.suspected_tail == 1
    assert stats.started_at == "2026-09-04T01:00:00Z"
    assert stats.ended_at == "2026-09-04T01:00:06Z"
    assert stats.tool_requests["Bash"] == 1
    assert stats.tool_results["Bash"] == len("Synthetic tool output.")
    assert "discarded stream update" not in str(stats.normalized.items)
    with pytest.raises(TypeError):
        stats.tool_requests["Bash"] = 2
    with pytest.raises(TypeError):
        stats.tool_results["Bash"] = 0


def test_claude_stats_accepts_only_explicit_compact_metadata(tmp_path):
    import json

    path = tmp_path / "agent-compact.jsonl"
    path.write_text(
        json.dumps(
            {
                "type": "system",
                "timestamp": "2026-09-04T01:00:00Z",
                "compactMetadata": {"preTokens": 123_456},
                "preTokens": 999_999,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    stats = ClaudeTranscriptAdapter(projects_root=tmp_path).stats(
        TranscriptRef(engine="claude", path=path)
    )

    assert stats.compact_pre_tokens == (123_456,)


def test_claude_stats_last_error_row_replaces_same_id_stream_update(tmp_path):
    import json

    path = tmp_path / "agent-final-error.jsonl"
    rows = [
        {
            "type": "user",
            "timestamp": "2026-09-04T01:00:00Z",
            "message": {"content": "Start."},
        },
        {
            "type": "assistant",
            "timestamp": "2026-09-04T01:00:01Z",
            "message": {
                "id": "same",
                "model": "claude-opus-5",
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 11,
                    "cache_read_input_tokens": 20,
                    "cache_creation_input_tokens": 7,
                },
                "content": [{"type": "text", "text": "stale success"}],
            },
        },
        {
            "type": "assistant",
            "timestamp": "2026-09-04T01:00:02Z",
            "error": "rate_limit",
            "isApiErrorMessage": True,
            "message": {"id": "same", "model": "<synthetic>", "content": []},
        },
    ]
    path.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n",
        encoding="utf-8",
    )
    ref = TranscriptRef(engine="claude", path=path)
    adapter = ClaudeTranscriptAdapter(projects_root=tmp_path)

    stats = adapter.stats(ref)

    assert [item.kind for item in stats.normalized.items] == ["message", "error"]
    assert "stale success" not in str(stats.normalized.items)
    assert stats.usage.messages == 0
    assert stats.usage.input == 0
    assert stats.context_tokens == ()
    assert stats.normalized == adapter.normalize(ref)
    assert stats.usage == adapter.usage(ref)


def test_claude_stats_dedups_tools_by_tool_use_id_not_message_id(tmp_path):
    import json

    path = tmp_path / "agent-tools.jsonl"
    rows = [
        {
            "type": "assistant",
            "timestamp": "2026-09-04T01:00:00Z",
            "message": {
                "id": "stream",
                "model": "claude-opus-5",
                "usage": {"input_tokens": 1},
                "content": [
                    {"type": "tool_use", "id": "tool-1", "name": "Bash", "input": {}}
                ],
            },
        },
        {
            "type": "assistant",
            "timestamp": "2026-09-04T01:00:01Z",
            "message": {
                "id": "stream",
                "model": "claude-opus-5",
                "usage": {"input_tokens": 2},
                "content": [
                    {"type": "tool_use", "id": "tool-1", "name": "Bash", "input": {}}
                ],
            },
        },
        {
            "type": "assistant",
            "timestamp": "2026-09-04T01:00:02Z",
            "message": {
                "id": "stream",
                "model": "claude-opus-5",
                "usage": {"input_tokens": 3},
                "content": [
                    {"type": "tool_use", "id": "tool-2", "name": "Read", "input": {}}
                ],
            },
        },
        {
            "type": "user",
            "timestamp": "2026-09-04T01:00:03Z",
            "message": {
                "content": [
                    {"type": "tool_result", "tool_use_id": "tool-1", "content": "partial"}
                ]
            },
        },
        {
            "type": "user",
            "timestamp": "2026-09-04T01:00:04Z",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tool-1",
                        "content": "complete one",
                    },
                    {
                        "type": "tool_result",
                        "tool_use_id": "tool-2",
                        "content": "complete two",
                    },
                ]
            },
        },
    ]
    path.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n",
        encoding="utf-8",
    )

    stats = ClaudeTranscriptAdapter(projects_root=tmp_path).stats(
        TranscriptRef(engine="claude", path=path)
    )

    assert stats.tool_requests == {"Bash": 1, "Read": 1}
    assert stats.tool_results == {
        "Bash": len("complete one"),
        "Read": len("complete two"),
    }
    assert stats.usage.messages == 1
    assert stats.context_tokens == (3,)


def test_claude_stats_uses_chronological_valid_timestamp_bounds(tmp_path):
    import json

    path = tmp_path / "agent-time.jsonl"
    rows = [
        {"type": "system", "timestamp": "2026-09-04T01:00:03Z"},
        {"type": "system", "timestamp": "not-a-timestamp"},
        {"type": "system"},
        {"type": "system", "timestamp": "2026-09-04T01:00:01+00:00"},
        {"type": "system", "timestamp": "2026-09-04T09:00:02+08:00"},
    ]
    path.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n",
        encoding="utf-8",
    )

    stats = ClaudeTranscriptAdapter(projects_root=tmp_path).stats(
        TranscriptRef(engine="claude", path=path)
    )

    assert stats.started_at == "2026-09-04T01:00:01+00:00"
    assert stats.ended_at == "2026-09-04T01:00:03Z"


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
    assert isinstance(
        ClaudeTranscriptAdapter(), transcripts_mod.StatsTranscriptAdapter
    )
    assert transcripts_mod.TranscriptStats is TranscriptStats
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
        "tool_request",  # web_search_call (D6.4②)
        "tool_result",   # web_search_end (D6.4②)
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
    # Also strip the fixture's synthetic web_search pair (D6.4②) — this test wants
    # exactly one INCOMPLETE row from the custom_tool_call whose output was cut,
    # not a second COMPLETED row from the unrelated web_search request/result.
    trimmed = [
        line
        for line in lines
        if "custom_tool_call_output" not in line and "web_search" not in line
    ]
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
    assert isinstance(adapter_for("codex"), TranscriptAdapter)


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


# --- D6.4: adapter four-fix batch --------------------------------------------


def test_collect_run_reads_contract_session_ref(claude_run):
    """`collect_run` must read `session_ref` off the run's own contract (D6.4①).

    Before this fix, `usage_harvest.collect_run` always built
    `RunIdentity(session_ref=None)` — `ClaudeTranscriptAdapter.locate` short-circuits
    to `[]` whenever `session_ref` is falsy, so the Claude engine's `collect_run`
    never actually located a transcript for any run, ever.
    """
    from autoresearch.trace import usage_harvest as U

    handle, main, session_id = claude_run

    rows = U.collect_run(handle.run_id, engine="claude")

    assert len(rows) == 1
    assert rows[0]["role"] == "main"
    assert rows[0]["status"] != "UNMEASURED"
    assert rows[0]["path"] == str(main)


def test_collect_run_without_session_ref_is_unmeasured_not_empty(tmp_path, monkeypatch):
    """A claude run whose contract never got a `session_ref` must still leave a
    row — the parenthetical half of D6.4①: `[]` reads upstream as "0 transcripts
    = free", which is exactly the false-green this whole capsule exists to remove.
    """
    from autoresearch.trace import capsule as local_capsule_mod, usage_harvest as U
    from tests.forensic_fixtures import FIXTURE_DATE, FIXTURE_NOW

    monkeypatch.setattr(ws, "ENGINE", "claude")
    monkeypatch.setattr(ws, "context_root", lambda: tmp_path / "context_claude")
    monkeypatch.setattr(ws, "reports_root", lambda: tmp_path / "reports_claude")
    monkeypatch.delenv("AUTORESEARCH_RUN_ID", raising=False)
    # 2026-09-03:`begin_run` 现在会自绑 harness session(`CLAUDE_CODE_SESSION_ID`)。
    # 本用例要的正是「没人绑成功」那条腿,所以必须自己把环境按住 —— 否则它在真机上
    # (开发者自己的 Claude 会话里)会悄悄变成「绑上了」而失去鉴别力。
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    monkeypatch.setattr(
        "autoresearch.scan.user_config.DEFAULT_PINNED_PATH",
        tmp_path / "missing-pinned.jsonc",
    )
    monkeypatch.setattr(
        local_capsule_mod,
        "snapshot_identity",
        lambda *args, **kwargs: {"ok": True, "components": {}, "missing": [], "errors": []},
    )
    handle = local_capsule_mod.begin_run(
        "scan-market", FIXTURE_DATE, "claude", {}, now=FIXTURE_NOW,
    )
    assert handle.contract.session_ref is None

    rows = U.collect_run(handle.run_id, engine="claude")

    assert len(rows) == 1
    assert rows[0]["status"] == "UNMEASURED"
    assert rows[0]["estimated_usd"] is None


def test_codex_web_search_becomes_tool_items(codex_run):
    """Codex `web_search_call`/`web_search_end` must survive normalize (D6.4②).

    `web_search_call` used to sit in `_SKIPPED_RESPONSE_ITEMS` — the harness's own
    network calls left zero trace.  This locks the request/result pair the fixture
    (`tests/trace/fixtures/codex/rollout.jsonl`) now carries, then proves the pair
    actually reaches `external_tools.jsonl` through the real production path
    (`bind_transcript` → `materialize_transcripts` → `capsule._external_tool_rows`).
    """
    handle, source = codex_run
    ref = TranscriptRef(engine="codex", path=source, role="l4-intel")

    normalized = CodexTranscriptAdapter().normalize(ref)

    requests = [item for item in normalized.items if item.kind == "tool_request"
                and item.payload.get("tool_name") == "web_search"]
    results = [item for item in normalized.items if item.kind == "tool_result"
               and item.payload.get("tool_call_id") == "ws-fixture-1"]
    assert len(requests) == 1
    assert requests[0].payload["tool_call_id"] == "ws-fixture-1"
    # `NormalizedItem.payload` freezes lists into tuples (base.py `_freeze`).
    assert requests[0].payload["input"] == {
        "type": "search", "queries": ("synthetic web search query",),
    }
    assert len(results) == 1
    assert results[0].payload["content"]["query"] == "synthetic web search query"

    bind_transcript(
        handle.run_id,
        source,
        role="l4-intel",
        subject="600000",
        invocation_id="agent-l4-intel-600000-2",
    )
    materialize_transcripts(handle.run_id)

    rows = read_jsonl(handle.capsule / "lineage/external_tools.jsonl")
    web_rows = [row for row in rows if row["tool_name"] == "web_search"]
    assert len(web_rows) == 1
    assert web_rows[0]["status"] == "COMPLETED"
    assert web_rows[0]["tool_call_id"] == "ws-fixture-1"
    assert blob_path(handle.capsule, web_rows[0]["result_hash"]).is_file()


def test_web_search_not_in_local_tool_names():
    """Premise check (D6.4② Step 1): `web_search` must never be in
    `LOCAL_TOOL_NAMES`, or `is_external_tool("web_search")` would be `False` and
    the whole point of normalizing it would be silently defeated downstream."""
    from autoresearch.trace.transcripts.base import LOCAL_TOOL_NAMES, is_external_tool

    assert "web_search" not in LOCAL_TOOL_NAMES
    assert is_external_tool("web_search") is True


def test_tool_results_spill_archived(claude_run):
    """`<session>/tool-results/*` must be archived alongside a bound `role=main`
    Claude transcript (D6.4③) — the harness spills large tool outputs there, and
    `ClaudeTranscriptAdapter.locate` never enumerates that directory.
    """
    handle, main, session_id = claude_run
    spill_dir = main.parent / session_id / "tool-results"
    spill_dir.mkdir(parents=True)
    (spill_dir / "x.txt").write_text("Synthetic large tool output.\n", encoding="utf-8")
    bind_transcript(handle.run_id, main, role="main", invocation_id="main-session")

    materialize_transcripts(handle.run_id)

    archived = handle.capsule / "agents/tool_results/x.txt.gz"
    assert archived.is_file()
    import gzip

    assert gzip.decompress(archived.read_bytes()) == b"Synthetic large tool output.\n"


def test_tool_results_spill_not_archived_for_non_main_role(codex_run):
    """The spill archive is scoped to `role=main` Claude bindings only (D6.4③) —
    a codex/subagent binding must not attempt (or need) it."""
    handle, source = codex_run
    bind_transcript(
        handle.run_id,
        source,
        role="l4-card",
        subject="600000",
        invocation_id="agent-l4-card-600000-3",
    )

    materialize_transcripts(handle.run_id)

    assert not (handle.capsule / "agents/tool_results").exists()


# --- Task 1: observation vocabulary + one pure classifier (spec §3.1) --------


def _tool_request(tool_name: str, **extra) -> NormalizedItem:
    return NormalizedItem(
        index=0, kind="tool_request", payload={"tool_name": tool_name, **extra}
    )


def _tool_result(*, is_error: bool = False, **extra) -> NormalizedItem:
    return NormalizedItem(
        index=1, kind="tool_result", payload={"is_error": is_error, **extra}
    )


def test_observation_kinds_excludes_not_observed():
    """`NOT_OBSERVED` is a view's judgment about an absence (spec §3.1 last
    paragraph, Task 5's chain_view render), never a transcript event a
    classifier could emit -- it must not be a member of the vocabulary."""
    assert "NOT_OBSERVED" not in OBSERVATION_KINDS


# --- Task 1: binding_status / segment_quality / coverage keys (spec §4.5) ----


def test_binding_statuses_match_spec():
    assert BINDING_STATUSES == (
        "BOUND", "UNVERIFIED_BY_PRODUCT", "AMBIGUOUS", "GONE", "ERROR",
    )


def test_segment_qualities_match_spec():
    assert SEGMENT_QUALITIES == ("complete", "partial", "interleaved", "unknown")


def test_binding_status_and_segment_quality_are_modeled_separately():
    """Spec §4.5: "绑定报告采用两个正交字段" -- whether a call is bound and how
    complete its segment is are independent axes; one vocabulary must not be
    derivable from, or collapsed into, the other."""
    assert BINDING_STATUSES != SEGMENT_QUALITIES
    assert not set(BINDING_STATUSES) & set(SEGMENT_QUALITIES)
    assert type(BINDING_STATUSES) is not type(SEGMENT_QUALITIES) or (
        BINDING_STATUSES is not SEGMENT_QUALITIES
    )


def test_coverage_keys_match_spec_minimum():
    """Spec §4.5: "报告 coverage 至少包含" these nine keys -- the minimum shape
    every later task's coverage dict must carry, named once so Task 3/4/8/9
    don't each retype the same nine strings."""
    assert set(COVERAGE_KEYS) == {
        "expected", "accounted", "bound", "unverified", "ambiguous",
        "gone", "errors", "unexpected", "denominator_quality",
    }


@pytest.mark.parametrize("tool_name", ["Read", "read_file"])
def test_classify_observation_read_error_is_not_read_succeeded(tool_name):
    """Required behaviour: an errored Read response must never classify as
    READ_SUCCEEDED (spec §3.1 READ_FAILED row)."""
    kind = classify_observation(_tool_request(tool_name), _tool_result(is_error=True))

    assert kind == "READ_FAILED"
    assert kind != "READ_SUCCEEDED"


@pytest.mark.parametrize(
    "result", [None, _tool_result(is_error=False), _tool_result(is_error=True)]
)
def test_classify_observation_glob_is_always_discovered(result):
    """Required behaviour: Glob can only ever classify as DISCOVERED, regardless
    of whether/how the (irrelevant) result resolved (spec §3.1 DISCOVERED
    row: "只说发现,不说读到正文")."""
    assert classify_observation(_tool_request("Glob"), result) == "DISCOVERED"


def test_classify_observation_read_success_is_read_succeeded():
    kind = classify_observation(_tool_request("Read"), _tool_result(is_error=False))

    assert kind == "READ_SUCCEEDED"


def test_classify_observation_read_with_no_result_is_read_requested():
    assert classify_observation(_tool_request("Read"), None) == "READ_REQUESTED"


def test_classify_observation_grep_success_is_partial_not_succeeded():
    """A grep hit proves only that its matched lines existed, never that the
    rest of the file was seen (spec §3.1 READ_PARTIAL row) -- it must never
    rise to READ_SUCCEEDED the way a plain Read does."""
    kind = classify_observation(_tool_request("grep"), _tool_result(is_error=False))

    assert kind == "READ_PARTIAL"


def test_classify_observation_grep_error_is_read_failed():
    kind = classify_observation(_tool_request("grep"), _tool_result(is_error=True))

    assert kind == "READ_FAILED"


@pytest.mark.parametrize(
    "result,expected",
    [
        (None, "WRITE_REQUESTED"),
        (_tool_result(is_error=False), "WRITE_SUCCEEDED"),
        (_tool_result(is_error=True), "WRITE_FAILED"),
    ],
)
def test_classify_observation_write_family_round_trip(result, expected):
    assert classify_observation(_tool_request("Write"), result) == expected


@pytest.mark.parametrize(
    "result,expected",
    [
        (None, "SEARCH_REQUESTED"),
        (_tool_result(is_error=False), "SEARCH_SUCCEEDED"),
        (_tool_result(is_error=True), "SEARCH_FAILED"),
    ],
)
def test_classify_observation_external_tool_is_search_family(result, expected):
    """Any tool name outside the local read/write/discover verbs falls into the
    same "external, evidence-bearing" bucket `is_external_tool` already fails
    unknown names open into (base.py module docstring)."""
    assert classify_observation(_tool_request("WebSearch"), result) == expected


@pytest.mark.parametrize("tool_name", ["bash", "Task", "TodoWrite"])
def test_classify_observation_rejects_local_tools_outside_its_domain(tool_name):
    """`bash`/`Task`/`TodoWrite` are local orchestration verbs, not file
    evidence.  Classifying `bash` would mean guessing at shell command text --
    exactly what this helper must refuse to do rather than interpret."""
    with pytest.raises(ValueError, match="no observation.kind"):
        classify_observation(_tool_request(tool_name), None)


def test_classify_observation_rejects_a_non_tool_request_item():
    message = NormalizedItem(index=0, kind="message", payload={"text": "hi"})

    with pytest.raises(ValueError, match="tool_request"):
        classify_observation(message, None)


def test_classify_observation_rejects_a_non_tool_result_item():
    not_a_result = NormalizedItem(index=1, kind="message", payload={})

    with pytest.raises(ValueError, match="tool_result"):
        classify_observation(_tool_request("Read"), not_a_result)


def test_classify_observation_rejects_a_blank_tool_name():
    blank = NormalizedItem(index=0, kind="tool_request", payload={"tool_name": ""})

    with pytest.raises(ValueError, match="tool_name"):
        classify_observation(blank, None)


# --- Task 1: hash families (spec §3.2) ---------------------------------------


def test_tool_response_digest_is_stable_and_key_order_independent():
    """Required behaviour: a structured response's digest must be stable --
    canonical-JSON hashing makes it independent of dict key insertion order."""
    first = hash_tool_response({"b": 2, "a": 1})
    second = hash_tool_response({"a": 1, "b": 2})
    third = hash_tool_response({"a": 1, "b": 2})

    assert first.sha256 == second.sha256 == third.sha256
    assert first.encoding == "canonical_json"


def test_tool_response_digest_records_the_string_encoding_rule():
    digest = hash_tool_response("plain text response")

    assert digest.encoding == "utf8_text"
    assert digest.sha256 == hash_tool_response("plain text response").sha256


def test_tool_response_digest_differs_from_a_differently_encoded_response():
    """Required behaviour: the digest must record which representation rule
    produced it -- a string and a structured value that merely *contain* the
    same text are not the same response."""
    structured = hash_tool_response({"text": "plain text response"})
    string = hash_tool_response("plain text response")

    assert structured.sha256 != string.sha256
    assert structured.encoding != string.encoding


def test_tool_response_and_artifact_digests_are_distinct_types():
    """Required behaviour: the structured-response digest must be separate
    from the file-digest field -- a caller cannot pass one where the other is
    meant without an explicit, visible type mismatch."""
    response_digest = hash_tool_response("same bytes")
    artifact_digest = hash_artifact_bytes(b"same bytes")

    assert type(response_digest) is ToolResponseDigest
    assert type(artifact_digest) is ArtifactDigest
    assert not isinstance(response_digest, ArtifactDigest)
    assert not isinstance(artifact_digest, ToolResponseDigest)


def test_hash_artifact_bytes_rejects_non_bytes_input():
    """`artifact_sha256` is defined over exact file bytes (spec §3.2) -- a
    `str` must be refused, not silently UTF-8-encoded the way a tool response
    would be, or the two families become interchangeable by accident."""
    with pytest.raises(TypeError, match="bytes"):
        hash_artifact_bytes("not bytes")  # type: ignore[arg-type]


def test_source_prefix_and_archive_digests_are_also_distinct_value_types():
    """The two snapshot-only hash families (populated by a later task's
    `snapshot.capture_snapshot`) stay distinct from each other and from the
    two in-memory ones defined here."""
    prefix = SourcePrefixDigest(sha256="a" * 64, byte_count=10)
    archive = ArchiveDigest(sha256="a" * 64, byte_count=10)

    assert type(prefix) is not type(archive)
    assert not isinstance(prefix, ArchiveDigest)
    assert not isinstance(archive, SourcePrefixDigest)


# --- Task 1: transcript schema version (bullet 3) ----------------------------


def test_known_transcript_schema_versions_are_derived_from_current():
    assert CURRENT_TRANSCRIPT_SCHEMA_VERSION == 2
    assert {1, 2} == KNOWN_TRANSCRIPT_SCHEMA_VERSIONS


def test_known_transcript_schema_versions_still_read():
    assert require_known_transcript_schema_version({"schema_version": 1}) == 1
    assert (
        require_known_transcript_schema_version(
            {"schema_version": CURRENT_TRANSCRIPT_SCHEMA_VERSION}
        )
        == CURRENT_TRANSCRIPT_SCHEMA_VERSION
    )


def test_unknown_transcript_schema_version_is_not_treated_as_empty_success():
    """Required behaviour: an unrecognized `schema_version` must be refused,
    never silently read as an empty/successful result (spec: "不把未知版本解释
    为空成功")."""
    with pytest.raises(TranscriptUnreadable, match="schema_version"):
        require_known_transcript_schema_version(
            {"schema_version": 999, "invocations": []}
        )


def test_missing_transcript_schema_version_is_also_refused():
    with pytest.raises(TranscriptUnreadable, match="schema_version"):
        require_known_transcript_schema_version({"invocations": []})


# --- Task 1: stage values come from the contract vocabulary ------------------


def test_bind_transcript_stage_is_a_known_pipeline_stage(codex_run):
    from autoresearch.contracts.stages import STAGES

    handle, source = codex_run
    row = bind_transcript(
        handle.run_id, source, role="l4-card", subject="600000",
        invocation_id="agent-l4-card-600000-stage",
    )

    assert row["stage"] in STAGES


def test_materialize_agent_index_event_stage_is_a_known_pipeline_stage(
    codex_run, monkeypatch
):
    """`materialize_agent_index`'s own `TRANSCRIPTS_MATERIALIZED` event must
    carry a stage name the contract vocabulary knows, not a hand-picked
    literal -- the fallback used to be `"cp7"`, which `contracts.stages.STAGES`
    has never heard of."""
    from autoresearch.contracts.stages import STAGES

    monkeypatch.delenv("AUTORESEARCH_STAGE", raising=False)
    handle, source = codex_run

    materialize_agent_index(handle.run_id)

    events = read_jsonl(handle.capsule / "events/events.jsonl")
    materialized = next(
        e for e in events if e["event_type"] == "TRANSCRIPTS_MATERIALIZED"
    )
    assert materialized["stage"] in STAGES


def test_bindings_and_index_are_written_at_the_current_schema_version(codex_run):
    import json

    handle, source = codex_run
    bind_transcript(
        handle.run_id, source, role="l4-card", subject="600000",
        invocation_id="agent-l4-card-600000-schema",
    )
    binding_rows = read_jsonl(handle.capsule / "agents/bindings.jsonl")
    assert binding_rows[-1]["schema_version"] == CURRENT_TRANSCRIPT_SCHEMA_VERSION

    index = materialize_agent_index(handle.run_id)
    assert index["schema_version"] == CURRENT_TRANSCRIPT_SCHEMA_VERSION

    normalized_path = next((handle.capsule / "agents/normalized").glob("*.json"))
    normalized = json.loads(normalized_path.read_text(encoding="utf-8"))
    assert normalized["schema_version"] == CURRENT_TRANSCRIPT_SCHEMA_VERSION
