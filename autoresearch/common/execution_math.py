#!/usr/bin/env python3
"""执行评价的**无 IO 纯计算**(工作包 C1/C2)—— 时点可见性、决策时点派生、快照入场条件。

实施计划:`docs/superpowers/plans/2026-09-06-execution-evaluation.md` Task C1/C2。

**这里算的是「当时看得见什么」,不是「成交了什么」。** 三种证据模式(EOD_PROXY /
SNAPSHOT_SIMULATED / OBSERVED_FILL)分别汇总,本模块只服务前两种的时点判定;真实成交
状态与损益归 C3/C4。

与计划的一处偏离(记在这里,不藏):计划 C1 Step 5 的示例 `decision_at_for_run(run_dir)`
直接 `from autoresearch.scan.exec_anchor import read_execution`,那会造出一条
`common → scan` 的**向上**边(`tests/contracts/test_layering.py`)。这里改成纯函数
`decision_at_from_execution_block(block)`:由上层(research 的导入器/CLI)自己
`read_execution(run_dir)` 之后把块传进来。语义一字不差,方向对了。
"""
from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

from autoresearch.contracts.agent_output import (
    EXEC_LINE_MAX_PCT_1D,
    EXEC_LINE_MAX_POS_IN_RANGE,
)
from autoresearch.contracts.execution import (
    ACTIONABLE,
    VENUE_TIMEZONES,
    VERIFIED_CALENDARS,
    conditional_gap as conditional_gap,  # compatibility export
    parse_aware,
    validate_decision_frame,
    validate_scenario_estimate as validate_scenario_estimate,
)


def build_decision_frame(*, analysis_session: str, knowledge_cutoff: str,
                         venue: str, research_depth: str, usage: str,
                         sessions: list[str], calendar_quality: str) -> dict:
    """Build the common frame from an injected exchange calendar, without I/O."""
    if not isinstance(venue, str) or venue not in VENUE_TIMEZONES:
        raise ValueError("unsupported venue")
    if not isinstance(calendar_quality, str):
        raise ValueError("calendar quality must be a string")
    days = sorted(set(sessions))
    for day in days:
        if not isinstance(day, str) or date.fromisoformat(day).isoformat() != day:
            raise ValueError("calendar session must be an ISO date")
    future = [day for day in days if day > analysis_session]
    verified = (calendar_quality in VERIFIED_CALENDARS and analysis_session in days
                and len(future) >= 2 and venue not in {"CONTINUOUS", "UNSPECIFIED"})
    return validate_decision_frame({
        "schema_version": 1, "analysis_session": analysis_session,
        "knowledge_cutoff": knowledge_cutoff, "venue": venue,
        "timezone": VENUE_TIMEZONES[venue], "ruler": "gap_c1_o2",
        "research_depth": research_depth, "usage": usage,
        "entry_session": future[0] if verified else None, "entry_phase": "CLOSE",
        "exit_session": future[1] if verified else None, "exit_phase": "OPEN",
        "return_basis": "ENTRY_PRICE",
        "calendar_quality": calendar_quality if verified else "UNKNOWN",
    })

_VISIBILITY_TIMES = ("decision_at", "market_event_at", "provider_published_at",
                     "received_at", "persisted_at")
_MARKET_FIELDS = ("last", "previous_close", "high_so_far", "low_so_far")


def snapshot_visibility(row: dict) -> str:
    """五个时点 → 可见性状态(闭集见 `contracts.execution.VISIBILITY_STATES`)。

    - 任一时点缺失 → `UNKNOWN`(**没给全**,不是「不可得」);
    - 顺序不成立(行情发生 ≤ 供应商发布 ≤ 我方收到 ≤ 落盘)→ `INVALID_TIME_ORDER`;
    - 行情发生或发布**晚于**决策时点 → `FUTURE_INFORMATION`;
    - 收到晚于决策 → `NOT_OBSERVED_AT_DECISION`(当时没看见,不能算进「当时的判断」);
    - 其余 → `AVAILABLE`。

    `persisted_at` 允许略晚于决策:先收到、后落盘是正常的,不据此否定已经收到的数据。

    这证明的只是**导入字段符合可得性顺序**,仍须 observation/blob 留证。历史供应商能证明
    当时已发布、但本系统晚收到的,只能标历史模拟,不升级 `OBSERVED_FILL`。
    """
    times = {key: parse_aware(row[key]) for key in _VISIBILITY_TIMES}
    if any(value is None for value in times.values()):
        return "UNKNOWN"
    decision, market, published, received, persisted = (times[k] for k in _VISIBILITY_TIMES)
    if not market <= published <= received <= persisted:
        return "INVALID_TIME_ORDER"
    if market > decision or published > decision:
        return "FUTURE_INFORMATION"
    return "AVAILABLE" if received <= decision else "NOT_OBSERVED_AT_DECISION"


