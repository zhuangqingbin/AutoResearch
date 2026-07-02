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


def test_brief_appends_dossier_when_history(tmp_path):
    """R5:有历史入围 → brief 含 📁 档案块;无历史 → 不含(老 brief 不破)。"""
    import pandas as pd

    from autoresearch.scan.agents.l4_card import compose_funnel_brief
    today = tmp_path / "2026-07-01"
    today.mkdir()
    pd.DataFrame([{"code": "002049", "name": "紫光国微"}]).to_csv(today / "finalists.csv", index=False)
    hist = tmp_path / "2026-06-30"
    (hist / "details").mkdir(parents=True)
    pd.DataFrame([{"code": "002049", "name": "紫光国微", "risk": "CFO为负"}]).to_csv(
        hist / "finalists.csv", index=False)
    (hist / "details" / "002049.md").write_text("**Rating**: Hold\n**一行多空**:多:x ｜ 空:CFO负\n",
                                                encoding="utf-8")
    brief = compose_funnel_brief("002049", today)
    assert "📁 个股档案" in brief and "2026-06-30" in brief
    brief2 = compose_funnel_brief("600000", today)
    assert "📁" not in brief2
