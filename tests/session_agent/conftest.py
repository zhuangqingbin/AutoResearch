from __future__ import annotations

from copy import deepcopy

import pytest

from autoresearch.contracts.session_plan import plan_hash

HASH = "a" * 64
RUN_ID = "20260913T010203000000Z"


def make_task(**changes):
    value = {
        "task_id": "step.one",
        "kind": "DETERMINISTIC",
        "role": None,
        "operation": "test.noop",
        "dependencies": [],
        "input_artifact_ids": [],
        "output_artifact_ids": ["step.one.output"],
        "expected_output_contract": "test.output.v1",
        "owner": "SESSION",
        "subject": None,
        "independent_context": False,
        "parent_task": None,
    }
    value.update(changes)
    return value


def make_plan(*, tasks=None, templates=None, **changes):
    value = {
        "schema_version": 1,
        "engine": "codex",
        "run_id": RUN_ID,
        "run_kind": "stock-research",
        "requested_mode": "LITE",
        "analysis_date": "2026-09-13",
        "orchestration_version": "session_v1",
        "input_contract_hash": HASH,
        "config_hash": "b" * 64,
        "host_profile_hash": "c" * 64,
        "roles_hash": "d" * 64,
        "tasks": deepcopy(tasks if tasks is not None else [make_task()]),
        "task_templates": deepcopy(templates if templates is not None else []),
        "plan_hash": "0" * 64,
    }
    value.update(changes)
    value["plan_hash"] = plan_hash(value)
    return value


@pytest.fixture
def two_step_plan():
    return make_plan(tasks=[
        make_task(),
        make_task(
            task_id="step.two",
            kind="INFERENCE",
            role="test.writer",
            operation=None,
            dependencies=["step.one"],
            input_artifact_ids=["step.one.output"],
            output_artifact_ids=["step.two.output"],
            expected_output_contract="test.markdown.v1",
        ),
    ])

