#!/usr/bin/env python3
"""Turn observed broker fills into deterministic long-only FIFO position episodes."""
from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict, deque
from decimal import Decimal

FEE_FIELDS = ("commission", "stamp_tax", "transfer_fee", "other_fee")


def _decimal(value, *, field: str) -> Decimal:
    try:
        result = Decimal(value)
    except Exception as exc:
        raise ValueError(f"invalid {field}: {value!r}") from exc
    if not result.is_finite() or result < 0:
        raise ValueError(f"invalid {field}: {value!r}")
    return result


def _fees(row: dict) -> Decimal | None:
    if any(row.get(field) is None for field in FEE_FIELDS):
        return None
    return sum((_decimal(row[field], field=field) for field in FEE_FIELDS), Decimal(0))


def _episode(account: str, code: str, first: dict) -> dict:
    identity = f"{account}|{code}|{first['trade_date']}|{first['fill_id']}"
    return {
        "account_hash": account,
        "code": code,
        "position_id": "pos:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16],
        "session": first["trade_date"],
        "lots": deque(),
        "buy_qty": Decimal(0),
        "sell_qty": Decimal(0),
        "allocated_buy_notional": Decimal(0),
        "allocated_buy_basis": Decimal(0),
        "sell_notional": Decimal(0),
        "sell_fees": Decimal(0),
        "fees_missing": False,
        "fill_ids": [],
    }


def _add_buy(ep: dict, row: dict) -> None:
    qty = _decimal(row["qty"], field="qty")
    amount = _decimal(row["amount"], field="amount")
    if qty <= 0 or amount <= 0:
        raise ValueError("buy qty and amount must be positive")
    fee = _fees(row)
    ep["fees_missing"] |= fee is None
    if not ep["lots"]:
        ep["active_entry_session"] = row["trade_date"]
    ep["lots"].append({"remaining": qty, "notional_per_qty": amount / qty,
                       "basis_per_qty": None if fee is None else (amount + fee) / qty})
    ep["buy_qty"] += qty
    ep["fill_ids"].append(row["fill_id"])


def _consume_sell(ep: dict, row: dict) -> bool:
    qty = _decimal(row["qty"], field="qty")
    amount = _decimal(row["amount"], field="amount")
    available = sum((lot["remaining"] for lot in ep["lots"]), Decimal(0))
    if qty <= 0 or amount <= 0 or qty > available:
        return False
    left = qty
    while left:
        lot = ep["lots"][0]
        take = min(left, lot["remaining"])
        ep["allocated_buy_notional"] += take * lot["notional_per_qty"]
        if lot["basis_per_qty"] is None:
            ep["fees_missing"] = True
        else:
            ep["allocated_buy_basis"] += take * lot["basis_per_qty"]
        lot["remaining"] -= take
        left -= take
        if lot["remaining"] == 0:
            ep["lots"].popleft()
    fee = _fees(row)
    ep["fees_missing"] |= fee is None
    ep["sell_qty"] += qty
    ep["fill_ids"].append(row["fill_id"])
    ep["sell_notional"] += amount
    if fee is not None:
        ep["sell_fees"] += fee
    return True


def _finish(ep: dict) -> dict:
    remaining = sum((lot["remaining"] for lot in ep["lots"]), Decimal(0))
    sold = ep["sell_qty"]
    complete = remaining == 0 and sold > 0
    has_return = sold > 0 and not ep["fees_missing"]
    realized = (ep["sell_notional"] - ep["sell_fees"] - ep["allocated_buy_basis"]
                if has_return else None)
    return {
        "evidence_mode": "OBSERVED_FILL",
        "account_hash": ep["account_hash"],
        "position_id": ep["position_id"],
        "fill_ids": list(ep["fill_ids"]),
        "code": ep["code"],
        "session": ep["session"],
        "entry_state": "FILLED",
        "exit_state": "FILLED" if complete else "PARTIAL_FILL" if sold else "UNKNOWN",
        "entry_verdict": "PASS",
        "entry_reason": "FEES_MISSING" if ep["fees_missing"] else "OBSERVED",
        "fill_rule_version": "observed",
        "cost_model_version": "observed",
        "buy_qty": ep["buy_qty"],
        "sell_qty": sold,
        "remaining_qty": remaining,
        "gross_return": (ep["sell_notional"] / ep["allocated_buy_notional"] - 1
                         if sold and ep["allocated_buy_notional"] else None),
        "realized_pnl": realized,
        "unrealized_pnl": None,
        "net_pnl_cash": (ep["sell_notional"] - ep["sell_fees"] - ep["allocated_buy_basis"]
                         if complete and has_return else None),
        "net_return_realized": (realized / ep["allocated_buy_basis"]
                                if realized is not None and ep["allocated_buy_basis"] else None),
        "holding_window_breached": None,
        "corporate_action_status": "NONE",
    }


