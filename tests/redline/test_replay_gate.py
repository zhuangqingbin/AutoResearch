"""Token growth guard M9 / red line R1: the zero-inference replay gate."""
from __future__ import annotations

import pytest

from autoresearch.common import workspace as ws
from autoresearch.scan import replay_gate


def test_gate_takes_the_newest_readable_run_and_fails_on_regressions(monkeypatch):
    monkeypatch.setattr(replay_gate, "candidate_runs", lambda: ["r3", "r2", "r1"])

    def replay(run_id):
        if run_id == "r3":
            raise FileNotFoundError("not a session_v1 workspace")
        return {"run_id": run_id, "checked": 4, "regressions": [{"task_id": "l4.x.card", "now": "REJECT"}]}

    monkeypatch.setattr(replay_gate, "replay", replay)
    result = replay_gate.gate(acknowledged=lambda run_id: False)
    assert result["verdict"] == "FAIL" and result["run_id"] == "r2" and result["skipped"][0]["run_id"] == "r3"
    assert replay_gate.gate(acknowledged=lambda run_id: run_id == "r2")["verdict"] == "ACKNOWLEDGED"


def test_gate_passes_on_a_clean_replay_and_skips_runs_without_accepted_outputs(monkeypatch):
    monkeypatch.setattr(replay_gate, "candidate_runs", lambda: ["r2", "r1"])
    monkeypatch.setattr(replay_gate, "replay", lambda run_id: {"run_id": run_id, "checked": 0 if run_id == "r2" else 7,
                                                               "regressions": []})
    result = replay_gate.gate(acknowledged=lambda run_id: False)
    assert (result["verdict"], result["run_id"], result["checked"]) == ("PASS", "r1", 7)


def test_no_runs_at_all_is_skipped(monkeypatch):
    monkeypatch.setattr(replay_gate, "candidate_runs", lambda: [])
    assert replay_gate.gate()["verdict"] == "SKIPPED"


def _real_run() -> str | None:
    for run_id in replay_gate.candidate_runs():
        try:
            if replay_gate.replay(run_id)["checked"]:
                return run_id
        except Exception:  # noqa: BLE001
            continue
    return None


def test_a_broken_validator_turns_every_accepted_output_into_a_regression(monkeypatch):
    """Real archived run (gitignored; skipped on a fresh checkout): current code accepts what was
    accepted, and a validator that starts rejecting is caught before any inference is spent."""
    run_id = _real_run()
    if run_id is None:
        pytest.skip(f"no archived {ws.ENGINE} scan run with accepted inference outputs")
    clean = replay_gate.replay(run_id)
    assert clean["regressions"] == []
    from autoresearch.session_agent import validation

    def reject(handle, submission, task):
        raise validation.DomainValidationError("tightened contract")

    monkeypatch.setattr(validation, "validate_registered_domain_contract", reject)
    broken = replay_gate.replay(run_id)
    assert len(broken["regressions"]) == broken["checked"] == clean["checked"]
