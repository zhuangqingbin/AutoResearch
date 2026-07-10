#!/usr/bin/env python3
"""L3.5 可插拔闸 registry + 三策略 单测(纯函数层,零 I/O)。

design: docs/specs/2026-07-11-recall-gate-pinned-config-design.md §3
(docs/plans/2026-07-11-l35-gate-backtest-plan.md Task 1)。

覆盖:
  - registry 模式(镜像 recall/registry.py):注册/取用/未知名报错/重复注册报错且不顶替原函数/
    全部内置闸对空帧不炸。
  - `passthrough`:全放行 = parity(默认闸,配置缺失时现状行为)。
  - `topk_simple`:按 conviction 排名(=『L3 排名』)截 params.k,对照基线。
  - `conviction_floor_quota`:conviction 地板 + lane 多样性配额(挤压救回)+ regime 分档上限 +
    与 params.l4_budget 取 min(预算旗收编为输入)。
  - **exempt 硬契约**(与 A3-T4 pinned/carryover/watchlist 直通车对齐):exempt 集恒直通、
    不占配额、不进 cut——即便 conviction 远低于地板、即便 cap 已满。
"""
from __future__ import annotations

import pandas as pd
import pytest

from autoresearch.scan.l35_gate import (
    GateResult,
    build,
    conviction_floor_quota,
    passthrough,
    registered_gates,
    topk_simple,
)


def _judged(rows: list[dict]) -> pd.DataFrame:
    """合成 judged 帧;每行默认 conviction=50.0/lane="trend",rows 里的键覆盖默认值。"""
    base = {"conviction": 50.0, "lane": "trend"}
    return pd.DataFrame([{**base, **r} for r in rows])


# ============================== registry 模式 ==============================

def test_registered_gates_lists_all_three_builtin():
    assert registered_gates() == ["conviction_floor_quota", "passthrough", "topk_simple"]


def test_build_returns_callable_for_known_name():
    fn = build("passthrough")
    assert callable(fn)
    assert fn is passthrough


def test_build_unknown_name_raises_keyerror_with_message():
    with pytest.raises(KeyError, match="unknown gate"):
        build("no_such_gate")


def test_duplicate_registration_raises_and_does_not_clobber_existing():
    from autoresearch.scan import l35_gate

    with pytest.raises(KeyError, match="already registered"):
        @l35_gate.gate("passthrough")
        def _dup(judged, *, regime, exempt, params):
            return GateResult(picked=[], cut=[])

    assert build("passthrough") is passthrough   # 原注册函数未被顶替


@pytest.mark.parametrize("name", registered_gates())
def test_all_builtin_gates_handle_empty_frame(name):
    fn = build(name)
    r = fn(_judged([]), regime="range", exempt=set(), params={})
    assert isinstance(r, GateResult)
    assert r.picked == [] and r.cut == []


# ============================== passthrough ==============================

def test_passthrough_keeps_everyone_regardless_of_conviction_or_lane():
    df = _judged([
        {"code": "000001", "conviction": 90.0, "lane": "trend"},
        {"code": "000002", "conviction": 5.0, "lane": "reversion"},  # 其它闸会砍,passthrough 不砍
        {"code": "000003", "conviction": None, "lane": None},
    ])
    r = passthrough(df, regime="risk_off", exempt=set(), params={})
    assert set(r.picked) == {"000001", "000002", "000003"}
    assert r.cut == []


# ============================== topk_simple ==============================

def test_topk_simple_cuts_to_k_by_conviction_rank():
    df = _judged([{"code": f"{i:06d}", "conviction": 100 - i} for i in range(6)])
    r = topk_simple(df, regime="trend", exempt=set(), params={"k": 3})
    assert r.picked == ["000000", "000001", "000002"]
    assert {c["code"] for c in r.cut} == {"000003", "000004", "000005"}
    assert all(c["reason"] for c in r.cut)


