"""时间锚:这份报告什么时候才能真的下单(设计稿 2026-08-28 §2.4 G1)。"""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from autoresearch.scan import exec_anchor as ea

# 2026-08-24(一)~ 08-28(五)一个整周,外加下周一,足够覆盖迟到 3 个 session。
SESSIONS = ["2026-08-24", "2026-08-25", "2026-08-26", "2026-08-27", "2026-08-28",
            "2026-08-31"]


def _block(approved: datetime, analysis_date: str = "2026-08-25", **kw):
    return ea.build_execution_block(analysis_date, approved_at=approved,
                                    sessions=SESSIONS, **kw)


def test_evening_report_is_actionable_next_session():
    """08-25 数据日、当晚 21:49 写完 → T+1(08-26)尾盘来得及。"""
    got = _block(datetime(2026, 8, 25, 21, 49))
    assert got["first_available_session"] == "2026-08-26"
    assert (got["exec_lag"], got["staleness_sessions"]) == (0, 1)
    assert got["actionability_status"] == ea.ACTIONABLE


def test_report_written_after_t1_cutoff_slips_a_session():
    """真实案发:`20260826_2000` —— 数据日 08-25,08-26 20:00 才写完,T+1 早收盘了。"""
    got = _block(datetime(2026, 8, 26, 20, 0))
    assert got["first_available_session"] == "2026-08-27"
    assert got["exec_lag"] == 1
    assert got["actionability_status"] == ea.LATE_REVALIDATION_REQUIRED


def test_cutoff_is_operational_not_the_exchange_bell():
    """14:46 获批:交易所 14:57 才截止,但人读完报告再下单已经来不及 → 顺延。"""
    assert ea.EXEC_DECISION_CUTOFF < ea.EXCHANGE_CUTOFF
    late = _block(datetime(2026, 8, 26, 14, 46))
    early = _block(datetime(2026, 8, 26, 14, 44))
    assert late["first_available_session"] == "2026-08-27"
    assert early["first_available_session"] == "2026-08-26"


def test_three_sessions_late_is_expired():
    got = _block(datetime(2026, 8, 28, 20, 0))
    assert got["exec_lag"] >= ea._EXPIRY_LAG_SESSIONS
    assert got["actionability_status"] == ea.EXPIRED


def test_weekend_gap_counts_sessions_not_calendar_days():
    """08-28(五)20:00 写完 08-27 的数据 → 下一个 session 是 08-31(一),不是 08-29。"""
    got = _block(datetime(2026, 8, 28, 20, 0), analysis_date="2026-08-27")
    assert got["first_available_session"] == "2026-08-31"
    assert got["exec_lag"] == 1              # 跳过周末只算一个 session 的迟到


def test_failed_run_is_never_actionable():
    got = _block(datetime(2026, 8, 25, 21, 49), business_status="FAILED")
    assert got["actionability_status"] == ea.FAILED


def test_missing_approval_is_unknown_not_actionable():
    got = ea.build_execution_block("2026-08-25", approved_at=None, sessions=SESSIONS)
    assert got["first_available_session"] is None
    assert got["actionability_status"] == ea.UNKNOWN


def test_read_execution_prefers_the_stored_block(tmp_path):
    run = tmp_path / "20260825-0826_2000"
    run.mkdir()
    stored = {"schema_version": 1, "analysis_date": "2026-08-25",
              "actionability_status": ea.ACTIONABLE, "ready_quality": "measured"}
    (run / "manifest.json").write_text(
        json.dumps({"analysis_date": "2026-08-25", "execution": stored}), encoding="utf-8")
    assert ea.read_execution(run)["ready_quality"] == "measured"


def test_read_execution_estimates_legacy_runs_and_says_so(tmp_path, monkeypatch):
    """老 run 没有 execution 块:`generated_at` 只是 lower bound,必须标 estimated。"""
    monkeypatch.setattr(ea, "trading_sessions", lambda *a, **k: (SESSIONS, "stub"))
    run = tmp_path / "20260826_2000"
    run.mkdir()
    (run / "manifest.json").write_text(
        json.dumps({"analysis_date": "2026-08-25",
                    "generated_at": "2026-08-26T20:00:25"}), encoding="utf-8")
    got = ea.read_execution(run)
    assert got["ready_quality"] == "estimated"
    assert got["ready_source"] == "manifest_generated_at"
    assert got["first_available_session"] == "2026-08-27"     # 真实案发日的判定
    assert got["actionability_status"] == ea.LATE_REVALIDATION_REQUIRED


def test_read_execution_never_reads_the_data_date_off_a_legacy_dir_name(tmp_path, monkeypatch):
    """legacy 目录名首段是**跑动日**;manifest 缺数据日时宁可 UNKNOWN 也不拿它顶替。"""
    monkeypatch.setattr(ea, "trading_sessions", lambda *a, **k: (SESSIONS, "stub"))
    run = tmp_path / "20260826_2000"
    run.mkdir()
    (run / "manifest.json").write_text(json.dumps({"generated_at": "2026-08-26T20:00:25"}),
                                       encoding="utf-8")
    got = ea.read_execution(run)
    assert got["analysis_date"] is None
    assert got["actionability_status"] == ea.UNKNOWN


def test_read_execution_uses_the_new_dir_name_when_manifest_is_silent(tmp_path, monkeypatch):
    monkeypatch.setattr(ea, "trading_sessions", lambda *a, **k: (SESSIONS, "stub"))
    run = tmp_path / "20260825-0826_2000"
    run.mkdir()
    (run / "manifest.json").write_text(json.dumps({"generated_at": "2026-08-26T20:00:25"}),
                                       encoding="utf-8")
    assert ea.read_execution(run)["analysis_date"] == "2026-08-25"


