from __future__ import annotations

import pytest

from autoresearch.common.atomic import canonical_json, sha256_bytes
from autoresearch.contracts.forensic import validate_acceptance_record
from autoresearch.contracts.publication import (
    publication_bundle_hash,
    publication_receipt_hash,
)
from autoresearch.session_agent import evaluation
from tests.contracts.test_publication_contracts import _bundle, _receipt
from tests.contracts.test_replay_contracts import _plan, _result


@pytest.fixture(autouse=True)
def _isolated_audit_root(tmp_path, monkeypatch):
    """Every test here states a rule about records and proofs; none is about whatever real
    boundary proofs this engine root happens to hold. Without this, the gate reads
    ``reports_<engine>/_acceptance`` on the developer's machine: the day real proofs were
    first imported (2026-10-01) one test went red under codex and another under claude."""
    monkeypatch.setattr(evaluation.ws, "reports_root", lambda: tmp_path / "isolated-reports")


def _record(
    engine: str,
    workflow: str,
    scenario: str,
    mode: str,
    *,
    evidence_kind: str = "REAL_SESSION",
) -> dict:
    return {
        "schema_version": 1,
        "engine": engine,
        "workflow": workflow,
        "mode": mode,
        "scenario": scenario,
        "code_tree_hash": "a" * 64,
        "run_id": "20260914T120000000000Z",
        "publication_id": "p1",
        "root_hash": "b" * 64,
        "evidence_kind": evidence_kind,
        "orchestration_verified": True,
        "report_covered": True,
        "completeness_ok": True,
        "replay_scope": [workflow],
        "compute_status": "FULL",
        "isolation_status": "ENFORCED",
        "notes": "fixture",
    }


def _matrix(workflow: str, *, evidence_kind: str) -> list[dict]:
    return [
        _record(engine, workflow, scenario, spec["mode"], evidence_kind=evidence_kind)
        for engine in ("codex", "claude")
        for scenario, spec in evaluation.required_acceptance_scenarios(workflow).items()
    ]


def test_acceptance_record_is_a_strict_contract():
    value = _record("codex", "macro-research", "full", "FULL")
    assert validate_acceptance_record(value) is value

    with pytest.raises(ValueError, match="fields mismatch"):
        validate_acceptance_record({**value, "status": "PASS"})
    with pytest.raises(ValueError, match="code_tree_hash"):
        validate_acceptance_record({**value, "code_tree_hash": "looks-good"})


def test_synthetic_pass_cannot_enable_default(monkeypatch):
    records = _matrix("macro-research", evidence_kind="SYNTHETIC")
    monkeypatch.setattr(
        evaluation,
        "_verify_acceptance_proof",
        lambda record, evidence_root=None: {"verified": True, "missing": []},
    )

    result = evaluation.accept_workflow(records)

    assert result["default_enabled"] is False
    assert result["status"] == "INCOMPLETE"
    assert result["missing_real_sessions"]


def test_arbitrary_run_id_without_proof_cannot_enable_default(tmp_path):
    records = _matrix("macro-research", evidence_kind="REAL_SESSION")

    result = evaluation.accept_workflow(records, evidence_root=tmp_path)

    assert result["default_enabled"] is False
    assert any("PROOF_MISSING" in item for item in result["invalid_records"])


def test_dual_host_real_records_enable_only_when_every_proof_verifies(monkeypatch):
    records = _matrix("macro-research", evidence_kind="REAL_SESSION")
    monkeypatch.setattr(
        evaluation,
        "_verify_acceptance_proof",
        lambda record, evidence_root=None: {"verified": True, "missing": []},
    )

    result = evaluation.accept_workflow(records)

    assert result["default_enabled"] is False
    assert result["status"] == "INCOMPLETE"
    assert result["research_boundary_gate"]["status"] == "PENDING_REAL_HOST_VALIDATION"
    assert result["missing_real_sessions"] == []
    assert result["invalid_records"] == []


def test_missing_one_host_scenario_and_false_forensic_flags_fail_closed(monkeypatch):
    records = _matrix("sector-research", evidence_kind="REAL_SESSION")
    records.pop()
    records[0]["completeness_ok"] = False
    monkeypatch.setattr(
        evaluation,
        "_verify_acceptance_proof",
        lambda record, evidence_root=None: {"verified": True, "missing": []},
    )

    result = evaluation.accept_workflow(records)

    assert result["default_enabled"] is False
    assert result["missing_real_sessions"]
    assert any("completeness_ok" in item for item in result["invalid_records"])


