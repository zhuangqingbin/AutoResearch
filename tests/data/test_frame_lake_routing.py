"""生产帧走湖 + 半载快照守卫 + B 级死腿记账(P0 T3)。NO network —— 全部 fake pro / fake 湖。

病灶(spec 2026-08-29 §1.3 第一行):`fetch_universe_tushare` 裸调
`pro.daily_basic / daily×3 / stock_basic / moneyflow / margin_detail / stk_factor_pro /
cyq_perf / hk_hold`,只有 60 日 `daily` 面板走湖 —— 于是 A 级数据契约(`contracts.CONTRACTS`)
**在生产路径上一次都没执行过**,它只在 prewarm / backfill / doctor 里跑过。三条实测后果:

  ① `north`/`rz` 两组自 07-13 起每次扫描非空率 **0.0**,而 `margin_detail` 失败只有一行
     `print` —— 零记账、零告警,composite 静默重归一;
  ② 08-26 与 07-29 的 `chip`/`tech` 组非空率 **0.5472**(21:xx 的 tushare 半载快照),
     `check_market_frame` 只查"列在不在",覆盖率半张表是 NaN 照样放行;
  ③ 08-28 普查回填对**同样的** `hk_hold 20260826` 拿到 958 行 —— 生产的"空返回"是 T 晚
     取数撞上发布滞后,不是真空。

本文件锁三件事:取数路由(湖 + 全字段)、帧出口的覆盖率判据、B 级腿失败必须留痕。
"""
from __future__ import annotations

import os
import time

import pandas as pd
import pytest

from autoresearch.data import cache, contracts, tushare_source
from autoresearch.data.contracts import DataContractError

_LAST, _D60, _DYS = "20260826", "20260602", "20260105"
_CODES = ["000001.SZ", "600000.SH", "300750.SZ", "002415.SZ", "601318.SH"]


# ───────────────────────── 合成端点帧 ─────────────────────────


def _endpoint_frame(endpoint: str, params: dict) -> pd.DataFrame:
    """每个端点的**全字段**合成帧(列名与 tushare 真身一致)。"""
    n = len(_CODES)
    if endpoint == "daily_basic":
        return pd.DataFrame({
            "ts_code": _CODES, "trade_date": _LAST, "close": 10.0, "turnover_rate": 2.0,
            "volume_ratio": 1.1, "pe": 20.0, "pe_ttm": 21.0, "pb": 2.0, "ps": 3.0,
            "dv_ratio": 1.5, "total_share": 1e5, "float_share": 9e4, "free_share": 8e4,
            "total_mv": 1_000_000.0, "circ_mv": 900_000.0,
        })
    if endpoint == "daily":
        d = str(params.get("trade_date"))
        px = {_LAST: 10.0, _D60: 8.0, _DYS: 5.0}[d]
        return pd.DataFrame({
            "ts_code": _CODES, "trade_date": d, "open": px, "high": px, "low": px,
            "close": px, "pre_close": px, "change": 0.0, "pct_chg": 1.0,
            "vol": 1e5, "amount": 500_000.0,
        })
    if endpoint == "stock_basic":
        return pd.DataFrame({"ts_code": _CODES, "name": [f"股{i}" for i in range(n)],
                             "list_date": "20200101", "market": "主板", "industry": "银行"})
    if endpoint == "moneyflow":
        return pd.DataFrame({
            "ts_code": _CODES, "trade_date": _LAST,
            "buy_sm_amount": 800.0, "sell_sm_amount": 1500.0,
            "buy_lg_amount": 5000.0, "sell_lg_amount": 2000.0,
            "buy_elg_amount": 3000.0, "sell_elg_amount": 1000.0, "net_mf_amount": 5000.0,
        })
    if endpoint == "cyq_perf":
        return pd.DataFrame({"ts_code": _CODES, "winner_rate": 60.0, "cost_15pct": 8.0,
                             "cost_50pct": 9.0, "cost_85pct": 11.0, "weight_avg": 9.5})
    if endpoint == "stk_factor_pro":
        return pd.DataFrame({
            "ts_code": _CODES, "close": 10.0, "ma_qfq_5": 9.5, "ma_qfq_10": 9.0,
            "ma_qfq_20": 8.5, "ma_qfq_60": 8.0, "rsi_qfq_6": 55.0, "rsi_qfq_12": 52.0,
            "macd_qfq": 0.1,
        })
    if endpoint == "hk_hold":
        return pd.DataFrame({"code": [c[:6] for c in _CODES], "trade_date": _LAST,
                             "ts_code": _CODES, "name": "x", "vol": 1e4, "ratio": 3.0,
                             "exchange": "SH"})
    if endpoint == "margin_detail":
        return pd.DataFrame({"ts_code": _CODES, "trade_date": _LAST, "rzye": 1e8,
                             "rqye": 1e6, "rzmre": 1_500_000.0, "rzche": 1e6, "rzrqye": 1e8})
    raise AssertionError(f"未预期的端点 {endpoint!r}")


