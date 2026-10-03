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
    ObservedOperation,
    RunIdentity,
    SourcePrefixDigest,
    ToolResponseDigest,
    TranscriptAdapter,
    TranscriptRef,
    TranscriptStats,
    TranscriptUnreadable,
    classify_observation,
    extract_operations,
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

    # Raw is keyed by snapshot_id (a content hash), not invocation_id (2026-09-12
    # Task 2: "唯一 raw 数由唯一快照数决定") -- resolve the path through the index,
    # the same way spec §5.1 says existing raw-path consumers must.
    index = materialize_agent_index(handle.run_id)
    raw_relative = index["invocations"][0]["raw"]
    archive = handle.capsule / raw_relative
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
    # Both are plain `tuple[str, ...]` (matching OBSERVATION_KINDS' own style), so
    # there is no separate *type* to compare -- `type(x) is not type(y)` would be
    # vacuously False for any two tuples and is deliberately not asserted here.
    # This is the one remaining clause that can actually fail: a copy-paste bug
    # that points SEGMENT_QUALITIES at the same tuple object as BINDING_STATUSES.
    assert BINDING_STATUSES is not SEGMENT_QUALITIES


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


# --- Task 2: stats_from_rows / extract_operations (O01/O02) -----------------


def _claude_row(row_type: str, **fields) -> dict:
    return {"type": row_type, **fields}


def test_claude_stats_from_rows_classifies_read_glob_grep_and_failure_separately(
    tmp_path,
):
    """O01: Read failure, Glob, Grep, and a paginated Read are labeled
    READ_FAILED / DISCOVERED / READ_PARTIAL / READ_PARTIAL respectively --
    never mixed into one summary caliber."""
    import json

    rows = [
        _claude_row(
            "assistant",
            message={
                "id": "m1",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "call-read-fail",
                        "name": "Read",
                        "input": {"file_path": "/repo/missing.py"},
                    }
                ],
            },
        ),
        _claude_row(
            "user",
            message={
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "call-read-fail",
                        "content": "Error: file not found",
                        "is_error": True,
                    }
                ]
            },
        ),
        _claude_row(
            "assistant",
            message={
                "id": "m2",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "call-glob",
                        "name": "Glob",
                        "input": {"pattern": "*.py", "path": "/repo"},
                    }
                ],
            },
        ),
        _claude_row(
            "user",
            message={
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "call-glob",
                        "content": "/repo/a.py\n/repo/b.py",
                    }
                ]
            },
        ),
        _claude_row(
            "assistant",
            message={
                "id": "m3",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "call-grep",
                        "name": "Grep",
                        "input": {"pattern": "TODO", "path": "/repo"},
                    }
                ],
            },
        ),
        _claude_row(
            "user",
            message={
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "call-grep",
                        "content": "/repo/a.py:3:TODO fix me",
                    }
                ]
            },
        ),
        _claude_row(
            "assistant",
            message={
                "id": "m4",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "call-read-paged",
                        "name": "Read",
                        "input": {
                            "file_path": "/repo/big.py",
                            "offset": 100,
                            "limit": 50,
                        },
                    }
                ],
            },
        ),
        _claude_row(
            "user",
            message={
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "call-read-paged",
                        "content": "   100\tsome line\n",
                    }
                ]
            },
        ),
    ]
    ref = TranscriptRef(engine="claude", path=tmp_path / "unused.jsonl", role="subagent")
    adapter = ClaudeTranscriptAdapter()

    stats = adapter.stats_from_rows(rows, ref)
    by_call = {op.call_id: op for op in stats.operations}

    assert by_call["call-read-fail"].kind == "READ_FAILED"
    assert by_call["call-glob"].kind == "DISCOVERED"
    assert by_call["call-glob"].path == "/repo"
    assert by_call["call-grep"].kind == "READ_PARTIAL"
    assert by_call["call-read-paged"].kind == "READ_PARTIAL"
    assert by_call["call-read-paged"].path == "/repo/big.py"
    assert by_call["call-read-paged"].path_source == "tool_input"
    # Every operation carries a response digest (it has a correlated result);
    # none of the four is missing one, and none is silently promoted to a
    # full read -- summary caliber (kind) stays distinct per call.
    assert {op.kind for op in by_call.values()} == {
        "READ_FAILED",
        "DISCOVERED",
        "READ_PARTIAL",
    }
    assert json.dumps([op.kind for op in stats.operations])  # kinds are JSON-safe strings


