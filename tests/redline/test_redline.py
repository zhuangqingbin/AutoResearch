"""Token growth guard M3 / M7 / M4-breaker: the post-run redline readout (zero LLM).

Fake capsules and transcripts only — no real run, no subscription.  Each test names the
red line it locks so that deleting the guard turns it red.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoresearch.common import workspace as ws
from autoresearch.scan import redline

RUN = "20261010T130000000000Z"
PREV = "20261009T130000000000Z"


@pytest.fixture
def roots(tmp_path, monkeypatch):
    monkeypatch.setattr(ws, "ENGINE", "claude")
    monkeypatch.setattr(ws, "context_root", lambda: tmp_path / "context_claude")
    monkeypatch.setattr(ws, "reports_root", lambda: tmp_path / "reports_claude")
    return tmp_path


def _cfg(**budgets) -> dict:
    return {"budgets": budgets, "l4": {"max_cards": 5}, "pinned": {"cap": 5}, "l4_intel": {"enabled": True},
            "sector": {"max_briefs": 6, "healthy_top3_extra": True},
            "session": {"max_turns": {"scan.l4.card": 14, "scan.l4.intel": 35}}}


def _claude_transcript(folder: Path, name: str, prefix: int) -> str:
    path = folder / f"{name}.jsonl"
    rows = [{"type": "user", "message": {"role": "user", "content": "x"}},
            {"type": "assistant", "message": {"id": "m1", "usage": {
                "input_tokens": 2, "cache_creation_input_tokens": prefix - 2, "cache_read_input_tokens": 0}}}]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return str(path)


def _capsule(roots: Path, run_id: str, rows: list[dict], *, engine: str = "claude", date: str = "2026-10-10",
             where: str = "failed") -> Path:
    base = (roots / f"reports_{engine}" / "scan" / "_failed" / run_id if where == "failed"
            else roots / f"reports_{engine}" / "scan" / "runs" / run_id / "p1")
    capsule = base / "capsule"
    (capsule / "usage").mkdir(parents=True, exist_ok=True)
    (capsule / "capsule.json").write_text(json.dumps(
        {"run_id": run_id, "engine": engine, "analysis_date": date, "business_status": "FAILED"}), encoding="utf-8")
    (capsule / "usage" / "_token_usage.json").write_text(json.dumps({"rows": rows, "totals": {}}), encoding="utf-8")
    return capsule


def _row(role: str, agent: str, path: str, *, messages=6, output=80_000, usd=2.5, model="claude-opus-5-5",
         host="2.1.294", **extra) -> dict:
    return {"role": role, "agent": agent, "path": path, "messages": messages, "output": output,
            "input": 10, "cache_create": 20_000, "cache_read": 200_000, "estimated_usd": usd,
            "model": model, "effort": "max", "host_version": host, "status": "SUCCEEDED", **extra}


def _rows(tmp: Path, *, prefix=20_000, output=80_000, usd=2.5, host="2.1.294", messages=6) -> list[dict]:
    tmp.mkdir(parents=True, exist_ok=True)
    return [_row("scan.l4.card", "l4-card", _claude_transcript(tmp, f"card{i}", prefix), output=output, usd=usd,
                 host=host, messages=messages) for i in range(3)] + [
        _row("subagent", "l4-intel", _claude_transcript(tmp, "intel", 9_000), output=60_000, usd=1.0,
             model="claude-sonnet-5-5", host=host)]


def test_build_aggregates_roles_prefixes_and_maps_agent_names(roots):
    _capsule(roots, RUN, _rows(roots / "t"))
    readout = redline.build(RUN, cfg=_cfg())
    assert readout["usage_status"] == "MEASURED"
    card, intel = readout["roles"]["scan.l4.card"], readout["roles"]["scan.l4.intel"]
    assert card["threads"] == 3 and card["prefix_median"] == 20_000 and card["calls_max"] == 6
    assert intel["threads"] == 1 and intel["prefix_median"] == 9_000      # agent l4-intel → scan.l4.intel
    assert readout["totals"]["usd"] == pytest.approx(8.5)
    assert readout["config"]["sector_briefs"] == 9 and readout["config"]["max_turns"]["scan.l4.card"] == 14


def test_duplicate_rows_of_one_transcript_are_counted_once(roots):
    # the 10-08 ledger listed every Codex thread twice: a host row and a headless row, same path
    path = _claude_transcript(roots, "dup", 22_000)
    rows = [_row("scan.l4.intel", "scan.l4.intel", path, usd=None), _row("headless", "L4 intel", path, usd=None)]
    _capsule(roots, RUN, rows)
    readout = redline.build(RUN, cfg=_cfg())
    assert readout["roles"]["scan.l4.intel"]["threads"] == 1


def test_run_over_the_declared_line_fails_and_writes_the_breaker(roots):
    _capsule(roots, RUN, _rows(roots / "t"))
    cfg = _cfg(declared={"claude_run_usd": 5.0})
    result = redline.post_run(RUN, cfg=cfg)
    assert result["verdict"] == "FAIL" and "FAIL:RUN_OVER_LINE" in result["findings"]
    breaker = json.loads(redline.breaker_path().read_text(encoding="utf-8"))
    assert breaker["run_id"] == RUN and breaker["findings"][0]["code"] == "RUN_OVER_LINE"
    assert redline.active_breaker(cfg)["run_id"] == RUN


def test_run_under_the_line_passes_without_breaker_and_suggests_a_lower_line(roots):
    _capsule(roots, RUN, _rows(roots / "t"))
    result = redline.post_run(RUN, cfg=_cfg(declared={"claude_run_usd": 41.0}))
    assert result["verdict"] in {"PASS", "WARN"}, result
    assert not redline.breaker_path().exists()
    readout = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
    assert readout["line"]["measured"] == pytest.approx(8.5)
    if readout["verdict"] == "PASS":
        assert readout["ratchet_suggestion"] == pytest.approx(9.8)        # 8.5 × 1.15, rounded up


def test_host_in_the_research_loop_fails(roots):
    rows = _rows(roots / "t") + [{"agent": "(主会话)", "role": "main", "path": "—", "messages": 180,
                                  "weighted_in": 2_000_000, "estimated_usd": 16.0, "status": "SUCCEEDED"}]
    _capsule(roots, RUN, rows)
    readout = redline.evaluate(redline.build(RUN, cfg=_cfg()), cfg=_cfg())
    assert any(f["code"] == "HOST_IN_LOOP" and f["level"] == "FAIL" for f in readout["findings"])


def test_turn_cap_hit_is_a_warning(roots):
    _capsule(roots, RUN, _rows(roots / "t", messages=14))
    readout = redline.evaluate(redline.build(RUN, cfg=_cfg()), cfg=_cfg())
    assert any(f["code"] == "TURN_CAP_HIT" for f in readout["findings"])


def _with_previous(roots, cfg, **now):
    _capsule(roots, PREV, _rows(roots / "prev"), date="2026-10-09")
    redline.post_run(PREV, cfg=cfg)
    _capsule(roots, RUN, _rows(roots / "now", **now))
    return redline.evaluate(redline.build(RUN, cfg=cfg), cfg=cfg,
                            previous=redline.previous_readout(RUN, "claude"))


def test_prefix_doubling_against_the_previous_run_fails(roots):
    readout = _with_previous(roots, _cfg(), prefix=41_000)
    assert any(f["code"] == "PREFIX_DRIFT" and f["level"] == "FAIL" for f in readout["findings"])


def test_small_prefix_growth_only_warns(roots):
    readout = _with_previous(roots, _cfg(), prefix=25_000)
    drift = [f for f in readout["findings"] if f["code"] == "PREFIX_DRIFT"]
    assert drift and all(f["level"] == "WARN" for f in drift)


def test_output_doubling_warns_alone_but_fails_with_an_identity_change(roots):
    alone = _with_previous(roots, _cfg(), output=170_000)
    assert [f["level"] for f in alone["findings"] if f["code"] == "OUTPUT_DRIFT"] == ["WARN"]


def test_output_doubling_with_a_new_host_version_fails(roots):
    # the 10-03 alias drift: same config, new CLI, card output 18k → 88k
    readout = _with_previous(roots, _cfg(), output=170_000, host="2.1.300")
    assert any(f["code"] == "OUTPUT_DRIFT" and f["level"] == "FAIL" for f in readout["findings"])


def test_missing_ledger_is_unmeasured_not_pass_and_opens_no_breaker(roots):
    result = redline.post_run(RUN, cfg=_cfg(declared={"claude_run_usd": 0.01}))
    assert result["verdict"] == "UNMEASURED"
    assert not redline.breaker_path().exists()


def test_post_run_never_raises(roots, monkeypatch):
    monkeypatch.setattr(redline, "build", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert redline.post_run(RUN, cfg=_cfg())["verdict"] == "ERROR"


def test_ack_archives_the_breaker_and_a_budget_change_clears_it(roots):
    _capsule(roots, RUN, _rows(roots / "t"))
    cfg = _cfg(declared={"claude_run_usd": 5.0})
    redline.post_run(RUN, cfg=cfg)
    with pytest.raises(ValueError):
        redline.acknowledge("someone-else", reason="x")
    target = redline.acknowledge(RUN, reason="看过了")
    assert target.is_file() and not redline.breaker_path().exists()
    assert json.loads(target.read_text(encoding="utf-8"))["ack_reason"] == "看过了"
    redline.post_run(RUN, cfg=cfg)                                   # FAIL again → new breaker
    assert redline.active_breaker(cfg) is not None
    assert redline.active_breaker(_cfg(declared={"claude_run_usd": 6.0})) is None   # line edited = seen


def _rollout(folder: Path, name: str, events: list[tuple[str, float, int]], *, prefix: int = 9_000) -> str:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"rollout-{name}.jsonl"
    lines = [{"type": "session_meta", "timestamp": events[0][0], "payload": {"id": name}}]
    for i, (ts, used, window) in enumerate(events):
        lines.append({"type": "event_msg", "timestamp": ts, "payload": {
            "type": "token_count", "info": {"last_token_usage": {"input_tokens": prefix + i * 1000}},
            "rate_limits": {"primary": {"used_percent": used, "resets_at": window},
                            "secondary": {"used_percent": used / 10, "resets_at": 1}}}})
    path.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
    return str(path)


def test_codex_window_points_and_the_codex_line(roots, monkeypatch):
    monkeypatch.setattr(ws, "ENGINE", "codex")
    monkeypatch.setattr(ws, "reports_root", lambda: roots / "reports_codex")
    day = roots / "sessions" / "2026" / "10" / "10"
    a = _rollout(day, "a", [("2026-10-10T14:00:00Z", 3.0, 7), ("2026-10-10T14:30:00Z", 40.0, 7)])
    b = _rollout(day, "b", [("2026-10-10T14:10:00Z", 20.0, 7), ("2026-10-10T15:00:00Z", 87.0, 7)])
    rows = [_row("scan.l4.intel", "L4 intel", a, usd=None, model="gpt-5.6-sol"),
            _row("scan.l4.card", "L4 card", b, usd=None, model="gpt-5.6-sol")]
    _capsule(roots, RUN, rows, engine="codex")
    cfg = _cfg(declared={"codex_run_window_points": 80})
    readout = redline.evaluate(redline.build(RUN, cfg=cfg, sessions_root=roots / "sessions"), cfg=cfg)
    assert readout["window"]["status"] == "MEASURED" and readout["window"]["primary_points"] == pytest.approx(84.0)
    assert readout["roles"]["scan.l4.intel"]["prefix_median"] == 9_000
    assert any(f["code"] == "RUN_OVER_LINE" and f["level"] == "FAIL" for f in readout["findings"])


def test_codex_concurrent_sessions_downgrade_the_window_verdict_to_a_warning(roots, monkeypatch):
    monkeypatch.setattr(ws, "ENGINE", "codex")
    monkeypatch.setattr(ws, "reports_root", lambda: roots / "reports_codex")
    day = roots / "sessions" / "2026" / "10" / "10"
    a = _rollout(day, "a", [("2026-10-10T14:00:00Z", 3.0, 7), ("2026-10-10T15:00:00Z", 87.0, 7)])
    _rollout(day, "novel", [("2026-10-10T14:20:00Z", 30.0, 7)])          # another project, same window
    _capsule(roots, RUN, [_row("scan.l4.intel", "L4 intel", a, usd=None)], engine="codex")
    cfg = _cfg(declared={"codex_run_window_points": 80})
    readout = redline.evaluate(redline.build(RUN, cfg=cfg, sessions_root=roots / "sessions"), cfg=cfg)
    assert readout["window"]["concurrent"] == 1
    assert [f["level"] for f in readout["findings"] if f["code"] == "RUN_OVER_LINE"] == ["WARN"]


def test_codex_window_reset_is_reported_as_a_lower_bound(roots):
    day = roots / "sessions" / "2026" / "10" / "10"
    first, second = 1791487526, 1791487526 + 5 * 3600                   # a reset = a 5h later window
    a = _rollout(day, "a", [("2026-10-10T14:00:00Z", 80.0, first), ("2026-10-10T14:30:00Z", 95.0, first),
                            ("2026-10-10T15:00:00Z", 10.0, second), ("2026-10-10T15:30:00Z", 30.0, second)])
    window = redline.window_points([a], sessions_root=roots / "sessions")
    assert window["status"] == "RESET_CROSSED" and window["primary_points"] == pytest.approx(35.0)


def test_a_one_second_jitter_in_resets_at_is_still_one_window(roots):
    # 10-08 real run: the weekly window's resets_at read …635 on 200 events and …636 on 33
    day = roots / "sessions" / "2026" / "10" / "10"
    a = _rollout(day, "a", [("2026-10-10T14:00:00Z", 2.0, 1791487526), ("2026-10-10T14:30:00Z", 40.0, 1791487527),
                            ("2026-10-10T15:00:00Z", 87.0, 1791487526)])
    window = redline.window_points([a], sessions_root=roots / "sessions")
    assert window["status"] == "MEASURED" and window["windows"] == 1
    assert window["primary_points"] == pytest.approx(85.0)


def test_prefix_guard_is_built_from_the_previous_readout_and_steps_aside_after_an_ack(roots):
    assert redline.PrefixGuard.for_run(RUN, engine="claude", cfg=_cfg()) is None      # no baseline yet
    _capsule(roots, PREV, _rows(roots / "prev"), date="2026-10-09")
    redline.post_run(PREV, cfg=_cfg())
    guard = redline.PrefixGuard.for_run(RUN, engine="claude", cfg=_cfg())
    assert guard is not None and guard.baseline["scan.l4.card"] == 20_000 and guard.factor == 2.0
    big = _claude_transcript(roots, "big", 45_000)
    assert guard.observe("scan.l4.card", big)["ratio"] == 2.25
    assert guard.observe("scan.l4.card", big) is None                                # first thread only
    redline.acknowledge(RUN, reason="CLI 升级已核")
    assert redline.PrefixGuard.for_run(RUN, engine="claude", cfg=_cfg()) is None