def decision_at_from_execution_block(block: dict) -> tuple[datetime | None, str]:
    """run 的时间锚块 → `(decision_at, actionability_status)`。

    `block` 就是 `scan.exec_anchor.read_execution(run_dir)` 的返回(或 `manifest.execution`)。
    只有 `ACTIONABLE` 的 run 才有决策时点:其余(迟到需复核 / 过期 / 未批准)返回
    `(None, status)`,由调用方记进覆盖表——**不进任何模式的分母**。

    08-28 实测:61 个已发布 run 里 8 个(13%)在 T+1 收盘之后才批准,账本给它们记的是一笔
    已经过去、下不了的单。让导入文件自己填 `decision_at` 就是把这个病搬进新仪器。

    缺 `exec_decision_cutoff` / `timezone_assumed` 时**抛错**,不取缺省:猜一次就够把 14:45
    这条运营纪律悄悄改掉。
    """
    status = str(block.get("actionability_status") or "UNKNOWN")
    session = block.get("first_available_session")
    if status != ACTIONABLE or not session:
        return None, (status if session or status != ACTIONABLE else "UNKNOWN")
    cutoff, zone = block.get("exec_decision_cutoff"), block.get("timezone_assumed")
    if not cutoff or not zone:
        raise ValueError("execution block must carry exec_decision_cutoff and timezone_assumed")
    hh, mm = (int(part) for part in str(cutoff).split(":"))
    return datetime.combine(date.fromisoformat(str(session)), time(hh, mm),
                            tzinfo=ZoneInfo(str(zone))), status


def entry_condition(row: dict, *, max_age_seconds: float) -> dict:
    """截至快照的入场条件 —— 与卡面执行线**同形式**,不是它的等价替换,更不是成交证明。

    阈值单源在 `contracts.agent_output`(`EXEC_LINE_MAX_PCT_1D` / `EXEC_LINE_MAX_POS_IN_RANGE`),
    等号方向照抄卡面:当日涨幅 `<=` 上限、区间位置 `<` 上限。

    `max_age_seconds` 是**显式研究参数**,不冒充项目现有缺省:快照有多新才算「当时」是本次
    实验的声明,得跟着 spec 走。

    封涨停单列 `LIMIT_UP_QUEUE`:没有队列/盘口证据,就不能从 `last == limit_up` 推出买得到
    ——08-28 普查「收益随买得到的可能性单调递减」正是在这里发生的。
    """
    if max_age_seconds < 0:
        raise ValueError("negative freshness policy")
    if snapshot_visibility(row) != "AVAILABLE":
        return {"verdict": "UNKNOWN", "reason": "TIME_NOT_VERIFIED"}
    age = (parse_aware(row["decision_at"]) - parse_aware(row["market_event_at"])).total_seconds()
    if age > max_age_seconds:
        return {"verdict": "UNKNOWN", "reason": "STALE_SNAPSHOT"}
    if row["suspended"] is True:
        return {"verdict": "FAIL", "reason": "SUSPENDED"}
    if row["suspended"] is None or any(row[k] is None for k in _MARKET_FIELDS):
        return {"verdict": "UNKNOWN", "reason": "MISSING_MARKET_FIELDS"}
    last, prev, high, low = (Decimal(row[k]) for k in _MARKET_FIELDS)
    if not all(x.is_finite() for x in (last, prev, high, low)):
        raise ValueError("nonfinite price")
    if not (prev > 0 and low > 0 and high >= last >= low):
        raise ValueError("invalid price range")
    if high == low:
        return {"verdict": "UNKNOWN", "reason": "ZERO_RANGE"}
    limit_up = row.get("limit_up_price")
    if limit_up is not None and last >= Decimal(limit_up):
        return {"verdict": "UNKNOWN", "reason": "LIMIT_UP_QUEUE"}
    pct = (last / prev - 1) * 100
    pos = (last - low) / (high - low)
    passed = (pct <= Decimal(str(EXEC_LINE_MAX_PCT_1D))
              and pos < Decimal(str(EXEC_LINE_MAX_POS_IN_RANGE)))
    return {"verdict": "PASS" if passed else "FAIL", "reason": "SNAPSHOT_PROXY"}


# ───────────────────────── C3:成交状态与部分实现损益(2026-09-07) ─────────────────────────


