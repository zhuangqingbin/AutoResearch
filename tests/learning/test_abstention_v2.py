"""弃权裁决 v2:反事实换 shadow_buys(Wave8 W8-11)。

**病根**:v1 的 FALSE 判据 = `rejection_attribution.csv` 里**任一** tradable 票
相对市场中位 ≥+2pp。而那张表覆盖**全市场**(07-24 实测 5,528 行,其中 opportunity
命中 **1,299 只**)—— 5,000 只票的市场几乎天天有人跑赢 2pp,所以判据近乎恒真。
它量的是**召回上限**,不是**弃权决策**。

后果:headline「CORRECT 0 · FALSE 5」与同屏的 paper_nav(真实 −0.24% vs 无门影子
−3.45% = 门在挣钱)互扇耳光,两条读数指向相反的结论。

**v2 判据**:只问系统当日**最想买的那几只**(`shadow_buys.csv`,K=3,20260618 起有史)
里有没有真跑赢 +2pp 且次开可交易的 —— 这才是"如果门放行,我会买的东西"。
全市场口径降级为 `recall_ceiling_n` 诊断字段(信息保留,撤出裁决 headline)。

过渡期两个 status 并存(v1 `status` + v2 `status_v2`);2026-08-19 判据达成(v2 达
≥10 个 mature day 且两日重跑一致,实测 19/19 全成熟)——v1 **headline** 已从
`abstention_ledger.md` 退役,但 `status` 字段/计算本身不删(`market.py`/`health.py`/
`zero_buy_ledger.py` 三个下游消费点仍读它),故本文件的计算层断言(`classify_abstention`
仍双算 v1/v2)继续成立,不受影响。
缺 shadow 数据的日子 `status_v2=None`(**不新增枚举值** —— STATUSES 有迭代消费方),
md 渲染 `—(no_shadow)` 且不计入 v2 统计。
"""

from __future__ import annotations

import pandas as pd

from autoresearch.learning.abstention_ledger import (
    ABSTENTION_VERDICT_SCHEMA_VERSION,
    AbstentionVerdict,
    classify_abstention,
)
import pytest  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)


def _rows(records: list[dict]) -> pd.DataFrame:
    base = {"date": "2026-07-28", "final_action": "ABSTAIN", "mature": True,
            "buyable": True, "gate_state_quality": "OK", "first_rejection_stage": "L4"}
    return pd.DataFrame([{**base, **r} for r in records])


def test_whole_market_opportunity_no_longer_drives_verdict(monkeypatch):
    """**核心**:全市场有 1,500 只跑赢 +2pp,但 shadow 三只全跑输 → v2 不判 FALSE。"""
    rows = _rows(
        [{"code": "000001", "excess_2": -0.05, "opportunity": False},
         {"code": "000002", "excess_2": -0.04, "opportunity": False}]
        + [{"code": f"6{i:05d}", "excess_2": 0.09, "opportunity": True} for i in range(1500)]
    )

    v = classify_abstention(rows, shadow_codes=["000001", "000002"])

    assert v.status == "FALSE", "v1 口径保持原样(过渡期双打印)"
    assert v.recall_ceiling_n == 1500, "全市场口径降级为诊断字段,信息不丢"
    assert v.status_v2 == "CORRECT", (
        "shadow 三只全跑输 → 弃权是对的;5000 只市场里有人涨不构成对弃权的指控"
    )
    assert list(v.shadow_opportunity_codes) == []


def test_shadow_hit_makes_v2_false():
    """shadow 里任一只真跑赢 +2pp 且可交易 → v2 判 FALSE(这才是真错过)。"""
    rows = _rows([
        {"code": "300857", "excess_2": 0.03, "opportunity": True},
        {"code": "601869", "excess_2": -0.06, "opportunity": False},
    ])

    v = classify_abstention(rows, shadow_codes=["300857", "601869"])

    assert v.status_v2 == "FALSE"
    assert list(v.shadow_opportunity_codes) == ["300857"]
    assert "shadow_buy_outperformed" in v.reasons


def test_shadow_must_also_be_buyable_and_mature():
    """不可交易/未成熟的 shadow 不算错过 —— 与 v1 的 eligible 同一条纪律。"""
    rows = _rows([
        {"code": "300857", "excess_2": 0.09, "opportunity": True, "buyable": False},
        {"code": "601869", "excess_2": 0.09, "opportunity": True, "mature": False},
    ])

    v = classify_abstention(rows, shadow_codes=["300857", "601869"])

    assert v.status_v2 != "FALSE"
    assert list(v.shadow_opportunity_codes) == []


def test_no_shadow_data_yields_none_not_a_new_status():
    """缺 shadow 数据 → status_v2=None,**不得**新增枚举值(STATUSES 有迭代消费方)。"""
    rows = _rows([{"code": "000001", "excess_2": -0.05, "opportunity": False}])

    v = classify_abstention(rows, shadow_codes=[])

    assert v.status_v2 is None
    assert v.status in {"CORRECT", "NEUTRAL", "FALSE", "IMMATURE"}


def test_v1_records_still_load_after_schema_bump(tmp_path):
    """旧 v1 记录必须仍可读 —— 哈希按各自 schema 版本校验,不重算。"""
    v1_payload = {
        "schema_version": 1, "date": "2026-07-15", "status": "FALSE",
        "n_bought": 0, "n_rejected": 5534, "n_opportunities": 2,
        "opportunity_codes": ["000001", "000002"], "data_quality": "DEGRADED",
        "reasons": ["market_relative_opportunity"],
        "generated_at": "2026-07-28T13:00:00.000000Z", "verdict_hash": "",
    }
    from autoresearch.scan.run_contract import sha256_json
    h = {k: v for k, v in v1_payload.items() if k != "verdict_hash"}
    v1_payload["verdict_hash"] = sha256_json(h)

    v = AbstentionVerdict.from_dict(v1_payload)

    assert v.schema_version == 1
    assert v.status == "FALSE"
    assert v.status_v2 is None and v.recall_ceiling_n == 0


def test_new_records_carry_v2_schema():
    rows = _rows([{"code": "000001", "excess_2": -0.05, "opportunity": False}])
    v = classify_abstention(rows, shadow_codes=["000001"])
    assert v.schema_version == ABSTENTION_VERDICT_SCHEMA_VERSION == 2
    assert AbstentionVerdict.from_dict(v.to_dict()).verdict_hash == v.verdict_hash
