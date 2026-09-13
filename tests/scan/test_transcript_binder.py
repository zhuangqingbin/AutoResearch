"""scene-reconstruction Task 3: expectation merge + attribution (B01-B10).

Plan: `docs/superpowers/plans/2026-09-12-scene-reconstruction-transcript-binding.md`
Brief: `.superpowers/sdd/2026-09-12-scene-reconstruction-transcript-binding/task-3-brief.md`
Spec (binding authority): `docs/superpowers/specs/2026-09-12-scene-reconstruction-
transcript-binding-design.md` §4

Every fixture below is synthetic, built from the *real* event/transcript row
shapes (`tests/trace/fixtures/{claude,codex}/*.jsonl` are the reference
shapes copied here field-for-field), never a real private transcript, and
every file this test suite writes lives under ``tmp_path`` -- nothing here
ever reads or writes the real ``~/.claude/projects`` or ``~/.codex/sessions``
(the Task 4 tests below that exercise ``bind_run``/``safe_bind_run`` through
their real, unoverridden adapter construction redirect ``$HOME`` itself to a
``tmp_path`` subdirectory for exactly this reason -- see
``_home_projects_root``).

Task 4 additions (B07's persistence-level conflict, R01, the engine-agreement
guard, the config switch, and the active CLI) start at the ``# Task 4``
banner below; B01-B10 above it are Task 3's own, unmodified.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from autoresearch.common import workspace as ws
from autoresearch.contracts import artifacts
from autoresearch.contracts.stages import ROLE_STAGES
from autoresearch.scan import l4_tasks, transcript_binder as tb
from autoresearch.trace import capsule as capsule_mod
from autoresearch.trace.transcripts.base import RunIdentity
from autoresearch.trace.transcripts.claude import ClaudeTranscriptAdapter
from tests.forensic_fixtures import FIXTURE_DATE, FIXTURE_NOW

DATE = FIXTURE_DATE


# --------------------------------------------------------------- run setup


def _redirect_claude(monkeypatch, tmp_path: Path) -> None:
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
        lambda *args, **kwargs: {"ok": True, "components": {}, "missing": [], "errors": []},
    )


def _begin_claude(tmp_path, monkeypatch, *, session_ref="session-claude-fixture", now=None):
    _redirect_claude(monkeypatch, tmp_path)
    handle = capsule_mod.begin_run(
        "scan-market", DATE, "claude", {}, now=now or FIXTURE_NOW, session_ref=session_ref,
    )
    monkeypatch.setenv("AUTORESEARCH_RUN_ID", handle.run_id)
    return handle


def _begin_codex(tmp_path, monkeypatch, *, now=None, session_ref=None):
    """Codex has no ambient harness session-id detection
    (`capsule.harness_session_ref` only reads what the *Claude* harness
    exports), so `begin_fixture_run`'s own `begin_run` call leaves
    `contract.session_ref=None` unless a caller supplies one explicitly --
    exactly what a real bound Codex run would have (via `runctl bind` or an
    equivalent explicit registration). Tests that need session-based
    location (B05/B09/B10) pass one; tests that don't may omit it."""
    from tests.forensic_fixtures import redirect_roots

    redirect_roots(monkeypatch, tmp_path)
    handle = capsule_mod.begin_run(
        "scan-market", FIXTURE_DATE, "codex", {}, now=now or FIXTURE_NOW, session_ref=session_ref,
    )
    monkeypatch.setenv("AUTORESEARCH_RUN_ID", handle.run_id)
    return handle


def _emit_task_event(
    handle,
    *,
    code: str,
    attempt: int,
    event_type: str,
    error_class: str | None = None,
    old_status: str = "PENDING",
) -> dict:
    """Drive the *real* `l4_tasks._record_task_transition` -- not a synthetic
    TASK_* event dict -- so this suite exercises the actual production
    writer, matching the brief's "real event ... formats" instruction.

    Known, deliberate fixture limitation (2026-09-13 fix round 1, coordinator
    note): the returned event's ``ts`` is `trace.events`'s own
    `_utc_now()` -- the *real* wall clock -- because `append_event` takes no
    ``now=`` override and this helper does not monkeypatch it. Tests that mix
    this real ``ts`` with the fixed ``base = datetime(2026, 8, 27, ...)``
    used for a rollout's own `session_meta`/`turn_context` rows (B05, the
    overlapping-windows test) are therefore correct only because the real
    clock is *after* 2026-08-27 -- true for the entire remaining lifetime of
    this fixture, since 2026-08-27 only ever recedes further into the past,
    but not a hermetically pinned guarantee. Deliberately left undone rather
    than monkeypatching `trace.events._utc_now` (used by every event this
    suite's fixtures record, including `RunState`/contract timestamps via
    `begin_run` itself): that patch's blast radius is larger than this one
    ordering assumption, and the assumption it would protect can only ever
    fail under a deliberately-mocked-into-the-past system clock, not a
    realistic CI condition.
    """
    task = l4_tasks._new_task(
        code,
        date=handle.analysis_date,
        scan_dir=handle.staging,
        context_root=handle.staging / "_external_inputs",
        meta={},
        now=None,
    )
    task["attempt"] = attempt
    payload = {"date": handle.analysis_date, "tasks": {code: task}}
    path = handle.staging / "_l4_tasks.json"
    l4_tasks._record_task_transition(
        path,
        payload,
        code,
        task_book_hash=f"hash-{code}-{attempt}-{event_type}",
        event_type=event_type,
        old_status=old_status,
        error_class=error_class,
    )
    events_path = handle.capsule / "events/events.jsonl"
    last_line = events_path.read_text(encoding="utf-8").splitlines()[-1]
    return json.loads(last_line)


def _dispatch_agent(handle, event_type: str, **kwargs) -> dict:
    return capsule_mod.record_agent_boundary(handle.run_id, event_type, **kwargs)


# ---------------------------------------------------------- transcript rows


def _claude_write_row(tool_id: str, target: Path, *, ts: str, msg_id: str, content="card") -> dict:
    return {
        "type": "assistant",
        "timestamp": ts,
        "message": {
            "id": msg_id,
            "model": "claude-opus-5",
            "content": [
                {
                    "type": "tool_use",
                    "id": tool_id,
                    "name": "Write",
                    "input": {"file_path": str(target), "content": content},
                }
            ],
        },
    }


def _claude_search_row(tool_id: str, query: str, *, ts: str, msg_id: str) -> dict:
    return {
        "type": "assistant",
        "timestamp": ts,
        "message": {
            "id": msg_id,
            "model": "claude-opus-5",
            "content": [
                {"type": "tool_use", "id": tool_id, "name": "WebSearch", "input": {"query": query}}
            ],
        },
    }


def _claude_read_row(tool_id: str, target: Path, *, ts: str, msg_id: str) -> dict:
    return {
        "type": "assistant",
        "timestamp": ts,
        "message": {
            "id": msg_id,
            "model": "claude-opus-5",
            "content": [
                {"type": "tool_use", "id": tool_id, "name": "Read", "input": {"file_path": str(target)}}
            ],
        },
    }


def _claude_result_row(tool_id: str, *, ts: str, is_error: bool = False, content="ok") -> dict:
    return {
        "type": "user",
        "timestamp": ts,
        "message": {
            "content": [
                {"type": "tool_result", "tool_use_id": tool_id, "content": content, "is_error": is_error}
            ]
        },
    }


