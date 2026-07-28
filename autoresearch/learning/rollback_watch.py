#!/usr/bin/env python3
"""Post-activation five-guard observation and rollback recommendations."""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from autoresearch.learning import experiment_registry as registry
from autoresearch.learning.promotion import evaluate_guards


SCHEMA_VERSION = 1


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


def _assessment(
    record: dict,
    facts: object,
    *,
    run_id: str,
    observed_at: str,
    observed_before: list[dict],
) -> dict:
    if not isinstance(facts, dict):
        raise registry.RegistryError("rollback observation facts must be an object")
    metrics = facts.get("metrics")
    guards = evaluate_guards(record["rollback_guards"], metrics)
    states = {item["status"] for item in guards.values()}
    status = "FAIL" if "FAIL" in states else (
        "UNKNOWN" if "UNKNOWN" in states else "PASS"
    )
    observed = len(observed_before) + 1
    required = int(record["rollback_window_runs"])
    all_pass = status == "PASS" and all(
        item.get("status") == "PASS" for item in observed_before
    )
    expired = date.fromisoformat(observed_at[:10]) > date.fromisoformat(
        record["expires_date"]
    )
    reason = None
    if expired:
        status = "FAIL"
        recommendation = "ROLLBACK"
        reason = "EXPERIMENT_EXPIRED"
    elif status == "FAIL":
        recommendation = "ROLLBACK"
        reason = "GUARD_BREACH"
    elif status == "PASS" and observed >= required and all_pass:
        recommendation = "ACCEPT_BASELINE"
    else:
        recommendation = "CONTINUE_OBSERVATION"
        reason = "GUARD_UNKNOWN" if status == "UNKNOWN" else "WINDOW_INCOMPLETE"
    input_hash = registry.canonical_hash(
        {
            "experiment_id": record["id"],
            "definition_hash": record["definition_hash"],
            "run_id": run_id,
            "facts": facts,
        }
    )
    assessment = {
        "schema_version": SCHEMA_VERSION,
        "experiment_id": record["id"],
        "definition_hash": record["definition_hash"],
        "run_id": run_id,
        "observed_at": observed_at,
        "input_hash": input_hash,
        "status": status,
        "recommendation": recommendation,
        "reason": reason,
        "guards": guards,
        "window": {
            "required": required,
            "observed": observed,
            "all_pass": all_pass,
        },
        "rollback_pointer": record["rollback_pointer"],
    }
    assessment["assessment_hash"] = registry.canonical_hash(assessment)
    return assessment


def observe(
    registry_path: Path | str,
    experiment_id: str,
    facts: dict,
    *,
    run_id: str,
    observed_at: str | None = None,
    json_out: Path | str | None = None,
) -> dict:
    """Record one distinct run; recommend only, never apply rollback."""
    run = registry._nonempty(run_id, "run_id")
    at = observed_at or datetime.now().astimezone().isoformat(timespec="seconds")
    at_dt = registry._parse_timestamp(at, "observed_at")
    record = registry.get_experiment(registry_path, experiment_id)
    if record["status"] != "ACTIVE":
        raise registry.RegistryError(
            f"rollback observation requires ACTIVE, got {record['status']}"
        )
    activated = registry._parse_timestamp(record["activated_at"], "activated_at")
    if at_dt < activated:
        raise registry.RegistryError("observation timestamp cannot predate activation")
    prior = list(record.get("observations") or [])
    input_hash = registry.canonical_hash(
        {
            "experiment_id": record["id"],
            "definition_hash": record["definition_hash"],
            "run_id": run,
            "facts": facts,
        }
    )
    replay = next((item for item in prior if item.get("run_id") == run), None)
    if replay is not None:
        if replay.get("input_hash") != input_hash:
            raise registry.RegistryError(
                f"run {run!r} already has a different observation"
            )
        if json_out is not None:
            _atomic_json(json_out, replay)
        return replay
    assessment = _assessment(
        record,
        facts,
        run_id=run,
        observed_at=at,
        observed_before=prior,
    )

    def mutate(payload: dict, current: dict):
        if current["status"] != "ACTIVE":
            raise registry.RegistryError(
                f"rollback observation requires ACTIVE, got {current['status']}"
            )
        current.setdefault("observations", []).append(assessment)
        current["latest_rollback_assessment"] = assessment
        if assessment["recommendation"] == "ROLLBACK":
            current["status"] = "ROLLBACK_RECOMMENDED"
            event = "ROLLBACK_RECOMMENDED"
        elif assessment["recommendation"] == "ACCEPT_BASELINE":
            current["status"] = "STABLE_CANDIDATE"
            event = "STABLE_BASELINE_RECOMMENDED"
        else:
            event = "ROLLBACK_OBSERVED"
        registry._audit(
            payload,
            event=event,
            at=at,
            actor="system",
            experiment_id=experiment_id,
            details={
                "run_id": run,
                "assessment_hash": assessment["assessment_hash"],
                "status": assessment["status"],
                "recommendation": assessment["recommendation"],
            },
        )
        return assessment

    _, result = registry.update_experiment(
        registry_path,
        experiment_id,
        mutate,
    )
    if json_out is not None:
        _atomic_json(json_out, result)
    return result
