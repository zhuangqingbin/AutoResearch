import pandas as pd

from autoresearch.scan.market import market_pack


def _mk(tmp_path, rows, sectors=None):
    d = tmp_path / "s"
    d.mkdir()
    pd.DataFrame(rows).to_csv(d / "L1_scored_full.csv", index=False)
    if sectors is not None:
        pd.DataFrame(sectors).to_csv(d / "sectors.csv", index=False)
    return d


def _rows(n_up, n_down):
    rows = []
    for i in range(n_up):
        rows.append({"code": f"{i:06d}", "above_ma60": 1.0, "ma_bull": 1.0, "pct_60d": 10.0,
                     "pct_ytd": 5.0, "pe": 30.0, "pb": 2.0, "main_net_ratio": 0.02, "cmf_20": 0.1})
    for i in range(n_down):
        rows.append({"code": f"{100 + i:06d}", "above_ma60": 0.0, "ma_bull": 0.0, "pct_60d": -25.0,
                     "pct_ytd": -30.0, "pe": 120.0, "pb": 5.0, "main_net_ratio": -0.03, "cmf_20": -0.2})
    return rows


def test_regime_and_breadth(tmp_path):
    pack = market_pack(_mk(tmp_path, _rows(8, 2)))   # breadth 0.8, med_mom>0 → trend
    assert pack["regime"]["label"] == "trend"
    assert pack["breadth"]["above_ma60"] == 0.8
    assert pack["breadth"]["up_60d"] == 0.8


def test_falling_knife_risk_off(tmp_path):
    pack = market_pack(_mk(tmp_path, _rows(2, 8)))   # breadth 0.2, med_mom<0 → risk_off
    assert pack["regime"]["label"] == "risk_off"
    assert pack["breadth"]["falling_knife"] == 0.8


def test_valuation_pe_positive_only(tmp_path):
    rows = _rows(5, 5)
    rows.append({"code": "999999", "above_ma60": 0.0, "pct_60d": -5.0, "pe": -10.0, "pb": 1.0})
    pack = market_pack(_mk(tmp_path, rows))
    assert pack["valuation"]["med_pe"] > 0           # 负 PE 被剔除


def test_sectors_red_black_ordering(tmp_path):
    secs = [
        {"industry": "半导体", "n_recall": 49, "median_composite": 26.6, "median_pct_60d": 114.1,
         "median_main_net_ratio": -0.002, "is_top": True},
        {"industry": "软件开发", "n_recall": 35, "median_composite": 61.0, "median_pct_60d": -24.6,
         "median_main_net_ratio": -0.02, "is_top": True},
        {"industry": "汽车零部件", "n_recall": 52, "median_composite": 62.8, "median_pct_60d": -25.3,
         "median_main_net_ratio": -0.018, "is_top": True},
    ]
    pack = market_pack(_mk(tmp_path, _rows(5, 5), sectors=secs))
    assert pack["sectors"]["red"][0]["industry"] == "半导体"        # 最高 median_pct_60d
    assert pack["sectors"]["black"][0]["industry"] == "汽车零部件"   # 最低 median_pct_60d


def test_missing_columns_degrade(tmp_path):
    pack = market_pack(_mk(tmp_path, [{"code": "000001", "pct_60d": 3.0}]))
    assert pack["regime"] is not None                # pct_60d>0 代理 breadth
    assert pack["valuation"]["med_pe"] is None
    assert pack["sectors"] is None


def test_no_l1_returns_empty(tmp_path):
    d = tmp_path / "empty"
    d.mkdir()
    pack = market_pack(d)
    assert pack["regime"] is None


# ── Wave6 Q3:当日切面块(pr_20260721_002;零新端点)───────────────────────────


def _slice_frame():
    """含 pct_1d 的最小帧:2 只涨停(≥9.5%)、1 只跌停、行业中位可分。"""
    return pd.DataFrame({
        "code": ["000001", "000002", "000003", "000004"],
        "pct_1d": [10.0, 9.6, -10.0, 1.0],
        "industry": ["半导体", "半导体", "煤炭开采", "煤炭开采"],
        "close": [10.0, 20.0, 30.0, 40.0],
        "mktcap_yi": [100.0, 200.0, 300.0, 400.0],
        "pct_60d": [5.0, 6.0, -7.0, 8.0],
        "main_net_ratio": [0.1, 0.2, -0.1, 0.0],
    })


def test_today_slice_counts_and_sector_medians():
    """涨跌停家数 + 全市场当日中位 + 板块当日中位 top3/bottom3(全部来自 pct_1d,零新端点)。"""
    from autoresearch.scan.market import _today_slice

    blk = _today_slice(_slice_frame())

    assert blk["n_up_limit"] == 2 and blk["n_down_limit"] == 1
    assert blk["median_pct_1d"] == 5.3          # (9.6 + 1.0)/2
    assert blk["sector_top3"][0]["industry"] == "半导体"
    assert blk["sector_bottom3"][-1]["industry"] == "煤炭开采"


def test_today_slice_none_without_pct_1d():
    """无 pct_1d 列 → None(presence-gated,不编 0 —— 「没有数据」≠「今天 0 只涨停」)。"""
    from autoresearch.scan.market import _today_slice

    assert _today_slice(pd.DataFrame({"code": ["000001"], "close": [10.0]})) is None
    assert _today_slice(pd.DataFrame()) is None


def test_today_slice_present_in_both_pack_entries(tmp_path):
    """🚨两个入口都必须有(FN-1 半接线防线)。

    帧入口(Stage 0 / market_view)能直接拿 pct_1d;staging 入口(**L4 的 market_context_block
    消费侧**)读 L1_scored_full.csv —— 而 universe 的投影列表此前不含 pct_1d。只改 market.py
    不补 universe.keep = 生产者接了、消费者永远拿不到,正是本仓反复烧的 FN-1 家族
    (test_market_pack_macro_cn 就专门守这一类)。
    """
    from autoresearch.scan.market import market_pack, market_pack_from_frame

    df = _slice_frame()
    pack_frame = market_pack_from_frame(df)

    scan = tmp_path / "context" / "scan" / "2026-07-24"
    scan.mkdir(parents=True)
    df.to_csv(scan / "L1_scored_full.csv", index=False)
    pack_staging = market_pack(scan)

    assert pack_frame["today_slice"]["n_up_limit"] == 2
    assert pack_staging["today_slice"]["n_up_limit"] == 2, "staging 入口缺 today_slice = 半接线"


def test_universe_projection_keeps_pct_1d():
    """L1_scored_full.csv 的投影列表必须含 pct_1d,否则 staging 入口在**真实跑动**里恒空。

    上面那个 parity 测试用自造 CSV,不能证明真实文件有这列 —— 真实文件由 universe 的
    `keep` 投影产出。

    ⚠️ 锚必须钉在**投影那一行**:`universe.py` 里别处(selftest 合成帧)本来就有 "pct_1d",
    所以「全文 grep pct_1d」是假绿灯 —— 第一版这么写,投影没改也照样通过。
    """
    from pathlib import Path

    from autoresearch.scan import universe

    src = Path(universe.__file__).read_text(encoding="utf-8")
    proj_lines = [ln for ln in src.splitlines() if '"pct_60d"' in ln and '"pct_ytd"' in ln]
    assert proj_lines, "投影行锚点漂移(pct_60d/pct_ytd 不再同行),先更新本测试"
    assert any('"pct_1d"' in ln for ln in proj_lines), \
        "universe 投影行缺 pct_1d → 当日切面在 L4 消费侧恒空(FN-1 半接线)"
