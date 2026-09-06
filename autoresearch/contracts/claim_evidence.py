#!/usr/bin/env python3
"""ClaimEvidence v2:重大事件断言的**字段级**证据契约(工作包 B2)—— 纯声明,不导入上层。

设计:`docs/superpowers/specs/2026-09-06-research-reliability-and-system-evolution-design.md`
§6.2;实施计划 `docs/superpowers/plans/2026-09-06-claim-evidence-verification.md` Task B2。

为什么要字段级:「关键词出现」与「证据支持断言」是两件事。「公司公告终止回购计划」里有
「回购」两个字,但它**反驳**「已完成回购」;「拟以不超过 10 亿元回购」是计划上限,不是已执行
金额。一条断言要被支持,主体、事件、生命周期、金额口径、生效时间得**逐个**对上 —— 对不上的
是 UNKNOWN,不是 FAIL;只有同一已知事件的明确反证才是 FAIL。

谓语首批四个:回购 / 增持 / 减持 / 中标。`claim_ledger._PREDICATE_WORDS` 本有 19 个受控词,
「增持」是 A 股最常被拿来拉抬的正面断言,首批不能缺席;其余(立案/问询/重组/涨停…)等 B5
读数后按类扩,不一次全开。

金额一律 decimal string(`"1000000000"`),并带 `amount_unit`(CNY / shares / percent)与
`amount_basis`(计划上限 / 已执行累计 / 合同总额)。**股份数、百分比、金额互不比较**;
计划上限与已执行金额相等也不等于「已完成」——那是两个不同的 basis。
"""
from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

RULE_VERSION = "claim_support.v2"
SCHEMA_VERSION = 2

IDENTITY_FIELDS: tuple[str, ...] = ("subject_code", "event_id", "predicate")
SEMANTIC_FIELDS: tuple[str, ...] = (
    "lifecycle", "assertion_kind", "polarity", "amount_value",
    "amount_unit", "amount_basis", "effective_at",
)
ENUMS: dict[str, frozenset] = {
    "predicate": frozenset({"回购", "增持", "减持", "中标"}),
    "lifecycle": frozenset({"plan", "in_progress", "completed", "terminated", "unknown"}),
    "assertion_kind": frozenset({"actual", "forecast", "conditional", "quotation"}),
    "polarity": frozenset({"affirmed", "negated", "uncertain"}),
    "amount_unit": frozenset({"CNY", "shares", "percent", None}),
    "amount_basis": frozenset({"planned_cap", "executed_total", "contract_total", "unknown", None}),
}
TIME_PRECISIONS: frozenset[str] = frozenset({"second", "minute", "day", "range"})
VERDICTS: tuple[str, ...] = ("PASS", "FAIL", "UNKNOWN")

#: 外层证据包(主设计 §6.2)。`event` 是上面那个对象;其余是引用身份。
BUNDLE_FIELDS: tuple[str, ...] = (
    "schema_version", "claim_id", "event", "source_observation_ids", "quote_spans",
    "extraction_origin", "verification_basis", "rule_version",
)
EXTRACTION_ORIGINS: frozenset[str] = frozenset({"regex_v1", "session_extraction", "human_review"})
VERIFICATION_BASES: frozenset[str] = frozenset({"structured_source", "human_review", "none"})

_CODE = re.compile(r"[0-9]{6}")


def validate_effective_time(value) -> None:
    """有效时间 = 左闭右开区间 + 精度。naive 时间、end ≤ start、非法精度一律拒。

    精度不够时不能假装精确到秒:公告只给「某日」的,`precision="day"`、区间就是那一整天。
    """
    if value is None:
        return
    if not isinstance(value, dict) or set(value) != {"start", "end", "precision"}:
        raise ValueError("invalid effective time")
    try:
        start, end = (datetime.fromisoformat(str(value[k])) for k in ("start", "end"))
    except ValueError as exc:
        raise ValueError("invalid effective time") from exc
    if start.tzinfo is None or end.tzinfo is None or end <= start:
        raise ValueError("invalid effective interval")
    if value["precision"] not in TIME_PRECISIONS:
        raise ValueError("invalid time precision")


def validate_event(event: dict) -> dict:
    """event 对象的形状校验。返回原 dict,不改写、不补缺省。"""
    if not isinstance(event, dict):
        raise ValueError("event must be a mapping")
    required = set(IDENTITY_FIELDS + SEMANTIC_FIELDS)
    if required - event.keys():
        raise ValueError(f"missing event fields: {sorted(required - event.keys())}")
    if event.keys() - required:
        raise ValueError(f"unknown event fields: {sorted(event.keys() - required)}")
    if not _CODE.fullmatch(str(event["subject_code"])):
        raise ValueError("invalid subject code")
    if not isinstance(event["event_id"], str) or not event["event_id"]:
        raise ValueError("event identity required")
    for key, allowed in ENUMS.items():
        if event[key] not in allowed:
            raise ValueError(f"invalid {key}")
    value = event["amount_value"]
    if value is not None:
        if not isinstance(value, str):
            raise ValueError("amount must be a decimal string")
        try:
            amount = Decimal(value)
        except InvalidOperation as exc:
            raise ValueError("invalid amount") from exc
        if not amount.is_finite() or amount < 0:
            raise ValueError("invalid amount")
        if event["amount_unit"] is None or event["amount_basis"] in {None, "unknown"}:
            raise ValueError("amount requires unit and basis")
    validate_effective_time(event["effective_at"])
    return event


def validate_bundle(bundle: dict) -> dict:
    """外层证据包:引用身份必须成形,`event` 走 `validate_event`。"""
    if not isinstance(bundle, dict) or set(bundle) != set(BUNDLE_FIELDS):
        raise ValueError("invalid evidence bundle shape")
    if bundle["schema_version"] != SCHEMA_VERSION:
        raise ValueError("unsupported evidence bundle schema")
    if not isinstance(bundle["claim_id"], str) or not bundle["claim_id"]:
        raise ValueError("claim_id required")
    ids = bundle["source_observation_ids"]
    if not isinstance(ids, list) or any(not isinstance(i, str) or not i for i in ids):
        raise ValueError("source_observation_ids must be a list of ids")
    spans = bundle["quote_spans"]
    if not isinstance(spans, list):
        raise ValueError("quote_spans must be a list")
    for span in spans:
        if not isinstance(span, dict) or set(span) != {
                "source_observation_id", "start", "end", "text", "blob_hash"}:
            raise ValueError("invalid quote span shape")
    if bundle["extraction_origin"] not in EXTRACTION_ORIGINS:
        raise ValueError("invalid extraction_origin")
    if bundle["verification_basis"] not in VERIFICATION_BASES:
        raise ValueError("invalid verification_basis")
    if not isinstance(bundle["rule_version"], str) or not bundle["rule_version"]:
        raise ValueError("rule_version required")
    validate_event(bundle["event"])
    return bundle