def entry_status(*, submitted, requested_qty, filled_qty, cancelled) -> str:
    """逐订单状态归并。`submitted=None` 或 `filled_qty=None` 是「不知道」,不是「没成交」。"""
    if submitted is None or filled_qty is None:
        return "UNKNOWN"
    qty = Decimal(filled_qty)
    if not submitted:
        if qty != 0:
            raise ValueError("fill without submitted order")
        return "NOT_SUBMITTED"
    requested = Decimal(requested_qty)
    if not requested.is_finite() or not qty.is_finite() or not 0 <= qty <= requested or requested <= 0:
        raise ValueError("invalid order quantity")
    if cancelled:
        return "CANCELLED"        # 已成交数量仍保留在独立字段
    return "FILLED" if qty == requested else "PARTIAL_FILL" if qty > 0 else "NO_FILL"


def exit_status(*, due, submitted, requested_qty, filled_qty, unsellable_open) -> str:
    """计划退出时点未到 → NOT_DUE;无委托证据 → UNKNOWN;有委托零成交 → NO_FILL。

    `unsellable_open=True`(T+2 一字跌停开,`ruler.EXIT_FLAG`)只作**标旗**随结果一起返回给
    调用方——样本不剔,剔了会美化;这里不因它改状态。
    """
    if not due:
        return "NOT_DUE"
    if submitted is None or filled_qty is None:
        return "UNKNOWN"
    if not submitted:
        return "UNKNOWN"          # 该卖没委托:是「没有委托证据」,不是「卖不出」
    qty, requested = Decimal(filled_qty), Decimal(requested_qty)
    if not 0 <= qty <= requested or requested <= 0:
        raise ValueError("invalid exit quantity")
    return "FILLED" if qty == requested else "PARTIAL_FILL" if qty > 0 else "NO_FILL"


def position_pnl(*, buy_qty, buy_notional, buy_fees, sell_qty, sell_notional, sell_fees,
                 mark_price, cash_distribution, receivable) -> dict:
    """一个已对账 position 的已实现 / 未实现 / 现金对账,全部 Decimal。

    `net_pnl_cash` 是完整仓位累计现金流,不是部分平仓的已实现利润;`realized_pnl` 与剩余市值
    必须并列。分红现金与应收单列,不并入价格腿,禁止与复权价重复计入。未知费用**不许**用 0 顶。
    """
    raw = (buy_qty, buy_notional, buy_fees, sell_qty, sell_notional, sell_fees,
           cash_distribution, receivable)
    if any(x is None for x in raw):
        raise ValueError("missing amounts; do not replace unknown fees with zero")
    bq, bn, bf, sq, sn, sf, cash, due = map(Decimal, raw)
    if any(not x.is_finite() or x < 0 for x in (bq, bn, bf, sq, sn, sf, cash, due)):
        raise ValueError("invalid amount")
    if bq <= 0 or bn <= 0 or sq > bq:
        raise ValueError("invalid long position")
    if sq == 0 and (sn != 0 or sf != 0):
        raise ValueError("sell cash without sell quantity")
    basis = bn + bf
    allocated = basis * sq / bq
    left = bq - sq
    realized = sn - sf - allocated
    mark = None if mark_price is None else Decimal(mark_price)
    if mark is not None and (not mark.is_finite() or mark <= 0):
        raise ValueError("invalid mark")
    unrealized = None if mark is None else left * mark - (basis - allocated)
    return {
        "remaining_qty": left, "realized_pnl": realized,
        "net_return_realized": realized / allocated if allocated > 0 else None,
        "unrealized_pnl": unrealized,
        "net_pnl_cash": sn + cash - bn - bf - sf,
        "cash_distribution": cash, "corporate_action_receivable": due,
    }


# ───────────────────────── C4:版本化模拟成本与成交规则 ─────────────────────────


def simulated_leg(*, price, qty, side, slippage_bps, commission_rate, minimum_commission,
                  tax_rate, tax_sides=frozenset({"SELL"}), transfer_fee_rate="0") -> dict:
    """一条模拟腿的成交价、名义额与三项费用。A 股印花税**只收卖出腿**;过户费双边由 policy 声明。"""
    p, q, slip, rate, minimum, tax, transfer = map(
        Decimal, (price, qty, slippage_bps, commission_rate, minimum_commission,
                  tax_rate, transfer_fee_rate))
    if any(not x.is_finite() or x < 0 for x in (p, q, slip, rate, minimum, tax, transfer)) or slip >= 10000:
        raise ValueError("invalid cost parameters")
    if side not in {"BUY", "SELL"} or p <= 0 or q <= 0:
        raise ValueError("invalid simulated order")
    if not set(tax_sides) <= {"BUY", "SELL"}:
        raise ValueError("tax sides must be BUY/SELL")
    adjusted = p * (1 + slip / 10000 if side == "BUY" else 1 - slip / 10000)
    notional = adjusted * q
    return {"price": adjusted, "notional": notional,
            "commission": max(notional * rate, minimum),
            "tax": notional * tax if side in tax_sides else Decimal("0"),
            "transfer_fee": notional * transfer}


