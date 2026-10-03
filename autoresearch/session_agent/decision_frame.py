"""Freeze the common overnight clock once and bind it to each new task input."""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from autoresearch.common.atomic import atomic_write_json
from autoresearch.common.execution_math import build_decision_frame
from autoresearch.contracts.execution import (
    parse_aware,
    validate_calendar_source,
    validate_decision_frame,
)
from autoresearch.contracts.session_plan import expansion_hash, plan_hash
from autoresearch.session_agent import artifacts

FRAME_ID = "research.frame"


def attach_plan(plan: dict) -> dict:
    if plan["run_kind"] == "dossier-init":
        return plan
    value = deepcopy(plan)
    for task in value["tasks"]:
        if FRAME_ID not in task["input_artifact_ids"]:
            task["input_artifact_ids"].append(FRAME_ID)
    value["plan_hash"] = plan_hash(value)
    return value


def attach_expansion(expansion: dict, snapshot: dict) -> dict:
    value = deepcopy(expansion)
    for task in value["tasks"]:
        if FRAME_ID not in task["input_artifact_ids"]:
            task["input_artifact_ids"].append(FRAME_ID)
    if not any(row["artifact_id"] == FRAME_ID for row in value["input_artifacts"]):
        value["input_artifacts"].append({key: snapshot[key] for key in ("artifact_id", "sha256")})
    value["expansion_hash"] = expansion_hash(value)
    value["expansion_id"] = f"{value['template_id']}-{value['expansion_hash'][:16]}"
    return value


def _venue(request: dict) -> str:
    if request.get("schema_version", 1) >= 3:
        return request["research_context"]["venue"]
    if request.get("asset_type") == "crypto":
        return "CONTINUOUS"
    if request["kind"] != "stock-research":
        return "XSHG"  # The sector/scan actionable scope is A shares.
    subject = str(request.get("subject") or "").upper()
    suffix = subject.rsplit(".", 1)[-1]
    known = {"SS": "XSHG", "SH": "XSHG", "SZ": "XSHE", "BJ": "XBSE", "HK": "XHKG"}
    if suffix in known:
        return known[suffix]
    if len(subject) == 6 and subject.isdigit():
        from autoresearch.dataflows.symbol_utils import normalize_symbol
        return _venue(dict(request, subject=normalize_symbol(subject)))
    # An unqualified ticker is not proof of listing venue or exchange calendar.
    return "UNSPECIFIED"


def check_calendar_engine(parts, *, engine: str) -> None:
    if engine not in {"codex", "claude"}:
        raise ValueError("unknown calendar engine")
    other = "claude" if engine == "codex" else "codex"
    if any(part in {f"context_{other}", f"reports_{other}"} for part in parts):
        raise ValueError("calendar source is outside the current engine boundary")


