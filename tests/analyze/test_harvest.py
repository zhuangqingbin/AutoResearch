"""Unit tests for autoresearch.analyze.harvest pure helpers (L1-row coercion, board limits, benchmarks)."""
import sys

import pandas as pd
import pytest

from autoresearch.analyze import blocks_us, harvest
from autoresearch.data import cache, contracts as dc


@pytest.mark.unit
def test_l1_float_coerces_and_rejects_nan():
    assert harvest._l1_float({"x": "12.5"}, "x") == 12.5
    assert harvest._l1_float({"x": 3}, "x") == 3.0
    assert harvest._l1_float({"x": None}, "x") is None
    assert harvest._l1_float({"x": "abc"}, "x") is None
    assert harvest._l1_float({"x": float("nan")}, "x") is None
    assert harvest._l1_float({}, "missing") is None


@pytest.mark.unit
def test_l1_flag_parses_boolish():
    assert harvest._l1_flag({"f": "是"}, "f") is True
    assert harvest._l1_flag({"f": "1"}, "f") is True
    assert harvest._l1_flag({"f": "true"}, "f") is True
    assert harvest._l1_flag({"f": 0}, "f") is False
    assert harvest._l1_flag({"f": "否"}, "f") is False
    assert harvest._l1_flag({"f": None}, "f") is None
    assert harvest._l1_flag({"f": float("nan")}, "f") is None


@pytest.mark.unit
def test_board_limit_bands_by_board():
    assert harvest._board_limit("830799.BJ")[1] == 0.30
    assert harvest._board_limit("300750.SZ")[1] == 0.20   # ChiNext
    assert harvest._board_limit("688981.SS")[1] == 0.20   # STAR
    assert harvest._board_limit("600519.SS")[1] == 0.10   # main board
    assert harvest._board_limit("AAPL")[1] is None         # US: no daily limit


@pytest.mark.unit
def test_benchmarks_by_market():
    assert harvest._benchmarks("600519.SS") == ["000300.SS"]
    assert "159915.SZ" in harvest._benchmarks("300750.SZ")   # ChiNext gets the ETF too
    assert harvest._benchmarks("NVDA") == ["SPY", "SOXX"]    # has a sector ETF
    assert harvest._benchmarks("KO") == ["SPY"]               # no mapped sector ETF


@pytest.mark.unit
def test_is_ashare_suffix_detection():
    assert harvest._is_ashare("600519.SS")
    assert harvest._is_ashare("000001.SZ")
    assert harvest._is_ashare("830799.BJ")
    assert not harvest._is_ashare("NVDA")
    assert not harvest._is_ashare("0700.HK")


# ───────────────────────── Task 6(D1.6):正确性六修 ─────────────────────────


@pytest.mark.unit
def test_news_window_filter(tmp_path, monkeypatch):
    """akshare stock_news_em 不接受日期参数——按 news_start<=日期<=end 过滤,窗外条目剔除。

    D1.1(走湖):`ashare_news_akshare` 现经 `cache.get_or_fetch` 取 `stock_news_em`,
    `monkeypatch.setattr(cache, "LAKE", tmp_path)` 强制本测试命中一份空湖(cache miss),
    确保真的落到下面打的 `ak.stock_news_em` 补丁,而不是读到别的测试/真跑留下的湖文件。
    """
    import akshare as ak

    df = pd.DataFrame({
        "新闻标题": ["窗内新闻A", "窗外新闻(太旧)", "窗内新闻B", "窗外新闻(太新)"],
        "发布时间": ["2026-08-20 10:00:00", "2026-08-01 09:00:00",
                   "2026-08-25 15:00:00", "2026-09-05 08:00:00"],
        "文章来源": ["财联社", "财联社", "证券时报", "财联社"],
    })
    monkeypatch.setattr(cache, "LAKE", tmp_path)
    monkeypatch.setattr(ak, "stock_news_em", lambda symbol: df)
    out = harvest.ashare_news_akshare("300308.SZ", start_date="2026-08-17", end_date="2026-08-31")
    assert out is not None
    assert "窗内新闻A" in out and "窗内新闻B" in out
    assert "太旧" not in out and "太新" not in out


