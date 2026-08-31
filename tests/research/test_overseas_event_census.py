"""D-0 海外事件窗普查(2026-08-29):合成事件 + 合成湖 + 合成账本,**零真网络**。

覆盖(任务书验收行逐条):窗口归属(两窄窗 + date_risk + outside)/ `DATE_ONLY` 不进窄窗 /
日期级聚合(同日多票只算一票)/ bootstrap 固定 seed 可复现 / Holm 校正算对 / n<20 走
「样本不足」分支 / 全市场与 finalist 分表不互相污染。

预注册见 `autoresearch/research/overseas_event_census.py` 模块 docstring 与设计稿 §10 D-0。
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from autoresearch.data.sources import official_event_calendar as oec
from autoresearch.research import overseas_event_census as oc

CN = ZoneInfo(oc.CN_TZ)
ET = ZoneInfo(oc.US_TZ)


# ───────────────────────── 夹具 ─────────────────────────


def _ev(local_date: date, *, quality=oec.TIME_QUALITY_DATE_ONLY, sched=None, eid="e1",
        first_seen=None, status=oec.STATUS_SCHEDULED, tz=oc.US_TZ,
        etype="macro_release") -> oec.ExternalEvent:
    return oec.ExternalEvent(
        event_id=eid, event_type=etype, subject="probe", scheduled_at_utc=sched,
        local_date=local_date, timezone=tz, time_quality=quality,
        source_url="https://example.com/x",
        first_seen_ts=first_seen or datetime(2000, 1, 1, tzinfo=oec.UTC),
        revision="r1", status=status)


def _anchors(d0="2026-08-26", d1="2026-08-27", d2="2026-08-28"):
    """(报告 21:00, T+1 14:45, T+2 09:30) —— 全部 CST aware。"""
    f = date.fromisoformat
    return (datetime.combine(f(d0), time(21, 0), tzinfo=CN),
            datetime.combine(f(d1), oc.ENTRY_CUTOFF, tzinfo=CN),
            datetime.combine(f(d2), oc.T2_OPEN, tzinfo=CN))


def _daily(n=120, *, flag_every=4, event_bump=0.0, seed=1):
    """日级面板 + 旗:`flag_every` 天一个事件日,事件日 mean_pp 加 `event_bump`。"""
    rng = np.random.default_rng(seed)
    days = [(date(2022, 3, 1) + timedelta(days=i)).strftime("%Y%m%d") for i in range(n)]
    flag = np.array([i % flag_every == 0 for i in range(n)])
    vals = rng.normal(0.0, 1.0, n) + flag * event_bump
    panel = pd.DataFrame({"mean_pp": vals, "absmean_pp": np.abs(vals) + 2.0,
                          "oc_t1_pp": rng.normal(0.0, 1.0, n), "n": 500}, index=days)
    panel.index.name = "date"
    return panel, pd.Series(flag, index=days)


# ───────────────────────── 1. 窗口归属:两窄窗 + date_risk + outside ─────────────────────────


def test_timed_event_lands_in_the_two_narrow_windows():
    """TIMED 事件按精确时刻落窗:入场前 / 持仓隔夜 / 窗外三种都要能出。"""
    rep, t1, t2 = _anchors()
    # 契约要求 local_date 与 scheduled_at_utc 在事件自己的时区下自洽 → TIMED 夹具用 CST 记本地日
    pre = _ev(date(2026, 8, 27), quality=oec.TIME_QUALITY_TIMED, tz=oc.CN_TZ,
              sched=datetime(2026, 8, 27, 10, 0, tzinfo=CN))          # 报告后、入场截止前
    hold = _ev(date(2026, 8, 27), quality=oec.TIME_QUALITY_TIMED, tz=oc.CN_TZ,
               sched=datetime(2026, 8, 27, 20, 30, tzinfo=CN))        # 入场后、T+2 开盘前
    out = _ev(date(2026, 8, 29), quality=oec.TIME_QUALITY_TIMED, tz=oc.CN_TZ,
              sched=datetime(2026, 8, 29, 20, 30, tzinfo=CN))         # T+2 开盘之后
    assert oc.event_flags(pre, rep, t1, t2) == {
        "window": oec.WINDOW_PRE_ENTRY, "holding": False, "pre_entry": True}
    assert oc.event_flags(hold, rep, t1, t2) == {
        "window": oec.WINDOW_HOLDING_OVERNIGHT, "holding": True, "pre_entry": False}
    got = oc.event_flags(out, rep, t1, t2)
    assert got["window"] == oec.WINDOW_OUTSIDE and not got["holding"] and not got["pre_entry"]


def test_date_only_never_enters_a_narrow_window():
    """§10 第 5 条:`DATE_ONLY` 只能是 `date_risk`,两窄窗对它永远关门。

    普查日级旗可以为真(ET 日与持仓窗相交是一句可验证的真陈述),但**契约窗**必须是
    `date_risk` —— 两者不得混为一谈。
    """
    rep, t1, t2 = _anchors()
    for q in (oec.TIME_QUALITY_DATE_ONLY, oec.TIME_QUALITY_UNKNOWN):
        got = oc.event_flags(_ev(date(2026, 8, 27), quality=q), rep, t1, t2)
        assert got["window"] == oec.WINDOW_DATE_RISK
        assert got["window"] not in (oec.WINDOW_PRE_ENTRY, oec.WINDOW_HOLDING_OVERNIGHT)
        assert got["holding"] is True                       # ET 日 T+1 恒包含持仓窗


def test_date_only_far_away_is_outside():
    rep, t1, t2 = _anchors()
    got = oc.event_flags(_ev(date(2026, 9, 15)), rep, t1, t2)
    assert got == {"window": oec.WINDOW_OUTSIDE, "holding": False, "pre_entry": False}


def test_pit_invisible_and_cancelled_events_get_no_flag():
    """PIT 不可见(`first_seen_ts > cutoff`)与已取消 → 契约窗 outside **且**两个旗都灭。

    旗不得比窗宽:普查旗是日级放宽,不是把 PIT / cancelled 也一起放宽。
    """
    rep, t1, t2 = _anchors()
    late = _ev(date(2026, 8, 27), first_seen=datetime(2026, 8, 27, 23, 0, tzinfo=CN))
    cancelled = _ev(date(2026, 8, 27), status=oec.STATUS_CANCELLED)
    for e in (late, cancelled):
        got = oc.event_flags(e, rep, t1, t2)
        assert got == {"window": oec.WINDOW_OUTSIDE, "holding": False, "pre_entry": False}


def test_et_day_of_t1_contains_the_holding_window_in_both_dst_regimes():
    """夏(EDT)/ 冬(EST)两侧:ET 日 T+1 都恒包含持仓窗 —— 写死 ET 偏移会在换季周错一小时。"""
    for d0, d1, d2 in (("2026-07-14", "2026-07-15", "2026-07-16"),      # EDT
                       ("2026-01-13", "2026-01-14", "2026-01-15")):     # EST
        rep, t1, t2 = _anchors(d0, d1, d2)
        assert oc.day_intersects(date.fromisoformat(d1), oc.US_TZ, t1, t2)
        got = oc.event_flags(_ev(date.fromisoformat(d1)), rep, t1, t2)
        assert got["holding"] is True and got["window"] == oec.WINDOW_DATE_RISK


def test_census_anchors_use_trade_days_for_t2_not_calendar_days():
    """T+2 取**交易日**序列的下一个,不是 T+1 + 1 自然日(周五 run 不许锚到周六 09:30)。"""
    P = ["20260828", "20260831", "20260901"]                # 五 / 一 / 二
    rep, t1, t2 = oc.census_anchors(P, 0)
    assert rep.date() == date(2026, 8, 28)
    assert (t1.date(), t1.timetz().replace(tzinfo=None)) == (date(2026, 8, 31), oc.ENTRY_CUTOFF)
    assert (t2.date(), t2.timetz().replace(tzinfo=None)) == (date(2026, 9, 1), oc.T2_OPEN)
    assert oc.census_anchors(P, 1) is None                  # i+2 越界 → 主尺本就算不出来


def test_flag_frame_only_covers_computable_days_and_counts_time_quality():
    P = ["20260824", "20260825", "20260826", "20260827", "20260828"]
    timed = _ev(date(2026, 8, 26), quality=oec.TIME_QUALITY_TIMED, tz=oc.CN_TZ,
                sched=datetime(2026, 8, 26, 20, 30, tzinfo=CN), eid="t1")
    frame = oc.flag_frame(P, {"CPI": [_ev(date(2026, 8, 26), eid="d1")], "FOMC": [timed]})
    assert list(frame.index) == P[:3]                       # 只有 i+2 存在的三天
    # 分析日 20260824 → T+1 = 0825,ET 日 0826 不与其持仓窗相交
    assert not bool(frame.loc["20260824", "CPI_holding"])
    # 分析日 20260825 → T+1 = 0826 → ET 日 0826 命中
    assert bool(frame.loc["20260825", "CPI_holding"])
    assert int(frame.loc["20260825", "CPI_n_date_only"]) == 1
    assert int(frame.loc["20260825", "CPI_n_timed"]) == 0
    assert int(frame.loc["20260825", "FOMC_n_timed"]) == 1


# ───────────────────────── 2. 日期级聚合:同日多票只算一票 ─────────────────────────


def test_ledger_panel_collapses_many_stocks_into_one_date(tmp_path):
    """同一 `analysis_date` 的 5 只票 → 面板里只有 1 行,值 = 等权均值(§10 第 2 条)。"""
    rows = []
    for i, g in enumerate([0.01, 0.02, 0.03, -0.01, -0.05]):
        rows.append({"run_id": "r", "analysis_date": "2026-08-25", "mode": "", "src": "run",
                     "code": f"00000{i}", "role": "finalist", "gap_c1_o2": g})
    rows.append({"run_id": "r", "analysis_date": "2026-08-26", "mode": "shadow", "src": "shared",
                 "code": "000009", "role": "finalist", "gap_c1_o2": 0.04})
    p = tmp_path / "recommendations.csv"
    pd.DataFrame(rows).to_csv(p, index=False)
    panel = oc.ledger_panel("finalist", ledger_csv=p)
    assert list(panel.index) == ["20260825", "20260826"]
    assert panel.loc["20260825", "n"] == 5
    assert panel.loc["20260825", "mean_pp"] == pytest.approx(0.0)      # (1+2+3-1-5)/5 = 0 pp
    assert panel.loc["20260825", "absmean_pp"] == pytest.approx(2.4)
    assert panel.loc["20260826", "n_shadow"] == 1 and panel.loc["20260826", "n_shared"] == 1


def test_ledger_panel_drops_impossible_gaps_and_missing_ruler(tmp_path):
    """`|gap| > GAP_CLIP` 在板制度下不可能 → 数据错,剔除;缺主尺的行同样不进面板。"""
    rows = [
        {"analysis_date": "2026-08-25", "code": "000001", "role": "finalist", "gap_c1_o2": 0.02},
        {"analysis_date": "2026-08-25", "code": "000002", "role": "finalist", "gap_c1_o2": 0.9},
        {"analysis_date": "2026-08-25", "code": "000003", "role": "finalist", "gap_c1_o2": None},
    ]
    p = tmp_path / "l.csv"
    pd.DataFrame(rows).to_csv(p, index=False)
    panel = oc.ledger_panel("finalist", ledger_csv=p)
    assert panel.loc["20260825", "n"] == 1
    assert panel.loc["20260825", "mean_pp"] == pytest.approx(2.0)


def test_ledger_panel_missing_file_is_empty_not_a_crash(tmp_path):
    assert oc.ledger_panel("finalist", ledger_csv=tmp_path / "nope.csv").empty


# ───────────────────────── 3. bootstrap 固定 seed 可复现 ─────────────────────────


def test_moving_block_bootstrap_is_reproducible_and_seed_sensitive():
    panel, flag = _daily(150, event_bump=0.0)
    v = panel["mean_pp"].to_numpy(float)
    f = flag.to_numpy(bool)
    a = oc.moving_block_diff(v, f, n_boot=800, seed=oc.SEED)
    b = oc.moving_block_diff(v, f, n_boot=800, seed=oc.SEED)
    c = oc.moving_block_diff(v, f, n_boot=800, seed=oc.SEED + 1)
    assert a == b                                            # 同 seed 逐字段相同
    assert a.block == oc.BLOCK and a.n_valid > 0
    assert (a.lo, a.hi) != (c.lo, c.hi)                      # 换 seed 就该动(否则 seed 是摆设)
    assert a.lo < a.point < a.hi
    assert a.p is not None and 0.0 < a.p <= 1.0


def test_bootstrap_p_floor_is_one_over_valid_draws_not_zero():
    """10,000 次抽样分辨不出比 1e-4 更小的 p —— 报 0 是伪精确。"""
    panel, flag = _daily(200, event_bump=6.0, seed=3)
    got = oc.moving_block_diff(panel["mean_pp"].to_numpy(float), flag.to_numpy(bool),
                               n_boot=500, seed=oc.SEED)
    assert got.p == pytest.approx(1.0 / got.n_valid)
    assert got.lo > 0                                        # 植入的正效应要被区间抓到


def test_bootstrap_returns_none_when_one_side_is_empty():
    v = np.arange(50, dtype=float)
    got = oc.moving_block_diff(v, np.zeros(50, dtype=bool), n_boot=100)
    assert got.point is None and got.p is None and got.lo is None


def test_newey_west_matches_ols_slope_and_gives_finite_t():
    panel, flag = _daily(150, event_bump=1.0, seed=5)
    v, f = panel["mean_pp"].to_numpy(float), flag.to_numpy(bool)
    beta, t, p = oc.newey_west_diff(v, f)
    assert beta == pytest.approx(v[f].mean() - v[~f].mean())
    assert t is not None and np.isfinite(t) and 0.0 <= p <= 1.0


def test_standardized_effect_is_diff_over_series_sd():
    panel, flag = _daily(80, event_bump=2.0, seed=9)
    v, f = panel["mean_pp"].to_numpy(float), flag.to_numpy(bool)
    assert oc.standardized_effect(v, f) == pytest.approx(
        (v[f].mean() - v[~f].mean()) / v.std(ddof=1))
    assert oc.standardized_effect(np.ones(10), np.array([True] * 5 + [False] * 5)) is None


# ───────────────────────── 4. Holm 校正 ─────────────────────────


def test_holm_matches_hand_computed_values_and_is_monotone():
    """[0.01,0.02,0.03,0.04] × m=4 → 逐步 [4p,3p,2p,1p] = [.04,.06,.06,.04],单调化后末位抬到 .06。"""
    assert oc.holm([0.01, 0.02, 0.03, 0.04]) == pytest.approx([0.04, 0.06, 0.06, 0.06])
    assert oc.holm([0.04, 0.03, 0.02, 0.01]) == pytest.approx([0.06, 0.06, 0.06, 0.04])
    assert oc.holm([0.5, 0.6]) == pytest.approx([1.0, 1.0])          # 封顶 1


def test_holm_m_total_does_not_shrink_when_a_family_is_unmeasurable():
    """样本不足的族不参与检验,但族规模 m 仍是预注册的 4 —— 少算一族不该让别人更容易过门。"""
    assert oc.holm([0.01, 0.02], m_total=4) == pytest.approx([0.04, 0.06])
    assert oc.holm([0.01, 0.02]) == pytest.approx([0.02, 0.02])       # 不给 m 就是 m=2
    with pytest.raises(ValueError):
        oc.holm([0.01, 0.02, 0.03], m_total=2)


def test_apply_holm_skips_thin_families_but_keeps_m_at_four():
    readouts = {"CPI": {"status": "OK", "p_raw": 0.01}, "NFP": {"status": "OK", "p_raw": 0.02},
                "FOMC": {"status": oc.THIN, "p_raw": None},
                "EARNINGS": {"status": oc.THIN, "p_raw": None}}
    got = oc.apply_holm(readouts)
    assert set(got) == {"CPI", "NFP"}
    assert got["CPI"] == pytest.approx(0.04) and got["NFP"] == pytest.approx(0.06)


def test_apply_fdr_is_separate_from_the_main_gate():
    got = oc.apply_fdr({"PCE": {"status": "OK", "p_raw": 0.01},
                        "GDP": {"status": "OK", "p_raw": 0.20}})
    assert got["PCE"] == pytest.approx(0.02) and got["GDP"] == pytest.approx(0.20)


# ───────────────────────── 5. n<20 → 样本不足分支 ─────────────────────────


@pytest.mark.parametrize("flag_every, expect_thin", [(20, True), (3, False)])
def test_family_readout_thin_branch_reports_no_pvalue(flag_every, expect_thin):
    """事件日 < 20 → `status=THIN` 且 p / CI / effect **全 None**(不足 20 日只报样本不足)。"""
    panel, flag = _daily(120, flag_every=flag_every)
    got = oc.family_readout(panel, flag, n_boot=300)
    assert (got["status"] == oc.THIN) is expect_thin
    if expect_thin:
        assert got["n_event_dates"] < oc.MIN_EVENT_DATES
        assert got["p_raw"] is None and got["ci_lo"] is None and got["effect"] is None
        assert got["diff_pp"] is None and got["subperiods"] is None
        assert oc.judge_shadow(got, holm_p=1e-9) == oc.THIN     # 样本门先判,p 再好也不给过
    else:
        assert got["p_raw"] is not None and got["effect"] is not None


def test_family_readout_thin_when_non_event_side_is_thin():
    """非事件日不足 20 也是样本不足:差值需要两侧,单侧再多也算不出对照。"""
    panel, flag = _daily(30, flag_every=1)
    got = oc.family_readout(panel, flag, n_boot=100)
    assert got["status"] == oc.THIN and got["n_non_dates"] == 0


def test_judge_shadow_requires_all_four_conditions():
    base = {"status": "OK", "diff_pp": 1.0, "effect": 0.5, "same_sign": True}
    assert oc.judge_shadow(base, holm_p=0.01) == oc.POS
    assert oc.judge_shadow({**base, "same_sign": False}, holm_p=0.01) == oc.UNPROVEN
    assert oc.judge_shadow({**base, "effect": 0.05}, holm_p=0.01) == oc.UNPROVEN
    assert oc.judge_shadow(base, holm_p=0.20) == oc.UNPROVEN
    assert oc.judge_shadow({**base, "diff_pp": -1.0}, holm_p=0.01) == oc.NEG
    assert oc.judge_shadow({"status": oc.THIN}, holm_p=0.001) == oc.THIN


def test_subperiod_same_sign_flag():
    """两子期同号才算稳定;一正一负 → `same_sign=False`(哪怕全期显著)。"""
    n = 400
    days = ([f"2022{m:02d}{d:02d}" for m in (3, 4, 5, 6) for d in range(1, 26)]
            + [f"2025{m:02d}{d:02d}" for m in (3, 4, 5, 6) for d in range(1, 26)])
    days = days[:n]
    flag = pd.Series([i % 3 == 0 for i in range(len(days))], index=days)
    early = [d < "2024" for d in days]
    vals = [(3.0 if f else 0.0) * (1 if e else -1) for f, e in zip(flag, early, strict=True)]
    panel = pd.DataFrame({"mean_pp": vals}, index=days)
    got = oc.family_readout(panel, flag, n_boot=200)
    assert got["status"] == "OK"
    assert got["subperiods"]["2022-23"]["diff_pp"] > 0
    assert got["subperiods"]["2024-26"]["diff_pp"] < 0
    assert got["same_sign"] is False


# ───────────────────────── 6. 全市场 / finalist 分表不互相污染 ─────────────────────────


def test_market_and_finalist_tables_are_computed_independently(tmp_path, monkeypatch):
    """两张表各算各的日期集合与基准:改动其中一张的输入,另一张逐字节不变(§10 第 4 条)。"""
    days = [(date(2022, 3, 1) + timedelta(days=i)).strftime("%Y%m%d") for i in range(60)]
    rng = np.random.default_rng(4)
    market = pd.DataFrame({"mean_pp": rng.normal(0, 1, 60), "absmean_pp": rng.random(60) + 2,
                           "oc_t1_pp": rng.normal(0, 1, 60), "n": 500}, index=days)
    fin_rows = [{"analysis_date": f"{d[:4]}-{d[4:6]}-{d[6:]}", "code": "000001",
                 "role": "finalist", "gap_c1_o2": 0.05} for d in days]
    p = tmp_path / "l.csv"
    pd.DataFrame(fin_rows).to_csv(p, index=False)

    monkeypatch.setattr(oc, "market_panel", lambda P, **kw: market)
    monkeypatch.setattr(oc, "build_events", lambda *a, **kw: (
        {"CPI": [_ev(date.fromisoformat(f"{d[:4]}-{d[4:6]}-{d[6:]}")) for d in days[::2]]},
        {"sources": {}}))
    monkeypatch.setattr(oc, "_lake_days", None, raising=False)
    from autoresearch.research import edge_census as ec
    monkeypatch.setattr(ec, "lake_trade_days", lambda *a, **kw: days)

    doc = oc.run_census(offline=True, with_earnings=False, n_boot=200, ledger_csv=p)
    mkt = doc["populations"][oc.POP_MARKET]
    fin = doc["populations"][oc.POP_FINALIST]
    assert mkt["status"] == "OK" and fin["status"] == "OK"
    # finalist 每天恒 +5pp → 组内方差为 0 → 差值恒 0;全市场是随机序列 → 两者不可能相等
    assert fin["families"]["CPI"]["diff_pp"] != mkt["families"]["CPI"]["diff_pp"]
    assert fin["n_dates"] != 0 and mkt["n_dates"] != 0

    # 污染探针:只改 finalist 账本 → 全市场那张表必须**逐字节不变**
    pd.DataFrame(fin_rows[:20]).to_csv(p, index=False)
    doc2 = oc.run_census(offline=True, with_earnings=False, n_boot=200, ledger_csv=p)
    assert doc2["populations"][oc.POP_MARKET] == mkt
    assert doc2["populations"][oc.POP_FINALIST] != fin


def test_pinned_and_finalist_rows_do_not_leak_into_each_other(tmp_path):
    rows = [{"analysis_date": "2026-08-25", "code": "000001", "role": "finalist", "gap_c1_o2": 0.02},
            {"analysis_date": "2026-08-25", "code": "000002", "role": "pinned", "gap_c1_o2": -0.04}]
    p = tmp_path / "l.csv"
    pd.DataFrame(rows).to_csv(p, index=False)
    fin = oc.ledger_panel("finalist", ledger_csv=p)
    pin = oc.ledger_panel("pinned", ledger_csv=p)
    assert fin.loc["20260825", "mean_pp"] == pytest.approx(2.0) and fin.loc["20260825", "n"] == 1
    assert pin.loc["20260825", "mean_pp"] == pytest.approx(-4.0) and pin.loc["20260825", "n"] == 1


# ───────────────────────── 7. 事件源:精确匹配 / 零网络 ─────────────────────────


def test_fred_exact_name_match_rejects_lookalike_releases():
    """`Research Consumer Price Index` 不是 CPI;每周的 `H.4.1` 不是 FOMC。

    源模块 `classify_family` 的子串规则两条都会误判 —— 本普查用精确名,故不继承那个缺陷。
    """
    frame = pd.DataFrame([
        {"release_id": "10", "release_name": "Consumer Price Index", "date": "2026-08-12"},
        {"release_id": "270", "release_name": "Research Consumer Price Index", "date": "2026-08-12"},
        {"release_id": "20", "release_name": "H.4.1 Factors Affecting Reserve Balances",
         "date": "2026-08-13"},
        {"release_id": "50", "release_name": "  Employment Situation  ", "date": "2026-08-07"},
    ])
    got = oc.fred_events(frame)
    assert [e.local_date for e in got["CPI"]] == [date(2026, 8, 12)]
    assert [e.local_date for e in got["NFP"]] == [date(2026, 8, 7)]
    assert "FOMC" not in got                                  # FRED 侧压根不产 FOMC 族
    from autoresearch.data.sources import fred_calendar as fc
    # 2026-08-29 已修:`H.4.1`(每周四的准备金余额报表)曾被判成 `fomc`,按它建族会凭空造出
    # 约 230 个「FOMC 日」,把一年 8 次的事件稀释成周事件。规则表已删掉 `h.4.1`,FOMC 真名录
    # 只走 `fomc_calendar.yaml`。本断言从「钉住缺陷」翻转为「钉住修复」。
    assert fc.classify_family("H.4.1 Factors Affecting Reserve Balances") == "other"
    assert fc.classify_family("Federal Open Market Committee Statement") == "fomc"


def test_fred_events_are_always_date_only_and_deduped():
    frame = pd.DataFrame([{"release_id": "10", "release_name": "Consumer Price Index",
                           "date": "2026-08-12"}] * 3)
    got = oc.fred_events(frame)["CPI"]
    assert len(got) == 1
    assert got[0].time_quality == oec.TIME_QUALITY_DATE_ONLY
    assert got[0].scheduled_at_utc is None and got[0].timezone == oc.US_TZ


def test_fetch_fred_frame_offline_uses_cache_only(tmp_path):
    """`--offline` 一条真请求都不许发:注入一个会炸的 fetch,offline 分支不得碰它。"""
    def boom(*a, **kw):                                       # pragma: no cover - 被断言不调用
        raise AssertionError("offline 模式发起了网络请求")
    assert oc.fetch_fred_frame("2026-08-01", "2026-08-31", cache_dir=tmp_path,
                               offline=True, fetch=boom).empty
    calls: list[tuple] = []

    def fake(lo, hi, **kw):
        calls.append((lo, hi, kw.get("limit")))
        return pd.DataFrame([{"release_id": "10", "release_name": "Consumer Price Index",
                              "date": "2026-08-12"}])
    oc.fetch_fred_frame("2026-08-01", "2026-08-31", cache_dir=tmp_path, fetch=fake)
    assert calls == [("2026-08-01", "2026-08-31", oc.FRED_PAGE_LIMIT)]   # 上限 1000,不是 10000
    again = oc.fetch_fred_frame("2026-08-01", "2026-08-31", cache_dir=tmp_path, fetch=boom)
    assert len(again) == 1 and len(calls) == 1                # 第二次全走缓存


def test_earnings_events_stay_date_only_even_though_yfinance_gives_a_time(tmp_path):
    """yfinance 是 T3 聚合源:它的 16:00 ET 只能进 `subject` 备查,**不得**升成 TIMED。"""
    def fake(sym):
        return pd.DataFrame({"ts": ["2026-08-26T16:00:00-04:00", "2021-01-01T16:00:00-05:00"]})
    got = oc.earnings_events(["NVDA"], "2022-03-01", "2026-08-31", cache_dir=tmp_path, fetch=fake)
    assert len(got) == 1                                      # 窗外那条被剔
    e = got[0]
    assert e.time_quality == oec.TIME_QUALITY_DATE_ONLY and e.scheduled_at_utc is None
    assert e.local_date == date(2026, 8, 26) and e.mapped_symbols == ("NVDA",)
    assert "16:00" in e.subject


def test_earnings_roster_load_map_is_empty_while_evidence_is_pending():
    """主族名单走 `load_map` —— 初版映射表全 `pending_evidence` ⇒ 名单为空。

    这不是 bug,是「没有真凭证的关系永远进不了任何报告」那条纪律在生效;raw 名单只能进探索表。
    """
    main, note_main = oc.mapped_symbols("2026-08-29", roster="load_map")
    raw, note_raw = oc.mapped_symbols("2026-08-29", roster="raw")
    assert main == [] and "load_map" in note_main
    assert len(raw) > 0 and "pending_evidence" in note_raw


# ───────────────────────── 8. 渲染 / 只读纪律 ─────────────────────────


def test_render_marks_thin_families_and_never_prints_a_pvalue_for_them():
    doc = {"meta": {"start": "2022-03-01", "end": "2026-08-31", "trade_days": 100,
                    "main_ruler": oc.MAIN, "block": 5, "n_boot": 10, "seed": 1,
                    "min_event_dates": 20, "event_counts": {}, "time_quality": {}},
           "populations": {oc.POP_MARKET: {
               "status": "OK", "n_dates": 100, "date_range": ["20220301", "20260831"],
               "families": {f: {"status": oc.THIN, "n_event_dates": 3} for f in oc.MAIN_FAMILIES},
               "dispersion": {}, "exploratory": {}}}}
    text = oc.render(doc)
    assert oc.THIN in text
    assert "0.0" not in text.split("### 主族")[1].split("###")[0]     # 主族表里不该出现任何 p 值
    assert "不得外推" in text


def test_module_writes_nothing_into_run_dirs(tmp_path, monkeypatch):
    """只读纪律:`run_census` 的任何分支都不许 mkdir/写 run 目录 / staging / lake。"""
    import builtins
    opened: list[str] = []
    real_open = builtins.open

    def spy(file, mode="r", *a, **kw):
        if any(m in str(mode) for m in ("w", "a", "x", "+")):
            opened.append(str(file))
        return real_open(file, mode, *a, **kw)
    monkeypatch.setattr(builtins, "open", spy)
    monkeypatch.setattr(oc, "market_panel", lambda P, **kw: pd.DataFrame())
    monkeypatch.setattr(oc, "build_events", lambda *a, **kw: ({}, {"sources": {}}))
    from autoresearch.research import edge_census as ec
    monkeypatch.setattr(ec, "lake_trade_days", lambda *a, **kw: [])
    oc.run_census(offline=True, with_earnings=False, ledger_csv=tmp_path / "nope.csv")
    assert not [p for p in opened if "/scan/" in p or "/lake/" in p], opened