def register_frame(request: dict, handle, *, calendar_loader=None) -> dict:
    """Register a read-only frame; an existing frozen clock is never refreshed."""
    path = Path(handle.staging) / "session_outputs/decision_frame.json"
    venue = _venue(request)
    anchor = request["analysis_date"]
    created = getattr(handle.contract, "created_at", None)
    if created is None:
        created = datetime.strptime(handle.run_id, "%Y%m%dT%H%M%S%fZ").replace(tzinfo=timezone.utc).isoformat()
    usage = {"stock-research": "standalone", "scan-market": "scan",
             "macro-research": "macro", "sector-research": "sector"}[request["kind"]]
    if request.get("schema_version", 1) >= 3:
        usage = request["research_context"]["usage"]
    depth = "FULL" if request["requested_mode"] == "AUTO" else request["requested_mode"]
    predecessor_hash = None
    if request.get("schema_version", 1) >= 3 and request.get("predecessor_run_id"):
        if request["predecessor_run_id"] == handle.run_id:
            raise ValueError("review requires a new run identity")
        prior = json.loads((Path(handle.workspace) / "session/predecessor.json").read_text())
        raw_prior = prior["frame_json"].encode("utf-8")
        predecessor_hash = hashlib.sha256(raw_prior).hexdigest()
        if prior["run_id"] != request["predecessor_run_id"] or prior["engine"] != handle.engine or predecessor_hash != prior["frame_sha256"]:
            raise ValueError("predecessor frame binding mismatch")
        previous = validate_decision_frame(json.loads(raw_prior))
        if previous["analysis_session"] != anchor or previous["venue"] != venue:
            raise ValueError("review must retain predecessor analysis anchor and venue")
        if parse_aware(created) <= parse_aware(previous["knowledge_cutoff"]):
            raise ValueError("review must have a newer knowledge cutoff")
    if path.is_file():
        value = validate_decision_frame(json.loads(path.read_text(encoding="utf-8")))
        expected = {"analysis_session": anchor, "venue": venue,
                    "research_depth": depth, "usage": usage}
        if any(value[key] != item for key, item in expected.items()):
            raise ValueError("frozen frame differs from the frozen research request")
        if value.get("predecessor_frame_hash") != predecessor_hash:
            raise ValueError("frozen frame predecessor mismatch")
        if parse_aware(value["knowledge_cutoff"]) != parse_aware(created):
            raise ValueError("frozen frame knowledge cutoff differs from run creation")
        if value.get("schema_version") == 2 and value["calendar_evidence"] is not None:
            frozen_source = Path(handle.staging) / "session_outputs/calendar_source.json"
            raw = frozen_source.read_bytes()
            if hashlib.sha256(raw).hexdigest() != value["calendar_evidence"]["source_sha256"] or json.loads(raw) != value["calendar_evidence"]["source"]:
                raise ValueError("frozen calendar source hash mismatch")
            artifacts.register_artifact(handle, "research.calendar", frozen_source, "READ")
    else:
        sessions, quality = [], "UNKNOWN"
        source = raw = None
        source_path = request.get("research_context", {}).get("calendar_source_path")
        if source_path is not None:
            candidate = Path(source_path)
            resolved = candidate.resolve(strict=True)
            check_calendar_engine((*candidate.parts, *resolved.parts), engine=handle.engine)
            if candidate.is_symlink() or not resolved.is_file():
                raise ValueError("calendar source must be an exact regular file")
            raw = resolved.read_bytes()
            source = validate_calendar_source(json.loads(raw), venue=venue, cutoff=created)
            sessions = [row["date"] for row in source["sessions"]]
            quality = "exchange_calendar"
        if source is None and venue in {"XSHG", "XSHE", "XBSE"}:
            if calendar_loader is None:
                from autoresearch.scan.exec_anchor import trading_sessions
                calendar_loader = trading_sessions
            end = (date.fromisoformat(anchor) + timedelta(days=31)).isoformat()
            sessions, quality = calendar_loader(anchor, end)
        value = build_decision_frame(
            analysis_session=anchor, knowledge_cutoff=created, venue=venue,
            research_depth=depth,
            usage=usage, sessions=sessions, calendar_quality=quality,
        )
        if request.get("schema_version", 1) >= 3:
            value.update(schema_version=2, calendar_evidence=None, predecessor_frame_hash=predecessor_hash)
            if source is not None:
                value["calendar_evidence"] = {"source_sha256": hashlib.sha256(raw).hexdigest(), "source": source}
            elif quality == "trade_cal" and value["calendar_quality"] != "UNKNOWN":
                # The established A-share calendar owner supplies session dates; regular
                # exchange phases are fixed for this venue, with raw captured dates retained.
                source = {"schema_version": 1, "venue": venue, "timezone": value["timezone"],
                    "source_id": "trade_cal", "published_at": created, "available_at": created,
                    "sessions": [{"date": day, "open_at": day + "T09:30:00+08:00",
                                  "close_at": day + "T15:00:00+08:00"} for day in sorted(set(sessions))]}
                raw = json.dumps(source, ensure_ascii=False, sort_keys=True).encode()
                value["calendar_evidence"] = {"source_sha256": hashlib.sha256(raw).hexdigest(), "source": source}
            validate_decision_frame(value)
            if raw is not None:
                frozen_source = Path(handle.staging) / "session_outputs/calendar_source.json"
                frozen_source.parent.mkdir(parents=True, exist_ok=True)
                frozen_source.write_bytes(raw)
                artifacts.register_artifact(handle, "research.calendar", frozen_source, "READ")
        atomic_write_json(path, value)
    artifacts.register_artifact(handle, FRAME_ID, path, "READ")
    artifacts.snapshot_artifact(handle, FRAME_ID)
    if value.get("calendar_evidence") is not None:
        artifacts.snapshot_artifact(handle, "research.calendar")
    return value


def frame_in_plan(plan: dict) -> bool:
    return any(FRAME_ID in task["input_artifact_ids"] for task in plan["tasks"])