def _fundamentals() -> pd.DataFrame:
    return pd.DataFrame({"code": [c[:6] for c in _CODES], "rev": 1e9, "rev_yoy": 10.0,
                         "np_": 1e8, "np_yoy": 12.0, "np_qoq": 3.0, "roe": 9.0,
                         "gross_margin": 30.0, "cfo_ps": 1.0, "industry": "银行",
                         "np_yoy_prev": 8.0, "rev_yoy_prev": 7.0})


class _TrapPro:
    """任何 `pro.<endpoint>()` 调用都被记下来 —— 裸调 = 绕过湖 = A 级契约不执行。"""

    def __init__(self, seen: list[str]):
        self._seen = seen

    def __getattr__(self, name: str):
        def _call(**params):
            self._seen.append(name)
            return _endpoint_frame(name, params)
        return _call


@pytest.fixture
def universe_world(monkeypatch, tmp_path):
    """把 `fetch_universe_tushare` 的世界全部替换掉:湖 / pro / 日期解析 / 基本面。

    返回 `(seen_lake, seen_raw)`:前者 = 经 `cache.get_or_fetch` 的 (endpoint, params, today)
    三元组,后者 = 裸 `pro.X()` 的端点名。
    """
    seen_lake: list[tuple[str, dict, str | None]] = []
    seen_raw: list[str] = []

    def _fake_get_or_fetch(endpoint, params, today=None, fetch=None):
        seen_lake.append((endpoint, dict(params), today))
        return _endpoint_frame(endpoint, params)

    monkeypatch.setattr(cache, "LAKE", tmp_path / "lake", raising=True)
    monkeypatch.setattr(cache, "get_or_fetch", _fake_get_or_fetch, raising=True)
    monkeypatch.setattr(tushare_source, "_pro", lambda: _TrapPro(seen_raw), raising=True)
    monkeypatch.setattr(tushare_source, "resolve_momentum_dates",
                        lambda pro, d: (_LAST, _D60, _DYS), raising=True)
    monkeypatch.setattr(tushare_source, "assert_tushare_ready", lambda pro, last: None,
                        raising=True)
    monkeypatch.setattr(tushare_source, "fetch_fundamentals_yjbb",
                        lambda d: _fundamentals(), raising=True)
    contracts.clear_degradations()
    yield seen_lake, seen_raw
    contracts.clear_degradations()


# ───────────────────────── ① 取数路由 ─────────────────────────


