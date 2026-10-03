"""B3 candidate identity is explicit, immutable, and independent of card_source."""
import json
from dataclasses import replace

import pytest

from autoresearch.contracts.session_task import validate_begin_request
from autoresearch.session_agent.research_profile import freeze_research_profile
from autoresearch.trace.completeness import (
    card_research_profile_from_capsule,
    freeze_card_rules,
    profile_from_capsule,
    write_expected,
)

from .test_service import _handle
from .test_stock_lite import _request


def candidate_request(**changes):
    return dict(_request(), schema_version=2, card_research_profile="two-stage-v1", **changes)


def test_v1_is_unchanged_and_v2_explicitly_selects_candidate():
    assert validate_begin_request(_request())["schema_version"] == 1
    assert validate_begin_request(candidate_request())["card_research_profile"] == "two-stage-v1"


@pytest.mark.parametrize("changes", [{"card_research_profile": "unknown"},
                                    {"card_research_profile": None},
                                    {"requested_mode": "FULL"}])
def test_invalid_candidate_request_rejected(changes):
    value = candidate_request()
    value.update(changes)
    with pytest.raises(ValueError):
        validate_begin_request(value)


def test_old_schema_cannot_smuggle_a_candidate():
    value = _request()
    value["card_research_profile"] = "two-stage-v1"
    with pytest.raises(ValueError):
        validate_begin_request(value)


def test_candidate_profile_roundtrip_and_conflicting_refreeze(tmp_path):
    handle = _handle(tmp_path)
    freeze_card_rules(handle.capsule, kind="stock-research")
    freeze_research_profile(handle, candidate_request())
    assert card_research_profile_from_capsule(handle.capsule) == "two-stage-v1"
    profile = profile_from_capsule(handle.capsule)
    assert profile.card_research_profile == "two-stage-v1"
    assert profile.card_source == "legacy_md"
    write_expected(handle.capsule, replace(profile, card_research_profile="single-stage-v1"))
    assert card_research_profile_from_capsule(handle.capsule) == "two-stage-v1"
    with pytest.raises(ValueError, match="frozen"):
        freeze_research_profile(handle, _request())


def test_historical_profile_and_existing_plan_cannot_be_upgraded(tmp_path):
    handle = _handle(tmp_path)
    profile = handle.capsule / "verification/profile.json"
    profile.parent.mkdir(parents=True)
    profile.write_text(json.dumps({"kind": "stock-research"}))
    assert card_research_profile_from_capsule(handle.capsule) == "single-stage-v1"
    plan = handle.workspace / "session/plan.json"
    plan.parent.mkdir(parents=True)
    plan.write_text("{}")
    with pytest.raises(ValueError, match="historical"):
        freeze_research_profile(handle, candidate_request())


def test_unknown_frozen_profile_is_rejected_instead_of_defaulted(tmp_path):
    handle = _handle(tmp_path)
    profile = handle.capsule / "verification/profile.json"
    profile.parent.mkdir(parents=True)
    profile.write_text(json.dumps({"kind": "stock-research", "card_research_profile": "unknown"}))
    with pytest.raises(ValueError, match="unknown"):
        card_research_profile_from_capsule(handle.capsule)
    with pytest.raises(ValueError, match="unknown"):
        profile_from_capsule(handle.capsule)
