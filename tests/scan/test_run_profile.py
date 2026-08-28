"""Mode- and terminal-state-aware evidence expectations for scan runs."""

from __future__ import annotations

import pytest

from autoresearch.scan.run_profile import (
    SCAN_AGENT_ROLES,
    SCAN_STAGES,
    scan_profile,
)


def test_sentinel_mode_drops_the_per_stock_leg_entirely():
    profile = scan_profile(mode="SENTINEL_EMPTY")

    assert "l4" not in profile.expected_stages
    assert profile.role_expected("l4-card") is False
    assert profile.role_expected("strategist") is True


@pytest.mark.parametrize("mode", ["FULL", "FORCED_FULL"])
def test_full_modes_keep_every_stage_and_role(mode):
    profile = scan_profile(mode=mode)

    assert profile.expected_stages == SCAN_STAGES
    assert profile.agent_roles == SCAN_AGENT_ROLES
    assert all(profile.role_expected(role) for role in SCAN_AGENT_ROLES)


def test_failed_run_marks_only_downstream_stages_unreached():
    profile = scan_profile(business_status="FAILED", last_stage="l3")

    assert profile.stage_reached("frame") is True
    assert profile.stage_reached("l3") is True
    assert profile.stage_reached("gate2") is False
    assert profile.stage_reached("l4") is False


def test_successful_run_reaches_every_stage_regardless_of_last_stage():
    profile = scan_profile(business_status="SUCCEEDED", last_stage="l3")

    assert all(profile.stage_reached(stage) for stage in SCAN_STAGES)


def test_unknown_mode_or_status_is_rejected_not_guessed():
    with pytest.raises(ValueError, match="unknown run mode"):
        scan_profile(mode="TURBO")
    with pytest.raises(ValueError, match="unknown business status"):
        scan_profile(business_status="MAYBE")
