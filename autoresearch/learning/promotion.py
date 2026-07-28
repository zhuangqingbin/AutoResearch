#!/usr/bin/env python3
"""Five-guard experiment promotion evaluator.

Evaluation may recommend promotion.  It never records human approval, activates
an experiment, or mutates production configuration.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

from autoresearch.learning import experiment_registry as registry


SCHEMA_VERSION = 1
_MISSING = object()


def _metric(metrics: object, dotted: str):
    current = metrics
    for part in dotted.split("."):
        if not isinstance(current, dict) or part not in current:
            return _MISSING
        current = current[part]
    return current


def _compare(actual: object, op: str, threshold: float) -> bool | None:
    if actual is _MISSING or actual is None:
        return None
    if not isinstance(actual, (int, float)):
        return None
    if op == "gt":
        return actual > threshold
    if op == "gte":
        return actual >= threshold
    if op == "lt":
        return actual < threshold
    if op == "lte":
        return actual <= threshold
    if op == "eq":
        return actual == threshold
    raise registry.RegistryError(f"unknown guard operator {op!r}")


def _condition_status(result: bool | None) -> str:
    return "UNKNOWN" if result is None else ("PASS" if result else "FAIL")


def evaluate_guards(guards: dict, metrics: object) -> dict:
    """Evaluate five independent domains; FAIL outranks UNKNOWN within a domain."""
    out = {}
    for domain in registry.GUARD_DOMAINS:
        rows = []
        for condition in guards[domain]:
            actual = _metric(metrics, condition["metric"])
            result = _compare(actual, condition["op"], condition["value"])
            rows.append(
                {
                    "metric": condition["metric"],
                    "op": condition["op"],
                    "threshold": condition["value"],
                    "actual": None if actual is _MISSING else actual,
                    "status": _condition_status(result),
                }
            )
        states = {row["status"] for row in rows}
        status = "FAIL" if "FAIL" in states else (
            "UNKNOWN" if "UNKNOWN" in states else "PASS"
        )
        out[domain] = {"status": status, "conditions": rows}
    return out


def evaluate_maturity(minimums: dict, sample: object) -> dict:
    sample = sample if isinstance(sample, dict) else {}
    regimes_raw = sample.get("regimes")
    regimes = (
        sorted({str(value) for value in regimes_raw if str(value)})
        if isinstance(regimes_raw, list)
        else []
    )

    def _count(key: str) -> int:
        try:
            return max(0, int(sample.get(key, 0)))
        except (TypeError, ValueError):
            return 0

    observed = {
        "forward_days": _count("forward_days"),
        "mature_events": _count("mature_events"),
        "unique_events": _count("unique_events"),
        "regimes": len(regimes),
    }
    shortfalls = {
        key: {"need": int(need), "have": observed[key]}
        for key, need in minimums.items()
        if observed[key] < int(need)
    }
    return {
        "status": "IMMATURE" if shortfalls else "MATURE",
        "minimums": dict(minimums),
        "observed": observed,
        "regime_values": regimes,
        "shortfalls": shortfalls,
    }


def _explicit_improvements(record: dict, guards: dict) -> list[str]:
    primary = record["primary_metric"]
    for domain in registry.GUARD_DOMAINS:
        for row in guards[domain]["conditions"]:
            if row["metric"] != primary or row["status"] != "PASS":
                continue
            actual = row["actual"]
            if row["op"] in {"gt", "gte"} and actual > 0:
                return [primary]
            if row["op"] in {"lt", "lte"} and actual < 0:
                return [primary]
    return []


def _atomic_json(path: Path | str, payload: dict) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f"{target.name}.tmp")
    temp.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    temp.replace(target)
    return target


def _evaluation(record: dict, facts: object, evaluated_at: str) -> dict:
    if not isinstance(facts, dict):
        raise registry.RegistryError("evaluation facts must be an object")
    sample = facts.get("sample")
    metrics = facts.get("metrics")
    maturity = evaluate_maturity(record["minimums"], sample)
    guards = evaluate_guards(record["promotion_guards"], metrics)
    improvements = _explicit_improvements(record, guards)
    sample = sample if isinstance(sample, dict) else {}
    try:
        trial_count = max(0, int(sample.get("trial_count", 0)))
    except (TypeError, ValueError):
        trial_count = 0
    breakpoints = sample.get("definition_breakpoints")
    if not isinstance(breakpoints, list):
        breakpoints = []
    breakpoints = [str(value) for value in breakpoints]

    try:
        expired = date.fromisoformat(evaluated_at[:10]) > date.fromisoformat(
            record["expires_date"]
        )
    except (TypeError, ValueError) as exc:
        raise registry.RegistryError("evaluated_at must start with YYYY-MM-DD") from exc

    domain_states = {item["status"] for item in guards.values()}
    reason = None
    if expired:
        status = "EXPIRED"
        recommendation = "DO_NOT_PROMOTE"
        reason = "EXPERIMENT_EXPIRED"
    elif maturity["status"] == "IMMATURE":
        status = "IMMATURE"
        recommendation = "CONTINUE_SHADOW"
        reason = "SAMPLE_IMMATURE"
    elif "FAIL" in domain_states:
        status = "FAIL"
        recommendation = "REJECT"
        reason = "GUARD_BREACH"
    elif "UNKNOWN" in domain_states:
        status = "UNKNOWN"
        recommendation = "CONTINUE_SHADOW"
        reason = "GUARD_UNKNOWN"
    elif not improvements:
        status = "FAIL"
        recommendation = "REJECT"
        reason = "NO_EXPLICIT_IMPROVEMENT"
    else:
        status = "PASS"
        recommendation = "PROMOTE"

    primary = _metric(metrics, record["primary_metric"])
    payload = {
        "schema_version": SCHEMA_VERSION,
        "experiment_id": record["id"],
        "definition_hash": record["definition_hash"],
        "evaluated_at": evaluated_at,
        "status": status,
        "recommendation": recommendation,
        "reason": reason,
        "maturity": maturity,
        "guards": guards,
        "primary_metric": {
            "metric": record["primary_metric"],
            "actual": None if primary is _MISSING else primary,
        },
        "improvements": improvements,
        "trial_count": trial_count,
        "multiple_testing_disclosure": trial_count > 1,
        "definition_breakpoints": breakpoints,
        "rollback_pointer": record["rollback_pointer"],
    }
    payload["evaluation_hash"] = registry.canonical_hash(payload)
    return payload


def evaluate_experiment(
    registry_path: Path | str,
    experiment_id: str,
    facts: dict,
    *,
    evaluated_at: str | None = None,
    json_out: Path | str | None = None,
) -> dict:
    at = evaluated_at or datetime.now().astimezone().isoformat(timespec="seconds")
    record = registry.get_experiment(registry_path, experiment_id)
    if record["status"] not in {"PREREGISTERED", "RECOMMENDED"}:
        raise registry.RegistryError(
            f"promotion evaluation not allowed from {record['status']}"
        )
    evaluation = _evaluation(record, facts, at)

    def mutate(payload: dict, current: dict):
        latest = current.get("latest_evaluation")
        if latest and latest.get("evaluation_hash") == evaluation["evaluation_hash"]:
            return latest
        current.setdefault("evaluation_history", []).append(evaluation)
        current["latest_evaluation"] = evaluation
        if evaluation["status"] == "PASS":
            current["status"] = "RECOMMENDED"
        elif evaluation["status"] == "EXPIRED":
            current["status"] = "EXPIRED"
        else:
            current["status"] = "PREREGISTERED"
        registry._audit(
            payload,
            event="PROMOTION_EVALUATED",
            at=at,
            actor="system",
            experiment_id=experiment_id,
            details={
                "evaluation_hash": evaluation["evaluation_hash"],
                "status": evaluation["status"],
                "recommendation": evaluation["recommendation"],
            },
        )
        return evaluation

    _, persisted = registry.update_experiment(
        registry_path,
        experiment_id,
        mutate,
    )
    if json_out is not None:
        _atomic_json(json_out, persisted)
    return persisted


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", default=str(registry.DEFAULT_REGISTRY))
    commands = parser.add_subparsers(dest="command", required=True)
    evaluate = commands.add_parser("evaluate", help="evaluate promotion guards")
    evaluate.add_argument("experiment_id")
    evaluate.add_argument("--facts", required=True)
    evaluate.add_argument("--evaluated-at")
    evaluate.add_argument("--json-out")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        facts = registry._read_json_object(args.facts, "promotion facts")
        result = evaluate_experiment(
            args.registry,
            args.experiment_id,
            facts,
            evaluated_at=args.evaluated_at,
            json_out=args.json_out,
        )
    except registry.RegistryError as exc:
        registry._print_json({"error": str(exc)}, file=sys.stderr)
        return 2
    registry._print_json(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