def test_topk_simple_exempt_bypasses_k_and_is_not_cut():
    df = _judged([{"code": f"{i:06d}", "conviction": 100 - i} for i in range(6)])
    r = topk_simple(df, regime="trend", exempt={"000005"}, params={"k": 2})  # 000005 = 全场最低分
    assert "000005" in r.picked
    assert "000005" not in {c["code"] for c in r.cut}
    assert {"000000", "000001"} <= set(r.picked)          # exempt 不挤占 top-2 名额


def test_topk_simple_missing_k_defaults_to_no_cut():
    df = _judged([{"code": f"{i:06d}", "conviction": 100 - i} for i in range(4)])
    r = topk_simple(df, regime="trend", exempt=set(), params={})
    assert set(r.picked) == {"000000", "000001", "000002", "000003"}
    assert r.cut == []


def test_topk_simple_negative_k_yields_no_picks_not_reversed_slice():
    # 防坑:iloc[:k] 若直喂负 k 是 Python 反向切片语义(=去掉尾部|k|行),非"零选中"
    df = _judged([{"code": f"{i:06d}", "conviction": 100 - i} for i in range(4)])
    r = topk_simple(df, regime="trend", exempt=set(), params={"k": -1})
    assert r.picked == []


# ==================== conviction_floor_quota:conviction 地板 ====================

def test_floor_cuts_low_conviction_with_reason():
    df = _judged([
        {"code": "000001", "conviction": 80.0},
        {"code": "000002", "conviction": 10.0},
    ])
    r = conviction_floor_quota(df, regime="trend", exempt=set(), params={"floor": 50})
    assert r.picked == ["000001"]
    assert len(r.cut) == 1 and r.cut[0]["code"] == "000002"
    assert "floor" in r.cut[0]["reason"]


def test_exempt_bypasses_floor_even_when_conviction_is_lowest():
    """硬契约:exempt 票 conviction 低于地板仍必须在 picked 里,且不进 cut。"""
    df = _judged([
        {"code": "000001", "conviction": 90.0},
        {"code": "000002", "conviction": 1.0},     # exempt,远低于 floor
    ])
    r = conviction_floor_quota(df, regime="trend", exempt={"000002"}, params={"floor": 50})
    assert "000002" in r.picked
    assert "000002" not in {c["code"] for c in r.cut}


def test_exempt_does_not_count_against_cap():
    """exempt 不占配额:即便 cap=1,exempt + 1 正常票都应在 picked(cap 不应把 exempt 挤走或反被 exempt 挤满)。"""
    df = _judged([
        {"code": "000001", "conviction": 90.0},
        {"code": "000002", "conviction": 1.0},     # exempt
    ])
    r = conviction_floor_quota(df, regime="risk_off", exempt={"000002"},
                               params={"floor": 0, "caps": {"risk_off": 1}})
    assert set(r.picked) == {"000001", "000002"}


# ================= conviction_floor_quota:lane 多样性挤压 =================

def test_lane_diversity_rescues_low_score_lane_representative():
    """挤压场景:trend 6 只分数全面压过 reversion 唯一 1 只;cap=3 若纯按 conviction
    截断会让 reversion 代表出局——lane_min=1 必须把它救回来。"""
    rows = [{"code": f"t{i}", "conviction": 90 - i, "lane": "trend"} for i in range(6)]
    rows.append({"code": "r0", "conviction": 20.0, "lane": "reversion"})  # 全场最低分
    df = _judged(rows)
    r = conviction_floor_quota(df, regime="trend", exempt=set(),
                               params={"floor": 0, "caps": {"trend": 3}, "lane_min": 1})
    assert "r0" in r.picked, f"reversion 低分代表应被 lane_min 救回,实际 picked={r.picked}"
    assert len(r.picked) == 3                      # cap 仍生效,只是构成变了


def test_lane_diversity_respects_lane_min_greater_than_one():
    rows = [{"code": f"t{i}", "conviction": 90 - i, "lane": "trend"} for i in range(5)]
    rows += [{"code": f"v{i}", "conviction": 30 - i, "lane": "value"} for i in range(2)]
    df = _judged(rows)
    r = conviction_floor_quota(df, regime="trend", exempt=set(),
                               params={"floor": 0, "caps": {"trend": 5}, "lane_min": 2})
    lanes = df.set_index("code")["lane"]
    picked_lanes = [lanes[c] for c in r.picked]
    assert picked_lanes.count("value") >= 2