def test_claude_stats_from_rows_skips_local_orchestration_verbs_not_guessed(tmp_path):
    """A `Bash` call is refused by classify_observation (never interpreted) --
    it must not appear as an ObservedOperation, while its raw round trip
    still exists in normalized.items (brief bullet 5)."""
    rows = [
        _claude_row(
            "assistant",
            message={
                "id": "m1",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "call-bash",
                        "name": "Bash",
                        "input": {"command": "cat /etc/passwd"},
                    }
                ],
            },
        ),
        _claude_row(
            "user",
            message={
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "call-bash",
                        "content": "root:x:0:0",
                    }
                ]
            },
        ),
    ]
    ref = TranscriptRef(engine="claude", path=tmp_path / "unused.jsonl", role="subagent")
    adapter = ClaudeTranscriptAdapter()

    stats = adapter.stats_from_rows(rows, ref)

    assert stats.operations == ()
    assert [item.kind for item in stats.normalized.items] == [
        "tool_request",
        "tool_result",
    ]


def test_claude_stats_from_rows_write_then_edit_only_write_earns_an_artifact_hash(
    tmp_path,
):
    """O02: a Write followed by an Edit on the same path -- both classify as
    WRITE_SUCCEEDED, but only the Write's *declared, complete* content earns
    an artifact hash; the Edit (a diff, old_string/new_string) never gets
    one fabricated (spec §3.2)."""
    import hashlib

    rows = [
        _claude_row(
            "assistant",
            message={
                "id": "m1",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "call-write",
                        "name": "Write",
                        "input": {
                            "file_path": "/repo/out.md",
                            "content": "first version",
                        },
                    }
                ],
            },
        ),
        _claude_row(
            "user",
            message={
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "call-write",
                        "content": "File written successfully.",
                    }
                ]
            },
        ),
        _claude_row(
            "assistant",
            message={
                "id": "m2",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "call-edit",
                        "name": "Edit",
                        "input": {
                            "file_path": "/repo/out.md",
                            "old_string": "first version",
                            "new_string": "second version",
                        },
                    }
                ],
            },
        ),
        _claude_row(
            "user",
            message={
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "call-edit",
                        "content": "The file /repo/out.md has been updated.",
                    }
                ]
            },
        ),
    ]
    ref = TranscriptRef(engine="claude", path=tmp_path / "unused.jsonl", role="subagent")
    adapter = ClaudeTranscriptAdapter()

    stats = adapter.stats_from_rows(rows, ref)
    by_call = {op.call_id: op for op in stats.operations}

    assert by_call["call-write"].kind == "WRITE_SUCCEEDED"
    assert by_call["call-edit"].kind == "WRITE_SUCCEEDED"
    assert by_call["call-write"].artifact is not None
    assert by_call["call-write"].artifact.sha256 == hashlib.sha256(
        b"first version"
    ).hexdigest()
    assert by_call["call-edit"].artifact is None
    # Both target the same product path -- a later task compares by that
    # path plus operation order, never by call_id alone.
    assert by_call["call-write"].path == by_call["call-edit"].path == "/repo/out.md"


def _codex_row(row_type: str, ordinal: int, payload: dict, timestamp: str) -> dict:
    return {"type": row_type, "ordinal": ordinal, "payload": payload, "timestamp": timestamp}


def test_codex_stats_from_rows_apply_patch_never_earns_a_full_file_artifact_hash(
    tmp_path,
):
    """O02: Codex's apply_patch is patch-diff-only -- WRITE_SUCCEEDED, but
    never a full-file artifact hash (spec §3.2)."""
    rows = [
        _codex_row(
            "response_item",
            0,
            {
                "type": "custom_tool_call",
                "id": "ctc-1",
                "call_id": "call-patch",
                "name": "apply_patch",
                "input": "*** Update File: /repo/out.md\n@@\n-old\n+new\n",
            },
            "2026-09-12T01:00:00.000Z",
        ),
        _codex_row(
            "response_item",
            1,
            {
                "type": "custom_tool_call_output",
                "id": "ctco-1",
                "call_id": "call-patch",
                "output": [{"type": "output_text", "text": "Done"}],
            },
            "2026-09-12T01:00:01.000Z",
        ),
    ]
    ref = TranscriptRef(engine="codex", path=tmp_path / "unused.jsonl", role="subagent")
    adapter = CodexTranscriptAdapter()

    stats = adapter.stats_from_rows(rows, ref)
    ops = {op.call_id: op for op in stats.operations}

    assert ops["call-patch"].kind == "WRITE_SUCCEEDED"
    assert ops["call-patch"].artifact is None


def test_codex_stats_from_rows_skips_exec_never_interprets_shell_text(tmp_path):
    """Codex's real read/search operations mostly arrive wrapped in `exec`
    (a JS snippet around a shell command) -- classify_observation refuses
    this local orchestration verb rather than guess at the command text, so
    no ObservedOperation is produced for it."""
    rows = [
        _codex_row(
            "response_item",
            0,
            {
                "type": "custom_tool_call",
                "id": "ctc-1",
                "call_id": "call-exec",
                "name": "exec",
                "input": "await shell(['cat', '/repo/out.md'])",
            },
            "2026-09-12T01:00:00.000Z",
        ),
        _codex_row(
            "response_item",
            1,
            {
                "type": "custom_tool_call_output",
                "id": "ctco-1",
                "call_id": "call-exec",
                "output": [{"type": "output_text", "text": "file contents"}],
            },
            "2026-09-12T01:00:01.000Z",
        ),
    ]
    ref = TranscriptRef(engine="codex", path=tmp_path / "unused.jsonl", role="subagent")
    adapter = CodexTranscriptAdapter()

    stats = adapter.stats_from_rows(rows, ref)

    assert stats.operations == ()


