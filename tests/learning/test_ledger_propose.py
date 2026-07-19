"""channel_ledger propose/apply 单测 —— 闭环半自动调 quota(advisory)。合成 ledger,无网络。

覆盖 spec §D + 两笔尺子/基线修正(2026-07-17):
  - **主尺 t2**(2026-07-10 用户裁定持仓超短 1~2 日):提议由 mean_unique_excess_t2 驱动,
    t5 列保留展示但不再驱动决策(测试里 t2/t5 故意反号以证明谁在开车)
  - 持续负边际超额(n_days≥min)→ 提议降;持续正 → 提议升
  - 样本不足(n_days<min)/ 中性带 → 不提议
  - apply 单步幅度封顶(±max_delta_frac)防抖;未知路忽略
  - **current_quotas() 基线 = registry 默认 ⊕ scan_config.jsonc 的 funnel.channel_quotas**
    (pr_20260714_004:基线硬编码会重复提议已实施的改动);缺文件/缺键 → registry(parity)
"""
from __future__ import annotations

import pandas as pd

from autoresearch.learning.channel_ledger import (
    current_quotas,
    propose_quota_adjustments,
)

_COLS = ["channel", "n_days", "sum_unique",
         "mean_unique_excess_t2", "mean_excess_t2", "mean_hit_rate_t2",
         "mean_unique_excess_t5", "mean_excess_t5", "mean_hit_rate_t5"]


def _led(rows):
    return pd.DataFrame(rows, columns=_COLS)


def test_persistent_negative_t2_proposes_down_even_if_t5_positive():
    # t2 −2%(主尺)· t5 +5%(参考)→ 仍提议降 = t5 已退位不驱动
    led = _led([["accumulation", 5, 10, -0.02, -0.01, 0.30, 0.05, 0.04, 0.60]])
    props = propose_quota_adjustments(led, {"accumulation": 120})
    assert props and props[0]["proposed_quota"] < 120 and props[0]["delta"] < 0
    assert "T2" in props[0]["reason"]


def test_persistent_positive_t2_proposes_up_even_if_t5_negative():
    led = _led([["heat", 4, 40, 0.03, 0.02, 0.55, -0.04, -0.03, 0.30]])
    props = propose_quota_adjustments(led, {"heat": 200})
    assert props and props[0]["proposed_quota"] > 200


def test_thin_sample_no_proposal():
    led = _led([["heat", 2, 8, -0.05, -0.03, 0.20, -0.05, -0.03, 0.20]])     # n_days 2 < 3
    assert propose_quota_adjustments(led, {"heat": 200}) == []


def test_neutral_band_t2_no_proposal_even_if_t5_extreme():
    # t2 落中性带(0<m<pos_thresh)、t5 深负 → 不提议 = 决策只看 t2
    led = _led([["value", 5, 30, 0.001, 0.0, 0.40, -0.09, -0.08, 0.10]])
    assert propose_quota_adjustments(led, {"value": 200}) == []


# ─────────────── ③ quota 基线读 scan_config(pr_20260714_004) ───────────────


def test_current_quotas_registry_fallback_when_no_config(tmp_path):
    """缺配置文件 → 纯 registry 默认(parity,即原硬编码值)。"""
    q = current_quotas(config_path=tmp_path / "absent.jsonc")
    assert q.get("composite") == 400 and q.get("heat") == 200 and len(q) >= 9


def test_current_quotas_overlays_scan_config(tmp_path):
    p = tmp_path / "scan_config.jsonc"
    p.write_text('{\n  // 已实施:heat 200→150\n  "funnel": {"channel_quotas": {"heat": 150, "ghost": 7}}\n}',
                 encoding="utf-8")
    q = current_quotas(config_path=p)
    assert q["heat"] == 150            # 配置覆盖生效(loader 支持 // 注释)
    assert q["composite"] == 400       # 未覆盖路保持 registry 默认
    assert "ghost" not in q            # 未知路忽略:registry 是路存在性的唯一事实源


def test_current_quotas_config_without_quota_key_is_parity(tmp_path):
    p = tmp_path / "scan_config.jsonc"
    p.write_text('{"funnel": {}}', encoding="utf-8")
    assert current_quotas(config_path=p).get("heat") == 200


def test_implemented_quota_not_reproposed(tmp_path):
    """pr_20260714_004 主场景:提议 heat 200→150 已写进 scan_config 后,基线读配置 →
    「200→150」这条已实施提议不再重现(信号仍负时从 150 继续降是新信息,不是重复)。"""
    p = tmp_path / "scan_config.jsonc"
    p.write_text('{"funnel": {"channel_quotas": {"heat": 150}}}', encoding="utf-8")
    led = _led([["heat", 5, 20, -0.02, -0.01, 0.30, -0.02, -0.01, 0.30]])
    props = propose_quota_adjustments(led, current_quotas(config_path=p))
    assert all(not (pp["cur_quota"] == 200 and pp["proposed_quota"] == 150) for pp in props)
    assert props and props[0]["cur_quota"] == 150          # 基线已是配置值


def test_implemented_quota_plus_neutral_signal_no_proposal(tmp_path):
    """配置里 quota 已等于提议目标且信号回到中性带 → 完全不再出提议。"""
    p = tmp_path / "scan_config.jsonc"
    p.write_text('{"funnel": {"channel_quotas": {"heat": 150}}}', encoding="utf-8")
    led = _led([["heat", 5, 20, 0.002, 0.001, 0.45, 0.002, 0.001, 0.45]])
    assert propose_quota_adjustments(led, current_quotas(config_path=p)) == []
