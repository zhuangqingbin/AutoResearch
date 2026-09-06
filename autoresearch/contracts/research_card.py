#!/usr/bin/env python3
"""ResearchCard v1 的校验(工作包 D1/D2)—— 词表在 `agent_output`,这里只有形状检查。

设计:主设计 §8.1;08-31 稿 D8;Q-D 裁定(2026-09-07):① 词表单源 `agent_output`;② 对拍期
JSON 由解析桥从 md 派生,md 仍是权威;③ 字段取两稿并集。

三条不许含糊的边界:

1. **研究规则不进 schema 拒绝**。「持仓票盈利质量/偿付不得未核」是研究规则:整卡 ValueError
   会让持仓票变成 card_missing,E6/brief 就看不到持仓卡(08-26 曾因持仓卡缺席误判)。它在
   `card_lint_warnings` 里,由 self_review 作 warn 上报。
2. **三门只有 PASS/FAIL/UNKNOWN**。`gate_status(text)` 的 bool 是「失守」、`rubric_rating` 的
   bool 是「通过」,方向相反;转换必须在桥里显式做,不透传。
3. **主观概率只能叫 subjective**。给了数就三情景合计 1、每项 [0,1];没给就 not_provided。
   不存在「calibrated」——校准是 F7 离线实验的事,不是卡片自报的事。
"""
from __future__ import annotations

import math
import re
from datetime import date

from autoresearch.contracts.agent_output import (
    CARD_ORIGINS,
    CARD_SCHEMA_VERSION,
    CONFIDENCE_LEVELS,
    DIMENSION_LEVELS,
    EARLY_STOP_PHASES,
    GATE_STATES,
    OW_GATES,
    PROBABILITY_BASES,
    PROPOSALS,
    RATING_ORDER,
    RESEARCH_CARD_FIELDS,
    RUBRIC_DIMENSIONS,
    SCENARIO_NAMES,
    STOP_REASONS,
)

REQUIRED: frozenset[str] = frozenset(RESEARCH_CARD_FIELDS)
NESTED_FIELDS: dict[str, frozenset[str]] = {
    "theses": frozenset({"thesis_id", "statement", "observable", "source_refs",
                         "verification_date", "falsifier", "unknowns"}),
    "evidence_refs": frozenset({"claim_id", "source_observation_id", "blob_hash",
                                "rule_version", "support_verdict"}),
    "scenarios": frozenset({"name", "assumptions", "return_fraction", "probability", "invalidators"}),
}
_SHA256 = re.compile(r"[0-9a-f]{64}")
_CODE = re.compile(r"[0-9]{6}")
_TEXT_FIELDS = ("management", "summary", "target", "rr", "rating_deviation_reason")
_NULLABLE_TEXT_FIELDS = ("base_rate_row", "ev", "conviction", "as_of", "engine", "model")


def _number(value) -> bool:
    return type(value) in {int, float} and math.isfinite(value)


def validate_nested(card: dict) -> None:
    for group, required in NESTED_FIELDS.items():
        for row in card[group]:
            if not isinstance(row, dict) or set(row) != required:
                raise ValueError(f"invalid {group} shape")
    seen: set[str] = set()
    for thesis in card["theses"]:
        if thesis["thesis_id"] in seen:
            raise ValueError("duplicate thesis_id")
        seen.add(thesis["thesis_id"])
    for ref in card["evidence_refs"]:
        if ref["support_verdict"] not in GATE_STATES:
            raise ValueError("invalid support verdict")
        if not isinstance(ref["blob_hash"], str) or not _SHA256.fullmatch(ref["blob_hash"]):
            raise ValueError("invalid evidence hash")
    probs = [s["probability"] for s in card["scenarios"]]
    values = [s["return_fraction"] for s in card["scenarios"]]
    if any(v is not None and not _number(v) for v in values):
        raise ValueError("invalid return fraction")
    if any(p is not None for p in probs):
        if len(probs) != 3 or {s["name"] for s in card["scenarios"]} != set(SCENARIO_NAMES):
            raise ValueError("three named scenarios required")
        if any(not _number(p) or not 0 <= p <= 1 for p in probs):
            raise ValueError("invalid subjective probabilities")
        if abs(sum(probs) - 1) > 1e-9 or card["probability_basis"] != "subjective":
            raise ValueError("invalid probability total or basis")
    elif card["probability_basis"] != "not_provided":
        raise ValueError("missing probabilities require not_provided basis")


