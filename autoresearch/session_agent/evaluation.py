"""Fail-closed comparison and host acceptance records for session_v1."""
from __future__ import annotations

import json
import re
from pathlib import Path

from autoresearch.common import workspace as ws
from autoresearch.common.atomic import atomic_write_json, canonical_json, sha256_bytes
from autoresearch.contracts.forensic import (
    validate_acceptance_record,
    validate_execution_origin,
    validate_verification_result,
)
from autoresearch.contracts.publication import (
    validate_publication_bundle,
    validate_publication_receipt,
)
from autoresearch.contracts.replay import validate_replay_plan, validate_replay_result
from autoresearch.contracts.session_task import require_exact_fields

COMPARISON_FIELDS = frozenset(
    {
        "schema_version",
        "engine",
        "workflow",
        "mode",
        "baseline_run_id",
        "candidate_run_id",
        "input_identity_equal",
        "config_identity_equal",
        "deterministic_diffs",
        "research_diffs",
        "missing_evidence",
        "verdict",
    }
)
_RUNTIME_METADATA = frozenset(
    {
        "run_id",
        "generated_at",
        "created_at",
        "updated_at",
        "runtime",
        "report_dir",
        "capsule_path",
        "workspace",
    }
)
_RUN_ID = re.compile(r"[0-9]{8}T[0-9]{12}Z")
_VERDICTS = frozenset({"PASS", "FAIL", "INCOMPLETE"})
_EVIDENCE_KINDS = frozenset({"REAL_SESSION", "SYNTHETIC", "NONE"})

_ACCEPTANCE_SCENARIOS = {
    "stock-research": {
        "a-share-full": {"mode": "FULL", "drill": False},
        "us-full": {"mode": "FULL", "drill": False},
        "lite-early-stop": {"mode": "LITE", "drill": False},
        "lite-full-card": {"mode": "LITE", "drill": False},
    },
    "macro-research": {
        "full": {"mode": "FULL", "drill": False},
        "lite": {"mode": "LITE", "drill": False},
    },
    "sector-research": {
        "full": {"mode": "FULL", "drill": False},
        "lite-reuse": {"mode": "LITE", "drill": False},
    },
    "dossier-init": {
        "init": {"mode": "INIT", "drill": False},
        "resume": {"mode": "INIT", "drill": False},
    },
    "scan-market": {
        "full": {"mode": "FULL", "drill": False},
        "forced-full": {"mode": "FORCED_FULL", "drill": True},
        "sentinel-empty": {"mode": "SENTINEL_EMPTY", "drill": True},
        "sentinel-pinned": {"mode": "SENTINEL_PINNED", "drill": True},
    },
}
_ACCEPTANCE_PROOF_FIELDS = frozenset(
    {
        "schema_version",
        "record",
        "verification",
        "replay_plan",
        "replay_result",
        "publication_bundle",
        "publication_receipt",
        "execution_origin",
        "proof_hash",
    }
)


def _without_runtime_metadata(value):
    if isinstance(value, dict):
        return {
            key: _without_runtime_metadata(item)
            for key, item in value.items()
            if key not in _RUNTIME_METADATA
        }
    if isinstance(value, list):
        return [_without_runtime_metadata(item) for item in value]
    return value


def _diff(left, right, path: str, out: list[dict]) -> None:
    if isinstance(left, dict) and isinstance(right, dict):
        for key in sorted(set(left) | set(right)):
            child = f"{path}.{key}" if path else key
            if key not in left:
                out.append({"path": child, "baseline": None, "candidate": right[key]})
            elif key not in right:
                out.append({"path": child, "baseline": left[key], "candidate": None})
            else:
                _diff(left[key], right[key], child, out)
        return
    if isinstance(left, list) and isinstance(right, list):
        length = max(len(left), len(right))
        for index in range(length):
            child = f"{path}[{index}]"
            if index >= len(left):
                out.append({"path": child, "baseline": None, "candidate": right[index]})
            elif index >= len(right):
                out.append({"path": child, "baseline": left[index], "candidate": None})
            else:
                _diff(left[index], right[index], child, out)
        return
    if left != right:
        out.append({"path": path or "$", "baseline": left, "candidate": right})


