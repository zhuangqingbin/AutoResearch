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
