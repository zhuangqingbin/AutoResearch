"""E4:前瞻收益纯计算下沉 `common/forward_returns.py` 的搬迁 parity。

两类断言缺一不可(2026-09-06 计划 §E4 Step 5):

1. **同对象** —— 证明旧入口是转发,不是复制粘贴出来的第二份实现;
2. **golden 逐位** —— golden 在**搬迁前**由 `research.factor_lab` 原实现录制
   (`tests/fixtures/forward_returns_golden.json`)。只对拍「新旧同对象」证明的是转发有效,
   证明不了数值没变:两边指向同一个**被改过**的函数照样全绿。

golden 的编码:NaN → `"__NAN__"`,`pd.NA` → `"__NA__"`,其余原样(JSON float 往返精确)。
"""
import json
from pathlib import Path

import pandas as pd
import pytest

from autoresearch.common import forward_returns as shared

GOLDEN = json.loads((Path(__file__).resolve().parents[1] / "fixtures"
                     / "forward_returns_golden.json").read_text(encoding="utf-8"))
FIELDS = ("open", "high", "low", "close", "pct_chg", "amount")


def _piv(rows):
    df = pd.DataFrame(rows, columns=("code", "date") + FIELDS)
    return {f: df.pivot_table(index="code", columns="date", values=f) for f in FIELDS}


def _enc(v):
    if v is pd.NA:
        return "__NA__"
    if isinstance(v, bool):
        return bool(v)
    try:
        if pd.isna(v):
            return "__NAN__"
    except (TypeError, ValueError):
        pass
    return int(v) if isinstance(v, int) else float(v)


def _assert_frame_matches(fr, golden, label):
    assert [str(x) for x in fr.index] == golden["index"], f"{label}: index 变了"
    assert [str(c) for c in fr.columns] == golden["columns"], f"{label}: 列集/列序变了"
    for col in golden["columns"]:
        assert str(fr[col].dtype) == golden["dtypes"][col], f"{label}.{col}: dtype 变了"
        got = [_enc(v) for v in fr[col].tolist()]
        assert got == golden["values"][col], f"{label}.{col}: 数值变了"


# ───────────────────────── ① 同对象:旧入口必须是转发 ─────────────────────────

def test_legacy_forward_returns_is_same_implementation():
    from autoresearch.research import factor_lab
    assert factor_lab.forward_returns is shared.forward_returns
    assert factor_lab._board_limit is shared._board_limit


def test_legacy_edge_census_is_same_implementation():
    from autoresearch.data import market_panel
    from autoresearch.research import edge_census
    assert edge_census.forward_frame is shared.forward_frame
    assert edge_census.lake_trade_days is market_panel.lake_trade_days
    assert edge_census.load_lake_pivots is market_panel.load_lake_pivots


# ───────────────────────── ② golden 逐位 ─────────────────────────

@pytest.mark.parametrize("case", GOLDEN["cases"], ids=[c["name"] for c in GOLDEN["cases"]])
def test_forward_returns_matches_pre_migration_golden(case):
    fr = shared.forward_returns(_piv([tuple(r) for r in case["rows"]]),
                                case["P"], case["D"], case["fwd"])
    _assert_frame_matches(fr, case["forward_returns"], case["name"])


def test_board_limit_matches_pre_migration_golden():
    for code, limit in GOLDEN["board_limit"].items():
        assert shared._board_limit(code) == limit, f"{code} 的板制度变了"


def test_forward_frame_matches_pre_migration_golden():
    spec = GOLDEN["forward_frame"]
    fr = shared.forward_frame(_piv([tuple(r) for r in spec["rows"]]), spec["P"], spec["D"])
    _assert_frame_matches(fr, spec["golden"], "forward_frame")
    # GAP_CLIP 剪裁的计数进 attrs —— 少了它,超板坏数据会静默流进均值
    assert fr.attrs["n_clipped"] == spec["golden"]["attrs"]["n_clipped"]


def test_forward_frame_returns_none_on_empty_or_unknown_day():
    spec = GOLDEN["forward_frame"]
    piv = _piv([tuple(r) for r in spec["rows"]])
    assert shared.forward_frame({}, ["D0"], "D0") is None
    assert shared.forward_frame(piv, spec["P"], "D9") is None