def build_episodes(fills: list[dict]) -> tuple[list[dict], list[dict]]:
    """Return `(position assessments, excluded/unresolved coverage)` for observed fills."""
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    coverage: list[dict] = []
    for row in fills:
        if row["side"] == "OTHER":
            coverage.append({"account_hash": row["account_hash"], "code": row["code"],
                             "fill_id": row["fill_id"], "amount": row["amount"],
                             "reason": "UNRESOLVED_CASH_EVENT"})
            continue
        groups[(row["account_hash"], row["code"])].append(row)

    assessments = []
    for (account, code), rows in sorted(groups.items()):
        current = None
        invalid = False
        for row in sorted(rows, key=lambda r: (r["trade_date"], r.get("trade_time") or "", r["fill_id"])):
            if row["side"] == "BUY":
                if current is None:
                    current = _episode(account, code, row)
                    invalid = False
                _add_buy(current, row)
                continue
            if current is None or not _consume_sell(current, row):
                coverage.append({"account_hash": account, "code": code, "fill_id": row["fill_id"],
                                 "reason": "UNMATCHED_OPENING_INVENTORY"})
                invalid = True
                current = None
                continue
            if not current["lots"]:
                if not invalid:
                    assessments.append(_finish(current))
                current = None
        if current is not None and not invalid:
            assessments.append(_finish(current))
    return assessments, coverage


__all__ = ["build_episodes", "observed_execution", "execution_plan_hash"]


EXECUTION_PLAN_FIELDS = (
    "code", "ruler", "execution_mode", "cost_model_version", "entry_window_start",
    "entry_window_end", "exit_window_start", "exit_window_end", "analysis_session", "calendar",
)


def execution_plan_hash(plan: dict) -> str:
    """Bind a probability declaration to the exact frozen execution event parameters.

    The declaration must store this hash when p is declared, before execution.
    Timestamps preserve their declared representation; changing any event parameter
    requires a new declaration. Unrelated plan metadata does not redefine the event.
    """
    payload = {key: plan.get(key) for key in EXECUTION_PLAN_FIELDS}
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _aware_time(value):
    from datetime import datetime
    try:
        result = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return result if result.utcoffset() is not None else None


