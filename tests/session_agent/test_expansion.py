from __future__ import annotations

import json

import pytest

from autoresearch.contracts.session_plan import expansion_hash, plan_hash
from autoresearch.session_agent.plan import apply_expansion, persist_expansion


def test_expansion_is_write_once_and_same_input_is_idempotent(tmp_path, two_step_plan):
    two_step_plan["task_templates"] = [{
        "template_id": "scan.l4",
        "expander": "scan.l4",
        "depends_on": ["step.one"],
        "allowed_roles": ["test.writer"],
    }]
    two_step_plan["plan_hash"] = plan_hash(two_step_plan)
    value = {
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
    value["expansion_hash"] = expansion_hash(value)
    value["expansion_id"] = f"scan.l4-{value['expansion_hash'][:16]}"
    path = persist_expansion(tmp_path, two_step_plan, value)
    assert persist_expansion(tmp_path, two_step_plan, value) == path
    changed = json.loads(json.dumps(value))
    changed["input_artifacts"][0]["sha256"] = "f" * 64
    changed["expansion_hash"] = expansion_hash(changed)
    changed["expansion_id"] = f"scan.l4-{changed['expansion_hash'][:16]}"
    with pytest.raises(RuntimeError, match="template input conflict"):
        persist_expansion(tmp_path, two_step_plan, changed)


def test_later_expansion_can_depend_on_an_earlier_expansion(two_step_plan):
    two_step_plan["task_templates"] = [
        {
            "template_id": "scan.l3",
            "expander": "scan.l3",
            "depends_on": ["step.one"],
            "allowed_roles": ["test.writer"],
        },
        {
            "template_id": "scan.l4",
            "expander": "scan.l4",
            "depends_on": ["step.one"],
            "allowed_roles": ["test.writer"],
        },
    ]
    two_step_plan["plan_hash"] = plan_hash(two_step_plan)

    def expansion(template_id, task_id, dependency):
        value = {
            "schema_version": 1,
            "expansion_id": "",
            "plan_hash": two_step_plan["plan_hash"],
            "template_id": template_id,
            "input_artifacts": [
                {"artifact_id": f"{template_id}.input", "sha256": "e" * 64}
            ],
            "tasks": [{
                **two_step_plan["tasks"][1],
                "task_id": task_id,
                "dependencies": [dependency],
            }],
            "expansion_hash": "",
        }
        value["expansion_hash"] = expansion_hash(value)
        value["expansion_id"] = f"{template_id}-{value['expansion_hash'][:16]}"
        return value

    first = expansion("scan.l3", "scan.l3.done", "step.one")
    second = expansion("scan.l4", "scan.l4.done", "scan.l3.done")
    tasks = apply_expansion(two_step_plan, first)
    tasks = apply_expansion(two_step_plan, second, existing_tasks=tasks)
    assert tasks[-1]["dependencies"] == ["scan.l3.done"]
