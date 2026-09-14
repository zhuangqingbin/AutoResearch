from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest

from autoresearch.common import workspace as ws
from autoresearch.common.atomic import canonical_json, sha256_bytes
from autoresearch.contracts.forensic import evidence_plan_hash
from autoresearch.contracts.publication import publication_bundle_hash
from autoresearch.trace.capsule import BusinessStatus, checkpoint, finalize
from autoresearch.trace.verification import verify_report
from tests.forensic_fixtures import begin_fixture_run, redirect_roots


@pytest.fixture
def published_report(tmp_path, monkeypatch):
    handle = begin_fixture_run(tmp_path, monkeypatch)
    report_dir = ws.run_reports_root("scan-market") / handle.run_id
    report_dir.mkdir(parents=True)
    report = report_dir / "summary.md"
    report.write_text("# synthetic report\nrun_id: fixture only\n", encoding="utf-8")
    checkpoint(handle.run_id, "l3", "SUCCEEDED", [], {"finalists": 1})
    finalized = finalize(handle.run_id, BusinessStatus.SUCCEEDED, report_dir)
    return handle, report, report_dir, finalized


def _tree(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_report_path_cannot_borrow_old_run_verification(published_report, tmp_path):
    handle, report, _, _ = published_report
    changed = tmp_path / "audit/summary.md"
    changed.parent.mkdir()
    shutil.copyfile(report, changed)
    changed.write_text(report.read_text() + "generated_at: 2026-09-14T10:41:00Z\n")

    result = verify_report(changed, expected_run_id=handle.run_id)

    assert result["report_covered"] is False
    assert "UNBOUND_REPORT" in result["missing"]
    assert result["run_id"] is None


@pytest.mark.parametrize(
    "revision",
    [
        "generated_at: 2026-09-14T10:41:00Z\n",
        "material_business_value: 42\n",
    ],
)
def test_unsealed_timestamp_or_business_revision_is_unbound(
    published_report, tmp_path, revision
):
    _, report, _, _ = published_report
    changed = tmp_path / revision.split(":", 1)[0] / "summary.md"
    changed.parent.mkdir()
    changed.write_text(report.read_text() + revision)

    result = verify_report(changed)

    assert result["report_covered"] is False
    assert result["publication_ok"] is False
    assert result["missing"] == ["UNBOUND_REPORT"]


def test_bound_report_verification_is_read_only_and_recomputes_full_scope(
    published_report,
):
    handle, report, report_dir, _ = published_report
    before = _tree(report_dir)

    integrity = verify_report(report, expected_run_id=handle.run_id, level="integrity")
    full = verify_report(report, expected_run_id=handle.run_id, level="full")

    assert integrity["report_covered"] is True
    assert integrity["integrity_ok"] is True
    assert integrity["publication_ok"] is True
    assert integrity["compute_status"] == "NONE"
    assert "evidence_closure" not in integrity["scope"]
    assert full["report_covered"] is True
    assert full["integrity_ok"] is True
    assert full["compute_status"] in {"FULL", "PARTIAL"}
    assert "evidence_closure" in full["scope"]
    assert _tree(report_dir) == before


def test_expected_run_conflict_fails_closed(published_report):
    _, report, _, _ = published_report

    result = verify_report(report, expected_run_id="20260914T999999999999Z")

    assert result["report_covered"] is False
    assert set(result["missing"]) == {"EXPECTED_RUN_ID_MISMATCH", "UNBOUND_REPORT"}


def test_capsule_facade_delegates_to_path_verification(published_report):
    from autoresearch.trace import capsule

    handle, report, _, _ = published_report
    assert capsule.verify_report(report, expected_run_id=handle.run_id)["report_covered"]


def test_committed_session_publication_binds_exact_report_bytes(tmp_path, monkeypatch):
    from autoresearch.trace import capsule
    from autoresearch.trace.publication import execute_publication

    handle = begin_fixture_run(tmp_path, monkeypatch)
    origin = capsule.freeze_legacy_execution_origin(
        handle,
        entrypoint="autoresearch.session_agent.begin",
        legacy_reason="transaction fixture",
    )
    payload = b"# committed session report\n"
    bundle = {
        "schema_version": 1,
        "engine": handle.engine,
        "run_id": handle.run_id,
        "run_kind": handle.contract.run_kind,
        "publication_id": "p1",
        "predecessor": None,
        "origin_hash": sha256_bytes(canonical_json(origin).encode("utf-8")),
        "plan_hash": "a" * 64,
        "evidence_plan_hash": "b" * 64,
        "business_files": [
            {
                "artifact_id": "scan.report",
                "relative_path": "report/summary.md",
                "sha256": sha256_bytes(payload),
                "bytes": len(payload),
                "media_type": "text/markdown",
            }
        ],
        "state_mutations": [],
        "generated_at": datetime(2026, 9, 14, tzinfo=timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "bundle_hash": "0" * 64,
    }
    bundle["bundle_hash"] = publication_bundle_hash(bundle)
    reports = ws.run_reports_root(handle.contract.run_kind)
    receipt = execute_publication(
        handle,
        bundle,
        artifact_reader=lambda artifact_id: payload,
        reports_root=reports,
        state_root=tmp_path / "state",
        finalizer=lambda canonical: capsule._finalize_unlocked(
            handle.run_id,
            BusinessStatus.SUCCEEDED,
            report_dir=canonical,
        ),
        now=datetime(2026, 9, 14, 0, 1, tzinfo=timezone.utc),
    )
    report = reports / receipt["canonical_path"] / "report/summary.md"

    result = verify_report(report, expected_run_id=handle.run_id, level="integrity")

    assert result["report_covered"] is True
    assert result["publication_ok"] is True
    assert result["orchestration"] == "legacy"
    assert result["orchestration_verified"] is True


def test_verify_report_cli_returns_machine_contract(published_report, capsys):
    from autoresearch.session_agent.__main__ import main

    handle, report, _, _ = published_report
    exit_code = main(
        [
            "verify-report",
            "--report-path",
            str(report),
            "--expected-run-id",
            handle.run_id,
            "--level",
            "integrity",
        ]
    )

    result = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert result["run_id"] == handle.run_id
    assert result["report_covered"] is True


def test_session_origin_is_reverified_from_frozen_plan_and_host_profile(
    tmp_path,
    monkeypatch,
):
    from autoresearch.session_agent import service
    from autoresearch.trace import capsule
    from tests.session_agent.test_service import _request

    redirect_roots(monkeypatch, tmp_path)
    started = service.begin(_request())
    handle = capsule.require_active_run(started["run_id"])
    report_dir = ws.run_reports_root("stock-research") / handle.run_id
    report_dir.mkdir(parents=True)
    report = report_dir / "summary.md"
    report.write_text("# session_v1 report\n", encoding="utf-8")
    checkpoint(handle.run_id, "harvest", "SUCCEEDED", [], {})
    finalize(handle.run_id, BusinessStatus.SUCCEEDED, report_dir)

    result = verify_report(report, expected_run_id=handle.run_id, level="integrity")

    assert result["orchestration"] == "session_v1"
    assert result["orchestration_verified"] is True
    assert not any(reason.startswith("SESSION_") for reason in result["missing"])


def test_full_verification_recomputes_instead_of_trusting_stored_closure(
    tmp_path,
    monkeypatch,
):
    handle = begin_fixture_run(tmp_path, monkeypatch)
    plan = {
        "schema_version": 1,
        "engine": handle.engine,
        "run_id": handle.run_id,
        "plan_hash": "a" * 64,
        "expansion_hashes": [],
        "task_keys": [
            {
                "task_id": "branch.not-reached",
                "attempt": 1,
                "owner": "SESSION",
                "subject": None,
                "state": "NOT_REACHED",
                "superseded_by": None,
                "requirements": [],
            }
        ],
        "closure_cutoff": "2026-09-14T00:00:00Z",
        "scope": ["session_v1"],
        "evidence_plan_hash": "0" * 64,
    }
    plan["evidence_plan_hash"] = evidence_plan_hash(plan)
    evidence = handle.capsule / "evidence/evidence_plan.json"
    evidence.parent.mkdir(parents=True)
    evidence.write_text(canonical_json(plan), encoding="utf-8")
    stored = handle.capsule / "verification/evidence_closure.json"
    stored.parent.mkdir(parents=True)
    stored.write_text(
        canonical_json(
            {
                "schema_version": 1,
                "engine": handle.engine,
                "run_id": handle.run_id,
                "evidence_plan_hash": plan["evidence_plan_hash"],
                "required_tasks": 1,
                "present_tasks": 0,
                "completeness_ok": False,
                "missing": ["STALE_STORED_RESULT"],
            }
        ),
        encoding="utf-8",
    )
    report_dir = ws.run_reports_root("scan-market") / handle.run_id
    report_dir.mkdir(parents=True)
    report = report_dir / "summary.md"
    report.write_text("# recompute closure\n", encoding="utf-8")
    checkpoint(handle.run_id, "l3", "SUCCEEDED", [], {})
    finalize(handle.run_id, BusinessStatus.SUCCEEDED, report_dir)

    result = verify_report(report, expected_run_id=handle.run_id, level="full")

    assert result["compute_status"] == "FULL"
    assert result["completeness_ok"] is True
    assert "STORED_EVIDENCE_CLOSURE_DIFFERS" in result["diffs"]
