#!/usr/bin/env python3
"""Preregistered experiment lifecycle and stable-baseline registry.

The registry is a deterministic control-plane artifact.  It records intent and
authority; it never edits scan configuration, weights, prompts, gates, ratings,
or production artifacts.
"""
from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable


SCHEMA_VERSION = 1
DEFAULT_REGISTRY = Path("context/learning/experiments/registry.json")
GUARD_DOMAINS = ("research", "decision", "token", "speed", "architecture")
OPS = {"gt", "gte", "lt", "lte", "eq"}
STATUSES = {
    "PREREGISTERED",
    "RECOMMENDED",
    "APPROVED",
    "ACTIVE",
    "STABLE_CANDIDATE",
    "ROLLBACK_RECOMMENDED",
    "ROLLED_BACK",
    "ACCEPTED",
    "EXPIRED",
}


class RegistryError(ValueError):
    """The registry or requested lifecycle transition violates its contract."""


def canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def canonical_hash(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _copy(value):
    return json.loads(json.dumps(value, ensure_ascii=False))


def _fresh_registry() -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "stable_baseline": None,
        "baseline_history": [],
        "experiments": {},
        "active_by_family": {},
        "audit": [],
    }


def _validate_registry(payload: object) -> dict:
    if not isinstance(payload, dict):
        raise RegistryError("invalid registry: root must be an object")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise RegistryError(
            f"invalid registry: unsupported schema_version "
            f"{payload.get('schema_version')!r}"
        )
    for key, expected in (
        ("baseline_history", list),
        ("experiments", dict),
        ("active_by_family", dict),
        ("audit", list),
    ):
        if not isinstance(payload.get(key), expected):
            raise RegistryError(f"invalid registry: {key} must be {expected.__name__}")
    stable = payload.get("stable_baseline")
    if stable is not None:
        _validate_pointer(stable, label="stable baseline", require_kind=False)
    for exp_id, record in payload["experiments"].items():
        if not isinstance(record, dict) or record.get("id") != exp_id:
            raise RegistryError(f"invalid registry: malformed experiment {exp_id!r}")
        if record.get("status") not in STATUSES:
            raise RegistryError(
                f"invalid registry: experiment {exp_id!r} has unknown status"
            )
    return payload


def load_registry(path: Path | str = DEFAULT_REGISTRY) -> dict:
    target = Path(path)
    if not target.exists():
        return _fresh_registry()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RegistryError(f"invalid registry: {exc}") from exc
    return _validate_registry(payload)


def write_registry(path: Path | str, payload: dict) -> Path:
    target = Path(path)
    _validate_registry(payload)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f"{target.name}.tmp")
    temp.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    temp.replace(target)
    return target