def _write_claude_rows(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def _claude_subagent_path(projects_root: Path, session_ref: str, name: str, *, slug="proj") -> Path:
    return projects_root / slug / session_ref / "subagents" / f"agent-{name}.jsonl"


def _codex_session_meta(session_ref: str, cwd: str, *, ts: str, ordinal: int = 0) -> dict:
    return {
        "timestamp": ts,
        "ordinal": ordinal,
        "type": "session_meta",
        "payload": {"session_id": session_ref, "cwd": cwd},
    }


def _codex_write_row(call_id: str, target: Path, *, ts: str, ordinal: int, content="card") -> dict:
    return {
        "timestamp": ts,
        "ordinal": ordinal,
        "type": "response_item",
        "payload": {
            "type": "custom_tool_call",
            "call_id": call_id,
            "name": "write_file",
            "input": {"file_path": str(target), "content": content},
        },
    }


def _codex_result_row(call_id: str, *, ts: str, ordinal: int, output="ok", is_error=False) -> dict:
    return {
        "timestamp": ts,
        "ordinal": ordinal,
        "type": "response_item",
        "payload": {
            "type": "custom_tool_call_output",
            "call_id": call_id,
            "output": output,
            "is_error": is_error,
        },
    }


def _write_codex_rows(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def _iso(base: datetime, seconds: float) -> str:
    return (base + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


# ---------------------------------------------------------------------- B01


def test_b01_successful_write_with_unique_identity_binds(tmp_path, monkeypatch):
    """B01: 成功写本 run 产物且身份唯一 -> 正确绑定, stage 同词表."""
    handle = _begin_claude(tmp_path, monkeypatch)
    d = _dispatch_agent(
        handle, "AGENT_DISPATCHED", role="l4-card", subject="600000",
        invocation_id="l4-card-600000-1", attempt=1,
    )
    c = _dispatch_agent(
        handle, "AGENT_COMPLETED", role="l4-card", subject="600000",
        invocation_id="l4-card-600000-1", attempt=1,
    )

    projects_root = tmp_path / "projects"
    session_ref = "session-b01"
    subagent = _claude_subagent_path(projects_root, session_ref, "b01")
    card_path = handle.staging / "details" / "600000.md"
    _write_claude_rows(
        subagent,
        [
            _claude_write_row("tool-1", card_path, ts=d["ts"], msg_id="msg-1"),
            _claude_result_row("tool-1", ts=c["ts"]),
        ],
    )

    run_identity = RunIdentity(run_id=handle.run_id, engine="claude", session_ref=session_ref)
    expectations = tb.agent_expectations(handle)
    assert "l4-card-600000-1" in expectations
    assert expectations["l4-card-600000-1"]["role"] in ROLE_STAGES

    candidates = tb.build_claude_candidates(
        run_identity, adapter=ClaudeTranscriptAdapter(projects_root=projects_root)
    )
    result = tb.assign(candidates, expectations, run_identity)

    row = result["rows"]["l4-card-600000-1"]
    assert row["binding_status"] == "BOUND"
    assert row["segment_quality"] == "complete"
    assert row["role"] in ROLE_STAGES
    assert result["coverage"]["expected"] == 1
    assert result["coverage"]["accounted"] == 1
    assert result["coverage"]["bound"] == 1


def test_search_count_is_reported_when_segment_is_complete(tmp_path, monkeypatch):
    """Finding 5 (2026-09-13 fix round 1): only the unknown-segment path
    (B06) was previously exercised for `search_count` -- the case where a
    `complete` segment is entitled to state a real, non-None number was
    never held by any assertion. A Claude candidate is always `complete`
    once uniquely bound, so two real search operations plus the product
    write must be counted."""
    handle = _begin_claude(tmp_path, monkeypatch)
    d = _dispatch_agent(
        handle, "AGENT_DISPATCHED", role="l4-card", subject="600016",
        invocation_id="l4-card-600016-1", attempt=1,
    )
    c = _dispatch_agent(
        handle, "AGENT_COMPLETED", role="l4-card", subject="600016",
        invocation_id="l4-card-600016-1", attempt=1,
    )

    projects_root = tmp_path / "projects"
    session_ref = "session-search-count"
    subagent = _claude_subagent_path(projects_root, session_ref, "search-count")
    card_path = handle.staging / "details" / "600016.md"
    _write_claude_rows(
        subagent,
        [
            _claude_search_row("tool-s1", "600016 news", ts=d["ts"], msg_id="msg-1"),
            _claude_result_row("tool-s1", ts=d["ts"], content="search result 1"),
            _claude_search_row("tool-s2", "600016 announcement", ts=d["ts"], msg_id="msg-2"),
            _claude_result_row("tool-s2", ts=d["ts"], content="search result 2"),
            _claude_write_row("tool-w", card_path, ts=c["ts"], msg_id="msg-3"),
            _claude_result_row("tool-w", ts=c["ts"]),
        ],
    )

    run_identity = RunIdentity(run_id=handle.run_id, engine="claude", session_ref=session_ref)
    expectations = tb.agent_expectations(handle)
    candidates = tb.build_claude_candidates(
        run_identity, adapter=ClaudeTranscriptAdapter(projects_root=projects_root)
    )
    result = tb.assign(candidates, expectations, run_identity)

    row = result["rows"]["l4-card-600016-1"]
    assert row["binding_status"] == "BOUND"
    assert row["segment_quality"] == "complete"
    assert row["search_count"] == 2


# ---------------------------------------------------------------------- B02


def test_b02_read_only_and_failed_write_never_claim_success(tmp_path, monkeypatch):
    """B02: 仅讨论/读取产物, 或 Write 返回失败 -> 不生成成功写入或产品强验证."""
    handle = _begin_claude(tmp_path, monkeypatch)
    d = _dispatch_agent(
        handle, "AGENT_DISPATCHED", role="l4-card", subject="600001",
        invocation_id="l4-card-600001-1", attempt=1,
    )
    c = _dispatch_agent(
        handle, "AGENT_COMPLETED", role="l4-card", subject="600001",
        invocation_id="l4-card-600001-1", attempt=1,
    )

    projects_root = tmp_path / "projects"
    session_ref = "session-b02"
    subagent = _claude_subagent_path(projects_root, session_ref, "b02")
    card_path = handle.staging / "details" / "600001.md"
    _write_claude_rows(
        subagent,
        [
            _claude_read_row("tool-1", card_path, ts=d["ts"], msg_id="msg-1"),
            _claude_result_row("tool-1", ts=d["ts"]),
            _claude_write_row("tool-2", card_path, ts=c["ts"], msg_id="msg-2"),
            _claude_result_row("tool-2", ts=c["ts"], is_error=True, content="permission denied"),
        ],
    )

    run_identity = RunIdentity(run_id=handle.run_id, engine="claude", session_ref=session_ref)
    expectations = tb.agent_expectations(handle)
    candidates = tb.build_claude_candidates(
        run_identity, adapter=ClaudeTranscriptAdapter(projects_root=projects_root)
    )
    result = tb.assign(candidates, expectations, run_identity)

    row = result["rows"]["l4-card-600001-1"]
    assert row["binding_status"] != "BOUND"
    assert row["binding_status"] == "UNVERIFIED_BY_PRODUCT"
    assert result["coverage"]["bound"] == 0


# ---------------------------------------------------------------------- B03


def test_b03_cross_run_and_escape_paths_are_rejected_visibly(tmp_path, monkeypatch):
    """B03: 其他 run、同日重跑、相对路径逃逸 -> 不误绑定; 原因可见.

    2026-09-13 fix round 1 (Finding 1): one sub-case per rejection kind
    `_normalize_operation_path` actually distinguishes -- directory escape,
    cross-run (naming the other run), cross-engine -- each asserting the
    *specific* reason text, not merely that some reason exists. Each code
    gets its own AGENT dispatch cycle and its own subagent file so the three
    rejections never interact.
    """
    handle = _begin_claude(tmp_path, monkeypatch)
    session_ref = "session-b03"
    projects_root = tmp_path / "projects"

    def _dispatch_and_write(code: str, agent_name: str, target: Path) -> dict:
        d = _dispatch_agent(
            handle, "AGENT_DISPATCHED", role="l4-card", subject=code,
            invocation_id=f"l4-card-{code}-1", attempt=1,
        )
        c = _dispatch_agent(
            handle, "AGENT_COMPLETED", role="l4-card", subject=code,
            invocation_id=f"l4-card-{code}-1", attempt=1,
        )
        subagent = _claude_subagent_path(projects_root, session_ref, agent_name)
        _write_claude_rows(
            subagent,
            [
                _claude_write_row("tool-1", target, ts=d["ts"], msg_id="msg-1"),
                _claude_result_row("tool-1", ts=c["ts"]),
            ],
        )
        return {"dispatched": d, "completed": c}

    # Case 1: pure directory escape -- no run-id-shaped or engine-shaped
    # component anywhere in the resolved path, just walks out entirely.
    escape_target = handle.workspace / ".." / ".." / "etc" / "passwd"
    _dispatch_and_write("600002", "b03-escape", escape_target)

    # Case 2: cross-run -- a syntactically valid *other* run_id sits in the
    # resolved path, so the rejection can name it specifically.
    other_run_target = (
        handle.workspace.parent / "20260101T000000000000Z" / "staging" / DATE
        / "details" / "600020.md"
    )
    _dispatch_and_write("600020", "b03-crossrun", other_run_target)

    # Case 3: cross-engine -- resolves under the *other* engine's context
    # root entirely (never relative to this run's own staging).
    cross_engine_target = (
        tmp_path / "context_codex" / "scan_runs" / "20260101T000000000000Z"
        / "staging" / DATE / "details" / "600021.md"
    )
    _dispatch_and_write("600021", "b03-crossengine", cross_engine_target)

    run_identity = RunIdentity(run_id=handle.run_id, engine="claude", session_ref=session_ref)
    expectations = tb.agent_expectations(handle)
    candidates = tb.build_claude_candidates(
        run_identity, adapter=ClaudeTranscriptAdapter(projects_root=projects_root)
    )
    result = tb.assign(candidates, expectations, run_identity)

    escape_row = result["rows"]["l4-card-600002-1"]
    crossrun_row = result["rows"]["l4-card-600020-1"]
    crossengine_row = result["rows"]["l4-card-600021-1"]

    for row in (escape_row, crossrun_row, crossengine_row):
        assert row["binding_status"] != "BOUND"
        assert row["reason"]

    assert "directory traversal" in escape_row["reason"]
    assert "different run" in crossrun_row["reason"]
    assert "20260101T000000000000Z" in crossrun_row["reason"]
    assert "crosses into engine" in crossengine_row["reason"]
    assert "'codex'" in crossengine_row["reason"]

    # Neither rejected write is silently promoted to "unexpected" evidence either.
    assert result["unexpected"] == ()


def test_normalize_operation_path_rejects_each_kind_specifically(tmp_path):
    """Unit-level companion to B03 (Finding 2): exercises
    `_normalize_operation_path` directly so the single surviving containment
    check has its own test independent of `assign()`'s wiring -- removing
    the containment check inside the function must fail *this* test, not
    only an integration-level one three layers away."""
    run_id = "20260827T010203456789Z"
    workspace = tmp_path / "context_claude" / "scan_runs" / run_id
    staging = workspace / "staging" / DATE
    staging.mkdir(parents=True)

    # Success: a path genuinely inside staging normalizes to its relative form.
    good = staging / "details" / "600002.md"
    rel, reason = tb._normalize_operation_path(
        str(good), cwd=None, staging=staging, run_id=run_id, engine="claude"
    )
    assert rel == "details/600002.md"
    assert reason is None

    # No path at all.
    rel, reason = tb._normalize_operation_path(
        None, cwd=None, staging=staging, run_id=run_id, engine="claude"
    )
    assert rel is None
    assert "no path" in reason

    # Directory escape.
    escape = workspace / ".." / ".." / "etc" / "passwd"
    rel, reason = tb._normalize_operation_path(
        str(escape), cwd=None, staging=staging, run_id=run_id, engine="claude"
    )
    assert rel is None
    assert "directory traversal" in reason

    # Cross-run: a different, validly-shaped run_id in the path.
    other_run = workspace.parent / "20260101T000000000000Z" / "staging" / DATE / "x.md"
    rel, reason = tb._normalize_operation_path(
        str(other_run), cwd=None, staging=staging, run_id=run_id, engine="claude"
    )
    assert rel is None
    assert "different run" in reason
    assert "20260101T000000000000Z" in reason

    # Cross-engine.
    cross_engine = tmp_path / "context_codex" / "scan_runs" / run_id / "staging" / DATE / "x.md"
    rel, reason = tb._normalize_operation_path(
        str(cross_engine), cwd=None, staging=staging, run_id=run_id, engine="claude"
    )
    assert rel is None
    assert "crosses into engine" in reason
    assert "'codex'" in reason


# ---------------------------------------------------------------------- B04


def test_b04_two_attempts_one_candidate_never_guesses_by_time(tmp_path, monkeypatch):
    """B04: 两期望 attempt, 仅一候选 -> 两行; 未匹配项 GONE, 不按时间猜归属."""
    handle = _begin_claude(tmp_path, monkeypatch)
    _dispatch_agent(
        handle, "AGENT_DISPATCHED", role="l4-card", subject="600003",
        invocation_id="l4-card-600003-1", attempt=1,
    )
    f1 = _dispatch_agent(
        handle, "AGENT_FAILED", role="l4-card", subject="600003",
        invocation_id="l4-card-600003-1", attempt=1, error={"status": "threw"},
    )
    d2 = _dispatch_agent(
        handle, "AGENT_DISPATCHED", role="l4-card", subject="600003",
        invocation_id="l4-card-600003-2", attempt=2,
    )
    c2 = _dispatch_agent(
        handle, "AGENT_COMPLETED", role="l4-card", subject="600003",
        invocation_id="l4-card-600003-2", attempt=2,
    )
    assert f1["ts"] <= d2["ts"]  # sanity: real wall-clock event order

    projects_root = tmp_path / "projects"
    session_ref = "session-b04"
    # Only attempt 2's own subagent transcript ever landed (attempt 1's was
    # never archived / genuinely lost) -- exactly what B04 exercises.
    subagent = _claude_subagent_path(projects_root, session_ref, "b04-attempt2")
    card_path = handle.staging / "details" / "600003.md"
    _write_claude_rows(
        subagent,
        [
            _claude_write_row("tool-1", card_path, ts=d2["ts"], msg_id="msg-1"),
            _claude_result_row("tool-1", ts=c2["ts"]),
        ],
    )

    run_identity = RunIdentity(run_id=handle.run_id, engine="claude", session_ref=session_ref)
    expectations = tb.agent_expectations(handle)
    assert {"l4-card-600003-1", "l4-card-600003-2"} <= set(expectations)

    candidates = tb.build_claude_candidates(
        run_identity, adapter=ClaudeTranscriptAdapter(projects_root=projects_root)
    )
    result = tb.assign(candidates, expectations, run_identity)

    assert len(result["rows"]) == len(expectations)
    row1 = result["rows"]["l4-card-600003-1"]
    row2 = result["rows"]["l4-card-600003-2"]
    assert row1["binding_status"] == "GONE"
    assert row2["binding_status"] == "BOUND"
    counts = result["coverage"]
    assert counts["bound"] + counts["unverified"] + counts["ambiguous"] + counts["gone"] + counts["errors"] == counts["expected"]
    assert counts["accounted"] == counts["expected"]


# ---------------------------------------------------------------------- B05


def test_b05_two_retries_get_their_own_segments_not_the_whole_file(tmp_path, monkeypatch):
    """B05: 两次重试有明确分段 -> 各自区段, 不重复认领整段."""
    handle = _begin_codex(tmp_path, monkeypatch, session_ref="0123456789abcdef-b05")
    claimed1 = _emit_task_event(handle, code="600004", attempt=1, event_type="TASK_CLAIMED")
    _emit_task_event(
        handle, code="600004", attempt=1, event_type="TASK_FAILED",
        error_class="TRANSIENT", old_status="CLAIMED",
    )
    claimed2 = _emit_task_event(handle, code="600004", attempt=2, event_type="TASK_CLAIMED")
    success2 = _emit_task_event(
        handle, code="600004", attempt=2, event_type="TASK_SUCCEEDED", old_status="CLAIMED",
    )

    session_ref = claimed1["payload"]["session_ref"]
    assert session_ref  # the l4_tasks.py correlation field this task adds

    sessions_root = tmp_path / "codex-sessions"
    rollout = sessions_root / "2026" / "08" / "27" / "rollout-b05.jsonl"
    card_path = handle.staging / "details" / "600004.md"
    base = datetime(2026, 8, 27, tzinfo=timezone.utc)
    rows = [
        _codex_session_meta(session_ref, str(handle.workspace), ts=_iso(base, 0), ordinal=0),
        {"timestamp": _iso(base, 1), "ordinal": 1, "type": "turn_context",
         "payload": {"model": "gpt-5.6-sol", "collaboration_mode": {"settings": {"reasoning_effort": "high"}}}},
        _codex_write_row("call-attempt1", card_path, ts=claimed1["ts"], ordinal=2, content="attempt1"),
        _codex_result_row("call-attempt1", ts=claimed1["ts"], ordinal=3),
        _codex_write_row("call-attempt2", card_path, ts=claimed2["ts"], ordinal=4, content="attempt2"),
        _codex_result_row("call-attempt2", ts=success2["ts"], ordinal=5),
    ]
    _write_codex_rows(rollout, rows)

    run_identity = RunIdentity(
        run_id=handle.run_id, engine="codex", session_ref=session_ref, cwd=handle.workspace,
    )
    expectations = tb.agent_expectations(handle)
    inv1 = "l4-card-600004-1"
    inv2 = "l4-card-600004-2"
    assert {inv1, inv2} <= set(expectations)
    assert expectations[inv1]["source"] == "task_events"
    assert expectations[inv2]["source"] == "task_events"

    candidates = tb.build_codex_candidates(
        run_identity, expectations, sessions_root=sessions_root, now=datetime(2026, 8, 27, tzinfo=timezone.utc)
    )
    result = tb.assign(candidates, expectations, run_identity)

    row1 = result["rows"][inv1]
    row2 = result["rows"][inv2]
    assert row1["binding_status"] == "BOUND"
    assert row2["binding_status"] == "BOUND"
    assert row1["candidate_path"] == row2["candidate_path"]  # same shared rollout file
    # Each attempt's own bound candidate is a *segment*, not "the whole file
    # claimed twice" -- distinguishable via the ref this task threads through.
    assert row1["segment_quality"] in ("complete", "partial")
    assert row2["segment_quality"] in ("complete", "partial")


def test_overlapping_purpose_built_windows_are_interleaved_not_complete(tmp_path, monkeypatch):
    """Finding 6 (2026-09-13 fix round 1): unlike B05's disjoint retries,
    these two attempts' own [claimed, terminal] windows genuinely overlap in
    wall-clock time (claimed1 < claimed2 < failed1 < succeeded2). Neither
    `ordinal_window_for_timestamps` call sees the other invocation's window,
    so each would independently report "complete" unless something
    cross-checks them -- "complete" is supposed to mean the segment
    provably covers that invocation *exclusively*, which is false here for
    both. Both must downgrade to `segment_quality="interleaved"`, never
    `"complete"`."""
    handle = _begin_codex(tmp_path, monkeypatch, session_ref="0123456789abcdef-overlap")
    claimed1 = _emit_task_event(handle, code="600017", attempt=1, event_type="TASK_CLAIMED")
    claimed2 = _emit_task_event(handle, code="600017", attempt=2, event_type="TASK_CLAIMED")
    failed1 = _emit_task_event(
        handle, code="600017", attempt=1, event_type="TASK_FAILED",
        error_class="TRANSIENT", old_status="CLAIMED",
    )
    success2 = _emit_task_event(
        handle, code="600017", attempt=2, event_type="TASK_SUCCEEDED", old_status="CLAIMED",
    )
    # Sanity: the four events really did interleave in wall-clock order, so
    # attempt1's window [claimed1, failed1] and attempt2's window
    # [claimed2, succeeded2] genuinely overlap rather than this test
    # accidentally reproducing B05's disjoint shape.
    assert claimed1["ts"] < claimed2["ts"] < failed1["ts"] < success2["ts"]

    session_ref = claimed1["payload"]["session_ref"]
    sessions_root = tmp_path / "codex-sessions"
    rollout = sessions_root / "2026" / "08" / "27" / "rollout-overlap.jsonl"
    card_path = handle.staging / "details" / "600017.md"
    base = datetime(2026, 8, 27, tzinfo=timezone.utc)
    rows = [
        _codex_session_meta(session_ref, str(handle.workspace), ts=_iso(base, 0), ordinal=0),
        {"timestamp": _iso(base, 1), "ordinal": 1, "type": "turn_context",
         "payload": {"model": "gpt-5.6-sol"}},
        _codex_write_row("call-a", card_path, ts=claimed1["ts"], ordinal=2, content="attempt1"),
        _codex_result_row("call-a", ts=claimed2["ts"], ordinal=3),
        _codex_write_row("call-b", card_path, ts=failed1["ts"], ordinal=4, content="attempt2"),
        _codex_result_row("call-b", ts=success2["ts"], ordinal=5),
    ]
    _write_codex_rows(rollout, rows)

    run_identity = RunIdentity(
        run_id=handle.run_id, engine="codex", session_ref=session_ref, cwd=handle.workspace,
    )
    expectations = tb.agent_expectations(handle)
    inv1, inv2 = "l4-card-600017-1", "l4-card-600017-2"
    assert {inv1, inv2} <= set(expectations)

    candidates = tb.build_codex_candidates(
        run_identity, expectations, sessions_root=sessions_root,
        now=datetime(2026, 8, 27, tzinfo=timezone.utc),
    )
    # Both windowed candidates exist, and neither independently claims
    # "complete" -- proving the downgrade happened at candidate-build time,
    # not merely as an artifact of how `assign()` happens to render it.
    windowed = [c for c in candidates if c.ref.invocation_id in (inv1, inv2)]
    assert len(windowed) == 2
    assert {c.segment_quality for c in windowed} == {"interleaved"}

    result = tb.assign(candidates, expectations, run_identity)
    row1 = result["rows"][inv1]
    row2 = result["rows"][inv2]
    assert row1["segment_quality"] == "interleaved"
    assert row2["segment_quality"] == "interleaved"
    assert row1["segment_quality"] != "complete"
    assert row2["segment_quality"] != "complete"
    # An interleaved segment's counts are exactly as untrustworthy as an
    # unknown one -- never reported as a real number.
    assert row1["search_count"] is None
    assert row2["search_count"] is None


# ---------------------------------------------------------------------- B06


def test_b06_intel_without_boundary_is_partial_never_zero_searches(tmp_path, monkeypatch):
    """B06: intel 搜索早于写入且边界不明 -> partial/unknown, 不声称搜索零次."""
    handle = _begin_codex(tmp_path, monkeypatch)
    # No task-book coverage for l4-intel (l4_tasks.py only ever tracks
    # l4-card) and no AGENT_* events under Codex at all -- l4-intel here can
    # only be discovered via its own product file (the boundary-less case
    # brief bullet 8 targets).
    intel_path = handle.staging / "_l4_intel_600005.md"
    intel_path.parent.mkdir(parents=True, exist_ok=True)
    intel_path.write_text("# intel\n", encoding="utf-8")

    session_ref = handle.contract.session_ref or "session-b06"
    sessions_root = tmp_path / "codex-sessions"
    rollout = sessions_root / "2026" / "08" / "27" / "rollout-b06.jsonl"
    base = datetime(2026, 8, 27, tzinfo=timezone.utc)
    rows = [
        _codex_session_meta(session_ref, str(handle.workspace), ts=_iso(base, 0), ordinal=0),
        {"timestamp": _iso(base, 1), "ordinal": 1, "type": "turn_context",
         "payload": {"model": "gpt-5.6-sol"}},
        # Several searches *before* the write -- their count must never be
        # asserted from an unbounded whole-file candidate.
        {"timestamp": _iso(base, 2), "ordinal": 2, "type": "response_item",
         "payload": {"type": "custom_tool_call", "call_id": "call-s1", "name": "web_search",
                     "input": {"query": "600005 news"}}},
        {"timestamp": _iso(base, 3), "ordinal": 3, "type": "response_item",
         "payload": {"type": "custom_tool_call_output", "call_id": "call-s1", "output": "..."}},
        {"timestamp": _iso(base, 4), "ordinal": 4, "type": "response_item",
         "payload": {"type": "custom_tool_call", "call_id": "call-s2", "name": "web_search",
                     "input": {"query": "600005 announcement"}}},
        {"timestamp": _iso(base, 5), "ordinal": 5, "type": "response_item",
         "payload": {"type": "custom_tool_call_output", "call_id": "call-s2", "output": "..."}},
        _codex_write_row("call-write", intel_path, ts=_iso(base, 6), ordinal=6, content="# intel"),
        _codex_result_row("call-write", ts=_iso(base, 7), ordinal=7),
    ]
    _write_codex_rows(rollout, rows)

    run_identity = RunIdentity(
        run_id=handle.run_id, engine="codex", session_ref=session_ref, cwd=handle.workspace,
    )
    expectations = tb.agent_expectations(handle)
    inv_id = next(k for k, v in expectations.items() if v["role"] == "l4-intel")
    assert expectations[inv_id]["boundary_quality"] == "unknown"

    candidates = tb.build_codex_candidates(
        run_identity, expectations, sessions_root=sessions_root, now=datetime(2026, 8, 27, tzinfo=timezone.utc)
    )
    result = tb.assign(candidates, expectations, run_identity)
    row = result["rows"][inv_id]

    assert row["binding_status"] in ("BOUND", "UNVERIFIED_BY_PRODUCT")
    assert row["segment_quality"] == "unknown"
    # The trap this bullet exists to catch: never claim "0 searches" just
    # because the segment covering them is not known.
    assert row["search_count"] is None
    assert row["search_count"] != 0


# ---------------------------------------------------------------------- B07


def test_b07_one_binding_conflict_does_not_break_the_rest_of_the_report(tmp_path, monkeypatch):
    """B07: 一条 binding conflict -> 原绑定保留, 其余项继续, 报告完整."""
    handle = _begin_claude(tmp_path, monkeypatch)
    strategist_d = _dispatch_agent(
        handle, "AGENT_DISPATCHED", role="strategist", invocation_id="strategist-market-1", attempt=1,
    )
    strategist_c = _dispatch_agent(
        handle, "AGENT_COMPLETED", role="strategist", invocation_id="strategist-market-1", attempt=1,
    )
    card_d = _dispatch_agent(
        handle, "AGENT_DISPATCHED", role="l4-card", subject="600006",
        invocation_id="l4-card-600006-1", attempt=1,
    )
    card_c = _dispatch_agent(
        handle, "AGENT_COMPLETED", role="l4-card", subject="600006",
        invocation_id="l4-card-600006-1", attempt=1,
    )

    projects_root = tmp_path / "projects"
    session_ref = "session-b07"
    strategist_path = handle.staging / "market_view.md"
    card_path = handle.staging / "details" / "600006.md"

    strategist_sub = _claude_subagent_path(projects_root, session_ref, "strategist")
    _write_claude_rows(
        strategist_sub,
        [
            _claude_write_row("tool-s", strategist_path, ts=strategist_d["ts"], msg_id="msg-s"),
            _claude_result_row("tool-s", ts=strategist_c["ts"]),
        ],
    )
    # Two *competing* subagent files both claim the l4-card product -- a
    # genuine, irreducible conflict (neither's own window disambiguates,
    # since both were built to span the same dispatch window).
    card_sub_a = _claude_subagent_path(projects_root, session_ref, "card-a")
    card_sub_b = _claude_subagent_path(projects_root, session_ref, "card-b")
    for sub in (card_sub_a, card_sub_b):
        _write_claude_rows(
            sub,
            [
                _claude_write_row("tool-c", card_path, ts=card_d["ts"], msg_id="msg-c"),
                _claude_result_row("tool-c", ts=card_c["ts"]),
            ],
        )

    run_identity = RunIdentity(run_id=handle.run_id, engine="claude", session_ref=session_ref)
    expectations = tb.agent_expectations(handle)
    candidates = tb.build_claude_candidates(
        run_identity, adapter=ClaudeTranscriptAdapter(projects_root=projects_root)
    )
    result = tb.assign(candidates, expectations, run_identity)

    card_row = result["rows"]["l4-card-600006-1"]
    strategist_row = result["rows"]["strategist-market-1"]
    assert card_row["binding_status"] == "AMBIGUOUS"
    assert len(card_row["candidate_paths"]) == 2
    assert strategist_row["binding_status"] == "BOUND"
    assert result["coverage"]["accounted"] == result["coverage"]["expected"] == 2
    assert result["coverage"]["ambiguous"] == 1
    assert result["coverage"]["bound"] == 1


# ---------------------------------------------------------------------- B08


def test_b08_mixed_agent_task_product_sources_and_conditional_roles(tmp_path, monkeypatch):
    """B08: AGENT/TASK/产品混合、条件角色 -> 不漏期望、不重复计数、下界标识."""
    handle = _begin_claude(tmp_path, monkeypatch)
    # AGENT-only: strategist.
    _dispatch_agent(
        handle, "AGENT_DISPATCHED", role="strategist", invocation_id="strategist-market-1", attempt=1,
    )
    _dispatch_agent(
        handle, "AGENT_COMPLETED", role="strategist", invocation_id="strategist-market-1", attempt=1,
    )
    # TASK-only: l4-card (no AGENT_* event -- the Codex-shaped case, but
    # reachable under Claude too whenever the best-effort AGENT emission
    # failed while the task book still recorded the transition).
    _emit_task_event(handle, code="600007", attempt=1, event_type="TASK_CLAIMED")
    _emit_task_event(handle, code="600007", attempt=1, event_type="TASK_SUCCEEDED", old_status="CLAIMED")
    # Product-only: sector-brief, no events at all.
    (handle.staging / "sector_briefs").mkdir(parents=True, exist_ok=True)
    (handle.staging / "sector_briefs" / "银行.md").write_text("## 地形段\n", encoding="utf-8")
    # Conditional role with genuinely nothing (no event, no product): must
    # never appear as a spurious expectation.
    # (l3-repair: nothing written, nothing dispatched.)

    expectations = tb.agent_expectations(handle)

    strategist_row = expectations["strategist-market-1"]
    assert strategist_row["source"] == "agent_events"
    assert strategist_row["denominator_quality"] == "full"

    card_inv = next(k for k, v in expectations.items() if v["role"] == "l4-card")
    assert expectations[card_inv]["source"] == "task_events"
    assert expectations[card_inv]["denominator_quality"] == "full"
    assert expectations[card_inv]["terminal"] == "COMPLETED"

    sector_inv = next(k for k, v in expectations.items() if v["role"] == "sector-brief")
    assert expectations[sector_inv]["source"] == "products"
    assert expectations[sector_inv]["denominator_quality"] == "lower_bound"
    assert expectations[sector_inv]["boundary_quality"] == "unknown"
    assert expectations[sector_inv]["terminal"] is None
    assert expectations[sector_inv]["subject"] == "银行"

    assert not any(v["role"] == "l3-repair" for v in expectations.values())
    # No duplicate rows for any one (role, subject, attempt).
    keys = [(v["role"], v.get("subject_key"), v["attempt"]) for v in expectations.values()]
    assert len(keys) == len(set(keys))
    assert len(expectations) == 3


# ---------------------------------------------------------------------- B09


def test_b09_two_sessions_in_one_repo_does_not_force_whole_run_ambiguous(tmp_path, monkeypatch):
    """B09: 同仓两会话仅一份有 run 证据 -> 不因候选主线程数量直接全场歧义."""
    handle = _begin_codex(tmp_path, monkeypatch, session_ref="0123456789abcdef-b09")
    claimed = _emit_task_event(handle, code="600008", attempt=1, event_type="TASK_CLAIMED")
    _emit_task_event(
        handle, code="600008", attempt=1, event_type="TASK_SUCCEEDED", old_status="CLAIMED",
    )
    session_ref = claimed["payload"]["session_ref"]

    sessions_root = tmp_path / "codex-sessions"
    base = datetime(2026, 8, 27, tzinfo=timezone.utc)
    card_path = handle.staging / "details" / "600008.md"

    # This run's own session.
    mine = sessions_root / "2026" / "08" / "27" / "rollout-mine.jsonl"
    _write_codex_rows(
        mine,
        [
            _codex_session_meta(session_ref, str(handle.workspace), ts=_iso(base, 0), ordinal=0),
            _codex_write_row("call-1", card_path, ts=_iso(base, 1), ordinal=1),
            _codex_result_row("call-1", ts=_iso(base, 2), ordinal=2),
        ],
    )
    # A second, *unrelated* parallel Codex session in the same repo.
    other = sessions_root / "2026" / "08" / "27" / "rollout-other.jsonl"
    _write_codex_rows(
        other,
        [_codex_session_meta("session-other", str(handle.workspace), ts=_iso(base, 0), ordinal=0)],
    )

    run_identity = RunIdentity(
        run_id=handle.run_id, engine="codex", session_ref=session_ref, cwd=handle.workspace,
    )
    search = __import__(
        "autoresearch.trace.transcripts.codex", fromlist=["discover_rollout_candidates"]
    ).discover_rollout_candidates(run_identity, sessions_root=sessions_root, now=base)
    assert search.scanned_files == 2
    assert search.candidates == (mine,)

    expectations = tb.agent_expectations(handle)
    candidates = tb.build_codex_candidates(
        run_identity, expectations, sessions_root=sessions_root, now=base
    )
    result = tb.assign(candidates, expectations, run_identity)
    inv_id = next(iter(expectations))
    assert result["rows"][inv_id]["binding_status"] == "BOUND"


# ---------------------------------------------------------------------- B10


def test_b10_session_created_yesterday_resumed_today_is_found(tmp_path, monkeypatch):
    """B10: 昨日创建, 今日恢复的 session -> 能找到且按当前 run 边界处理."""
    handle = _begin_codex(tmp_path, monkeypatch, session_ref="0123456789abcdef-b10")
    claimed = _emit_task_event(handle, code="600009", attempt=1, event_type="TASK_CLAIMED")
    _emit_task_event(
        handle, code="600009", attempt=1, event_type="TASK_SUCCEEDED", old_status="CLAIMED",
    )
    session_ref = claimed["payload"]["session_ref"]

    sessions_root = tmp_path / "codex-sessions"
    yesterday = datetime(2026, 8, 26, tzinfo=timezone.utc)
    today = datetime(2026, 8, 27, tzinfo=timezone.utc)
    card_path = handle.staging / "details" / "600009.md"

    rollout = sessions_root / "2026" / "08" / "26" / "rollout-resumed.jsonl"
    _write_codex_rows(
        rollout,
        [
            _codex_session_meta(session_ref, str(handle.workspace), ts=_iso(yesterday, 0), ordinal=0),
            _codex_write_row("call-1", card_path, ts=_iso(today, 1), ordinal=1),
            _codex_result_row("call-1", ts=_iso(today, 2), ordinal=2),
        ],
    )

    run_identity = RunIdentity(
        run_id=handle.run_id, engine="codex", session_ref=session_ref, cwd=handle.workspace,
    )
    from autoresearch.trace.transcripts.codex import discover_rollout_candidates

    search = discover_rollout_candidates(run_identity, sessions_root=sessions_root, now=today)
    assert search.candidates == (rollout,)
    assert search.searched_from <= "2026-08-26" <= search.searched_to

    expectations = tb.agent_expectations(handle)
    candidates = tb.build_codex_candidates(
        run_identity, expectations, sessions_root=sessions_root, now=today
    )
    result = tb.assign(candidates, expectations, run_identity)
    inv_id = next(iter(expectations))
    assert result["rows"][inv_id]["binding_status"] == "BOUND"


# ============================================================================
# Task 4
#
# `bind_run`/`safe_bind_run`/the report/the CLI (design §5.2 生产接线).
# Brief: `.superpowers/sdd/2026-09-12-scene-reconstruction-transcript-
# binding/task-4-brief.md`. Covers acceptance rows B07 (persistence-level
# conflict -- distinct from Task 3's own attribution-level B07 above) and R01
# (zero-BUY day / failed run / both sentinel paths), plus the reviewer's two
# new requirements on Task 3 (engine-agreement guard; ids taken verbatim,
# never re-derived -- every test below reads `report["rows"]` by the exact
# key `agent_expectations()` produced, including the synthesized
# `role-subject-attempt` form).
# ============================================================================


def _home_projects_root(monkeypatch, tmp_path: Path) -> Path:
    """`bind_run`/`safe_bind_run` build candidates through each adapter's own
    *default* construction -- the fixed 2-arg public signature
    (`bind_run(run_id, scan_dir)`) has no room for a test-only adapter
    override the way B01-B10 inject one directly into `build_claude_
    candidates`/`build_codex_candidates`. Redirecting ``$HOME`` makes
    `ClaudeTranscriptAdapter()`'s default `Path.home()/".claude"/"projects"`
    (and Codex's `Path.home()/".codex"/"sessions"`) land under *tmp_path*,
    never the real developer home directory."""
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path / ".claude" / "projects"


def test_bind_run_persists_bound_candidate_and_writes_staging_report(tmp_path, monkeypatch):
    """`bind_run`'s own new work, on top of what B01 already proved about
    `assign()`: a clean BOUND attribution is actually persisted via
    `capsule.bind_transcript` (a real row lands in `agents/bindings.jsonl`,
    not just an in-memory attribution), and the presence=always staging
    report lands at the registered artifact path with matching content."""
    projects_root = _home_projects_root(monkeypatch, tmp_path)
    session_ref = "session-bindrun-01"
    handle = _begin_claude(tmp_path, monkeypatch, session_ref=session_ref)
    d = _dispatch_agent(
        handle, "AGENT_DISPATCHED", role="l4-card", subject="600010",
        invocation_id="l4-card-600010-1", attempt=1,
    )
    c = _dispatch_agent(
        handle, "AGENT_COMPLETED", role="l4-card", subject="600010",
        invocation_id="l4-card-600010-1", attempt=1,
    )
    subagent = _claude_subagent_path(projects_root, session_ref, "bindrun01")
    card_path = handle.staging / "details" / "600010.md"
    _write_claude_rows(
        subagent,
        [
            _claude_write_row("tool-1", card_path, ts=d["ts"], msg_id="msg-1"),
            _claude_result_row("tool-1", ts=c["ts"]),
        ],
    )

    report = tb.bind_run(handle.run_id, handle.staging)

    assert report["enabled"] is True
    assert report["status"] == "OK"
    assert report["reason"] is None
    row = report["rows"]["l4-card-600010-1"]
    assert row["binding_status"] == "BOUND"
    assert row["segment_quality"] == "complete"
    assert report["coverage"] == {
        "expected": 1, "accounted": 1, "bound": 1, "unverified": 0,
        "ambiguous": 0, "gone": 0, "errors": 0, "unexpected": 0,
        "denominator_quality": "full",
    }

    report_path = handle.staging / artifacts.by_name("transcript_bindings_report").path
    assert report_path.is_file()
    # Round-trip the in-memory report through JSON too before comparing --
    # `assign()`'s own rows carry tuples (`candidate_paths`) that JSON
    # legitimately renders as lists; a direct `==` against the raw in-memory
    # dict would fail on that type distinction alone, not on any real content
    # difference.
    assert json.loads(report_path.read_text(encoding="utf-8")) == json.loads(json.dumps(report))

    bindings = [
        json.loads(line)
        for line in (handle.capsule / "agents/bindings.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert len(bindings) == 1
    assert bindings[0]["invocation_id"] == "l4-card-600010-1"
    assert bindings[0]["path"] == str(subagent.resolve())


def test_bind_run_rerun_with_unchanged_bindings_does_not_touch_report_bytes(
    tmp_path, monkeypatch,
):
    """Ruling 6, generalized from `bindings.jsonl` to the report that
    summarizes it: re-running `bind_run` against the exact same, already-
    bound evidence must not even rewrite `_transcript_bindings.json`'s bytes
    -- a `generated_at` timestamp that changed on every call despite nothing
    else differing would make this the one observe-time artifact that broke
    this repo's own byte-idempotence convention (caught for real by
    `tests/scan/test_wave3_observation.py`'s second-call check)."""
    projects_root = _home_projects_root(monkeypatch, tmp_path)
    session_ref = "session-bindrun-rerun"
    handle = _begin_claude(tmp_path, monkeypatch, session_ref=session_ref)
    d = _dispatch_agent(
        handle, "AGENT_DISPATCHED", role="l4-card", subject="600015",
        invocation_id="l4-card-600015-1", attempt=1,
    )
    c = _dispatch_agent(
        handle, "AGENT_COMPLETED", role="l4-card", subject="600015",
        invocation_id="l4-card-600015-1", attempt=1,
    )
    subagent = _claude_subagent_path(projects_root, session_ref, "bindrunrerun")
    card_path = handle.staging / "details" / "600015.md"
    _write_claude_rows(
        subagent,
        [
            _claude_write_row("tool-1", card_path, ts=d["ts"], msg_id="msg-1"),
            _claude_result_row("tool-1", ts=c["ts"]),
        ],
    )
    report_path = handle.staging / artifacts.by_name("transcript_bindings_report").path

    first = tb.bind_run(handle.run_id, handle.staging)
    first_bytes = report_path.read_bytes()
    first_bindings = (handle.capsule / "agents/bindings.jsonl").read_bytes()

    second = tb.bind_run(handle.run_id, handle.staging)

    # Byte-level proof first (the real claim: the file was never rewritten,
    # not even its `generated_at`) -- then a content check normalized through
    # one JSON round-trip on *both* sides (the first call's raw in-memory
    # dict still carries tuples like `candidate_paths`; `second` is read back
    # off disk as lists -- the same distinction the on-disk-vs-in-memory
    # comparisons above hit, not a real difference).
    assert report_path.read_bytes() == first_bytes
    assert (handle.capsule / "agents/bindings.jsonl").read_bytes() == first_bindings
    assert json.loads(json.dumps(second)) == json.loads(json.dumps(first))


def test_bind_and_report_rejects_engine_mismatch_loudly(tmp_path, monkeypatch):
    """Review finding on Task 3: `assign()` resolves this run's own workspace
    via `ws.find_run_root(run_identity.run_id)`, which reads the *ambient*
    `ws.ENGINE` -- if that ever disagreed with the run's own recorded engine,
    `find_run_root` would silently return `None` and every product-derived
    expectation would attribute zero evidence with no error raised ("safe by
    convention, not enforced"). `_bind_and_report` must fail loudly instead.

    Exercised directly against the private `_bind_and_report`: `bind_run`/
    `safe_bind_run`'s own `require_active_run` call already forbids reaching
    a mismatched state through either public entry point in normal use (it
    raises its own, different error first) -- this proves the belt as well
    as the suspenders the review asked for.
    """
    _home_projects_root(monkeypatch, tmp_path)
    handle = _begin_claude(tmp_path, monkeypatch)
    monkeypatch.setattr(ws, "ENGINE", "codex")

    with pytest.raises(RuntimeError, match="engine"):
        tb._bind_and_report(handle, handle.staging)


def test_b07_bind_conflict_leaves_original_binding_and_continues(tmp_path, monkeypatch):
    """Task 4's own B07 (persistence-level -- distinct from Task 3's
    attribution-level B07 above): a genuine change to an already-bound
    segment is recorded as a conflict (Controller ruling 6), the pre-existing
    binding is left untouched (never overwritten), the *other* invocation in
    the same batch still binds normally (ruling 2: a per-invocation failure
    never aborts the batch), and the report accounts for both (spec §4.5:
    accounted == expected, coverage sums)."""
    projects_root = _home_projects_root(monkeypatch, tmp_path)
    session_ref = "session-b07-bindrun"
    handle = _begin_claude(tmp_path, monkeypatch, session_ref=session_ref)

    strategist_d = _dispatch_agent(
        handle, "AGENT_DISPATCHED", role="strategist", invocation_id="strategist-market-1",
        attempt=1,
    )
    strategist_c = _dispatch_agent(
        handle, "AGENT_COMPLETED", role="strategist", invocation_id="strategist-market-1",
        attempt=1,
    )
    card_d = _dispatch_agent(
        handle, "AGENT_DISPATCHED", role="l4-card", subject="600011",
        invocation_id="l4-card-600011-1", attempt=1,
    )
    card_c = _dispatch_agent(
        handle, "AGENT_COMPLETED", role="l4-card", subject="600011",
        invocation_id="l4-card-600011-1", attempt=1,
    )

    strategist_path = handle.staging / "market_view.md"
    card_path = handle.staging / "details" / "600011.md"
    strategist_sub = _claude_subagent_path(projects_root, session_ref, "strategist07")
    _write_claude_rows(
        strategist_sub,
        [
            _claude_write_row("tool-s", strategist_path, ts=strategist_d["ts"], msg_id="msg-s"),
            _claude_result_row("tool-s", ts=strategist_c["ts"]),
        ],
    )
    card_sub = _claude_subagent_path(projects_root, session_ref, "card07")
    _write_claude_rows(
        card_sub,
        [
            _claude_write_row("tool-c", card_path, ts=card_d["ts"], msg_id="msg-c"),
            _claude_result_row("tool-c", ts=card_c["ts"]),
        ],
    )

    # Pre-seed a *different*, already-bound identity for the card invocation
    # -- a genuine prior binding this run's own real evidence now disagrees
    # with (ruling 6's "变更区段写冲突而不覆盖").
    conflicting_source = tmp_path / "external" / "unrelated.jsonl"
    conflicting_source.parent.mkdir(parents=True, exist_ok=True)
    conflicting_source.write_text('{"type": "unrelated"}\n', encoding="utf-8")
    capsule_mod.bind_transcript(
        handle.run_id, conflicting_source, role="l4-card", subject="600011",
        invocation_id="l4-card-600011-1", engine="claude",
    )
    original_bindings = (handle.capsule / "agents/bindings.jsonl").read_text(encoding="utf-8")
    assert original_bindings.strip()  # sanity: the pre-seed really landed

    report = tb.bind_run(handle.run_id, handle.staging)

    # The pre-seeded card line is untouched -- not overwritten, not dropped.
    # The file as a *whole* legitimately grows (the strategist invocation, a
    # genuinely new binding in the same batch, appends its own line) -- B07
    # is about that one conflicting line surviving unmodified, not about the
    # whole file staying byte-identical despite other real work happening.
    after_bindings = (handle.capsule / "agents/bindings.jsonl").read_text(encoding="utf-8")
    assert original_bindings.strip() in after_bindings
    after_lines = [line for line in after_bindings.splitlines() if line.strip()]
    card_lines = [line for line in after_lines if '"l4-card-600011-1"' in line]
    assert card_lines == [original_bindings.strip()]  # exactly the one, unchanged line

    card_row = report["rows"]["l4-card-600011-1"]
    strategist_row = report["rows"]["strategist-market-1"]
    assert card_row["binding_status"] == "ERROR"
    assert "conflict" in card_row["reason"].lower()
    assert strategist_row["binding_status"] == "BOUND"
    assert report["coverage"]["expected"] == report["coverage"]["accounted"] == 2
    assert report["coverage"]["bound"] == 1
    assert report["coverage"]["errors"] == 1
    assert report["status"] == "OK"  # the *run* of bind_run itself did not fail


# ---------------------------------------------------------------------- R01


def test_r01_full_mode_run_accounts_without_inventing_invocations(tmp_path, monkeypatch):
    """R01, leg 1: a normal, fully-successful research day. Whether E6
    ultimately decides BUY or 0-BUY that day is irrelevant to transcript
    binding -- it never reads `_relative_buy_decision.json` -- so this test's
    job is only to show accounting matches exactly what actually dispatched,
    no more, no less."""
    projects_root = _home_projects_root(monkeypatch, tmp_path)
    session_ref = "session-r01-full"
    handle = _begin_claude(tmp_path, monkeypatch, session_ref=session_ref)

    d = _dispatch_agent(
        handle, "AGENT_DISPATCHED", role="strategist", invocation_id="strategist-market-1",
        attempt=1,
    )
    c = _dispatch_agent(
        handle, "AGENT_COMPLETED", role="strategist", invocation_id="strategist-market-1",
        attempt=1,
    )
    strategist_path = handle.staging / "market_view.md"
    sub = _claude_subagent_path(projects_root, session_ref, "r01strategist")
    _write_claude_rows(
        sub,
        [
            _claude_write_row("tool-s", strategist_path, ts=d["ts"], msg_id="msg-s"),
            _claude_result_row("tool-s", ts=c["ts"]),
        ],
    )

    report = tb.bind_run(handle.run_id, handle.staging)

    assert set(report["rows"]) == {"strategist-market-1"}
    assert report["coverage"] == {
        "expected": 1, "accounted": 1, "bound": 1, "unverified": 0,
        "ambiguous": 0, "gone": 0, "errors": 0, "unexpected": 0,
        "denominator_quality": "full",
    }


def test_r01_failed_run_accounts_as_gone_without_fabricating_evidence(tmp_path, monkeypatch):
    """R01, leg 2: a failed attempt that never produced a transcript is
    accounted as GONE -- never silently dropped, never fabricated as bound.
    Also exercises the Codex engine branch of `_bind_and_report`: an empty
    `~/.codex/sessions` naturally yields zero candidates with no crash, and
    with no wall-clock-anchored fixture-date gymnastics required (nothing
    exists to find, at any date)."""
    monkeypatch.setenv("HOME", str(tmp_path))  # empty ~/.codex/sessions
    handle = _begin_codex(tmp_path, monkeypatch)
    _emit_task_event(handle, code="600012", attempt=1, event_type="TASK_CLAIMED")
    _emit_task_event(
        handle, code="600012", attempt=1, event_type="TASK_FAILED",
        error_class="PERMANENT", old_status="CLAIMED",
    )

    report = tb.bind_run(handle.run_id, handle.staging)

    assert report["engine"] == "codex"
    inv_id = next(iter(report["rows"]))
    assert inv_id == "l4-card-600012-1"  # the id agent_expectations() produced, verbatim
    row = report["rows"][inv_id]
    assert row["binding_status"] == "GONE"
    assert report["coverage"]["expected"] == report["coverage"]["accounted"] == 1
    assert report["coverage"]["gone"] == 1
    assert report["unexpected"] == []


def test_r01_sentinel_empty_mode_excludes_l4_without_inventing_rows(tmp_path, monkeypatch):
    """R01, sentinel path 1: SENTINEL_EMPTY (nothing reached L4) must not
    invent an l4-card expectation just because a stray product file happens
    to sit on disk (e.g. left over from an unrelated earlier attempt) --
    `RunProfile.role_expected` (Task 3's own gate, reused unchanged) excludes
    the role structurally, not by accident of what files happen to exist."""
    projects_root = _home_projects_root(monkeypatch, tmp_path)
    session_ref = "session-r01-sentinel-empty"
    handle = _begin_claude(tmp_path, monkeypatch, session_ref=session_ref)
    (handle.staging / "run_mode.json").write_text(
        json.dumps({"mode": "SENTINEL_EMPTY"}), encoding="utf-8",
    )
    d = _dispatch_agent(
        handle, "AGENT_DISPATCHED", role="strategist", invocation_id="strategist-market-1",
        attempt=1,
    )
    c = _dispatch_agent(
        handle, "AGENT_COMPLETED", role="strategist", invocation_id="strategist-market-1",
        attempt=1,
    )
    strategist_path = handle.staging / "market_view.md"
    sub = _claude_subagent_path(projects_root, session_ref, "r01sentinelempty")
    _write_claude_rows(
        sub,
        [
            _claude_write_row("tool-s", strategist_path, ts=d["ts"], msg_id="msg-s"),
            _claude_result_row("tool-s", ts=c["ts"]),
        ],
    )
    (handle.staging / "details").mkdir(parents=True, exist_ok=True)
    (handle.staging / "details" / "600013.md").write_text("stray\n", encoding="utf-8")

    report = tb.bind_run(handle.run_id, handle.staging)

    assert set(report["rows"]) == {"strategist-market-1"}
    assert report["coverage"]["expected"] == 1


def test_r01_sentinel_pinned_mode_still_binds_l4_card(tmp_path, monkeypatch):
    """R01, sentinel path 2: SENTINEL_PINNED's evidence obligations are
    "完全不同" from SENTINEL_EMPTY's (contracts/stages.py's own words) -- a
    pinned holding's l4-card must still be expected and bindable, not swept
    into the same "nothing ran" bucket as SENTINEL_EMPTY."""
    projects_root = _home_projects_root(monkeypatch, tmp_path)
    session_ref = "session-r01-sentinel-pinned"
    handle = _begin_claude(tmp_path, monkeypatch, session_ref=session_ref)
    (handle.staging / "run_mode.json").write_text(
        json.dumps({"mode": "SENTINEL_PINNED"}), encoding="utf-8",
    )
    d = _dispatch_agent(
        handle, "AGENT_DISPATCHED", role="l4-card", subject="600014",
        invocation_id="l4-card-600014-1", attempt=1,
    )
    c = _dispatch_agent(
        handle, "AGENT_COMPLETED", role="l4-card", subject="600014",
        invocation_id="l4-card-600014-1", attempt=1,
    )
    card_path = handle.staging / "details" / "600014.md"
    sub = _claude_subagent_path(projects_root, session_ref, "r01sentinelpinned")
    _write_claude_rows(
        sub,
        [
            _claude_write_row("tool-c", card_path, ts=d["ts"], msg_id="msg-c"),
            _claude_result_row("tool-c", ts=c["ts"]),
        ],
    )

    report = tb.bind_run(handle.run_id, handle.staging)

    row = report["rows"]["l4-card-600014-1"]
    assert row["binding_status"] == "BOUND"
    assert report["coverage"]["expected"] == 1


# ------------------------------------------------------------- safe_bind_run


def test_safe_bind_run_writes_disabled_report_when_switch_off(tmp_path, monkeypatch):
    """Controller ruling 4: the switch off -> `enabled=False` report with a
    reason, still written (the report is `presence="always"`)."""
    cfg_dir = tmp_path / ".claude" / "skills" / "scan-market"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "scan_config.jsonc").write_text(
        json.dumps({"retention": {"bind_transcripts": False}}), encoding="utf-8",
    )
    monkeypatch.setattr(
        "autoresearch.scan.user_config.DEFAULT_PATH", cfg_dir / "scan_config.jsonc"
    )
    scan = tmp_path / "scan-dir-off"
    scan.mkdir()

    report = tb.safe_bind_run(scan)

    assert report["enabled"] is False
    assert report["status"] == "DISABLED"
    assert "bind_transcripts" in report["reason"]
    assert report["coverage"]["expected"] == 0
    on_disk = json.loads((scan / "_transcript_bindings.json").read_text(encoding="utf-8"))
    assert on_disk == report


def test_safe_bind_run_writes_disabled_report_without_active_run(tmp_path, monkeypatch):
    """Controller ruling 4: no active run -> `enabled=False` report with a
    reason, no existing evidence touched (there is none to touch here, but
    the function must not attempt any binding at all)."""
    monkeypatch.delenv("AUTORESEARCH_RUN_ID", raising=False)
    monkeypatch.setattr("autoresearch.scan.user_config.DEFAULT_PATH", tmp_path / "nope.jsonc")
    scan = tmp_path / "scan-dir-no-run"
    scan.mkdir()

    report = tb.safe_bind_run(scan)

    assert report["enabled"] is False
    assert report["status"] == "DISABLED"
    assert "active" in report["reason"].lower()


def test_safe_bind_run_never_raises_and_degrades_on_whole_run_failure(
    tmp_path, monkeypatch, capsys,
):
    """Controller ruling 3: a publish must never fail because evidence
    collection failed. A whole-run failure (here: `agent_expectations`
    itself blowing up) must not propagate -- it goes through the existing
    evidence-degradation channel and stderr, and the report plainly says
    ERROR rather than silently claiming completeness."""
    _home_projects_root(monkeypatch, tmp_path)
    monkeypatch.setattr("autoresearch.scan.user_config.DEFAULT_PATH", tmp_path / "nope.jsonc")
    handle = _begin_claude(tmp_path, monkeypatch)
    degraded: list[tuple] = []
    monkeypatch.setattr(
        capsule_mod, "_degrade_evidence",
        lambda handle_, endpoint, reason: degraded.append((endpoint, reason)),
    )
    monkeypatch.setattr(
        tb, "agent_expectations",
        lambda _handle: (_ for _ in ()).throw(RuntimeError("synthetic expectation failure")),
    )

    report = tb.safe_bind_run(handle.staging)

    assert report is not None
    assert report["enabled"] is True
    assert report["status"] == "ERROR"
    assert "synthetic expectation failure" in report["reason"]
    assert degraded and "synthetic expectation failure" in degraded[0][1]
    assert "整场绑定失败" in capsys.readouterr().err


def test_configured_bind_transcripts_defaults_true_on_config_failure(
    tmp_path, monkeypatch, capsys,
):
    """Config-layer failure must degrade toward *more* evidence collection,
    not silently less (same discipline as `relative_buy.
    configured_relative_buy()`) -- a scan_config.jsonc typo elsewhere in the
    file must not silently turn binding off."""
    bad_cfg = tmp_path / "scan_config.jsonc"
    bad_cfg.write_text(json.dumps({"unknown_top_level_key": 1}), encoding="utf-8")
    monkeypatch.setattr("autoresearch.scan.user_config.DEFAULT_PATH", bad_cfg)

    assert tb._configured_bind_transcripts() is True
    assert "retention.bind_transcripts" in capsys.readouterr().err


# --------------------------------------------------------------------- CLI


def test_cli_binds_using_run_handles_own_staging(tmp_path, monkeypatch, capsys):
    """The active CLI (`python -m autoresearch.scan.transcript_binder
    --run-id <contract_run_id>`) resolves staging from the run handle
    itself -- never a guessed "latest directory in a date folder"."""
    projects_root = _home_projects_root(monkeypatch, tmp_path)
    session_ref = "session-cli-01"
    handle = _begin_claude(tmp_path, monkeypatch, session_ref=session_ref)
    d = _dispatch_agent(
        handle, "AGENT_DISPATCHED", role="strategist", invocation_id="strategist-market-1",
        attempt=1,
    )
    c = _dispatch_agent(
        handle, "AGENT_COMPLETED", role="strategist", invocation_id="strategist-market-1",
        attempt=1,
    )
    strategist_path = handle.staging / "market_view.md"
    sub = _claude_subagent_path(projects_root, session_ref, "clistrategist")
    _write_claude_rows(
        sub,
        [
            _claude_write_row("tool-s", strategist_path, ts=d["ts"], msg_id="msg-s"),
            _claude_result_row("tool-s", ts=c["ts"]),
        ],
    )

    exit_code = tb.main(["--run-id", handle.run_id])

    assert exit_code == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["rows"]["strategist-market-1"]["binding_status"] == "BOUND"
    on_disk_path = handle.staging / "_transcript_bindings.json"
    assert on_disk_path.is_file()
    assert json.loads(on_disk_path.read_text(encoding="utf-8")) == printed


# ============================================================================
# Task 4, fix round 1: three findings from review.
# ============================================================================


def test_safe_write_degrades_evidence_when_the_report_cannot_be_persisted(
    tmp_path, monkeypatch, capsys,
):
    """Finding 1: spec §5.2's "若报告本身无法落盘,通过既有 evidence
    degradation/event 通道留失败状态与 stderr;不能报成功" applies to the
    write itself, not only to upstream computation failures -- the whole-run-
    failure branch already calls `capsule._degrade_evidence`; an unwritable
    report is the *more* serious failure (we cannot even record what
    happened) and must not be the quieter of the two."""
    from autoresearch.data import contracts as data_contracts

    monkeypatch.setattr(
        tb, "atomic_write_json",
        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")),
    )
    scan = tmp_path / "scan-dir-unwritable"
    scan.mkdir()

    result = tb._safe_write(
        scan,
        tb._base_report(
            run_id="20260827T010203456789Z", engine="claude", enabled=True,
            status="OK", reason=None,
        ),
    )

    assert result is None
    degraded = data_contracts.degradations()
    assert any(
        row["endpoint"] == "capsule.transcript_binding"
        and row["key"] == "20260827T010203456789Z"
        and "disk full" in row["reasons"][0]
        for row in degraded
    )
    assert "报告落盘失败" in capsys.readouterr().err


def test_persist_binding_isolates_an_unreadable_source_like_a_conflict(
    tmp_path, monkeypatch,
):
    """Finding 2: of the three causes Controller ruling 2 names ("一条
    binding conflict、一个源不可读或一次不支持的归一化"), only "binding
    conflict" (B07 above) had a real fixture; "unreadable source" is the
    cause most likely to occur in production and is exercised here directly
    -- the candidate's own file is genuinely deleted between `assign()`
    choosing it and `_persist_binding` trying to bind it (a real TOCTOU
    window, not a mocked exception), so the *real* `capsule.
    _require_external_source`'s `is_file()` check is what raises. A second,
    independent invocation in the same batch (strategist) still binds
    normally, proving the isolation, not just the failure."""
    projects_root = _home_projects_root(monkeypatch, tmp_path)
    session_ref = "session-finding2-unreadable"
    handle = _begin_claude(tmp_path, monkeypatch, session_ref=session_ref)

    strategist_d = _dispatch_agent(
        handle, "AGENT_DISPATCHED", role="strategist", invocation_id="strategist-market-1",
        attempt=1,
    )
    strategist_c = _dispatch_agent(
        handle, "AGENT_COMPLETED", role="strategist", invocation_id="strategist-market-1",
        attempt=1,
    )
    card_d = _dispatch_agent(
        handle, "AGENT_DISPATCHED", role="l4-card", subject="600021",
        invocation_id="l4-card-600021-1", attempt=1,
    )
    card_c = _dispatch_agent(
        handle, "AGENT_COMPLETED", role="l4-card", subject="600021",
        invocation_id="l4-card-600021-1", attempt=1,
    )

    strategist_path = handle.staging / "market_view.md"
    card_path = handle.staging / "details" / "600021.md"
    strategist_sub = _claude_subagent_path(projects_root, session_ref, "strategist-f2")
    _write_claude_rows(
        strategist_sub,
        [
            _claude_write_row("tool-s", strategist_path, ts=strategist_d["ts"], msg_id="msg-s"),
            _claude_result_row("tool-s", ts=strategist_c["ts"]),
        ],
    )
    card_sub = _claude_subagent_path(projects_root, session_ref, "card-f2")
    _write_claude_rows(
        card_sub,
        [
            _claude_write_row("tool-c", card_path, ts=card_d["ts"], msg_id="msg-c"),
            _claude_result_row("tool-c", ts=card_c["ts"]),
        ],
    )

    real_bind_transcript = capsule_mod.bind_transcript

    def _delete_source_then_bind(run_id, path, *, invocation_id, **kwargs):
        # The genuinely destructive step: this specific invocation's own
        # candidate file is gone by the time persistence is attempted --
        # `assign()` (called moments earlier, inside the same `bind_run`)
        # already read it successfully to attribute this row, so this is a
        # real TOCTOU gap, not a candidate that was never readable.
        if invocation_id == "l4-card-600021-1":
            Path(path).unlink()
        return real_bind_transcript(run_id, path, invocation_id=invocation_id, **kwargs)

    monkeypatch.setattr(capsule_mod, "bind_transcript", _delete_source_then_bind)

    report = tb.bind_run(handle.run_id, handle.staging)

    card_row = report["rows"]["l4-card-600021-1"]
    strategist_row = report["rows"]["strategist-market-1"]
    assert card_row["binding_status"] == "ERROR"
    assert "not a regular file" in card_row["reason"] or "no such file" in card_row["reason"].lower()
    assert strategist_row["binding_status"] == "BOUND"
    assert report["coverage"]["expected"] == report["coverage"]["accounted"] == 2
    assert report["coverage"]["bound"] == 1
    assert report["coverage"]["errors"] == 1


def test_lower_bound_denominator_does_not_make_completeness_green(tmp_path, monkeypatch):
    """Finding 3: coverage, binding status, and the materialized carrier
    status (`materialize_agent_index`/`completeness.evaluate`) are three
    separate facts (spec §4.5) -- a grep shows neither reads
    `transcript_binder`, `denominator_quality`, or `segment_quality` today,
    but nothing pinned that. This builds a run where this module's own
    report genuinely carries `denominator_quality="lower_bound"` (a
    sector-brief with product evidence but no event at all) *alongside* a
    genuine capsule-level gap (an l4-card AGENT_DISPATCHED with no bound
    transcript), and asserts the separate completeness computation still
    correctly reports incomplete -- a lower-bound-flavored report must not
    quietly launder a real GONE gap into a green verdict, and the product-
    only expectation this module invented must never even reach
    `materialize_agent_index`'s own accounting.

    What would turn this red: any future change that made `completeness.
    evaluate`/`agent_coverage`/`materialize_agent_index` read this module's
    `_transcript_bindings.json`, `coverage.denominator_quality`, or a row's
    `segment_quality` and let any of them substitute for -- or paper over --
    `agents/index.json`'s own PRESENT/GONE accounting.
    """
    from autoresearch.contracts.profiles import profile_factory
    from autoresearch.trace import completeness as completeness_mod

    _home_projects_root(monkeypatch, tmp_path)  # keep ~/.claude/projects under tmp_path
    session_ref = "session-finding3"
    handle = _begin_claude(tmp_path, monkeypatch, session_ref=session_ref)

    # A genuine capsule-level gap: dispatched, never bound.
    _dispatch_agent(
        handle, "AGENT_DISPATCHED", role="l4-card", subject="600030",
        invocation_id="l4-card-600030-1", attempt=1,
    )
    _dispatch_agent(
        handle, "AGENT_COMPLETED", role="l4-card", subject="600030",
        invocation_id="l4-card-600030-1", attempt=1,
    )
    # A product-only expectation with no event at all -- this module's own
    # lower_bound leg, invisible to materialize_agent_index by construction.
    (handle.staging / "sector_briefs").mkdir(parents=True, exist_ok=True)
    (handle.staging / "sector_briefs" / "银行.md").write_text("## 地形段\n", encoding="utf-8")

    report = tb.bind_run(handle.run_id, handle.staging)
    assert report["coverage"]["denominator_quality"] == "lower_bound"  # sanity: scenario is real

    index = capsule_mod.materialize_agent_index(handle.run_id)
    assert index["coverage"]["missing"] > 0  # the l4-card gap really is there
    assert not any(row.get("role") == "sector-brief" for row in index["invocations"])

    profile = profile_factory(handle.contract.run_kind)(mode="FULL")
    verdict = completeness_mod.evaluate(handle.capsule, profile)

    assert verdict["completeness_ok"] is False


# ============================================================================
# Task 8 (2026-09-12 scene-reconstruction): offline reconstruction of a
# *frozen*, already-published run's index -- `transcript_binder.offline_index`
# / the `--offline` CLI flag.
#
# Every fixture directly, minimally constructs its own
# `reports_<engine>/scan/<report_run_id>/` directory (same discipline as
# `tests/scan/test_salvage.py`'s own `_write_run`) rather than driving the
# full begin_run -> finalize -> publisher pipeline, which this task does not
# own. `_freeze_run` below copies a *real* active run's capsule/staging (so
# `capsule/events/events.jsonl` -- `agent_expectations`'s one required input
# -- is the genuine production shape, not a hand-typed approximation) into
# that frozen shape, exactly mirroring what a real publish leaves behind.
# ============================================================================

def _freeze_run(tmp_path, handle, report_run_id: str, *, engine: str = "claude") -> Path:
    """Publish *handle*'s still-active capsule/staging into a frozen
    `reports_<engine>/scan/<report_run_id>/` directory -- `manifest.json` +
    `trace/run_contract.json` (both read by `offline_index`'s identity
    check) + `capsule/` (copied whole, so `events/events.jsonl` is the real
    production shape) + `trace/staging/` (the 2026-08-26+ retention mirror
    `agent_expectations`'s product-fallback and `resolve_run_mode` both
    read). Call `capsule_mod.materialize_agent_index(handle.run_id)` *before*
    this to get a realistic (GONE-only, when no live transcript exists)
    `capsule/agents/index.json` baked into the frozen copy, matching what a
    real old run's capsule actually looks like today.
    """
    scan_reports = tmp_path / f"reports_{engine}" / "scan"
    run_dir = scan_reports / report_run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(handle.capsule, run_dir / "capsule")
    (run_dir / "trace").mkdir(exist_ok=True)
    contract_bytes = (handle.workspace / "run_contract.json").read_bytes()
    (run_dir / "trace" / "run_contract.json").write_bytes(contract_bytes)
    shutil.copytree(handle.staging, run_dir / "trace" / "staging", dirs_exist_ok=True)
    contract = json.loads(contract_bytes)
    manifest = {
        "analysis_date": contract["analysis_date"],
        "run_id": contract["run_id"],
        "generated_at": "2026-08-27T20:30:00",
    }
    (run_dir / "manifest.json").write_bytes(json.dumps(manifest).encode("utf-8"))
    (run_dir / "trace" / "staging" / "run_mode.json").write_text(
        json.dumps({"schema_version": 1, "mode": "FULL"}), encoding="utf-8"
    )
    return run_dir


def _write_archived_transcript(
    run_dir: Path, *, agent: str, original_name: str, rows: list[dict]
) -> Path:
    """Reproduce exactly what `retention.archive_transcripts` (the
    pre-existing, pre-Task-2 mechanism) already leaves inside a published
    run -- `trace/transcripts/<agent>-<stem>.jsonl.gz` + an `_index.json`
    row -- without importing that module (out of this task's scope; this is
    the *consumer* side, proving `offline_index` reads the real on-disk
    shape, not a mocked one)."""
    raw = ("\n".join(json.dumps(r) for r in rows) + "\n").encode("utf-8")
    out_dir = run_dir / "trace" / "transcripts"
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(original_name).stem
    target = out_dir / f"{agent}-{stem}.jsonl.gz"
    target.write_bytes(gzip.compress(raw, compresslevel=6, mtime=0))
    index_path = out_dir / "_index.json"
    doc = (
        json.loads(index_path.read_text(encoding="utf-8"))
        if index_path.is_file()
        else {"schema_version": 1, "agents": [], "transcripts": []}
    )
    doc["transcripts"].append(
        {
            "agent": agent,
            "file": original_name,
            "status": "PRESENT",
            "raw_bytes": len(raw),
            "gz_bytes": target.stat().st_size,
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
    )
    if agent not in doc["agents"]:
        doc["agents"].append(agent)
    index_path.write_text(json.dumps(doc), encoding="utf-8")
    return target


def _run_dir_fingerprint(run_dir: Path) -> dict[str, str]:
    """Relative-path -> sha256 for every file under *run_dir* -- the exact
    before/after comparison ruling 8 demands ("every relative path and every
    file's content hash under that run must be unchanged"), never a
    filename-only comparison."""
    out: dict[str, str] = {}
    for p in sorted(run_dir.rglob("*")):
        if p.is_file():
            out[str(p.relative_to(run_dir))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


# --------------------------------------------------- required fixture 1 (H01)


def test_h01_offline_index_backfills_a_capsule_whose_invocations_are_all_gone(
    tmp_path, monkeypatch,
):
    """Brief's first required fixture: a real, frozen capsule whose
    `agents/index.json` shows every invocation GONE (no bound live
    transcript ever existed), while the run's own retention-archived
    transcript still holds the real evidence. `offline_index` must surface
    it -- and never touch the frozen capsule's own GONE row."""
    handle = _begin_claude(tmp_path, monkeypatch, session_ref="session-h01")
    d = _dispatch_agent(
        handle, "AGENT_DISPATCHED", role="l4-card", subject="600031",
        invocation_id="l4-card-600031-1", attempt=1,
    )
    c = _dispatch_agent(
        handle, "AGENT_COMPLETED", role="l4-card", subject="600031",
        invocation_id="l4-card-600031-1", attempt=1,
    )
    card_path = handle.staging / "details" / "600031.md"
    card_path.parent.mkdir(parents=True, exist_ok=True)
    card_path.write_text("# 600031 决策卡\n", encoding="utf-8")
    # No transcript ever bound at the live path -- materialize sees nothing.
    before = capsule_mod.materialize_agent_index(handle.run_id)
    before_row = next(
        r for r in before["invocations"] if r["invocation_id"] == "l4-card-600031-1"
    )
    assert before_row["status"] == "GONE"

    run_dir = _freeze_run(tmp_path, handle, "20260827-0827_2000")
    _write_archived_transcript(
        run_dir, agent="l4-card", original_name="agent-h01card.jsonl",
        rows=[
            _claude_write_row("tool-1", card_path, ts=d["ts"], msg_id="msg-1"),
            _claude_result_row("tool-1", ts=c["ts"]),
        ],
    )
    fingerprint_before = _run_dir_fingerprint(run_dir)

    result = tb.offline_index(run_dir, sessions_root=tmp_path / "no-such-sessions")

    row = next(r for r in result["invocations"] if r["invocation_id"] == "l4-card-600031-1")
    assert row["status"] == "PRESENT"
    assert row["binding_status"] == "BOUND"
    assert row["role"] == "l4-card"
    assert row["subject"] == "600031"
    assert row["normalized"] is not None
    assert result["current_revision_id"] == row["normalized"].split("/")[1]

    normalized_doc_path = (
        tb._ledger_agents_index_root(run_dir) / row["normalized"]
    )
    assert normalized_doc_path.is_file()
    doc = json.loads(normalized_doc_path.read_text(encoding="utf-8"))
    kinds = {op["kind"] for op in doc["operations"]}
    assert "WRITE_SUCCEEDED" in kinds

    # Ruling 8: the frozen run itself is never written.
    assert _run_dir_fingerprint(run_dir) == fingerprint_before
    after_capsule = json.loads(
        (run_dir / "capsule" / "agents" / "index.json").read_text(encoding="utf-8")
    )
    after_row = next(
        r for r in after_capsule["invocations"] if r["invocation_id"] == "l4-card-600031-1"
    )
    assert after_row["status"] == "GONE"  # original capsule's own conclusion, untouched


# --------------------------------------------------- required fixture 2


def test_h01_archive_alone_reproduces_what_the_live_session_showed_before_deletion(
    tmp_path, monkeypatch,
):
    """Brief's second required fixture: once the harness's own live session
    is deleted, the run's own already-archived snapshot must independently
    reproduce the *same* observed evidence -- spec §6.1's "harness 消失后，
    已归档快照仍可独立生成视图"."""
    projects_root = _home_projects_root(monkeypatch, tmp_path)
    session_ref = "session-h01-archive-only"
    handle = _begin_claude(tmp_path, monkeypatch, session_ref=session_ref)
    d = _dispatch_agent(
        handle, "AGENT_DISPATCHED", role="l4-card", subject="600032",
        invocation_id="l4-card-600032-1", attempt=1,
    )
    c = _dispatch_agent(
        handle, "AGENT_COMPLETED", role="l4-card", subject="600032",
        invocation_id="l4-card-600032-1", attempt=1,
    )
    card_path = handle.staging / "details" / "600032.md"
    card_path.parent.mkdir(parents=True, exist_ok=True)
    card_path.write_text("# 600032 决策卡\n", encoding="utf-8")
    rows = [
        _claude_write_row("tool-1", card_path, ts=d["ts"], msg_id="msg-1"),
        _claude_result_row("tool-1", ts=c["ts"]),
    ]
    live_path = _claude_subagent_path(projects_root, session_ref, "h01archiveonly")
    _write_claude_rows(live_path, rows)
    capsule_mod.materialize_agent_index(handle.run_id)

    run_dir = _freeze_run(tmp_path, handle, "20260827-0827_2010")
    _write_archived_transcript(
        run_dir, agent="l4-card", original_name=live_path.name, rows=rows,
    )

    # Sanity: with the live session still present, offline_index resolves it
    # (via source 3) to the same conclusion source 1 alone will reach.
    while_present = tb.offline_index(
        run_dir, ledger_root=tmp_path / "ledger-a", sessions_root=projects_root,
    )
    row_present = next(
        r for r in while_present["invocations"] if r["invocation_id"] == "l4-card-600032-1"
    )
    assert row_present["binding_status"] == "BOUND"

    # Now the harness's own copy is gone -- only the run's own archive remains.
    live_path.unlink()
    assert not live_path.exists()

    after_deletion = tb.offline_index(
        run_dir, ledger_root=tmp_path / "ledger-b", sessions_root=projects_root,
    )
    row_after = next(
        r for r in after_deletion["invocations"] if r["invocation_id"] == "l4-card-600032-1"
    )
    assert row_after["binding_status"] == "BOUND"
    assert row_after["status"] == "PRESENT"
    assert row_after["source_sha256"] == row_present["source_sha256"]
    assert row_after["offline_source"] == "archived_transcript"


# --------------------------------------------------- required fixture 3


def test_h01_two_runs_same_date_never_borrow_each_others_evidence(tmp_path, monkeypatch):
    """Brief's third required fixture: two different runs sharing one
    `analysis_date` must never be conflated -- ruling 6's explicit "不能把
    同日 shared 目录当归属证据". A decoy file sits in the *shared*,
    date-keyed staging root (the historical, pre-2026-08-26 fallback
    location) with different content than either run's own copy; if
    `offline_index` ever fell back to that shared root instead of each run's
    own `trace/staging/` mirror, this test would catch it."""
    handle_a = _begin_claude(tmp_path, monkeypatch, session_ref="session-h01-run-a")
    da = _dispatch_agent(
        handle_a, "AGENT_DISPATCHED", role="l4-card", subject="600041",
        invocation_id="l4-card-600041-1", attempt=1,
    )
    ca = _dispatch_agent(
        handle_a, "AGENT_COMPLETED", role="l4-card", subject="600041",
        invocation_id="l4-card-600041-1", attempt=1,
    )
    card_a = handle_a.staging / "details" / "600041.md"
    card_a.parent.mkdir(parents=True, exist_ok=True)
    card_a.write_text("# run A 600041\n", encoding="utf-8")
    capsule_mod.materialize_agent_index(handle_a.run_id)
    run_a = _freeze_run(tmp_path, handle_a, "20260827-0827_1900")
    _write_archived_transcript(
        run_a, agent="l4-card", original_name="agent-runA.jsonl",
        rows=[
            _claude_write_row("tool-a", card_a, ts=da["ts"], msg_id="msg-a"),
            _claude_result_row("tool-a", ts=ca["ts"]),
        ],
    )

    handle_b = _begin_claude(
        tmp_path, monkeypatch, session_ref="session-h01-run-b",
        now=FIXTURE_NOW + timedelta(hours=2),  # distinct contract_run_id from handle_a
    )
    db = _dispatch_agent(
        handle_b, "AGENT_DISPATCHED", role="l4-card", subject="600042",
        invocation_id="l4-card-600042-1", attempt=1,
    )
    cb = _dispatch_agent(
        handle_b, "AGENT_COMPLETED", role="l4-card", subject="600042",
        invocation_id="l4-card-600042-1", attempt=1,
    )
    card_b = handle_b.staging / "details" / "600042.md"
    card_b.parent.mkdir(parents=True, exist_ok=True)
    card_b.write_text("# run B 600042\n", encoding="utf-8")
    capsule_mod.materialize_agent_index(handle_b.run_id)
    run_b = _freeze_run(tmp_path, handle_b, "20260827-0827_2100")
    _write_archived_transcript(
        run_b, agent="l4-card", original_name="agent-runB.jsonl",
        rows=[
            _claude_write_row("tool-b", card_b, ts=db["ts"], msg_id="msg-b"),
            _claude_result_row("tool-b", ts=cb["ts"]),
        ],
    )
    assert run_a.parent == run_b.parent  # both under the same reports_.../scan/ root

    # A decoy in the *shared*, date-keyed context root -- must never be read.
    shared_dir = tmp_path / "context_claude" / "scan" / handle_a.analysis_date / "details"
    shared_dir.mkdir(parents=True, exist_ok=True)
    (shared_dir / "600041.md").write_text("# DECOY -- not either run's own copy\n", encoding="utf-8")

    result_a = tb.offline_index(run_a, sessions_root=tmp_path / "no-sessions")
    result_b = tb.offline_index(run_b, sessions_root=tmp_path / "no-sessions")

    assert result_a["report_run_id"] == "20260827-0827_1900"
    assert result_a["contract_run_id"] == handle_a.run_id
    ids_a = {r["invocation_id"] for r in result_a["invocations"]}
    assert "l4-card-600041-1" in ids_a
    assert "l4-card-600042-1" not in ids_a
    row_a = next(r for r in result_a["invocations"] if r["invocation_id"] == "l4-card-600041-1")
    assert row_a["binding_status"] == "BOUND"

    assert result_b["report_run_id"] == "20260827-0827_2100"
    assert result_b["contract_run_id"] == handle_b.run_id
    ids_b = {r["invocation_id"] for r in result_b["invocations"]}
    assert "l4-card-600042-1" in ids_b
    assert "l4-card-600041-1" not in ids_b


# --------------------------------------------------- required fixture 4


def test_h01_invocation_id_that_would_escape_its_directory_is_rejected_not_written(
    tmp_path, monkeypatch,
):
    """Brief's fourth required fixture: a corrupted historical
    `events.jsonl` line (bypassing `record_agent_boundary`'s own
    `_AGENT_ID_RE` validation, which every *current* dispatch already goes
    through -- this defends the *offline reader* against old/tampered data,
    not the live writer) carries an invocation_id that would, if used
    verbatim as a ledger filename, escape `agents_index/<report_run_id>/
    <revision_id>/normalized/`. Its *subject* (600052) is otherwise ordinary
    and has real, matching archived-transcript evidence -- so `assign()`
    itself legitimately resolves it to `BOUND`, and it is specifically
    `offline_index`'s own ledger-write path-safety gate (not
    `_normalize_operation_path`'s unrelated staging-containment check, which
    this fixture deliberately does not exercise) that must catch the unsafe
    invocation_id and quarantine that one row (a distinct error, not a
    crash, not a silently-dropped expectation) while every other,
    well-formed invocation in the same run is unaffected.
    """
    handle = _begin_claude(tmp_path, monkeypatch, session_ref="session-h01-escape")
    d = _dispatch_agent(
        handle, "AGENT_DISPATCHED", role="l4-card", subject="600051",
        invocation_id="l4-card-600051-1", attempt=1,
    )
    c = _dispatch_agent(
        handle, "AGENT_COMPLETED", role="l4-card", subject="600051",
        invocation_id="l4-card-600051-1", attempt=1,
    )
    card_path = handle.staging / "details" / "600051.md"
    card_path.parent.mkdir(parents=True, exist_ok=True)
    card_path.write_text("# 600051 决策卡\n", encoding="utf-8")
    escape_card_path = handle.staging / "details" / "600052.md"
    escape_card_path.write_text("# 600052 决策卡\n", encoding="utf-8")
    capsule_mod.materialize_agent_index(handle.run_id)

    run_dir = _freeze_run(tmp_path, handle, "20260827-0827_2200")
    _write_archived_transcript(
        run_dir, agent="l4-card", original_name="agent-escape.jsonl",
        rows=[
            _claude_write_row("tool-1", card_path, ts=d["ts"], msg_id="msg-1"),
            _claude_result_row("tool-1", ts=c["ts"]),
        ],
    )
    escape_ts = "2026-08-27T19:00:00.000000Z"
    _write_archived_transcript(
        run_dir, agent="l4-card", original_name="agent-escape2.jsonl",
        rows=[
            _claude_write_row("tool-2", escape_card_path, ts=escape_ts, msg_id="msg-2"),
            _claude_result_row("tool-2", ts=escape_ts),
        ],
    )

    # A hand-injected, malformed AGENT_DISPATCHED/AGENT_COMPLETED pair -- as
    # if this run predated the id-shape validation `record_agent_boundary`
    # enforces today. Never goes through the real writer on purpose. Ordinary
    # subject (600052, matching the second archived write above) so this
    # resolves as real, bound evidence -- only the invocation_id itself is
    # unsafe.
    events_path = run_dir / "capsule" / "events" / "events.jsonl"
    malformed_events = [
        {
            "event_type": "AGENT_DISPATCHED", "invocation_id": "../../escape-1",
            "payload": {"role": "l4-card"}, "subject": "600052", "attempt": 1,
            "ts": escape_ts,
        },
        {
            "event_type": "AGENT_COMPLETED", "invocation_id": "../../escape-1",
            "payload": {"role": "l4-card", "result": {}}, "subject": "600052",
            "attempt": 1, "ts": escape_ts,
        },
    ]
    with events_path.open("a", encoding="utf-8") as fh:
        for event in malformed_events:
            fh.write(json.dumps(event) + "\n")
    fingerprint_before = _run_dir_fingerprint(run_dir)

    result = tb.offline_index(run_dir, sessions_root=tmp_path / "no-sessions")

    ledger_root = tb._ledger_agents_index_root(run_dir)
    # Nothing was ever written outside the revision directory this run owns.
    for path in ledger_root.rglob("*"):
        if path.is_file():
            assert path.resolve().is_relative_to(ledger_root.resolve())
            assert ".." not in path.relative_to(ledger_root).parts

    escaped_row = next(
        r for r in result["invocations"] if r["invocation_id"] == "../../escape-1"
    )
    assert escaped_row["status"] != "PRESENT"
    assert escaped_row["normalized"] is None
    assert "unsafe" in escaped_row["reason"].lower() or "escape" in escaped_row["reason"].lower()
    assert any(err["invocation_id"] == "../../escape-1" for err in result["errors"])

    # The well-formed invocation in the same run is entirely unaffected.
    good_row = next(r for r in result["invocations"] if r["invocation_id"] == "l4-card-600051-1")
    assert good_row["status"] == "PRESENT"
    assert good_row["binding_status"] == "BOUND"

    assert _run_dir_fingerprint(run_dir) == fingerprint_before


# --------------------------------------------------- idempotence (ruling 4)


def test_offline_index_rerun_against_the_same_sources_produces_no_new_revision(
    tmp_path, monkeypatch,
):
    """Ruling 4: the same sources and the same `OFFLINE_INDEX_PARSER_VERSION`
    must not rewrite evidence -- re-running produces the identical
    `current_revision_id`, no second revision directory, and the existing
    revision's `normalized`/`raw` files are never touched a second time
    (mtime-unchanged proof, not just a content-equality one). This is also
    mutation probe (a)'s target: making `revision_id` depend on the clock
    must turn this test red."""
    import time

    handle = _begin_claude(tmp_path, monkeypatch, session_ref="session-h01-idempotent")
    d = _dispatch_agent(
        handle, "AGENT_DISPATCHED", role="l4-card", subject="600061",
        invocation_id="l4-card-600061-1", attempt=1,
    )
    c = _dispatch_agent(
        handle, "AGENT_COMPLETED", role="l4-card", subject="600061",
        invocation_id="l4-card-600061-1", attempt=1,
    )
    card_path = handle.staging / "details" / "600061.md"
    card_path.parent.mkdir(parents=True, exist_ok=True)
    card_path.write_text("# 600061 决策卡\n", encoding="utf-8")
    capsule_mod.materialize_agent_index(handle.run_id)
    run_dir = _freeze_run(tmp_path, handle, "20260827-0827_2300")
    _write_archived_transcript(
        run_dir, agent="l4-card", original_name="agent-idempotent.jsonl",
        rows=[
            _claude_write_row("tool-1", card_path, ts=d["ts"], msg_id="msg-1"),
            _claude_result_row("tool-1", ts=c["ts"]),
        ],
    )

    first = tb.offline_index(run_dir, sessions_root=tmp_path / "no-sessions")
    ledger_root = tb._ledger_agents_index_root(run_dir)
    revision_root = ledger_root / run_dir.name
    revision_dirs_after_first = sorted(p.name for p in revision_root.iterdir() if p.is_dir())
    assert len(revision_dirs_after_first) == 1
    normalized_path = (
        revision_root / first["current_revision_id"] / "normalized" / "l4-card-600061-1.json"
    )
    assert normalized_path.is_file()
    mtime_after_first = normalized_path.stat().st_mtime_ns

    time.sleep(0.01)  # make a clock-driven bug observable, not just theoretical
    second = tb.offline_index(run_dir, sessions_root=tmp_path / "no-sessions")

    assert second["current_revision_id"] == first["current_revision_id"]
    revision_dirs_after_second = sorted(p.name for p in revision_root.iterdir() if p.is_dir())
    assert revision_dirs_after_second == revision_dirs_after_first  # no new sibling directory
    assert normalized_path.stat().st_mtime_ns == mtime_after_first  # never rewritten
    # The one field that *does* legitimately change every call -- it is real
    # UTC wall-clock time and must not participate in revision identity.
    assert second["computed_at"] != first["computed_at"]


# ------------------------------------------- salvage attribution gate (ruling 3)


def test_source2_salvage_only_verified_run_items_become_candidates(tmp_path, monkeypatch):
    """Ruling 3 / mutation probe (b): only a salvage row whose ``attribution``
    passes `salvage.is_fact` may serve as an offline-index input.
    `TIME_WINDOW_ONLY` (and by the same logic `OVERWRITTEN_BY_LATER_RUN`/
    `UNKNOWN`/`ABSENT`) rows are reference material only and must never
    become a candidate, even though a blob genuinely exists for them."""
    from autoresearch.scan import salvage as salvage_mod
    from autoresearch.trace import blobs as trace_blobs

    _redirect_claude(monkeypatch, tmp_path)
    run_dir = tmp_path / "reports_claude" / "scan" / "20260827-0827_1800"
    run_dir.mkdir(parents=True)
    salvage_dir = tmp_path / "_ledger" / "salvage" / run_dir.name
    salvage_dir.mkdir(parents=True)

    def _archive(text: str) -> bytes:
        raw = (json.dumps({"type": "user", "message": {"content": text}}) + "\n").encode("utf-8")
        return gzip.compress(raw, mtime=0)

    verified_archive = _archive("verified evidence")
    verified_digest = trace_blobs.put_bytes(salvage_dir, verified_archive)
    window_archive = _archive("time-window-only, not evidence")
    window_digest = trace_blobs.put_bytes(salvage_dir, window_archive)

    def _blob_ref(digest: str, size: int) -> dict:
        return {"sha256": digest, "bytes": size, "path": f"blobs/sha256/{digest[:2]}/{digest}"}

    provenance = {
        "schema_version": salvage_mod.PROVENANCE_SCHEMA_VERSION,
        "report_run_id": run_dir.name, "contract_run_id": "x", "engine": "claude",
        "analysis_date": "2026-08-27", "run_window": {}, "same_date_multi_run": False,
        "sibling_report_run_ids": [], "captured_at": "2026-08-27T20:00:00Z",
        "files": [
            {
                "file_kind": "transcript", "logical_name": "transcript:verified",
                "source": "verified-source", "source_sha256": "a" * 64, "source_bytes": 1,
                "source_mtime": None, "captured_at": "2026-08-27T20:00:00Z",
                "attribution": "VERIFIED_RUN", "reason": "session_ref matched",
                "engine": "claude", "session_ref": "session-verified",
                "snapshot_id": "snap-verified",
                "blob": _blob_ref(verified_digest, len(verified_archive)),
                "archive_sha256": verified_digest,
            },
            {
                "file_kind": "transcript", "logical_name": "transcript:window",
                "source": "window-source", "source_sha256": "b" * 64, "source_bytes": 1,
                "source_mtime": None, "captured_at": "2026-08-27T20:00:00Z",
                "attribution": "TIME_WINDOW_ONLY", "reason": "mtime close, not identity",
                "engine": "claude", "session_ref": None, "snapshot_id": "snap-window",
                "blob": _blob_ref(window_digest, len(window_archive)),
                "archive_sha256": window_digest,
            },
        ],
    }
    (salvage_dir / "provenance.json").write_text(json.dumps(provenance), encoding="utf-8")

    candidates, snapshots, errors = tb._source2_salvage_candidates(
        run_dir, engine="claude", ledger_root=tmp_path / "_ledger",
    )

    assert errors == []
    assert len(candidates) == 1
    assert candidates[0].session_ref == "session-verified"
    assert snapshots[str(candidates[0].path)].snapshot_id is not None
    # The rejected row's own content never entered the candidate pool at all.
    assert all(
        "time-window-only" not in json.dumps([dict(r) for r in snap.rows])
        for snap in snapshots.values()
    )


def test_source2_salvage_reads_a_pre_fan_out_blob_at_its_recorded_flat_path(
    tmp_path, monkeypatch,
):
    """Coordinator finding (2026-09-13, mid-Task-8): a real repo-wide scan
    found 723 already-`VERIFIED_RUN` salvage rows across 45 published runs
    whose `blob["path"]` still names the *old*, pre-`trace.blobs` flat
    layout (`blobs/<digest>`) -- `_merge_rows`'s sticky rule never rewrites
    an already-verified row after `scan.salvage`'s storage layout changed
    underneath it. Recomputing a location from `blob["sha256"]` alone (via
    `trace_blobs.blob_path`, which only ever knows *today's* two-level
    fan-out) silently finds nothing for every one of those real rows.

    This fixture is built **by hand** at the old flat path -- not through
    any current code path, which would only ever write the new layout and
    could never exercise this regression."""
    from autoresearch.scan import salvage as salvage_mod
    from autoresearch.trace.atomic import sha256_bytes

    _redirect_claude(monkeypatch, tmp_path)
    run_dir = tmp_path / "reports_claude" / "scan" / "20260827-0827_1700"
    run_dir.mkdir(parents=True)
    salvage_dir = tmp_path / "_ledger" / "salvage" / run_dir.name

    raw = (json.dumps({"type": "user", "message": {"content": "pre-fan-out evidence"}}) + "\n").encode(
        "utf-8"
    )
    archive = gzip.compress(raw, mtime=0)
    digest = sha256_bytes(archive)
    flat_blob_path = salvage_dir / "blobs" / digest  # the *old*, pre-fan-out layout
    flat_blob_path.parent.mkdir(parents=True, exist_ok=True)
    flat_blob_path.write_bytes(archive)

    provenance = {
        "schema_version": salvage_mod.PROVENANCE_SCHEMA_VERSION,
        "report_run_id": run_dir.name, "contract_run_id": "x", "engine": "claude",
        "analysis_date": "2026-08-27", "run_window": {}, "same_date_multi_run": False,
        "sibling_report_run_ids": [], "captured_at": "2026-08-27T20:00:00Z",
        "files": [
            {
                "file_kind": "transcript", "logical_name": "transcript:pre-fan-out",
                "source": "pre-fan-out-source", "source_sha256": "c" * 64, "source_bytes": 1,
                "source_mtime": None, "captured_at": "2026-08-27T20:00:00Z",
                "attribution": "VERIFIED_RUN", "reason": "session_ref matched (pre-fan-out era)",
                "engine": "claude", "session_ref": "session-pre-fan-out",
                "snapshot_id": "snap-pre-fan-out",
                # The recorded path names the OLD flat layout verbatim -- exactly
                # what a real row written before the fix-round-1 layout switch
                # still has on disk today (`_merge_rows` never rewrote it).
                "blob": {"sha256": digest, "bytes": len(archive), "path": f"blobs/{digest}"},
                "archive_sha256": digest,
            },
        ],
    }
    salvage_dir.mkdir(parents=True, exist_ok=True)
    (salvage_dir / "provenance.json").write_text(json.dumps(provenance), encoding="utf-8")

    candidates, snapshots, errors = tb._source2_salvage_candidates(
        run_dir, engine="claude", ledger_root=tmp_path / "_ledger",
    )

    assert errors == []
    assert len(candidates) == 1
    assert candidates[0].session_ref == "session-pre-fan-out"
    assert candidates[0].path == flat_blob_path.resolve()
