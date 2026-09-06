#!/usr/bin/env python3
"""引用绑定适配器(工作包 B4):原断言 → 观测 → blob → 经复核字段,唯一接入点。

实施计划:`docs/superpowers/plans/2026-09-06-claim-evidence-verification.md` Task B4。

`support_bound_claim` 只做四件事,按顺序,任一步不成立就停在那一步的原因码上:

1. 证据包引用的观测**每一个**都在 catalog 里、都有原文 —— 否则 `SOURCE_NOT_BOUND`;
2. 每个观测的 `available_at` 都不晚于 `decision_at` —— 否则 `NOT_AVAILABLE_AT_DECISION`
   (`decision_at` 由调用方从 run 的时间锚派生;非 ACTIONABLE 的 run 传 None,这里直接
   `RUN_NOT_ACTIONABLE`,不猜);
3. 每条引用的定位都能在原文里重定位且 hash 未变 —— 否则 `QUOTE_NOT_VERIFIED`;
4. 才把 claim event 与 bundle 里的来源 event 交给 `compare_events`。

`trusted_fields` 不是 LLM 自报的可信度:只能来自来源结构化字段的白名单映射,或带 reviewer
身份和原文 hash 的人工复核记录。传空集合是**合法**的——那时结论是 UNKNOWN,不是 PASS。
"""
from __future__ import annotations

from datetime import datetime

from autoresearch.contracts.claim_evidence import RULE_VERSION, validate_bundle, validate_event
from autoresearch.news.claim_support import compare_events, quote_matches


def _aware(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("timestamps must be timezone-aware")
        return value
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return parsed


def support_bound_claim(claim_event: dict, bundle: dict, *, observations: dict, texts: dict,
                        trusted_fields, decision_at) -> dict:
    validate_event(claim_event)
    validate_bundle(bundle)
    if bundle["event"]["subject_code"] != claim_event["subject_code"]:
        raise ValueError("bundle subject does not match the claim being checked")
    ids = bundle["source_observation_ids"]
    if not ids or any(i not in observations or i not in texts for i in ids):
        return {"verdict": "UNKNOWN", "reason": "SOURCE_NOT_BOUND", "rule_version": RULE_VERSION}
    cutoff = _aware(decision_at)
    if cutoff is None:
        return {"verdict": "UNKNOWN", "reason": "RUN_NOT_ACTIONABLE", "rule_version": RULE_VERSION}
    for i in ids:
        seen = _aware(observations[i].get("available_at"))
        if seen is None or seen > cutoff:
            return {"verdict": "UNKNOWN", "reason": "NOT_AVAILABLE_AT_DECISION",
                    "rule_version": RULE_VERSION}
    spans = bundle["quote_spans"]
    if not spans or any(
        s["source_observation_id"] not in ids
        or not quote_matches(texts[s["source_observation_id"]], s)
        for s in spans
    ):
        return {"verdict": "UNKNOWN", "reason": "QUOTE_NOT_VERIFIED", "rule_version": RULE_VERSION}
    result = compare_events(claim_event, bundle["event"], checked_fields=set(trusted_fields))
    return {**result, "reason": "COMPARED"}
