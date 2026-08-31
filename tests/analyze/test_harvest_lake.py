"""D1.1:已登记 tushare/akshare 端点改走 `cache.get_or_fetch`(走湖)。

范围裁定(task-9 brief):只改「endpoints.py 已登记且 data/sources 已有 fetcher」的
DataFrame 端点;`forecast`/`express` 因 policy key="date"(全市场按 ann_date 切,不含
ts_code)与本仓这两个端点"整表拉单票全历史、无日期参数"的天然用法不相容(直传 ts_code
会让 `_cache_key` 退化成 "unkeyed",不同票互相踩踏同一份湖文件)——本任务范围收窄,
不碰(见 `tushare_enrich.ashare_calendar_ts` docstring 记名)。

一端点一测,函数名 `test_<ep>_goes_through_lake`;每个测试都监控 `calls` 列表证明
`cache.get_or_fetch` 被正确以该端点名调用(修前:直调 `pro.X`/`ak.X`,列表为空)。
"""
from __future__ import annotations

import pandas as pd
import pytest

from autoresearch.analyze import harvest
from autoresearch.common import uzi_lenses
from autoresearch.data import tushare_enrich, tushare_source


def _fake_gof(rows_by_endpoint: dict[str, pd.DataFrame], calls: list[tuple[str, dict]]):
    """通用假 `get_or_fetch`:按 endpoint 返回预置帧,同时把 (endpoint, params) 记进 calls。"""
    def fake(endpoint, params, today=None, fetch=None):
        calls.append((endpoint, dict(params)))
        return rows_by_endpoint.get(endpoint, pd.DataFrame())
    return fake


# ───────────────────────── tushare_enrich.ashare_market_context_ts ─────────────────────────
# moneyflow(date 键,10 日趋势,逐日全市场帧)+ stk_factor_pro/cyq_perf/hk_hold(date 键,单日全市场帧)


def _mkt_context_env(monkeypatch, calls, rows_by_endpoint):
    monkeypatch.setattr(tushare_enrich, "_pro", lambda: object())
    monkeypatch.setattr(tushare_enrich, "_last_trade", lambda pro, curr_date: "20260828")
    monkeypatch.setattr(tushare_enrich, "_trade_days",
                        lambda pro, start, end: ["20260826", "20260827", "20260828"])
    monkeypatch.setattr("autoresearch.data.cache.get_or_fetch", _fake_gof(rows_by_endpoint, calls))


@pytest.mark.unit
def test_moneyflow_goes_through_lake(monkeypatch):
    calls: list[tuple[str, dict]] = []
    moneyflow_day = pd.DataFrame({
        "ts_code": ["300308.SZ", "000001.SZ"],
        "trade_date": ["20260828", "20260828"],
        "net_mf_amount": [1234.0, -999.0],
    })
    _mkt_context_env(monkeypatch, calls, {"moneyflow": moneyflow_day})
    out = tushare_enrich.ashare_market_context_ts("300308.SZ", "2026-08-28")
    mf_calls = [c for c in calls if c[0] == "moneyflow"]
    assert len(mf_calls) == 3                              # 逐日(3 个交易日)各取一次全市场帧
    assert all(c[1] == {"trade_date": d} for c, d in
              zip(mf_calls, ("20260826", "20260827", "20260828"), strict=True))
    assert "主力资金流" in (out or "") and "+0.12 亿" in out


@pytest.mark.unit
def test_stk_factor_pro_goes_through_lake(monkeypatch):
    calls: list[tuple[str, dict]] = []
    sf_day = pd.DataFrame({
        "ts_code": ["300308.SZ", "000001.SZ"],
        "close": [100.0, 10.0], "ma_qfq_5": [95.0, 11.0], "ma_qfq_10": [90.0, 12.0],
        "ma_qfq_20": [85.0, 13.0], "ma_qfq_60": [80.0, 14.0],
        "rsi_qfq_6": [55.0, 40.0], "rsi_qfq_12": [50.0, 45.0],
        "macd_qfq": [0.1, -0.1], "macd_dif_qfq": [0.2, -0.2], "macd_dea_qfq": [0.1, -0.1],
    })
    _mkt_context_env(monkeypatch, calls, {"stk_factor_pro": sf_day})
    out = tushare_enrich.ashare_market_context_ts("300308.SZ", "2026-08-28")
    sfp_calls = [c for c in calls if c[0] == "stk_factor_pro"]
    assert sfp_calls == [("stk_factor_pro", {"trade_date": "20260828"})]   # 单日全市场,不带 ts_code
    assert "多头排列 **是**" in (out or "")     # 300308.SZ 行:100>95>90>85>80