def test_codex_stats_from_rows_web_search_is_search_family(tmp_path):
    """A recognized external tool (not in LOCAL_TOOL_NAMES) classifies as the
    SEARCH family -- is_external_tool's existing fail-open bucket, not a
    refusal."""
    rows = [
        _codex_row(
            "response_item",
            0,
            {"type": "web_search_call", "id": "ws-1", "action": {"type": "search"}},
            "2026-09-12T01:00:00.000Z",
        ),
        _codex_row(
            "event_msg",
            1,
            {
                "type": "web_search_end",
                "call_id": "ws-1",
                "query": "fixture query",
                "results": [{"type": "text_result", "text": "..."}],
            },
            "2026-09-12T01:00:01.000Z",
        ),
    ]
    ref = TranscriptRef(engine="codex", path=tmp_path / "unused.jsonl", role="subagent")
    adapter = CodexTranscriptAdapter()

    stats = adapter.stats_from_rows(rows, ref)
    ops = {op.call_id: op for op in stats.operations}

    assert ops["ws-1"].kind == "SEARCH_SUCCEEDED"


def test_extract_operations_never_drops_the_five_bundled_facts(tmp_path):
    """Brief bullet 2's non-conflation guardrail, exercised directly: every
    ObservedOperation must be able to carry call_id, kind (operation result
    incl. the partial flag), path + path_source, and item_index all at once.

    Fix round 1 (2026-09-13): item_index is the position in this call's own
    NormalizedTranscript.items, not a snapshot/row reference -- see
    test_operation_item_index_is_not_a_row_index_when_rows_are_skipped_or_expanded
    for the fixture that proves the two sequences differ."""
    rows = [
        _claude_row(
            "assistant",
            message={
                "id": "m1",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "call-1",
                        "name": "Read",
                        "input": {"file_path": "/a.py"},
                    }
                ],
            },
        ),
        _claude_row(
            "user",
            message={
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "call-1",
                        "content": "print(1)",
                    }
                ]
            },
        ),
    ]
    ref = TranscriptRef(engine="claude", path=tmp_path / "unused.jsonl", role="subagent")
    stats = ClaudeTranscriptAdapter().stats_from_rows(rows, ref)

    assert len(stats.operations) == 1
    op = stats.operations[0]
    assert isinstance(op, ObservedOperation)
    assert op.call_id == "call-1"
    assert op.kind == "READ_SUCCEEDED"
    assert op.path == "/a.py"
    assert op.path_source == "tool_input"
    assert op.item_index == stats.normalized.items[
        [item.kind for item in stats.normalized.items].index("tool_request")
    ].index
    assert op.response is not None


def test_operation_item_index_is_not_a_row_index_when_rows_are_skipped_or_expanded(
    tmp_path,
):
    """Fix round 1, Finding 1: item_index is a position in this call's
    NormalizedTranscript.items, never an index into a TranscriptSnapshot's
    rows -- rows and items are different sequences of different lengths.

    This fixture has both properties the review required in one shot: two
    superseded streaming rows (rows 0-1, same message id as row 2) that
    contribute *zero* items each, and one row (row 3) whose text-plus-
    tool_use content expands into *two* items. The Read operation's request
    therefore lands at item_index 2, while its actual source is rows[3] --
    provably different integers, not a coincidence of small counts.
    """
    rows = [
        _claude_row(  # row 0: superseded, contributes 0 items
            "assistant",
            message={"id": "dup", "content": [{"type": "text", "text": "stale v1"}]},
        ),
        _claude_row(  # row 1: superseded, contributes 0 items
            "assistant",
            message={"id": "dup", "content": [{"type": "text", "text": "stale v2"}]},
        ),
        _claude_row(  # row 2: final "dup" -> 1 item (item_index 0)
            "assistant",
            message={
                "id": "dup",
                "content": [{"type": "text", "text": "final"}],
                "stop_reason": "end_turn",
            },
        ),
        _claude_row(  # row 3: expands into 2 items (item_index 1 message, 2 tool_request)
            "assistant",
            message={
                "id": "m2",
                "content": [
                    {"type": "text", "text": "checking"},
                    {
                        "type": "tool_use",
                        "id": "call-2",
                        "name": "Read",
                        "input": {"file_path": "/b.py"},
                    },
                ],
            },
        ),
        _claude_row(  # row 4: tool_result for call-2 -> item_index 3
            "user",
            message={
                "content": [
                    {"type": "tool_result", "tool_use_id": "call-2", "content": "ok"}
                ]
            },
        ),
    ]
    ref = TranscriptRef(engine="claude", path=tmp_path / "unused.jsonl", role="subagent")
    stats = ClaudeTranscriptAdapter().stats_from_rows(rows, ref)

    # 5 rows -> 4 items: the two superseded rows vanish, the expanding row
    # accounts for the surplus.
    assert len(rows) == 5
    assert len(stats.normalized.items) == 4
    assert len(stats.operations) == 1
    op = stats.operations[0]
    assert op.kind == "READ_SUCCEEDED"

    true_source_row_position = 3  # rows[3] is the row whose tool_use this came from
    assert op.item_index == 2
    assert op.item_index != true_source_row_position

    # item_index correctly resolves within *items*...
    assert stats.normalized.items[op.item_index].kind == "tool_request"
    # ...but the same integer used as an index into `rows` selects an
    # entirely different, wrong row: rows[2] is the message-only "final" row
    # that never mentions this call at all.
    wrong_row = rows[op.item_index]
    assert wrong_row["message"]["id"] == "dup"
    assert "call-2" not in str(wrong_row)


