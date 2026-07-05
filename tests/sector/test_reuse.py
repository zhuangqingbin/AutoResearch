"""Phase 3 —— 行业 brief TTL 复用(确定性判据:TTL/regime/动量位移)。NO network。"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from autoresearch.sector.reuse import apply_reuse, find_reusable

TODAY = "2026-07-03"
PREV = "2026-07-01"

BRIEF = """# 行业 brief — 半导体 @ 2026-07-01

## 地形段(喂 L3/L4 · 描述性)
- 景气读数:中位60日 5.0%

## 研判段(仅 L5)
**行业方向**: 中性 — 磨底
"""


def _mk_day(root: Path, date: str, mom: float, regime: str | None = "range",
            with_brief: bool = False) -> Path:
    d = root / date
    d.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([{"code": "600001", "industry": "半导体", "pct_60d": mom},
                  {"code": "600002", "industry": "半导体", "pct_60d": mom}]
                 ).to_csv(d / "L1_scored_full.csv", index=False)
    if regime is not None:
        (d / "meta.json").write_text(json.dumps({"regime": regime}), encoding="utf-8")
    if with_brief:
        bd = d / "sector_briefs"
        bd.mkdir(exist_ok=True)
        (bd / "半导体.md").write_text(BRIEF, encoding="utf-8")
    return d


def test_reuse_hit_and_apply(tmp_path):
    _mk_day(tmp_path, PREV, mom=5.0, with_brief=True)
    _mk_day(tmp_path, TODAY, mom=6.0)                       # 位移 1pp ≤ 3、regime 同
    found = find_reusable(TODAY, ["半导体"], root=tmp_path)
    assert found["半导体"]["prev"] == PREV
    assert apply_reuse(TODAY, found, root=tmp_path) == 1
    dst = tmp_path / TODAY / "sector_briefs" / "半导体.md"
    body = dst.read_text(encoding="utf-8")
    assert body.startswith("> ♻️ 复用自 2026-07-01")
    assert "失效条件" in body and "**行业方向**" in body     # 原 brief 契约随行


def test_reuse_miss_on_regime_flip(tmp_path):
    _mk_day(tmp_path, PREV, mom=5.0, with_brief=True)
    _mk_day(tmp_path, TODAY, mom=5.5, regime="risk_off")    # regime 翻转 → 保守不复用
    assert find_reusable(TODAY, ["半导体"], root=tmp_path) == {}


def test_reuse_miss_on_mom_shift(tmp_path):
    _mk_day(tmp_path, PREV, mom=5.0, with_brief=True)
    _mk_day(tmp_path, TODAY, mom=15.0)                      # 位移 10pp > 3
    assert find_reusable(TODAY, ["半导体"], root=tmp_path) == {}


def test_reuse_miss_on_ttl_and_missing_regime(tmp_path):
    _mk_day(tmp_path, "2026-06-20", mom=5.0, with_brief=True)   # age 13d > ttl 5
    _mk_day(tmp_path, TODAY, mom=5.0)
    assert find_reusable(TODAY, ["半导体"], root=tmp_path) == {}
    _mk_day(tmp_path, PREV, mom=5.0, regime=None, with_brief=True)  # 前日无 meta → 不复用
    assert find_reusable(TODAY, ["半导体"], root=tmp_path) == {}
