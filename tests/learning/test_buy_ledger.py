"""买单 ledger:抽账/目标命中/基率/空表。合成,无网络。

spec: docs/specs/2026-07-02-scan-portfolio-memory-design.md §2
"""
from __future__ import annotations

import pandas as pd

from autoresearch.learning.buy_ledger import rating_base_rates, render, roll

CARD = ("# 决策卡\n\n| 评级 | 目标(EV) | R:R |\n|---|---|---|\n"
        "| Overweight | 120(EV) | 2:1 |\n\n**Rating**: Overweight\n")


def _mk_day(root, date, with_attr=True):
    d = root / date
    (d / "details").mkdir(parents=True)
    pd.DataFrame([{"code": "000001", "name": "甲", "sector": "半导体"}]).to_csv(
        d / "finalists.csv", index=False)
    (d / "details" / "000001.md").write_text(CARD, encoding="utf-8")
    pd.DataFrame([{"code": "000001", "close": 100.0}]).to_csv(
        d / "L1_scored_full.csv", index=False)
    if with_attr:
        (d / "retro").mkdir()
        pd.DataFrame([{"code": "000001", "fwd_1_oo": 0.01, "fwd_5_oc": 0.08,
                       "fwd_10_oc": 0.25, "gap_d1": 0.02}]).to_csv(
            d / "retro" / "attribution.csv", index=False)
    return d


def test_roll_and_target_hit(tmp_path):
    _mk_day(tmp_path, "2026-07-01")
    df = roll(tmp_path)
    assert len(df) == 1
    r = df.iloc[0]
    assert r["rating"] == "Overweight" and r["fwd_5"] == 0.08 and r["fwd_10"] == 0.25
    assert r["gap_open"] == 0.02
    assert abs(r["target_ret"] - 0.20) < 1e-9                # 120/100−1
    assert bool(r["target_hit"])                              # fwd_10 0.25 ≥ 0.20
    md = "\n".join(render(df))
    assert "✅" in md and "Overweight" in md and "⚠样本少" in md


def test_unrealized_fwd_degrades(tmp_path):
    _mk_day(tmp_path, "2026-07-01", with_attr=False)
    df = roll(tmp_path)
    r = df.iloc[0]
    assert r["fwd_5"] is None or pd.isna(r["fwd_5"])
    assert r["target_hit"] is None or pd.isna(r["target_hit"])


def test_base_rates_and_empty(tmp_path):
    assert rating_base_rates(pd.DataFrame()) == []
    assert "尚无 ≥OW 买单" in "\n".join(render(roll(tmp_path)))
    _mk_day(tmp_path, "2026-07-01")
    br = rating_base_rates(roll(tmp_path), min_n=10)
    assert br[0]["rating"] == "Overweight" and br[0]["n"] == 1 and br[0]["thin"]
    assert br[0]["win5"] == 1.0
