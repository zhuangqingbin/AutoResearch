"""P2 扩容 · L3 块:守卫阈 / pass1 / 紧凑表 / 画像词 / 回看窗 / finalist 下限(2026-09-27)。

每键:写进 config → 消费点读到(默认 = 迁前常量,逐字 parity)。
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from autoresearch.contracts import scan_config as reg


def _cfg(tmp_path, monkeypatch, block: dict) -> None:
    p = tmp_path / "scan_config.jsonc"
    p.write_text(json.dumps({"l3": block}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr("autoresearch.scan.user_config.DEFAULT_PATH", p)


def test_guards_cfg_deep_merges_config_over_the_constants(tmp_path, monkeypatch):
    from autoresearch.scan.l3 import merge

    _cfg(tmp_path, monkeypatch, {"guards": {"chase_1d_pct": 5.0, "qualify_conv": {"lane": 70}}})
    g = merge.guards_cfg()
    assert g["chase_1d_pct"] == 5.0
    assert g["sector_cap"] == merge.L3_SECTOR_CAP
    assert g["qualify_conv"] == {"backfill": 55.0, "lane": 70, "lowturn": 55.0}
    assert g["lane_floors"] == {"trend": 2, "lowturn": 1}
    assert g["conv_force_in"] == 75.0 and g["conv_min"] == 55.0


def test_lane_floor_and_healthy_quota_follow_config(tmp_path, monkeypatch):
    from autoresearch.scan.l3 import merge

    _cfg(tmp_path, monkeypatch, {"guards": {"lane_floors": {"trend": 4}, "healthy_quota_frac": 0.5}})
    m = pd.DataFrame({"code": ["1", "2", "3"], "lane": ["trend", "healthy", "lowturn"]})
    floors = merge._lane_quota_floor(m, {0, 1, 2})
    assert floors["trend"] == 4 and floors["lowturn"] == 1
    assert floors["healthy"] == 2                       # ceil(3 × 0.5)
    assert merge._healthy_quota(3) == 2


def test_composite_seat_cfg_exposes_exclude_knife(tmp_path, monkeypatch):
    from autoresearch.scan.l3 import merge

    _cfg(tmp_path, monkeypatch, {"composite_seat": {"enabled": True, "m": 2, "exclude_knife": False}})
    assert merge.composite_seat_cfg() == (True, 2)      # 既有二元组不变
    assert merge.composite_seat_exclude_knife() is False
    monkeypatch.setattr("autoresearch.scan.user_config.DEFAULT_PATH", tmp_path / "nope.jsonc")
    assert merge.composite_seat_exclude_knife() is True


def test_pass1_cfg_reads_resonance_and_healthy_mandatory(tmp_path, monkeypatch):
    from autoresearch.scan.l3 import triage

    _cfg(tmp_path, monkeypatch, {"pass1": {"resonance_cap": 1, "resonance_min_channels": 2, "healthy_mandatory": True}})
    p = triage.pass1_cfg()
    assert p == {"resonance_cap": 1, "resonance_min_channels": 2, "healthy_mandatory": True}
    monkeypatch.setattr("autoresearch.scan.user_config.DEFAULT_PATH", tmp_path / "nope.jsonc")
    assert triage.pass1_cfg() == {"resonance_cap": triage.RESONANCE_CAP, "resonance_min_channels": 3,
                                  "healthy_mandatory": triage.HEALTHY_MANDATORY}


def test_row_profile_words_follow_profile_thresholds(tmp_path, monkeypatch):
    from autoresearch.scan.l3 import prompt

    _cfg(tmp_path, monkeypatch, {"profile": {"pct60_high": 50, "pe_low": 5}})
    row = pd.Series({"pct_60d": 45.0, "pe": 10.0})
    words = prompt.row_profile(row).split("·")
    assert "中位" in words and "高位" not in words
    assert "PE中" in words                              # pe_low 5 → 10 落在中档


def test_table_cfg_reads_delta_and_tolerances(tmp_path, monkeypatch):
    from autoresearch.scan.l3 import prompt

    _cfg(tmp_path, monkeypatch, {"table": {"delta": False, "delta_tol": {"composite": 9.0}, "sections": {"misread": False}}})
    t = prompt.table_cfg()
    assert t["delta"] is False
    assert t["delta_tol"] == {"composite": 9.0, "pct_60d": 2.0}
    assert t["sections"]["misread"] is False and t["sections"]["dist"] is True


def test_lookback_days_resolve_from_config(tmp_path, monkeypatch):
    from autoresearch.scan.l3 import prompt

    _cfg(tmp_path, monkeypatch, {"lookback_days": {"news": 3}})
    assert prompt.lookback_days("news") == 3
    assert prompt.lookback_days("evidence") == 10
    assert prompt.lookback_days("catalyst") == 10


def test_finalist_min_flows_through_gate1_caps(tmp_path, monkeypatch):
    from autoresearch.scan.l4.card_count import effective_caps

    caps = effective_caps({"l3": {"finalist_min": 3}, "l4": {"max_cards": 13}}, 30)
    assert caps["l3min"] == 3
    assert effective_caps({}, 30)["l3min"] == 7
    assert effective_caps({"l4": {"max_cards": 4}}, 30)["l3min"] <= effective_caps({"l4": {"max_cards": 4}}, 30)["l3cap"]


def test_session_agent_l3_bounds_read_the_gate1_echo():
    from autoresearch.session_agent.dispatch import l3_bounds_from_gate1

    assert l3_bounds_from_gate1({"l3cap": 10, "l3min": 4, "max_cards": 13}) == (4, 10)
    assert l3_bounds_from_gate1({"l3cap": 10, "max_cards": 13}) == (7, 10)    # 旧 GATE1 缺 l3min → 内建 7