def test_no_lane_diversity_needed_when_cap_covers_all_eligible():
    df = _judged([{"code": f"{i:06d}", "conviction": 90 - i} for i in range(3)])
    r = conviction_floor_quota(df, regime="trend", exempt=set(),
                               params={"floor": 0, "caps": {"trend": 10}})
    assert set(r.picked) == {"000000", "000001", "000002"}
    assert r.cut == []


# ================= conviction_floor_quota:regime 分档上限 =================

@pytest.mark.parametrize("regime,expected_cap", [("trend", 10), ("range", 8), ("risk_off", 6)])
def test_default_regime_caps_numeric(regime, expected_cap):
    df = _judged([{"code": f"{i:03d}", "conviction": 100 - i} for i in range(20)])
    r = conviction_floor_quota(df, regime=regime, exempt=set(), params={"floor": 0})
    assert len(r.picked) == expected_cap


def test_unknown_regime_falls_back_to_range_cap():
    df = _judged([{"code": f"{i:03d}", "conviction": 100 - i} for i in range(20)])
    r = conviction_floor_quota(df, regime="unknown_regime_xyz", exempt=set(), params={"floor": 0})
    assert len(r.picked) == 8


def test_unknown_regime_with_custom_caps_missing_range_key_falls_back_to_8():
    df = _judged([{"code": f"{i:03d}", "conviction": 100 - i} for i in range(20)])
    r = conviction_floor_quota(df, regime="unknown_regime_xyz", exempt=set(),
                               params={"floor": 0, "caps": {"trend": 4}})
    assert len(r.picked) == 8


def test_custom_caps_override_default():
    df = _judged([{"code": f"{i:03d}", "conviction": 100 - i} for i in range(20)])
    r = conviction_floor_quota(df, regime="trend", exempt=set(),
                               params={"floor": 0, "caps": {"trend": 4}})
    assert len(r.picked) == 4


# ============== conviction_floor_quota:与 menu.l4_budget 取 min ==============

def test_l4_budget_param_tightens_below_regime_cap():
    df = _judged([{"code": f"{i:03d}", "conviction": 100 - i} for i in range(20)])
    r = conviction_floor_quota(df, regime="trend", exempt=set(),
                               params={"floor": 0, "l4_budget": 5})   # trend cap=10,budget=5 更紧
    assert len(r.picked) == 5


def test_l4_budget_param_looser_than_regime_cap_is_noop():
    df = _judged([{"code": f"{i:03d}", "conviction": 100 - i} for i in range(20)])
    r = conviction_floor_quota(df, regime="risk_off", exempt=set(),
                               params={"floor": 0, "l4_budget": 99})  # risk_off cap=6 更紧
    assert len(r.picked) == 6


def test_l4_budget_zero_means_no_non_exempt_picks_but_exempt_still_through():
    df = _judged([
        {"code": "000001", "conviction": 90.0},
        {"code": "000002", "conviction": 1.0},    # exempt
    ])
    r = conviction_floor_quota(df, regime="trend", exempt={"000002"},
                               params={"floor": 0, "l4_budget": 0})
    assert r.picked == ["000002"]
    assert any(c["code"] == "000001" for c in r.cut)


# ==================== conviction_floor_quota:缺列降级 ====================

def test_missing_lane_column_degrades_without_raising():
    df = pd.DataFrame({"code": [f"{i:03d}" for i in range(5)],
                       "conviction": [90, 80, 70, 60, 50]})
    r = conviction_floor_quota(df, regime="trend", exempt=set(),
                               params={"floor": 0, "caps": {"trend": 2}})
    assert len(r.picked) == 2


def test_missing_conviction_column_degrades_without_raising():
    df = pd.DataFrame({"code": [f"{i:03d}" for i in range(5)], "lane": ["trend"] * 5})
    r = conviction_floor_quota(df, regime="trend", exempt=set(), params={"floor": 0})
    assert len(r.picked) == 5     # 无 conviction 列 → 视作并列(非 -inf 惩罚),floor=0 全过
