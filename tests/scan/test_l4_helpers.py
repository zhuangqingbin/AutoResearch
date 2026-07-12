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


def test_pick_opportunity_candidates(tmp_path):
    """0买日机会成本红队名单:Hold 按 L3 conviction 降序取 top-k(spec 2026-07-02 任务E)。"""
    import pandas as pd

    from autoresearch.scan.agents.l4_card import pick_opportunity_candidates
    pd.DataFrame({"code": ["000001", "000002", "000003", "000004"],
                  "conviction": [55, 70, 62, 90]}).to_csv(tmp_path / "finalists.csv", index=False)
    ratings = {"000001": "Hold", "000002": "Hold", "000003": "Hold", "000004": "Underweight"}
    out = pick_opportunity_candidates(ratings, tmp_path, k=2)
    assert out == ["000002", "000003"]        # UW 不入;按 conviction 70>62>55 取 2
    assert pick_opportunity_candidates({}, tmp_path) == []
    assert pick_opportunity_candidates(ratings, tmp_path / "nope") == []   # 缺 finalists 优雅


def test_force_full_card_pinned_always_full():
    """📌 保送持仓票恒强制满卡:你真金白银持有的票,盈利质量/偿付(爆雷)两维不允许标『未核』。

    pinned 票绕过 L3 finalist tier(finalist=false → conviction 常 50–55),按 conv_min=70
    的通用判据必然落进早停 → 2026-07-12 实测 4/4 持仓卡全部早停在 P3、爆雷维未核。
    """
    assert force_full_card({"lane": "pinned", "conviction": 50, "n_channels": 1}) is True
    assert force_full_card({"lane": "pinned"}) is True                     # 连 conviction 都缺也强制
    assert force_full_card({"lane": "value", "conviction": 50, "n_channels": 1}) is False