@pytest.mark.unit
def test_cyq_perf_goes_through_lake(monkeypatch):
    calls: list[tuple[str, dict]] = []
    cy_day = pd.DataFrame({
        "ts_code": ["000001.SZ", "300308.SZ"],
        "winner_rate": [10.0, 92.0], "cost_50pct": [8.0, 88.0],
    })
    _mkt_context_env(monkeypatch, calls, {"cyq_perf": cy_day})
    out = tushare_enrich.ashare_market_context_ts("300308.SZ", "2026-08-28")
    cyp_calls = [c for c in calls if c[0] == "cyq_perf"]
    assert cyp_calls == [("cyq_perf", {"trade_date": "20260828"})]
    assert "获利比例 **92%**" in (out or "") and "高位获利盘重" in out


@pytest.mark.unit
def test_hk_hold_goes_through_lake(monkeypatch):
    calls: list[tuple[str, dict]] = []
    hk_day = pd.DataFrame({"ts_code": ["300308.SZ"], "ratio": [3.21]})
    _mkt_context_env(monkeypatch, calls, {"hk_hold": hk_day})
    out = tushare_enrich.ashare_market_context_ts("300308.SZ", "2026-08-28")
    hk_calls = [c for c in calls if c[0] == "hk_hold"]
    assert hk_calls == [("hk_hold", {"trade_date": "20260828"})]
    assert "持股占比 **3.21%**" in (out or "")


@pytest.mark.unit
def test_hk_hold_absent_ticker_renders_no_holding_note(monkeypatch):
    """本票不在当天北向表里(过滤后 0 行)→ 原有『非标的/无持股记录』文案不变。"""
    calls: list[tuple[str, dict]] = []
    hk_day = pd.DataFrame({"ts_code": ["000001.SZ"], "ratio": [1.0]})   # 不含 300308.SZ
    _mkt_context_env(monkeypatch, calls, {"hk_hold": hk_day})
    out = tushare_enrich.ashare_market_context_ts("300308.SZ", "2026-08-28")
    assert "非标的/无持股记录" in (out or "")


# ───────────────────────── tushare_enrich.ashare_shareholder_ts ─────────────────────────
# stk_holdernumber / pledge_stat(as_of 键,entity=ts_code,天然吻合原有"整表拉取"用法)


@pytest.mark.unit
def test_stk_holdernumber_goes_through_lake(monkeypatch):
    calls: list[tuple[str, dict]] = []
    hn = pd.DataFrame({"end_date": ["20250630", "20251231"], "ann_date": ["20250715", "20260115"],
                       "holder_num": [50000, 45000]})
    monkeypatch.setattr(tushare_enrich, "_pro", lambda: object())
    monkeypatch.setattr("autoresearch.data.cache.get_or_fetch",
                        _fake_gof({"stk_holdernumber": hn, "pledge_stat": pd.DataFrame()}, calls))
    out = tushare_enrich.ashare_shareholder_ts("300308.SZ", curr_date="2026-08-30")
    hn_calls = [c for c in calls if c[0] == "stk_holdernumber"]
    assert hn_calls == [("stk_holdernumber", {"ts_code": "300308.SZ"})]
    assert "45,000" in (out or "")


