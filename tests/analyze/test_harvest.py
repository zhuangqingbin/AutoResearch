"""Unit tests for autoresearch.analyze.harvest pure helpers (L1-row coercion, board limits, benchmarks)."""
import sys

import pandas as pd
import pytest

from autoresearch.analyze import harvest
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
