"""scene-reconstruction Task 3: expectation merge + attribution (B01-B10).

Plan: `docs/superpowers/plans/2026-09-12-scene-reconstruction-transcript-binding.md`
Brief: `.superpowers/sdd/2026-09-12-scene-reconstruction-transcript-binding/task-3-brief.md`
Spec (binding authority): `docs/superpowers/specs/2026-09-12-scene-reconstruction-
transcript-binding-design.md` §4

Every fixture below is synthetic, built from the *real* event/transcript row
shapes (`tests/trace/fixtures/{claude,codex}/*.jsonl` are the reference
shapes copied here field-for-field), never a real private transcript, and
every file this test suite writes lives under ``tmp_path`` -- nothing here
ever reads or writes the real ``~/.claude/projects`` or ``~/.codex/sessions``.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from autoresearch.common import workspace as ws
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
    """B03: 其他 run、同日重跑、相对路径逃逸 -> 不误绑定; 原因可见."""
    handle = _begin_claude(tmp_path, monkeypatch)
    d = _dispatch_agent(
        handle, "AGENT_DISPATCHED", role="l4-card", subject="600002",
        invocation_id="l4-card-600002-1", attempt=1,
    )
    c = _dispatch_agent(
        handle, "AGENT_COMPLETED", role="l4-card", subject="600002",
        invocation_id="l4-card-600002-1", attempt=1,
    )

    projects_root = tmp_path / "projects"
    session_ref = "session-b03"
    subagent = _claude_subagent_path(projects_root, session_ref, "b03")
    # An "escape" write: walks out of this run's own workspace entirely.
    escaping_target = handle.workspace / ".." / ".." / "etc" / "passwd"
    other_run_target = handle.workspace.parent / "20260101T000000000000Z" / "staging" / DATE / "details" / "600002.md"
    _write_claude_rows(
        subagent,
        [
            _claude_write_row("tool-1", escaping_target, ts=d["ts"], msg_id="msg-1"),
            _claude_result_row("tool-1", ts=d["ts"]),
            _claude_write_row("tool-2", other_run_target, ts=c["ts"], msg_id="msg-2"),
            _claude_result_row("tool-2", ts=c["ts"]),
        ],
    )

    run_identity = RunIdentity(run_id=handle.run_id, engine="claude", session_ref=session_ref)
    expectations = tb.agent_expectations(handle)
    candidates = tb.build_claude_candidates(
        run_identity, adapter=ClaudeTranscriptAdapter(projects_root=projects_root)
    )
    result = tb.assign(candidates, expectations, run_identity)

    row = result["rows"]["l4-card-600002-1"]
    assert row["binding_status"] != "BOUND"
    assert row["reason"]
    # Neither rejected write is silently promoted to "unexpected" evidence either.
    assert result["unexpected"] == ()


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
