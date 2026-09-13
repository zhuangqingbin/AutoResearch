from __future__ import annotations

import pytest

from autoresearch.session_agent.workflows.scan import build_scan_plan, sector_expansion

from .test_scan_prelude import context, request


@pytest.mark.parametrize(
    ("mode", "operation"),
    [
        ("FULL", "scan.sector.prepare"),
        ("FORCED_FULL", "scan.sector.prepare"),
        ("SENTINEL_EMPTY", "scan.sector.skip"),
        ("SENTINEL_PINNED", "scan.sector.skip"),
    ],
)
def test_gate1_mode_selects_one_frozen_sector_branch(tmp_path, mode, operation):
    plan = build_scan_plan(request(), context(tmp_path))
    expansion = sector_expansion(
        plan,
        {"mode": mode, "pinned_codes": ["600519"]},
        {"artifact_id": "scan.run_mode", "sha256": "e" * 64},
    )
    assert expansion["template_id"] == "scan.sectors"
    assert expansion["tasks"][0]["operation"] == operation


def test_pinned_branch_uses_the_frozen_run_mode_codes(tmp_path):
    plan = build_scan_plan(request(), context(tmp_path))
    mode = {"mode": "SENTINEL_PINNED", "pinned_codes": ["000001", "600519"]}
    expansion = sector_expansion(
        plan,
        mode,
        {"artifact_id": "scan.run_mode", "sha256": "e" * 64},
    )
    assert [task["subject"] for task in expansion["tasks"]] == [None]
    assert expansion["input_artifacts"][0]["sha256"] == "e" * 64