@pytest.mark.unit
def test_pledge_stat_goes_through_lake(monkeypatch):
    calls: list[tuple[str, dict]] = []
    pl = pd.DataFrame({"end_date": ["20260630"], "pledge_ratio": [12.5]})
    monkeypatch.setattr(tushare_enrich, "_pro", lambda: object())
    monkeypatch.setattr("autoresearch.data.cache.get_or_fetch",
                        _fake_gof({"stk_holdernumber": pd.DataFrame(), "pledge_stat": pl}, calls))
    out = tushare_enrich.ashare_shareholder_ts("300308.SZ", curr_date="2026-08-30")
    pl_calls = [c for c in calls if c[0] == "pledge_stat"]
    assert pl_calls == [("pledge_stat", {"ts_code": "300308.SZ"})]
    assert "12.5%" in (out or "")


@pytest.mark.unit
def test_forecast_and_express_intentionally_not_lake_routed(monkeypatch):
    """范围红线:forecast/express 的 key="date" 与"整表按 ts_code 取"用法不相容(会
    "unkeyed" 互相踩踏)——本任务不改,保持直调 `pro.forecast`/`pro.express`。"""
    calls: list[tuple[str, dict]] = []

    class FakePro:
        def forecast(self, ts_code):
            return pd.DataFrame({"ann_date": ["20260715"], "end_date": ["20260630"],
                                 "type": ["预增"], "p_change_min": [10.0], "p_change_max": [20.0],
                                 "change_reason": ["需求旺盛"]})

        def express(self, ts_code):
            return pd.DataFrame()

    monkeypatch.setattr(tushare_enrich, "_pro", lambda: FakePro())
    monkeypatch.setattr("autoresearch.data.cache.get_or_fetch", _fake_gof({}, calls))
    out = tushare_enrich.ashare_calendar_ts("300308.SZ", "2026-08-30")
    assert calls == []                                     # get_or_fetch 全程未被调用
    assert "业绩预告" in (out or "")                          # FakePro 直调路径仍然产出


# ───────────────────────── uzi_lenses.margin_trend_ts(margin_detail,date 键,20 日趋势) ─────────────────────────


@pytest.mark.unit
def test_margin_detail_goes_through_lake(monkeypatch):
    calls: list[tuple[str, dict]] = []
    cal = pd.DataFrame({"cal_date": ["20260618", "20260619", "20260620"]})
    rows_by_day = {
        "20260618": pd.DataFrame({"ts_code": ["300308.SZ"], "trade_date": ["20260618"],
                                  "rzye": [1.0e8], "rzrqye": [0.0]}),
        "20260619": pd.DataFrame({"ts_code": ["300308.SZ"], "trade_date": ["20260619"],
                                  "rzye": [1.05e8], "rzrqye": [0.0]}),
        "20260620": pd.DataFrame({"ts_code": ["300308.SZ"], "trade_date": ["20260620"],
                                  "rzye": [1.1e8], "rzrqye": [0.0]}),
    }

    class FakePro:
        def trade_cal(self, exchange, start_date, end_date, is_open):
            return cal

    def fake_gof(endpoint, params, today=None, fetch=None):
        calls.append((endpoint, dict(params)))
        assert endpoint == "margin_detail"
        return rows_by_day.get(params["trade_date"], pd.DataFrame())

    monkeypatch.setattr(tushare_source, "_pro", lambda: FakePro())
    monkeypatch.setattr("autoresearch.data.cache.get_or_fetch", fake_gof)
    out = uzi_lenses.margin_trend_ts("300308.SZ", curr_date="2026-06-20")
    assert len(calls) == 3
    assert {c[1]["trade_date"] for c in calls} == {"20260618", "20260619", "20260620"}
    assert out is not None and "1.10亿" in out


# ───────────────────────── uzi_lenses.lhb_seats(top_inst,date 键,已有逐日循环) ─────────────────────────


