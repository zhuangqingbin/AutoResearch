"""Freeze observed dispatch metadata and already accepted inputs for later research.

The caller supplies actual production observations. Missing observations remain null;
this producer does not infer historical choices from today's configuration or prompts.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from pathlib import Path

from autoresearch.common import workspace as ws
from autoresearch.common.atomic import canonical_json, sha256_bytes

FILENAME = "_research_provenance.json"
SCHEMA_VERSION = 1
PROFILES = {"single-stage-v1", "two-stage-v1"}
CANDIDATE_FIELDS = (
    "force_full",
    "force_full_reason",
    "force_full_rule_hash",
    "lane",
    "conviction",
    "n_channels",
    "reserved",
    "deep_declared",
    "deep_read_proof",
    "deep_read_verified",
    "card_kind",
    "early_stop_phase",
    "intel_status",
    "dossier_status",
    "quality_status",
    "solvency_status",
    "initial_rating",
    "initial_faces",
    "initial_raw_metrics",
    "final_faces",
    "final_raw_metrics",
)
BOOL_FIELDS = ("force_full", "reserved", "deep_declared")


def safe_path(value):
    path = Path(value).absolute()
    other = "claude" if ws.ENGINE == "codex" else "codex"
    if any(part in {f"context_{other}", f"reports_{other}"} for part in path.parts):
        raise ValueError("other engine raw root forbidden")
    if path.resolve() != path:
        raise ValueError("noncanonical or symlink research source forbidden")
    return path


def _digest(value):
    return isinstance(value, str) and re.fullmatch("[0-9a-f]{64}", value) is not None


def validate_provenance(value):
    required = {
        "schema_version",
        "run_id",
        "analysis_date",
        "contract_hash",
        "profile",
        "captured_at",
        "base_inputs",
        "candidates",
    }
    if (
        not isinstance(value, dict)
        or set(value) not in (required, required | {"collection_binding"})
        or type(value["schema_version"]) is not int
        or value["schema_version"] != SCHEMA_VERSION
    ):
        raise ValueError("invalid research provenance schema")
    if "collection_binding" in value and not _digest(value["collection_binding"]):
        raise ValueError("invalid collector binding")
    ws.validate_run_id(value["run_id"])
    if date.fromisoformat(value["analysis_date"]).isoformat() != value["analysis_date"]:
        raise ValueError("invalid research date")
    if not _digest(value["contract_hash"]) or value["profile"] not in PROFILES | {None}:
        raise ValueError("invalid research contract/profile")
    timestamp = datetime.fromisoformat(value["captured_at"])
    if timestamp.utcoffset() is None:
        raise ValueError("aware capture time required")
    if not isinstance(value["base_inputs"], list) or not isinstance(value["candidates"], dict):
        raise ValueError("research inputs and candidates required")
    ids, paths = set(), set()
    for item in value["base_inputs"]:
        if set(item) != {"artifact_id", "path", "sha256"} or not _digest(item["sha256"]):
            raise ValueError("invalid frozen base input")
        key, relative = item["artifact_id"], Path(item["path"])
        if (
            not isinstance(key, str)
            or not re.fullmatch(r"[A-Za-z0-9_.:-]+", key)
            or key in ids
            or str(relative) in paths
            or relative.is_absolute()
            or ".." in relative.parts
            or str(relative) in {"", "."}
        ):
            raise ValueError("invalid/duplicate research input identity or path")
        ids.add(key)
        paths.add(str(relative))
    for code, row in value["candidates"].items():
        if (
            not re.fullmatch(r"\d{6}", code)
            or not isinstance(row, dict)
            or set(row) != set(CANDIDATE_FIELDS)
        ):
            raise ValueError("invalid research candidate fields")
        if any(row[k] is not None and type(row[k]) is not bool for k in BOOL_FIELDS):
            raise ValueError("research switches must be boolean or null")
        if row["deep_read_verified"] is not None:
            raise ValueError("deep read requires bound transcript verification, not a self-report")
        if row["force_full_rule_hash"] is not None and not _digest(row["force_full_rule_hash"]):
            raise ValueError("invalid force full rule hash")
    json.dumps(value, allow_nan=False)  # reject non-JSON/nonfinite observations
    return value


def freeze_bytes(path, content, *, allow_existing=False):
    """Atomic exclusive capture; retries may reuse only identical original bytes."""
    import os
    import tempfile

    path = safe_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".capture-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if not allow_existing or path.read_bytes() != content:
                raise
    finally:
        temporary.unlink(missing_ok=True)


def freeze_research_provenance(
    target,
    *,
    run_id,
    analysis_date,
    contract_hash,
    profile,
    candidates,
    base_inputs,
    collection_binding=None,
):
    """Exclusively freeze real bytes; input hashes must come from the accepted task."""
    path = safe_path(target)
    if path.exists():
        raise FileExistsError(path)
    if any(set(row) - set(CANDIDATE_FIELDS) for row in candidates.values()):
        raise ValueError("unknown research candidate fields")
    rows = {
        code: {key: row.get(key) for key in CANDIDATE_FIELDS} for code, row in candidates.items()
    }
    captures, refs = [], []
    for index, item in enumerate(base_inputs):
        content = safe_path(item["path"]).read_bytes()
        if sha256_bytes(content) != item["sha256"]:
            raise ValueError("accepted input hash mismatch")
        relative = f"research_inputs/{index:04d}.bin"
        refs.append(
            {"artifact_id": item["artifact_id"], "path": relative, "sha256": item["sha256"]}
        )
        captures.append((relative, content))
    value = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "analysis_date": analysis_date,
        "contract_hash": contract_hash,
        "profile": profile,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "base_inputs": refs,
        "candidates": rows,
    }
    if collection_binding is not None:
        value["collection_binding"] = collection_binding
    validate_provenance(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    if captures:
        safe_path(path.parent / "research_inputs").mkdir(exist_ok=True)
    for relative, content in captures:
        freeze_bytes(path.parent / relative, content, allow_existing=True)
    freeze_bytes(path, (canonical_json(value) + "\n").encode())
    return path


def freeze_force_full(target, *, code, decision, priors, rule):
    """The dispatch producer records the actual predicate inputs, not a later replay."""
    import math

    def number(value):
        if value is None:
            return None
        try:
            result = float(value)
            return result if math.isfinite(result) else None
        except (ValueError, TypeError):
            return None

    lane = priors.get("lane")
    frozen = {
        "lane": lane if isinstance(lane, str) else None,
        "conviction": number(priors.get("conviction")),
        "n_channels": number(priors.get("n_channels")),
        "reserved": bool(priors.get("l2_lane_reserved"))
        if priors.get("l2_lane_reserved") is not None
        else None,
    }
    value = {
        "schema_version": 1,
        "code": code,
        "force_full": bool(decision),
        "priors": frozen,
        "reason": "pinned"
        if decision and lane == "pinned"
        else "strong_prior"
        if decision
        else "predicate_false",
        "rule": rule,
        "rule_hash": sha256_bytes(canonical_json(rule).encode()),
    }
    path = safe_path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if json.loads(path.read_text()) != value:
            raise ValueError("frozen force_full dispatch observation changed")
        return path
    with path.open("x") as stream:
        stream.write(canonical_json(value) + "\n")
    return path