def _nonempty(value: object, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise RegistryError(f"{label} is required")
    return text


def _validate_hash(value: object, label: str = "content_hash") -> str:
    text = str(value or "")
    if not re.fullmatch(r"[0-9a-f]{64}", text):
        raise RegistryError(f"{label} must be a 64-character lowercase SHA-256")
    return text


def _validate_pointer(
    pointer: object,
    *,
    label: str,
    require_kind: bool = True,
) -> dict:
    if not isinstance(pointer, dict):
        raise RegistryError(f"{label} must be an object")
    if require_kind:
        _nonempty(pointer.get("kind"), f"{label}.kind")
    _nonempty(pointer.get("pointer"), f"{label}.pointer")
    _validate_hash(pointer.get("content_hash"), f"{label}.content_hash")
    return pointer


def _parse_day(value: object, label: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise RegistryError(f"{label} must be YYYY-MM-DD") from exc


def _parse_timestamp(value: object, label: str) -> datetime:
    text = _nonempty(value, label)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RegistryError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _validate_guards(guards: object, label: str) -> dict:
    if not isinstance(guards, dict) or set(guards) != set(GUARD_DOMAINS):
        raise RegistryError(
            f"{label} must define exactly the five guard domains "
            f"{', '.join(GUARD_DOMAINS)}"
        )
    for domain in GUARD_DOMAINS:
        conditions = guards.get(domain)
        if not isinstance(conditions, list) or not conditions:
            raise RegistryError(f"{label}.{domain} must be a non-empty list")
        for condition in conditions:
            if not isinstance(condition, dict):
                raise RegistryError(f"{label}.{domain} condition must be an object")
            _nonempty(condition.get("metric"), f"{label}.{domain}.metric")
            if condition.get("op") not in OPS:
                raise RegistryError(
                    f"{label}.{domain}.op must be one of {sorted(OPS)}"
                )
            if not isinstance(condition.get("value"), (int, float)):
                raise RegistryError(f"{label}.{domain}.value must be numeric")
    return guards


def _validate_minimums(value: object) -> dict:
    if not isinstance(value, dict):
        raise RegistryError("minimums must be an object")
    required = ("forward_days", "mature_events", "unique_events", "regimes")
    if set(value) != set(required):
        raise RegistryError(f"minimums must define exactly {', '.join(required)}")
    out = {}
    for key in required:
        try:
            out[key] = int(value[key])
        except (TypeError, ValueError) as exc:
            raise RegistryError(f"minimums.{key} must be an integer") from exc
        floor = 0 if key == "unique_events" else 1
        if out[key] < floor:
            raise RegistryError(f"minimums.{key} must be >= {floor}")
    return out


def _validated_spec(spec: object) -> dict:
    if not isinstance(spec, dict):
        raise RegistryError("experiment spec must be an object")
    required = {
        "id",
        "title",
        "trial_family",
        "definition",
        "start_date",
        "expires_date",
        "primary_metric",
        "promotion_guards",
        "rollback_guards",
        "challenger_pointer",
        "minimums",
        "rollback_window_runs",
    }
    missing = sorted(required - set(spec))
    extra = sorted(set(spec) - required)
    if missing or extra:
        raise RegistryError(
            f"experiment spec fields mismatch: missing={missing}, extra={extra}"
        )
    clean = _copy(spec)
    exp_id = _nonempty(clean["id"], "id")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", exp_id):
        raise RegistryError("id may contain only letters, digits, dot, underscore, hyphen")
    _nonempty(clean["title"], "title")
    _nonempty(clean["trial_family"], "trial_family")
    if not isinstance(clean["definition"], dict) or not clean["definition"]:
        raise RegistryError("definition must be a non-empty object")
    start = _parse_day(clean["start_date"], "start_date")
    expiry = _parse_day(clean["expires_date"], "expires_date")
    if expiry < start:
        raise RegistryError("expiry must not precede start_date")
    primary = _nonempty(clean["primary_metric"], "primary_metric")
    promotion = _validate_guards(clean["promotion_guards"], "promotion five guards")
    _validate_guards(clean["rollback_guards"], "rollback five guards")
    metrics = {
        condition["metric"]
        for conditions in promotion.values()
        for condition in conditions
    }
    if primary not in metrics:
        raise RegistryError("primary_metric must appear in promotion five guards")
    _validate_pointer(
        clean["challenger_pointer"],
        label="challenger_pointer",
    )
    clean["minimums"] = _validate_minimums(clean["minimums"])
    try:
        clean["rollback_window_runs"] = int(clean["rollback_window_runs"])
    except (TypeError, ValueError) as exc:
        raise RegistryError("rollback_window_runs must be an integer") from exc
    if clean["rollback_window_runs"] < 1:
        raise RegistryError("rollback_window_runs must be >= 1")
    return clean


def _audit(
    payload: dict,
    *,
    event: str,
    at: str,
    actor: str,
    experiment_id: str | None = None,
    details: dict | None = None,
) -> dict:
    entry = {
        "seq": len(payload["audit"]) + 1,
        "event": event,
        "at": _nonempty(at, "audit timestamp"),
        "actor": _nonempty(actor, "audit actor"),
        "experiment_id": experiment_id,
        "details": _copy(details or {}),
    }
    payload["audit"].append(entry)
    return entry


def set_stable_baseline(
    path: Path | str = DEFAULT_REGISTRY,
    *,
    name: str,
    pointer: str,
    content_hash: str,
    approved_by: str,
    approved_at: str | None = None,
    note: str = "",
) -> dict:
    payload = load_registry(path)
    at = approved_at or datetime.now().astimezone().isoformat(timespec="seconds")
    baseline = {
        "name": _nonempty(name, "baseline name"),
        "pointer": _nonempty(pointer, "baseline pointer"),
        "content_hash": _validate_hash(content_hash),
        "approved_by": _nonempty(approved_by, "approved_by"),
        "approved_at": _nonempty(at, "approved_at"),
        "note": str(note or ""),
    }
    current = payload.get("stable_baseline")
    if current == baseline:
        return _copy(current)
    if payload["active_by_family"]:
        raise RegistryError(
            "stable baseline cannot change while an active experiment exists"
        )
    if current is not None and current not in payload["baseline_history"]:
        payload["baseline_history"].append(_copy(current))
    payload["stable_baseline"] = baseline
    _audit(
        payload,
        event="STABLE_BASELINE_SET",
        at=at,
        actor=approved_by,
        details={
            "baseline": baseline,
            "previous": current,
        },
    )
    write_registry(path, payload)
    return _copy(baseline)


def register_experiment(
    path: Path | str,
    spec: dict,
    *,
    registered_at: str | None = None,
) -> dict:
    payload = load_registry(path)
    baseline = payload.get("stable_baseline")
    if baseline is None:
        raise RegistryError("stable baseline must be set before registration")
    clean = _validated_spec(spec)
    definition_hash = canonical_hash(clean)
    existing = payload["experiments"].get(clean["id"])
    if existing is not None:
        if existing.get("definition_hash") != definition_hash:
            raise RegistryError(
                f"experiment {clean['id']} definition hash changed"
            )
        return _copy(existing)
    at = registered_at or datetime.now().astimezone().isoformat(timespec="seconds")
    record = {
        **clean,
        "definition_hash": definition_hash,
        "registered_at": at,
        "status": "PREREGISTERED",
        "rollback_pointer": _copy(baseline),
        "latest_evaluation": None,
        "approval": None,
        "activated_at": None,
        "observations": [],
        "latest_rollback_assessment": None,
        "closed_at": None,
    }
    payload["experiments"][clean["id"]] = record
    _audit(
        payload,
        event="EXPERIMENT_PREREGISTERED",
        at=at,
        actor="system",
        experiment_id=clean["id"],
        details={
            "definition_hash": definition_hash,
            "rollback_pointer": baseline,
        },
    )
    write_registry(path, payload)
    return _copy(record)


def get_experiment(path: Path | str, experiment_id: str) -> dict:
    payload = load_registry(path)
    try:
        return _copy(payload["experiments"][experiment_id])
    except KeyError as exc:
        raise RegistryError(f"unknown experiment {experiment_id!r}") from exc


def update_experiment(
    path: Path | str,
    experiment_id: str,
    mutate: Callable[[dict, dict], object],
) -> tuple[dict, object]:
    """Internal atomic read/mutate/write primitive used by promotion/watch."""
    payload = load_registry(path)
    try:
        record = payload["experiments"][experiment_id]
    except KeyError as exc:
        raise RegistryError(f"unknown experiment {experiment_id!r}") from exc
    result = mutate(payload, record)
    _validate_registry(payload)
    write_registry(path, payload)
    return _copy(record), result


def approve_experiment(
    path: Path | str,
    experiment_id: str,
    *,
    approved_by: str,
    approved_at: str | None = None,
    approval_expires_at: str | None = None,
    note: str = "",
) -> dict:
    """Record explicit human approval without activating the experiment."""
    actor = _nonempty(approved_by, "approved_by")
    at = approved_at or datetime.now().astimezone().isoformat(timespec="seconds")
    at_dt = _parse_timestamp(at, "approved_at")
    expires = approval_expires_at or (
        at_dt + timedelta(days=7)
    ).isoformat(timespec="seconds")
    expires_dt = _parse_timestamp(expires, "approval_expires_at")
    if expires_dt <= at_dt:
        raise RegistryError("approval_expires_at must be after approved_at")

    def mutate(payload: dict, record: dict):
        approval = {
            "decision": "APPROVE",
            "approved_by": actor,
            "approved_at": at,
            "expires_at": expires,
            "note": str(note or ""),
            "evaluation_hash": (
                (record.get("latest_evaluation") or {}).get("evaluation_hash")
            ),
        }
        if record["status"] == "APPROVED" and record.get("approval") == approval:
            return None
        if record["status"] != "RECOMMENDED":
            raise RegistryError(
                f"approval requires RECOMMENDED, got {record['status']}"
            )
        if not approval["evaluation_hash"]:
            raise RegistryError("approval requires a persisted PASS evaluation")
        evaluated_at = _parse_timestamp(
            record["latest_evaluation"].get("evaluated_at"),
            "latest evaluation timestamp",
        )
        if at_dt < evaluated_at:
            raise RegistryError("approval timestamp cannot predate evaluation")
        if at_dt.date() > date.fromisoformat(record["expires_date"]):
            raise RegistryError("experiment expired before approval")
        record["approval"] = approval
        record["status"] = "APPROVED"
        _audit(
            payload,
            event="EXPERIMENT_APPROVED",
            at=at,
            actor=actor,
            experiment_id=experiment_id,
            details=approval,
        )
        return None

    record, _ = update_experiment(path, experiment_id, mutate)
    return record


def _baseline_addressable(payload: dict, pointer: dict) -> bool:
    candidates = [
        payload.get("stable_baseline"),
        *payload.get("baseline_history", []),
    ]
    return pointer in candidates


def activate_experiment(
    path: Path | str,
    experiment_id: str,
    *,
    activated_by: str,
    activated_at: str | None = None,
) -> dict:
    """Activate only an approved challenger; the stable baseline is untouched."""
    actor = _nonempty(activated_by, "activated_by")
    at = activated_at or datetime.now().astimezone().isoformat(timespec="seconds")
    at_dt = _parse_timestamp(at, "activated_at")

    def mutate(payload: dict, record: dict):
        if record["status"] == "ACTIVE":
            if payload["active_by_family"].get(record["trial_family"]) == experiment_id:
                return None
            raise RegistryError("active registry mapping is inconsistent")
        if record["status"] != "APPROVED":
            raise RegistryError(
                f"activation requires APPROVED, got {record['status']}"
            )
        approval = record.get("approval") or {}
        approval_expiry = _parse_timestamp(
            approval.get("expires_at"),
            "approval expires_at",
        )
        approval_at = _parse_timestamp(
            approval.get("approved_at"),
            "approval approved_at",
        )
        if at_dt < approval_at:
            raise RegistryError("activation timestamp cannot predate approval")
        if at_dt > approval_expiry:
            raise RegistryError("approval expired before activation")
        if at_dt.date() > date.fromisoformat(record["expires_date"]):
            raise RegistryError("experiment expired before activation")
        family = record["trial_family"]
        active = payload["active_by_family"].get(family)
        if active and active != experiment_id:
            raise RegistryError(
                f"trial family {family!r} already active as {active!r}"
            )
        if not _baseline_addressable(payload, record["rollback_pointer"]):
            raise RegistryError("rollback pointer is no longer addressable")
        record["status"] = "ACTIVE"
        record["activated_at"] = at
        record["activation"] = {
            "activated_by": actor,
            "activated_at": at,
            "approval_evaluation_hash": approval.get("evaluation_hash"),
            "rollback_pointer": _copy(record["rollback_pointer"]),
        }
        payload["active_by_family"][family] = experiment_id
        _audit(
            payload,
            event="EXPERIMENT_ACTIVATED",
            at=at,
            actor=actor,
            experiment_id=experiment_id,
            details=record["activation"],
        )
        return None

    record, _ = update_experiment(path, experiment_id, mutate)
    return record


def rollback_experiment(
    path: Path | str,
    experiment_id: str,
    *,
    rolled_back_by: str,
    rolled_back_at: str | None = None,
    note: str = "",
) -> dict:
    """Audit a human-applied rollback to the preserved stable pointer."""
    actor = _nonempty(rolled_back_by, "rolled_back_by")
    at = rolled_back_at or datetime.now().astimezone().isoformat(timespec="seconds")
    at_dt = _parse_timestamp(at, "rolled_back_at")

    def mutate(payload: dict, record: dict):
        if record["status"] != "ROLLBACK_RECOMMENDED":
            raise RegistryError(
                f"rollback requires ROLLBACK_RECOMMENDED, got {record['status']}"
            )
        assessment_at = _parse_timestamp(
            (record.get("latest_rollback_assessment") or {}).get("observed_at"),
            "latest rollback assessment timestamp",
        )
        if at_dt < assessment_at:
            raise RegistryError("rollback timestamp cannot predate recommendation")
        if not _baseline_addressable(payload, record["rollback_pointer"]):
            raise RegistryError("rollback pointer is no longer addressable")
        target = _copy(record["rollback_pointer"])
        record["rollback"] = {
            "rolled_back_by": actor,
            "rolled_back_at": at,
            "note": str(note or ""),
            "target": target,
        }
        record["status"] = "ROLLED_BACK"
        record["closed_at"] = at
        payload["active_by_family"].pop(record["trial_family"], None)
        _audit(
            payload,
            event="EXPERIMENT_ROLLED_BACK",
            at=at,
            actor=actor,
            experiment_id=experiment_id,
            details=record["rollback"],
        )
        return None

    record, _ = update_experiment(path, experiment_id, mutate)
    return record


def accept_experiment_as_baseline(
    path: Path | str,
    experiment_id: str,
    *,
    accepted_by: str,
    accepted_at: str | None = None,
    note: str = "",
) -> dict:
    """Explicitly accept a clean observed challenger as the new stable baseline."""
    actor = _nonempty(accepted_by, "accepted_by")
    at = accepted_at or datetime.now().astimezone().isoformat(timespec="seconds")
    at_dt = _parse_timestamp(at, "accepted_at")

    def mutate(payload: dict, record: dict):
        if record["status"] != "STABLE_CANDIDATE":
            raise RegistryError(
                f"accept requires STABLE_CANDIDATE, got {record['status']}"
            )
        assessment_at = _parse_timestamp(
            (record.get("latest_rollback_assessment") or {}).get("observed_at"),
            "latest rollback assessment timestamp",
        )
        if at_dt < assessment_at:
            raise RegistryError("accept timestamp cannot predate observation window")
        current = payload.get("stable_baseline")
        if current is not None and current not in payload["baseline_history"]:
            payload["baseline_history"].append(_copy(current))
        challenger = record["challenger_pointer"]
        baseline = {
            "name": f"experiment:{experiment_id}",
            "pointer": challenger["pointer"],
            "content_hash": challenger["content_hash"],
            "approved_by": actor,
            "approved_at": at,
            "note": str(note or ""),
        }
        payload["stable_baseline"] = baseline
        record["acceptance"] = {
            "accepted_by": actor,
            "accepted_at": at,
            "note": str(note or ""),
            "previous_baseline": current,
            "new_baseline": baseline,
        }
        record["status"] = "ACCEPTED"
        record["closed_at"] = at
        payload["active_by_family"].pop(record["trial_family"], None)
        _audit(
            payload,
            event="EXPERIMENT_ACCEPTED_AS_BASELINE",
            at=at,
            actor=actor,
            experiment_id=experiment_id,
            details=record["acceptance"],
        )
        return None

    record, _ = update_experiment(path, experiment_id, mutate)
    return record