@pytest.mark.unit
def test_news_dedup(tmp_path, monkeypatch):
    """同标题多来源(常见于转载)按标题去重,只留一条;结果按时间倒序。"""
    import akshare as ak

    df = pd.DataFrame({
        "新闻标题": ["重复标题", "重复标题", "独家标题(更新)"],
        "发布时间": ["2026-08-20 10:00:00", "2026-08-20 11:30:00", "2026-08-21 09:00:00"],
        "文章来源": ["财联社", "证券时报", "财联社"],
    })
    monkeypatch.setattr(cache, "LAKE", tmp_path)
    monkeypatch.setattr(ak, "stock_news_em", lambda symbol: df)
    out = harvest.ashare_news_akshare("300308.SZ", start_date="2026-08-17", end_date="2026-08-31")
    assert out is not None
    assert out.count("重复标题") == 1
    # 按时间倒序:更新的「独家标题」(08-21)必须排在「重复标题」(08-20)之前
    assert out.index("独家标题") < out.index("重复标题")


@pytest.mark.unit
def test_fwd_pe_uses_snapshot_close():
    """同进程已取的 verified-snapshot close 补进 consensus_eps_block 的 price=,不二次取网。"""
    snapshot_section = harvest._section(
        "Verified market snapshot (source of truth)",
        lambda: "### Latest verified OHLCV row\n\n| Field | Value |\n|---|---:|\n| Close | 88.50 |\n")
    price = harvest._consensus_eps_price(None, snapshot_section)
    assert price == 88.5

    df = pd.DataFrame({"year": ["2027"], "eps": [4.0], "np_yi": [50.0], "kind": ["YC"]})
    out = harvest.consensus_eps_block("300308.SZ", price, fetch=lambda code: df)
    assert "fwd-PE" in out

    # l1_row 的 close(slim 复用路径)优先于 snapshot 兜底
    assert harvest._consensus_eps_price({"close": 12.3}, snapshot_section) == 12.3


@pytest.mark.unit
def test_gnews_query_chinese():
    """A 股 gnews 查询词优先用显式 --name 中文简称,fallback identity 的英文 longName。"""
    assert harvest._company_query_name("中际旭创", {"company_name": "Suzhou Zhongji Innolight"}) \
        == "中际旭创"
    assert harvest._company_query_name(None, {"company_name": "Suzhou Zhongji Innolight"}) \
        == "Suzhou Zhongji Innolight"

    dispatch = harvest.external_sections("300308.SZ", "2026-08-28", slim=False, company_name="中际旭创")
    gnews_entries = [d for d in dispatch if d[0] == harvest._TITLE_GNEWS_ZH]
    assert gnews_entries and gnews_entries[0][2][0] == "中际旭创"


@pytest.mark.unit
def test_offline_short_circuits(monkeypatch):
    """AUTORESEARCH_OFFLINE 设置时 main() 提前退出 rc=2,零网络调用(identity 解析都不做)。"""
    monkeypatch.setenv("AUTORESEARCH_OFFLINE", "1")
    monkeypatch.setattr(sys, "argv", ["harvest", "AAPL", "2026-08-28"])
    called = []
    monkeypatch.setattr(harvest, "resolve_instrument_identity",
                        lambda *a, **k: called.append(1) or {})
    rc = harvest.main()
    assert rc == 2
    assert called == []


@pytest.mark.unit
def test_slim_anchors_single_source():
    """_SLIM_ANCHORS 单一事实源:scan/l4/producers 与本测试同引 contracts.agent_output;
    变异探针 —— 改一个标题字符串,两侧都会红。"""
    from autoresearch.contracts.agent_output import SLIM_ANCHORS
    from autoresearch.scan.l4 import producers

    assert producers._SLIM_ANCHORS is SLIM_ANCHORS   # 同一个对象,不是"抄一份值相等的"

    # harvest 侧:main() 真实用来渲染这几节的标题,经 _section 包一层就是 slim 产物里的锚点行。
    rendered = "".join(
        harvest._section(title, lambda: "x") for title in (
            "Verified market snapshot (source of truth)",
            "Market context — A股 (主力/技术/筹码/北向)",
            "Market context — US (regime/breadth/sector/VIX)",
            "Fundamentals overview",
        ))
    for anchor in SLIM_ANCHORS:
        if anchor == "### Latest verified OHLCV row":
            continue   # 来自 get_verified_market_snapshot 自己的正文,不是 _section 的标题包装
        assert anchor in rendered, f"标题改了没同步 SLIM_ANCHORS: {anchor!r}"


