#!/usr/bin/env python3
"""Explicit, privacy-preserving aggregate view over Claude transcript statistics."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from autoresearch.common import workspace as ws
from autoresearch.common.atomic import canonical_json, sha256_file
from autoresearch.common.run_identity import RunContract, load_run_contract, resolve_git_sha
from autoresearch.trace.pricing import PRICE_SOURCE_EFFECTIVE_DATE
from autoresearch.trace.transcripts import ClaudeTranscriptAdapter, RunIdentity, TranscriptStats
from autoresearch.trace.usage_harvest import build_ledger, legacy_usage_dict, model_family

METRIC_VERSION = "weighted-input-v1"
PARSER_VERSION = "claude-transcript-stats-v1"
TIMEZONE = "UTC"
_SESSION_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_RUN_RE = re.compile(r"[0-9]{8}T[0-9]{12}Z\Z")


class SelectionError(ValueError):
    """The requested population is implicit, ambiguous, or unsupported."""


@dataclass(frozen=True)
class Selection:
    engine: str
    cohort: str
    sessions: tuple[str, ...] = ()
    run_ids: tuple[str, ...] = ()
    from_ts: datetime | None = None
    to_ts: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "sessions", tuple(self.sessions))
        object.__setattr__(self, "run_ids", tuple(self.run_ids))


def _aware(value: datetime | None) -> bool:
    return value is not None and value.tzinfo is not None and value.utcoffset() is not None


def validate_selection(selection: Selection) -> None:
    if selection.engine != "claude":
        raise SelectionError("usage panorama wave 1 supports only claude")
    if selection.cohort not in {"baseline", "candidate"}:
        raise SelectionError("cohort must be baseline or candidate")
    has_from = selection.from_ts is not None
    has_to = selection.to_ts is not None
    if has_from != has_to:
        raise SelectionError("time selection requires a complete time range")
    if has_from and (not _aware(selection.from_ts) or not _aware(selection.to_ts)):
        raise SelectionError("time range must use timezone-aware timestamps")
    if has_from and selection.from_ts > selection.to_ts:  # type: ignore[operator]
        raise SelectionError("time range starts after it ends")
    if not selection.sessions and not selection.run_ids and not (has_from and has_to):
        raise SelectionError("explicit session/run or complete time range is required")
    if any(
        not isinstance(value, str) or not _SESSION_RE.fullmatch(value)
        for value in selection.sessions
    ):
        raise SelectionError("session identifiers contain unsafe characters")
    if any(
        not isinstance(value, str) or not _RUN_RE.fullmatch(value) for value in selection.run_ids
    ):
        raise SelectionError("run identifiers must use the canonical UTC run-id format")


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_event_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _selection_payload(selection: Selection) -> dict:
    return {
        "engine": selection.engine,
        "cohort": selection.cohort,
        "sessions": list(selection.sessions),
        "run_ids": list(selection.run_ids),
        "from": _iso(selection.from_ts),
        "to": _iso(selection.to_ts),
    }


def _rebase(repo_root: Path, workspace_path: Path) -> Path:
    """Anchor workspace's relative root API beneath an explicit repository root."""
    return workspace_path if workspace_path.is_absolute() else repo_root / workspace_path


def _published_contract_paths(reports_root: Path) -> list[Path]:
    paths: list[Path] = []
    for report_dir in ws.RUN_REPORT_DIRS.values():
        paths.extend(
            sorted((reports_root / report_dir).glob("*/capsule/identity/run_contract.json"))
        )
    return paths