def compare_manifests(baseline: object, candidate: object) -> list[dict]:
    """Compare products after removing only declared runtime/path metadata."""
    diffs: list[dict] = []
    _diff(
        _without_runtime_metadata(baseline),
        _without_runtime_metadata(candidate),
        "",
        diffs,
    )
    return diffs


def _run_id(value: object, field: str, *, optional: bool = False) -> None:
    if value is None and optional:
        return
    if type(value) is not str or _RUN_ID.fullmatch(value) is None:
        raise ValueError(f"invalid {field}")


def validate_comparison(value: dict) -> dict:
    require_exact_fields(value, COMPARISON_FIELDS)
    if value["schema_version"] != 1:
        raise ValueError("unsupported comparison schema")
    if value["engine"] not in {"codex", "claude"}:
        raise ValueError("invalid comparison engine")
    for field in ("workflow", "mode"):
        if type(value[field]) is not str or not value[field]:
            raise ValueError(f"{field} required")
    _run_id(value["baseline_run_id"], "baseline_run_id", optional=True)
    _run_id(value["candidate_run_id"], "candidate_run_id")
    for field in ("input_identity_equal", "config_identity_equal"):
        if type(value[field]) is not bool:
            raise ValueError(f"{field} must be boolean")
    for field in ("deterministic_diffs", "research_diffs", "missing_evidence"):
        if type(value[field]) is not list:
            raise ValueError(f"{field} must be a list")
    if value["verdict"] not in _VERDICTS:
        raise ValueError("invalid comparison verdict")
    return value


def build_comparison(
    *,
    engine: str,
    workflow: str,
    mode: str,
    baseline_run_id: str | None,
    candidate_run_id: str,
    input_identity_equal: bool,
    config_identity_equal: bool,
    deterministic_diffs: list,
    research_diffs: list,
    missing_evidence: list,
) -> dict:
    if missing_evidence or baseline_run_id is None:
        verdict = "INCOMPLETE"
    elif not input_identity_equal or not config_identity_equal or deterministic_diffs:
        verdict = "FAIL"
    else:
        verdict = "PASS"
    value = {
        "schema_version": 1,
        "engine": engine,
        "workflow": workflow,
        "mode": mode,
        "baseline_run_id": baseline_run_id,
        "candidate_run_id": candidate_run_id,
        "input_identity_equal": input_identity_equal,
        "config_identity_equal": config_identity_equal,
        "deterministic_diffs": deterministic_diffs,
        "research_diffs": research_diffs,
        "missing_evidence": missing_evidence,
        "verdict": verdict,
    }
    return validate_comparison(value)


def _validate_acceptance_case(value: dict) -> dict:
    fields = frozenset(
        {"host", "scenario", "status", "evidence_kind", "run_id", "notes"}
    )
    require_exact_fields(value, fields)
    if value["host"] not in {"codex", "claude"}:
        raise ValueError("invalid acceptance host")
    if type(value["scenario"]) is not str or not value["scenario"]:
        raise ValueError("acceptance scenario required")
    if value["status"] not in _VERDICTS:
        raise ValueError("invalid acceptance status")
    if value["evidence_kind"] not in _EVIDENCE_KINDS:
        raise ValueError("invalid acceptance evidence kind")
    _run_id(value["run_id"], "acceptance run_id", optional=True)
    if type(value["notes"]) is not str:
        raise ValueError("acceptance notes must be a string")
    if value["status"] == "PASS" and (
        value["evidence_kind"] != "REAL_SESSION" or value["run_id"] is None
    ):
        raise ValueError("PASS requires REAL_SESSION evidence and run_id")
    return value


def _rollup(statuses: list[str]) -> str:
    if "FAIL" in statuses:
        return "FAIL"
    if "INCOMPLETE" in statuses or not statuses:
        return "INCOMPLETE"
    return "PASS"


def build_acceptance_matrix(cases: list[dict]) -> dict:
    if not isinstance(cases, list) or not cases:
        raise ValueError("acceptance cases required")
    checked = [_validate_acceptance_case(dict(item)) for item in cases]
    keys = [(item["host"], item["scenario"]) for item in checked]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate host/scenario acceptance case")
    hosts = {
        host: _rollup([item["status"] for item in checked if item["host"] == host])
        for host in sorted({item["host"] for item in checked})
    }
    return {
        "schema_version": 1,
        "overall": _rollup(list(hosts.values())),
        "hosts": hosts,
        "cases": sorted(checked, key=lambda item: (item["host"], item["scenario"])),
    }


