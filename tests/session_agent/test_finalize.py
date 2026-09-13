from __future__ import annotations

from autoresearch.analyze.run_profile import analyze_profile
from autoresearch.session_agent import publication, service
from autoresearch.trace.completeness import build_expected, profile_from_capsule, write_expected

from .test_service import _handle, _planner, _request


def test_session_role_stage_mapping_is_frozen_and_restored(tmp_path):
    profile = analyze_profile(
        mode="LITE",
        agent_roles=("stock.card",),
        role_stages={"stock.card": "card"},
    )
    expected = build_expected(profile).to_dict()
    role = next(item for item in expected["items"] if item["key"] == "agent:stock.card")
    assert role["disposition"] == "REQUIRED"
    write_expected(tmp_path, profile)
    restored = profile_from_capsule(tmp_path, kind="stock-research")
    assert restored.role_stages == {"stock.card": "card"}


def test_legacy_profile_keeps_global_role_stage_behavior():
    profile = analyze_profile(mode="FULL", agent_roles=("company-intel",))
    assert profile.role_stages is None
    assert profile.role_expected("company-intel") is True


def test_session_profile_uses_only_roles_in_frozen_plan(tmp_path):
    handle = _handle(tmp_path)
    service.begin(_request(), begin_capsule=lambda request: handle, planner=_planner)
    profile = publication.session_profile(handle)
    assert profile.agent_roles == ("stock.card",)
    assert profile.role_stages == {"stock.card": "card"}