def _resolve_run_session(
    repo_root: Path, run_id: str
) -> tuple[str | None, str | None, str | None, Path | None, str | None]:
    """Resolve one run only from its validated capsule identity contract."""
    context_root = _rebase(repo_root, ws.context_root())
    reports_root = _rebase(repo_root, ws.reports_root())
    contract_paths = [
        context_root / spool / run_id / "capsule/identity/run_contract.json"
        for spool in ws.RUN_SPOOLS.values()
    ]
    contract_paths.extend(_published_contract_paths(reports_root))
    valid: list[tuple[RunContract, Path]] = []
    errors: list[tuple[Path, str]] = []
    for path in contract_paths:
        if not path.is_file():
            continue
        try:
            contract = load_run_contract(path)
            if contract.run_id != run_id:
                continue
            if contract.schema_version != 3:
                raise ValueError("run contract v3 required")
            if contract.engine != "claude":
                raise ValueError("run contract is not Claude-owned")
            expected_workspace = context_root / ws.RUN_SPOOLS[contract.run_kind] / run_id
            if Path(contract.workspace_path).resolve() != expected_workspace.resolve():
                raise ValueError("run contract workspace mismatch")
        except Exception as exc:  # noqa: BLE001 - invalid evidence remains unclaimed
            errors.append((path, f"invalid run contract: {type(exc).__name__}"))
            continue
        valid.append((contract, path))
    identities = {
        (contract.session_ref, contract.config_hash, contract.run_kind, contract.contract_hash)
        for contract, _path in valid
    }
    if len(identities) > 1:
        return None, None, None, valid[0][1], "conflicting verified run contracts"
    if valid:
        contract, path = valid[0]
        return contract.session_ref, contract.config_hash, contract.run_kind, path, None
    if errors:
        return None, None, None, errors[0][0], errors[0][1]
    return None, None, None, None, "run capsule contract not found"


def _verified_contract_scope(repo_root: Path) -> dict[str, tuple[RunContract, Path]]:
    """All valid Claude capsule contracts that can affect attribution ambiguity."""
    context_root = _rebase(repo_root, ws.context_root())
    reports_root = _rebase(repo_root, ws.reports_root())
    paths: list[Path] = []
    for spool in ws.RUN_SPOOLS.values():
        paths.extend(sorted((context_root / spool).glob("*/capsule/identity/run_contract.json")))
    paths.extend(_published_contract_paths(reports_root))
    out: dict[str, tuple[RunContract, Path]] = {}
    for path in paths:
        try:
            contract = load_run_contract(path)
            if contract.schema_version != 3 or contract.engine != "claude":
                continue
        except Exception:  # noqa: BLE001 - invalid evidence cannot create ambiguity
            continue
        out.setdefault(contract.run_id, (contract, path))
    return out


def _discover_sessions(projects_root: Path) -> tuple[str, ...]:
    sessions: set[str] = set()
    if not projects_root.is_dir():
        return ()
    for slug in sorted(path for path in projects_root.iterdir() if path.is_dir()):
        sessions.update(path.stem for path in slug.glob("*.jsonl") if path.is_file())
        sessions.update(
            subagents.parent.name
            for subagents in slug.glob("*/subagents")
            if subagents.is_dir() and any(subagents.rglob("agent-*.jsonl"))
        )
    return tuple(sorted(sessions))


def _in_range(started: str | None, ended: str | None, selection: Selection) -> bool | None:
    if selection.from_ts is None:
        return True
    start = _parse_event_time(started)
    end = _parse_event_time(ended)
    if start is None or end is None:
        return None
    lower = selection.from_ts.astimezone(timezone.utc)
    upper = selection.to_ts.astimezone(timezone.utc)  # type: ignore[union-attr]
    return end >= lower and start <= upper