def test_universe_fetch_goes_through_lake(universe_world):
    """生产帧的每条 tushare 取数都必须经 `get_or_fetch` —— 否则 A 级契约在生产路径上从不执行。"""
    seen_lake, seen_raw = universe_world
    out = tushare_source.fetch_universe_tushare("2026-08-26")

    assert seen_raw == [], f"这些端点绕过了湖(裸 pro 调用):{seen_raw}"
    assert {"daily", "daily_basic", "moneyflow", "cyq_perf", "stk_factor_pro", "stock_basic",
            "hk_hold", "margin_detail"} <= {e for e, _, _ in seen_lake}
    # daily 三次:as-of / 60 日前 / 年初 —— 三个不同的 key,不是同一天取三遍
    assert {p["trade_date"] for e, p, _ in seen_lake if e == "daily"} == {_LAST, _D60, _DYS}
    # 死腿复活的证据面:north(hk_ratio)/ rz(rz_buy_intensity)两组在帧里真有值
    assert out["hk_ratio"].notna().all() and out["rz_buy_intensity"].notna().all()
    assert out["rsi6"].notna().all() and out["winner_rate"].notna().all()


def test_lake_calls_never_pass_narrow_fields(universe_world):
    """**入湖一律全字段**:湖里一个 key 只有一个 parquet,窄 `fields` 首写会把窄表钉成当天快照
    (2026-07-12 M1 对拍:daily 被钉成两列 → volprice 整组 NaN → 全市场打分失真 98.8%)。"""
    seen_lake, _ = universe_world
    tushare_source.fetch_universe_tushare("2026-08-26")
    offenders = [(e, p) for e, p, _ in seen_lake if "fields" in p]
    assert offenders == [], f"这些入湖调用带了窄 fields:{offenders}"


def test_lake_calls_carry_analysis_date_as_today(universe_world):
    """`today=分析日` 决定"已结算/盘中未结算"分支 —— 漏传会把当天的帧误判成可入湖的历史。"""
    seen_lake, _ = universe_world
    tushare_source.fetch_universe_tushare("2026-08-26")
    assert {t for _, _, t in seen_lake} == {"2026-08-26"}


# ───────────────────────── ② 半载快照守卫(帧出口覆盖率) ─────────────────────────


def _frame(n=4000, **over):
    df = pd.DataFrame({"code": [f"{i:06d}" for i in range(n)], "close": 10.0,
                       "mktcap_yi": 100.0, "pct_60d": 5.0, "main_net_ratio": 0.01,
                       "cmf_20": 0.1, "obv_mom_20": 0.2, "rsi6": 55.0, "winner_rate": 60.0})
    for k, v in over.items():
        df[k] = v
    return df


def _half(df: pd.DataFrame, col: str, rate: float = 0.5472) -> pd.DataFrame:
    """把某列打成 08-26 实测的非空率(0.5472)—— 行在、列在、值一半是 NaN。"""
    df = df.copy()
    cut = int(len(df) * rate)
    df.loc[df.index[cut:], col] = float("nan")
    return df


def test_half_loaded_snapshot_is_recorded_not_silent():
    """21:xx 的半载快照(A 级列非空率 0.547)此前**静默**通过 —— composite 会悄悄重归一。

    2026-08-29 复核:0.547 落在 [0.50, 0.90) → **记账 + 告警,不阻断**。
    立案引的两个现场(08-26 / 07-29)实测就是 0.547;若这一档直接抛,那两晚不是
    「带着半载 chip/tech 出报告」而是**整趟不存在** —— 诊断书写的病是「降级不留痕」,
    不是「不许降级」。这条用例锁的就是「看得见」而不是「必须死」。
    """
    contracts._DEGRADED.clear()
    frame = _half(_half(_frame(), "rsi6"), "winner_rate")
    assert contracts.check_market_frame(frame) is frame        # 不阻断
    keys = {d.get("key") for d in contracts.degradations()}
    assert {"coverage_rsi6", "coverage_winner_rate"} <= keys    # 但留痕


def test_coverage_below_block_line_still_raises():
    """低于阻断线(0.50)= 这一组实际上不在了 —— 与「整列全 NaN」同族,照旧抛。"""
    frame = _half(_frame(), "rsi6", rate=0.40)
    with pytest.raises(DataContractError, match="阻断线"):
        contracts.check_market_frame(frame)