def test_extract_operations_is_the_same_function_both_adapters_call():
    """Ruling 1 ("do not re-derive observation kinds"): there is exactly one
    walker from NormalizedItems to ObservedOperations, imported by both
    adapter modules rather than reimplemented per engine."""
    import autoresearch.trace.transcripts.claude as claude_mod
    import autoresearch.trace.transcripts.codex as codex_mod

    assert claude_mod.extract_operations is extract_operations
    assert codex_mod.extract_operations is extract_operations


# --- Task 2 / S02: two invocations sharing one source -----------------------


def test_two_disjoint_segments_of_one_source_write_exactly_one_raw_archive(codex_run):
    """S02 (capsule half): two invocations bound to the same underlying
    rollout file (disjoint ordinal segments) must dedupe the raw archive by
    snapshot -- one physical file, both index rows referencing it -- while
    each still gets its own per-invocation normalized JSON."""
    handle, source = codex_run
    bind_transcript(
        handle.run_id, source, role="l4-card", subject="600000",
        invocation_id="seg-a", start_ordinal=0, end_ordinal=6,
    )
    bind_transcript(
        handle.run_id, source, role="l4-intel", subject="600000",
        invocation_id="seg-b", start_ordinal=7, end_ordinal=13,
    )

    index = materialize_agent_index(handle.run_id)

    rows_by_id = {row["invocation_id"]: row for row in index["invocations"]}
    assert rows_by_id["seg-a"]["status"] == "PRESENT"
    assert rows_by_id["seg-b"]["status"] == "PRESENT"
    assert rows_by_id["seg-a"]["snapshot_id"] == rows_by_id["seg-b"]["snapshot_id"]
    assert rows_by_id["seg-a"]["raw"] == rows_by_id["seg-b"]["raw"]
    assert rows_by_id["seg-a"]["normalized"] != rows_by_id["seg-b"]["normalized"]

    raw_files = sorted((handle.capsule / "agents/raw").glob("*.jsonl.gz"))
    assert len(raw_files) == 1
    normalized_files = sorted((handle.capsule / "agents/normalized").glob("*.json"))
    assert len(normalized_files) == 2


def test_collect_run_deduplicates_overlapping_segments_of_one_source(codex_run):
    """S02 (usage half): two invocations bound to the same source with
    *overlapping* ordinal segments must not each contribute their own usage
    delta (that double-counts the overlap's cumulative window) -- both are
    UNMEASURED, and one combined, whole-source row carries the true total.

    Realistic-fixture coverage (uses the packaged rollout.jsonl, which also
    has an error `task_complete` inside segment A's window). Fix round 1,
    Finding 3: this fixture is *not* the isolated mutation-probe evidence --
    removing the overlap guard also changes segment A's own lifecycle status
    for an unrelated reason (the error/retry timing), so a mutated run can
    turn this test red before ever reaching the double-count claim. See
    test_collect_run_overlap_dedup_prevents_a_double_counted_total for a
    fixture that isolates exactly the claim this guard exists to prove.
    """
    from autoresearch.trace import usage_harvest as U

    handle, source = codex_run
    # Fixture token_count events sit at ordinal 5 (cumulative input 100000,
    # cached 50000) and ordinal 10 (cumulative input 408129, cached 200448) --
    # ordinal 10 is the *last* cumulative snapshot in the whole file, so it
    # already subsumes everything counted at ordinal 5.
    bind_transcript(
        handle.run_id, source, role="l4-card", subject="600000",
        invocation_id="overlap-a", start_ordinal=0, end_ordinal=8,
    )
    bind_transcript(
        handle.run_id, source, role="l4-intel", subject="600000",
        invocation_id="overlap-b", start_ordinal=5, end_ordinal=13,
    )

    rows = U.collect_run(handle.run_id, engine="codex")
    by_role = {row.get("role"): row for row in rows}

    assert by_role["l4-card"]["status"] == "UNMEASURED"
    assert "overlap" in by_role["l4-card"]["reason"]
    assert by_role["l4-intel"]["status"] == "UNMEASURED"
    assert "overlap" in by_role["l4-intel"]["reason"]

    combined = by_role["shared_source"]
    assert combined["status"] != "UNMEASURED"
    # The whole-file truth (the last cumulative snapshot, ordinal 10) --
    # never the naive sum of the two overlapping deltas (which would double
    # count everything already reflected by ordinal 5's snapshot).
    assert combined["input"] == 408129 - 200448
    assert combined["output"] == 1671
    naive_sum_input = (100000 - 50000) + (408129 - 200448)
    assert combined["input"] != naive_sum_input
    assert combined["merged_invocation_ids"] == ["overlap-a", "overlap-b"]


