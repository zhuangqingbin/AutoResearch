"""门的跨日 ledger:roll 聚合 gate_fires×attribution / render / 空目录。合成,无网络。"""
from __future__ import annotations

import pandas as pd

from autoresearch.learning.gate_ledger import render, roll


def _mk_day(root, date, fires, attr_rows):
    d = root / date
    (d / "retro").mkdir(parents=True)
    pd.DataFrame(fires).to_csv(d / "gate_fires.csv", index=False)
    pd.DataFrame(attr_rows).to_csv(d / "retro" / "attribution.csv", index=False)


def test_roll_and_render(tmp_path):
    _mk_day(tmp_path, "2026-07-01",
            [{"date": "2026-07-01", "code": "000001", "check": "经验红线·获利盘满", "severity": "fail", "detail": "d"}],
            [{"code": "000001", "fwd_1_oo": -0.05, "fwd_2_oc": -0.07, "fwd_5_oc": -0.10},
             {"code": "000002", "fwd_1_oo": 0.01, "fwd_2_oc": 0.015, "fwd_5_oc": 0.02}])
    _mk_day(tmp_path, "2026-07-02",
            [{"date": "2026-07-02", "code": "000003", "check": "经验红线·获利盘满", "severity": "fail", "detail": "d"}],
            [{"code": "000003", "fwd_1_oo": 0.06, "fwd_2_oc": 0.07, "fwd_5_oc": 0.08},
             {"code": "000004", "fwd_1_oo": 0.0, "fwd_2_oc": 0.0, "fwd_5_oc": 0.0}])
    df = roll(tmp_path)
    row = df.set_index("check").loc["经验红线·获利盘满"]
    assert row["n_fires"] == 2 and row["n_days"] == 2
    assert "mean_ex2" in df.columns
    md = "\n".join(render(df))
    assert "获利盘满" in md and "拦对率" in md


def test_hit_rate_computed_from_ex2(tmp_path):
    # 单门单次拦截:ex1(按 fwd_1)为正(拦错),ex2(按 fwd_2,主尺)为负(拦对) → hit_rate 应按 ex2 判对。
    _mk_day(tmp_path, "2026-07-01",
            [{"date": "2026-07-01", "code": "000001", "check": "门X", "severity": "fail", "detail": "d"}],
            [{"code": "000001", "fwd_1_oo": 0.10, "fwd_2_oc": -0.05, "fwd_5_oc": 0.20},
             {"code": "000002", "fwd_1_oo": 0.0, "fwd_2_oc": 0.05, "fwd_5_oc": 0.0}])
    df = roll(tmp_path)
    row = df.set_index("check").loc["门X"]
    # market mean: ex1=0.05→ex1=+0.05(m1=0.05); ex2 mean=0(m2=0)→ex2=-0.05<0 → hit_rate=1.0
    assert abs(row["hit_rate"] - 1.0) < 1e-9


def test_empty_graceful(tmp_path):
    assert len(roll(tmp_path)) == 0
    assert any("无" in ln for ln in render(roll(tmp_path)))


def test_gate_ledger_tail_rate_raw_preserved(tmp_path):
    """`tail_rate_raw` = 被拦票 fwd_2_oc ≤ -5% 占比的原始值(不收缩,供审计/回放对照)。"""
    _mk_day(tmp_path, "2026-07-09",
            [{"date": "2026-07-09", "check": "OW三门·估值不透支", "code": "000002", "level": "binding"},
             {"date": "2026-07-09", "check": "OW三门·估值不透支", "code": "000003", "level": "binding"},
             {"date": "2026-07-09", "check": "OW三门·估值不透支", "code": "000004", "level": "binding"}],
            [{"code": "000002", "fwd_1_oo": -0.06, "fwd_2_oc": -0.08, "fwd_5_oc": -0.1},
             {"code": "000003", "fwd_1_oo": -0.06, "fwd_2_oc": -0.09, "fwd_5_oc": -0.1},
             {"code": "000004", "fwd_1_oo": -0.06, "fwd_2_oc": -0.07, "fwd_5_oc": -0.1}])
    led = roll(tmp_path)
    assert "tail_rate" in led.columns and "tail_rate_raw" in led.columns
    assert led.iloc[0]["tail_rate_raw"] == 1.0            # 3/3 都 ≤ -5% 左尾
    md = "\n".join(render(led))
    assert "拦对率(左尾≤-5%)" in md