def test_half_loaded_message_names_the_thin_columns_and_threshold():
    """留痕必须点名是哪一列、实测多少、门槛多少 —— 否则现场只剩一句"帧违约"。"""
    contracts._DEGRADED.clear()
    df = _half(_frame(), "main_net_ratio")
    assert contracts.check_market_frame(df) is df
    notes = " ".join(str(d) for d in contracts.degradations())
    assert "main_net_ratio" in notes and "0.547" in notes and "0.90" in notes


def test_blocking_message_names_the_column_and_the_block_line():
    """真阻断时同样要点名,并且说的是**阻断线**不是告警线。"""
    with pytest.raises(DataContractError) as ei:
        contracts.check_market_frame(_half(_frame(), "main_net_ratio", rate=0.30))
    msg = str(ei.value)
    assert "main_net_ratio" in msg and "0.300" in msg and "0.50" in msg


def test_full_coverage_frame_still_passes():
    """覆盖率齐全 → 逐字节旧行为(这条是覆盖率判据的"不误伤"对照)。"""
    df = _frame()
    assert contracts.check_market_frame(df) is df


def test_coverage_criterion_only_bites_below_threshold():
    """0.95 覆盖率不该被判违约 —— 门槛是 0.90,不是"必须满"。"""
    df = _half(_frame(), "rsi6", rate=0.95)
    assert contracts.check_market_frame(df) is df


def test_absent_enhancement_column_is_not_upgraded_to_blocking():
    """列**整根缺席**(低权限 token 没有 stk_factor_pro)仍是合法降级,不是 A 级阻断 ——
    覆盖率判据管的是"列在场但半张表是 NaN",两回事。"""
    df = _frame().drop(columns=["rsi6", "winner_rate"])
    assert contracts.check_market_frame(df) is df


def test_b_tier_thin_column_degrades_with_a_record():
    """B 级(hk_ratio / rz_buy_intensity)覆盖率不足 → 不阻断,但**必须记账**。"""
    contracts.clear_degradations()
    try:
        df = _frame()
        df["hk_ratio"] = float("nan")
        df.loc[df.index[:200], "hk_ratio"] = 3.0          # 非空率 0.05
        assert contracts.check_market_frame(df) is df     # 不抛
        recs = [r for r in contracts.degradations() if "hk_ratio" in str(r.get("key"))]
        assert recs and recs[0]["endpoint"] == "market_frame"
    finally:
        contracts.clear_degradations()


# ───────────────────────── ③ B 级死腿必须留痕 ─────────────────────────


def test_b_tier_leg_failure_is_recorded(universe_world, monkeypatch):
    """margin_detail 失败此前只 print:rz 组从此恒 NaN 而没有任何一行降级记账。"""
    seen_lake, _ = universe_world
    real = cache.get_or_fetch

    def _boom(endpoint, params, today=None, fetch=None):
        if endpoint == "margin_detail":
            raise RuntimeError("抱歉,您没有访问该接口的权限")
        return real(endpoint, params, today=today, fetch=fetch)

    monkeypatch.setattr(cache, "get_or_fetch", _boom, raising=True)
    out = tushare_source.fetch_universe_tushare("2026-08-26")

    assert out["rz_buy_intensity"].isna().all()           # 降级本身合法
    assert any(d["endpoint"] == "margin_detail" for d in contracts.degradations()), \
        "margin_detail 失败必须留一行降级记账,不能只有 print"


def test_hk_hold_empty_leaves_a_record(universe_world, monkeypatch):
    """08-28 普查证明 hk_hold 的"空"多半是发布滞后 —— 取数日不改(Q8 未裁),但空必须留痕。"""
    real = cache.get_or_fetch

    def _empty_hk(endpoint, params, today=None, fetch=None):
        if endpoint == "hk_hold":
            return pd.DataFrame()
        return real(endpoint, params, today=today, fetch=fetch)

    monkeypatch.setattr(cache, "get_or_fetch", _empty_hk, raising=True)
    tushare_source.fetch_universe_tushare("2026-08-26")
    assert any(d["endpoint"] == "hk_hold" for d in contracts.degradations())