def test_collect_run_overlap_dedup_prevents_a_double_counted_total(codex_run):
    """S02 (usage half), isolated mutation-probe fixture (fix round 1,
    Finding 3): every lifecycle event in both overlapping segments is a
    *clean* task_complete (no error, no retry) so nothing about a segment's
    own status can turn this test red for a reason unrelated to overlap
    de-duplication -- the only way this test can fail is the summed `input`
    across the returned rows landing on the naive double-counted sum instead
    of the true whole-source total.
    """
    import json

    from autoresearch.trace import usage_harvest as U

    handle, source = codex_run
    rows = [
        {
            "type": "session_meta", "ordinal": 0,
            "timestamp": "2026-09-13T00:00:00.000Z",
            "payload": {
                "id": "clean-fixture", "session_id": "clean-fixture",
                "timestamp": "2026-09-13T00:00:00.000Z", "cwd": "/fixture",
                "originator": "codex-tui",
            },
        },
        {
            "type": "turn_context", "ordinal": 1,
            "timestamp": "2026-09-13T00:00:01.000Z",
            "payload": {
                "turn_id": "t1", "model": "gpt-5-codex",
                "collaboration_mode": {"settings": {"reasoning_effort": "high"}},
            },
        },
        {
            "type": "event_msg", "ordinal": 2,
            "timestamp": "2026-09-13T00:00:02.000Z",
            "payload": {"type": "token_count", "info": {"total_token_usage": {
                "input_tokens": 1000, "cached_input_tokens": 100,
                "output_tokens": 50, "cache_write_input_tokens": 10,
                "reasoning_output_tokens": 5, "total_tokens": 1050,
            }}},
        },
        {
            "type": "event_msg", "ordinal": 3,
            "timestamp": "2026-09-13T00:00:03.000Z",
            # Clean completion -- no "error" key at all, unlike the packaged
            # rollout.jsonl fixture used above.
            "payload": {"type": "task_complete", "turn_id": "t1",
                        "last_agent_message": "first turn done"},
        },
        {
            "type": "event_msg", "ordinal": 4,
            "timestamp": "2026-09-13T00:00:04.000Z",
            "payload": {"type": "token_count", "info": {"total_token_usage": {
                "input_tokens": 5000, "cached_input_tokens": 500,
                "output_tokens": 200, "cache_write_input_tokens": 40,
                "reasoning_output_tokens": 30, "total_tokens": 5200,
            }}},
        },
        {
            "type": "event_msg", "ordinal": 5,
            "timestamp": "2026-09-13T00:00:05.000Z",
            "payload": {"type": "task_complete", "turn_id": "t2",
                        "last_agent_message": "second turn done"},
        },
    ]
    source.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
    )
    bind_transcript(
        handle.run_id, source, role="l4-card", subject="600000",
        invocation_id="clean-a", start_ordinal=0, end_ordinal=3,
    )
    bind_transcript(
        handle.run_id, source, role="l4-intel", subject="600000",
        invocation_id="clean-b", start_ordinal=2, end_ordinal=5,
    )

    result = U.collect_run(handle.run_id, engine="codex")

    # The isolated claim, checked first and without depending on `status`
    # strings at all: the summed input across every returned row must equal
    # the true whole-source total (5000-500=4500 -- the last cumulative
    # snapshot, ordinal 4), never the naive per-segment sum
    # ((1000-100) + (5000-500) = 5400) a missing overlap guard would produce.
    # UNMEASURED rows report input=0, so summing unconditionally is safe.
    true_total_input = 5000 - 500
    naive_sum_input = (1000 - 100) + (5000 - 500)
    assert true_total_input != naive_sum_input
    assert sum(r["input"] for r in result) == true_total_input

    by_role = {row.get("role"): row for row in result}
    assert by_role["l4-card"]["status"] == "UNMEASURED"
    assert by_role["l4-intel"]["status"] == "UNMEASURED"
    assert by_role["shared_source"]["input"] == true_total_input
    assert by_role["shared_source"]["output"] == 200


