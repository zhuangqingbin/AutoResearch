from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from autoresearch.common.atomic import canonical_json, sha256_bytes
from autoresearch.contracts.publication import publication_receipt_hash
from autoresearch.session_agent import artifacts, publication


def _receipt(run_id: str = "20260913T010203000000Z") -> dict:
    value = {
        "schema_version": 1,
        "engine": "codex",
        "run_id": run_id,
        "publication_id": "p1",
        "bundle_hash": "a" * 64,
        "capsule_root_hash": "b" * 64,
        "canonical_path": f"runs/{run_id}/p1",
        "committed_at": "2026-09-13T01:02:04Z",
        "state_effects": [],
        "previous_receipt_hash": None,
        "receipt_hash": "0" * 64,
    }
    value["receipt_hash"] = publication_receipt_hash(value)
    return value


def test_predecessor_resolves_only_from_a_valid_committed_receipt(tmp_path, monkeypatch):
    run_id = "20260913T010203000000Z"
    roots = {kind: tmp_path / kind for kind in publication.ws.RUN_REPORT_DIRS}
    monkeypatch.setattr(publication.ws, "run_reports_root", lambda kind: roots[kind])
    path = roots["stock-research"] / "_publications" / run_id / "p1.json"
    path.parent.mkdir(parents=True)
    receipt = _receipt(run_id)
    path.write_text(json.dumps(receipt), encoding="utf-8")
    (path.parents[1] / "receipts.jsonl").write_text(
        canonical_json(receipt) + "\n", encoding="utf-8"
    )
    workspace = tmp_path / "workspace"
    (workspace / "session").mkdir(parents=True)
    (workspace / "session/request.json").write_text(
        json.dumps({"predecessor_run_id": run_id}), encoding="utf-8"
    )
    handle = SimpleNamespace(engine="codex", workspace=workspace)

    assert publication._publication_predecessor(handle) == {
        "engine": "codex",
        "run_id": run_id,
        "publication_id": "p1",
        "root_hash": "b" * 64,
    }

    path.unlink()
    with pytest.raises(RuntimeError, match="exactly one committed receipt"):
        publication._publication_predecessor(handle)


def test_legacy_delivery_sidecar_binds_to_commit_and_rejects_retargeting(tmp_path, monkeypatch):
    reports = tmp_path / "reports/macro"
    delivered = reports / "20260913/macro.md"
    delivered.parent.mkdir(parents=True)
    delivered.write_text("report", encoding="utf-8")
    monkeypatch.setattr(publication.ws, "run_reports_root", lambda kind: reports)
    handle = SimpleNamespace(contract=SimpleNamespace(run_kind="macro-research"))
    receipt = _receipt()

    sidecar = publication._write_delivery_sidecar(handle, delivered, receipt)

    assert sidecar == delivered.with_name("macro.md.delivery.json")
    assert (
        json.loads(sidecar.read_text(encoding="utf-8"))["receipt_hash"] == receipt["receipt_hash"]
    )
    assert publication._write_delivery_sidecar(handle, delivered, receipt) == sidecar
    with pytest.raises(RuntimeError, match="sidecar conflict"):
        publication._write_delivery_sidecar(
            handle,
            delivered,
            {**receipt, "receipt_hash": "f" * 64},
        )


def test_delivery_sidecar_refuses_paths_outside_workflow_root(tmp_path, monkeypatch):
    reports = tmp_path / "reports/macro"
    reports.mkdir(parents=True)
    outside = tmp_path / "elsewhere/report.md"
    outside.parent.mkdir(parents=True)
    outside.write_text("report", encoding="utf-8")
    monkeypatch.setattr(publication.ws, "run_reports_root", lambda kind: reports)
    handle = SimpleNamespace(contract=SimpleNamespace(run_kind="macro-research"))

    with pytest.raises(ValueError):
        publication._write_delivery_sidecar(handle, outside, _receipt())


def test_transactional_finish_seals_canonical_then_delivers_legacy_view(tmp_path, monkeypatch):
    from autoresearch.trace import capsule

    run_id = "20260913T010203000000Z"
    workspace = tmp_path / "context/sector_runs" / run_id
    staging = workspace / "staging/2026-09-13"
    output = staging / "session_outputs"
    identity = workspace / "capsule/identity"
    evidence = workspace / "capsule/evidence"
    for directory in (output, identity, evidence, workspace / "session"):
        directory.mkdir(parents=True, exist_ok=True)
    request = {
        "analysis_date": "2026-09-13",
        "predecessor_run_id": None,
    }
    (workspace / "session/request.json").write_text(json.dumps(request), encoding="utf-8")
    plan = {"plan_hash": "b" * 64, "tasks": [], "task_templates": []}
    (workspace / "session/plan.json").write_text(json.dumps(plan), encoding="utf-8")
    (identity / "execution_origin.json").write_text(
        json.dumps({"origin": "session_v1"}), encoding="utf-8"
    )
    (evidence / "evidence_plan.json").write_text(
        json.dumps({"evidence_plan_hash": "c" * 64, "closure_cutoff": "2026-09-13T02:00:00Z"}),
        encoding="utf-8",
    )
    report = output / "sector.md"
    report.write_text("# 电子行业\n", encoding="utf-8")
    domain_bundle = {
        "analysis_date": "2026-09-13",
        "industry": "电子",
        "report_sha256": sha256_bytes(report.read_bytes()),
    }
    bundle_path = output / "sector.publication.json"
    bundle_path.write_text(json.dumps(domain_bundle), encoding="utf-8")
    handle = SimpleNamespace(
        run_id=run_id,
        engine="codex",
        workspace=workspace,
        staging=staging,
        capsule=workspace / "capsule",
        contract=SimpleNamespace(run_kind="sector-research"),
    )
    for artifact_id, path in {
        "sector.report": report,
        "sector.publication.bundle": bundle_path,
    }.items():
        artifacts.register_artifact(handle, artifact_id, path, "WRITE")
        artifacts.bind_artifact_hash(handle, artifact_id)
    reports_engine = tmp_path / "reports"
    monkeypatch.setattr(publication.ws, "reports_root", lambda: reports_engine)
    monkeypatch.setattr(
        publication.ws,
        "run_reports_root",
        lambda kind: reports_engine / "sector",
    )
    monkeypatch.setattr(publication.ws, "context_root", lambda: tmp_path / "context")
    monkeypatch.setattr(publication.ws, "find_run_root", lambda unused: workspace)
    monkeypatch.setattr(publication, "session_profile", lambda current: None)

    def fake_finalize(unused_run_id, unused_status, *, report_dir, profile):
        assert profile is None
        verification = report_dir / "capsule/verification"
        verification.mkdir(parents=True)
        root_hash = sha256_bytes((report_dir / "report/电子.md").read_bytes())
        (verification / "ROOT.json").write_text(
            canonical_json({"root_hash": root_hash}), encoding="utf-8"
        )
        return {"root_hash": root_hash}

    monkeypatch.setattr(capsule, "_finalize_unlocked", fake_finalize)

    result = publication.transactional_finish(handle)

    canonical = reports_engine / "sector" / result["receipt"]["canonical_path"]
    legacy = reports_engine / "sector/2026-09-13/电子.md"
    assert (canonical / "report/电子.md").read_text(encoding="utf-8") == "# 电子行业\n"
    assert legacy.read_text(encoding="utf-8") == "# 电子行业\n"
    sidecar = json.loads(legacy.with_name("电子.md.delivery.json").read_text(encoding="utf-8"))
    assert sidecar["receipt_hash"] == result["receipt"]["receipt_hash"]
