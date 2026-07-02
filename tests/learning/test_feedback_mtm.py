"""feedback_store MTM 套件:R1 regime 域 / R2+R7 mtm_update / R4 看板 / R6 注入治理。

spec: docs/specs/2026-07-02-learning-mtm-design.md
"""
from __future__ import annotations

import pytest

import autoresearch.learning.feedback_store as fs

_G = [("global", "*")]


@pytest.fixture(autouse=True)
def _tmp_store(tmp_path):
    old = fs.KNOW
    fs.set_root(tmp_path)
    yield
    fs.set_root(old)


# ── R1 · regime 作用域 ──


def test_regime_scope_filter_and_compat():
    fs.upsert_lesson("a", ("global", "*"), rule="避险专用", evidence=["e"], regimes=["risk_off"])
    fs.upsert_lesson("b", ("global", "*"), rule="全域", evidence=["e"])
    assert {h["id"] for h in fs.lessons_for(_G, regime="trend")} == {"ls_b"}
    assert {h["id"] for h in fs.lessons_for(_G, regime="risk_off")} == {"ls_a", "ls_b"}
    assert {h["id"] for h in fs.lessons_for(_G)} == {"ls_a", "ls_b"}   # None = parity


# ── R2+R7 · mark-to-market ──


def test_mtm_update_idempotent_and_confidence():
    fs.upsert_lesson("m", ("global", "*"), rule="r", evidence=[], confidence=0.6)
    r1 = fs.mtm_update("m", "refute", day="2026-07-02")
    assert r1["mtm"]["refute"] == 1 and abs(r1["confidence"] - 0.52) < 1e-9
    r2 = fs.mtm_update("m", "refute", day="2026-07-02")            # 同日同 verdict 幂等
    assert r2["mtm"]["refute"] == 1
    r3 = fs.mtm_update("m", "support", day="2026-07-03")
    assert r3["mtm"]["support"] == 1 and abs(r3["confidence"] - 0.55) < 1e-9


def test_mtm_refute_threshold_proposes_once():
    fs.upsert_lesson("g", ("global", "*"), rule="r", evidence=[],
                     guard={"field": "x", "op": ">", "value": 1})
    for d in ("2026-07-01", "2026-07-02", "2026-07-03"):
        fs.mtm_update("g", "refute", day=d)
    props = fs.open_proposals("2026-07-04")
    assert sum("ls_g" in p["summary"] for p in props) == 1          # 达阈自动提名(人批)
    fs.mtm_update("g", "refute", day="2026-07-04")                  # 过阈后不重复提名
    assert sum("ls_g" in p["summary"] for p in fs.open_proposals("2026-07-05")) == 1


# ── R4 · proposals 看板 ──


def test_open_proposals_age_sorted():
    fs.add_proposal("gate", "s-old", ts="2026-06-10T09:00:00")
    fs.add_proposal("factor", "s-new", ts="2026-07-01T09:00:00")
    props = fs.open_proposals("2026-07-02")
    assert props[0]["summary"] == "s-old" and props[0]["age_days"] == 22
    assert props[1]["age_days"] == 1


# ── R6 · 注入治理 ──


def test_lessons_rank_tiebreak():
    fs.upsert_lesson("old", ("global", "*"), rule="old", evidence=[], confidence=0.6, day="2026-06-01")
    fs.upsert_lesson("new", ("global", "*"), rule="new", evidence=[], confidence=0.6, day="2026-07-01")
    fs.upsert_lesson("ind", ("industry", "半导体"), rule="ind", evidence=[], confidence=0.6, day="2026-06-01")
    hits = fs.lessons_for([("global", "*"), ("industry", "半导体")])
    assert [h["id"] for h in hits] == ["ls_new", "ls_ind", "ls_old"]  # conf→新近→scope精度


def test_render_cap_and_small_store_parity():
    for i in range(4):
        fs.upsert_lesson(f"p{i}", ("global", "*"), rule=f"rule{i}", evidence=[])
    once = fs.render_calibration_block(_G)
    assert fs.render_calibration_block(_G) == once and "rule0" in once   # ≤K 不触发 cap
    for i in range(4, 13):
        fs.upsert_lesson(f"p{i}", ("global", "*"), rule=f"rule{i}", evidence=[])
    capped = fs.render_calibration_block(_G)
    assert sum(f"rule{i}" in capped for i in range(13)) == 8             # cap K=8
    assert "另有 5 条" in capped                                          # 溢出提示
