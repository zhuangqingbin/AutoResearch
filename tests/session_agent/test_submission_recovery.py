from __future__ import annotations

import json

from autoresearch.common.atomic import atomic_write_json
from autoresearch.session_agent import store


def test_intent_without_owner_commit_is_not_success(tmp_path, two_step_plan):
    path = tmp_path / "session" / "tasks.json"
    store.initialize(path, two_step_plan)
    store.claim(path, "step.one", 1, "session-a")
    intent_dir = path.parent / "receipts"
    intent_dir.mkdir()
    atomic_write_json(intent_dir / "step.one.intent.json", {
        "schema_version": 1,
        "task_id": "step.one",
        "attempt": 1,
        "submission_hash": "f" * 64,
        "outputs": [{"artifact_id": "step.one.output", "sha256": "e" * 64}],
    })
    assert store.recover_receipt(path, "step.one") is None
    assert store.read_states(path)["step.one"] == "RUNNING"


def test_owner_success_without_receipt_reconstructs_same_receipt(tmp_path, two_step_plan):
    path = tmp_path / "session" / "tasks.json"
    store.initialize(path, two_step_plan)
    state = json.loads(path.read_text())
    state["tasks"]["step.one"].update({
        "state": "SUCCEEDED",
        "attempt": 1,
        "session_ref": "session-a",
        "submission_hash": "f" * 64,
        "outputs": [{"artifact_id": "step.one.output", "sha256": "e" * 64}],
    })
    atomic_write_json(path, state)
    first = store.recover_receipt(path, "step.one")
    second = store.recover_receipt(path, "step.one")
    assert first == second
    assert first["status"] == "ACCEPTED"