@pytest.mark.unit
def test_l1_reuse_columns_single_source():
    """L1_REUSE_COLUMNS 单一事实源:缺列时静默回退改为回退 + record_degradation(legit_empty)。"""
    from autoresearch.contracts.agent_output import L1_REUSE_COLUMNS

    dc.clear_degradations()
    full_row = dict.fromkeys(L1_REUSE_COLUMNS, 1.0)
    full_row["code"] = "300308"
    harvest.ashare_market_context_from_l1(full_row)
    assert not [r for r in dc.degradations() if r["endpoint"] == "L1_scored_full"]

    dc.clear_degradations()
    missing_col = L1_REUSE_COLUMNS[0]
    partial_row = {c: 1.0 for c in L1_REUSE_COLUMNS if c != missing_col}
    partial_row["code"] = "300308"
    out = harvest.ashare_market_context_from_l1(partial_row)   # 仍要能渲染(回退),不炸
    assert out
    recs = [r for r in dc.degradations() if r["endpoint"] == "L1_scored_full"]
    assert recs and missing_col in recs[-1]["reasons"][0]
    assert recs[-1]["kind"] == "legit_empty"


# ───────────────────────── Task 11(D1.5,Q7):full 档瘦身 ─────────────────────────


def _synth_daily_ohlcv(n_weeks: int = 52) -> pd.DataFrame:
    """`n_weeks` 周的合成日线(每周一~周五,Mon-Fri 5 天/周,首日=某个周一)。"""
    dates = pd.bdate_range("2025-09-01", periods=n_weeks * 5, freq="B")
    n = len(dates)
    return pd.DataFrame({
        "Date": dates,
        "Open": [100.0 + i * 0.1 for i in range(n)],
        "High": [100.0 + i * 0.1 + 1.0 for i in range(n)],
        "Low": [100.0 + i * 0.1 - 1.0 for i in range(n)],
        "Close": [100.0 + i * 0.1 + 0.5 for i in range(n)],
        "Volume": [1000 + i for i in range(n)],
    })


@pytest.mark.unit
def test_weekly_aggregate_shape_and_ohlc_semantics():
    df = _synth_daily_ohlcv(n_weeks=52)
    weekly = blocks_us.weekly_aggregate(df)
    assert 51 <= len(weekly) <= 53               # 52±1(周边界对齐可能多切出半周)

    # 用真实一周(2025-09-08 周一 ~ 09-12 周五,五个完整交易日)核对逐列语义。
    week_mask = (df["Date"] >= "2025-09-08") & (df["Date"] <= "2025-09-12")
    week_rows = df[week_mask].sort_values("Date")
    assert len(week_rows) == 5
    target = weekly[weekly["Date"] == pd.Timestamp("2025-09-12")].iloc[0]
    assert target["Open"] == week_rows.iloc[0]["Open"]      # O = 周内首日开
    assert target["Close"] == week_rows.iloc[-1]["Close"]   # C = 周内末日收
    assert target["High"] == week_rows["High"].max()        # H = 周内最高
    assert target["Low"] == week_rows["Low"].min()           # L = 周内最低
    assert target["Volume"] == week_rows["Volume"].sum()     # V = 周内成交量之和


@pytest.mark.unit
def test_weekly_aggregate_drops_weeks_with_no_trading_days():
    """周末/假期整周无数据的周不应该产生全 NaN 行。"""
    df = _synth_daily_ohlcv(n_weeks=4)
    weekly = blocks_us.weekly_aggregate(df)
    assert not weekly[["Open", "High", "Low", "Close", "Volume"]].isna().any().any()


