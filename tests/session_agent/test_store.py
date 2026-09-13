from __future__ import annotations

import json
import subprocess
import sys

import pytest
from conftest import HASH, RUN_ID

from autoresearch.contracts.session_plan import plan_hash
from autoresearch.session_agent import store


def _submission(plan_hash: str, *, digest: str = "e" * 64):
    return {
        "schema_version": 1,
        "envelope": {
            "schema_version": 1,
            "engine": "codex",
            "run_id": RUN_ID,
            "task_id": "step.two",
            "role": "test.writer",
            "input_artifact_ids": ["step.one.output"],
            "input_contract_hash": HASH,
            "expected_output_contract": "test.markdown.v1",
            "attempt": 1,
        },
        "plan_hash": plan_hash,
        "outputs": [{"artifact_id": "step.two.output", "sha256": digest}],
        "host_receipt_id": None,
    }


def test_initialize_and_claim_preserve_attempt_identity(tmp_path, two_step_plan):
    path = tmp_path / "session" / "tasks.json"
    store.initialize(path, two_step_plan)
    claim = store.claim(path, "step.one", 1, "session-a")
    assert claim["attempt"] == 1
    assert store.claim(path, "step.one", 1, "session-a") == claim
    with pytest.raises(store.TaskConflict):
        store.claim(path, "step.one", 1, "session-b")
    with pytest.raises(store.TaskConflict):
        store.claim(path, "step.one", 2, "session-a")


def test_two_processes_cannot_claim_same_attempt(tmp_path, two_step_plan):
    path = tmp_path / "session" / "tasks.json"
    store.initialize(path, two_step_plan)
    code = (
        "from autoresearch.session_agent.store import claim; "
        "import sys; "
        "claim(sys.argv[1], 'step.one', 1, sys.argv[2]); print('won')"
    )
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", code, str(path), session],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for session in ("session-a", "session-b")
    ]
    results = [process.communicate(timeout=10) + (process.returncode,) for process in processes]
    assert sum(result[2] == 0 and result[0].strip() == "won" for result in results) == 1


def test_accept_is_idempotent_and_conflicting_output_is_rejected(tmp_path, two_step_plan):
    path = tmp_path / "session" / "tasks.json"
    store.initialize(path, two_step_plan)
    state = json.loads(path.read_text())
    state["tasks"]["step.one"]["state"] = "SUCCEEDED"
    path.write_text(json.dumps(state))
    store.claim(path, "step.two", 1, "session-a")
    submission = _submission(two_step_plan["plan_hash"])
    receipt = store.accept(path, submission, lambda submitted, spec: None)
    assert receipt["status"] == "ACCEPTED"
    assert store.accept(path, submission, lambda submitted, spec: None) == receipt
    with pytest.raises(store.TaskConflict):
        store.accept(
            path,
            _submission(two_step_plan["plan_hash"], digest="f" * 64),
            lambda submitted, spec: None,
        )


def test_store_never_owns_l4_taskbook_state(tmp_path, two_step_plan):
    two_step_plan["tasks"].append({
        **two_step_plan["tasks"][0],
        "task_id": "l4.600519",
        "owner": "L4_TASKBOOK",
        "subject": "600519",
    })
    two_step_plan["plan_hash"] = plan_hash(two_step_plan)
    path = tmp_path / "session" / "tasks.json"
    store.initialize(path, two_step_plan)
    assert "l4.600519" not in json.loads(path.read_text())["tasks"]
    with pytest.raises(KeyError):
        store.claim(path, "l4.600519", 1, "session-a")
