from __future__ import annotations

import json
from types import SimpleNamespace

from autoresearch.common.atomic import sha256_bytes
from autoresearch.session_agent import artifacts
from autoresearch.session_agent.workflows.scan import prepare_scan_bundle, publish_scan


def test_scan_publisher_copies_the_verified_whole_bundle_atomically(tmp_path):
    workspace = tmp_path / "run"
    staging = workspace / "staging/2026-09-13"
    session = workspace / "session"
    candidate = staging / "session_outputs/report_build/20260913-0913_1200"
    candidate.mkdir(parents=True)
    session.mkdir(parents=True)
    for name, body in {
        "brief.md": "brief",
        "summary.md": "summary",
        "appendix.md": "appendix",
        "manifest.json": "{}",
        "details/600519.md": "card",
    }.items():
        path = candidate / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    handle = SimpleNamespace(
        run_id="20260913T010203000000Z",
        engine="codex",
        workspace=workspace,
        staging=staging,
    )
    from autoresearch.session_agent.workflows.scan import directory_manifest

    bundle = {
        "schema_version": 1,
        "run_id": handle.run_id,
        "engine": "codex",
        "analysis_date": "2026-09-13",
        "folder": candidate.name,
        "candidate_relative": candidate.relative_to(workspace).as_posix(),
        "files": directory_manifest(candidate),
    }
    bundle_path = staging / "session_outputs/scan.publication.json"
    bundle_path.parent.mkdir(parents=True, exist_ok=True)
    bundle_path.write_text(json.dumps(bundle))
    artifacts.register_artifact(handle, "scan.publication.bundle", bundle_path, "WRITE")
    artifacts.bind_artifact_hash(handle, "scan.publication.bundle")
    target = publish_scan(handle, reports_root=tmp_path / "reports_codex/scan")
    assert (target / "brief.md").read_text() == "brief"
    assert (target / "details/600519.md").read_text() == "card"
    assert publish_scan(handle, reports_root=tmp_path / "reports_codex/scan") == target


def test_scan_publication_carries_frozen_pool_mutation(tmp_path, monkeypatch):
    from autoresearch.dossier import pool as dossier_pool
    from autoresearch.session_agent.workflows.scan import directory_manifest

    workspace = tmp_path / "run"
    staging = workspace / "staging/2026-09-13"
    candidate = staging / "session_outputs/report_build/report"
    candidate.mkdir(parents=True)
    (candidate / "brief.md").write_text("brief", encoding="utf-8")
    pool_path = tmp_path / "context/coverage_pool.json"
    pool_path.parent.mkdir(parents=True)
    before = b'{"stocks":{},"cap":30}\n'
    after = b'{"stocks":{},"cap":30,"pending_init":[{"code":"600519"}]}\n'
    pool_path.write_bytes(before)
    pool_candidate = staging / "session_outputs/scan.pool.candidate.json"
    pool_candidate.write_bytes(after)
    monkeypatch.setattr(dossier_pool, "POOL_PATH", pool_path)
    handle = SimpleNamespace(
        run_id="20260913T010203000000Z",
        engine="codex",
        workspace=workspace,
        staging=staging,
    )
    publication = {
        "schema_version": 1,
        "run_id": handle.run_id,
        "engine": "codex",
        "analysis_date": "2026-09-13",
        "folder": "report",
        "candidate_relative": candidate.relative_to(workspace).as_posix(),
        "files": directory_manifest(candidate),
        "pool_mutation": True,
        "pool_before_sha256": sha256_bytes(before),
        "pool_after_sha256": sha256_bytes(after),
    }
    publication_path = staging / "session_outputs/scan.publication.json"
    publication_path.write_text(json.dumps(publication), encoding="utf-8")
    for artifact_id, path in {
        "scan.publication.bundle": publication_path,
        "scan.pool.candidate": pool_candidate,
    }.items():
        artifacts.register_artifact(handle, artifact_id, path, "WRITE")
        artifacts.bind_artifact_hash(handle, artifact_id)

    prepared = prepare_scan_bundle(handle)
    target = publish_scan(handle, reports_root=tmp_path / "reports_codex/scan")

    assert prepared["state_mutations"] == [
        {
            "target_key": "dossier.coverage_pool",
            "expected_before_hash": sha256_bytes(before),
            "after_artifact_id": "scan.pool.candidate",
            "apply_policy": "CAS_REPLACE",
        }
    ]
    assert (target / "brief.md").read_text(encoding="utf-8") == "brief"
    assert pool_path.read_bytes() == after
