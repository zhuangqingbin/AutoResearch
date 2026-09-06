#!/usr/bin/env python3
"""执行评价的字段契约(工作包 C1)—— 纯声明,不导入任何上层。

设计:`docs/superpowers/specs/2026-09-06-research-reliability-and-system-evolution-design.md`
§7.3;实施计划 `docs/superpowers/plans/2026-09-06-execution-evaluation.md` Task C1。

两条贯穿全包的纪律,写在这里是因为它们是**字段级**的:

1. **「字段缺席」≠「字段为未知」**。每个声明过的字段都必须出现;来源没有的值写 `null`。
   前者是导入器漏了、后者是来源没有——把前者当后者,缺失就静默消失了(与
   `data/contracts.py` 的「降级必须留痕」同一条律)。
2. **价格与数量一律 decimal string**。`10.1` 存成 float 是 10.099999999999999645…;
   在金额口径上这是不可接受的静默失真,而它恰好在小额上看不出来。

`ACTIONABLE` / `EXEC_DECISION_CUTOFF_HHMM` 是从 `scan.exec_anchor` **抄来的字面量**:
contracts 在最底层,不能 import scan(那是向上的边)。字面量不漂移由
`tests/contracts/test_execution_contract.py::test_actionability_literal_does_not_drift_from_exec_anchor`
钉死——靠测试,不靠 import。
"""
from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

SNAPSHOT_SCHEMA_VERSION = 1

#: 14:45 运营截止(人读完 brief 还要下单,得留缓冲);交易所收盘集合竞价 14:57 另计。
EXEC_DECISION_CUTOFF_HHMM = "14:45"
#: 只有这个状态的 run 才进分母 —— 其余(迟到/过期/未批准)只进覆盖表。
ACTIONABLE = "ACTIONABLE"

SNAPSHOT_FIELDS: tuple[str, ...] = (
    "schema_version", "snapshot_id", "engine", "run_id", "code", "venue",
    "session_date", "decision_at", "market_event_at", "provider_published_at",
    "received_at", "persisted_at", "timezone", "last", "previous_close",
    "high_so_far", "low_so_far", "volume_so_far", "amount_so_far", "suspended",
    "limit_up_price", "limit_down_price", "price_adjustment_basis",
    "source_observation_id", "payload_hash", "timestamp_precision", "quality_flags",
)
#: 这些不允许 `null` —— 不知道自己是谁的快照,后面每一步都无从对账。
SNAPSHOT_IDENTITY_FIELDS: frozenset[str] = frozenset(
    {"schema_version", "snapshot_id", "engine", "run_id", "code", "payload_hash"})
TIME_FIELDS: tuple[str, ...] = (
    "decision_at", "market_event_at", "provider_published_at", "received_at", "persisted_at",
)
DECIMAL_FIELDS: tuple[str, ...] = (
    "last", "previous_close", "high_so_far", "low_so_far", "volume_so_far",
    "amount_so_far", "limit_up_price", "limit_down_price",
)

EVIDENCE_MODES: frozenset[str] = frozenset({"EOD_PROXY", "SNAPSHOT_SIMULATED", "OBSERVED_FILL"})
ENTRY_STATES: frozenset[str] = frozenset(
    {"UNKNOWN", "NOT_SUBMITTED", "NO_FILL", "PARTIAL_FILL", "FILLED", "CANCELLED"})
EXIT_STATES: frozenset[str] = frozenset(
    {"NOT_DUE", "UNKNOWN", "NO_FILL", "PARTIAL_FILL", "FILLED"})

#: `snapshot_visibility` 的闭集。`UNKNOWN` 是「时点没给全」,不是「不可得」。
VISIBILITY_STATES: frozenset[str] = frozenset(
    {"AVAILABLE", "NOT_OBSERVED_AT_DECISION", "FUTURE_INFORMATION",
     "INVALID_TIME_ORDER", "UNKNOWN"})
TIMESTAMP_PRECISIONS: frozenset[str] = frozenset({"second", "minute", "day", "range"})

_CODE = re.compile(r"[0-9]{6}")
_SHA256 = re.compile(r"[0-9a-f]{64}")


def parse_aware(value: str | None) -> datetime | None:
    """ISO8601 → 带时区 datetime;`None` 原样返回。naive(无偏移)一律拒绝。

    没有偏移的时间戳在跨时区对账里是**未定义**的,不是「按本地时区解释」——猜一次就够把
    14:45 的截止判成 15:00 之后。
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("timestamp must be an ISO8601 string")
    try:
        result = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"invalid timestamp: {value!r}") from exc
    if result.tzinfo is None:
        raise ValueError(f"timezone required: {value!r}")
    return result


def parse_amount(value: str | None, *, field: str = "amount") -> Decimal | None:
    """decimal string → `Decimal`;`None` 原样返回。float/int、NaN、Infinity、负数全拒。"""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a decimal string, not {type(value).__name__}")
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"invalid {field}: {value!r}") from exc
    if not amount.is_finite() or amount < 0:
        raise ValueError(f"invalid {field}: {value!r}")
    return amount


def validate_snapshot(row: dict) -> dict:
    """字段齐全性 + 类型 + 身份非空。返回原 dict(不复制、不改写、不补缺省)。"""
    declared = set(SNAPSHOT_FIELDS)
    missing = declared - row.keys()
    if missing:
        raise ValueError(f"missing snapshot fields: {sorted(missing)}")
    extra = row.keys() - declared
    if extra:
        raise ValueError(f"unknown snapshot fields: {sorted(extra)}")
    for field in SNAPSHOT_IDENTITY_FIELDS:
        if row[field] is None:
            raise ValueError(f"identity field must not be null: {field}")
    if row["schema_version"] != SNAPSHOT_SCHEMA_VERSION:
        raise ValueError("unsupported snapshot schema")
    if not isinstance(row["code"], str) or not _CODE.fullmatch(row["code"]):
        raise ValueError(f"invalid code: {row['code']!r}")
    if not isinstance(row["payload_hash"], str) or not _SHA256.fullmatch(row["payload_hash"]):
        raise ValueError("payload_hash must be sha256 hex")
    for field in TIME_FIELDS:
        parse_aware(row[field])
    for field in DECIMAL_FIELDS:
        parse_amount(row[field], field=field)
    if row["suspended"] is not None and type(row["suspended"]) is not bool:
        raise ValueError("suspended must be boolean or null")
    if row["timestamp_precision"] is not None \
            and row["timestamp_precision"] not in TIMESTAMP_PRECISIONS:
        raise ValueError(f"invalid timestamp precision: {row['timestamp_precision']!r}")
    flags = row["quality_flags"]
    if flags is not None and (not isinstance(flags, list)
                              or any(not isinstance(f, str) for f in flags)):
        raise ValueError("quality_flags must be a list of reason codes")
    return row
