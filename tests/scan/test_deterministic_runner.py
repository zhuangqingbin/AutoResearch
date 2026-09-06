"""E2:交接校验 —— 坏 hash / 旧 attempt / 错 engine / 错 run / 缺产物 / 未知角色 全拒。"""
import pytest

from autoresearch.scan import deterministic_runner as runner

RUN = "20260901T000000000001Z"
REGISTERED = {("l4-card", "L4_CARD.v1"), ("l3-rank", "L3_JUDGED.v1")}


def envelope(**changes):
    row = {"schema_version": 1, "engine": "claude", "run_id": RUN, "task_id": "600000",
           "role": "l4-card", "input_artifact_ids": ["prompt", "slim"],
           "input_contract_hash": "a" * 64, "expected_output_contract": "L4_CARD.v1", "attempt": 2}
    return dict(row, **changes)


def task(**changes):
    return dict({"code": "600000", "attempt": 2, "status": "RUNNING"}, **changes)


def verify(env=None, **kw):
    base = {"engine": "claude", "run_id": RUN, "task": task(), "input_hash": "a" * 64,
            "available_artifacts": {"prompt", "slim", "intel"}, "registered_contracts": REGISTERED}
    return runner.verify_handoff(env or envelope(), **{**base, **kw})


def test_matching_handoff_passes():
    assert verify() == envelope()


@pytest.mark.parametrize("changes", [
    {"input_contract_hash": "b" * 64}, {"attempt": 1}, {"attempt": 3}, {"engine": "codex"},
    {"run_id": "20260901T000000000002Z"}, {"task_id": "600001"},
])
def test_stale_or_foreign_handoff_rejected(changes):
    with pytest.raises(ValueError, match="stale or foreign"):
        verify(envelope(**changes))


@pytest.mark.parametrize("status", ["SUCCEEDED", "FAILED", "CLAIMED", None])
def test_task_must_be_running(status):
    with pytest.raises(ValueError, match="stale or foreign"):
        verify(task=task(status=status))


def test_unregistered_input_artifact_rejected():
    with pytest.raises(ValueError, match="missing registered input"):
        verify(envelope(input_artifact_ids=["prompt", "ghost"]))


def test_unknown_role_or_contract_rejected():
    with pytest.raises(ValueError, match="unknown role"):
        verify(envelope(role="l4-oracle"))
    with pytest.raises(ValueError, match="unknown role"):
        verify(envelope(expected_output_contract="ResearchCard.v9"))


def test_malformed_run_id_is_rejected_by_workspace_not_by_us():
    with pytest.raises(ValueError, match="run_id"):
        verify(envelope(run_id="2026-09-01"), run_id="2026-09-01")


def test_verify_does_not_mutate_the_task_or_the_envelope():
    t, env = task(), envelope()
    runner.verify_handoff(env, engine="claude", run_id=RUN, task=t, input_hash="a" * 64,
                          available_artifacts={"prompt", "slim"}, registered_contracts=REGISTERED)
    assert t == task() and env == envelope()
