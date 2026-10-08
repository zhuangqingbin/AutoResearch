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
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

DECISION_FRAME_SCHEMA_VERSION = 1
DECISION_FRAME_FIELDS = frozenset({
    "schema_version", "analysis_session", "knowledge_cutoff", "venue", "timezone",
    "ruler", "research_depth", "usage", "entry_session", "entry_phase",
    "exit_session", "exit_phase", "return_basis", "calendar_quality",
})
VENUE_TIMEZONES = {
    "XSHG": "Asia/Shanghai", "XSHE": "Asia/Shanghai", "XBSE": "Asia/Shanghai",
    "XNYS": "America/New_York", "XNAS": "America/New_York",
    "XHKG": "Asia/Hong_Kong", "UNSPECIFIED": "UTC", "CONTINUOUS": "UTC",
}
VERIFIED_CALENDARS = frozenset({"trade_cal", "exchange_calendar"})


CALENDAR_SOURCE_FIELDS = frozenset({"schema_version", "venue", "timezone", "source_id", "published_at", "available_at", "sessions"})


def validate_calendar_source(value: dict, *, venue: str, cutoff: str) -> dict:
    if not isinstance(value, dict) or set(value) != CALENDAR_SOURCE_FIELDS:
        raise ValueError("invalid exchange calendar source fields")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ValueError("invalid exchange calendar source version")
    if value["venue"] != venue or value["timezone"] != VENUE_TIMEZONES.get(venue):
        raise ValueError("calendar venue/timezone differs from research request")
    if not isinstance(value["source_id"], str) or not value["source_id"].strip():
        raise ValueError("calendar source identity required")
    published = parse_aware(value["published_at"])
    available = parse_aware(value["available_at"])
    if published is None or available is None or published > available or available > parse_aware(cutoff):
        raise ValueError("calendar source unavailable at knowledge cutoff")
    if not isinstance(value["sessions"], list):
        raise ValueError("calendar sessions must be a list")
    previous = ""
    for row in value["sessions"]:
        if not isinstance(row, dict) or set(row) != {"date", "open_at", "close_at"}:
            raise ValueError("invalid exchange calendar session fields")
        day = row["date"]
        if not isinstance(day, str) or date.fromisoformat(day).isoformat() != day or day <= previous:
            raise ValueError("calendar sessions must be ordered unique ISO dates")
        previous = day
        times = [parse_aware(row[key]) for key in ("open_at", "close_at")]
        if any(t is None or t.astimezone(ZoneInfo(value["timezone"])).date().isoformat() != day
               or t.utcoffset() != t.astimezone(ZoneInfo(value["timezone"])).utcoffset() for t in times):
            raise ValueError("calendar session timezone/date mismatch")
        if times[0] >= times[1]:
            raise ValueError("calendar open must precede close")
    return value


