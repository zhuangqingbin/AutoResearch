from __future__ import annotations

import json

import pytest

from autoresearch.session_agent import artifacts, service, store
from autoresearch.session_agent.workflows.scan import (
    build_scan_plan,
    register_scan_artifacts,
)

from .test_scan_prelude import context, request


def _prepared(tmp_path):
    handle = context(tmp_path / "context_codex/scan_runs/20260913T010203000000Z")
    handle.workspace.mkdir(parents=True)
    handle.staging.mkdir(parents=True)
    handle.capsule = handle.workspace / "capsule"
    handle.capsule.mkdir()
    plan = build_scan_plan(request(), handle)
    session = handle.workspace / "session"
    session.mkdir()
    (session / "request.json").write_text(json.dumps(request()))
    (session / "plan.json").write_text(json.dumps(plan))
    store.initialize(session / "tasks.json", plan)
    register_scan_artifacts(request(), handle, plan)
    return handle, plan


def test_gate1_completion_persists_and_registers_the_next_branch(tmp_path):
    handle, plan = _prepared(tmp_path)
    mode_path = handle.staging / "run_mode.json"
    mode_path.write_text(json.dumps({"mode": "FULL", "pinned_codes": []}))
    artifacts.bind_artifact_hash(handle, "scan.run_mode")
    task = next(task for task in plan["tasks"] if task["task_id"] == "scan.gate1")
    activated = service._activate_after_task(handle, task)
    assert len(activated) == 1
    assert store.read_entry(
        handle.workspace / "session/tasks.json", "scan.sector.prepare"
    )["state"] == "PENDING"
    assert service._activate_after_task(handle, task) == activated


def test_changed_run_mode_cannot_replace_the_expansion_input(tmp_path):
    handle, plan = _prepared(tmp_path)
    mode_path = handle.staging / "run_mode.json"
    mode_path.write_text(json.dumps({"mode": "FULL", "pinned_codes": []}))
    artifacts.bind_artifact_hash(handle, "scan.run_mode")
    task = next(task for task in plan["tasks"] if task["task_id"] == "scan.gate1")
    service._activate_after_task(handle, task)
    mode_path.write_text(json.dumps({"mode": "SENTINEL_EMPTY", "pinned_codes": []}))
    with pytest.raises(artifacts.ArtifactConflict, match="changed"):
        service._activate_after_task(handle, task)


def test_later_scan_expansion_can_depend_on_an_earlier_dynamic_task(tmp_path):
    from autoresearch.session_agent import plan as plan_service
    from autoresearch.session_agent.workflows.scan import review_expansion

    handle, plan = _prepared(tmp_path)
    earlier = {
        **review_expansion(
            plan,
            {"reviews": []},
            [{"artifact_id": "scan.review.plan", "sha256": "f" * 64}],
        )
    }
    # The expansion depends on scan.l4.skip from a previous expansion, so the
    # persisted validator must see the accumulated task graph.
    previous = {
        "task_id": "scan.l4.skip",
        "kind": "DETERMINISTIC",
        "role": None,
        "operation": "scan.l4.skip",
        "dependencies": ["scan.gate1"],
        "input_artifact_ids": ["scan.run_mode"],
        "output_artifact_ids": ["scan.review.plan"],
        "expected_output_contract": "scan.l4.plan.v1",
        "owner": "SESSION",
        "subject": None,
        "independent_context": False,
        "parent_task": None,
    }
    path = plan_service.persist_expansion(
        handle.workspace / "session",
        plan,
        earlier,
        existing_tasks=[*plan["tasks"], previous],
    )
    assert path.is_file()
