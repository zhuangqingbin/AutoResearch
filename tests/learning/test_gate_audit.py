"""retro MTM 机判 + 门审计 + 看板节 + decay 节奏。合成 fixture,无网络。

spec: docs/specs/2026-07-02-learning-mtm-design.md §R2/R3/R4
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import autoresearch.learning.feedback_store as fs
import autoresearch.learning.retro as retro


@pytest.fixture(autouse=True)
def _tmp_store(tmp_path):
    old = fs.KNOW
    fs.set_root(tmp_path / "know")
    yield
    fs.set_root(old)


def _attr(n=20, sat_fwd=-0.02, base_fwd=0.01, sat_n=6, field="winner_rate"):
    """前 sat_n 行满足 guard(field>88),其 fwd 均值偏离市场 → 判 support/refute。"""
    df = pd.DataFrame({"code": [f"{i:06d}" for i in range(n)],
                       field: [95.0] * sat_n + [50.0] * (n - sat_n),
                       "fwd_1_oo": [sat_fwd] * sat_n + [base_fwd] * (n - sat_n),
                       "fwd_5_oc": np.linspace(-0.05, 0.05, n)})
    return df


def _lesson(guard=True):
    return fs.upsert_lesson("wr_gate", ("global", "*"), rule="获利盘满勿买", evidence=[],
                            guard={"field": "winner_rate", "op": ">", "value": 88} if guard else None)


def test_mtm_check_guards_support_refute_skip():
    lsn = _lesson()
    out = retro.mtm_check_guards(_attr(sat_fwd=-0.05), [lsn], day="2026-07-02", apply=False)
    assert out[0]["verdict"] == "support" and out[0]["n"] == 6      # 拦对:满足组显著跑输
    out2 = retro.mtm_check_guards(_attr(sat_fwd=0.08), [lsn], day="2026-07-02", apply=False)
    assert out2[0]["verdict"] == "refute"                            # 拦错:满足组跑赢
    out3 = retro.mtm_check_guards(_attr(sat_n=3), [lsn], day="2026-07-02", apply=False)
    assert out3[0]["verdict"] == "skip"                              # n<5 不判


def test_mtm_check_guards_applies_counters():
    lsn = _lesson()
    retro.mtm_check_guards(_attr(sat_fwd=-0.05), [lsn], day="2026-07-02", apply=True)
    rec = next(r for r in fs.lessons_for([("global", "*")]) if r["id"] == "ls_wr_gate")
    assert rec["mtm"]["support"] == 1


def test_gate_audit_joins_fwd(tmp_path):
    sdir = tmp_path / "2026-07-01"
    sdir.mkdir()
    pd.DataFrame([{"date": "2026-07-01", "code": "000001", "check": "经验红线·获利盘满",
                   "severity": "fail", "detail": "d"}]).to_csv(sdir / "gate_fires.csv", index=False)
    attr = _attr()
    out = retro.gate_audit(attr, sdir)
    assert len(out) == 1 and out.iloc[0]["code"] == "000001"
    assert out.iloc[0]["ex1"] < 0                                    # 被拦票 fwd 低于市场 → 拦对
    assert len(retro.gate_audit(attr, tmp_path / "nope")) == 0       # 缺文件优雅


def test_mark_done_triggers_decay(tmp_path, monkeypatch):
    called = {}
    monkeypatch.setattr(fs, "decay_lessons", lambda **kw: called.setdefault("hit", True) and [])
    retro.mark_done("2026-07-01", scan_root=tmp_path)
    assert called.get("hit") is True
