"""L3 表主力失真旗(dist_flag,默认关=parity)+ L4 简报失真标注。合成,无网络。

07-03 病灶:L1/L3 喂原始 main_net_ratio,L3 据此打 conv 60-82,L4 再花 ~15 张卡逐一
辟谣「占比失真(微盘放大/绝对净出)」。修法=失真旗确定性化,判断层不再重复还债。
"""
from __future__ import annotations

import pandas as pd

from autoresearch.scan.agents.l3_select import l3_table_md
from autoresearch.scan.agents.l4_card import compose_funnel_brief

_DATE = "2026-07-03"


def _mk(root, rows):
    d = root / _DATE
    d.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(d / "L2_gbdt_top200.csv", index=False)
    return d


def _row(code, ratio, inflow, name="甲"):
    return {"code": code, "name": name, "industry": "电子", "composite": 80.0,
            "main_net_ratio": ratio, "main_inflow_yi": inflow, "cmf_20": 0.05,
            "pct_60d": 10.0, "pe": 30.0}


def test_l3_table_dist_flag_on(tmp_path):
    _mk(tmp_path, [_row("000001", 0.05, -1.2), _row("000002", 0.087, 0.03, name="乙"),
                   _row("000003", 0.14, 0.91, name="丙")])
    md = l3_table_md(_DATE, root=tmp_path, dist_flag=True)
    assert "main_dist" in md and "反号" in md and "微量" in md
    assert "⚠主力失真" in md            # 图例行:规则说明(不得单独作核心多头论点)
    assert "main_inflow_yi" in md       # 绝对净额列一并入表


def test_l3_table_dist_flag_default_off_parity(tmp_path):
    _mk(tmp_path, [_row("000001", 0.05, -1.2)])
    md = l3_table_md(_DATE, root=tmp_path)
    assert "main_dist" not in md and "⚠主力失真" not in md


def test_funnel_brief_carries_distortion_mark(tmp_path):
    d = _mk(tmp_path, [_row("000001", 0.05, -1.2)])
    pd.DataFrame([_row("000001", 0.05, -1.2)]).to_csv(d / "L1_recall_top1000.csv", index=False)
    brief = compose_funnel_brief("000001", d)
    assert "⚠主力占比失真(反号" in brief

    d2 = _mk(tmp_path / "b", [_row("000001", 0.14, 0.91)])
    pd.DataFrame([_row("000001", 0.14, 0.91)]).to_csv(d2 / "L1_recall_top1000.csv", index=False)
    assert "⚠主力占比失真" not in compose_funnel_brief("000001", d2)
