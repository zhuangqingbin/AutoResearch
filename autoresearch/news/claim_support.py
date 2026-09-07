#!/usr/bin/env python3
"""保守字段比较器(工作包 B3)—— 纯函数,无网络、无模型、无 IO。

实施计划:`docs/superpowers/plans/2026-09-06-claim-evidence-verification.md` Task B3。

判定规则(主设计 §6.3),三句话:

1. **身份先对上**(主体 / event_id / 谓语三项都 PASS),否则所有语义字段一律 UNKNOWN ——
   另一个事件的字段拿来比,比出来的 FAIL 是**错杀**。
2. 语义字段逐个比:断言没声称的可选字段(claim 侧 None)记 PASS,不替它制造新主张;来源
   侧未知(None / unknown / uncertain)记 UNKNOWN;时间不一致记 UNKNOWN(不同精度或时段
   交叠要单独复核,**不许**用「±3 天」自动放宽成 PASS);其余不等才是 FAIL。
3. 汇总:任一 FAIL → FAIL;全部 PASS → PASS;其余 UNKNOWN。

`checked_fields` 不是 LLM 自报的可信度:只能由来源结构化字段的白名单映射,或带 reviewer
身份和原文 hash 的人工复核记录提供。标题词表和未复核的 session_extraction 默认一个字段都不
提供 —— 那时比较结果全是 UNKNOWN,这是**正确**的:没核过就是不知道。
"""
from __future__ import annotations

from decimal import Decimal
from hashlib import sha256

from autoresearch.contracts.claim_evidence import (
    IDENTITY_FIELDS,
    RULE_VERSION,
    SEMANTIC_FIELDS,
    validate_event,
)

_UNKNOWN_SOURCE_VALUES = frozenset({"unknown", "uncertain"})


def quote_matches(text: str, span: dict) -> bool:
    """引用可重定位且防篡改:偏移落在文内、hash 是全文 sha256、切片逐字相等。三者缺一即假。"""
    start, end = span.get("start"), span.get("end")
    return (
        type(start) is int and type(end) is int and 0 <= start < end <= len(text)
        and sha256(text.encode("utf-8")).hexdigest() == span.get("blob_hash")
        and text[start:end] == span.get("text")
    )


def compare_events(claim: dict, source: dict, *, checked_fields) -> dict:
    """claim event × source event → `{verdict, fields, rule_version}`,逐字段给理由。"""
    validate_event(claim)
    validate_event(source)
    checked = set(checked_fields)
    fields: dict[str, str] = {}
    for key in IDENTITY_FIELDS:
        fields[key] = "PASS" if (key in checked and claim[key] == source[key]) else "UNKNOWN"
    identity_ok = all(value == "PASS" for value in fields.values())
    for key in SEMANTIC_FIELDS:
        left, right = claim[key], source[key]
        if not identity_ok or key not in checked:
            fields[key] = "UNKNOWN"
        elif left is None:
            fields[key] = "PASS"          # 断言没声称此可选字段,不制造新主张
        elif right is None or (isinstance(right, str) and right in _UNKNOWN_SOURCE_VALUES):
            fields[key] = "UNKNOWN"
        elif key == "effective_at":
            # 时间是 dict(区间+精度):不同精度或时段交叠需单独复核,不许「±3 天」自动放宽
            fields[key] = "PASS" if left == right else "UNKNOWN"
        elif key == "amount_value":
            comparators = ("amount_unit", "amount_basis", "effective_at")
            comparable = all(
                name in checked and claim[name] is not None and claim[name] == source[name]
                for name in comparators)
            fields[key] = ("UNKNOWN" if not comparable
                           else "PASS" if Decimal(left) == Decimal(right) else "FAIL")
        else:
            fields[key] = "PASS" if left == right else "FAIL"
    verdict = ("FAIL" if "FAIL" in fields.values()
               else "PASS" if all(v == "PASS" for v in fields.values())
               else "UNKNOWN")
    return {"verdict": verdict, "fields": fields, "rule_version": RULE_VERSION}


def merge_sources(results: list[dict]) -> dict:
    """同一事件多份有效材料 → 一个结论。互相冲突输出 `source_conflict` + UNKNOWN,不任意选第一份。"""
    if not results:
        return {"verdict": "UNKNOWN", "reason": "NO_SOURCE", "rule_version": RULE_VERSION}
    verdicts = {r["verdict"] for r in results}
    if "PASS" in verdicts and "FAIL" in verdicts:
        return {"verdict": "UNKNOWN", "reason": "source_conflict", "rule_version": RULE_VERSION,
                "n_sources": len(results)}
    if "FAIL" in verdicts:
        return {"verdict": "FAIL", "reason": "REFUTED_BY_SOURCE", "rule_version": RULE_VERSION,
                "n_sources": len(results)}
    if verdicts == {"PASS"}:
        return {"verdict": "PASS", "reason": "ALL_SOURCES_AGREE", "rule_version": RULE_VERSION,
                "n_sources": len(results)}
    return {"verdict": "UNKNOWN", "reason": "INSUFFICIENT", "rule_version": RULE_VERSION,
            "n_sources": len(results)}
