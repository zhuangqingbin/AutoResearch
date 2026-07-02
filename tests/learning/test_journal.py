"""扫描日记:每日一行(regime/菜单/漏斗/买/触发/fwd 回填)。合成,无网络。

spec: docs/specs/2026-07-02-scan-observability-design.md §2
"""
from __future__ import annotations

import json

import pandas as pd

from autoresearch.learning.journal import render, roll


def _mk_day(root, date, regime="range", with_retro=False, trigger=False):
    d = root / date
    (d / "details").mkdir(parents=True)
    (d / "meta.json").write_text(json.dumps({"regime": regime}), encoding="utf-8")
    pd.DataFrame([{"code": "000001", "industry": "半导体", "pct_60d": -30.0,
                   "main_net_ratio": 0.01, "cmf_20": 0.02},
                  {"code": "000002", "industry": "电力", "pct_60d": 10.0,
                   "main_net_ratio": 0.02, "cmf_20": 0.05}]).to_csv(
        d / "L2_gbdt_top200.csv", index=False)
    pd.DataFrame([{"code": "000001", "name": "甲", "sector": "半导体"}]).to_csv(
        d / "finalists.csv", index=False)
    (d / "details" / "000001.md").write_text("**Rating**: Overweight\n", encoding="utf-8")
    if trigger:
        pd.DataFrame([{"code": "000009", "name": "乙", "status": "触发",
                       "detail": "", "narrative": "", "born": date, "expiry": ""}]).to_csv(
            d / "watchlist_status.csv", index=False)
    if with_retro:
        (d / "retro").mkdir()
        pd.DataFrame([{"code": f"{i:06d}", "fwd_1_oo": 0.01, "fwd_5_oc": -0.02}
                      for i in range(5)]).to_csv(d / "retro" / "attribution.csv", index=False)
        (d / "retro" / "done.json").write_text("{}", encoding="utf-8")


def test_journal_two_days(tmp_path):
    _mk_day(tmp_path, "2026-07-01", regime="risk_off", with_retro=True)
    _mk_day(tmp_path, "2026-07-02", trigger=True)
    df = roll(tmp_path)
    assert list(df["date"]) == ["2026-07-01", "2026-07-02"]
    r1, r2 = df.iloc[0], df.iloc[1]
    assert r1["regime"] == "risk_off" and r1["retro_done"] and r1["mkt_fwd1"] == 0.01
    assert r1["knife"] == 0.5 and r1["healthy"] == 1                    # 2 只 L2:1 落刀 1 健康
    assert r2["buys"] == 1 and r2["triggers"] == 1 and not r2["retro_done"]
    md = "\n".join(render(df))
    assert "2026-07-01" in md and "risk_off" in md and "汇总" in md
    assert "50%" in md and "200%" not in md      # 落刀=占比;计数列取整,别把 2 渲成 200%


def test_journal_empty(tmp_path):
    df = roll(tmp_path)
    assert not len(df)
    assert "_无 scan 日_" in "\n".join(render(df))
