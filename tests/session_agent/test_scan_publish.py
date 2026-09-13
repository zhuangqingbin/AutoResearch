from __future__ import annotations

import json
from types import SimpleNamespace

from autoresearch.session_agent import artifacts
from autoresearch.session_agent.workflows.scan import publish_scan


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
