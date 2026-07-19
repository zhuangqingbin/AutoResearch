"""L3 增量表(Δ模式)+ 稳定性抽检:默认 parity / 过滤+标记 / 回退 / 乱序。合成,无网络。

spec: docs/specs/2026-07-02-scan-l4-economy-design.md §3-4
"""
from __future__ import annotations

import pandas as pd

from autoresearch.scan.agents.l3_select import l3_table_md


def _l2(root, date, rows):
    d = root / date
    d.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(d / "L2_gbdt_top200.csv", index=False)
    return d


def _row(code, comp, pct):
    return {"code": code, "name": f"N{code}", "industry": "半导体",
            "composite": comp, "pct_60d": pct}


def _mk_prev(root):
    d = _l2(root, "2026-07-01",
            [_row("000001", 50, 10), _row("000002", 51, 11),
             _row("000003", 52, 12), _row("000004", 53, 13)])
    pd.DataFrame({"code": ["000001", "000002", "000003", "000004"]}).to_csv(
        d / "L3_judged_full.csv", index=False)
    pd.DataFrame({"code": ["000001"]}).to_csv(d / "finalists.csv", index=False)  # 选1弃3


def test_delta_filters_and_marks(tmp_path):
    _mk_prev(tmp_path)
    _l2(tmp_path, "2026-07-02",
        [_row("000001", 50, 10),          # 昨选 → 保留,prev_l3=选
         _row("000002", 51, 11),          # 昨弃 + 无变化 → 略去
         _row("000003", 60, 12),          # 昨弃 + composite 变 → 保留,prev_l3=弃
         _row("000005", 40, 5)])          # 新进 → 保留
    s = l3_table_md("2026-07-02", root=tmp_path, delta=True)
    assert "略去 **1** 只" in s and "严禁沿用昨日结论" in s
    assert "000002" not in s
    assert "000001" in s and "000003" in s and "000005" in s
    assert "prev_l3" in s and "选" in s and "弃" in s


def test_default_is_parity_and_fallback(tmp_path):
    _mk_prev(tmp_path)
    _l2(tmp_path, "2026-07-02", [_row("000001", 50, 10), _row("000002", 51, 11)])
    plain = l3_table_md("2026-07-02", root=tmp_path)
    assert "略去" not in plain and "prev_l3" not in plain and "000002" in plain
    fresh = tmp_path / "fresh"
    _l2(fresh, "2026-07-02", [_row("000001", 50, 10)])
    s = l3_table_md("2026-07-02", root=fresh, delta=True)
    assert "回退全量表" in s and "000001" in s               # 无前日 L3 现场


def test_shuffle_deterministic(tmp_path):
    _l2(tmp_path, "2026-07-02", [_row(f"{i:06d}", 50 + i, i) for i in range(1, 8)])
    a = l3_table_md("2026-07-02", root=tmp_path, shuffle_seed=42)
    b = l3_table_md("2026-07-02", root=tmp_path, shuffle_seed=42)
    plain = l3_table_md("2026-07-02", root=tmp_path)
    assert a == b and a != plain
    assert sorted(a.splitlines()) == sorted(plain.splitlines())   # 同内容,仅行序不同
