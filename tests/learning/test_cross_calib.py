"""跨层校准环:L3→L4 翻案率(per lane)+ rubric 门柱级拦对/错杀。合成,无网络。

spec: docs/specs/2026-07-05-scan-metering-calibration-wave-design.md §7
"""
from __future__ import annotations

import pandas as pd

CARD = ("# 决策卡\n\n| 评级 | 目标(EV) | R:R |\n|---|---|---|\n"
        "| {rating} | 120(EV) | 2:1 |\n\n"
        "OW三门:主力真在{g1} · 业绩真兑现{g2} · 估值不透支{g3} → {note}\n\n"
        "**Rating**: {rating}\n")


def _mk_day(root, date, cards, judged=None, attr_extra=()):
    """cards = [(code, rating, (g1,g2,g3), fwd_5, hi_10)];judged = L3_judged_full 行 dicts。"""
    d = root / date
    (d / "details").mkdir(parents=True)
    (d / "retro").mkdir()
    pd.DataFrame([{"code": c, "name": f"n{c}", "sector": "半导体"} for c, *_ in cards]
                 ).to_csv(d / "finalists.csv", index=False)
    pd.DataFrame([{"code": c, "close": 100.0} for c, *_ in cards]
                 ).to_csv(d / "L1_scored_full.csv", index=False)
    attr_rows = []
    for code, rating, (g1, g2, g3), fwd5, hi in cards:
        (d / "details" / f"{code}.md").write_text(
            CARD.format(rating=rating, g1=g1, g2=g2, g3=g3, note="压 Hold"),
            encoding="utf-8")
        attr_rows.append({"code": code, "fwd_1_oo": 0.01, "fwd_5_oc": fwd5,
                          "fwd_10_oc": fwd5, "hi_10_oc": hi, "gap_d1": 0.02})
    for i, fwd5 in enumerate(attr_extra):                     # 市场对照行(非 finalist)
        attr_rows.append({"code": f"9{i:05d}", "fwd_1_oo": 0.0, "fwd_5_oc": fwd5,
                          "fwd_10_oc": fwd5, "hi_10_oc": 0.0, "gap_d1": 0.0})
    pd.DataFrame(attr_rows).to_csv(d / "retro" / "attribution.csv", index=False)
    if judged is not None:
        pd.DataFrame(judged).to_csv(d / "L3_judged_full.csv", index=False)
    return d


def test_gate_status_shared_parser():
    """门柱解析共享函数(assemble 与 cross_calib 双消费同源,防口径漂移)。"""
    from autoresearch.scan.assemble import gate_status
    st = gate_status(CARD.format(rating="Hold", g1="✓", g2="✗", g3="✓", note="压 Hold"))
    assert st == {"主力真在": False, "业绩真兑现": True, "估值不透支": False}
    assert gate_status("# 卡\n无门柱段\n") is None
    tol = gate_status("OW三门:主力真在门✗ · 业绩真兑现✓ · 估值不透支✓ → x")
    assert tol["主力真在"]                                    # 「门✗」措辞容错


def test_flip_stats_per_lane(tmp_path):
    """高确信翻案率:conviction≥70 且 L4 ≤UW = 翻案;低确信/无卡行不入分母。"""
    from autoresearch.learning.cross_calib import flip_stats
    _mk_day(tmp_path, "2026-07-01",
            cards=[("000001", "Underweight", ("✓", "✗", "✓"), 0.0, 0.0),
                   ("000002", "Hold", ("✓", "✓", "✓"), 0.0, 0.0)],
            judged=[{"code": "000001", "lane": "吸筹", "conviction": 80, "triage_lean": "看多"},
                    {"code": "000002", "lane": "吸筹", "conviction": 75, "triage_lean": "看多"},
                    {"code": "000003", "lane": "吸筹", "conviction": 90, "triage_lean": "看多"},  # 无卡
                    {"code": "000002", "lane": "趋势", "conviction": 50, "triage_lean": "回避"}])
    df = flip_stats(tmp_path, min_n=1)
    row = df[df["lane"] == "吸筹"].iloc[0]
    assert row["n_hiconv"] == 2                # 000003 无卡不入分母
    assert abs(row["flip_rate"] - 0.5) < 1e-9  # 000001 UW 翻案 / 000002 Hold 未翻
    assert not row["thin"]
    trend = df[df["lane"] == "趋势"].iloc[0]
    assert trend["n_hiconv"] == 0 and (trend["flip_rate"] is None or pd.isna(trend["flip_rate"]))


def test_gate_stats_binding_misskill(tmp_path):
    """binding=唯一✗门;错杀 = ex5>0 且 hi_10 触达卡内目标;拦对 = ex5<0。"""
    from autoresearch.learning.cross_calib import gate_stats
    # 目标幅 0.20(close基)→ o1 基 ≈0.1765;市场均值由 attr 全表 fwd_5 算
    _mk_day(tmp_path, "2026-07-01",
            cards=[("000001", "Hold", ("✓", "✗", "✓"), 0.20, 0.25),    # 兑现门拦;跑赢+触达 → 错杀
                   ("000002", "Hold", ("✓", "✓", "✗"), -0.10, 0.02),   # 估值门拦;跑输 → 拦对
                   ("000003", "Hold", ("✗", "✗", "✓"), 0.0, 0.0),      # 双✗ → 多门
                   ("000004", "Overweight", ("✓", "✓", "✓"), 0.0, 0.0)],  # 无✗ → 不入
            attr_extra=(0.0, 0.0))
    df = gate_stats(tmp_path, min_n=1).set_index("gate")
    assert set(df.index) == {"业绩真兑现", "估值不透支", "多门"}
    a = df.loc["业绩真兑现"]
    assert a["n_blocked"] == 1 and a["mean_ex5"] > 0
    assert abs(a["misskill_rate"] - 1.0) < 1e-9 and abs(a["block_ok_rate"] - 0.0) < 1e-9
    b = df.loc["估值不透支"]
    assert abs(b["block_ok_rate"] - 1.0) < 1e-9 and abs(b["misskill_rate"] - 0.0) < 1e-9


def test_render_and_suggestion_lines(tmp_path):
    from autoresearch.learning.cross_calib import (flip_stats, gate_stats, render,
                                                   suggestion_lines)
    md = "\n".join(render(flip_stats(tmp_path / "nx"), gate_stats(tmp_path / "nx")))
    assert "翻案" in md and "门柱" in md                       # 空表也有骨架
    assert suggestion_lines(flip_stats(tmp_path / "nx"), gate_stats(tmp_path / "nx")) == []
    _mk_day(tmp_path, "2026-07-01",
            cards=[("000001", "Underweight", ("✓", "✗", "✓"), 0.20, 0.25)],
            judged=[{"code": "000001", "lane": "吸筹", "conviction": 80, "triage_lean": "看多"}],
            attr_extra=(0.0,))
    flips, gates = flip_stats(tmp_path, min_n=1), gate_stats(tmp_path, min_n=1)
    lines = suggestion_lines(flips, gates, min_n=1)
    assert any("吸筹" in ln and "翻案" in ln for ln in lines)
    assert any("业绩真兑现" in ln for ln in lines)
    thin = suggestion_lines(flip_stats(tmp_path), gate_stats(tmp_path), min_n=10)
    assert all("禁注" in ln for ln in thin) and thin           # thin → 禁注文案
    md = "\n".join(render(flips, gates))
    assert "吸筹" in md and "业绩真兑现" in md
