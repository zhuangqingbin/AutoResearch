"""session_v1 runner: scheduling only — deterministic in-process, inference via executor."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from autoresearch.common.atomic import sha256_file
from autoresearch.session_agent import runner, service, store
from autoresearch.session_agent.executors.base import (
    DispatchRequest,
    DispatchResult,
    ExecutorTimeout,
)

from ._runner_support import CARD_TEXT, begin_synthetic_run, det, inf, profile


class _FakeExecutor:
    name = "fake"

    def __init__(self, *, outcomes=None, write=True, delay=0.0):
        self.calls: list[tuple[str, int]] = []
        self.requests: list[DispatchRequest] = []
        self.outcomes = dict(outcomes or {})   # (task_id, attempt) -> Exception | DispatchResult
        self.write = write
        self.delay = delay
        self._lock = threading.Lock()
        self.active = 0
        self.max_active = 0

    def dispatch(self, request: DispatchRequest) -> DispatchResult:
        with self._lock:
            self.calls.append((request.task_id, request.attempt))
            self.requests.append(request)
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            if self.delay:
                time.sleep(self.delay)
            outcome = self.outcomes.get((request.task_id, request.attempt))
            if isinstance(outcome, Exception):
                raise outcome
            if isinstance(outcome, DispatchResult):
                return outcome
            if self.write:
                for path in request.output_paths.values():
                    Path(path).parent.mkdir(parents=True, exist_ok=True)
                    Path(path).write_text(CARD_TEXT, encoding="utf-8")
            return DispatchResult(
                ok=True,
                session_ref="session-main",
                context_ref=f"ctx-{request.task_id}-a{request.attempt}",
                parent_context_ref="session-main",
            )
        finally:
            with self._lock:
                self.active -= 1


class _ReattachingExecutor(_FakeExecutor):
    supports_reattach = True


def _entry(run, task_id):
    return store.read_entry(Path(run.handle.workspace) / "session/tasks.json", task_id)


def _three_task_run(tmp_path, monkeypatch):
    return begin_synthetic_run(tmp_path, monkeypatch, [
        det("synthetic.one"),
        det("synthetic.two", deps=["synthetic.one"], inputs=["synthetic.one.out"]),
        inf("synthetic.inference", deps=["synthetic.two"], inputs=["synthetic.two.out"]),
    ])


def test_run_loop_executes_deterministic_then_inference_then_finishes(tmp_path, monkeypatch):
    run = _three_task_run(tmp_path, monkeypatch)
    ex = _FakeExecutor()
    final = runner.run_loop(run.run_id, ex, poll_seconds=0.01, max_rounds=200, hooks=run.hooks())
    assert final["status"] == "DONE" and final["finished"] is True
    assert final["stop_reason"] == "FINISHED"
    assert [c[0] for c in ex.calls] == ["synthetic.inference"]          # dispatched exactly once
    assert run.op_calls == ["synthetic.one@1", "synthetic.two@1"]
    assert run.finish_calls == ["publish", "finalize"]
    # The driver hashes the output file itself; it never trusts an executor self-report.
    entry = _entry(run, "synthetic.inference")
    assert entry["outputs"] == [{
        "artifact_id": "synthetic.inference.out",
        "sha256": sha256_file(run.output_path("synthetic.inference.out")),
    }]


def test_request_carries_the_dispatch_contract_for_executors(tmp_path, monkeypatch):
    run = _three_task_run(tmp_path, monkeypatch)
    ex = _FakeExecutor()
    runner.run_loop(run.run_id, ex, poll_seconds=0.01, max_rounds=200, hooks=run.hooks())
    request = ex.requests[0]
    assert request.run_id == run.run_id and request.engine == "codex"
    assert (request.task_id, request.attempt, request.role) == ("synthetic.inference", 1, "stock.card")
    assert request.agent_type == "l4-card"
    assert request.output_paths == {
        "synthetic.inference.out": str(run.output_path("synthetic.inference.out")),
    }
    assert request.prompt and str(run.output_path("synthetic.inference.out")) in request.prompt
    assert request.timeout_seconds > 0
    assert request.host_session_ref == "session-main"
    assert DispatchRequest.from_json(json.loads(json.dumps(request.to_json()))) == request


def test_orphan_claimed_inference_is_not_redispatched(tmp_path, monkeypatch):
    run = begin_synthetic_run(tmp_path, monkeypatch, [inf("synthetic.inference")])
    # The previous runner process claimed the task and died before submit.
    service.claim(run.run_id, "synthetic.inference", 1, handle_loader=lambda rid: run.handle,
                  event_recorder=lambda *a, **k: None)
    ex = _FakeExecutor()
    final = runner.run_loop(run.run_id, ex, poll_seconds=0.01, max_rounds=5, hooks=run.hooks())
    assert ex.calls == []
    assert final["status"] in {"WAITING", "BLOCKED"} and final["finished"] is False
    assert final["stop_reason"] == "STALLED"
    assert [{key: row[key] for key in ("task_id", "attempt", "kind")}
            for row in final["orphans"]] == [
        {"task_id": "synthetic.inference", "attempt": 1, "kind": "INFERENCE"}]


def test_reattaching_executor_adopts_orphan_without_a_new_claim(tmp_path, monkeypatch):
    run = begin_synthetic_run(tmp_path, monkeypatch, [inf("synthetic.inference")])
    service.claim(run.run_id, "synthetic.inference", 1, handle_loader=lambda rid: run.handle,
                  event_recorder=lambda *a, **k: None)
    ex = _ReattachingExecutor()
    final = runner.run_loop(run.run_id, ex, poll_seconds=0.01, max_rounds=50, hooks=run.hooks())
    assert ex.calls == [("synthetic.inference", 1)]
    assert final["status"] == "DONE" and final["finished"] is True


def test_run_loop_respects_parallel_cap(tmp_path, monkeypatch):
    tasks = [det("synthetic.root")] + [
        inf(f"synthetic.inference.{i}", deps=["synthetic.root"]) for i in range(6)
    ]
    run = begin_synthetic_run(tmp_path, monkeypatch, tasks)
    ex = _FakeExecutor(delay=0.05)
    claimed = []
    original = ex.dispatch

    def dispatch(request):
        states = store.read_states(Path(run.handle.workspace) / "session/tasks.json")
        claimed.append(sum(1 for key, value in states.items()
                           if key.startswith("synthetic.inference") and value == "RUNNING"))
        return original(request)

    ex.dispatch = dispatch
    final = runner.run_loop(run.run_id, ex, max_parallel=2, poll_seconds=0.01, max_rounds=500,
                            hooks=run.hooks())
    assert final["finished"] is True
    assert len(ex.calls) == 6
    assert ex.max_active == 2          # capped, and the cap is actually used
    assert max(claimed) <= 2           # over-cap tasks stay READY: not even claimed


def test_timeout_is_retried_once_then_succeeds(tmp_path, monkeypatch):
    run = begin_synthetic_run(tmp_path, monkeypatch, [inf("synthetic.inference")])
    ex = _FakeExecutor(outcomes={
        ("synthetic.inference", 1): ExecutorTimeout("等待 synthetic.inference 的结果文件 x 超时"),
    })
    final = runner.run_loop(run.run_id, ex, poll_seconds=0.01, max_rounds=50, hooks=run.hooks())
    assert ex.calls == [("synthetic.inference", 1), ("synthetic.inference", 2)]
    assert final["finished"] is True
    failure = json.loads((
        Path(run.handle.capsule) / "evidence/attempt_records/synthetic.inference/a1/failure.json"
    ).read_text(encoding="utf-8"))
    assert failure["error"]["code"] == "TIMEOUT"
    assert "synthetic.inference" in failure["error"]["message"]


def test_second_timeout_blocks_without_a_third_dispatch(tmp_path, monkeypatch):
    run = begin_synthetic_run(tmp_path, monkeypatch, [inf("synthetic.inference")])
    ex = _FakeExecutor(outcomes={
        ("synthetic.inference", 1): ExecutorTimeout("t1"),
        ("synthetic.inference", 2): ExecutorTimeout("t2"),
    })
    final = runner.run_loop(run.run_id, ex, poll_seconds=0.01, max_rounds=50, hooks=run.hooks())
    assert ex.calls == [("synthetic.inference", 1), ("synthetic.inference", 2)]
    assert final["status"] == "BLOCKED" and final["finished"] is False
    assert _entry(run, "synthetic.inference")["attempt"] == 2


def test_reported_non_transient_failure_blocks_without_retry(tmp_path, monkeypatch):
    run = begin_synthetic_run(tmp_path, monkeypatch, [inf("synthetic.inference")])
    ex = _FakeExecutor(outcomes={
        ("synthetic.inference", 1): DispatchResult(ok=False, error="agent refused the task"),
    })
    final = runner.run_loop(run.run_id, ex, poll_seconds=0.01, max_rounds=50, hooks=run.hooks())
    assert ex.calls == [("synthetic.inference", 1)]
    assert final["status"] == "BLOCKED"
    assert _entry(run, "synthetic.inference")["error"]["code"] == "AGENT_ERROR"


def test_reported_transient_failure_is_classified_and_retried(tmp_path, monkeypatch):
    run = begin_synthetic_run(tmp_path, monkeypatch, [inf("synthetic.inference")])
    ex = _FakeExecutor(outcomes={
        ("synthetic.inference", 1): DispatchResult(
            ok=False, error="API Error: Connection closed mid-response"),
    })
    final = runner.run_loop(run.run_id, ex, poll_seconds=0.01, max_rounds=50, hooks=run.hooks())
    assert ex.calls == [("synthetic.inference", 1), ("synthetic.inference", 2)]
    assert final["finished"] is True


def test_missing_output_is_a_contract_error_naming_the_path(tmp_path, monkeypatch):
    run = begin_synthetic_run(tmp_path, monkeypatch, [inf("synthetic.inference")])
    ex = _FakeExecutor(write=False)
    final = runner.run_loop(run.run_id, ex, poll_seconds=0.01, max_rounds=50, hooks=run.hooks())
    assert final["status"] == "BLOCKED"
    error = _entry(run, "synthetic.inference")["error"]
    assert error["code"] == "CONTRACT_ERROR"
    assert str(run.output_path("synthetic.inference.out")) in error["message"]


def test_failed_idempotent_deterministic_operation_is_retried_once(tmp_path, monkeypatch):
    run = begin_synthetic_run(tmp_path, monkeypatch, [det("synthetic.one")])
    run.op_failures["synthetic.one"] = 1
    final = runner.run_loop(run.run_id, _FakeExecutor(), poll_seconds=0.01, max_rounds=50,
                            hooks=run.hooks())
    assert run.op_calls == ["synthetic.one@1", "synthetic.one@2"]
    assert final["finished"] is True


def test_deterministic_operation_failing_twice_blocks(tmp_path, monkeypatch):
    run = begin_synthetic_run(tmp_path, monkeypatch, [det("synthetic.one")])
    run.op_failures["synthetic.one"] = 2
    final = runner.run_loop(run.run_id, _FakeExecutor(), poll_seconds=0.01, max_rounds=50,
                            hooks=run.hooks())
    assert run.op_calls == ["synthetic.one@1", "synthetic.one@2"]
    assert final["status"] == "BLOCKED" and final["finished"] is False


def test_independent_review_submits_a_verified_host_receipt(tmp_path, monkeypatch):
    from autoresearch.session_agent import host_evidence

    run = begin_synthetic_run(
        tmp_path, monkeypatch,
        [inf("synthetic.review", role="scan.l4.review", subject="600519", independent=True,
             inputs=("synthetic.prompt",))],
        host=profile(independent_context=True),
    )
    seen = []
    monkeypatch.setattr(host_evidence, "resolve_receipt_evidence",
                        lambda handle, task, receipt: seen.append(receipt) or [receipt])
    ex = _FakeExecutor(outcomes={})

    def dispatch(request):
        path = Path(request.output_paths["synthetic.review.out"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(CARD_TEXT, encoding="utf-8")
        return DispatchResult(ok=True, session_ref="session-main", context_ref="agent-r2",
                              parent_context_ref="session-main",
                              evidence_refs=("host-binding:" + "1" * 64,))

    ex.dispatch = dispatch
    final = runner.run_loop(run.run_id, ex, poll_seconds=0.01, max_rounds=50, hooks=run.hooks())
    assert final["finished"] is True
    assert seen and seen[0]["context_ref"] == "agent-r2"
    assert seen[0]["parent_context_ref"] == "session-main"
    assert seen[0]["evidence_refs"] == ["host-binding:" + "1" * 64]
    receipts = list((Path(run.handle.capsule) / "agents/session/host_receipts").glob("*.json"))
    assert len(receipts) == 1


def test_independent_review_without_evidence_fails_instead_of_faking_a_receipt(tmp_path, monkeypatch):
    run = begin_synthetic_run(
        tmp_path, monkeypatch,
        [inf("synthetic.review", role="scan.l4.review", subject="600519", independent=True,
             inputs=("synthetic.prompt",))],
        host=profile(independent_context=True),
    )
    final = runner.run_loop(run.run_id, _FakeExecutor(), poll_seconds=0.01, max_rounds=50,
                            hooks=run.hooks())
    assert final["status"] == "BLOCKED"
    assert _entry(run, "synthetic.review")["error"]["code"] == "EVIDENCE_MISSING"


def test_evidence_binder_hook_receives_the_transcript(tmp_path, monkeypatch):
    run = _three_task_run(tmp_path, monkeypatch)
    ex = _FakeExecutor()
    bound = []

    def dispatch(request):
        _FakeExecutor.dispatch(ex, request)
        return DispatchResult(ok=True, session_ref="session-main", context_ref="agent-x",
                              parent_context_ref="session-main", transcript_path="/t/agent-x.jsonl")

    ex.dispatch = dispatch
    final = runner.run_loop(
        run.run_id, ex, poll_seconds=0.01, max_rounds=200, hooks=run.hooks(),
        evidence_binder=lambda request, result: bound.append(
            (request.task_id, request.attempt, result.transcript_path)) or (),
    )
    assert final["finished"] is True
    assert bound == [("synthetic.inference", 1, "/t/agent-x.jsonl")]


def test_l4_ticket_is_claimed_through_the_taskbook_not_executed(tmp_path, monkeypatch):
    from autoresearch.session_agent import legacy_scan

    parent = {"owner": "L4_TASKBOOK", "subject": "600519", "attempt": 1}
    tasks = [
        det("scan.root", outputs=["scan.root.out"]),
        det("l4.600519.a1", deps=["scan.root"], owner="L4_TASKBOOK", subject="600519",
            outputs=["l4.600519.a1.ticket"], operation="scan.l4.ticket"),
        det("l4.600519.a1.child", deps=["scan.root"], subject="600519", parent=parent,
            outputs=["l4.600519.a1.child.out"]),
    ]
    run = begin_synthetic_run(tmp_path, monkeypatch, tasks, run_kind="scan-market")
    staging = Path(run.handle.staging)
    (staging / "_l4_prompt_600519.md").write_text("task pack", encoding="utf-8")
    legacy_scan.initialize_tickets(run.handle, ["600519"])
    book = staging / "_l4_tasks.json"

    def op_runner(handle, stage, argv, invocation_id, attempt, subject, *, task_id):
        result = run.operation_runner(handle, stage, argv, invocation_id, attempt, subject,
                                      task_id=task_id)
        if task_id == "l4.600519.a1.child":    # stands in for scan.l4.finalize
            payload = json.loads(book.read_text(encoding="utf-8"))
            payload["tasks"]["600519"]["status"] = "SUCCEEDED"
            book.write_text(json.dumps(payload), encoding="utf-8")
        return result

    final = runner.run_loop(run.run_id, _FakeExecutor(), poll_seconds=0.01, max_rounds=100,
                            hooks=run.hooks(operation_runner=op_runner))
    assert final["finished"] is True
    assert "l4.600519.a1@1" not in run.op_calls              # owner task: claimed, never executed
    assert run.op_calls == ["scan.root@1", "l4.600519.a1.child@1"]
    assert json.loads(book.read_text(encoding="utf-8"))["tasks"]["600519"]["attempt"] == 1


def test_run_writes_a_status_file_the_host_can_poll(tmp_path, monkeypatch):
    run = _three_task_run(tmp_path, monkeypatch)
    final = runner.run_loop(run.run_id, _FakeExecutor(), poll_seconds=0.01, max_rounds=200,
                            hooks=run.hooks())
    status = json.loads((Path(run.handle.staging) / "_dispatch/runner.json").read_text("utf-8"))
    assert status["state"] == "EXITED"
    assert status["outcome"]["stop_reason"] == final["stop_reason"] == "FINISHED"


# ── I3 (review 2026-09-26): restart derives retries from durable state ──────────────

def _handle_loader(run):
    return lambda rid: run.handle


def test_restart_retries_a_durably_failed_session_task(tmp_path, monkeypatch):
    """The previous runner recorded TIMEOUT for a1 and died before retrying: a restart
    must spend the promised single retry, not stop BLOCKED with zero dispatches."""
    run = begin_synthetic_run(tmp_path, monkeypatch, [inf("synthetic.inference")])
    service.claim(run.run_id, "synthetic.inference", 1, handle_loader=_handle_loader(run),
                  event_recorder=lambda *a, **k: None)
    service.fail(run.run_id, "synthetic.inference", 1, "TIMEOUT", "timed out before crash",
                 handle_loader=_handle_loader(run))
    ex = _FakeExecutor()
    final = runner.run_loop(run.run_id, ex, poll_seconds=0.01, max_rounds=50, hooks=run.hooks())
    assert ex.calls == [("synthetic.inference", 2)]
    assert final["finished"] is True, (final["stop_reason"], final["errors"])


def test_deterministic_orphan_hint_is_actionable_and_restart_progresses(tmp_path, monkeypatch):
    run = begin_synthetic_run(tmp_path, monkeypatch, [det("synthetic.one")])
    service.claim(run.run_id, "synthetic.one", 1, handle_loader=_handle_loader(run),
                  event_recorder=lambda *a, **k: None)          # runner died mid-execute
    events = []
    first = runner.run_loop(run.run_id, _FakeExecutor(), poll_seconds=0.01, max_rounds=5,
                            hooks=run.hooks(), log=events.append)
    assert first["stop_reason"] == "STALLED"
    orphan = first["orphans"][0]
    assert (orphan["task_id"], orphan["attempt"]) == ("synthetic.one", 1)
    hint = orphan["hint"]
    assert "fail" in hint and "--error-class STALE_TASK" in hint and "--attempt 1" in hint
    assert "detach key" in hint
    # Follow the hint literally: record the orphan as a STALE_TASK failure, restart.
    service.fail(run.run_id, "synthetic.one", 1, "STALE_TASK", "orphaned by a dead runner",
                 handle_loader=_handle_loader(run))
    second = runner.run_loop(run.run_id, _FakeExecutor(), poll_seconds=0.01, max_rounds=50,
                             hooks=run.hooks())
    assert second["finished"] is True, (second["stop_reason"], second["errors"])
    assert run.op_calls == ["synthetic.one@2"]


def test_orphan_without_its_frozen_handoff_is_reported_not_a_crash(tmp_path, monkeypatch):
    """Crash between store.claim and the handoff freeze: no request file to re-attach."""
    run = begin_synthetic_run(tmp_path, monkeypatch, [inf("synthetic.inference")])
    store.claim(Path(run.handle.workspace) / "session/tasks.json", "synthetic.inference", 1,
                "session-main", [])
    final = runner.run_loop(run.run_id, _ReattachingExecutor(), poll_seconds=0.01,
                            max_rounds=5, hooks=run.hooks())
    assert final["stop_reason"] == "STALLED"
    orphan = final["orphans"][0]
    assert orphan["task_id"] == "synthetic.inference" and "FileNotFoundError" in orphan["error"]
    assert "--error-class STALE_TASK" in orphan["hint"]


def test_second_runner_on_the_same_run_refuses_to_start(tmp_path, monkeypatch):
    """M2: two live runners would claim the same work (same session_ref)."""
    import os

    run = begin_synthetic_run(tmp_path, monkeypatch, [inf("synthetic.inference")])
    release = threading.Event()
    started = threading.Event()

    class _Blocking(_FakeExecutor):
        def dispatch(self, request):
            started.set()
            release.wait(10)
            return super().dispatch(request)

    outcome = {}
    thread = threading.Thread(target=lambda: outcome.update(runner.run_loop(
        run.run_id, _Blocking(), poll_seconds=0.01, max_rounds=5000, hooks=run.hooks())))
    thread.start()
    try:
        assert started.wait(10)
        status_path = Path(run.handle.staging) / "_dispatch/runner.json"
        with pytest.raises(runner.RunnerAlreadyRunning, match=f"pid {os.getpid()}"):
            runner.run_loop(run.run_id, _FakeExecutor(), poll_seconds=0.01, max_rounds=5,
                            hooks=run.hooks())
        assert json.loads(status_path.read_text("utf-8"))["state"] == "RUNNING"
    finally:
        release.set()
        thread.join(10)
    assert outcome["finished"] is True
    # The lock is released on exit: a later runner may start again.
    again = runner.run_loop(run.run_id, _FakeExecutor(), poll_seconds=0.01, max_rounds=5,
                            hooks=run.hooks())
    assert again["stop_reason"] in {"FINISHED", "FINISH_FAILED"}


def test_heartbeat_keeps_beating_while_the_loop_thread_is_busy(tmp_path, monkeypatch):
    """RUNNER_DEAD uses heartbeat age: a long finish/publish must not look dead."""
    run = _three_task_run(tmp_path, monkeypatch)
    status_path = Path(run.handle.staging) / "_dispatch/runner.json"
    beats = []

    def slow_publish(current):
        for _ in range(2):
            beats.append(json.loads(status_path.read_text("utf-8"))["heartbeat_epoch"])
            time.sleep(0.25)
        return None

    final = runner.run_loop(run.run_id, _FakeExecutor(), poll_seconds=0.01, max_rounds=200,
                            hooks=run.hooks(publisher=slow_publish), heartbeat_seconds=0.05)
    assert final["finished"] is True
    assert beats[1] > beats[0]                              # beat while publish blocked the loop
    status = json.loads(status_path.read_text("utf-8"))
    assert status["state"] == "EXITED" and status["heartbeat_seconds"] == 0.05