def test_collect_run_keeps_disjoint_shared_segments_individually_measured(codex_run):
    """The overlap rule must not over-trigger: two segments of one source
    that do *not* overlap are each measured normally (no combined row)."""
    from autoresearch.trace import usage_harvest as U

    handle, source = codex_run
    bind_transcript(
        handle.run_id, source, role="l4-card", subject="600000",
        invocation_id="disjoint-a", start_ordinal=0, end_ordinal=6,
    )
    bind_transcript(
        handle.run_id, source, role="l4-intel", subject="600000",
        invocation_id="disjoint-b", start_ordinal=7, end_ordinal=13,
    )

    rows = U.collect_run(handle.run_id, engine="codex")
    by_role = {row.get("role"): row for row in rows}

    assert "shared_source" not in by_role
    assert by_role["l4-card"]["status"] != "UNMEASURED"
    assert by_role["l4-intel"]["status"] != "UNMEASURED"


# --- Fix round 2: redacted usage figures must not crash usage parsing ------
#
# `trace.identity._SECRET_KEY_RE` matches "token" anywhere in a dict *key*
# (case-insensitive) -- every one of these usage field names contains it
# (input_tokens, cached_input_tokens, cache_write_input_tokens,
# output_tokens, reasoning_output_tokens, total_tokens), so a transcript
# that has gone through `redact_value` (e.g. the salvage store's archived
# copy) has every one of these *values* replaced with the literal string
# "[REDACTED]", regardless of whether the original value was a number.
# Real-world impact (Task 8's offline backfill of a real run): 183 crashes,
# salvage-sourced usage effectively 100% unreadable for the Claude engine.


def test_claude_stats_from_rows_redacted_usage_field_is_unmeasured_not_a_crash(
    tmp_path,
):
    rows = [
        {
            "type": "assistant",
            "timestamp": "2026-09-13T00:00:00Z",
            "message": {
                "id": "m1",
                "model": "claude-opus-5",
                "stop_reason": "end_turn",
                "usage": {
                    "input_tokens": "[REDACTED]",
                    "output_tokens": 42,
                    "cache_read_input_tokens": 10,
                    "cache_creation_input_tokens": 5,
                },
                "content": [{"type": "text", "text": "hello"}],
            },
        },
    ]
    ref = TranscriptRef(engine="claude", path=tmp_path / "unused.jsonl", role="subagent")

    stats = ClaudeTranscriptAdapter().stats_from_rows(rows, ref)

    assert stats.usage.status == "UNMEASURED"
    # A zero here is the documented UNMEASURED placeholder, not a claim that
    # zero tokens were used -- the surrounding assertions on `status` are
    # what make that distinction legible to a caller.
    assert stats.usage.input == 0
    assert stats.usage.output == 0
    assert stats.usage.cache_read == 0
    assert stats.usage.cache_create == 0
    # One unreadable usage field must not cost the whole transcript: the
    # message item is still there.
    assert len(stats.normalized.items) == 1
    assert stats.normalized.items[0].kind == "message"
    assert stats.normalized.status == "SUCCEEDED"


def test_claude_stats_from_rows_redacted_cache_split_field_is_also_unmeasured(
    tmp_path,
):
    """Every usage field that can be redacted gets the same treatment, not
    just input_tokens -- here the 1h/5m cache-creation split."""
    rows = [
        {
            "type": "assistant",
            "timestamp": "2026-09-13T00:00:00Z",
            "message": {
                "id": "m1",
                "model": "claude-opus-5",
                "stop_reason": "end_turn",
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 20,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 5,
                    "cache_creation": {
                        "ephemeral_1h_input_tokens": "[REDACTED]",
                        "ephemeral_5m_input_tokens": 5,
                    },
                },
                "content": [{"type": "text", "text": "hello"}],
            },
        },
    ]
    ref = TranscriptRef(engine="claude", path=tmp_path / "unused.jsonl", role="subagent")

    stats = ClaudeTranscriptAdapter().stats_from_rows(rows, ref)

    assert stats.usage.status == "UNMEASURED"
    assert stats.usage.input == 0


def test_claude_stats_from_rows_context_tokens_skips_a_redacted_sample(tmp_path):
    """context_tokens is a tuple of per-message samples: one unreadable
    sample is omitted (never fabricated, never crashes), the rest survive."""
    rows = [
        {
            "type": "assistant",
            "timestamp": "2026-09-13T00:00:00Z",
            "message": {
                "id": "m1",
                "usage": {
                    "input_tokens": "[REDACTED]",
                    "output_tokens": 1,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                },
                "content": [{"type": "text", "text": "hi"}],
            },
        },
        {
            "type": "assistant",
            "timestamp": "2026-09-13T00:00:01Z",
            "message": {
                "id": "m2",
                "stop_reason": "end_turn",
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 1,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                },
                "content": [{"type": "text", "text": "hi2"}],
            },
        },
    ]
    ref = TranscriptRef(engine="claude", path=tmp_path / "unused.jsonl", role="subagent")

    stats = ClaudeTranscriptAdapter().stats_from_rows(rows, ref)

    assert stats.context_tokens == (100,)