def test_factor_legs_failure_is_recorded(universe_world, monkeypatch):
    """`_fetch_factors` 的两条腿此前也只 print(tech / chip 两组静默清零)。"""
    real = cache.get_or_fetch

    def _boom(endpoint, params, today=None, fetch=None):
        if endpoint in ("stk_factor_pro", "cyq_perf"):
            raise RuntimeError("网络超时")
        return real(endpoint, params, today=today, fetch=fetch)

    monkeypatch.setattr(cache, "get_or_fetch", _boom, raising=True)
    tushare_source.fetch_universe_tushare("2026-08-26")
    eps = {d["endpoint"] for d in contracts.degradations()}
    assert "stk_factor_pro" in eps


def test_a_tier_contract_error_is_not_swallowed_into_a_degradation(universe_world, monkeypatch):
    """A 级契约违约**不得**被 `except Exception` 吞成降级(Global Constraints 数据契约条)。

    这是 08-26 半载快照能走到打分层的第二道口子:`_fetch_factors` 的 `except Exception`
    会把 `DataContractError` 一起接住,于是"阻断"退化成"返回 None"。
    """
    real = cache.get_or_fetch

    def _violate(endpoint, params, today=None, fetch=None):
        if endpoint == "stk_factor_pro":
            raise DataContractError("[数据契约·A级] stk_factor_pro 违约:行数腰斩")
        return real(endpoint, params, today=today, fetch=fetch)

    monkeypatch.setattr(cache, "get_or_fetch", _violate, raising=True)
    with pytest.raises(DataContractError):
        tushare_source.fetch_universe_tushare("2026-08-26")


# ───────────────────────── ④ stock_basic 的周刷新 ─────────────────────────


def test_stock_basic_lake_copy_is_refreshed_weekly(monkeypatch, tmp_path):
    """`stock_basic` 的 policy 键是 static("存在即命中、永不重取")—— 工作树那份 mtime 停在
    06-22。接进湖后必须按 ISO 周刷新,否则新上市的票永远没有 name/list_date,剔次新门与 ST
    门都看不见它们。"""
    lake = tmp_path / "lake"
    monkeypatch.setattr(cache, "LAKE", lake, raising=True)
    path = lake / "stock_basic" / "static.parquet"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"stale-parquet")                    # 内容无关:只测"刷不刷"
    os.utime(path, (time.time() - 30 * 86400,) * 2)       # 30 天前 = 上上个月那一周

    calls: list[str] = []

    def _fake(endpoint, params, today=None, fetch=None):
        calls.append(endpoint)
        assert not path.exists(), "陈旧副本必须先被挪开,否则 get_or_fetch 命中的还是旧的"
        return _endpoint_frame("stock_basic", params)

    monkeypatch.setattr(cache, "get_or_fetch", _fake, raising=True)
    out = tushare_source._fetch_stock_basic("2026-08-26")
    assert calls == ["stock_basic"] and len(out) == len(_CODES)


def test_stock_basic_within_the_same_week_is_not_refetched(monkeypatch, tmp_path):
    """本周已有副本 → 走湖命中(不挪、不重拉);一周一份,不是每天重拉。"""
    lake = tmp_path / "lake"
    monkeypatch.setattr(cache, "LAKE", lake, raising=True)
    path = lake / "stock_basic" / "static.parquet"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"fresh-parquet")                    # mtime = 现在 = 本 ISO 周

    def _fake(endpoint, params, today=None, fetch=None):
        assert path.exists(), "本周副本不该被挪开"
        return _endpoint_frame("stock_basic", params)

    monkeypatch.setattr(cache, "get_or_fetch", _fake, raising=True)
    tushare_source._fetch_stock_basic("2026-08-26")
    assert path.exists() and path.read_bytes() == b"fresh-parquet"


