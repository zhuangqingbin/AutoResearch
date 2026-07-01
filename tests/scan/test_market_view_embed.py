import pandas as pd

from autoresearch.scan.assemble import build_summary


def _min_scan(tmp_path, with_view):
    d = tmp_path / "s"
    d.mkdir()
    (d / "meta.json").write_text("{}", encoding="utf-8")
    (d / "finalists.csv").write_text("code,name,sector\n", encoding="utf-8")
    if with_view:
        (d / "market_view.md").write_text("## 定调\n避险哑铃,半导体拥挤。\n", encoding="utf-8")
    return d


def test_embed_when_view_present(tmp_path):
    md = build_summary(_min_scan(tmp_path, True), "2026-06-30", "2314", "20260630_2314")
    assert "## 📈 今日 A 股市场(首席策略师视角)" in md
    assert "避险哑铃" in md


def test_fallback_when_view_absent_and_market_data(tmp_path):
    d = _min_scan(tmp_path, False)
    pd.DataFrame([{"code": "1", "above_ma60": 0.0, "pct_60d": -25.0}] * 5).to_csv(
        d / "L1_scored_full.csv", index=False)
    md = build_summary(d, "2026-06-30", "2314", "20260630_2314")
    assert "市场脉搏(确定性回退)" in md


def test_no_market_section_when_no_data(tmp_path):
    md = build_summary(_min_scan(tmp_path, False), "2026-06-30", "2314", "20260630_2314")
    assert "今日 A 股市场" not in md          # 老路:无市场数据不加节
    assert "## 1. 漏斗(数量)" in md          # 其余照旧
