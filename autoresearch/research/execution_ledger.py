#!/usr/bin/env python3
"""Turn observed broker fills into deterministic long-only FIFO position episodes."""
from __future__ import annotations

import hashlib
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
    }


def _add_buy(ep: dict, row: dict) -> None:
    qty = _decimal(row["qty"], field="qty")
    amount = _decimal(row["amount"], field="amount")
    if qty <= 0 or amount <= 0:
        raise ValueError("buy qty and amount must be positive")
    fee = _fees(row)
    ep["fees_missing"] |= fee is None
    ep["lots"].append({"remaining": qty, "notional_per_qty": amount / qty,
                       "basis_per_qty": None if fee is None else (amount + fee) / qty})
    ep["buy_qty"] += qty


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


__all__ = ["build_episodes"]