def validate_card(card: dict) -> dict:
    """形状 + 词表。返回原 dict,不改写、不补缺省、不做研究判断。"""
    if not isinstance(card, dict) or set(card) != REQUIRED:
        missing = sorted(REQUIRED - set(card or {}))
        extra = sorted(set(card or {}) - REQUIRED)
        raise ValueError(f"missing or unknown card fields (missing={missing}, unknown={extra})")
    if type(card["schema_version"]) is not int or card["schema_version"] != CARD_SCHEMA_VERSION:
        raise ValueError("unsupported research card schema")
    if card["card_origin"] not in CARD_ORIGINS:
        raise ValueError("unknown card origin")
    if not isinstance(card["code"], str) or not _CODE.fullmatch(card["code"]):
        raise ValueError("invalid code")
    date.fromisoformat(card["analysis_date"])
    if card["ruler"] != "gap_c1_o2":
        raise ValueError("unsupported lite ruler")
    if not isinstance(card["dimensions"], dict) or set(card["dimensions"]) != set(RUBRIC_DIMENSIONS):
        raise ValueError("six dimensions required")
    if any(v not in DIMENSION_LEVELS for v in card["dimensions"].values()):
        raise ValueError("invalid dimension")
    if not isinstance(card["gates"], dict) or set(card["gates"]) != set(OW_GATES):
        raise ValueError("three gates required")
    if any(v not in GATE_STATES for v in card["gates"].values()):
        raise ValueError("invalid gate state")
    if card["initial_rating"] not in RATING_ORDER or card["proposal"] not in PROPOSALS:
        raise ValueError("invalid rating or P4 proposal")
    if card["confidence"] is not None and card["confidence"] not in CONFIDENCE_LEVELS:
        raise ValueError("invalid confidence")
    if card["probability_basis"] not in PROBABILITY_BASES:
        raise ValueError("uncalibrated probability must not be labelled calibrated")
    early = card["early_stop"]
    if early is not None:
        if not isinstance(early, dict) or set(early) != {"phase", "reason"}:
            raise ValueError("invalid early-stop shape")
        if early["phase"] not in EARLY_STOP_PHASES or early["reason"] not in STOP_REASONS:
            raise ValueError("invalid early stop")
    if type(card["holding"]) is not bool:
        raise ValueError("holding must be boolean")
    for field in ("theses", "evidence_refs", "scenarios", "entry_veto", "exec_lines", "tripwires"):
        if not isinstance(card[field], list):
            raise ValueError(f"{field} must be a list")
    if any(not isinstance(x, str) for x in card["entry_veto"] + card["exec_lines"]):
        raise ValueError("entry_veto / exec_lines must be text lists")
    if any(not isinstance(x, dict) or "kind" not in x for x in card["tripwires"]):
        raise ValueError("tripwires must be structured rows with a kind")
    for field in _TEXT_FIELDS:
        if not isinstance(card[field], str):
            raise ValueError(f"{field} must be text")
    for field in _NULLABLE_TEXT_FIELDS:
        if card[field] is not None and not isinstance(card[field], str):
            raise ValueError(f"{field} must be text or null")
    validate_nested(card)
    return card


def card_lint_warnings(card: dict) -> list[str]:
    """研究规则告警(不拒卡)。"""
    warnings = []
    if card["holding"] and any(card["dimensions"][d] == "未核" for d in ("盈利质量", "偿付")):
        warnings.append("PINNED_QUALITY_UNREVIEWED")
    if card["gates"] and all(v == "UNKNOWN" for v in card["gates"].values()) and card["early_stop"] is None:
        warnings.append("FULL_CARD_WITHOUT_GATE_MARKS")
    return warnings


def json_schema() -> dict:
    """JSON Schema(两引擎交集:全 required + additionalProperties:false)。供 emit / Codex
    `--output-schema` 消费(D5);这里只生成,不落盘。"""
    enum = lambda values: {"enum": list(values)}  # noqa: E731
    nullable_text = {"type": ["string", "null"]}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "ResearchCard", "type": "object", "additionalProperties": False,
        "required": list(RESEARCH_CARD_FIELDS),
        "properties": {
            "schema_version": {"const": CARD_SCHEMA_VERSION},
            "card_origin": enum(CARD_ORIGINS), "code": {"type": "string", "pattern": "^[0-9]{6}$"},
            "analysis_date": {"type": "string"}, "ruler": {"const": "gap_c1_o2"},
            "dimensions": {"type": "object", "additionalProperties": False,
                           "required": list(RUBRIC_DIMENSIONS),
                           "properties": {d: enum(DIMENSION_LEVELS) for d in RUBRIC_DIMENSIONS}},
            "gates": {"type": "object", "additionalProperties": False, "required": list(OW_GATES),
                      "properties": {g: enum(GATE_STATES) for g in OW_GATES}},
            "early_stop": {"type": ["object", "null"], "additionalProperties": False,
                           "required": ["phase", "reason"],
                           "properties": {"phase": enum(EARLY_STOP_PHASES), "reason": enum(STOP_REASONS)}},
            "theses": {"type": "array"}, "evidence_refs": {"type": "array"},
            "initial_rating": enum(RATING_ORDER), "proposal": enum(PROPOSALS),
            "rating_deviation_reason": {"type": "string"},
            "confidence": {"type": ["string", "null"], "enum": [*CONFIDENCE_LEVELS, None]},
            "scenarios": {"type": "array"}, "probability_basis": enum(PROBABILITY_BASES),
            "holding": {"type": "boolean"}, "management": {"type": "string"},
            "target": {"type": "string"}, "rr": {"type": "string"}, "summary": {"type": "string"},
            "entry_veto": {"type": "array", "items": {"type": "string"}},
            "exec_lines": {"type": "array", "items": {"type": "string"}},
            "tripwires": {"type": "array", "items": {"type": "object"}},
            "base_rate_row": nullable_text, "ev": nullable_text, "conviction": nullable_text,
            "as_of": nullable_text, "engine": nullable_text, "model": nullable_text,
        },
    }
