from __future__ import annotations

from autoresearch.common import workspace as ws
from autoresearch.contracts import profiles, stages
from autoresearch.session_agent import service

from .test_macro import _request


def test_macro_run_kind_is_registered_across_lifecycle_tables():
    assert "macro-research" in stages.RUN_KINDS
    assert ws.RUN_SPOOLS["macro-research"] == "macro_runs"
    assert ws.RUN_REPORT_DIRS["macro-research"] == "macro"
    assert profiles.profile_factory("macro-research").__name__ == "macro_profile"


def test_macro_profiles_have_their_own_stages_and_roles():
    factory = profiles.profile_factory("macro-research")
    full = factory(mode="FULL")
    lite = factory(mode="LITE")
    assert full.expected_stages == ("harvest", "intel", "write", "assemble", "publish")
    assert lite.expected_stages == ("frame", "write", "publish")
    assert full.captured_stages == ()
    assert lite.captured_stages == ()
    assert full.role_stages == {
        "macro.research": "write",
        "macro.brief": "write",
    }


def test_macro_begin_allocates_the_macro_spool(tmp_path, monkeypatch):
    context_root = tmp_path / "context_codex"
    reports_root = tmp_path / "reports_codex"
    monkeypatch.setattr(ws, "context_root", lambda: context_root)
    monkeypatch.setattr(ws, "reports_root", lambda: reports_root)
    result = service.begin(_request("LITE"))
    assert result["state"] == "READY"
    assert (context_root / "macro_runs" / result["run_id"] / "session/plan.json").is_file()
