from __future__ import annotations

import json

import pytest

from autoresearch.contracts.session_plan import expansion_hash
from autoresearch.session_agent.plan import apply_expansion, freeze_plan, ready_tasks


def test_ready_tasks_is_read_only_and_obeys_dependencies(two_step_plan):
    states = {"step.one": "PENDING", "step.two": "PENDING"}
    assert [item["task_id"] for item in ready_tasks(two_step_plan["tasks"], states)] == ["step.one"]
    assert states == {"step.one": "PENDING", "step.two": "PENDING"}
    states["step.one"] = "SUCCEEDED"
    assert [item["task_id"] for item in ready_tasks(two_step_plan["tasks"], states)] == ["step.two"]


def test_freeze_plan_is_idempotent_but_rejects_changed_bytes(tmp_path, two_step_plan):
    path = tmp_path / "session" / "plan.json"
    assert freeze_plan(path, two_step_plan) == path
    assert freeze_plan(path, dict(reversed(list(two_step_plan.items())))) == path
    changed = dict(two_step_plan, analysis_date="2026-09-14")
    with pytest.raises(RuntimeError, match="frozen plan conflict"):
        freeze_plan(path, changed)
    assert json.loads(path.read_text()) == two_step_plan


def test_apply_expansion_checks_template_inputs_roles_and_collisions(two_step_plan):
    two_step_plan["task_templates"] = [{
        "template_id": "scan.l4",
        "expander": "scan.l4",
        "depends_on": ["step.one"],
        "allowed_roles": ["test.writer"],
    }]
    from autoresearch.contracts.session_plan import plan_hash

    two_step_plan["plan_hash"] = plan_hash(two_step_plan)
    expansion = {
        "schema_version": 1,
        "expansion_id": "",
        "plan_hash": two_step_plan["plan_hash"],
        "template_id": "scan.l4",
        "input_artifacts": [{"artifact_id": "scan.finalists", "sha256": "e" * 64}],
        "tasks": [{
            **two_step_plan["tasks"][1],
            "task_id": "l4.600519.a1.card",
            "dependencies": ["step.one"],
        }],
        "expansion_hash": "",
    }
    expansion["expansion_hash"] = expansion_hash(expansion)
    expansion["expansion_id"] = f"scan.l4-{expansion['expansion_hash'][:16]}"
    combined = apply_expansion(two_step_plan, expansion)
    assert [item["task_id"] for item in combined] == [
        "step.one", "step.two", "l4.600519.a1.card"
    ]
    wrong_role = {
            **expansion,
            "tasks": [{**expansion["tasks"][0], "role": "unregistered"}],
    }
    wrong_role["expansion_hash"] = expansion_hash(wrong_role)
    wrong_role["expansion_id"] = f"scan.l4-{wrong_role['expansion_hash'][:16]}"
    with pytest.raises(ValueError, match="role"):
        apply_expansion(two_step_plan, wrong_role)