@pytest.mark.unit
def test_top_inst_goes_through_lake(monkeypatch):
    calls: list[tuple[str, dict]] = []
    day_hit = pd.DataFrame({"ts_code": ["300308.SZ"], "net_buy": [500000.0], "exalter": ["机构专用"]})

    def fake_gof(endpoint, params, today=None, fetch=None):
        calls.append((endpoint, dict(params)))
        assert endpoint == "top_inst"
        return day_hit if params["trade_date"] == "20260828" else pd.DataFrame()

    monkeypatch.setattr(tushare_source, "_pro", lambda: object())
    monkeypatch.setattr(tushare_source, "resolve_momentum_dates", lambda pro, date: ("20260828", "", ""))
    monkeypatch.setattr(tushare_source, "_trade_days", lambda pro, start, end: ["20260827", "20260828"])
    monkeypatch.setattr("autoresearch.data.cache.get_or_fetch", fake_gof)
    out = uzi_lenses.lhb_seats("300308.SZ", "2026-08-28")
    assert len(calls) == 2 and all(c[0] == "top_inst" for c in calls)
    assert out is not None and "机构专用净买 **+50万**" in out


# ───────────────────────── harvest.py 的 4 个 akshare as_of 端点 ─────────────────────────


@pytest.mark.unit
def test_stock_news_em_goes_through_lake(monkeypatch):
    calls: list[tuple[str, dict]] = []
    df = pd.DataFrame({"新闻标题": ["窗内新闻A"], "发布时间": ["2026-08-20 10:00:00"],
                       "文章来源": ["财联社"]})
    monkeypatch.setattr("autoresearch.data.cache.get_or_fetch", _fake_gof({"stock_news_em": df}, calls))
    out = harvest.ashare_news_akshare("300308.SZ", start_date="2026-08-17", end_date="2026-08-31")
    assert calls == [("stock_news_em", {"symbol": "300308"})]
    assert "窗内新闻A" in (out or "")


@pytest.mark.unit
def test_stock_restricted_release_queue_em_goes_through_lake(monkeypatch):
    calls: list[tuple[str, dict]] = []
    rel = pd.DataFrame({"解禁时间": ["2026-09-15"], "解禁数量": [1.0e8],
                        "占流通市值比例": [0.05], "限售股类型": ["定增机构配售"]})
    monkeypatch.setattr(
        "autoresearch.data.cache.get_or_fetch",
        _fake_gof({"stock_restricted_release_queue_em": rel}, calls))
    out = harvest.ashare_corporate_calendar("300308.SZ", "2026-08-30")
    assert calls == [("stock_restricted_release_queue_em", {"symbol": "300308"})]
    assert "限售解禁队列" in (out or "")


@pytest.mark.unit
def test_stock_lhb_stock_statistic_em_goes_through_lake(monkeypatch):
    calls: list[tuple[str, dict]] = []
    stat = pd.DataFrame({"代码": ["300308"], "上榜次数": [3], "最近上榜日": ["2026-08-20"],
                         "龙虎榜净买额": [5.0e7], "买方机构次数": [1], "卖方机构次数": [0]})
    monkeypatch.setattr(
        "autoresearch.data.cache.get_or_fetch",
        _fake_gof({"stock_lhb_stock_statistic_em": stat}, calls))
    out = harvest.ashare_market_context("300308.SZ", "2026-08-28")
    lhb_calls = [c for c in calls if c[0] == "stock_lhb_stock_statistic_em"]
    assert lhb_calls == [("stock_lhb_stock_statistic_em", {"symbol": "近三月"})]
    assert "龙虎榜（近三月）" in (out or "")


@pytest.mark.unit
def test_stock_zh_a_gdhs_detail_em_goes_through_lake(monkeypatch):
    calls: list[tuple[str, dict]] = []
    df = pd.DataFrame({"股东户数统计截止日": ["2026-06-30"], "股东户数-本次": [40000],
                       "股东户数-增减": [-2000], "股东户数-增减比例": [-4.76],
                       "户均持股市值": [150000.0]})
    monkeypatch.setattr(
        "autoresearch.data.cache.get_or_fetch",
        _fake_gof({"stock_zh_a_gdhs_detail_em": df}, calls))
    out = harvest.ashare_shareholder_count("300308.SZ")
    assert calls == [("stock_zh_a_gdhs_detail_em", {"symbol": "300308"})]
    assert "40,000" in (out or "")