def test_codex_stats_from_rows_redacted_usage_field_is_unmeasured_not_a_crash(
    tmp_path,
):
    rows = [
        {
            "type": "session_meta", "ordinal": 0,
            "timestamp": "2026-09-13T00:00:00.000Z",
            "payload": {"id": "s1", "session_id": "s1",
                        "timestamp": "2026-09-13T00:00:00.000Z",
                        "cwd": "/fixture", "originator": "codex-tui"},
        },
        {
            "type": "event_msg", "ordinal": 1,
            "timestamp": "2026-09-13T00:00:01.000Z",
            "payload": {"type": "token_count", "info": {"total_token_usage": {
                "input_tokens": "[REDACTED]", "cached_input_tokens": 10,
                "output_tokens": 5, "cache_write_input_tokens": 0,
                "reasoning_output_tokens": 0, "total_tokens": 15,
            }}},
        },
        {
            "type": "event_msg", "ordinal": 2,
            "timestamp": "2026-09-13T00:00:02.000Z",
            "payload": {"type": "task_complete", "turn_id": "t1",
                        "last_agent_message": "ok"},
        },
    ]
    ref = TranscriptRef(engine="codex", path=tmp_path / "unused.jsonl", role="subagent")

    stats = CodexTranscriptAdapter().stats_from_rows(rows, ref)

    assert stats.usage.status == "UNMEASURED"
    assert stats.usage.input == 0
    assert stats.usage.output == 0
    # normalization is unaffected by the redacted usage figure
    assert stats.normalized.status == "SUCCEEDED"


def test_codex_stats_from_rows_redacted_baseline_snapshot_is_also_unmeasured(
    tmp_path,
):
    """The *baseline* (preceding) snapshot used for a role segment's delta
    can be redacted too, not just the segment's own last snapshot."""
    rows = [
        {
            "type": "session_meta", "ordinal": 0,
            "timestamp": "2026-09-13T00:00:00.000Z",
            "payload": {"id": "s1", "session_id": "s1",
                        "timestamp": "2026-09-13T00:00:00.000Z",
                        "cwd": "/fixture", "originator": "codex-tui"},
        },
        {
            "type": "event_msg", "ordinal": 1,
            "timestamp": "2026-09-13T00:00:01.000Z",
            "payload": {"type": "token_count", "info": {"total_token_usage": {
                "input_tokens": "[REDACTED]", "cached_input_tokens": 0,
                "output_tokens": 0, "cache_write_input_tokens": 0,
                "reasoning_output_tokens": 0, "total_tokens": 0,
            }}},
        },
        {
            "type": "event_msg", "ordinal": 2,
            "timestamp": "2026-09-13T00:00:02.000Z",
            "payload": {"type": "token_count", "info": {"total_token_usage": {
                "input_tokens": 500, "cached_input_tokens": 50,
                "output_tokens": 20, "cache_write_input_tokens": 5,
                "reasoning_output_tokens": 1, "total_tokens": 520,
            }}},
        },
        {
            "type": "event_msg", "ordinal": 3,
            "timestamp": "2026-09-13T00:00:03.000Z",
            "payload": {"type": "task_complete", "turn_id": "t1",
                        "last_agent_message": "ok"},
        },
    ]
    ref = TranscriptRef(
        engine="codex", path=tmp_path / "unused.jsonl", role="subagent",
        start_ordinal=2, end_ordinal=3,
    )

    stats = CodexTranscriptAdapter().stats_from_rows(rows, ref)

    # The segment's own window only sees the ordinal-2 snapshot (clean), but
    # its *baseline* (the preceding ordinal-1 snapshot, used to difference
    # against) is redacted -- must still come out UNMEASURED, not a crash
    # and not a delta computed against a wrong (treated-as-zero) baseline.
    assert stats.usage.status == "UNMEASURED"
    assert stats.usage.input == 0


def test_codex_stats_from_rows_context_tokens_skips_a_redacted_sample(tmp_path):
    rows = [
        {
            "type": "session_meta", "ordinal": 0,
            "timestamp": "2026-09-13T00:00:00.000Z",
            "payload": {"id": "s1", "session_id": "s1",
                        "timestamp": "2026-09-13T00:00:00.000Z",
                        "cwd": "/fixture", "originator": "codex-tui"},
        },
        {
            "type": "event_msg", "ordinal": 1,
            "timestamp": "2026-09-13T00:00:01.000Z",
            "payload": {"type": "token_count", "info": {"total_token_usage": {
                "input_tokens": "[REDACTED]", "cached_input_tokens": 0,
                "output_tokens": 0, "cache_write_input_tokens": 0,
                "reasoning_output_tokens": 0, "total_tokens": 0,
            }}},
        },
        {
            "type": "event_msg", "ordinal": 2,
            "timestamp": "2026-09-13T00:00:02.000Z",
            "payload": {"type": "token_count", "info": {"total_token_usage": {
                "input_tokens": 300, "cached_input_tokens": 0,
                "output_tokens": 0, "cache_write_input_tokens": 0,
                "reasoning_output_tokens": 0, "total_tokens": 300,
            }}},
        },
    ]
    ref = TranscriptRef(engine="codex", path=tmp_path / "unused.jsonl", role="subagent")

    stats = CodexTranscriptAdapter().stats_from_rows(rows, ref)

    assert stats.context_tokens == (300,)


