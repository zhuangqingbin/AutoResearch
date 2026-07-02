"""影子漏斗:变体 L2 落盘(经 universe run 的 shadow 块逻辑)+ retro 对照捕获数。合成,无网络。

spec: docs/specs/2026-07-02-scan-calendar-shadow-design.md §2
universe.run 需网络,shadow 块逻辑经 parity fixture 间接覆盖;此处直接测 select_l2 变体语义
与 retro.shadow_compare 的读盘/求交。
"""
from __future__ import annotations

import pandas as pd

from autoresearch.learning.retro import shadow_compare


def _recall(n=40):
    rows = []
    for i in range(n):
        rows.append({"code": f"{i:06d}", "name": f"N{i}", "industry": "半导体" if i % 2 else "电力",
                     "composite": 100 - i, "pct_60d": i, "main_net_ratio": 0.01,
                     "cmf_20": 0.01, "amount_yi": 5.0})
    return pd.DataFrame(rows)


def test_variants_semantics(tmp_path):
    """nostrat = 纯 composite 序;nocap = 分层但无行业上限(与 universe shadow 块同构)。"""
    from autoresearch.scan.recall.l2_stratify import select_l2
    rc = _recall()
    nostrat = rc.sort_values("composite", ascending=False).head(10)
    assert list(nostrat["code"])[:3] == ["000000", "000001", "000002"]
    nocap, eng = select_l2(rc, 10, sector_cap_frac=1.0)
    capped, _ = select_l2(rc, 10, sector_cap_frac=0.20)
    assert len(nocap) == 10 and len(capped) == 10
    top_share = nocap["industry"].value_counts(normalize=True).iloc[0]
    cap_share = capped["industry"].value_counts(normalize=True).iloc[0]
    assert cap_share <= 0.5 and top_share >= cap_share      # cap 关后行业可更集中


def test_shadow_compare_capture(tmp_path):
    d = tmp_path / "2026-07-02"
    (d / "shadow").mkdir(parents=True)
    pd.DataFrame({"code": ["000001", "000002"]}).to_csv(d / "L2_gbdt_top200.csv", index=False)
    pd.DataFrame({"code": ["000001", "000003"]}).to_csv(d / "shadow" / "L2_nostrat.csv", index=False)
    attr = pd.DataFrame([
        {"code": "000001", "winner": True, "winner_5": False},
        {"code": "000002", "winner": False, "winner_5": True},
        {"code": "000003", "winner": True, "winner_5": True},
    ])
    rows = shadow_compare(attr, d)
    assert len(rows) == 1 and rows[0]["variant"] == "nostrat"
    r = rows[0]
    assert r["cap1"] == 2 and r["cap1_main"] == 1           # 影子抓到 1+3,主只抓 1
    assert r["cap5"] == 1 and r["cap5_main"] == 1
    assert shadow_compare(attr, tmp_path / "nope") == []    # 无影子 → []
