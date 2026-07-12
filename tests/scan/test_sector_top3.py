"""P7:确定性看多行业 top3 —— 资格门/倒U动量/防锚定/ledger 分账/brief 并集。"""
import json

import pandas as pd


def _frame():
    # 行业A:资金+健康+温和动量+便宜(应第一);行业B:资金门死(主力中位<0 且 +占比<50%);
    # 行业C:落刀(<-20);行业D:n<8。每行业 10 只(D 只 3 只)。
    def block(ind, n, mnr, p60, cmf, pe):
        return pd.DataFrame({"industry": ind, "code": [f"{hash(ind) % 90 + 10}{i:04d}" for i in range(n)],
                             "main_net_ratio": mnr, "pct_60d": p60, "cmf_20": cmf, "pe": pe,
                             "above_ma60": 1.0, "ma_bull": 0.0})   # classify_regime 消费列,防 pack 级测试炸
    return pd.concat([
        block("行业A", 10, 0.02, 12.0, 0.1, 20.0),
        block("行业B", 10, -0.05, 15.0, 0.1, 25.0),
        block("行业C", 10, 0.03, -30.0, 0.1, 10.0),
        block("行业D", 3, 0.05, 10.0, 0.2, 15.0),
    ], ignore_index=True)


def test_top3_gates_and_order():
    from autoresearch.scan.market import sector_healthy_top3
    rows = sector_healthy_top3(_frame())
    assert [r["industry"] for r in rows] == ["行业A"]      # B 资金门/C 落刀/D n<8 全被拦,宁缺毋滥
    assert rows[0]["n"] == 10 and rows[0]["med_pct_60d"] == 12.0


def test_top3_missing_cols_none():
    from autoresearch.scan.market import sector_healthy_table
    assert sector_healthy_table(pd.DataFrame({"industry": ["x"]})) is None


def test_pack_and_render_and_anti_anchor():
    from autoresearch.scan.market import (
        market_context_block,
        market_pack_from_frame,
        render_sector_top3,
    )
    pack = market_pack_from_frame(_frame(), date=None)
    assert pack["sector_healthy_top3"][0]["industry"] == "行业A"
    md = render_sector_top3(pack)
    assert "🎯 看多行业 top3" in md and "行业A" in md
    assert "看多行业" not in market_context_block(pack)     # 防锚定:L3/L4 地形块无 top3 痕迹
    assert render_sector_top3({}) == ""


def test_ledger_record_top3_idempotent_and_separate(tmp_path):
    from autoresearch.learning.sector_ledger import _load, record_calls, record_top3
    p = tmp_path / "sector_calls.jsonl"
    assert record_top3("2026-07-10", ["行业A", "行业B"], path=p) == 2
    assert record_top3("2026-07-10", ["行业A"], path=p) == 0          # 幂等
    d = tmp_path / "scan" / "sector_briefs"
    d.mkdir(parents=True)
    (d / "行业A.md").write_text("**行业方向**: 看多 — x", encoding="utf-8")
    assert record_calls(tmp_path / "scan", "2026-07-10", path=p) == 1  # brief 与 top3 分账不互斥
    rows = _load(p)
    assert {r["source"] for r in rows} == {"deterministic_top3", "brief"}


def test_briefing_sectors_union_top3(tmp_path):
    from autoresearch.sector.pack import select_briefing_sectors
    scan_dir = tmp_path
    pd.DataFrame({"industry": [f"热{i}" for i in range(8)],
                  "median_pct_60d": range(8, 0, -1), "n_recall": 5}
                 ).to_csv(scan_dir / "sectors.csv", index=False)
    (scan_dir / "market_pack.json").write_text(json.dumps(
        {"sector_healthy_top3": [{"industry": "冷门X"}, {"industry": "热2"}]}), encoding="utf-8")
    inds, prov = select_briefing_sectors(scan_dir, k=3, wl_path=tmp_path / "nope.csv")
    assert inds[:3] == ["热0", "热1", "热2"]           # 红榜降序 top3;基础来源仍 cap=k
    assert "冷门X" in inds and prov["冷门X"] == "top3看多"   # top3 追加不占 cap
    assert len(inds) == 4                              # 热2 已在基础集,去重
