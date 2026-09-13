from __future__ import annotations

import re
from types import SimpleNamespace

from autoresearch.session_agent.workflows.sector import build_sector_plan

from .test_service import _profile


def _request(mode="FULL", subject="电子"):
    return {
        "schema_version": 1,
        "kind": "sector-research",
        "requested_mode": mode,
        "analysis_date": "2026-09-13",
        "subject": subject,
        "peers": [],
        "asset_type": None,
        "name": None,
        "host_profile": _profile(),
        "predecessor_run_id": None,
    }


def _context(tmp_path):
    return SimpleNamespace(
        run_id="20260913T010203000000Z",
        engine="codex",
        workspace=tmp_path,
        staging=tmp_path / "staging/2026-09-13",
        contract=SimpleNamespace(contract_hash="a" * 64, config_hash="b" * 64),
    )


def test_sector_plan_uses_stable_ascii_task_ids_and_fixed_chains(tmp_path):
    full = build_sector_plan(_request(), _context(tmp_path))
    lite = build_sector_plan(_request("LITE"), _context(tmp_path))
    for plan in (full, lite):
        assert all(re.fullmatch(r"[a-z0-9_.-]+", task["task_id"]) for task in plan["tasks"])
        assert all("电子" not in task["task_id"] for task in plan["tasks"])
    assert [task["task_id"].split(".")[-1] for task in lite["tasks"]] == [
        "prepare",
        "brief",
        "validate",
        "publish",
    ]
    assert [task["role"] for task in full["tasks"] if task["kind"] == "INFERENCE"] == [
        "sector.intel",
        "sector.research",
    ]

