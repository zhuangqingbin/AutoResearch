from __future__ import annotations

from autoresearch.common import workspace as ws
from autoresearch.contracts import profiles, stages
from autoresearch.session_agent import service

from .test_sector import _request


def test_sector_run_kind_is_registered_across_lifecycle_tables():
    assert "sector-research" in stages.RUN_KINDS
    assert ws.RUN_SPOOLS["sector-research"] == "sector_runs"
    assert ws.RUN_REPORT_DIRS["sector-research"] == "sector"
    assert profiles.profile_factory("sector-research").__name__ == "sector_profile"


def test_sector_profiles_declare_their_actual_roles():
    factory = profiles.profile_factory("sector-research")
    assert factory(mode="LITE").agent_roles == ("sector.brief",)
    assert factory(mode="FULL").agent_roles == ("sector.intel", "sector.research")
    assert factory(mode="FULL").captured_stages == ()


def test_sector_begin_allocates_the_sector_spool(tmp_path, monkeypatch):
    context_root = tmp_path / "context_codex"
    monkeypatch.setattr(ws, "context_root", lambda: context_root)
    monkeypatch.setattr(ws, "reports_root", lambda: tmp_path / "reports_codex")
    result = service.begin(_request("LITE"))
    assert result["state"] == "READY"
    assert (context_root / "sector_runs" / result["run_id"] / "session/plan.json").is_file()