def test_drills_are_allowed_only_for_declared_control_scenarios(monkeypatch):
    scan = _matrix("scan-market", evidence_kind="REAL_SESSION")
    for record in scan:
        if record["scenario"] != "full":
            record["evidence_kind"] = "REAL_SESSION_DRILL"
    monkeypatch.setattr(
        evaluation,
        "_verify_acceptance_proof",
        lambda record, evidence_root=None: {"verified": True, "missing": []},
    )
    assert evaluation.accept_workflow(scan)["accepted_records"] == sorted(f"{row['engine']}:{row['scenario']}" for row in scan)
    assert evaluation.accept_workflow(scan)["default_enabled"] is False

    stock = _matrix("stock-research", evidence_kind="REAL_SESSION")
    stock[0]["evidence_kind"] = "REAL_SESSION_DRILL"
    result = evaluation.accept_workflow(stock)
    assert result["default_enabled"] is False
    assert any("DRILL_NOT_ALLOWED" in item for item in result["invalid_records"])


def test_portable_proof_revalidates_all_identity_links(tmp_path, monkeypatch):
    record = _record("codex", "macro-research", "lite", "LITE")
    plan = _plan()
    record["code_tree_hash"] = plan["code_tree_hash"]
    replay = _result()
    replay["replay_plan_hash"] = plan["replay_plan_hash"]
    replay["requested_scope"] = record["replay_scope"]
    origin = {
        "schema_version": 1,
        "engine": "codex",
        "run_id": record["run_id"],
        "run_kind": record["workflow"],
        "orchestration": "session_v1",
        "entrypoint": "autoresearch.session_agent.begin",
        "plan_hash": plan["plan_hash"],
        "host_profile_hash": "e" * 64,
        "legacy_reason": None,
        "created_at": "2026-09-14T12:00:00Z",
    }
    bundle = _bundle()
    bundle.update(
        {
            "origin_hash": sha256_bytes(canonical_json(origin).encode("utf-8")),
            "plan_hash": plan["plan_hash"],
            "evidence_plan_hash": plan["evidence_plan_hash"],
        }
    )
    bundle["bundle_hash"] = publication_bundle_hash(bundle)
    receipt = _receipt()
    receipt.update(
        {
            "bundle_hash": bundle["bundle_hash"],
            "capsule_root_hash": record["root_hash"],
        }
    )
    receipt["receipt_hash"] = publication_receipt_hash(receipt)
    verification = {
        "schema_version": 1,
        "engine": "codex",
        "run_id": record["run_id"],
        "report_path": "report/macro.md",
        "report_sha256": bundle["business_files"][0]["sha256"],
        "publication_id": "p1",
        "orchestration": "session_v1",
        "orchestration_verified": True,
        "report_covered": True,
        "integrity_ok": True,
        "publication_ok": True,
        "completeness_ok": True,
        "compute_status": "FULL",
        "model_status": "EVIDENCE_ONLY",
        "scope": ["all"],
        "missing": [],
        "diffs": [],
    }
    monkeypatch.setattr(evaluation.ws, "ENGINE", "codex")

    proof = {
        "schema_version": 1,
        "record": record,
        "verification": verification,
        "replay_plan": plan,
        "replay_result": replay,
        "publication_bundle": bundle,
        "publication_receipt": receipt,
        "execution_origin": origin,
        "proof_hash": "0" * 64,
    }
    proof["proof_hash"] = evaluation._proof_hash(proof)
    assert evaluation.validate_acceptance_proof(record, proof) is proof
    path = evaluation.acceptance_proof_path(record, tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(canonical_json(proof), encoding="utf-8")

    assert path.is_file()
    assert evaluation._verify_acceptance_proof(
        record, evidence_root=tmp_path
    ) == {"verified": True, "missing": []}

    corrupted = path.read_text(encoding="utf-8").replace(
        record["root_hash"], "f" * 64, 1
    )
    path.write_text(corrupted, encoding="utf-8")
    assert evaluation._verify_acceptance_proof(
        record, evidence_root=tmp_path
    )["verified"] is False

    with pytest.raises(FileNotFoundError):
        evaluation.write_acceptance_proof(
            record,
            verification=verification,
            replay_plan=plan,
            replay_result=replay,
            publication_bundle=bundle,
            publication_receipt=receipt,
            execution_origin=origin,
            evidence_root=tmp_path,
        )


def test_old_complete_proofs_cannot_bypass_c4_loaded_host_gate(monkeypatch):
    records = _matrix('macro-research', evidence_kind='REAL_SESSION')
    monkeypatch.setattr(evaluation, '_verify_acceptance_proof',
                        lambda *args: {'verified': True, 'missing': []})
    result = evaluation.accept_workflow(records)
    assert result['default_enabled'] is False
    assert result['research_boundary_gate']['schema_version'] == 1
    assert result['research_boundary_gate']['acceptance_satisfied'] is False
    assert result['research_boundary_gate']['loaded_host_evidence'] == []