def _calendar_window_valid(plan, windows):
    """Verify D1-close / D2-open against frozen, sourced exchange session bytes."""
    from datetime import date
    from zoneinfo import ZoneInfo

    from autoresearch.contracts.execution import validate_calendar_source
    calendar = plan.get('calendar')
    if not isinstance(calendar, dict) or set(calendar) != {'source_sha256', 'source'}:
        return False
    try:
        source = calendar['source']
        raw = json.dumps(source, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()
        if hashlib.sha256(raw).hexdigest() != calendar['source_sha256']:
            return False
        venue = source['venue']
        if venue not in {'XSHG', 'XSHE', 'XBSE'} or not all(windows):
            return False
        validate_calendar_source(source, venue=venue, cutoff=plan['entry_window_start'])
        analysis = plan['analysis_session']
        if date.fromisoformat(analysis).isoformat() != analysis:
            return False
        dates = [row['date'] for row in source['sessions']]
        index = dates.index(analysis)
        entry, exit = source['sessions'][index+1:index+3]
        local_dates = [value.astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat() for value in windows]
        if local_dates != [entry['date'], entry['date'], exit['date'], exit['date']]:
            return False
        close, opened = _aware_time(entry['close_at']), _aware_time(exit['open_at'])
        return windows[0] <= close == windows[1] and windows[2] <= opened <= windows[3]
    except (ValueError, TypeError, KeyError, IndexError):
        return False


def observed_execution(fills: list[dict], *, plan: dict) -> dict:
    """Bind one planned A-share overnight position to imported, actual fills.

    The caller supplies the frozen plan and normalized offline fill records; no
    broker/account access occurs. Windows are explicit aware timestamps supplied
    from the trading calendar, never inferred from the next civil day. Null fees,
    incomplete positions, unbound times and out-of-window legs remain unlabelled.
    Each leg's window must lie within one exchange-local date, with entry before
    exit. The plan must bind a sourced exchange calendar whose next two sessions
    match the declared D1 close and D2 open windows; missing calendars stay unknown.
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo

    reasons = []
    if not plan.get("execution_mode"):
        reasons.append("MISSING_EXECUTION_MODE")
    elif plan["execution_mode"] != "OBSERVED_FILL":
        reasons.append("ACTUAL_FILL_MODE_REQUIRED")
    if not plan.get("cost_model_version"):
        reasons.append("MISSING_COST_MODEL")
    if plan.get("ruler") != "gap_c1_o2":
        reasons.append("PLANNED_OVERNIGHT_REQUIRED")
    windows = [_aware_time(plan.get(key)) for key in (
        "entry_window_start", "entry_window_end", "exit_window_start", "exit_window_end")]
    valid_window = all(windows) and windows[0] <= windows[1] < windows[2] <= windows[3]
    if not valid_window:
        reasons.append("MISSING_PLANNED_WINDOW")
    else:
        dates = [value.astimezone(ZoneInfo("Asia/Shanghai")).date() for value in windows]
        if not dates[0] == dates[1] < dates[2] == dates[3]:
            valid_window = False
            reasons.append("INVALID_OVERNIGHT_DATES")
    if plan.get('calendar') is None:
        reasons.append('MISSING_FROZEN_CALENDAR')
    elif not _calendar_window_valid(plan, windows):
        reasons.append('INVALID_D1_D2_CALENDAR')
    if not plan.get("code"):
        reasons.append("MISSING_PLANNED_CODE")
    elif any(row.get("code") != plan["code"] for row in fills):
        reasons.append("FILL_CODE_MISMATCH")
    if not fills:
        reasons.append("MISSING_ACTUAL_FILLS")
    fill_ids = [row.get("fill_id") for row in fills]
    if any(not isinstance(value, str) or not value for value in fill_ids):
        reasons.append("MISSING_FILL_ID")
    elif len(set(fill_ids)) != len(fill_ids):
        reasons.append("DUPLICATE_FILL_ID")
    if any(row.get("side") not in {"BUY", "SELL", "OTHER"} for row in fills):
        reasons.append("INVALID_FILL_SIDE")
    timed = True
    breached = False
    for fill in fills:
        try:
            if not fill.get("trade_time"):
                raise ValueError("missing time")
            timestamp = datetime.fromisoformat(f"{fill['trade_date']}T{fill['trade_time']}")
            if timestamp.utcoffset() is None:
                timestamp = timestamp.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
        except (KeyError, TypeError, ValueError):
            timed = False
            reasons.append("MISSING_FILL_TIME")
            continue
        if valid_window and fill.get("side") in {"BUY", "SELL"}:
            low, high = windows[:2] if fill["side"] == "BUY" else windows[2:]
            if not low <= timestamp <= high:
                breached = True
                reasons.append("ENTRY_OUTSIDE_WINDOW" if fill["side"] == "BUY" else "EXIT_OUTSIDE_WINDOW")
    try:
        episodes, coverage = build_episodes(fills)
    except (ValueError, KeyError, TypeError):
        episodes, coverage = [], []
        reasons.append("INVALID_ACTUAL_FILLS")
    reasons.extend(item["reason"] for item in coverage)
    episode = episodes[0] if len(episodes) == 1 else None
    if len(episodes) > 1:
        reasons.append("MULTIPLE_EXECUTION_EPISODES")
    complete = episode is not None and episode["exit_state"] == "FILLED"
    if not complete:
        reasons.append("INCOMPLETE_EXIT")
    if episode and episode["entry_reason"] == "FEES_MISSING":
        reasons.append("FEES_MISSING")
    return {"evidence_mode": "OBSERVED_FILL", "execution_mode": plan.get("execution_mode"),
            "cost_model_version": plan.get("cost_model_version"), "code": plan.get("code"),
            "filled": bool(episodes), "exit_complete": complete,
            "window_breached": breached if valid_window and timed and fills else None,
            "net_pnl_cash": episode["net_pnl_cash"] if episode and not reasons else None,
            "net_return_realized": episode["net_return_realized"] if episode and not reasons else None,
            "missing_reasons": sorted(set(reasons)),
            "fill_ids": [row.get("fill_id") for row in fills]}


EVIDENCE_MODES = frozenset({"EOD_PROXY", "SNAPSHOT_SIMULATED", "OBSERVED_FILL"})


def validate_portfolio_policy(policy):
    """Validate preregistered capital/risk rules; no return-dependent sizing."""
    required = {
        "schema_version",
        "initial_cash",
        "initial_positions",
        "account_hash",
        "cash_daily_return",
        "max_positions",
        "max_weight",
        "duplicate_plan_policy",
        "cost_model_version",
        "benchmark",
        "simulation_rule",
    }
    if set(policy) != required or policy["schema_version"] != 1:
        raise ValueError("invalid portfolio policy")
    _decimal(policy["initial_cash"], field="initial_cash")
    rate = Decimal(policy["cash_daily_return"])
    if not rate.is_finite() or rate <= -1:
        raise ValueError("invalid cash return")
    weight = _decimal(policy["max_weight"], field="max_weight")
    if (
        not 0 < weight <= 1
        or type(policy["max_positions"]) is not int
        or policy["max_positions"] < 1
    ):
        raise ValueError("invalid position limits")
    if (
        policy["duplicate_plan_policy"] != "REJECT"
        or policy["benchmark"] != "CASH"
        or policy["simulation_rule"] != "snapshot_last.v1"
        or not policy["account_hash"]
        or not policy["cost_model_version"]
    ):
        raise ValueError("unregistered portfolio rule")
    seen = set()
    for item in policy["initial_positions"]:
        if item["code"] in seen:
            raise ValueError("duplicate initial inventory")
        seen.add(item["code"])
        for key in ("qty", "cost_basis", "market_value"):
            if item.get(key) is not None:
                _decimal(item[key], field=key)
        if item.get("qty") is None or Decimal(item["qty"]) <= 0:
            raise ValueError("initial quantity required")
    return policy


def execution_day_panel(sessions, *, decisions, fills, marks, policy, evidence_mode):
    """Complete frozen calendar, FIFO inventory and cash, with explicit unknowns.

    Callers supply one mode's verified legs only. Marks value existing holdings;
    they never create fills. An unknown execution or fee poisons subsequent cash
    until an explicit reconciliation (not supported by v1), preventing fictitious NAV.
    """
    from datetime import date

    validate_portfolio_policy(policy)
    if evidence_mode not in EVIDENCE_MODES:
        raise ValueError("unregistered evidence mode")
    if not sessions or sessions != sorted(set(sessions)):
        raise ValueError("unique ordered frozen calendar required")
    if any(date.fromisoformat(day).isoformat() != day for day in sessions):
        raise ValueError("invalid calendar date")
    if set(decisions) - set(sessions):
        raise ValueError("decision outside frozen calendar")
    if len({f["fill_id"] for f in fills}) != len(fills):
        raise ValueError("duplicate fill")
    for f in fills:
        if f["trade_date"] not in sessions or f["account_hash"] != policy["account_hash"]:
            raise ValueError("fill outside calendar/account")
        if f.get("evidence_mode", evidence_mode) != evidence_mode:
            raise ValueError("mixed evidence modes")
    cash = Decimal(policy["initial_cash"])
    initial = cash
    positions, sectors = {}, {}
    for item in policy["initial_positions"]:
        code = item["code"]
        ep = _episode(
            policy["account_hash"], code, {"trade_date": sessions[0], "fill_id": "opening:" + code}
        )
        quantity = _decimal(item["qty"], field="qty")
        basis = (
            _decimal(item["cost_basis"], field="cost_basis")
            if item.get("cost_basis") is not None
            else None
        )
        ep["lots"].append(
            {
                "remaining": quantity,
                "notional_per_qty": basis / quantity if basis is not None else Decimal(0),
                "basis_per_qty": basis / quantity if basis is not None else None,
            }
        )
        ep["buy_qty"] = quantity
        ep["fees_missing"] = basis is None
        positions[code] = ep
        sectors[code] = item.get("sector")
        initial = (
            initial + Decimal(item["market_value"])
            if initial is not None and item.get("market_value") is not None
            else None
        )
    previous_nav = initial
    previous_market_value = initial - cash if initial is not None else None
    rows = []
    cumulative_realized = Decimal(0)
    persistent = set()
    for day in sessions:
        declaration = decisions.get(day, {"status": "DATA_UNKNOWN"})
        status = declaration["status"]
        research_status = declaration.get("research_status", status)
        if status not in {"ABSTAIN", "SELECTED", "TASK_FAILED", "DATA_UNKNOWN"}:
            raise ValueError("unknown research day status")
        today = sorted(
            [r for r in fills if r["trade_date"] == day],
            key=lambda r: (r.get("trade_time") or "", r["fill_id"]),
        )
        if status == "SELECTED" and declaration.get("execution_status") == "NOT_DUE" and not today:
            status = "PLANNED_NOT_DUE"
        elif status == "SELECTED":
            status = (
                "NO_FILL"
                if declaration.get("execution_status") == "NO_FILL"
                and declaration.get("execution_evidence")
                and not today
                else "FILLED"
                if today
                else "EXECUTION_UNKNOWN"
            )
        elif today:
            status = "FILLED" if status == "ABSTAIN" else status
        requested = declaration.get("requested_quantities", {})
        if declaration.get("requested_qty") and today:
            requested = {today[0]["code"]: declaration["requested_qty"]}
        if status == "FILLED" and any(
            sum(
                (
                    _decimal(f["qty"], field="qty")
                    for f in today
                    if f["side"] == "BUY" and f["code"] == code
                ),
                Decimal(0),
            )
            < Decimal(qty)
            for code, qty in requested.items()
        ):
            status = "PARTIAL_FILL"
        order_states = []
        for order in declaration.get("orders", []):
            quantity = sum(
                (
                    _decimal(f["qty"], field="qty")
                    for f in today
                    if f["side"] == "BUY" and f["code"] == order["code"]
                ),
                Decimal(0),
            )
            if quantity >= Decimal(order["requested_qty"]):
                state = "FILLED"
            elif quantity > 0:
                state = "PARTIAL_FILL"
            elif order.get("state") == "NO_FILL" and order.get("source_observation_id"):
                state = "NO_FILL"
            else:
                state = "EXECUTION_UNKNOWN"
            order_states.append({**order, "state": state, "observed_qty": str(quantity)})
        if order_states:
            states = {row["state"] for row in order_states}
            status = (
                "EXECUTION_UNKNOWN"
                if "EXECUTION_UNKNOWN" in states
                else "PARTIAL_FILL"
                if "PARTIAL_FILL" in states
                else "NO_FILL"
                if states == {"NO_FILL"}
                else "FILLED"
            )
            if "PARTIAL_FILL" in states:
                persistent.add("INCOMPLETE_ORDER_EVIDENCE")
        sectors.update(declaration.get("sectors", {}))
        execution_status = status
        if research_status in {"TASK_FAILED", "DATA_UNKNOWN"}:
            status = research_status
        if status in {"TASK_FAILED", "DATA_UNKNOWN", "EXECUTION_UNKNOWN"}:
            persistent.add("EXECUTION_COVERAGE_UNKNOWN")
        daily_missing = set(persistent)
        cash_return = cash * Decimal(policy["cash_daily_return"]) if cash is not None else None
        if cash is not None:
            cash += cash_return
        turnover = Decimal(0)
        for f in today:
            if f["side"] not in {"BUY", "SELL"}:
                persistent.add("UNRESOLVED_CASH_EVENT")
                cash = None
                continue
            code = f["code"]
            amount = _decimal(f["amount"], field="amount")
            fee = _fees(f)
            turnover += amount
            if f["side"] == "BUY":
                if code not in positions:
                    positions[code] = _episode(policy["account_hash"], code, f)
                _add_buy(positions[code], f)
                if cash is not None and fee is not None:
                    cash -= amount + fee
            elif code not in positions or not _consume_sell(positions[code], f):
                persistent.add("UNMATCHED_OPENING_INVENTORY")
                cash = None
            elif cash is not None and fee is not None:
                cash += amount - fee
            if fee is None:
                persistent.add("FEES_MISSING")
                cash = None
        daily_missing.update(persistent)
        active = {
            code: sum((lot["remaining"] for lot in ep["lots"]), Decimal(0))
            for code, ep in positions.items()
        }
        active = {code: qty for code, qty in active.items() if qty > 0}
        values = {}
        for code, qty in active.items():
            value = marks.get((day, code))
            if value is None:
                daily_missing.add("MISSING_MARK")
            else:
                values[code] = qty * _decimal(value, field="mark")
        market_value = sum(values.values(), Decimal(0)) if len(values) == len(active) else None
        basis = [
            lot["remaining"] * lot["basis_per_qty"] if lot["basis_per_qty"] is not None else None
            for ep in positions.values()
            for lot in ep["lots"]
        ]
        unrealized = (
            market_value - sum(basis, Decimal(0))
            if market_value is not None and all(x is not None for x in basis)
            else None
        )
        known_cash = cash if not persistent else None
        nav = (
            known_cash + sum(values.values(), Decimal(0))
            if known_cash is not None and len(values) == len(active)
            else None
        )
        realized = [_finish(ep)["realized_pnl"] for ep in positions.values() if ep["sell_qty"] > 0]
        total_realized = sum(realized, Decimal(0)) if all(x is not None for x in realized) else None
        daily_realized = (
            total_realized - cumulative_realized
            if total_realized is not None and cumulative_realized is not None
            else None
        )
        cumulative_realized = total_realized
        breaches = []
        if cash is not None and cash < 0:
            breaches.append("NEGATIVE_CASH")
        if len(active) > policy["max_positions"]:
            breaches.append("MAX_POSITIONS")
        weights = [v / nav for v in values.values()] if nav is not None and nav > 0 else []
        if weights and max(weights) > Decimal(policy["max_weight"]):
            breaches.append("MAX_WEIGHT")
        sector_values = defaultdict(Decimal)
        for code, value in values.items():
            if sectors.get(code):
                sector_values[sectors[code]] += value

        def out(value):
            return format(value, "f") if value is not None else None

        due_dates = dict(declaration.get("exit_dates", {}))
        for window in declaration.get("plan_windows", []):
            code = window["code"]
            if (
                code in positions
                and positions[code].get("active_entry_session") == window["entry_date"]
            ):
                due_dates[code] = window["exit_date"]
        rows.append(
            {
                "session": day,
                "evidence_mode": evidence_mode,
                "status": status,
                "research_status": research_status,
                "execution_status": execution_status,
                "order_states": order_states,
                "cash": out(known_cash),
                "cash_return": out(cash_return) if not persistent else None,
                "positions": {k: out(v) for k, v in active.items()},
                "nav": out(nav),
                "net_return": out(nav / previous_nav - 1)
                if nav is not None and previous_nav is not None and previous_nav > 0
                else None,
                "realized_pnl": out(daily_realized),
                "unrealized_pnl": out(unrealized),
                "market_value_change": out(market_value - previous_market_value)
                if market_value is not None and previous_market_value is not None
                else None,
                "market_value": out(sum(values.values(), Decimal(0)))
                if len(values) == len(active)
                else None,
                "turnover": out(turnover / previous_nav)
                if previous_nav is not None and previous_nav > 0
                else None,
                "max_position_weight": out(max(weights))
                if weights
                else "0"
                if not active and nav is not None
                else None,
                "sector_coverage": {
                    "known_positions": sum(bool(sectors.get(code)) for code in active),
                    "positions": len(active),
                },
                "max_sector_weight": out(max(sector_values.values()) / nav)
                if nav and all(sectors.get(code) for code in active) and sector_values
                else None,
                "holding_window_breached": any(
                    code in active and day >= due for code, due in due_dates.items()
                ),
                "constraint_breaches": breaches,
                "missing_reasons": sorted(daily_missing),
            }
        )
        previous_nav = nav
        previous_market_value = market_value
    complete = all(row["net_return"] is not None for row in rows)
    returns = [Decimal(r["net_return"]) for r in rows if r["net_return"] is not None]
    drawdowns, peak = [], initial
    if complete:
        for r in rows:
            peak = max(peak, Decimal(r["nav"]))
            drawdowns.append(Decimal(r["nav"]) / peak - 1)
    return {
        "schema_version": 1,
        "evidence_mode": evidence_mode,
        "days": rows,
        "summary": {
            "n_days": len(rows),
            "n_marked_days": sum(r["nav"] is not None for r in rows),
            "n_return_days": len(returns),
            "complete": complete,
            "total_return": out(Decimal(rows[-1]["nav"]) / initial - 1) if complete else None,
            "max_drawdown": out(min(drawdowns)) if complete else None,
            "worst_day": out(min(returns)) if complete else None,
            "tail_mean_5pct": out(
                sum(sorted(returns)[: max(1, math.ceil(len(returns) * 0.05))])
                / max(1, math.ceil(len(returns) * 0.05))
            )
            if complete
            else None,
            "turnover": out(sum(Decimal(r["turnover"]) for r in rows)) if complete else None,
            "cash_days": sum(not r["positions"] and r["cash"] is not None for r in rows),
            "benchmark_return": out((1 + Decimal(policy["cash_daily_return"])) ** len(rows) - 1),
            "benchmark": "CASH",
            "cost_model_version": policy["cost_model_version"],
            "status_counts": {
                s: sum(r["status"] == s for r in rows) for s in sorted({r["status"] for r in rows})
            },
        },
    }