def test_gate_ledger_tail_rate_shrinks_toward_pooled_global(tmp_path):
    """P0-3(spec 原文点名"现 n=2-3 天最急需"):tail_rate 收缩,n=`tail_n` 向全部门池化左尾率拉。

    门A 3 次拦截全部左尾(raw=1.0);门B 3 次拦截全部不左尾(raw=0.0)→ 全局池化=3/6=0.5;
    收缩后两门都应落在 raw 与 0.5 之间(真实拉力),且 render 里带 n_tag 的 ⚠(tail_n=3<5)。
    """
    _mk_day(tmp_path, "2026-07-09",
            [{"date": "2026-07-09", "check": "门A", "code": "000002", "level": "binding"},
             {"date": "2026-07-09", "check": "门A", "code": "000003", "level": "binding"},
             {"date": "2026-07-09", "check": "门A", "code": "000004", "level": "binding"},
             {"date": "2026-07-09", "check": "门B", "code": "000005", "level": "binding"},
             {"date": "2026-07-09", "check": "门B", "code": "000006", "level": "binding"},
             {"date": "2026-07-09", "check": "门B", "code": "000007", "level": "binding"}],
            [{"code": "000002", "fwd_1_oo": -0.06, "fwd_2_oc": -0.08, "fwd_5_oc": -0.1},
             {"code": "000003", "fwd_1_oo": -0.06, "fwd_2_oc": -0.09, "fwd_5_oc": -0.1},
             {"code": "000004", "fwd_1_oo": -0.06, "fwd_2_oc": -0.07, "fwd_5_oc": -0.1},
             {"code": "000005", "fwd_1_oo": 0.02, "fwd_2_oc": 0.01, "fwd_5_oc": 0.0},
             {"code": "000006", "fwd_1_oo": 0.02, "fwd_2_oc": 0.02, "fwd_5_oc": 0.0},
             {"code": "000007", "fwd_1_oo": 0.02, "fwd_2_oc": 0.0, "fwd_5_oc": 0.0}])
    led = roll(tmp_path, shrink=True, k=15).set_index("check")
    a, b = led.loc["门A"], led.loc["门B"]
    assert a["tail_n"] == 3 and b["tail_n"] == 3
    assert 0.5 < a["tail_rate"] < 1.0
    assert 0.0 < b["tail_rate"] < 0.5
    expected_a = round((3 * 1.0 + 15 * 0.5) / (3 + 15), 4)
    assert abs(a["tail_rate"] - expected_a) < 1e-6
    md = "\n".join(render(led.reset_index()))
    assert "(n=3⚠)" in md               # tail_n=3<_TAIL_THIN_N(5)


def test_gate_ledger_tail_rate_below_floor_excluded(tmp_path):
    """tail_n<3(MIN_N_INJECT)→ tail_rate=None(绝对禁注,不受 shrink 开关影响)。"""
    _mk_day(tmp_path, "2026-07-09",
            [{"date": "2026-07-09", "check": "OW三门·估值不透支", "code": "000002", "level": "binding"}],
            [{"code": "000002", "fwd_1_oo": -0.06, "fwd_2_oc": -0.08, "fwd_5_oc": -0.1}])
    led = roll(tmp_path)
    assert led.iloc[0]["tail_n"] == 1
    assert pd.isna(led.iloc[0]["tail_rate"])
    assert led.iloc[0]["tail_rate_raw"] == 1.0          # raw 仍算,只是不注入


def test_gate_ledger_tail_rate_shrink_false_returns_raw(tmp_path):
    _mk_day(tmp_path, "2026-07-09",
            [{"date": "2026-07-09", "check": "门A", "code": "000002", "level": "binding"},
             {"date": "2026-07-09", "check": "门A", "code": "000003", "level": "binding"},
             {"date": "2026-07-09", "check": "门A", "code": "000004", "level": "binding"}],
            [{"code": "000002", "fwd_1_oo": -0.06, "fwd_2_oc": -0.08, "fwd_5_oc": -0.1},
             {"code": "000003", "fwd_1_oo": -0.06, "fwd_2_oc": -0.09, "fwd_5_oc": -0.1},
             {"code": "000004", "fwd_1_oo": -0.06, "fwd_2_oc": -0.07, "fwd_5_oc": -0.1}])
    led = roll(tmp_path, shrink=False)
    assert led.iloc[0]["tail_rate"] == 1.0
