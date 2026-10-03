"""Publication consumers resolve artifacts even when fixed work copies differ."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from autoresearch.common.atomic import sha256_bytes
from autoresearch.session_agent import artifacts
from autoresearch.session_agent.workflows import dossier, macro, scan, sector


def _handle(tmp_path):
    handle = SimpleNamespace(workspace=tmp_path, staging=tmp_path / "staging",
                             engine="codex", run_id="20260930T010203000000Z")
    (tmp_path / "session").mkdir()
    (tmp_path / "session/request.json").write_text(json.dumps({
        "analysis_date": "2026-09-30", "subject": "600519", "name": "fixture",
    }))
    (handle.staging / "session_outputs").mkdir(parents=True)
    (handle.staging / "resolved").mkdir()
    return handle


def _bytes(value):
    return value if isinstance(value, bytes) else json.dumps(value).encode()


def _install(handle, artifact_id, filename, accepted, work_copy):
    """Exercise the real resolver independently of the snapshot producer tests."""
    path = handle.staging / "resolved" / artifact_id
    path.write_bytes(_bytes(accepted))
    artifacts.register_artifact(handle, artifact_id, path, "READ")
    (handle.staging / "session_outputs" / filename).write_bytes(_bytes(work_copy))


@pytest.mark.parametrize("kind", ["macro", "sector", "dossier"])
def test_prepare_reads_resolved_metadata(tmp_path, kind):
    handle = _handle(tmp_path)
    if kind == "macro":
        _install(handle, "macro.publication.bundle", "macro.publication.json",
                 {"mode": "FULL", "output_name": "accepted.md"},
                 {"mode": "LITE", "output_name": "wrong.md"})
        value = macro.prepare_macro_bundle(handle)
        assert value["business_files"][0]["relative_path"] == "report/accepted.md"
        assert value["state_mutations"][0]["after_artifact_id"] == "macro.state.candidate"
    elif kind == "sector":
        _install(handle, "sector.publication.bundle", "sector.publication.json",
                 {"industry": "accepted"}, {"industry": "wrong"})
        assert sector.prepare_sector_bundle(handle)["business_files"][0]["relative_path"] == "report/accepted.md"
    else:
        _install(handle, "dossier.permissions", "dossier.permissions.json",
                 {"opening_target_sha256": "a" * 64}, {"opening_target_sha256": "b" * 64})
        _install(handle, "dossier.publication.bundle", "dossier.publication.json",
                 {"pool_before_sha256": "c" * 64}, {"pool_before_sha256": "d" * 64})
        mutations = dossier.prepare_dossier_bundle(handle)["state_mutations"]
        assert [item["expected_before_hash"] for item in mutations] == ["a" * 64, "c" * 64]


def test_macro_compatibility_reads_resolved_report_and_state(tmp_path):
    handle = _handle(tmp_path)
    report, wrong = b"accepted macro", b"wrong macro"
    _install(handle, "macro.publication.bundle", "macro.publication.json",
             {"mode": "FULL", "output_name": "accepted.md", "report_sha256": sha256_bytes(report)},
             {"mode": "FULL", "output_name": "wrong.md", "report_sha256": sha256_bytes(wrong)})
    _install(handle, "macro.full.report", "macro.full.report.md", report, wrong)
    state = {"as_of": "2026-09-30", "session_run_id": handle.run_id, "marker": "accepted"}
    _install(handle, "macro.state.candidate", "macro_state.json", state, dict(state, marker="wrong"))
    latest = tmp_path / "latest.json"
    target = macro._publish_macro_active(handle, reports_root=tmp_path / "reports", state_path=latest)
    assert target.name == "accepted.md"
    assert target.read_bytes() == report
    assert json.loads(latest.read_bytes()) == state


def test_sector_compatibility_reads_resolved_report(tmp_path):
    handle = _handle(tmp_path)
    report, wrong = b"accepted sector", b"wrong sector"
    _install(handle, "sector.publication.bundle", "sector.publication.json",
             {"analysis_date": "20260930", "industry": "accepted", "report_sha256": sha256_bytes(report)},
             {"analysis_date": "20260930", "industry": "wrong", "report_sha256": sha256_bytes(wrong)})
    _install(handle, "sector.report", "sector.md", report, wrong)
    target = sector._publish_sector_active(handle, reports_root=tmp_path / "reports")
    assert target.name == "accepted.md"
    assert target.read_bytes() == report


def test_dossier_compatibility_reads_resolved_permissions_and_candidates(tmp_path):
    handle = _handle(tmp_path)
    before, candidate, pool_bytes = b"old dossier", b"accepted dossier", b'{"stocks": {}}'
    _install(handle, "dossier.publication.bundle", "dossier.publication.json",
             {"candidate_sha256": sha256_bytes(candidate), "pool_before_sha256": None,
              "pool_after_sha256": sha256_bytes(pool_bytes)},
             {"candidate_sha256": sha256_bytes(b"wrong"), "pool_after_sha256": "f" * 64})
    _install(handle, "dossier.permissions", "dossier.permissions.json",
             {"opening_target_sha256": sha256_bytes(before)}, {"opening_target_sha256": "b" * 64})
    _install(handle, "dossier.candidate", "dossier.candidate.md", candidate, b"wrong")
    _install(handle, "dossier.pool.candidate", "dossier.pool.candidate.json", pool_bytes, b"wrong")
    target, pool_target = tmp_path / "live.md", tmp_path / "pool.json"
    target.write_bytes(before)
    dossier._publish_dossier_active(handle, target_path=target, pool_path=pool_target)
    assert target.read_bytes() == candidate
    assert pool_target.read_bytes() == pool_bytes


def test_scan_compatibility_reads_resolved_pool_candidate(tmp_path, monkeypatch):
    from autoresearch.dossier import pool

    handle = _handle(tmp_path)
    candidate = handle.staging / "report_candidate"
    candidate.mkdir()
    (candidate / "summary.md").write_text("accepted report")
    pool_bytes = b'{"stocks": {}}'
    bundle = {"run_id": handle.run_id, "engine": handle.engine, "folder": "published",
              "candidate_relative": candidate.relative_to(handle.workspace).as_posix(),
              "files": scan.directory_manifest(candidate), "pool_mutation": True,
              "pool_before_sha256": None, "pool_after_sha256": sha256_bytes(pool_bytes)}
    _install(handle, "scan.publication.bundle", "scan.publication.json", bundle, bundle)
    _install(handle, "scan.pool.candidate", "scan.pool.candidate.json", pool_bytes, b"wrong")
    target = tmp_path / "pool.json"
    monkeypatch.setattr(pool, "POOL_PATH", target)
    scan._publish_scan_active(handle, reports_root=tmp_path / "reports")
    assert target.read_bytes() == pool_bytes