def validate_decision_frame(value: dict) -> dict:
    """Validate a declared overnight clock; dates are supplied by a calendar owner."""
    version = value.get("schema_version") if isinstance(value, dict) else None
    fields = DECISION_FRAME_FIELDS | ({"calendar_evidence", "predecessor_frame_hash"} if version == 2 else set())
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("invalid decision frame fields")
    if type(value["schema_version"]) is not int or value["schema_version"] not in {1, 2}:
        raise ValueError("unsupported decision frame schema")
    enum_fields = ("ruler", "entry_phase", "exit_phase", "return_basis", "research_depth",
                   "usage", "venue", "timezone", "calendar_quality")
    if any(not isinstance(value[key], str) for key in enum_fields):
        raise ValueError("decision frame enums must be strings")
    fixed = {"ruler": "gap_c1_o2", "entry_phase": "CLOSE", "exit_phase": "OPEN",
             "return_basis": "ENTRY_PRICE"}
    if any(value[key] != expected for key, expected in fixed.items()):
        raise ValueError("decision frame must use close-to-open entry-price returns")
    if value["research_depth"] not in {"FULL", "LITE"}:
        raise ValueError("invalid research depth")
    if value["usage"] not in {"standalone", "scan", "holding_review", "macro", "sector"}:
        raise ValueError("invalid research usage")
    if value["venue"] not in VENUE_TIMEZONES or value["timezone"] != VENUE_TIMEZONES[value["venue"]]:
        raise ValueError("venue and timezone must agree")
    if not isinstance(value["analysis_session"], str):
        raise ValueError("analysis session must be an ISO date")
    analysis = date.fromisoformat(value["analysis_session"])
    if analysis.isoformat() != value["analysis_session"]:
        raise ValueError("analysis session must be an ISO date")
    cutoff = parse_aware(value["knowledge_cutoff"])
    if cutoff is None or cutoff.astimezone(ZoneInfo(value["timezone"])).date() < analysis:
        raise ValueError("knowledge cutoff precedes analysis session")
    if version == 2:
        predecessor = value["predecessor_frame_hash"]
        if predecessor is not None and (not isinstance(predecessor, str) or not re.fullmatch(r"[0-9a-f]{64}", predecessor)):
            raise ValueError("invalid predecessor frame hash")
        evidence = value["calendar_evidence"]
        if evidence is not None:
            if not isinstance(evidence, dict) or set(evidence) != {"source_sha256", "source"}:
                raise ValueError("invalid calendar evidence fields")
            if not isinstance(evidence["source_sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", evidence["source_sha256"]):
                raise ValueError("invalid calendar source hash")
            source = validate_calendar_source(evidence["source"], venue=value["venue"], cutoff=value["knowledge_cutoff"])
            days = [row["date"] for row in source["sessions"]]
            if value["calendar_quality"] != "UNKNOWN":
                if value["analysis_session"] not in days:
                    raise ValueError("analysis is not a verified trading session")
                index = days.index(value["analysis_session"])
                if days[index + 1:index + 3] != [value["entry_session"], value["exit_session"]]:
                    raise ValueError("entry/exit must be consecutive calendar sessions")
                if cutoff < parse_aware(source["sessions"][index]["close_at"]):
                    raise ValueError("analysis session is not settled at knowledge_cutoff")
        elif value["calendar_quality"] != "UNKNOWN":
            raise ValueError("verified frame requires calendar source evidence")
    quality = value["calendar_quality"]
    if quality == "UNKNOWN":
        if value["entry_session"] is not None or value["exit_session"] is not None:
            raise ValueError("unverified calendar cannot declare trading sessions")
        return value
    if quality not in VERIFIED_CALENDARS or value["venue"] in {"CONTINUOUS", "UNSPECIFIED"}:
        raise ValueError("unverified trading calendar")
    if value["venue"] in {"XSHG", "XSHE", "XBSE"}:
        # The A-share regular session ends at 15:00 local time. Foreign closes
        # (including early-close sessions) must be supplied by their calendar owner.
        analysis_close = datetime.combine(analysis, time(15), ZoneInfo(value["timezone"]))
        if cutoff < analysis_close:
            raise ValueError("analysis session is not settled at knowledge_cutoff")
    for key in ("entry_session", "exit_session"):
        if not isinstance(value[key], str) or date.fromisoformat(value[key]).isoformat() != value[key]:
            raise ValueError("session must be an ISO date")
    if not value["analysis_session"] < value["entry_session"] < value["exit_session"]:
        raise ValueError("expected analysis < entry < exit sessions")
    return value

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


# ───────────────────────── C3/C4:成交、成本与损益的词表(2026-09-07) ─────────────────────────

#: 模拟成交规则版本。**没有规则版本的模拟结果不得进入读数**。
#: - close_auction_limit_v1:买腿 = 以 14:45 快照 last×(1+limit_bps) 挂收盘集合竞价限价单;
#:   成交价 = 收盘价;成交条件 = 收盘价 ≤ 限价 且 收盘未封涨停(ENTRY_FLAG 可买),否则 NO_FILL。
#: - after_hours_fixed_v1:仅 688/300 代码,15:05–15:30 盘后固定价格按收盘价成交,量以盘后
#:   成交量为上限;缺盘后量数据 → UNKNOWN。
#: - open_auction_v1:卖腿 = T+2 开盘集合竞价;一字跌停开 = EXIT_FLAG 标旗不剔。
FILL_RULE_VERSIONS: frozenset[str] = frozenset(
    {"close_auction_limit_v1", "after_hours_fixed_v1", "open_auction_v1"})
#: 盘后固定价格交易只对这两个板开放(科创 688 / 创业 300、301)。
AFTER_HOURS_PREFIXES: tuple[str, ...] = ("688", "300", "301")

#: 版本化成本模型必填。费率数值由执行者按来源与生效规则核验,这里只声明**形状**。
COST_MODEL_FIELDS: tuple[str, ...] = (
    "cost_model_version", "venue", "effective_from", "commission_rate", "minimum_commission",
    "tax_rate", "tax_sides", "transfer_fee_rate", "slippage_bps", "order_merge",
)
CORPORATE_ACTION_STATES: frozenset[str] = frozenset(
    {"NONE", "RESOLVED", "CORPORATE_ACTION_UNRESOLVED"})


def validate_cost_model(policy: dict) -> dict:
    """成本模型的形状:字段齐、`tax_sides` ⊆ {BUY, SELL}、费率全是非负 decimal string。"""
    if not isinstance(policy, dict) or set(policy) != set(COST_MODEL_FIELDS):
        raise ValueError("cost model must carry exactly the declared fields")
    if not isinstance(policy["cost_model_version"], str) or not policy["cost_model_version"]:
        raise ValueError("cost_model_version required")
    sides = policy["tax_sides"]
    if not isinstance(sides, list) or not set(sides) <= {"BUY", "SELL"}:
        raise ValueError("tax_sides must be a subset of BUY/SELL")
    for field in ("commission_rate", "minimum_commission", "tax_rate", "transfer_fee_rate",
                  "slippage_bps"):
        parse_amount(policy[field], field=field)
    if policy["order_merge"] not in {"per_order", "per_day"}:
        raise ValueError("order_merge must be per_order or per_day")
    return policy


def conditional_gap(exit_price: str | None, entry_price: str | None) -> str | None:
    """Scenario return relative to the declared entry, never today's quote."""
    exit_value = parse_amount(exit_price, field="exit_price")
    entry_value = parse_amount(entry_price, field="entry_price")
    if exit_value is None or entry_value is None:
        return None
    if exit_value <= 0 or entry_value <= 0:
        raise ValueError("scenario prices must be positive")
    return format(exit_value / entry_value - 1, "f")


#: 卡面收益 / EV / R:R 的最小声明精度(R5-1,2026-10-07):agent 没有计算工具,比值与四舍五入
#: 对不上 1e-9 是必然的。校验容差 = 声明末位的一半、封顶半个万分位(声明得更粗不放宽,更细更严),
#: EV 与 R:R 再加上各情景收益舍入传播过来的误差。
SCENARIO_PRECISION = Decimal("0.0001")


def declared_tolerance(raw: str) -> Decimal:
    """半个「声明末位」,封顶 ``SCENARIO_PRECISION / 2``:``"0.0240"`` → 0.00005,``"0.02"`` → 0.00005,
    ``"0.024018"`` → 0.0000005。"""
    exponent = Decimal(raw).as_tuple().exponent
    places = -exponent if isinstance(exponent, int) and exponent < 0 else 0
    return min(Decimal(1).scaleb(-places), SCENARIO_PRECISION) / 2


def validate_scenario_estimate(value: dict) -> dict:
    """Validate declared conditional returns; interval bounds use high/low entry respectively.

    Declared figures are compared within :func:`declared_tolerance` (plus the rounding of
    the returns they derive from), never bit-exactly.
    """
    fields = {'schema_version', 'entry', 'probability_basis', 'scenarios', 'ev_range', 'rr'}
    if not isinstance(value, dict) or set(value) != fields or type(value['schema_version']) is not int or value['schema_version'] != 1:
        raise ValueError('invalid conditional-scenarios-v1 fields')

    def number(raw, *, positive=False):
        from decimal import InvalidOperation
        if not isinstance(raw, str):
            raise ValueError('scenario number must be a decimal string')
        try:
            result = Decimal(raw)
        except InvalidOperation as exc:
            raise ValueError('invalid scenario number') from exc
        if not result.is_finite() or (positive and result <= 0):
            raise ValueError('scenario prices must be finite and positive')
        return result

    def bounds(raw, *, positive=False):
        if not isinstance(raw, dict) or set(raw) != {'low', 'high'}:
            raise ValueError('scenario range must declare low/high')
        low, high = (number(raw[key], positive=positive) for key in ('low', 'high'))
        if low > high:
            raise ValueError('scenario range is reversed')
        return low, high

    entry = None if value['entry'] is None else bounds(value['entry'], positive=True)
    rows = value['scenarios']
    if not isinstance(rows, list) or not rows:
        raise ValueError('scenario prices required')
    names = []
    returns, return_tolerances, probabilities = [], [], []
    for row in rows:
        if not isinstance(row, dict) or set(row) != {'name', 'exit_price', 'return_range', 'probability'}:
            raise ValueError('invalid scenario fields')
        if row['name'] not in {'bull', 'base', 'bear'} or row['name'] in names:
            raise ValueError('invalid or repeated scenario name')
        names.append(row['name'])
        number(row['exit_price'], positive=True)
        if entry is None:
            if row['return_range'] is not None:
                raise ValueError('scenario return needs declared entry')
        else:
            actual = bounds(row['return_range'])
            expected = tuple(Decimal(conditional_gap(row['exit_price'], str(point))) for point in reversed(entry))
            tolerance = tuple(declared_tolerance(row['return_range'][key]) for key in ('low', 'high'))
            if any(abs(a-b) > t for a, b, t in zip(actual, expected, tolerance, strict=True)):
                raise ValueError('scenario return contradicts declared entry/exit')
            returns.append(actual)
            return_tolerances.append(tolerance)
        probability = row['probability']
        probabilities.append(None if probability is None else number(probability))
    if any(p is not None for p in probabilities):
        if (set(names) != {'bull', 'base', 'bear'} or any(p is None or not 0 <= p <= 1 for p in probabilities)
                or sum(probabilities) != 1 or value['probability_basis'] != 'subjective'):
            raise ValueError('three subjective scenario probabilities must sum to one')
    elif value['probability_basis'] != 'not_provided':
        raise ValueError('probability basis without probabilities')
    ev = value['ev_range']
    if ev is not None:
        if entry is None or any(p is None for p in probabilities):
            raise ValueError('EV needs declared entry and subjective probabilities')
        actual = bounds(ev)
        expected = tuple(sum(row[i]*p for row, p in zip(returns, probabilities, strict=True)) for i in (0, 1))
        # 声明精度的一半 + 各情景收益舍入按概率加权传播。
        tolerance = tuple(declared_tolerance(ev[key])
                          + sum(t[i]*p for t, p in zip(return_tolerances, probabilities, strict=True))
                          for i, key in ((0, 'low'), (1, 'high')))
        if any(abs(a-b) > t for a, b, t in zip(actual, expected, tolerance, strict=True)):
            raise ValueError('EV contradicts scenario returns')
    if value['rr'] is not None:
        if entry is None or entry[0] != entry[1] or set(names) != {'bull', 'base', 'bear'}:
            raise ValueError('R:R requires point entry and three scenarios')
        by_name = dict(zip(names, returns, strict=True))
        by_tolerance = dict(zip(names, return_tolerances, strict=True))
        gain, loss = by_name['bull'][0], by_name['bear'][0]
        if gain <= 0 or loss >= 0:
            raise ValueError('R:R contradicts bull/bear returns')
        declared = number(value['rr'], positive=True)
        gain_tolerance, loss_tolerance = by_tolerance['bull'][0], by_tolerance['bear'][0]
        # d(g/-l) ≈ dg/|l| + g·dl/l²:两头收益的舍入都会放大进比值。
        tolerance = (declared_tolerance(value['rr'])
                     + gain_tolerance / -loss + gain * loss_tolerance / (loss * loss))
        if abs(declared - gain / -loss) > tolerance:
            raise ValueError('R:R contradicts bull/bear returns')
    return value

