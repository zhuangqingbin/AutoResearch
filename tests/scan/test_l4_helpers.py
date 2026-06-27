"""L4 helpers 单测 —— force_full_card(强先验白名单)+ audit_rubric_gates(自评一致性抽检)。无网络。

覆盖 spec §Leaf L4:
  - 强先验(高 conviction + 多路/lane 救回)→ 强制满卡(不被表面早停误杀)
  - 弱先验 → 不强制
  - 卡片自评 gate=True 但正文矛盾 → flag(防 gaming);一致/未声明 → 不 flag
"""
from __future__ import annotations

from autoresearch.scan.agents.l4_card import audit_rubric_gates, force_full_card


def test_force_full_strong_prior():
    assert force_full_card({"conviction": 80, "n_channels": 5}) is True


def test_force_full_weak_prior():
    assert force_full_card({"conviction": 40, "n_channels": 2}) is False


def test_force_full_lane_reserved_path():
    assert force_full_card({"conviction": 75, "n_channels": 1, "l2_lane_reserved": True}) is True


def test_force_full_high_conv_but_single_channel_no_lane():
    assert force_full_card({"conviction": 90, "n_channels": 1}) is False    # 高 conv 但孤路无 lane → 不强制


def test_audit_flags_contradiction():
    flags = audit_rubric_gates("主力资金持续净流出,盘面走弱", {"主力真在": True})
    assert flags and "主力真在" in flags[0]


def test_audit_no_flag_when_consistent():
    assert audit_rubric_gates("主力资金净流入,放量上攻", {"主力真在": True}) == []


def test_audit_ignores_unclaimed_gate():
    assert audit_rubric_gates("主力流出明显", {"主力真在": False}) == []
