"""L3 表机构面(rc,卖方一致预期修正)列(presence-gated,默认关=parity)。合成,无网络。

镜像 cat_flag 接线:`consensus.csv`(l4_card.fetch_consensus 产出)在才注,
值 = `f"{eps_delta_pct:+.0f}%"`(无对应行 → 空串)。
"""
from __future__ import annotations

import pandas as pd

from autoresearch.scan.agents.l3_select import l3_table_md

_DATE = "2026-06-30"


def _mk(root, rows):
    d = root / _DATE
    d.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(d / "L2_gbdt_top200.csv", index=False)
    return d


def _row(code, name="甲"):
    return {"code": code, "name": name, "industry": "电子", "composite": 80.0,
            "main_net_ratio": 0.05, "pct_60d": 10.0, "pe": 30.0}


def test_l3_table_rc_flag_on_with_consensus(tmp_path):
    d = _mk(tmp_path, [_row("000001"), _row("000002", name="乙")])
    pd.DataFrame([{"code": "000001", "n_reports": 5, "eps_delta_pct": 5.2}]).to_csv(
        d / "consensus.csv", index=False)
    md = l3_table_md(_DATE, root=tmp_path, rc_flag=True)
    assert "rc" in md and "+5%" in md
    assert "机构面列" in md                                     # 图例行


def test_l3_table_rc_flag_no_consensus_file_column_absent(tmp_path):
    _mk(tmp_path, [_row("000001")])
    md = l3_table_md(_DATE, root=tmp_path, rc_flag=True)
    assert "机构面列" not in md and "| rc |" not in md            # 无文件 → 列不出现


def test_l3_table_rc_flag_default_off_parity(tmp_path):
    d = _mk(tmp_path, [_row("000001")])
    pd.DataFrame([{"code": "000001", "n_reports": 5, "eps_delta_pct": 5.2}]).to_csv(
        d / "consensus.csv", index=False)
    md = l3_table_md(_DATE, root=tmp_path)                       # rc_flag 默认 False
    assert "机构面列" not in md and "| rc |" not in md
