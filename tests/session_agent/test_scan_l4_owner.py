from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from autoresearch.session_agent import legacy_scan
from autoresearch.session_agent.plan import ready_tasks


def _handle(tmp_path):
    staging = tmp_path / "staging/2026-09-13"
    staging.mkdir(parents=True)
    return SimpleNamespace(
        run_id="20260913T010203000000Z",
        analysis_date="2026-09-13",
        staging=staging,
    )


def test_ticket_claim_is_owned_by_the_existing_l4_taskbook(tmp_path):
    handle = _handle(tmp_path)
    (handle.staging / "_l4_prompt_600519.md").write_text("task pack")
    initialized = legacy_scan.initialize_tickets(
        handle,
        ["600519"],
        meta={"600519": {"ticker": "600519.SS", "pinned": True}},
        caps={"tushare": 1, "web_search": 1, "web_fetch": 1, "l4_stock": 2},
    )
    assert initialized["ok"] is True
    receipt = legacy_scan.claim_ticket(handle, "600519", 1)
    assert receipt["action"] == "RUN"
    assert receipt["attempt"] == 1
    assert legacy_scan.ticket_states(handle) == {"l4.600519.a1": "RUNNING"}
    legacy_scan.validate_parent(
        handle, {"owner": "L4_TASKBOOK", "subject": "600519", "attempt": 1}
    )


def test_old_child_attempt_cannot_attach_to_a_new_ticket_attempt(tmp_path):
    handle = _handle(tmp_path)
    (handle.staging / "_l4_prompt_600519.md").write_text("task pack")
    legacy_scan.initialize_tickets(handle, ["600519"])
    legacy_scan.claim_ticket(handle, "600519", 1)
    book = handle.staging / "_l4_tasks.json"
    payload = json.loads(book.read_text())
    payload["tasks"]["600519"]["attempt"] = 2
    payload["tasks"]["600519"]["status"] = "RUNNING"
    book.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="parent ticket"):
        legacy_scan.validate_parent(
            handle,
            {"owner": "L4_TASKBOOK", "subject": "600519", "attempt": 1},
        )


def test_parent_ticket_must_be_running_before_child_becomes_ready():
    owner = {
        "task_id": "l4.600519.a1",
        "kind": "DETERMINISTIC",
        "role": None,
        "operation": "scan.l4.ticket",
        "dependencies": ["scan.l4.prepare"],
        "input_artifact_ids": ["scan.l4.taskbook"],
        "output_artifact_ids": ["scan.l4.600519.a1.ticket"],
        "expected_output_contract": "scan.l4.ticket.v1",
        "owner": "L4_TASKBOOK",
        "subject": "600519",
        "independent_context": False,
        "parent_task": None,
    }
    child = {
        **owner,
        "task_id": "l4.600519.a1.card",
        "kind": "INFERENCE",
        "role": "scan.l4.card",
        "operation": None,
        "input_artifact_ids": ["scan.l4.600519.a1.prompt"],
        "output_artifact_ids": ["scan.l4.600519.a1.card"],
        "owner": "SESSION",
        "parent_task": {
            "owner": "L4_TASKBOOK",
            "subject": "600519",
            "attempt": 1,
        },
    }
    tasks = [owner, child]
    states = {
        "scan.l4.prepare": "SUCCEEDED",
        owner["task_id"]: "PENDING",
        child["task_id"]: "PENDING",
    }
    assert [task["task_id"] for task in ready_tasks(tasks, states)] == [owner["task_id"]]
    states[owner["task_id"]] = "RUNNING"
    assert [task["task_id"] for task in ready_tasks(tasks, states)] == [child["task_id"]]
