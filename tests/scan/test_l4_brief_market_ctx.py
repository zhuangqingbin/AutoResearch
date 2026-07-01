import pandas as pd
from autoresearch.scan.agents.l4_card import compose_funnel_brief


def _scan(tmp_path, with_market):
    d = tmp_path / "s"
    d.mkdir()
    pd.DataFrame([{"code": "000001", "name": "测试股", "industry": "半导体", "composite": 50,
                   "n_channels": 3, "recall_channels": "momentum", "pct_60d": 100.0, "pe": 120.0}]
                 ).to_csv(d / "L1_recall_top1000.csv", index=False)
    if with_market:
        pd.DataFrame([{"code": "000001", "above_ma60": 1.0, "pct_60d": 100.0, "pe": 120.0}] * 5
                     ).to_csv(d / "L1_scored_full.csv", index=False)
        pd.DataFrame([{"industry": "半导体", "n_recall": 49, "median_composite": 26.6,
                       "median_pct_60d": 114.1, "median_main_net_ratio": -0.002, "is_top": True}]
                     ).to_csv(d / "sectors.csv", index=False)
    return d


def test_brief_has_market_terrain_when_data(tmp_path):
    b = compose_funnel_brief("000001", _scan(tmp_path, True))
    assert "市场地形" in b and "本股所在板块" in b


def test_brief_unchanged_without_market_data(tmp_path):
    b = compose_funnel_brief("000001", _scan(tmp_path, False))   # 只有 L1_recall,无 L1_scored_full
    assert "市场地形" not in b
    assert "漏斗简报" in b                                        # 老 brief 照旧
