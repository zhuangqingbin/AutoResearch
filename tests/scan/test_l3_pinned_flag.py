"""L3 表 📌 pinned 列(保送票标记,presence-gated,默认关=parity)。合成,无网络。

design: docs/specs/2026-07-11-recall-gate-pinned-config-design.md §4.1。
plan: docs/plans/2026-07-11-pinned-config-plan.md Task 4。

镜像 rc_flag/cat_flag 接线:pinned.json(user_config.load_pinned)在且有 kept 才注 `pinned`
列(📌 标记该码,其余空);无 pinned.json / kept 全过期 → 列不出现(presence-gated parity)。
"""
from __future__ import annotations

import json

import pandas as pd

from autoresearch.scan.agents.l3_select import l3_table_md

_DATE = "2026-07-11"


def _mk(root, rows):
    d = root / _DATE
    d.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(d / "L2_gbdt_top200.csv", index=False)
    return d


def _row(code, name="甲"):
    return {"code": code, "name": name, "industry": "电子", "composite": 80.0,
            "main_net_ratio": 0.05, "pct_60d": 10.0, "pe": 30.0}


def test_l3_table_pinned_flag_on_with_kept_entry(tmp_path):
    _mk(tmp_path, [_row("000001"), _row("000002", name="乙")])
    pin = tmp_path / "pinned.json"
    pin.write_text(json.dumps([
        {"code": "000001", "note": "长期仓", "expires": "2099-01-01"},
    ]), encoding="utf-8")
    md = l3_table_md(_DATE, root=tmp_path, pinned_flag=True, pinned_path=pin)
    assert "pinned" in md and "📌" in md
    assert "📌(pinned列)" in md                                  # 图例行


def test_l3_table_pinned_flag_marks_only_pinned_code(tmp_path):
    _mk(tmp_path, [_row("000001"), _row("000002", name="乙")])
    pin = tmp_path / "pinned.json"
    pin.write_text(json.dumps([{"code": "000001", "expires": "2099-01-01"}]), encoding="utf-8")
    md = l3_table_md(_DATE, root=tmp_path, pinned_flag=True, pinned_path=pin)
    lines = [ln for ln in md.splitlines() if ln.startswith("|") and ("000001" in ln or "000002" in ln)]
    row1 = next(ln for ln in lines if "000001" in ln)
    row2 = next(ln for ln in lines if "000002" in ln)
    assert "📌" in row1
    assert "📌" not in row2


def test_l3_table_pinned_flag_no_file_column_absent(tmp_path):
    _mk(tmp_path, [_row("000001")])
    md = l3_table_md(_DATE, root=tmp_path, pinned_flag=True)      # 缺 pinned.json(默认生产路径)
    assert "📌(pinned列)" not in md
    assert "| pinned |" not in md


def test_l3_table_pinned_flag_all_expired_column_absent(tmp_path):
    _mk(tmp_path, [_row("000001")])
    pin = tmp_path / "pinned.json"
    pin.write_text(json.dumps([
        {"code": "000001", "added": "2026-06-01", "expires": "2026-07-01"},   # < _DATE,已过期
    ]), encoding="utf-8")
    md = l3_table_md(_DATE, root=tmp_path, pinned_flag=True, pinned_path=pin)
    assert "📌(pinned列)" not in md
    assert "| pinned |" not in md


def test_l3_table_pinned_flag_default_off_is_parity(tmp_path):
    """pinned_flag 默认 False:即便 pinned.json 存在也不加列(与其它 flag 同款 presence-gate 前提——
    flag 参数本身才是总开关,`load_pinned` 是 flag=True 之后的第二层 presence-gate)。"""
    _mk(tmp_path, [_row("000001")])
    pin = tmp_path / "pinned.json"
    pin.write_text(json.dumps([{"code": "000001", "expires": "2099-01-01"}]), encoding="utf-8")
    md_no_pinned_path = l3_table_md(_DATE, root=tmp_path)                     # 两参数都缺省
    md_same_as_others = l3_table_md(_DATE, root=tmp_path, dist_flag=True, reg_flag=True,
                                    cat_flag=True, misread_flag=True, rc_flag=True)
    assert "📌" not in md_no_pinned_path
    assert "📌" not in md_same_as_others