_SYNTH_INDICATOR_TEXT = """## close_50_sma values from 2026-07-26 to 2026-08-25:

2026-08-25: 12.500000
2026-08-24: N/A: Not a trading day (weekend or holiday)
2026-08-23: N/A: Not a trading day (weekend or holiday)
2026-08-22: 12.300000
2026-08-21: 12.200000
2026-08-20: 12.100000
2026-08-19: 12.000000
2026-08-18: 11.900000

50 SMA: A medium-term trend indicator.

## rsi values from 2026-07-26 to 2026-08-25:

2026-08-25: 55.000000
2026-08-24: N/A: Not a trading day (weekend or holiday)
2026-08-23: N/A: Not a trading day (weekend or holiday)
2026-08-22: 60.000000
2026-08-21: 61.000000
2026-08-20: 62.000000
2026-08-19: 63.000000
2026-08-18: 64.000000

RSI: Measures momentum to flag overbought/oversold conditions.
"""


@pytest.mark.unit
def test_indicator_summary_table_row_count_matches_indicators():
    indicators = ["close_50_sma", "rsi"]
    rows = blocks_us.indicator_summary_table(_SYNTH_INDICATOR_TEXT, indicators)
    assert len(rows) == len(indicators)
    assert [r["indicator"] for r in rows] == indicators


@pytest.mark.unit
def test_indicator_summary_table_latest_skips_non_trading_days():
    rows = blocks_us.indicator_summary_table(_SYNTH_INDICATOR_TEXT, ["close_50_sma"])
    row = rows[0]
    assert row["latest"] == 12.5 and row["latest_date"] == "2026-08-25"


@pytest.mark.unit
def test_indicator_summary_table_prior_5d_skips_na_rows_direction_down():
    """5 个交易日前 = 从最新值往回数第 5 个**有效**(非 N/A)读数,不是第 5 行文本
    (中间夹了 2 行 N/A 非交易日)。close_50_sma:12.5(最新)→ 往回数 5 个有效值
    是 11.9(08-18)→ 上升;rsi:55 → 往回 5 个有效值是 64 → 下降。"""
    rows = blocks_us.indicator_summary_table(_SYNTH_INDICATOR_TEXT, ["close_50_sma", "rsi"])
    sma_row = next(r for r in rows if r["indicator"] == "close_50_sma")
    assert sma_row["prior_5d"] == 11.9 and sma_row["prior_date"] == "2026-08-18"
    assert sma_row["direction"] == "↑"
    rsi_row = next(r for r in rows if r["indicator"] == "rsi")
    assert rsi_row["prior_5d"] == 64.0
    assert rsi_row["direction"] == "↓"


@pytest.mark.unit
def test_indicator_summary_table_missing_indicator_degrades_to_dashes():
    rows = blocks_us.indicator_summary_table(_SYNTH_INDICATOR_TEXT, ["close_50_sma", "macd"])
    macd_row = next(r for r in rows if r["indicator"] == "macd")
    assert macd_row["latest"] is None and macd_row["direction"] == "—"


@pytest.mark.unit
def test_render_indicator_summary_table_is_a_markdown_table():
    rows = blocks_us.indicator_summary_table(_SYNTH_INDICATOR_TEXT, ["close_50_sma", "rsi"])
    md = blocks_us.render_indicator_summary_table(rows)
    assert "| 指标 | 末值 | 5 日前值 | 方向 |" in md
    assert "close_50_sma" in md and "12.5000" in md and "↑" in md


@pytest.mark.unit
def test_price_history_compact_block_renders_daily_and_weekly_sections(monkeypatch):
    df = _synth_daily_ohlcv(n_weeks=52)
    monkeypatch.setattr(blocks_us, "load_ohlcv", lambda symbol, curr_date: df)
    out = blocks_us.price_history_compact_block("300308.SZ", "2026-08-25")
    assert "近 60 交易日" in out and "近 52 周" in out
    daily_section = out.split("近 60 交易日")[1].split("近 52 周")[0]
    daily_rows = daily_section.count("\n|") - 2   # 减表头 2 行(标题行+分隔行)
    assert daily_rows == 60


@pytest.mark.unit
def test_price_history_compact_block_degrades_on_no_market_data(monkeypatch):
    from autoresearch.dataflows.symbol_utils import NoMarketDataError

    def _boom(symbol, curr_date):
        raise NoMarketDataError(symbol, symbol, "no rows")

    monkeypatch.setattr(blocks_us, "load_ohlcv", _boom)
    out = blocks_us.price_history_compact_block("ZZZZ", "2026-08-25")
    assert "OHLCV" in out and "失败" in out