def closing_auction_fill(*, snapshot_last, limit_bps, close_price, entry_sealed) -> dict:
    """`close_auction_limit_v1`:以 14:45 快照 last×(1+limit_bps) 挂收盘集合竞价限价单。

    成交价 = 收盘价;成交条件 = 收盘价 ≤ 限价 **且** 收盘未封涨停(`entry_sealed=False`)。
    `entry_sealed=None`(不知道封没封)→ UNKNOWN,不推「大概能买到」。
    """
    if snapshot_last is None or close_price is None:
        return {"state": "UNKNOWN", "reason": "MISSING_PRICE", "fill_rule_version": "close_auction_limit_v1"}
    last, close, bps = Decimal(snapshot_last), Decimal(close_price), Decimal(limit_bps)
    if not all(x.is_finite() for x in (last, close, bps)) or last <= 0 or close <= 0 or bps < 0:
        raise ValueError("invalid auction inputs")
    limit = last * (1 + bps / 10000)
    if entry_sealed is None:
        return {"state": "UNKNOWN", "reason": "SEAL_UNKNOWN", "limit": limit,
                "fill_rule_version": "close_auction_limit_v1"}
    if entry_sealed:
        return {"state": "NO_FILL", "reason": "LIMIT_UP_SEALED", "limit": limit,
                "fill_rule_version": "close_auction_limit_v1"}
    if close > limit:
        return {"state": "NO_FILL", "reason": "AUCTION_ABOVE_LIMIT", "limit": limit,
                "fill_rule_version": "close_auction_limit_v1"}
    return {"state": "FILLED", "reason": "AUCTION_AT_CLOSE", "price": close, "limit": limit,
            "fill_rule_version": "close_auction_limit_v1"}


def open_auction_fill(*, open_price, exit_unsellable, due: bool) -> dict:
    """`open_auction_v1`:到期后按 D+2 开盘价退出；一字跌停只标未成交，不剔样本。"""
    if not due:
        return {"state": "NOT_DUE", "reason": "EXIT_NOT_DUE",
                "fill_rule_version": "open_auction_v1"}
    if open_price is None or exit_unsellable is None:
        return {"state": "UNKNOWN", "reason": "MISSING_EXIT_DATA",
                "fill_rule_version": "open_auction_v1"}
    price = Decimal(open_price)
    if not price.is_finite() or price <= 0:
        raise ValueError("invalid open auction price")
    if exit_unsellable:
        return {"state": "NO_FILL", "reason": "LIMIT_DOWN_UNSELLABLE", "price": price,
                "fill_rule_version": "open_auction_v1"}
    return {"state": "FILLED", "reason": "OPEN_AUCTION", "price": price,
            "fill_rule_version": "open_auction_v1"}


def after_hours_fixed_fill(*, code, close_price, wanted_qty, after_hours_volume) -> dict:
    """`after_hours_fixed_v1`:科创/创业板 15:05–15:30 按收盘价成交,量以盘后成交量为上限。"""
    from autoresearch.contracts.execution import AFTER_HOURS_PREFIXES

    if not str(code).startswith(AFTER_HOURS_PREFIXES):
        return {"state": "NOT_SUBMITTED", "reason": "BOARD_NOT_ELIGIBLE",
                "fill_rule_version": "after_hours_fixed_v1"}
    if after_hours_volume is None or close_price is None:
        return {"state": "UNKNOWN", "reason": "MISSING_AFTER_HOURS_DATA",
                "fill_rule_version": "after_hours_fixed_v1"}
    wanted, available = Decimal(wanted_qty), Decimal(after_hours_volume)
    if wanted <= 0 or available < 0:
        raise ValueError("invalid quantities")
    filled = min(wanted, available)
    state = "NO_FILL" if filled == 0 else "PARTIAL_FILL" if filled < wanted else "FILLED"
    return {"state": state, "filled_qty": filled, "price": Decimal(close_price),
            "fill_rule_version": "after_hours_fixed_v1"}


def constrain_execution_to_frame(block: dict, frame: dict) -> dict:
    """Apply frozen calendar uncertainty without changing any research rating or E6 selection."""
    validate_decision_frame(frame)
    result = dict(block)
    if frame["analysis_session"] != result["analysis_date"]:
        raise ValueError("execution analysis anchor differs from frozen frame")
    if frame["calendar_quality"] == "UNKNOWN":
        result.update(actionability_status="UNKNOWN", first_available_session=None,
                      exec_lag=None, staleness_sessions=None, calendar_quality="UNKNOWN")
    elif result.get("actionability_status") == "ACTIONABLE" and result.get("first_available_session") != frame["entry_session"]:
        result["actionability_status"] = "UNKNOWN"
    result["decision_frame"] = frame
    return result
