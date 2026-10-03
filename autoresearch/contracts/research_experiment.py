#!/usr/bin/env python3
"""F1:离线研究实验的方案契约 —— 结果之前先把话说死。

实施计划:`docs/superpowers/plans/2026-09-06-research-methods-and-stage-value.md` Task F1。

为什么要一份**冻结**的 spec:大量试验后挑最好看的那个回测,是选择偏差最经典的形态。它躲不过
「事后看起来很有道理」的自我说服,只能靠「先写后看」这个物理动作挡——写完落盘、排他创建、
改假设就换新 experiment_id。

三条字段级纪律:

1. **尺子**:`ruler` 只能是主尺 `gap_c1_o2`;`sensitivity_rulers` 是**白名单闭集**,可空。
   敏感尺(fwd_5/fwd_10/相对市场/相对行业)与主尺**并列输出**,永远不给决策类结论背书
   —— 但也不能不许它们出现:只用主尺,阶段价值实验只会第三次推出「判断层隔夜负」这个
   已知结论(08-21 已证低位转强在周级尺翻正)。
2. **加权**:`weighting` 恒 `day_equal`。工作包 A 刚把区间口径统一成日等权;阶段比较换回
   行等权,两边读数就不可比了。
3. **停止规则**:`stop_rule` 必须在冻结时写死样本终点/成熟政策。收益好看时提前停下、还管
   自己叫「固定方案」,是最省力也最不可证伪的作弊。

`MAIN_RULER` / `SENSITIVITY_RULERS` 是**抄来的字面量**:contracts 在最底层,不能 import
`common.ruler` 之上的任何东西——实际上 `common` 也在上层。不漂移由
`tests/research/test_experiment_io.py::test_ruler_literals_do_not_drift` 钉死。
"""
from __future__ import annotations

from datetime import date

#: 主尺(2026-08-05 用户裁定):T+1 收盘买 → T+2 开盘卖。
MAIN_RULER = "gap_c1_o2"
#: 敏感尺白名单。只观察、只并列报告,**永不进 BUY**。
SENSITIVITY_RULERS: frozenset[str] = frozenset(
    {"fwd_5_oc", "fwd_10_oc", "rel_gap_market", "rel_gap_sector"})

SCHEMA_VERSION = 1
REQUIRED_SPEC: frozenset[str] = frozenset({
    "schema_version", "experiment_id", "engine", "created_at", "code_sha",
    "prompt_hashes", "input_manifest_hash", "experiment_family", "hypotheses",
    "population_rule", "selection_rule", "ruler", "sensitivity_rulers", "return_unit",
    "baseline", "evidence_mode", "weighting", "cost_model_version", "split",
    "purge_rule", "embargo_sessions", "bootstrap", "multiplicity",
    "maturity_policy", "quality_constraints", "stop_rule",
})
#: 每条假设都要能被**否掉**:少一项,这条假设就不是可证伪的主张,只是一段愿望。
REQUIRED_HYPOTHESIS: frozenset[str] = frozenset({
    "hypothesis_id", "mechanism", "expected_direction", "available_at",
    "metric_definition", "population", "label", "rejection_condition",
})
DIRECTIONS: frozenset[str] = frozenset({"positive", "negative", "two_sided"})


def validate_hypothesis(value: dict) -> dict:
    if not isinstance(value, dict) or set(value) != REQUIRED_HYPOTHESIS:
        raise ValueError(f"hypothesis must carry exactly {sorted(REQUIRED_HYPOTHESIS)}")
    if value["expected_direction"] not in DIRECTIONS:
        raise ValueError(f"invalid expected_direction: {value['expected_direction']!r}")
    for field in ("hypothesis_id", "mechanism", "metric_definition", "rejection_condition"):
        if not isinstance(value[field], str) or not value[field].strip():
            raise ValueError(f"hypothesis field must be non-empty text: {field}")
    return value


def validate_spec(value: dict) -> dict:
    """方案字段的形状校验。返回原 dict —— 不补缺省、不改写、不排序。"""
    if not isinstance(value, dict) or set(value) != REQUIRED_SPEC:
        missing = sorted(REQUIRED_SPEC - set(value or {}))
        extra = sorted(set(value or {}) - REQUIRED_SPEC)
        raise ValueError(f"invalid experiment spec (missing={missing}, unknown={extra})")
    if value["schema_version"] != SCHEMA_VERSION:
        raise ValueError("unsupported experiment schema")
    if value["ruler"] != MAIN_RULER or value["return_unit"] != "fraction":
        raise ValueError("wrong overnight label or unit")
    sensitivity = value["sensitivity_rulers"]
    if not isinstance(sensitivity, list) or not set(sensitivity) <= SENSITIVITY_RULERS:
        raise ValueError("unregistered sensitivity ruler")
    if len(set(sensitivity)) != len(sensitivity):
        raise ValueError("duplicate sensitivity ruler")
    if value["weighting"] != "day_equal":
        raise ValueError("stage comparison requires declared day-equal weighting")
    if not value["hypotheses"] or not value["experiment_family"]:
        raise ValueError("hypothesis family must be registered")
    if not isinstance(value["hypotheses"], list):
        raise ValueError("hypotheses must be a list")
    seen: set[str] = set()
    for hypothesis in value["hypotheses"]:
        validate_hypothesis(hypothesis)
        if hypothesis["hypothesis_id"] in seen:
            raise ValueError(f"duplicate hypothesis_id: {hypothesis['hypothesis_id']}")
        seen.add(hypothesis["hypothesis_id"])
    if type(value["embargo_sessions"]) is not int or value["embargo_sessions"] < 0:
        raise ValueError("invalid embargo")
    split = value["split"]
    if not isinstance(split, dict) or set(split) != {"train", "validation", "test"}:
        raise ValueError("split must name train/validation/test date ranges")
    for name, window in split.items():
        if not isinstance(window, list) or len(window) != 2:
            raise ValueError(f"split {name} must be an ordered [start, end) pair")
        try:
            if any(not isinstance(day, str) or date.fromisoformat(day).isoformat() != day for day in window):
                raise ValueError("noncanonical date")
        except (ValueError, TypeError) as exc:
            raise ValueError(f"split {name} requires valid ISO dates") from exc
        if window[0] >= window[1]:
            raise ValueError(f"split {name} must be an ordered [start, end) pair")
    if split['train'][1] > split['validation'][0] or split['validation'][1] > split['test'][0]:
        raise ValueError("split windows overlap or are out of order")
    if not isinstance(value["stop_rule"], str) or not value["stop_rule"].strip():
        raise ValueError("stop_rule must be fixed before results are seen")
    return value
