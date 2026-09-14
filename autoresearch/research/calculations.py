"""Registered, deterministic research calculations with replayable evidence."""

from __future__ import annotations

import inspect
import json
from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from autoresearch.common.atomic import atomic_write_json, canonical_json, sha256_bytes
from autoresearch.common.execution_context import current_execution_context
from autoresearch.common.uzi_lenses import dcf_sensitivity, simple_dcf
from autoresearch.contracts.calculation import (
    CALCULATOR_IDS,
    calculation_id,
    validate_calculation,
)


class CalculationInputError(ValueError):
    """A registered calculation received incomparable or incomplete inputs."""


def _exact(value: dict, fields: set[str], name: str) -> None:
    if not isinstance(value, dict) or set(value) != fields:
        raise CalculationInputError(
            f"{name} fields differ: missing={sorted(fields - set(value or {}))}, "
            f"unknown={sorted(set(value or {}) - fields)}"
        )


def _decimal(value: object, name: str, *, positive: bool = False) -> Decimal:
    if not isinstance(value, str):
        raise CalculationInputError(f"{name} must be a decimal string")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise CalculationInputError(f"{name} is not decimal") from exc
    if not result.is_finite() or (positive and result <= 0):
        raise CalculationInputError(f"{name} is outside its valid range")
    return result


def _ratio(numerator: Decimal, denominator: Decimal, name: str) -> str:
    if denominator == 0:
        raise CalculationInputError(f"{name} denominator is zero")
    return str(numerator / denominator)


def _money(value: object, name: str) -> tuple[Decimal, dict]:
    _exact(value, {"value", "period_basis", "currency", "unit"}, name)
    assert isinstance(value, dict)
    if value["period_basis"] not in {"single_period", "cumulative"}:
        raise CalculationInputError(f"{name} period_basis is invalid")
    currency = value["currency"]
    if not isinstance(currency, str) or len(currency) != 3 or not currency.isupper():
        raise CalculationInputError(f"{name} currency is invalid")
    if value["unit"] not in {"units", "thousand", "million", "billion"}:
        raise CalculationInputError(f"{name} unit is invalid")
    return _decimal(value["value"], f"{name}.value"), value


def _financial_period_ratios(inputs: dict, parameters: dict) -> tuple[dict, dict]:
    allowed = {"revenue", "profit", "previous_revenue", "previous_profit"}
    if not {"revenue", "profit"} <= set(inputs) or set(inputs) - allowed:
        raise CalculationInputError("financial inputs require revenue/profit only")
    _exact(parameters, {"period_start", "period_end", "shares_basis"}, "parameters")
    start = date.fromisoformat(parameters["period_start"])
    end = date.fromisoformat(parameters["period_end"])
    if end < start:
        raise CalculationInputError("financial period ends before it starts")
    revenue, revenue_meta = _money(inputs["revenue"], "revenue")
    profit, profit_meta = _money(inputs["profit"], "profit")
    comparable = ("period_basis", "currency", "unit")
    for field in comparable:
        if revenue_meta[field] != profit_meta[field]:
            raise CalculationInputError(f"financial {field} mismatch")
    prior_keys = {"previous_revenue", "previous_profit"} & set(inputs)
    if prior_keys and prior_keys != {"previous_revenue", "previous_profit"}:
        raise CalculationInputError("previous revenue and profit must be paired")
    values = {"net_margin": _ratio(profit, revenue, "net_margin")}
    if prior_keys:
        previous_revenue, previous_revenue_meta = _money(
            inputs["previous_revenue"], "previous_revenue"
        )
        previous_profit, previous_profit_meta = _money(inputs["previous_profit"], "previous_profit")
        for current, previous, name in (
            (revenue_meta, previous_revenue_meta, "revenue"),
            (profit_meta, previous_profit_meta, "profit"),
        ):
            for field in comparable:
                if current[field] != previous[field]:
                    raise CalculationInputError(f"previous {name} {field} mismatch")
        values.update(
            {
                "revenue_growth": _ratio(
                    revenue - previous_revenue, previous_revenue, "revenue_growth"
                ),
                "profit_growth": _ratio(profit - previous_profit, previous_profit, "profit_growth"),
            }
        )
    return values, {
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "period_basis": revenue_meta["period_basis"],
        "currency": revenue_meta["currency"],
        "unit": revenue_meta["unit"],
        "shares_basis": parameters["shares_basis"],
    }