def test_parse_token_count_distinguishes_absent_zero_and_unparseable():
    """The shared helper both adapters use: absent/falsy -> 0 (the historical
    `int(x or 0)` behaviour), a real number -> itself, anything else
    (a redacted string, a list, a non-integer float) -> None, an explicit
    "cannot parse" signal rather than a fabricated 0."""
    from autoresearch.trace.transcripts.base import parse_token_count

    assert parse_token_count(None) == 0
    assert parse_token_count(0) == 0
    assert parse_token_count("") == 0
    assert parse_token_count(1234) == 1234
    assert parse_token_count(12.0) == 12
    assert parse_token_count("[REDACTED]") is None
    assert parse_token_count(True) is None
    assert parse_token_count(12.5) is None
    assert parse_token_count([1, 2]) is None


def test_codex_discovery_distinguishes_child_id_from_root_session_id(tmp_path):
    import json
    from datetime import datetime, timezone
    from autoresearch.trace.transcripts.codex import discover_rollout_candidates

    day = tmp_path / '2026/10/01'
    day.mkdir(parents=True)
    paths = {}
    for thread in ('root', 'child', 'sibling'):
        path = day / f'rollout-{thread}.jsonl'
        path.write_text(json.dumps({'type': 'session_meta', 'payload': {
            'id': thread, 'session_id': 'root'}}) + '\n')
        paths[thread] = path
    for thread, path in paths.items():
        found = discover_rollout_candidates(
            RunIdentity('probe', 'codex', session_ref=thread), sessions_root=tmp_path,
            now=datetime(2026, 10, 1, tzinfo=timezone.utc))
        assert found.candidates == (path,)


def test_claude_bound_segment_is_positional(tmp_path):
    """Claude rows have no native ordinal: a ref's ordinals are row positions."""
    import json

    path = tmp_path / "agent-seg.jsonl"
    rows = [{"type": "user", "timestamp": f"2026-10-01T07:00:0{i}Z",
             "message": {"role": "user", "content": f"turn {i}"}} for i in range(4)]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    adapter = ClaudeTranscriptAdapter(projects_root=tmp_path)
    whole = adapter.normalize(TranscriptRef(engine="claude", path=path))
    segment = adapter.normalize(
        TranscriptRef(engine="claude", path=path, start_ordinal=1, end_ordinal=2))
    assert [item.payload["text"] for item in whole.items] == [f"turn {i}" for i in range(4)]
    assert [item.payload["text"] for item in segment.items] == ["turn 1", "turn 2"]



# --- 2026-10-03 实际身份:Codex 侧对应物 --------------------------------------


def test_codex_usage_records_cli_version_and_the_segment_model(codex_ref):
    usage = CodexTranscriptAdapter().usage(codex_ref)

    assert usage.models == ("gpt-5.6-sol",)
    assert usage.host_version == "0.149.1"


def test_codex_usage_lists_every_model_a_segment_ran_on(tmp_path):
    """档位 fallback(sol → terra)发生在段内时,`model` 只剩最后一个;`models` 两个都留。"""
    rows = [
        _codex_row("session_meta", 0, {"id": "s", "cli_version": "0.159.3"}, "2026-10-03T00:00:00Z"),
        _codex_row("turn_context", 1, {"model": "gpt-5.6-sol"}, "2026-10-03T00:00:01Z"),
        _codex_row("turn_context", 2, {"model": "gpt-5.6-terra"}, "2026-10-03T00:00:02Z"),
        _codex_row("turn_context", 3, {"model": "gpt-5.6-sol"}, "2026-10-03T00:00:03Z"),
    ]
    ref = TranscriptRef(engine="codex", path=tmp_path / "rollout.jsonl", role="l4-card")

    usage = CodexTranscriptAdapter().stats_from_rows(rows, ref).usage

    assert usage.model == "gpt-5.6-sol"
    assert usage.models == ("gpt-5.6-sol", "gpt-5.6-terra")
    assert usage.host_version == "0.159.3"


def test_codex_usage_without_session_meta_has_no_guessed_version(tmp_path):
    rows = [_codex_row("turn_context", 1, {"model": "gpt-5.6-sol"}, "2026-10-03T00:00:01Z")]
    ref = TranscriptRef(engine="codex", path=tmp_path / "rollout.jsonl", role="l4-card")

    usage = CodexTranscriptAdapter().stats_from_rows(rows, ref).usage

    assert usage.host_version == "—"
