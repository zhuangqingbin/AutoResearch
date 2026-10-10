"""Headless recovery continues the original owner or publication transaction."""
import json
from types import SimpleNamespace

import pytest

from autoresearch.common import workspace as ws
from autoresearch.scan import scan_run
from autoresearch.trace import capsule, publication


def _case(tmp_path, monkeypatch, state="ACTIVE"):
    workspace = tmp_path / "context_codex/scan_runs/run"
    staging = workspace / "staging/2026-10-08"
    staging.mkdir(parents=True)
    (workspace / "session").mkdir()
    (workspace / "state.json").write_text(json.dumps({"business_status": state}))
    (workspace / "session/request.json").write_text(json.dumps({"host_profile": {
        "engine": "codex", "session_ref": "headless-original"}}))
    handle = SimpleNamespace(workspace=workspace, staging=staging, engine="codex",
        analysis_date="2026-10-08", contract=SimpleNamespace(run_kind="scan-market"))
    monkeypatch.setattr(ws, "ENGINE", "codex")
    monkeypatch.setattr(capsule, "load_run", lambda _: handle)
    return handle


@pytest.mark.parametrize("journal_state", ["VIEWS_APPLIED", "COMMITTED"])
def test_sealed_publication_resumes_finish_without_new_inference(tmp_path, monkeypatch, journal_state):
    handle = _case(tmp_path, monkeypatch, "SUCCEEDED")
    monkeypatch.setattr(publication, "load_publication_journal", lambda _: {"state": journal_state})
    calls = []
    canonical = str(tmp_path / "reports_codex/scan/runs/run")
    def call(argv, **kwargs):
        calls.append(argv[3])
        return 0, json.dumps({"state": "DONE", "result": {"publication": {
            "canonical_path": canonical}}})
    monkeypatch.setattr(scan_run, "_call", call)
    steps = scan_run.default_steps(SimpleNamespace(resume_run_id="run"), None)
    assert steps.resume("run") == handle.analysis_date
    outcome = steps.run("run", 60)
    assert outcome["finished"] and outcome["finish"]["canonical_path"] == canonical
    assert calls == ["finish"]


def test_active_resume_repairs_receipts_then_drives_original_graph(tmp_path, monkeypatch):
    _case(tmp_path, monkeypatch)
    calls = []
    def call(argv, **kwargs):
        calls.append(argv[3])
        return 0, json.dumps({"finished": True, "stop_reason": "FINISHED"})
    monkeypatch.setattr(scan_run, "_call", call)
    outcome = scan_run.default_steps(SimpleNamespace(resume_run_id="run"), None).run("run", 60)
    assert outcome["finished"] and calls == ["resume", "run"]


def test_terminal_failed_run_is_never_reactivated(tmp_path, monkeypatch):
    _case(tmp_path, monkeypatch, "FAILED")
    monkeypatch.setattr(scan_run, "_call", lambda *a, **k: pytest.fail("terminal run mutated"))
    with pytest.raises(RuntimeError, match="ACTIVE"):
        scan_run.default_steps(SimpleNamespace(resume_run_id="run"), None).resume("run")
