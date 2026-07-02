"""卖方一致预期积累管道:status 统计 / consensus_delta 数学。合成 pkl,无网络。

背景:tushare report_rc 限频 1次/小时 → 历史回补不可行,改前向积累(每日 1 拉);
积累 ≥60 日后 factor_lab 验 IC,过门才谈入 composite(附录 B 纪律)。
"""
from __future__ import annotations

import pandas as pd

from autoresearch.research.consensus import consensus_delta, status


def _cache(root, d, rows):
    (root / "report_rc").mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_pickle(root / "report_rc" / f"{d}.pkl")


def _rep(code, eps, quarter="2026Q4", d="20260601"):
    return {"ts_code": f"{code}.SZ", "report_date": d, "quarter": quarter, "eps": eps, "np": 1.0}


def test_status_counts(tmp_path):
    _cache(tmp_path, "20260601", [_rep("000001", 1.0)])
    _cache(tmp_path, "20260602", [_rep("000001", 1.1), _rep("000002", 2.0)])
    s = status(cache_root=tmp_path)
    assert s["n_days"] == 2 and s["first"] == "20260601" and s["last"] == "20260602"
    assert s["n_stocks"] == 2


def test_consensus_delta_math(tmp_path):
    # 旧窗(0601-0610):000001 FY2026 EPS 中位 1.0;新窗(0611-0620):升到 1.2 → delta +20%
    for d in ("20260601", "20260605"):
        _cache(tmp_path, d, [_rep("000001", 1.0, d=d)])
    for d in ("20260612", "20260618"):
        _cache(tmp_path, d, [_rep("000001", 1.2, d=d)])
    out = consensus_delta("20260620", old_span=("20260601", "20260610"),
                          new_span=("20260611", "20260620"), fy="2026", cache_root=tmp_path)
    row = out.set_index("code").loc["000001"]
    assert abs(row["eps_new"] - 1.2) < 1e-9 and abs(row["eps_delta_pct"] - 20.0) < 1e-6


def test_consensus_delta_empty_when_no_cache(tmp_path):
    out = consensus_delta("20260620", old_span=("20260601", "20260610"),
                          new_span=("20260611", "20260620"), fy="2026", cache_root=tmp_path)
    assert len(out) == 0 and "eps_delta_pct" in out.columns
