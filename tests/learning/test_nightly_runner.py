"""夜间跑批加固单测 —— §4.2 的运行安全 + 两条容易写错的判活。"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from autoresearch.dossier import debt_schedule
from autoresearch.learning import nightly_runner as nr
from autoresearch.research import nested_probe as np_probe
from autoresearch.scan import gate0, temperature_v2_guard as tv2


def _now(offset_seconds: float = 0.0) -> datetime:
    return datetime(2026, 8, 4, 20, 30, tzinfo=timezone.utc) + timedelta(
        seconds=offset_seconds)


# ══════════════════ 锁 + 心跳 ══════════════════


def test_second_instance_is_blocked_while_the_heartbeat_is_fresh(tmp_path):
    with nr.exclusive(tmp_path, now=_now()), pytest.raises(nr.LockBusy):
        with nr.exclusive(tmp_path, now=_now(60)):
            pass


def test_stale_lock_is_recovered_not_stuck_forever(tmp_path):
    """被 kill -9 的夜跑不该让第二天起所有夜跑静默跳过。"""
    with nr.exclusive(tmp_path, now=_now()):
        pass
    (tmp_path / nr.LOCK_NAME).write_text("stale", encoding="utf-8")
    with nr.exclusive(tmp_path, now=_now(nr.STALE_LOCK_SECONDS + 60)) as info:
        assert info["recovered_stale_lock"] is True


def test_lock_is_released_even_when_the_body_raises(tmp_path):
    with pytest.raises(ValueError), nr.exclusive(tmp_path, now=_now()):
        raise ValueError("boom")
    assert not (tmp_path / nr.LOCK_NAME).exists()


def test_heartbeat_is_written_atomically(tmp_path):
    nr.beat(tmp_path, run_id="r1", step="s", now=_now())
    assert not list(tmp_path.glob("*.tmp"))
    assert nr.read_heartbeat(tmp_path)["run_id"] == "r1"


def test_corrupt_heartbeat_reads_as_missing(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / nr.HEARTBEAT_NAME).write_text("{ nope", encoding="utf-8")
    assert nr.read_heartbeat(tmp_path) is None
    assert nr.lock_age_seconds(tmp_path) is None


# ══════════════════ 判活 1:0 rows 是 NOOP 还是失败 ══════════════════


def test_zero_debt_zero_output_is_noop():
    assert nr.classify_outcome({"retro_pending": 0}, {}, failed=False) == "NOOP"


def test_debt_but_zero_output_is_stalled_not_noop():
    """有债却一件没产 —— 行数同样是 0,但这不是 NOOP。"""
    assert nr.classify_outcome({"retro_pending": 5}, {"a": 0}, failed=False) == "STALLED"


def test_partial_needs_a_commensurable_remaining_debt():
    """`input_debt`(待办日)与 `outputs`(步骤数)量纲不同 —— 不比大小,只比有无。

    要判 PARTIAL 得给出跑完后重新数的**同量纲**残债。
    """
    assert nr.classify_outcome({"d": 5}, {"a": 1}, failed=False) == "OK"
    assert nr.classify_outcome({"d": 5}, {"a": 1}, failed=False,
                               remaining_debt={"d": 3}) == "PARTIAL"
    assert nr.classify_outcome({"d": 5}, {"a": 1}, failed=False,
                               remaining_debt={"d": 0}) == "OK"


def test_unreadable_debt_marker_does_not_count_as_debt():
    """-1 表示「读不出来」,不该被当成 -1 笔欠账参与求和。"""
    assert nr.classify_outcome({"a": -1}, {}, failed=False) == "NOOP"


def test_failure_outranks_everything():
    assert nr.classify_outcome({"d": 0}, {}, failed=True) == "FAILED"


def test_unreadable_debt_is_minus_one_not_zero(monkeypatch):
    """读不出来 ≠ 没有欠账。"""
    monkeypatch.setattr("autoresearch.learning.retro.pending_days",
                        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert nr.collect_debts("2026-08-04")["retro_pending"] == -1


# ══════════════════ 幂等 + catch-up ══════════════════


def test_idempotency_key_includes_the_debt_snapshot():
    """同一天里债务可能变 —— 只用日期做键分不开这两种情况。"""
    a = nr.idempotency_key("2026-08-04", {"retro_pending": 3})
    b = nr.idempotency_key("2026-08-04", {"retro_pending": 5})
    assert a != b
    assert a == nr.idempotency_key("2026-08-04", {"retro_pending": 3})


def test_catch_up_uses_the_trade_calendar_not_natural_days():
    days = ["2026-08-03", "2026-08-04", "2026-08-05"]
    out = nr.catch_up_days("2026-08-03", "2026-08-05", trade_days=days)
    assert out["days"] == ["2026-08-04", "2026-08-05"]
    assert out["basis"] == "trade_calendar"


def test_catch_up_without_calendar_only_runs_today():
    out = nr.catch_up_days("2026-07-01", "2026-08-04")
    assert out["days"] == ["2026-08-04"] and "不臆造" in out["note"]


def test_catch_up_beyond_max_days_lists_what_it_dropped():
    days = [f"2026-07-{i:02d}" for i in range(1, 31)]
    out = nr.catch_up_days("2026-07-01", "2026-07-30", trade_days=days, max_days=3)
    assert len(out["days"]) == 3 and out["skipped"]
    assert "放弃" in out["note"]


def test_catch_up_first_run():
    out = nr.catch_up_days(None, "2026-08-04", trade_days=["2026-08-04"])
    assert out["basis"] == "first_run" and out["days"] == ["2026-08-04"]


# ══════════════════ 债务分级 ══════════════════


def test_research_debt_does_not_hard_block():
    tiers = nr.classify_debts({"retro_pending": 4, "t1_pending": 1})
    assert tiers["would_hard_block"] is False
    assert tiers["enforcement"] == "ADVISORY"


def test_data_integrity_debt_would_block():
    tiers = nr.classify_debts({"attribution_missing": 2})
    assert tiers["would_hard_block"] is True
    assert nr.TIER_DATA_INTEGRITY in tiers["blocking_tiers"]


def test_unknown_debt_defaults_to_research_not_blocking():
    """未登记的债不擅自升格成阻断项。"""
    assert nr.classify_debts({"brand_new_debt": 9})["would_hard_block"] is False


def test_policy_deletes_the_unfounded_causal_claim():
    assert "无因果证据" in nr.DEBT_POLICY_NOTE


def test_contended_artifacts_are_named():
    assert set(nr.CONTENDED_ARTIFACTS) == {"task book", "T1 快环", "dossier"}


# ══════════════════ 环境 / 重试 / 端到端 ══════════════════


def test_environment_check_catches_a_wrong_cwd(tmp_path):
    result = nr.check_environment(cwd=tmp_path, require_env=())
    assert result["ok"] is False and "launchd 默认 cwd" in result["problems"][0]


def test_environment_check_catches_missing_env(monkeypatch):
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    result = nr.check_environment(require_env=("TUSHARE_TOKEN",))
    assert any("缺环境变量" in p for p in result["problems"])


def test_retries_use_exponential_backoff():
    slept = []
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("rate limited")
        return "ok"

    result = nr.with_retries(flaky, retries=2, backoff_base=1.0, sleep=slept.append)
    assert result["ok"] and result["attempts"] == 3
    assert slept == [1.0, 2.0]


def test_retries_give_up_with_an_error_fingerprint():
    result = nr.with_retries(lambda: (_ for _ in ()).throw(ValueError("nope")),
                             retries=1, sleep=lambda _s: None)
    assert result["ok"] is False
    assert len(result["error"]["hash"]) == 16 and "ValueError" in result["error"]["head"]


def test_run_once_records_a_ledger_row(tmp_path, monkeypatch):
    monkeypatch.setattr(nr, "check_environment",
                        lambda **_k: {"ok": True, "cwd": ".", "problems": [],
                                      "env_present": {}})
    monkeypatch.setattr(nr, "collect_debts", lambda _d: {"retro_pending": 2})
    result = nr.run_once("2026-08-04", state_dir=tmp_path,
                         runner=lambda *_a: [("retro_refresh", True, "2 日")])
    assert result["status"] == "OK"
    rows = nr.read_runs(tmp_path)
    assert len(rows) == 1 and rows[0]["input_debt"] == {"retro_pending": 2}


def test_run_once_is_idempotent_for_the_same_debt_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(nr, "check_environment",
                        lambda **_k: {"ok": True, "cwd": ".", "problems": [],
                                      "env_present": {}})
    monkeypatch.setattr(nr, "collect_debts", lambda _d: {"retro_pending": 1})
    runner = lambda *_a: [("s", True, "")]  # noqa: E731
    nr.run_once("2026-08-04", state_dir=tmp_path, runner=runner)
    second = nr.run_once("2026-08-04", state_dir=tmp_path, runner=runner)
    assert second["status"] == "SKIPPED_IDEMPOTENT"


def test_run_once_bails_on_a_bad_environment(tmp_path, monkeypatch):
    monkeypatch.setattr(nr, "check_environment",
                        lambda **_k: {"ok": False, "cwd": "/", "problems": ["坏"],
                                      "env_present": {}})
    result = nr.run_once("2026-08-04", state_dir=tmp_path)
    assert result["status"] == "ENV_FAIL"
    assert nr.read_runs(tmp_path)[0]["status"] == "ENV_FAIL"


def test_run_once_records_failure_fingerprint(tmp_path, monkeypatch):
    monkeypatch.setattr(nr, "check_environment",
                        lambda **_k: {"ok": True, "cwd": ".", "problems": [],
                                      "env_present": {}})
    monkeypatch.setattr(nr, "collect_debts", lambda _d: {"retro_pending": 1})
    monkeypatch.setattr(nr, "with_retries",
                        lambda fn, **_k: {"ok": False, "attempts": 1, "result": None,
                                          "error": {"hash": "abc", "head": "X: y"}})
    result = nr.run_once("2026-08-04", state_dir=tmp_path, runner=lambda *_a: [])
    assert result["status"] == "FAILED"
    assert nr.read_runs(tmp_path)[0]["error_hash"] == "abc"


def test_launchd_plist_sets_the_working_directory():
    """launchd 默认 cwd 是 / —— 不设这一行,产物落根目录而任务照样「成功」。"""
    plist = nr.launchd_plist(hour=21, minute=5, repo="/repo")
    assert "<key>WorkingDirectory</key><string>/repo</string>" in plist
    assert "<key>Hour</key><integer>21</integer>" in plist


def test_status_render_separates_noop_from_stalled(tmp_path):
    nr.append_run(nr.RunRecord(run_id="r", day="2026-08-04", idempotency_key="k",
                               started_at="x", status="NOOP"), tmp_path)
    md = nr.render_status(tmp_path)
    assert "NOOP" in md and "STALLED" in md


def test_cli_status_and_plist(capsys):
    assert nr.main(["plist"]) == 0
    assert "WorkingDirectory" in capsys.readouterr().out


# ══════════════════ GATE0 preflight ══════════════════


def test_gate0_is_advisory_by_default():
    report = gate0.preflight({"attribution_missing": 3}, day="2026-08-04")
    assert report["mode"] == gate0.ADVISORY and report["verdict"] == gate0.WARN


def test_gate0_blocks_only_in_blocking_mode():
    report = gate0.preflight({"attribution_missing": 3}, day="2026-08-04",
                             mode=gate0.BLOCKING)
    assert report["verdict"] == gate0.BLOCK


def test_gate0_passes_on_research_only_debt():
    report = gate0.preflight({"retro_pending": 9}, day="2026-08-04",
                             mode=gate0.BLOCKING)
    assert report["verdict"] == gate0.PASS


def test_gate0_carries_the_gate1_correction():
    report = gate0.preflight({}, day="2026-08-04")
    assert "L2 之后" in report["gate1_correction"]
    assert "挡不住" in report["gate1_correction"]


def test_override_expires():
    ov = gate0.Override(actor="qa", reason="临时", expires="2026-08-01")
    blocked = gate0.preflight({"attribution_missing": 1}, day="2026-08-04",
                              mode=gate0.BLOCKING, override=ov)
    assert blocked["verdict"] == gate0.BLOCK and blocked["override_expired"] is True
    allowed = gate0.preflight({"attribution_missing": 1}, day="2026-07-30",
                              mode=gate0.BLOCKING, override=ov)
    assert allowed["verdict"] == gate0.WARN and allowed["override_applied"] is True


def test_blocking_mode_needs_registry_authorization(tmp_path):
    registry = tmp_path / "r.json"
    registry.write_text(json.dumps({
        "schema_version": 1, "stable_baseline": None, "baseline_history": [],
        "experiments": {}, "active_by_family": {}, "audit": []}), encoding="utf-8")
    with pytest.raises(gate0.Gate0Error, match="ACTIVE"):
        gate0.assert_may_block(registry)


def test_unknown_mode_raises():
    with pytest.raises(gate0.Gate0Error):
        gate0.preflight({}, mode="YOLO")


def test_gate0_render():
    md = gate0.render(gate0.preflight({"attribution_missing": 1}, day="2026-08-04"))
    assert "⚠️" in md and "GATE0" in md


def test_gate0_cli_advisory_exits_zero(capsys):
    assert gate0.main(["check", "2026-08-04"]) == 0


# ══════════════════ §4.3 嵌套 probe ══════════════════


def test_all_probes_start_untested(tmp_path):
    info = np_probe.summary(path=tmp_path / "p.json")
    assert info["counts"][np_probe.UNTESTED] == len(np_probe.PROBES)
    assert info["capability_verified"] is False


def test_untested_is_not_pass(tmp_path):
    ledger = tmp_path / "p.json"
    for key, _ in np_probe.PROBES[:-1]:
        np_probe.record(key, np_probe.PASS, path=ledger)
    info = np_probe.summary(path=ledger)
    assert info["capability_verified"] is False
    assert info["untested"] == [np_probe.PROBES[-1][0]]
    assert "不得读成" in info["untested_discipline"]


def test_all_pass_verifies_capability_only(tmp_path):
    ledger = tmp_path / "p.json"
    for key, _ in np_probe.PROBES:
        np_probe.record(key, np_probe.PASS, evidence="toy run", path=ledger)
    ready = np_probe.ready_for_ab(ledger)
    assert ready["capability"] == "PASS"
    assert ready["verdict"] == "CAPABILITY_ONLY"
    assert "PENDING" in ready["recoverability_not_worse"]
    assert "PENDING" in ready["measured_cost"]


def test_a_single_failure_blocks(tmp_path):
    ledger = tmp_path / "p.json"
    for key, _ in np_probe.PROBES:
        np_probe.record(key, np_probe.PASS, path=ledger)
    np_probe.record("parent_death", np_probe.FAIL, evidence="子变孤儿", path=ledger)
    assert np_probe.ready_for_ab(ledger)["verdict"] == "NOT_READY"


def test_unknown_probe_or_verdict_raises(tmp_path):
    with pytest.raises(ValueError):
        np_probe.record("nope", np_probe.PASS, path=tmp_path / "p.json")
    with pytest.raises(ValueError):
        np_probe.record("timeout", "MAYBE", path=tmp_path / "p.json")


def test_assumptions_are_labelled_as_untested():
    info = np_probe.summary(path="/nonexistent/x.json")
    for value in info["untested_assumptions"].values():
        assert "待测" in value or "未验证" in value


def test_inheritance_flags_the_wave9_conflict():
    info = np_probe.summary(path="/nonexistent/x.json")
    assert "需用户重裁" in info["inheritance"]["Wave9"]


def test_failure_semantics_forbid_silent_missing_cards():
    info = np_probe.summary(path="/nonexistent/x.json")
    assert "静默少一票" in info["failure_semantics"]
    assert "阻断 assemble" in info["failure_semantics"]


def test_corrupt_ledger_falls_back_to_fresh(tmp_path):
    bad = tmp_path / "p.json"
    bad.write_text("{ nope", encoding="utf-8")
    assert np_probe.summary(path=bad)["counts"][np_probe.UNTESTED] == len(np_probe.PROBES)


def test_probe_cli(tmp_path, capsys):
    ledger = str(tmp_path / "p.json")
    assert np_probe.main(["record", "timeout", "PASS", "--ledger", ledger]) == 0
    capsys.readouterr()
    assert np_probe.main(["status", "--ledger", ledger]) == 0
    assert "UNTESTED" in capsys.readouterr().out


# ══════════════════ §4.5 温度 v2 守卫 ══════════════════


def test_selective_news_is_rejected_from_market_temperature():
    candidate = tv2.Candidate(name="l2_ticker_news", kind="news", scope="selective",
                              fixed_global_feed=True, coverage_normalized=True,
                              freshness_normalized=True)
    verdict = tv2.judge(candidate)
    assert verdict["verdict"] == tv2.REJECTED
    assert "选股结果" in verdict["reason"]


def test_derivative_needs_all_three_properties():
    partial = tv2.Candidate(name="pcr", kind="derivative", normalized=True)
    assert tv2.judge(partial)["missing"] == ["roll_adjusted", "lead_evidence"]
    full = tv2.Candidate(name="pcr", kind="derivative", normalized=True,
                         roll_adjusted=True, lead_evidence=True)
    assert tv2.judge(full)["verdict"] == tv2.ELIGIBLE


def test_news_needs_fixed_feed_and_normalization():
    candidate = tv2.Candidate(name="feed", kind="news", fixed_global_feed=True)
    assert set(tv2.judge(candidate)["missing"]) == {"coverage_normalized",
                                                    "freshness_normalized"}


def test_default_is_reject_not_allow():
    """没声明就是没满足。"""
    assert tv2.judge(tv2.Candidate(name="x", kind="derivative"))["verdict"] == tv2.REJECTED


def test_assert_no_selective_news_raises():
    with pytest.raises(ValueError, match="选择性"):
        tv2.assert_no_selective_news([tv2.Candidate(name="x", kind="news",
                                                    scope="selective")])


def test_screen_declares_the_wiring_class():
    report = tv2.screen([])
    assert "B 类" in report["wiring_class"]


def test_tv2_cli(capsys):
    assert tv2.main(["check"]) == 0
    assert "温度 v2" in capsys.readouterr().out


# ══════════════════ §4.5 档案债排期 ══════════════════


def test_schedule_is_derived_from_real_slo_readings():
    slo = {"pending_n": 18, "nightly_cap": 3, "reconcile_overdue": ["a"] * 19,
           "added_7d": 7, "digested_7d": 0}
    plan = debt_schedule.plan("2026-08-04", slo=slo)
    assert plan["init"]["nights_needed"] == 6         # 18 只 ÷ 3/晚
    assert len(plan["init"]["dates"]) == 6
    assert plan["reconcile"]["n"] == 19
    assert plan["throughput"]["converging"] is False


def test_schedule_changes_when_the_pool_grows():
    """排期由真读数派生 —— 写死成静态表的话池扩张时它会一直说「6 晚」。"""
    small = debt_schedule.plan("2026-08-04", slo={"pending_n": 18, "nightly_cap": 3})
    big = debt_schedule.plan("2026-08-04", slo={"pending_n": 60, "nightly_cap": 3})
    assert big["init"]["nights_needed"] > small["init"]["nights_needed"]


def test_schedule_rounds_up_partial_nights():
    plan = debt_schedule.plan("2026-08-04", slo={"pending_n": 7, "nightly_cap": 3})
    assert plan["init"]["nights_needed"] == 3


def test_empty_debt_needs_no_nights():
    plan = debt_schedule.plan("2026-08-04", slo={"pending_n": 0, "nightly_cap": 3})
    assert plan["init"]["nights_needed"] == 0 and plan["init"]["dates"] == []


def test_schedule_says_it_builds_no_new_mechanism():
    plan = debt_schedule.plan("2026-08-04", slo={"pending_n": 1})
    assert "不新建机制" in plan["mechanism_note"]


def test_schedule_render():
    md = debt_schedule.render(debt_schedule.plan(
        "2026-08-04", slo={"pending_n": 18, "nightly_cap": 3,
                           "reconcile_overdue": ["a"], "added_7d": 1,
                           "digested_7d": 5}))
    assert "季度对账" in md and "6 晚" in md and "✅ 收敛" in md