def _aware(value: object, name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError as exc:
        raise CalculationInputError(f"{name} is not an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CalculationInputError(f"{name} must be timezone-aware")
    return parsed


def _ah_premium(inputs: dict, parameters: dict) -> tuple[dict, dict]:
    _exact(inputs, {"a_price", "h_price", "fx"}, "inputs")
    _exact(parameters, {"max_time_skew_seconds"}, "parameters")
    skew = parameters["max_time_skew_seconds"]
    if type(skew) is not int or not 0 <= skew <= 86400:
        raise CalculationInputError("max_time_skew_seconds is invalid")
    prices = {}
    times = []
    basis = None
    for key, currency in (("a_price", "CNY"), ("h_price", "HKD")):
        row = inputs[key]
        _exact(row, {"value", "currency", "as_of", "share_basis"}, key)
        if row["currency"] != currency:
            raise CalculationInputError(f"{key} currency must be {currency}")
        if not isinstance(row["share_basis"], str) or not row["share_basis"]:
            raise CalculationInputError(f"{key} share_basis is required")
        if basis is not None and row["share_basis"] != basis:
            raise CalculationInputError("A/H share basis mismatch")
        basis = row["share_basis"]
        prices[key] = _decimal(row["value"], f"{key}.value", positive=True)
        times.append(_aware(row["as_of"], f"{key}.as_of"))
    fx = inputs["fx"]
    _exact(fx, {"value", "pair", "as_of"}, "fx")
    if fx["pair"] != "HKD/CNY":
        raise CalculationInputError("fx pair must be HKD/CNY")
    fx_value = _decimal(fx["value"], "fx.value", positive=True)
    times.append(_aware(fx["as_of"], "fx.as_of"))
    if (max(times) - min(times)).total_seconds() > skew:
        raise CalculationInputError("A/H/FX quote times exceed allowed skew")
    premium = prices["a_price"] / (prices["h_price"] * fx_value) - Decimal(1)
    return {"premium": str(premium)}, {
        "share_basis": basis,
        "max_time_skew_seconds": skew,
        "quote_times": [value.isoformat() for value in times],
        "fx_pair": "HKD/CNY",
    }


def _conditional_base_rates(inputs: dict, parameters: dict) -> tuple[dict, dict]:
    _exact(inputs, {"observations"}, "inputs")
    _exact(parameters, {"window_start", "window_end", "overlap_policy"}, "parameters")
    start = date.fromisoformat(parameters["window_start"])
    end = date.fromisoformat(parameters["window_end"])
    if end < start:
        raise CalculationInputError("base-rate window ends before it starts")
    if parameters["overlap_policy"] not in {
        "disjoint_observations",
        "overlap_disclosed",
    }:
        raise CalculationInputError("overlap_policy is invalid")
    rows = inputs["observations"]
    if not isinstance(rows, list) or not rows:
        raise CalculationInputError("observations must be a non-empty list")
    seen = set()
    normalized = []
    for row in rows:
        _exact(row, {"observation_id", "date", "condition", "outcome"}, "observation")
        identity = row["observation_id"]
        observed = date.fromisoformat(row["date"])
        if not isinstance(identity, str) or not identity or identity in seen:
            raise CalculationInputError("observation ids must be unique")
        if not start <= observed <= end:
            raise CalculationInputError("observation is outside the declared window")
        if type(row["condition"]) is not bool or type(row["outcome"]) is not bool:
            raise CalculationInputError("condition/outcome must be boolean")
        seen.add(identity)
        normalized.append(row)
    conditional = [row for row in normalized if row["condition"]]
    outcomes = [row for row in normalized if row["outcome"]]
    joint = [row for row in conditional if row["outcome"]]
    values = {
        "n_total": len(normalized),
        "n_condition": len(conditional),
        "n_outcome": len(outcomes),
        "n_condition_and_outcome": len(joint),
        "base_rate": _ratio(Decimal(len(outcomes)), Decimal(len(normalized)), "base_rate"),
        "conditional_rate": _ratio(
            Decimal(len(joint)), Decimal(len(conditional)), "conditional_rate"
        ),
    }
    return values, {
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "overlap_policy": parameters["overlap_policy"],
        "observation_ids_sha256": sha256_bytes(canonical_json(sorted(seen)).encode()),
    }


def _dcf(inputs: dict, parameters: dict) -> tuple[dict, dict]:
    _exact(inputs, {"fcf_base", "net_debt", "shares"}, "inputs")
    _exact(
        parameters,
        {"waccs", "growths", "terminal_growth", "years"},
        "parameters",
    )
    fcf, fcf_meta = _money(inputs["fcf_base"], "fcf_base")
    debt, debt_meta = _money(inputs["net_debt"], "net_debt")
    for field in ("period_basis", "currency", "unit"):
        if fcf_meta[field] != debt_meta[field]:
            raise CalculationInputError(f"DCF fcf/net_debt {field} mismatch")
    shares = inputs["shares"]
    _exact(shares, {"value", "unit", "shares_basis"}, "shares")
    if shares["unit"] not in {"shares", "thousand_shares", "million_shares"}:
        raise CalculationInputError("shares unit is invalid")
    if not isinstance(shares["shares_basis"], str) or not shares["shares_basis"]:
        raise CalculationInputError("shares_basis is required")
    share_value = _decimal(shares["value"], "shares.value", positive=True)
    waccs = [_decimal(value, "wacc", positive=True) for value in parameters["waccs"]]
    growths = [_decimal(value, "growth") for value in parameters["growths"]]
    terminal = _decimal(parameters["terminal_growth"], "terminal_growth")
    years = parameters["years"]
    if type(years) is not int or not 1 <= years <= 20:
        raise CalculationInputError("DCF years is invalid")
    if not waccs or not growths or any(value <= terminal for value in waccs):
        raise CalculationInputError("DCF requires non-empty grids and WACC above terminal growth")
    values = dcf_sensitivity(
        float(fcf),
        float(share_value),
        float(debt),
        [float(value) for value in waccs],
        [float(value) for value in growths],
        terminal_growth=float(terminal),
        years=years,
    )
    return values, {
        "currency": fcf_meta["currency"],
        "money_unit": fcf_meta["unit"],
        "period_basis": fcf_meta["period_basis"],
        "shares_unit": shares["unit"],
        "shares_basis": shares["shares_basis"],
        "rounding": "uzi_lenses.simple_dcf.v1:2dp",
    }


_CALCULATORS: dict[str, tuple[Callable, tuple[Callable, ...]]] = {
    "financial_period_ratios.v1": (_financial_period_ratios, ()),
    "ah_premium.v1": (_ah_premium, ()),
    "conditional_base_rates.v1": (_conditional_base_rates, ()),
    "dcf_sensitivity.v1": (_dcf, (dcf_sensitivity, simple_dcf)),
}
if frozenset(_CALCULATORS) != CALCULATOR_IDS:  # pragma: no cover - import-time contract guard
    raise RuntimeError("calculator registry differs from calculation contract")


def calculator_ids() -> tuple[str, ...]:
    return tuple(_CALCULATORS)


def _code_hash(calculator_id: str) -> str:
    handler, dependencies = _CALCULATORS[calculator_id]
    source = "\n".join(inspect.getsource(item) for item in (handler, *dependencies))
    return sha256_bytes(source.encode("utf-8"))


def _refs(inputs: dict, input_refs: list[dict] | None) -> list[dict]:
    if input_refs is None:
        return [
            {
                "artifact_id": "inline.inputs",
                "sha256": sha256_bytes(canonical_json(inputs).encode("utf-8")),
            }
        ]
    result = []
    for ref in input_refs:
        _exact(ref, {"artifact_id", "sha256"}, "input_ref")
        if not isinstance(ref["artifact_id"], str) or not ref["artifact_id"]:
            raise CalculationInputError("input artifact_id is required")
        if not isinstance(ref["sha256"], str) or len(ref["sha256"]) != 64:
            raise CalculationInputError("input sha256 is invalid")
        int(ref["sha256"], 16)
        result.append(dict(ref))
    if not result or len({row["artifact_id"] for row in result}) != len(result):
        raise CalculationInputError("input refs must be non-empty and unique")
    return result


def calculate(
    calculator_id: str,
    inputs: dict,
    parameters: dict,
    *,
    input_refs: list[dict] | None = None,
    task_id: str | None = None,
    attempt: int | None = None,
) -> dict:
    """Run one registered calculator; invalid business inputs become frozen failures."""
    if calculator_id not in _CALCULATORS:
        raise KeyError(f"unregistered calculator: {calculator_id}")
    if not isinstance(inputs, dict) or not isinstance(parameters, dict):
        raise CalculationInputError("inputs and parameters must be objects")
    execution = current_execution_context()
    resolved_task = task_id or (execution.task_id if execution is not None else "unbound")
    resolved_attempt = attempt or (execution.attempt if execution is not None else 1)
    if type(resolved_attempt) is not int or resolved_attempt < 1:
        raise CalculationInputError("attempt must be positive")
    refs = _refs(inputs, input_refs)
    error = None
    values = {}
    assumptions = {}
    try:
        values, assumptions = _CALCULATORS[calculator_id][0](inputs, parameters)
        status = "SUCCEEDED"
    except (CalculationInputError, TypeError, ValueError, InvalidOperation) as exc:
        status = "FAILED"
        error = {"category": type(exc).__name__, "message": str(exc)}
    result = {
        "schema_version": 1,
        "calculation_id": "0" * 64,
        "calculator_id": calculator_id,
        "calculator_version": calculator_id.rsplit(".", maxsplit=1)[-1],
        "code_hash": _code_hash(calculator_id),
        "task_id": resolved_task,
        "attempt": resolved_attempt,
        "input_refs": refs,
        "parameters": parameters,
        "values": values,
        "assumptions": assumptions,
        "status": status,
        "error": error,
    }
    result["calculation_id"] = calculation_id(result)
    json.loads(canonical_json(result))
    return validate_calculation(result)


def replay_calculation(result: dict, inputs: dict, parameters: dict) -> dict:
    if result.get("code_hash") != _code_hash(str(result.get("calculator_id"))):
        raise RuntimeError("calculation code identity does not match this runtime")
    replayed = calculate(
        result["calculator_id"],
        inputs,
        parameters,
        input_refs=result["input_refs"],
        task_id=result["task_id"],
        attempt=result["attempt"],
    )
    if replayed != result:
        raise RuntimeError("calculation replay mismatch")
    return replayed


def persist_calculation(handle, result: dict) -> Path:
    validate_calculation(result)
    target = Path(handle.capsule) / "evidence/calculations" / f"{result['calculation_id']}.json"
    if target.is_file():
        if json.loads(target.read_text(encoding="utf-8")) != result:
            raise RuntimeError("frozen calculation changed")
        return target
    atomic_write_json(target, result)
    return target


__all__ = [
    "CalculationInputError",
    "calculate",
    "calculator_ids",
    "persist_calculation",
    "replay_calculation",
    "validate_calculation",
]
