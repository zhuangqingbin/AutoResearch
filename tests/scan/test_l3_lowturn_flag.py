"""L3 表低位转强旗列(lowturn_flag,默认关=parity)+ prepare 接线跟随 l3.lowturn.enabled。合成,无网络。"""
from __future__ import annotations

import json

import pandas as pd

from autoresearch.scan.agents.l3_select import l3_table_md, prepare_l3_table

_DATE = "2026-08-20"


def _row(code, *, turn: bool, name="甲"):
    base = {"code": code, "name": name, "industry": "电子", "composite": 80.0, "gbdt_score": 80.0,
            "n_channels": 1, "recall_channels": "reversal", "pe": 20.0, "main_net_ratio": -0.01,
            "pct_60d": -12.0, "dist_high_60": -22.0, "pct_5d": 4.0, "above_ma20": 1.0,
            "ma5_gt_ma10": 1.0, "vol_ratio_20": 1.6, "main_inflow_yi": 0.8, "cmf_20": 0.05}
    if not turn:
        base.update(above_ma20=0.0)
    return base


def _mk(root, rows):
    d = root / _DATE
    d.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(d / "L2_gbdt_top200.csv", index=False)
    return d


def test_lowturn_flag_adds_column_and_legend(tmp_path):
    _mk(tmp_path, [_row("000001", turn=True), _row("000002", turn=False, name="乙")])
    md = l3_table_md(_DATE, root=tmp_path, lowturn_flag=True)
    assert "lowturn" in md and "转强" in md and "硬约束 B 不适用" in md and "今日旗亮 1 只" in md


def test_lowturn_flag_default_off_is_byte_parity(tmp_path):
    _mk(tmp_path, [_row("000001", turn=True)])
    assert l3_table_md(_DATE, root=tmp_path) == l3_table_md(_DATE, root=tmp_path, lowturn_flag=False)
    md = l3_table_md(_DATE, root=tmp_path)
    assert "lowturn" not in "\n".join(line for line in md.splitlines() if line.startswith("|"))
    assert "lowturn 低位转强(确定性旗)" not in md


def test_lowturn_cfg_threshold_respected(tmp_path):
    _mk(tmp_path, [_row("000001", turn=True)])
    md = l3_table_md(_DATE, root=tmp_path, lowturn_flag=True, lowturn_cfg={"min_vol_ratio_20": 2.0})
    assert "今日旗亮 0 只" in md


def test_prepare_follows_config_enabled(tmp_path, monkeypatch):
    base = tmp_path / "context" / "scan"
    _mk(base, [_row("000001", turn=True), _row("000002", turn=False, name="乙")])
    cfgp = tmp_path / "scan_config.jsonc"
    cfgp.write_text(json.dumps({"l3": {"two_pass": True, "pass1_target": 40,
                                       "lowturn": {"enabled": True}}}), encoding="utf-8")
    monkeypatch.setattr("autoresearch.scan.user_config.DEFAULT_PATH", cfgp)
    res = prepare_l3_table(_DATE, root=base, do_harvest=False)
    md = (base / _DATE / "_l3_table.md").read_text(encoding="utf-8")
    assert "lowturn" in md and res.get("lowturn_n") == 1


def test_prepare_config_disabled_has_no_column(tmp_path, monkeypatch):
    base = tmp_path / "context" / "scan"
    _mk(base, [_row("000001", turn=True)])
    cfgp = tmp_path / "scan_config.jsonc"
    cfgp.write_text(json.dumps({"l3": {"two_pass": True, "pass1_target": 40}}), encoding="utf-8")
    monkeypatch.setattr("autoresearch.scan.user_config.DEFAULT_PATH", cfgp)
    res = prepare_l3_table(_DATE, root=base, do_harvest=False)
    md = (base / _DATE / "_l3_table.md").read_text(encoding="utf-8")
    assert "lowturn" not in "\n".join(line for line in md.splitlines() if line.startswith("|"))
    assert "lowturn 低位转强(确定性旗)" not in md
    assert "lowturn_n" not in res