def required_acceptance_scenarios(workflow: str) -> dict[str, dict]:
    """Return a copy of the real-session denominator for one workflow."""
    if workflow not in _ACCEPTANCE_SCENARIOS:
        raise ValueError(f"unknown acceptance workflow: {workflow}")
    return {
        scenario: dict(spec)
        for scenario, spec in _ACCEPTANCE_SCENARIOS[workflow].items()
    }


def acceptance_proof_path(record: dict, evidence_root: Path | str | None = None) -> Path:
    checked = validate_acceptance_record(record)
    root = (
        Path(evidence_root)
        if evidence_root is not None
        else ws.reports_root() / "_acceptance/proofs"
    )
    return (
        root
        / checked["engine"]
        / checked["workflow"]
        / checked["run_id"]
        / f"{checked['scenario']}.json"
    )


def _proof_hash(value: dict) -> str:
    body = {key: item for key, item in value.items() if key != "proof_hash"}
    return sha256_bytes(canonical_json(body).encode("utf-8"))


def validate_acceptance_proof(record: dict, value: dict) -> dict:
    """Verify a portable proof package; hashes are integrity, not host signatures."""
    checked = validate_acceptance_record(record)
    require_exact_fields(value, _ACCEPTANCE_PROOF_FIELDS)
    if value["schema_version"] != 1 or value["record"] != checked:
        raise ValueError("acceptance proof record mismatch")
    if value["proof_hash"] != _proof_hash(value):
        raise ValueError("acceptance proof hash mismatch")
    verification = validate_verification_result(value["verification"])
    replay_plan = validate_replay_plan(value["replay_plan"])
    replay = validate_replay_result(value["replay_result"])
    bundle = validate_publication_bundle(value["publication_bundle"])
    receipt = validate_publication_receipt(value["publication_receipt"])
    origin = validate_execution_origin(value["execution_origin"])
    identity = (checked["engine"], checked["run_id"])
    for name, item in (
        ("verification", verification),
        ("replay plan", replay_plan),
        ("replay result", replay),
        ("publication bundle", bundle),
        ("publication receipt", receipt),
        ("execution origin", origin),
    ):
        if (item["engine"], item["run_id"]) != identity:
            raise ValueError(f"{name} identity mismatch")
    if bundle["run_kind"] != checked["workflow"] or origin["run_kind"] != checked["workflow"]:
        raise ValueError("acceptance workflow identity mismatch")
    if bundle["publication_id"] != checked["publication_id"]:
        raise ValueError("acceptance publication identity mismatch")
    if (
        receipt["publication_id"] != checked["publication_id"]
        or receipt["bundle_hash"] != bundle["bundle_hash"]
        or receipt["capsule_root_hash"] != checked["root_hash"]
    ):
        raise ValueError("acceptance receipt identity mismatch")
    if origin["orchestration"] != "session_v1" or sha256_bytes(
        canonical_json(origin).encode("utf-8")
    ) != bundle["origin_hash"]:
        raise ValueError("acceptance origin is not bound session_v1")
    if (
        replay_plan["plan_hash"] != bundle["plan_hash"]
        or replay_plan["evidence_plan_hash"] != bundle["evidence_plan_hash"]
        or replay_plan["code_tree_hash"] != checked["code_tree_hash"]
        or replay["replay_plan_hash"] != replay_plan["replay_plan_hash"]
        or replay["run_mode"] != checked["mode"]
        or replay["requested_scope"] != checked["replay_scope"]
        or replay["compute_status"] != checked["compute_status"]
        or replay["isolation_status"] != checked["isolation_status"]
    ):
        raise ValueError("acceptance replay identity mismatch")
    if (
        verification["publication_id"] != checked["publication_id"]
        or verification["orchestration"] != "session_v1"
        or verification["orchestration_verified"]
        != checked["orchestration_verified"]
        or verification["report_covered"] != checked["report_covered"]
        or verification["completeness_ok"] != checked["completeness_ok"]
        or verification["integrity_ok"] is not True
        or verification["publication_ok"] is not True
        or verification["compute_status"] != "FULL"
        or verification["missing"]
        or verification["diffs"]
    ):
        raise ValueError("acceptance verification mismatch")
    if not any(
        row["relative_path"] == verification["report_path"]
        and row["sha256"] == verification["report_sha256"]
        for row in bundle["business_files"]
    ):
        raise ValueError("acceptance report is not covered by publication bundle")
    return value


