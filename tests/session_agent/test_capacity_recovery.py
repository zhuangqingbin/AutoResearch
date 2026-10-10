"""Capacity pauses preserve accepted work; recovery never rewrites old attempts."""
from pathlib import Path

import pytest

from autoresearch.session_agent import runner, service
from autoresearch.session_agent.executors.base import DispatchResult, classify_error
from autoresearch.session_agent.executors.headless_codex import HeadlessCodexExecutor

from ._runner_support import begin_synthetic_run, det, inf
from .test_runner import _entry, _FakeExecutor


def test_subscription_exhaustion_is_distinct_from_transient_rate_limit():
    assert classify_error("You've hit your usage limit. Try again at 3:25 AM") == "USAGE_LIMIT"
    assert classify_error("HTTP 429 rate limit") == "RATE_LIMIT"
    assert classify_error("You've hit your limit · resets 3am") == "USAGE_LIMIT"
    assert classify_error("quota", "USAGE_LIMIT") == "USAGE_LIMIT"
    error, kind = HeadlessCodexExecutor._failure(
        1, {"failed": [], "errors": []}, "", "You've hit your usage limit")
    assert error and kind == "USAGE_LIMIT"


def test_capacity_refusal_preserves_failure_and_does_not_require_nonexistent_research(tmp_path, monkeypatch):
    from autoresearch.session_agent import evidence
    run = begin_synthetic_run(tmp_path, monkeypatch, [inf("synthetic.first")])
    service.claim(run.run_id, "synthetic.first", 1,
                  handle_loader=lambda _: run.handle, event_recorder=lambda *a, **k: None)
    service.fail(run.run_id, "synthetic.first", 1, "USAGE_LIMIT", "usage limit reached",
                 handle_loader=lambda _: run.handle)
    ledger = evidence.build_evidence_plan(run.handle)
    [key] = ledger["task_keys"]
    assert key["evidence_kind"] == "CAPACITY_DEFERRED"
    assert "transcript" not in key["requirements"]
    assert evidence.read_failure(run.handle, "synthetic.first", 1)["error"]["code"] == "USAGE_LIMIT"
    owner = runner.Runner(run.run_id, _FakeExecutor(), hooks=run.hooks())
    owner.handle = run.handle
    assert owner._outcome({"state": "BLOCKED"}, 2, "MAX_ROUNDS")["recoverable"]


def test_quota_stops_new_dispatch_and_can_resume_only_failed_attempt(tmp_path, monkeypatch):
    run = begin_synthetic_run(tmp_path, monkeypatch, [
        inf("synthetic.first"),
        inf("synthetic.second", deps=["synthetic.first"], inputs=["synthetic.first.out"]),
    ])
    quota = DispatchResult(ok=False, error_class="USAGE_LIMIT", error="usage limit reached")
    ex = _FakeExecutor(outcomes={("synthetic.first", 1): quota})
    outcome = runner.run_loop(run.run_id, ex, max_parallel=1, poll_seconds=.01,
                              max_rounds=100, hooks=run.hooks())
    assert outcome["stop_reason"] == "USAGE_LIMIT" and outcome["recoverable"]
    assert ex.calls == [("synthetic.first", 1)]
    assert _entry(run, "synthetic.first")["state"] == "FAILED"
    assert run.finish_calls == []
    # A restart without explicit authorization doesn't spend anything.
    unchanged = _FakeExecutor()
    outcome = runner.run_loop(run.run_id, unchanged, poll_seconds=.01,
                              max_rounds=100, hooks=run.hooks())
    assert unchanged.calls == [] and outcome["stop_reason"] == "USAGE_LIMIT"
    service.recover_task(run.run_id, "synthetic.first", 1, "subscription window reset",
                         handle_loader=lambda _: run.handle)
    resumed = _FakeExecutor()
    outcome = runner.run_loop(run.run_id, resumed, poll_seconds=.01,
                              max_rounds=100, hooks=run.hooks())
    assert outcome["finished"], outcome
    assert resumed.calls == [("synthetic.first", 2), ("synthetic.second", 1)]
    assert len(_entry(run, "synthetic.first")["recovery_authorizations"]) == 1
    assert (Path(run.handle.capsule) / "agents/session/recovery_authorizations"
            / "synthetic.first-a2.json").is_file()


