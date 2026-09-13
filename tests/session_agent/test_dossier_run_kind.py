from __future__ import annotations

from autoresearch.common import workspace as ws
from autoresearch.contracts import profiles, stages
from autoresearch.session_agent import service

from .test_dossier import _request


def test_dossier_run_kind_and_init_mode_are_registered():
    assert "dossier-init" in stages.RUN_KINDS
    assert ws.RUN_SPOOLS["dossier-init"] == "dossier_runs"
    assert ws.RUN_REPORT_DIRS["dossier-init"] == "dossiers"
    profile = profiles.profile_factory("dossier-init")(mode="INIT")
    assert profile.expected_stages == ("prefetch", "skeleton", "research", "lint", "publish")
    assert profile.agent_roles == ("dossier.init",)
    assert profile.captured_stages == ()


def test_dossier_begin_allocates_the_dossier_spool(tmp_path, monkeypatch):
    context_root = tmp_path / "context_codex"
    monkeypatch.setattr(ws, "context_root", lambda: context_root)
    monkeypatch.setattr(ws, "reports_root", lambda: tmp_path / "reports_codex")
    result = service.begin(_request())
    assert result["state"] == "READY"
    assert (context_root / "dossier_runs" / result["run_id"] / "session/plan.json").is_file()