def test_stock_basic_refresh_failure_falls_back_to_last_weeks_roster(monkeypatch, tmp_path):
    """周刷新取数失败 → 把上周副本放回去 + 记账并沿用(宁可用上周名单,也不要没有名单)。"""
    lake = tmp_path / "lake"
    monkeypatch.setattr(cache, "LAKE", lake, raising=True)
    path = lake / "stock_basic" / "static.parquet"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"last-week")
    os.utime(path, (time.time() - 30 * 86400,) * 2)

    state = {"n": 0}

    def _fake(endpoint, params, today=None, fetch=None):
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("connection reset")        # 刷新那一跳失败
        assert path.exists() and path.read_bytes() == b"last-week", "旧副本必须被放回去"
        return _endpoint_frame("stock_basic", params)

    monkeypatch.setattr(cache, "get_or_fetch", _fake, raising=True)
    contracts.clear_degradations()
    try:
        out = tushare_source._fetch_stock_basic("2026-08-26")
        assert len(out) == len(_CODES) and state["n"] == 2
        assert any(d["endpoint"] == "stock_basic" for d in contracts.degradations())
    finally:
        contracts.clear_degradations()


# ───────────────────── hk_hold 代码域(2026-08-30 裁定) ─────────────────────

def _hk_rows(n: int = 4, exchange: str = "HK") -> pd.DataFrame:
    """港股通**南向**持股行:`ts_code` 是港股代码,补零后与 A 股撞号。"""
    return pd.DataFrame({
        "ts_code": [f"{i:05d}.HK" for i in range(1, n + 1)],
        "ratio": [1.85, 1.96, 1.53, 0.14][:n],
        "exchange": [exchange] * n,
    })


def test_southbound_rows_never_become_a_share_codes(monkeypatch):
    """南向(HK)行绝不许变成 A 股 code —— 那不是缺数,是**串号**。

    `_code6("00001.HK")` = `"000001"` = 平安银行;`00002.HK`(中电控股)= `000002` 万科A。
    2026-08-30 实测:当日 958 行南向数据里 297 个落在真实 A 股代码上,于是平安银行
    拿着长和的持股比例进了 composite 的 north 组。字段有值、量级也像,只是属于别人 ——
    这类错比缺数难发现得多,所以这条守卫锁的是**代码域**,不是行数。
    """
    contracts._DEGRADED.clear()
    monkeypatch.setattr(tushare_source, "_lake_fetch",
                        lambda *a, **k: _hk_rows())
    out = tushare_source._fetch_hk_hold("20260826", "2026-08-26")
    assert out is None, f"南向行漏进来了:{out}"
    notes = " ".join(str(d) for d in contracts.degradations())
    assert "南向" in notes and "2024-08" in notes, "缺席必须留痕,并说清为什么没有合法数据源"


def test_genuine_northbound_rows_still_pass_through(monkeypatch):
    """有真北向(SH/SZ)时照常用 —— 这条守卫是过滤,不是一刀切关掉 north 组。"""
    north = pd.DataFrame({
        "ts_code": ["600519.SH", "000858.SZ"],
        "ratio": [3.2, 1.1],
        "exchange": ["SH", "SZ"],
    })
    monkeypatch.setattr(tushare_source, "_lake_fetch", lambda *a, **k: north)
    out = tushare_source._fetch_hk_hold("20260826", "2026-08-26")
    assert out is not None
    assert set(out["code"]) == {"600519", "000858"}
    assert out["hk_ratio"].tolist() == [3.2, 1.1]


def test_mixed_frame_keeps_only_northbound(monkeypatch):
    """南北混装时只留北向 —— 端点一个名字装两个方向,过滤必须按 exchange 而非行数。"""
    mixed = pd.concat([_hk_rows(2), pd.DataFrame({
        "ts_code": ["600519.SH"], "ratio": [3.2], "exchange": ["SH"]})], ignore_index=True)
    monkeypatch.setattr(tushare_source, "_lake_fetch", lambda *a, **k: mixed)
    out = tushare_source._fetch_hk_hold("20260826", "2026-08-26")
    assert set(out["code"]) == {"600519"}, "南向行必须被滤掉"