def _percentile(values: list[int], fraction: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = round((len(ordered) - 1) * fraction)
    return ordered[index]


def _union_seconds(intervals: list[tuple[datetime, datetime]]) -> float | None:
    if not intervals:
        return None
    merged: list[list[datetime]] = []
    for start, end in sorted(intervals):
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        elif end > merged[-1][1]:
            merged[-1][1] = end
    return sum((end - start).total_seconds() for start, end in merged)


def build_panorama(
    selection: Selection,
    *,
    projects_root: Path,
    repo_root: Path,
) -> dict:
    validate_selection(selection)
    projects_root = Path(projects_root)
    repo_root = Path(repo_root)
    adapter = ClaudeTranscriptAdapter(projects_root=projects_root)
    selected: dict[str, list[str]] = {}
    unclaimed: list[str] = []
    input_files: dict[str, str] = {}
    project_slugs: set[str] = set()
    budget_observation_refs: list[str] = []
    exclusions: list[dict] = []
    run_bindings: dict[str, str | None] = {}
    run_config_hashes: dict[str, str | None] = {}
    run_kinds: dict[str, str | None] = {}

    for session in selection.sessions:
        selected.setdefault(session, [])
    for run_id in selection.run_ids:
        session, config_hash, run_kind, binding_path, binding_error = _resolve_run_session(
            repo_root, run_id
        )
        run_bindings[run_id] = session
        run_config_hashes[run_id] = config_hash
        run_kinds[run_id] = run_kind
        if binding_path is not None and binding_path.is_file():
            input_files[f"binding:{run_id}"] = sha256_file(binding_path)
            run_root = binding_path.parents[2]
            for observation in (
                run_root / "staging" / "_budget_observation.json",
                *sorted((run_root / "staging").glob("*/_budget_observation.json")),
            ):
                if observation.is_file():
                    key = f"budget:{run_id}"
                    input_files[key] = sha256_file(observation)
                    budget_observation_refs.append(key)
                    break
        if session is None:
            unclaimed.append(run_id)
            exclusions.append(
                {"run_id": run_id, "reason": binding_error or "run has no session_ref"}
            )
        else:
            selected.setdefault(session, []).append(run_id)
    if not selection.sessions and not selection.run_ids:
        for session in _discover_sessions(projects_root):
            selected[session] = []

    verified_scope = _verified_contract_scope(repo_root)
    relevant_sessions = set(selected)
    for sibling_run, (contract, path) in verified_scope.items():
        if contract.session_ref in relevant_sessions:
            input_files.setdefault(f"attribution-contract:{sibling_run}", sha256_file(path))

    session_rows: list[dict] = []
    agent_rows: list[dict] = []
    context_values: list[int] = []
    compact_values: list[int] = []
    total_tail = 0
    total_tool_requests: Counter[str] = Counter()
    total_tool_results: Counter[str] = Counter()

    for session, run_ids in selected.items():
        refs = adapter.locate(
            RunIdentity(
                run_id=run_ids[0] if run_ids else "explicit-session",
                engine="claude",
                session_ref=session,
            )
        )
        if not refs:
            session_rows.append(
                {
                    "session_ref": session,
                    "run_ids": run_ids,
                    "status": "GONE",
                    "agents": 0,
                    "weighted_input_proxy": None,
                    "wall_time_seconds": None,
                }
            )
            exclusions.append(
                {"session_ref": session, "reason": "bound transcript files are missing"}
            )
            continue
        measured_stats: list[tuple[TranscriptStats, str]] = []
        timeless_count = 0
        outside_count = 0
        for ref in refs:
            stats = adapter.stats(ref)
            ref_label = ref.path.name if ref.path is not None else ref.role
            if ref.path is not None:
                try:
                    rel = ref.path.relative_to(projects_root)
                    key = f"transcript:{rel.as_posix()}"
                    if rel.parts:
                        project_slugs.add(rel.parts[0])
                except ValueError:
                    key = f"transcript:{ref.path.name}"
                input_files[key] = sha256_file(ref.path)
                meta = ref.path.with_name(ref.path.name.replace(".jsonl", ".meta.json"))
                meta_status = (
                    "NOT_APPLICABLE"
                    if ref.role == "main"
                    else "PRESENT"
                    if meta.is_file()
                    else "UNMEASURED"
                )
                if meta.is_file():
                    input_files[f"meta:{key.removeprefix('transcript:')}"] = sha256_file(meta)
                elif ref.role != "main":
                    exclusions.append(
                        {
                            "session_ref": session,
                            "transcript": ref_label,
                            "reason": "missing agent meta",
                        }
                    )
            else:  # pragma: no cover - PRESENT Claude refs always have paths
                meta_status = "UNMEASURED"
            range_state = _in_range(stats.started_at, stats.ended_at, selection)
            if selection.from_ts is not None and range_state is None:
                timeless_count += 1
                exclusions.append(
                    {
                        "session_ref": session,
                        "transcript": ref_label,
                        "reason": "missing reliable transcript event time",
                    }
                )
                continue
            if range_state is False:
                outside_count += 1
                exclusions.append(
                    {
                        "session_ref": session,
                        "transcript": ref_label,
                        "reason": "outside event-time range",
                    }
                )
                continue
            if stats.started_at is None or stats.ended_at is None:
                timeless_count += 1
            measured_stats.append((stats, meta_status))

        if not measured_stats and outside_count and not timeless_count:
            continue
        status = (
            "PARTIAL_UNMEASURED_TIME"
            if measured_stats and 0 < timeless_count < len(refs)
            else "UNMEASURED_TIME"
            if timeless_count
            else "MEASURED"
        )
        session_agent_start = len(agent_rows)
        valid_starts: list[datetime] = []
        valid_ends: list[datetime] = []
        for stats, meta_status in measured_stats:
            usage = legacy_usage_dict(stats.usage)
            started = _parse_event_time(stats.started_at)
            ended = _parse_event_time(stats.ended_at)
            if started is not None:
                valid_starts.append(started)
            if ended is not None:
                valid_ends.append(ended)
            context_values.extend(stats.context_tokens)
            compact_values.extend(stats.compact_pre_tokens)
            total_tail += stats.suspected_tail
            total_tool_requests.update(stats.tool_requests)
            total_tool_results.update(stats.tool_results)
            agent_rows.append(
                {
                    "session_ref": session,
                    "role": stats.usage.role,
                    "agent": stats.usage.agent,
                    "model": stats.usage.model,
                    "status": stats.usage.status,
                    "meta_status": meta_status,
                    "time_status": (
                        "MEASURED"
                        if stats.started_at is not None and stats.ended_at is not None
                        else "UNMEASURED_TIME"
                    ),
                    "messages": stats.usage.messages,
                    "input": stats.usage.input,
                    "output": stats.usage.output,
                    "cache_read": stats.usage.cache_read,
                    "cache_create": stats.usage.cache_create,
                    "cache_create_5m": stats.usage.cache_create_5m,
                    "cache_create_1h": stats.usage.cache_create_1h,
                    "weighted_input_proxy": usage["weighted_in"],
                    "estimated_usd": usage["estimated_usd"],
                    "failure_count": stats.usage.failure_count,
                    "retry_count": stats.usage.retry_count,
                    "discarded": stats.usage.discarded,
                    "wall_time_seconds": (
                        (ended - started).total_seconds()
                        if started is not None and ended is not None
                        else None
                    ),
                    "first_context_tokens": stats.first_context_tokens,
                    "context_tokens": list(stats.context_tokens),
                    "compact_pre_tokens": list(stats.compact_pre_tokens),
                    "suspected_tail": stats.suspected_tail,
                    "tool_requests": dict(stats.tool_requests),
                    "tool_result_chars": dict(stats.tool_results),
                }
            )
        session_agents = agent_rows[session_agent_start:]
        session_start = min(valid_starts) if valid_starts else None
        session_end = max(valid_ends) if valid_ends else None
        session_rows.append(
            {
                "session_ref": session,
                "run_ids": run_ids,
                "status": status,
                "started_at": _iso(session_start),
                "ended_at": _iso(session_end),
                "agents": len(session_agents),
                "weighted_input_proxy": (
                    sum(row["weighted_input_proxy"] for row in session_agents)
                    if session_agents
                    else None
                ),
                "wall_time_seconds": (
                    (session_end - session_start).total_seconds()
                    if session_start is not None and session_end is not None
                    else None
                ),
            }
        )

    for run_id in unclaimed:
        session_rows.append(
            {"session_ref": None, "run_ids": [run_id], "status": "UNCLAIMED", "agents": 0}
        )

    usage_rows = [
        {
            "messages": row["messages"],
            "input": row["input"],
            "output": row["output"],
            "cache_read": row["cache_read"],
            "cache_create": row["cache_create"],
            "cache_create_5m": row["cache_create_5m"],
            "cache_create_1h": row["cache_create_1h"],
            "weighted_in": row["weighted_input_proxy"],
            "role": row["role"],
            "agent": row["agent"],
            "model": row["model"],
            "status": row["status"],
            "failure_count": row["failure_count"],
            "retry_count": row["retry_count"],
            "discarded": row["discarded"],
            "estimated_usd": row["estimated_usd"],
            "discarded_usd": (
                row["estimated_usd"] if row["discarded"] and row["estimated_usd"] else 0.0
            ),
        }
        for row in agent_rows
    ]
    ledger = build_ledger(usage_rows, source="usage_panorama")
    selection_payload = _selection_payload(selection)
    selection_hash = hashlib.sha256(canonical_json(selection_payload).encode()).hexdigest()
    measured = sum(row["status"] == "MEASURED" for row in session_rows)
    selected_count = len(session_rows)
    coverage_status = (
        "COMPLETE"
        if selected_count and measured == selected_count
        else "PARTIAL"
        if measured
        else "UNMEASURED"
    )
    weighted_by_session = [
        row["weighted_input_proxy"]
        for row in session_rows
        if row.get("weighted_input_proxy") is not None
    ]
    shell_rows = [row for row in agent_rows if row["agent"] == "gp-shell"]
    shell_weighted = sum(row["weighted_input_proxy"] for row in shell_rows)
    main_context = [
        value for row in agent_rows if row["role"] == "main" for value in row["context_tokens"]
    ]
    subagent_rows = [row for row in agent_rows if row["role"] != "main"]
    weighted_total = ledger["totals"]["weighted_input_proxy"]
    model_groups: dict[str, dict] = {}
    for row in agent_rows:
        family = model_family(row["model"])
        group = model_groups.setdefault(
            family,
            {
                "model_family": family,
                "calls": 0,
                "input": 0,
                "cache_read": 0,
                "cache_create_5m": 0,
                "cache_create_1h": 0,
                "weighted_input_proxy": 0,
                "output": 0,
                "estimated_usd": 0.0,
                "unpriced_calls": 0,
            },
        )
        group["calls"] += row["messages"]
        group["input"] += row["input"]
        group["cache_read"] += row["cache_read"]
        group["cache_create_5m"] += row["cache_create_5m"]
        group["cache_create_1h"] += row["cache_create_1h"]
        group["weighted_input_proxy"] += row["weighted_input_proxy"]
        group["output"] += row["output"]
        if row["estimated_usd"] is None:
            group["unpriced_calls"] += 1
        else:
            group["estimated_usd"] += row["estimated_usd"]
    for group in model_groups.values():
        if group["unpriced_calls"]:
            group["estimated_usd"] = None

    session_by_ref = {row["session_ref"]: row for row in session_rows if row["session_ref"]}
    shared_sessions = Counter(
        contract.session_ref
        for contract, _path in verified_scope.values()
        if contract.session_ref is not None
    )
    e1_by_run: dict[str, dict] = {}
    for run_id in selection.run_ids:
        session_ref = run_bindings.get(run_id)
        session_row = session_by_ref.get(session_ref)
        if session_ref is None or session_row is None:
            e1_by_run[run_id] = {
                "status": ("UNCLAIMED" if session_ref is None else "EXCLUDED_TIME_RANGE"),
                "value": None,
            }
        elif shared_sessions[session_ref] > 1:
            e1_by_run[run_id] = {
                "status": "AMBIGUOUS_SHARED_SESSION",
                "value": None,
                "ambiguity_note": "multiple explicit run_ids share one session; usage is not apportioned",
            }
        else:
            e1_by_run[run_id] = {
                "status": session_row["status"],
                "value": session_row.get("weighted_input_proxy"),
            }
    distinct_run_ids = tuple(dict.fromkeys(selection.run_ids))
    eligible_run_ids = [
        run_id
        for run_id in distinct_run_ids
        if run_kinds.get(run_id) == "scan-market"
        and e1_by_run.get(run_id, {}).get("status") == "MEASURED"
    ]
    e2_config_hashes = {run_config_hashes[run_id] for run_id in eligible_run_ids}
    real_run_population = (
        bool(distinct_run_ids)
        and not selection.sessions
        and len(eligible_run_ids) == len(distinct_run_ids)
    )
    e2_selected_count = len(distinct_run_ids) if distinct_run_ids else selected_count
    e2_eligible_count = len(eligible_run_ids)
    e2_status = (
        "PARTIAL"
        if coverage_status == "PARTIAL"
        else "INELIGIBLE"
        if not real_run_population or e2_selected_count < 10
        else "INCOMPARABLE"
        if len(e2_config_hashes) != 1
        else "ELIGIBLE"
    )
    e2_can_evaluate = e2_status == "ELIGIBLE"
    wall_values = [row["wall_time_seconds"] for row in agent_rows]
    cumulative_agent_time = (
        sum(value for value in wall_values if value is not None)
        if wall_values and all(value is not None for value in wall_values)
        else None
    )
    session_intervals = [
        (_parse_event_time(row.get("started_at")), _parse_event_time(row.get("ended_at")))
        for row in session_rows
    ]
    reliable_intervals = [
        (start, end) for start, end in session_intervals if start is not None and end is not None
    ]
    wall_time = (
        _union_seconds(reliable_intervals)
        if reliable_intervals and coverage_status == "COMPLETE"
        else None
    )
    wall_by_session = {
        row["session_ref"]: {
            "status": "MEASURED" if row.get("wall_time_seconds") is not None else "UNMEASURED",
            "value": row.get("wall_time_seconds"),
        }
        for row in session_rows
        if row["session_ref"] is not None
    }
    wall_by_run: dict[str, dict] = {}
    for run_id in selection.run_ids:
        session_ref = run_bindings.get(run_id)
        if session_ref is None:
            wall_by_run[run_id] = {"status": "UNMEASURED", "value": None}
        elif shared_sessions[session_ref] > 1:
            wall_by_run[run_id] = {"status": "AMBIGUOUS_SHARED_SESSION", "value": None}
        else:
            wall_by_run[run_id] = wall_by_session.get(
                session_ref, {"status": "UNMEASURED", "value": None}
            )
    config_list = [
        {"run_id": run_id, "config_hash": run_config_hashes.get(run_id)}
        for run_id in selection.run_ids
    ]

    def measured_value(value: object) -> object | None:
        return value if agent_rows else None

    exclusions.append(
        {
            "metric": "stage_wall_time_seconds",
            "reason": "stage timing is unavailable from transcript statistics",
        }
    )
    payload = {
        "schema_version": 1,
        "metric_version": METRIC_VERSION,
        "cohort": selection.cohort,
        "coverage_status": coverage_status,
        "provenance": {
            "selection_hash": selection_hash,
            "selection": selection_payload,
            "explicit_sessions": list(selection.sessions),
            "explicit_run_ids": list(selection.run_ids),
            "git_sha": resolve_git_sha(repo_root),
            "parser_version": PARSER_VERSION,
            "metric_version": METRIC_VERSION,
            "price_effective_date": PRICE_SOURCE_EFFECTIVE_DATE,
            "timezone": TIMEZONE,
            "project_slugs": sorted(project_slugs),
            "run_config_hashes": run_config_hashes,
            "run_config_list_hash": hashlib.sha256(
                canonical_json(config_list).encode()
            ).hexdigest(),
            "meter_config_hash": hashlib.sha256(
                canonical_json(
                    {
                        "metric_version": METRIC_VERSION,
                        "parser_version": PARSER_VERSION,
                        "timezone": TIMEZONE,
                    }
                ).encode()
            ).hexdigest(),
            "exclusions": exclusions,
            "input_file_sha256": dict(sorted(input_files.items())),
        },
        "sessions": session_rows,
        "agents": agent_rows,
        "model_mix": sorted(model_groups.values(), key=lambda row: row["model_family"]),
        "totals": {
            "sessions": len(session_rows),
            "measured_sessions": measured,
            "unclaimed_sessions": sum(row["status"] == "UNCLAIMED" for row in session_rows),
            "gone_sessions": sum(row["status"] == "GONE" for row in session_rows),
            "unmeasured_time_sessions": sum(
                row["status"] in {"UNMEASURED_TIME", "PARTIAL_UNMEASURED_TIME"}
                for row in session_rows
            ),
            "partial_time_sessions": sum(
                row["status"] == "PARTIAL_UNMEASURED_TIME" for row in session_rows
            ),
            "agents": len(agent_rows),
            "transcript_count": len(agent_rows),
            "calls": sum(row["messages"] for row in agent_rows),
            "messages": measured_value(ledger["totals"]["messages"]),
            "weighted_input_proxy": measured_value(ledger["totals"]["weighted_input_proxy"]),
            "input": measured_value(ledger["totals"]["input"]),
            "output": measured_value(ledger["totals"]["output"]),
            "cache_create": measured_value(sum(row["cache_create"] for row in agent_rows)),
            "cache_create_5m": measured_value(ledger["totals"]["cache_create_5m"]),
            "cache_create_1h": measured_value(ledger["totals"]["cache_create_1h"]),
            "cache_read": measured_value(ledger["totals"]["cache_read"]),
            "estimated_usd": (
                ledger["totals"]["estimated_usd"]
                if agent_rows and ledger["totals"]["unpriced_transcripts"] == 0
                else None
            ),
            "priced_calls": ledger["totals"]["priced_transcripts"],
            "unpriced_calls": ledger["totals"]["unpriced_transcripts"],
            "failure_count": measured_value(ledger["totals"]["failure_count"]),
            "retry_count": measured_value(ledger["totals"]["retry_count"]),
            "discarded_calls": measured_value(ledger["totals"]["discarded_transcripts"]),
            "wall_time_seconds": wall_time,
            "wall_time_status": "MEASURED" if wall_time is not None else "UNMEASURED",
        },
        "wall_time": {
            "elapsed_seconds": wall_time,
            "cumulative_agent_time_s": cumulative_agent_time,
            "by_session": wall_by_session,
            "by_run": wall_by_run,
            "stage_wall_time_seconds": None,
            "stage_status": "UNMEASURED",
        },
        "context": {
            "first_values": [
                row["first_context_tokens"]
                for row in agent_rows
                if row["first_context_tokens"] is not None
            ],
            "p50": _percentile(context_values, 0.50),
            "p90": _percentile(context_values, 0.90),
        },
        "tools": {
            "requests": dict(sorted(total_tool_requests.items())),
            "result_chars": dict(sorted(total_tool_results.items())),
        },
        "compaction": {"pre_tokens": compact_values, "count": len(compact_values)},
        "suspected_tail": {
            "count": total_tail,
            "weighted_input_proxy": None,
            "weighted_status": "UNMEASURED",
        },
        "budget_observation_refs": sorted(set(budget_observation_refs)),
        "efficiency_metrics": {
            "E1_run_weighted_input_proxy": e1_by_run,
            "E2_cohort": {
                "status": e2_status,
                "selected_count": e2_selected_count,
                "eligible_count": e2_eligible_count,
                "p50": (_percentile(weighted_by_session, 0.50) if e2_can_evaluate else None),
                "p90": (_percentile(weighted_by_session, 0.90) if e2_can_evaluate else None),
            },
            "E3_scan_shell": {
                "weighted_input_proxy": shell_weighted,
                "share": None if not weighted_total else shell_weighted / weighted_total,
            },
            "E4_gp_shell_first_context_p50": _percentile(
                [
                    row["first_context_tokens"]
                    for row in shell_rows
                    if row["first_context_tokens"] is not None
                ],
                0.50,
            ),
            "E5_main_context_p90": _percentile(main_context, 0.90),
            "E6_max_subagent": {
                "messages": max((row["messages"] for row in subagent_rows), default=None),
                "weighted_input_proxy": max(
                    (row["weighted_input_proxy"] for row in subagent_rows), default=None
                ),
            },
            "E7_suspected_tail": {
                "count": total_tail,
                "weighted_input_proxy": None,
                "weighted_status": "UNMEASURED",
            },
            "E8_selection_weighted_input_proxy": {
                "status": "UNMEASURED",
                "value": None,
                "reason": "development-line classification is unavailable",
            },
        },
        "quality_guards": {
            "measurement_coverage": (None if not selected_count else measured / selected_count),
            "gate1": {"status": "UNMEASURED", "value": None},
            "gate2": {"status": "UNMEASURED", "value": None},
            "gate4": {"status": "UNMEASURED", "value": None},
            "evidence_coverage": {"status": "UNMEASURED", "value": None},
            "freshness": {"status": "UNMEASURED", "value": None},
            "rating_boundaries": {"status": "UNMEASURED", "value": None},
            "failure_count": {
                "status": "MEASURED" if agent_rows else "UNMEASURED",
                "value": ledger["totals"]["failure_count"] if agent_rows else None,
            },
            "retry_count": {
                "status": "MEASURED" if agent_rows else "UNMEASURED",
                "value": ledger["totals"]["retry_count"] if agent_rows else None,
            },
            "discarded_calls": {
                "status": "MEASURED" if agent_rows else "UNMEASURED",
                "value": ledger["totals"]["discarded_transcripts"] if agent_rows else None,
            },
            "budget_decision_source": "budget.observe_run",
            "panorama_changes_run_status": False,
            "truncated": False,
        },
    }
    return payload


def _cell(value: object) -> str:
    return "—" if value is None else str(value)


def render_panorama(payload: dict) -> str:
    """Render the supplied JSON-compatible payload without consulting other state."""
    totals = payload["totals"]
    lines = [
        f"# Token usage panorama — {payload['cohort']}",
        "",
        f"- Metric: `{payload['metric_version']}`",
        f"- Selection: `{payload['provenance']['selection_hash']}`",
        f"- Sessions: {totals['sessions']} (measured {totals['measured_sessions']})",
        f"- Weighted input proxy: {totals['weighted_input_proxy']}",
        f"- Calls/messages: {totals['calls']} / {_cell(totals['messages'])}",
        f"- Input/cache5m/cache1h/cache-read/output: {_cell(totals['input'])} / "
        f"{_cell(totals['cache_create_5m'])} / {_cell(totals['cache_create_1h'])} / "
        f"{_cell(totals['cache_read'])} / {_cell(totals['output'])}",
        f"- Estimated USD / wall seconds: {_cell(totals['estimated_usd'])} / "
        f"{_cell(totals['wall_time_seconds'])} ({totals['wall_time_status']})",
        "",
        "| session | runs | status | agents | weighted input |",
        "|---|---|---|---:|---:|",
    ]
    for row in payload["sessions"]:
        lines.append(
            f"| {_cell(row['session_ref'])} | {', '.join(row['run_ids']) or '—'} | "
            f"{row['status']} | {row['agents']} | {_cell(row.get('weighted_input_proxy'))} |"
        )
    lines += [
        "",
        "| role | agent | model | status | weighted input | output |",
        "|---|---|---|---|---:|---:|",
    ]
    for row in payload["agents"]:
        lines.append(
            f"| {row['role']} | {row['agent']} | {row['model']} | {row['status']} | "
            f"{row['weighted_input_proxy']} | {row['output']} |"
        )
    lines += [
        "",
        "| model | calls | weighted input | output | estimated USD | unpriced |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in payload["model_mix"]:
        lines.append(
            f"| {row['model_family']} | {row['calls']} | {row['weighted_input_proxy']} | "
            f"{row['output']} | {_cell(row['estimated_usd'])} | {row['unpriced_calls']} |"
        )
    return "\n".join(lines) + "\n"


def _atomic_private_create(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(path)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, 0o600)
        os.link(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def write_panorama(
    payload: dict,
    markdown: str,
    *,
    reports_root: Path,
    now: datetime,
) -> tuple[Path, Path]:
    if not _aware(now):
        raise ValueError("now must be timezone-aware")
    stamp = now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    root = Path(reports_root) / "_metering"
    json_path = root / f"panorama_{stamp}.json"
    markdown_path = root / f"panorama_{stamp}.md"
    if json_path.exists() or markdown_path.exists():
        raise FileExistsError(f"panorama reading already exists: {stamp}")
    canonical = canonical_json(payload).encode("utf-8") + b"\n"
    expected_markdown = render_panorama(json.loads(canonical))
    if markdown != expected_markdown:
        raise ValueError("markdown must be rendered from the supplied JSON payload")
    _atomic_private_create(json_path, canonical)
    try:
        _atomic_private_create(markdown_path, markdown.encode("utf-8"))
    except Exception:
        json_path.unlink(missing_ok=True)
        raise
    return json_path, markdown_path


def _parse_cli_time(raw: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid ISO timestamp: {raw}") from exc
    if not _aware(parsed):
        raise argparse.ArgumentTypeError("timestamp must include a timezone")
    return parsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="explicit Claude token panorama")
    parser.add_argument("--engine", required=True, choices=("claude",))
    parser.add_argument("--cohort", required=True, choices=("baseline", "candidate"))
    parser.add_argument("--session", action="append", default=[])
    parser.add_argument("--run-id", action="append", default=[])
    parser.add_argument("--from", dest="from_ts", type=_parse_cli_time)
    parser.add_argument("--to", dest="to_ts", type=_parse_cli_time)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    # 引擎守卫必须在 argparse **之后**:`--help` 是任何引擎下都该退 0 的自述
    # (tests/test_cli_entrypoints.py Tier 2 锁的就是这条),而读 Claude 计量输入
    # 仍旧只有 claude 引擎能做——守卫仍先于 build_panorama,不碰任何 transcript。
    if ws.ENGINE != "claude":
        print(
            "[usage_panorama] Codex engine may not access Claude metering inputs",
            file=sys.stderr,
        )
        return 2
    selection = Selection(
        engine=args.engine,
        cohort=args.cohort,
        sessions=tuple(args.session),
        run_ids=tuple(args.run_id),
        from_ts=args.from_ts,
        to_ts=args.to_ts,
    )
    try:
        validate_selection(selection)
        payload = build_panorama(
            selection,
            projects_root=Path.home() / ".claude/projects",
            repo_root=Path.cwd(),
        )
    except SelectionError as exc:
        print(f"[usage_panorama] {exc}", file=sys.stderr)
        return 2
    markdown = render_panorama(payload)
    if args.write:
        paths = write_panorama(
            payload,
            markdown,
            reports_root=ws.reports_root(),
            now=datetime.now(timezone.utc),
        )
        print(f"[usage_panorama] JSON → {paths[0]}\n[usage_panorama] Markdown → {paths[1]}")
    else:
        print(markdown, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
