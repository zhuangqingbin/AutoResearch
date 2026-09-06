#!/usr/bin/env python3
"""regex_v1:把情报稿里关于**本票**的一行文字抽成 ClaimEvidence v2 的 event(工作包 B4,Q-B ③)。

这是**抽取**,不是**核实**:抽出来的 event 只说明「这行文字在断言什么」,支持与否要等
`claim_binding.support_bound_claim` 拿到已绑定的来源才谈得上。所以本模块产出的证据包一律
`extraction_origin="regex_v1"`、`verification_basis="none"`。

规则刻意保守、刻意可读——每条都是词表匹配,没有一处「猜」:

- 谓语:首个命中的 回购 / 增持 / 减持 / 中标(首批四个)。
- 生命周期:终止/取消/撤销 → terminated;完成/实施完毕/已实施 → completed;
  实施中/正在/进行中 → in_progress;拟/计划/预案/将 → plan;其余 unknown。
- 极性:含 否认/不实/未 → negated;含 传闻/据悉/或将/可能 → uncertain;其余 affirmed。
- 断言类型:预计/预期 → forecast;若/如果/前提 → conditional;表示/称/据 → quotation;其余 actual。
- 金额:`数字 + (亿元|万元|元|亿股|万股|股|%)`,换算成 decimal string;**口径**由生命周期定
  (plan → planned_cap,completed/in_progress → executed_total,中标 → contract_total),定不出
  的一律**丢掉金额**并记 `amount_dropped`——没有口径的金额进比较器只会制造假 FAIL。
- 生效时间:行内第一个日期(YYYY-MM-DD 或 YYYY年M月D日)→ 当日整天(precision=day);没有 → None。

词表不够时抽不出来是**正确**结果(返回 None),不是要把词表放宽到能抽出来为止。
"""
from __future__ import annotations

import hashlib
import re
from decimal import Decimal

from autoresearch.contracts.claim_evidence import (
    ENUMS,
    RULE_VERSION,
    SCHEMA_VERSION,
    validate_event,
)

PREDICATES: tuple[str, ...] = ("回购", "增持", "减持", "中标")
_LIFECYCLE = (
    ("terminated", ("终止", "取消", "撤销")),
    ("completed", ("实施完毕", "已完成", "完成", "已实施", "累计")),
    ("in_progress", ("实施中", "正在", "进行中", "首次")),
    ("plan", ("拟", "计划", "预案", "将")),
)
_NEGATED = ("否认", "不实", "未实施", "尚未", "未")
_UNCERTAIN = ("传闻", "据悉", "或将", "可能", "市场消息")
_FORECAST = ("预计", "预期")
_CONDITIONAL = ("若", "如果", "前提")
_QUOTATION = ("表示", "称", "据")
_AMOUNT = re.compile(r"(\d+(?:\.\d+)?)\s*(亿元|万元|元|亿股|万股|股|%)")
_DATE_ISO = re.compile(r"(20\d{2})-(\d{2})-(\d{2})")
_DATE_CN = re.compile(r"(20\d{2})年(\d{1,2})月(\d{1,2})日")
_SCALE = {"亿": Decimal("100000000"), "万": Decimal("10000")}


def _first(words, line):
    return next((w for w in words if w in line), None)


def _lifecycle(line: str) -> str:
    for name, words in _LIFECYCLE:
        if _first(words, line):
            return name
    return "unknown"


def _amount(line: str, *, lifecycle: str, predicate: str):
    m = _AMOUNT.search(line)
    if not m:
        return None, None, None, None
    number, unit = m.group(1), m.group(2)
    value = Decimal(number)
    if unit.endswith("元"):
        kind = "CNY"
    elif unit.endswith("股"):
        kind = "shares"
    else:
        kind = "percent"
    for prefix, scale in _SCALE.items():
        if unit.startswith(prefix):
            value *= scale
    if predicate == "中标":
        basis = "contract_total"
    elif lifecycle == "plan":
        basis = "planned_cap"
    elif lifecycle in ("completed", "in_progress"):
        basis = "executed_total"
    else:
        return None, None, None, f"amount_dropped:no_basis_for_lifecycle={lifecycle}"
    return format(value.normalize(), "f"), kind, basis, None


def _effective_at(line: str):
    m = _DATE_ISO.search(line) or _DATE_CN.search(line)
    if not m:
        return None, None
    y, mo, d = (int(g) for g in m.groups())
    start = f"{y:04d}-{mo:02d}-{d:02d}T00:00:00+08:00"
    # 次日 00:00:用 date 算,别手写月末
    from datetime import date, timedelta
    nxt = date(y, mo, d) + timedelta(days=1)
    end = f"{nxt.isoformat()}T00:00:00+08:00"
    return {"start": start, "end": end, "precision": "day"}, f"{y:04d}-{mo:02d}-{d:02d}"


def extract_event(line: str, *, subject_code: str) -> dict | None:
    """一行 → `{"event": …, "notes": [...]}`;没有谓语词 → None。"""
    predicate = _first(PREDICATES, line)
    if predicate is None:
        return None
    lifecycle = _lifecycle(line)
    if _first(_NEGATED, line):
        polarity = "negated"
    elif _first(_UNCERTAIN, line):
        polarity = "uncertain"
    else:
        polarity = "affirmed"
    if _first(_FORECAST, line):
        kind = "forecast"
    elif _first(_CONDITIONAL, line):
        kind = "conditional"
    elif _first(_QUOTATION, line):
        kind = "quotation"
    else:
        kind = "actual"
    amount_value, amount_unit, amount_basis, dropped = _amount(
        line, lifecycle=lifecycle, predicate=predicate)
    effective_at, day = _effective_at(line)
    ident = f"{subject_code}|{predicate}|{day or 'nodate'}|{amount_value or ''}|{lifecycle}"
    event = {
        "subject_code": str(subject_code).zfill(6),
        "event_id": f"{predicate}-{day or 'nodate'}-" + hashlib.sha256(ident.encode()).hexdigest()[:8],
        "predicate": predicate, "lifecycle": lifecycle, "assertion_kind": kind,
        "polarity": polarity, "amount_value": amount_value, "amount_unit": amount_unit,
        "amount_basis": amount_basis, "effective_at": effective_at,
    }
    validate_event(event)
    notes = [dropped] if dropped else []
    return {"event": event, "notes": notes}


def bundle_from_line(line: str, *, subject_code: str, claim_id: str) -> dict | None:
    """一行 → 未绑定的证据包(引用为空;绑定是别人的事)。"""
    got = extract_event(line, subject_code=subject_code)
    if got is None:
        return None
    return {
        "schema_version": SCHEMA_VERSION, "claim_id": claim_id, "event": got["event"],
        "source_observation_ids": [], "quote_spans": [],
        "extraction_origin": "regex_v1", "verification_basis": "none",
        "rule_version": RULE_VERSION,
    }


assert set(PREDICATES) == ENUMS["predicate"], "抽取器谓语集必须与契约枚举同源"
