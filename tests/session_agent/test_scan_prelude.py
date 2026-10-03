from __future__ import annotations

from types import SimpleNamespace

from autoresearch.scan.prelude import STEP_NAMES
from autoresearch.session_agent.workflows.scan import build_scan_plan

from .test_service import _profile


def request():
    return {
        "schema_version": 1,
        "kind": "scan-market",
        "requested_mode": "AUTO",
        "analysis_date": "2026-09-13",
        "subject": None,
        "peers": [],
        "asset_type": None,
        "name": None,
        "force_full": False,
        "host_profile": _profile(),
        "predecessor_run_id": None,
    }


def context(tmp_path):
    return SimpleNamespace(
        run_id="20260913T010203000000Z",
        engine="codex",
        workspace=tmp_path,
        staging=tmp_path / "staging/2026-09-13",
        contract=SimpleNamespace(
            contract_hash="a" * 64,
            config_hash="b" * 64,
            user_config={},
        ),
    )


def test_scan_plan_starts_with_parallel_safe_fixed_prelude(tmp_path):
    plan = build_scan_plan(request(), context(tmp_path))
    tasks = {task["task_id"]: task for task in plan["tasks"]}
    assert tasks["scan.frame"]["dependencies"] == []
    assert tasks["scan.prelude"]["dependencies"] == []
    assert tasks["scan.market_view"]["dependencies"] == ["scan.frame"]
    assert set(tasks["scan.gate1"]["dependencies"]) == {
        "scan.prelude",
        "scan.market_view",
    }
    assert [template["template_id"] for template in plan["task_templates"]] == [
        "scan.sectors",
        "scan.l3",
        "scan.l3.repair",
        "scan.l4",
        "scan.reviews",
        "scan.review3",
        "scan.review.join",
    ]


def test_prelude_step_source_is_the_existing_module_constant():
    assert STEP_NAMES
    assert len(STEP_NAMES) == len(set(STEP_NAMES))