@pytest.mark.parametrize("status", [ea.ACTIONABLE, ea.LATE_REVALIDATION_REQUIRED,
                                    ea.EXPIRED, ea.FAILED, ea.UNKNOWN])
def test_status_vocabulary_is_closed(status):
    assert status in {ea.ACTIONABLE, ea.LATE_REVALIDATION_REQUIRED, ea.EXPIRED,
                      ea.FAILED, ea.UNKNOWN}


def test_weekday_heuristic_is_labelled_when_no_calendar(monkeypatch):
    """认不出节假日的那一级必须自报家门,否则调用方会把它当真日历用。"""
    import autoresearch.common.workspace as ws

    monkeypatch.setattr(ws, "lake_root", lambda: __import__("pathlib").Path("/nonexistent"))
    monkeypatch.setattr("autoresearch.data.tushare_source._pro",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no token")))
    days, quality = ea.trading_sessions("2026-08-24", "2026-08-28")
    assert quality == "weekday_heuristic"
    assert days == ["2026-08-24", "2026-08-25", "2026-08-26", "2026-08-27", "2026-08-28"]


def _run_with_gate4(tmp_path, name, *, generated_at, gate4_utc, status="SUCCEEDED"):
    run = tmp_path / name
    (run / "trace" / "stage_results").mkdir(parents=True)
    (run / "manifest.json").write_text(
        json.dumps({"analysis_date": "2026-08-25", "generated_at": generated_at}),
        encoding="utf-8")
    (run / "trace" / "stage_results" / "gate4.json").write_text(
        json.dumps({"stage": "gate4", "status": status, "recorded_at": gate4_utc}),
        encoding="utf-8")
    return run


def test_measured_gate4_beats_the_publish_time_estimate(tmp_path, monkeypatch):
    monkeypatch.setattr(ea, "trading_sessions", lambda *a, **k: (SESSIONS, "stub"))
    run = _run_with_gate4(tmp_path, "20260825-0825_2149",
                          generated_at="2026-08-25T21:49:50",
                          gate4_utc="2026-08-25T13:49:52.092997Z")   # UTC → 本地 21:49:52
    got = ea.read_execution(run)
    assert got["ready_quality"] == "measured"
    assert got["decision_approved_at"].startswith("2026-08-25T21:49:52")


def test_stale_gate4_from_shared_staging_is_rejected(tmp_path, monkeypatch):
    """🚨 实测污染:`20260730_0116` 与 `20260730_2132` 的 gate4 时间戳逐字节相同。

    staging 按**数据日**键、同日重跑原地覆盖 —— 直接采信会把 07-30 21:32 才发布的
    run 判成"前一晚就批准了",凭空造出一个假 `ACTIONABLE`。
    """
    monkeypatch.setattr(ea, "trading_sessions", lambda *a, **k: (SESSIONS, "stub"))
    run = _run_with_gate4(tmp_path, "20260825-0826_2000",
                          generated_at="2026-08-26T20:00:00",
                          gate4_utc="2026-08-25T13:05:40.266192Z")   # 早一整天 = 残留
    got = ea.read_execution(run)
    assert got["ready_quality"] == "estimated"
    assert "共享 staging" in got["ready_source"]
    assert got["actionability_status"] == ea.LATE_REVALIDATION_REQUIRED   # 不被伪装成可执行


def test_gate4_that_did_not_pass_is_not_an_approval(tmp_path, monkeypatch):
    monkeypatch.setattr(ea, "trading_sessions", lambda *a, **k: (SESSIONS, "stub"))
    run = _run_with_gate4(tmp_path, "20260825-0825_2149",
                          generated_at="2026-08-25T21:49:50",
                          gate4_utc="2026-08-25T13:49:52Z", status="FAILED")
    assert ea.read_execution(run)["ready_quality"] == "estimated"


def test_stored_estimate_is_upgraded_once_gate4_lands(tmp_path, monkeypatch):
    """发布时 GATE4 还没跑,存的是发布时刻估算;门过之后读到实测要就地升级。

    不修的话新 run 反而不如老 run —— 老 run 没有存储块,走的正是实测那条路。
    """
    monkeypatch.setattr(ea, "trading_sessions", lambda *a, **k: (SESSIONS, "stub"))
    run = _run_with_gate4(tmp_path, "20260825-0825_2149",
                          generated_at="2026-08-25T21:49:50",
                          gate4_utc="2026-08-25T13:49:52.092997Z")
    man = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    man["execution"] = ea.build_execution_block(
        "2026-08-25", approved_at=datetime(2026, 8, 25, 21, 49, 50),
        brief_written_at=datetime(2026, 8, 25, 21, 49, 50), sessions=SESSIONS,
        ready_source="publish_time", ready_quality="estimated")
    (run / "manifest.json").write_text(json.dumps(man), encoding="utf-8")

    got = ea.read_execution(run)
    assert got["ready_quality"] == "measured"
    assert got["ready_source"] == "gate4_stage_result"
    # manifest 一个字没被改动(发布后不再变是 MANIFEST/ROOT 的不变量)
    on_disk = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    assert on_disk["execution"]["ready_quality"] == "estimated"


def test_a_measured_block_is_returned_as_is(tmp_path, monkeypatch):
    monkeypatch.setattr(ea, "trading_sessions", lambda *a, **k: (SESSIONS, "stub"))
    run = tmp_path / "20260825-0825_2149"
    run.mkdir()
    stored = {"analysis_date": "2026-08-25", "ready_quality": "measured",
              "actionability_status": ea.ACTIONABLE, "sentinel": "不许被重算"}
    (run / "manifest.json").write_text(
        json.dumps({"analysis_date": "2026-08-25", "execution": stored}), encoding="utf-8")
    assert ea.read_execution(run)["sentinel"] == "不许被重算"
