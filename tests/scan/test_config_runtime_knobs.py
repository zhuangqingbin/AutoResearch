"""P3 扩容 · 运行时键(scan 侧):prelude / runner / readiness / l4_tasks / l4_watch / self_review / report /
delivery / retention / overseas / tripwire / budgets.maturity(2026-09-27)。每块:config 值到达消费点,缺省 = 迁前常量。
"""
from __future__ import annotations

import json
from datetime import time, timedelta


def _cfg(tmp_path, monkeypatch, cfg: dict) -> None:
    p = tmp_path / "scan_config.jsonc"
    p.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr("autoresearch.scan.user_config.DEFAULT_PATH", p)


def _nocfg(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("autoresearch.scan.user_config.DEFAULT_PATH", tmp_path / "nope.jsonc")


def test_prelude_skip_steps_and_lookback(tmp_path, monkeypatch):
    from autoresearch.scan import prelude

    _cfg(tmp_path, monkeypatch, {"prelude": {"skip_steps": ["consensus"], "announcement_lookback": 2}})
    assert prelude.configured_skip_steps() == ("consensus",)
    assert prelude.announcement_lookback() == 2
    _nocfg(tmp_path, monkeypatch)
    assert prelude.configured_skip_steps() == () and prelude.announcement_lookback() == 5


def test_runner_clocks_follow_config(tmp_path, monkeypatch):
    from autoresearch.scan import scan_run

    _cfg(tmp_path, monkeypatch, {"runner": {"window_start": "20:00", "hard_stop": "02:30", "min_runner_minutes": 4,
                                            "run_timeout_minutes": 30, "live_run_window_min": 10, "kill_grace_s": 1.0,
                                            "subprocess_timeout_s": 60, "force_full": True}})
    r = scan_run.runner_cfg()
    assert r["window_start"] == "20:00" and r["hard_stop"] == "02:30" and r["min_runner_minutes"] == 4
    assert r["run_timeout_minutes"] == 30 and r["live_run_window"] == timedelta(minutes=10)
    assert r["kill_grace_s"] == 1.0 and r["subprocess_timeout_s"] == 60 and r["force_full"] is True
    _nocfg(tmp_path, monkeypatch)
    r = scan_run.runner_cfg()
    assert (r["window_start"], r["hard_stop"], r["min_runner_minutes"], r["run_timeout_minutes"]) == ("21:10", "01:00", 10, 180)
    assert r["live_run_window"] == timedelta(minutes=90) and r["kill_grace_s"] == 5.0 and r["subprocess_timeout_s"] == 1800
    assert r["force_full"] is False


def test_readiness_and_settle_follow_config(tmp_path, monkeypatch):
    from autoresearch.scan import prewarm, readiness

    _cfg(tmp_path, monkeypatch, {"readiness": {"min_rows": 10, "deadline": "23:00", "settle_hhmm": "20:00"}})
    r = readiness.readiness_cfg()
    assert r["min_rows"] == 10 and r["deadline"] == "23:00" and r["stable_polls"] == 2 and r["interval_s"] == 300
    assert r["late_recheck_s"] == 30
    assert prewarm.settle_minutes() == 20 * 60
    _nocfg(tmp_path, monkeypatch)
    assert readiness.readiness_cfg()["min_rows"] == 5300 and prewarm.settle_minutes() == 19 * 60 + 15


def test_l4_tasks_and_watch_follow_config(tmp_path, monkeypatch):
    from autoresearch.scan import l4_tasks, l4_watch

    _cfg(tmp_path, monkeypatch, {"l4_tasks": {"max_attempts": 5, "stale_after_s": 60, "slim_retries": 3,
                                              "slim_workers": 2, "slot_poll_s": 1.0, "slot_heartbeat_s": 7},
                                 "l4_watch": {"stale_min": 9, "interval_s": 2.0}})
    t = l4_tasks.tasks_cfg()
    assert t == {"max_attempts": 5, "stale_after_s": 60, "slim_retries": 3, "slim_workers": 2,
                 "slot_poll_s": 1.0, "slot_heartbeat_s": 7}
    assert l4_watch.watch_cfg() == {"stale_min": 9, "interval_s": 2.0}
    _nocfg(tmp_path, monkeypatch)
    assert l4_tasks.tasks_cfg()["max_attempts"] == 2 and l4_tasks.tasks_cfg()["stale_after_s"] == 3600
    assert l4_watch.watch_cfg() == {"stale_min": 30, "interval_s": 5.0}


def test_self_review_gates_follow_config(tmp_path, monkeypatch):
    from autoresearch.scan import self_review

    _cfg(tmp_path, monkeypatch, {"self_review": {"coverage_min": 0.5, "winner_rate_max": 50, "overheat": {"rsi6": 70},
                                                 "citation_min": 1, "intel_stale_days": {"v2": 10}}})
    s = self_review.self_review_cfg()
    assert s["coverage_min"] == 0.5 and s["winner_rate_max"] == 50 and s["overheat"] == {"pct60": 50, "rsi6": 70}
    assert s["citation_min"] == 1 and s["intel_stale_days"] == {"v1": 7, "v2": 10}
    assert s["composite_floor"] == 30.0 and s["sector_max"] == 0.6 and s["liveness_escalate_streak"] == 3
    _nocfg(tmp_path, monkeypatch)
    assert self_review.self_review_cfg()["winner_rate_max"] == 88


def test_report_and_delivery_budgets_follow_config(tmp_path, monkeypatch):
    from autoresearch.scan import brief, delivery, report_model

    _cfg(tmp_path, monkeypatch, {"report": {"brief_max_bytes": 100, "tone_chars": 5, "summary_warn_bytes": 1000},
                                 "delivery": {"bark_body_limit": 10, "http_timeout_s": 1.5, "mail_timeout_s": 2}})
    assert report_model.report_cfg()["brief_max_bytes"] == 100 and report_model.report_cfg()["tone_chars"] == 5
    assert report_model.report_cfg()["summary_warn_bytes"] == 1000 and report_model.report_cfg()["appendix_warn_bytes"] == 24 * 1024
    assert brief.max_bytes() == 100
    d = delivery.delivery_limits()
    assert d == {"bark_body_limit": 10, "http_timeout_s": 1.5, "mail_timeout_s": 2}
    _nocfg(tmp_path, monkeypatch)
    assert brief.max_bytes() == 3000 and delivery.delivery_limits()["bark_body_limit"] == 3000


def test_retention_overseas_tripwire_follow_config(tmp_path, monkeypatch):
    from autoresearch.scan import overseas, retention, salvage, transcript_binder, tripwire_watch
    from autoresearch.trace import capsule

    _cfg(tmp_path, monkeypatch, {"retention": {"bind_transcripts": True, "archive_transcript_agents": ["l4-card"],
                                               "lake_window_days": 9, "capsule_stale_after_min": 1,
                                               "salvage_window_min": 2, "salvage_max_hours": 3,
                                               "codex_transcript_lookback_days": 4},
                                 "overseas": {"horizon_days": 2, "rows_summary": 1},
                                 "tripwire": {"date_lead_days": 9}})
    r = retention.retention_cfg()
    assert r["archive_transcript_agents"] == ("l4-card",) and r["lake_window_days"] == 9
    assert capsule.stale_after() == timedelta(minutes=1)
    assert salvage.salvage_windows() == (timedelta(minutes=2), timedelta(hours=3))
    assert transcript_binder.codex_lookback_days() == 4
    assert overseas.overseas_cfg() == {"horizon_days": 2, "rows_summary": 1, "rows_brief": 1, "rows_tripwire": 3}
    assert tripwire_watch.date_lead_days() == 9
    _nocfg(tmp_path, monkeypatch)
    assert retention.retention_cfg()["lake_window_days"] == 70 and capsule.stale_after() == timedelta(minutes=5)
    assert overseas.overseas_cfg()["horizon_days"] == 14 and tripwire_watch.date_lead_days() == 3


def test_budget_maturity_targets_follow_config(tmp_path, monkeypatch):
    from autoresearch.scan import budget

    policy = budget.normalize_budgets({"maturity": {"phase2": {"p50": 40}}})
    assert policy["maturity"]["phase2"] == {"cost_reduction": 0.25, "p50": 40, "p90": 90}
    assert policy["maturity"]["phase1"] == {"cost_reduction": 0.15, "p50": 75, "p90": 100}
    assert budget.normalize_budgets(None)["maturity"]["phase1"]["p50"] == 75