def write_acceptance_proof(
    record: dict,
    *,
    verification: dict,
    replay_plan: dict,
    replay_result: dict,
    publication_bundle: dict,
    publication_receipt: dict,
    execution_origin: dict,
    evidence_root: Path | str | None = None,
) -> Path:
    """Write one content-bound proof package into this engine's audit root."""
    checked = validate_acceptance_record(record)
    if checked["engine"] != ws.ENGINE:
        raise ValueError("cannot author another engine's acceptance proof")
    canonical = ws.canonical_publication_root(
        checked["workflow"], checked["run_id"], checked["publication_id"]
    )
    actual_paths = {
        "publication_bundle": canonical
        / "_publication/publication_bundle.json",
        "publication_receipt": ws.run_reports_root(checked["workflow"])
        / "_publications"
        / checked["run_id"]
        / f"{checked['publication_id']}.json",
        "execution_origin": canonical
        / "capsule/identity/execution_origin.json",
    }
    actual = {}
    for name, source in actual_paths.items():
        value = json.loads(source.read_text(encoding="utf-8"))
        actual[name] = value
    supplied = {
        "publication_bundle": publication_bundle,
        "publication_receipt": publication_receipt,
        "execution_origin": execution_origin,
    }
    if actual != supplied:
        raise ValueError("acceptance proof inputs differ from canonical publication")
    root = json.loads(
        (canonical / "capsule/verification/ROOT.json").read_text(encoding="utf-8")
    )
    if root.get("root_hash") != checked["root_hash"]:
        raise ValueError("acceptance root_hash differs from canonical publication")
    source_manifest = json.loads(
        (canonical / "capsule/identity/source_tree_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    if source_manifest.get("code_tree_hash") != checked["code_tree_hash"]:
        raise ValueError("acceptance code_tree_hash differs from frozen source")
    evidence_plan = json.loads(
        (canonical / "capsule/evidence/evidence_plan.json").read_text(encoding="utf-8")
    )
    if evidence_plan.get("evidence_plan_hash") != replay_plan.get(
        "evidence_plan_hash"
    ):
        raise ValueError("acceptance replay plan differs from frozen evidence plan")
    from autoresearch.trace.verification import verify_report

    recomputed = verify_report(
        canonical / verification["report_path"],
        expected_run_id=checked["run_id"],
        level="full",
    )
    if recomputed != verification:
        raise ValueError("acceptance verification was not recomputed from report path")
    value = {
        "schema_version": 1,
        "record": checked,
        "verification": verification,
        "replay_plan": replay_plan,
        "replay_result": replay_result,
        "publication_bundle": publication_bundle,
        "publication_receipt": publication_receipt,
        "execution_origin": execution_origin,
        "proof_hash": "0" * 64,
    }
    value["proof_hash"] = _proof_hash(value)
    validate_acceptance_proof(checked, value)
    path = acceptance_proof_path(checked, evidence_root)
    if path.is_file():
        current = json.loads(path.read_text(encoding="utf-8"))
        if current != value:
            raise RuntimeError("acceptance proof conflict")
        return path
    atomic_write_json(path, value)
    return path


def _verify_acceptance_proof(
    record: dict,
    evidence_root: Path | str | None = None,
) -> dict:
    path = acceptance_proof_path(record, evidence_root)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        validate_acceptance_proof(record, value)
    except FileNotFoundError:
        return {"verified": False, "missing": [f"PROOF_MISSING:{path}"]}
    except Exception as exc:  # noqa: BLE001 - an invalid proof fails the gate
        return {
            "verified": False,
            "missing": [f"PROOF_INVALID:{path}:{type(exc).__name__}"],
        }
    return {"verified": True, "missing": []}


def accept_workflow(
    records: list[dict],
    *,
    evidence_root: Path | str | None = None,
) -> dict:
    """Enable one workflow only after its dual-host real denominator is proven."""
    if not isinstance(records, list) or not records:
        raise ValueError("acceptance records required")
    checked = []
    invalid = []
    for index, record in enumerate(records):
        try:
            checked.append(validate_acceptance_record(record))
        except Exception as exc:  # noqa: BLE001 - malformed claims disable the gate
            invalid.append(f"INVALID_RECORD:{index}:{type(exc).__name__}")
    workflows = {record["workflow"] for record in checked}
    if len(workflows) != 1:
        invalid.append("WORKFLOW_SET_MISMATCH")
        workflow = next(iter(workflows), "UNKNOWN")
        required = {}
    else:
        workflow = next(iter(workflows))
        required = required_acceptance_scenarios(workflow)
    by_key = {}
    for record in checked:
        key = (record["engine"], record["scenario"])
        if key in by_key:
            invalid.append(f"DUPLICATE_RECORD:{key[0]}:{key[1]}")
        else:
            by_key[key] = record
    missing_real = []
    accepted = []
    for engine in ("codex", "claude"):
        for scenario, spec in required.items():
            label = f"{engine}:{scenario}"
            record = by_key.get((engine, scenario))
            if record is None:
                missing_real.append(label)
                continue
            evidence_kind = record["evidence_kind"]
            if evidence_kind not in {"REAL_SESSION", "REAL_SESSION_DRILL"}:
                missing_real.append(label)
                continue
            if evidence_kind == "REAL_SESSION_DRILL" and not spec["drill"]:
                invalid.append(f"DRILL_NOT_ALLOWED:{label}")
                continue
            if record["mode"] != spec["mode"]:
                invalid.append(f"MODE_MISMATCH:{label}")
                continue
            for field, expected in (
                ("orchestration_verified", True),
                ("report_covered", True),
                ("completeness_ok", True),
                ("compute_status", "FULL"),
                ("isolation_status", "ENFORCED"),
            ):
                if record[field] != expected:
                    invalid.append(f"{label}:{field}={record[field]}")
            if record["replay_scope"] != [workflow]:
                invalid.append(f"{label}:replay_scope")
            if any(item.startswith(f"{label}:") for item in invalid):
                continue
            proof = _verify_acceptance_proof(record, evidence_root)
            if not proof["verified"]:
                invalid.extend(f"{label}:{item}" for item in proof["missing"])
                continue
            accepted.append(label)
    expected_keys = {
        (engine, scenario)
        for engine in ("codex", "claude")
        for scenario in required
    }
    expected_labels = {f"{engine}:{scenario}" for engine, scenario in expected_keys}
    for engine, scenario in sorted(set(by_key) - expected_keys):
        invalid.append(f"UNEXPECTED_SCENARIO:{engine}:{scenario}")
    enabled = bool(required) and not invalid and not missing_real and set(accepted) == expected_labels
    return {
        "schema_version": 1,
        "workflow": workflow,
        "status": "ENABLED" if enabled else "INCOMPLETE",
        "default_enabled": enabled,
        "accepted_records": sorted(accepted),
        "missing_real_sessions": sorted(set(missing_real)),
        "invalid_records": sorted(set(invalid)),
    }


def orchestration_status(handle) -> dict:
    """Project a recomputed origin verdict for evaluation and later cutover gates."""
    import json
    from pathlib import Path

    from autoresearch.session_agent.origin import verify_execution_origin

    path = Path(handle.capsule) / "identity/execution_origin.json"
    orchestration = "UNKNOWN"
    if path.is_file():
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            orchestration = str(value.get("orchestration") or "UNKNOWN")
        except Exception:
            pass
    checked = verify_execution_origin(handle)
    return {
        "orchestration": orchestration,
        "orchestration_verified": checked["verified"],
        "missing": checked["missing"],
    }


__all__ = [
    "COMPARISON_FIELDS",
    "accept_workflow",
    "acceptance_proof_path",
    "build_acceptance_matrix",
    "build_comparison",
    "compare_manifests",
    "orchestration_status",
    "required_acceptance_scenarios",
    "validate_acceptance_proof",
    "validate_comparison",
    "write_acceptance_proof",
]