def test_pure_lane_and_direct_start_obey_persisted_capacity_pause(tmp_path, monkeypatch):
    run = begin_synthetic_run(tmp_path, monkeypatch, [inf("synthetic.first"), inf("synthetic.other")])
    service.claim(run.run_id, "synthetic.first", 1,
                  handle_loader=lambda _: run.handle, event_recorder=lambda *a, **k: None)
    service.fail(run.run_id, "synthetic.first", 1, "USAGE_LIMIT", "usage limit reached",
                 handle_loader=lambda _: run.handle)
    ex = _FakeExecutor()
    owner = runner.Runner(run.run_id, ex, hooks=run.hooks())
    owner.handle = run.handle
    monkeypatch.setattr(service, "next", lambda *a, **k: pytest.fail("queried after quota pause"))
    owner._overlap_inference(lambda: True)
    assert owner._start(run.tasks[1], 1) is False
    assert ex.calls == [] and _entry(run, "synthetic.other")["state"] == "PENDING"


def test_operator_retries_only_failed_idempotent_operation_after_auto_cap(tmp_path, monkeypatch):
    run = begin_synthetic_run(tmp_path, monkeypatch, [
        inf("synthetic.accepted"),
        det("synthetic.assemble", deps=["synthetic.accepted"],
            inputs=["synthetic.accepted.out"]),
    ])
    run.op_failures["synthetic.assemble"] = 2
    ex = _FakeExecutor()
    outcome = runner.run_loop(run.run_id, ex, poll_seconds=.01,
                              max_rounds=100, hooks=run.hooks())
    assert not outcome["finished"]
    service.recover_task(run.run_id, "synthetic.assemble", 2, "fixed assembly bug",
                         handle_loader=lambda _: run.handle)
    resumed = _FakeExecutor()
    outcome = runner.run_loop(run.run_id, resumed, poll_seconds=.01,
                              max_rounds=100, hooks=run.hooks())
    assert outcome["finished"], outcome
    assert resumed.calls == []
    assert run.op_calls == ["synthetic.assemble@1", "synthetic.assemble@2", "synthetic.assemble@3"]


@pytest.mark.parametrize("error", ["CONTRACT_ERROR", "DOMAIN_VALIDATION", "EVIDENCE_MISSING"])
def test_recovery_cannot_bypass_research_or_identity_failure(tmp_path, monkeypatch, error):
    run = begin_synthetic_run(tmp_path, monkeypatch, [inf("synthetic.first")])
    service.claim(run.run_id, "synthetic.first", 1,
                  handle_loader=lambda _: run.handle, event_recorder=lambda *a, **k: None)
    service.fail(run.run_id, "synthetic.first", 1, error, "rejected",
                 handle_loader=lambda _: run.handle)
    with pytest.raises((ValueError, RuntimeError), match="recover"):
        service.recover_task(run.run_id, "synthetic.first", 1, "try to bypass",
                             handle_loader=lambda _: run.handle)


def test_recovery_checks_attempt_and_frozen_input_hash(tmp_path, monkeypatch):
    run = begin_synthetic_run(tmp_path, monkeypatch, [inf("synthetic.first")])
    service.claim(run.run_id, "synthetic.first", 1,
                  handle_loader=lambda _: run.handle, event_recorder=lambda *a, **k: None)
    service.fail(run.run_id, "synthetic.first", 1, "USAGE_LIMIT", "capacity exhausted",
                 handle_loader=lambda _: run.handle)
    with pytest.raises((ValueError, RuntimeError), match="attempt"):
        service.recover_task(run.run_id, "synthetic.first", 2, "reset",
                             handle_loader=lambda _: run.handle)
    entry = _entry(run, "synthetic.first")
    from autoresearch.session_agent import artifacts
    source = artifacts.artifact_path(run.handle, entry["claim_receipt"]["input_snapshots"][0]["artifact_id"])
    source.write_text("changed facts")
    with pytest.raises((ValueError, RuntimeError), match="changed|hash|snapshot"):
        service.recover_task(run.run_id, "synthetic.first", 1, "reset",
                             handle_loader=lambda _: run.handle)
