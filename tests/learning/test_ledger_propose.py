"""channel_ledger propose/apply 单测 —— 闭环半自动调 quota(advisory)。合成 ledger,无网络。

覆盖 spec §D:
  - 持续负边际超额(n_days≥min)→ 提议降;持续正 → 提议升
  - 样本不足(n_days<min)/ 中性带 → 不提议
  - apply 单步幅度封顶(±max_delta_frac)防抖;未知路忽略
  - current_quotas() 从 registry 取(全 9 路存在)
"""
from __future__ import annotations

import pandas as pd

from autoresearch.learning.channel_ledger import (
    apply_proposals,
    current_quotas,
    propose_quota_adjustments,
)

_COLS = ["channel", "n_days", "sum_unique", "mean_unique_excess_t5", "mean_excess_t5", "mean_hit_rate_t5"]


def _led(rows):
    return pd.DataFrame(rows, columns=_COLS)


def test_persistent_negative_proposes_down():
    led = _led([["accumulation", 5, 10, -0.02, -0.01, 0.30]])
    props = propose_quota_adjustments(led, {"accumulation": 120})
    assert props and props[0]["proposed_quota"] < 120 and props[0]["delta"] < 0


def test_persistent_positive_proposes_up():
    led = _led([["heat", 4, 40, 0.03, 0.02, 0.55]])
    props = propose_quota_adjustments(led, {"heat": 200})
    assert props and props[0]["proposed_quota"] > 200


def test_thin_sample_no_proposal():
    led = _led([["heat", 2, 8, -0.05, -0.03, 0.20]])     # n_days 2 < 3
    assert propose_quota_adjustments(led, {"heat": 200}) == []


def test_neutral_band_no_proposal():
    led = _led([["value", 5, 30, 0.001, 0.0, 0.40]])      # 0<m<pos_thresh → 中性
    assert propose_quota_adjustments(led, {"value": 200}) == []


def test_apply_caps_delta():
    props = [{"channel": "heat", "cur_quota": 200, "proposed_quota": 400, "delta": 200, "reason": "x"}]
    new, audit = apply_proposals(props, {"heat": 200}, max_delta_frac=0.25)
    assert new["heat"] == 250                              # +25% 封顶(不是 +200)
    assert audit


def test_apply_ignores_unknown_channel():
    new, audit = apply_proposals(
        [{"channel": "ghost", "cur_quota": 1, "proposed_quota": 9, "delta": 8, "reason": ""}], {"heat": 200})
    assert new == {"heat": 200} and audit == []


def test_current_quotas_from_registry():
    q = current_quotas()
    assert q.get("composite") == 400 and q.get("heat") == 200 and len(q) >= 9
