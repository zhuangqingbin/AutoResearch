"""Root-operator commands that turn one finished session_v1 run into acceptance evidence.

``accept-run`` recomputes everything from this engine's frozen canonical publication:
the full report verification, an isolated offline replay, and (only with ``--write``)
the portable proof plus the records index. ``acceptance-import`` admits a portable
proof produced under another root (the other host, or this engine's isolated drill
worktree) after revalidating every identity link it carries.

Neither command decides whether a workflow is enabled. ``evaluation.accept_workflow``
still owns that, with the fixed dual-host denominator and the research boundary gate.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from autoresearch.common import workspace as ws
from autoresearch.common.atomic import atomic_write_json, sha256_bytes
from autoresearch.contracts.forensic import validate_acceptance_record
from autoresearch.session_agent import evaluation

_VERIFIED = ("report_covered", "publication_ok", "orchestration_verified",
             "integrity_ok", "completeness_ok")
_EVIDENCE_KINDS = ("REAL_SESSION", "REAL_SESSION_DRILL")


def _json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _audit_root() -> Path:
    reports = ws.reports_root()
    audit = reports / "_acceptance"
    if reports.is_symlink() or audit.is_symlink():
        raise ValueError("acceptance audit root may not redirect through a symlink")
    return audit


def _publication(run_id: str, publication_id: str | None) -> tuple[str, str, Path]:
    """(workflow, publication_id, receipt path) of one committed publication of this engine."""
    ws.validate_run_id(run_id)
    found = []
    for kind in ws.RUN_REPORT_DIRS:
        folder = ws.run_reports_root(kind) / "_publications" / run_id
        if folder.is_dir():
            found.extend((kind, path) for path in sorted(folder.glob("p*.json")))
    if publication_id is not None:
        found = [(kind, path) for kind, path in found if path.stem == publication_id]
    if len(found) != 1:
        raise ValueError(
            f"expected exactly one committed publication for {run_id}, found {len(found)}"
            + ("" if publication_id else "; pass --publication-id"))
    kind, path = found[0]
    return kind, path.stem, path


def _report_relative(bundle: dict, requested: str | None) -> str:
    rows = sorted(row["relative_path"] for row in bundle["business_files"])
    if requested is not None:
        if requested not in rows:
            raise ValueError("--report is not a business file of this publication: " + ", ".join(rows))
        return requested
    if len(rows) != 1:
        raise ValueError("publication has several business files; pass --report with one of: "
                         + ", ".join(rows))
    return rows[0]


def _fresh_replay_dir(cell: Path) -> Path:
    """First directory of this (run, scenario) cell that holds no earlier replay.

    The offline layout refuses a non-empty directory, and an earlier replay is audit bytes
    we keep; a dry run followed by ``--write`` therefore lands in ``<scenario>`` and then
    ``<scenario>.2``.
    """
    candidate, attempt = cell, 1
    while candidate.exists() and (not candidate.is_dir() or any(candidate.iterdir())):
        attempt += 1
        candidate = cell.with_name(f"{cell.name}.{attempt}")
    return candidate


def _blockers(record: dict, verification: dict, replay: dict, spec: dict) -> list[str]:
    """Why this run cannot be recorded for the scenario; empty means every gate is met."""
    out = [f"verification.{name}={verification.get(name)!r}" for name in _VERIFIED
           if verification.get(name) is not True]
    for owner, value in (("verification", verification), ("replay", replay)):
        if value.get("compute_status") != "FULL":
            out.append(f"{owner}.compute_status={value.get('compute_status')!r}")
        for name in ("missing", "diffs"):
            if value.get(name):
                out.append(f"{owner}.{name}={value[name]!r}")
    if replay.get("isolation_status") != "ENFORCED":
        out.append(f"replay.isolation_status={replay.get('isolation_status')!r}")
    if record["replay_scope"] != [record["workflow"]]:
        out.append(f"replay.requested_scope={record['replay_scope']!r}")
    if record["mode"] != spec["mode"]:
        out.append(f"run mode {record['mode']!r} does not match scenario mode {spec['mode']!r}")
    return out


def upsert_record(record: dict) -> Path:
    """Replace this (engine, workflow, scenario) cell; keep the previous index bytes."""
    checked = validate_acceptance_record(dict(record))
    audit = _audit_root()
    path = audit / "records.json"
    previous = path.read_bytes() if path.is_file() else None
    records = json.loads(previous.decode("utf-8")) if previous is not None else []
    if not isinstance(records, list):
        raise ValueError("acceptance records index must be a list")
    key = (checked["engine"], checked["workflow"], checked["scenario"])
    updated = [row for row in records
               if not isinstance(row, dict)
               or (row.get("engine"), row.get("workflow"), row.get("scenario")) != key] + [checked]
    if updated == records:
        return path
    if previous is not None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        history = audit / "record-history" / f"{stamp}-{sha256_bytes(previous)[:12]}.json"
        history.parent.mkdir(parents=True, exist_ok=True)
        history.write_bytes(previous)
    atomic_write_json(path, updated)
    return path


def collect(run_id: str, scenario: str, *, evidence_kind: str = "REAL_SESSION",
            notes: str = "", publication_id: str | None = None, report: str | None = None,
            replay_timeout: float | None = None, write: bool = False,
            replay_root: Path | str | None = None) -> dict:
    """Verify, replay and (with ``write``) record one committed run of this engine."""
    from autoresearch.session_agent.replay_adapters import DomainReplayRunner
    from autoresearch.session_agent.replay_registry import build_replay_plan
    from autoresearch.trace.replay import execute_replay
    from autoresearch.trace.verification import verify_report

    workflow, publication_id, receipt_path = _publication(run_id, publication_id)
    required = evaluation.required_acceptance_scenarios(workflow)
    if scenario not in required:
        raise ValueError(f"unknown {workflow} scenario {scenario!r}; expected one of {sorted(required)}")
    if evidence_kind not in _EVIDENCE_KINDS:
        raise ValueError("evidence kind must be REAL_SESSION or REAL_SESSION_DRILL")
    if evidence_kind == "REAL_SESSION_DRILL" and not required[scenario]["drill"]:
        raise ValueError(f"{workflow}:{scenario} does not admit drill evidence")
    canonical = ws.canonical_publication_root(workflow, run_id, publication_id)
    capsule = canonical / "capsule"
    bundle = _json(canonical / "_publication/publication_bundle.json")
    receipt = _json(receipt_path)
    origin = _json(capsule / "identity/execution_origin.json")
    root = _json(capsule / "verification/ROOT.json")
    source = _json(capsule / "identity/source_tree_manifest.json")
    report_file = canonical / _report_relative(bundle, report)
    verification = verify_report(report_file, expected_run_id=run_id, level="full")
    plan = build_replay_plan(SimpleNamespace(capsule=capsule, engine=ws.ENGINE, run_id=run_id))
    output = _fresh_replay_dir((Path(replay_root) if replay_root is not None
                                else _audit_root() / "replays") / run_id / scenario)
    # No timeout of our own: the adapter runner owns its per-unit default; a caller raises it
    # explicitly for heavy units (a scan prelude replay) instead of this module guessing one.
    runner = (DomainReplayRunner(capsule) if replay_timeout is None
              else DomainReplayRunner(capsule, timeout=replay_timeout))
    replay = execute_replay(plan, capsule, output, runner)
    record = {
        "schema_version": 1, "engine": ws.ENGINE, "workflow": workflow, "mode": replay["run_mode"],
        "scenario": scenario, "code_tree_hash": source["code_tree_hash"], "run_id": run_id,
        "publication_id": publication_id, "root_hash": root["root_hash"],
        "evidence_kind": evidence_kind,
        "orchestration_verified": verification["orchestration_verified"],
        "report_covered": verification["report_covered"],
        "completeness_ok": verification["completeness_ok"],
        "replay_scope": list(replay["requested_scope"]),
        "compute_status": replay["compute_status"],
        "isolation_status": replay["isolation_status"], "notes": notes,
    }
    blockers = _blockers(record, verification, replay, required[scenario])
    result = {
        "schema_version": 1, "command": "accept-run", "run_id": run_id, "workflow": workflow,
        "scenario": scenario, "report_file": str(report_file), "replay_dir": str(output),
        "record": record, "blockers": blockers, "ready": not blockers,
        "verification": {name: verification.get(name)
                         for name in (*_VERIFIED, "compute_status", "missing", "diffs")},
        "replay": {name: replay.get(name)
                   for name in ("compute_status", "isolation_status", "model_status",
                                "scene_status", "run_mode", "missing", "diffs")},
        "written": False, "proof_path": None, "records_path": None,
    }
    if not write:
        return result
    if blockers:
        raise ValueError("acceptance evidence is incomplete: " + "; ".join(blockers))
    proof = evaluation.write_acceptance_proof(
        validate_acceptance_record(record), verification=verification, replay_plan=plan,
        replay_result=replay, publication_bundle=bundle, publication_receipt=receipt,
        execution_origin=origin)
    result.update(written=True, proof_path=str(proof), records_path=str(upsert_record(record)))
    return result


def import_proof(source: Path | str) -> dict:
    """Admit a portable proof staged by the operator; never open the other engine's roots."""
    path = Path(source)
    other = "claude" if ws.ENGINE == "codex" else "codex"
    if any(part in {f"context_{other}", f"reports_{other}"} for part in path.absolute().parts):
        raise ValueError("stage the proof inside this engine before importing")
    if path.is_symlink() or not path.is_file():
        raise ValueError("regular proof file required")
    value = _json(path)
    if not isinstance(value, dict) or not isinstance(value.get("record"), dict):
        raise ValueError("portable acceptance proof required")
    record = validate_acceptance_record(dict(value["record"]))
    evaluation.validate_acceptance_proof(record, value)
    spec = evaluation.required_acceptance_scenarios(record["workflow"]).get(record["scenario"])
    if spec is None:
        raise ValueError("proof names a scenario outside the fixed denominator")
    # Same gate as ``collect``: a self-consistent proof of incomplete evidence is still refused.
    if record["evidence_kind"] == "REAL_SESSION_DRILL" and not spec["drill"]:
        raise ValueError(f"{record['workflow']}:{record['scenario']} does not admit drill evidence")
    blockers = _blockers(record, value["verification"], value["replay_result"], spec)
    if blockers:
        raise ValueError("acceptance evidence is incomplete: " + "; ".join(blockers))
    target = evaluation.acceptance_proof_path(record, _audit_root() / "proofs")
    if target.is_file():
        if _json(target) != value:
            raise RuntimeError("acceptance proof conflict")
    else:
        atomic_write_json(target, value)
    return {"schema_version": 1, "command": "acceptance-import",
            "engine": record["engine"], "workflow": record["workflow"],
            "scenario": record["scenario"], "run_id": record["run_id"],
            "proof_path": str(target), "records_path": str(upsert_record(record))}


__all__ = ["collect", "import_proof", "upsert_record"]
