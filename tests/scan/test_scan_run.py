"""Unattended scan run orchestration (batch 4 Task 3, spec §6 C2).

Every external effect is an injected step: no tushare, no ``claude``, no real begin, no push.
Order under test: lock → trade day → no live manual run → readiness → session_v1 begin →
headless runner → (finished) verify + deliver | (not finished) capsule FAILED + notify.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from autoresearch.common import workspace as ws
from autoresearch.scan import run_lock, scan_run

DATE = "2026-09-28"
RUN_ID = "20260928T132000000000Z"


@pytest.fixture
def roots(tmp_path, monkeypatch):
    monkeypatch.setattr(ws, "ENGINE", "claude")
    monkeypatch.setattr(ws, "context_root", lambda: tmp_path / "context_claude")
    monkeypatch.setattr(ws, "reports_root", lambda: tmp_path / "reports_claude")
    return tmp_path


class _Steps:
    """Records the order of calls; each step's behavior is configurable."""

    def __init__(self, tmp_path: Path, **changes):
        self.calls: list[str] = []
        self.notified: list[tuple[str, str]] = []
        self.delivered: list[dict] = []
        self.finalized: list[tuple[str, dict]] = []
        self.request: dict | None = None
        self.date = changes.pop("date", DATE)
        self.ready = changes.pop("ready", True)
        self.live = changes.pop("live", [])
        self.begin_error = changes.pop("begin_error", None)
        self.outcome = changes.pop("outcome", {
            "finished": True, "stop_reason": "FINISHED", "status": "DONE", "errors": [],
            "dispatches": [], "finish": {"canonical_path": str(tmp_path / "canonical")}})
        self.verification = changes.pop("verification", {
            "report_covered": True, "publication_ok": True, "orchestration_verified": True,
            "completeness_ok": True})
        self.delivery = changes.pop("delivery", {"channel": "bark", "status": "SENT"})
        self.compat = tmp_path / "reports_claude" / "scan" / "20260928-0928_2120"
        # the wall clock: 21:20 on the scan day (inside the 21:10–22:30 window) unless changed;
        # ``after_ready`` = the clock once the readiness wait returns.
        self.clock = changes.pop("now", datetime(2026, 9, 28, 21, 20))
        self.after_ready = changes.pop("after_ready", None)
        self.missed = changes.pop("missed", DATE)
        self.channel = changes.pop("channel", "bark")
        self.raises = changes.pop("raises", {})          # step name -> exception to raise
        self.swept: list[str] = []
        self.run_timeouts: list[float] = []
        assert not changes, changes

    def _maybe_raise(self, name: str) -> None:
        if name in self.raises:
            raise self.raises[name]

    def as_steps(self) -> scan_run.Steps:
        def resolve_date(explicit):
            self.calls.append("resolve_date")
            return self.date

        def live_runs():
            self.calls.append("live_runs")
            return self.live

        def wait_ready(date, deadline):
            self.calls.append("wait_ready")
            self._maybe_raise("wait_ready")
            if self.after_ready is not None:
                self.clock = self.after_ready
            return self.ready

        def begin(request_path):
            self.calls.append("begin")
            self.request = json.loads(Path(request_path).read_text(encoding="utf-8"))
            if self.begin_error:
                raise RuntimeError(self.begin_error)
            return RUN_ID

        def run(run_id, timeout_s):
            self.calls.append("run")
            self.run_timeouts.append(timeout_s)
            self._maybe_raise("run")
            return self.outcome

        def verify(canonical, run_id):
            self.calls.append("verify")
            self._maybe_raise("verify")
            return self.verification

        def locate_brief(run_id, canonical):
            self.calls.append("locate_brief")
            self.compat.mkdir(parents=True, exist_ok=True)
            (self.compat / "brief.md").write_text("brief", encoding="utf-8")
            return self.compat / "brief.md"

        def deliver(brief, **kwargs):
            self.calls.append("deliver")
            self.delivered.append({"brief": brief, **kwargs})
            return dict(self.delivery)

        def finalize_failed(run_id, error):
            self.calls.append("finalize_failed")
            self.finalized.append((run_id, error))

        def notify(title, body):
            self.calls.append("notify")
            self.notified.append((title, body))
            return {"status": "SENT"}

        def sweep(run_id):
            self.calls.append("sweep")
            self.swept.append(run_id)
            return [4242]

        return scan_run.Steps(
            resolve_date=resolve_date, live_runs=live_runs, wait_ready=wait_ready, begin=begin,
            run=run, verify=verify, locate_brief=locate_brief, deliver=deliver,
            finalize_failed=finalize_failed, notify=notify, sweep=sweep,
            now=lambda: self.clock, missed_date=lambda now: self.missed,
            channel=lambda: self.channel)


def _args(**changes):
    values = {"date": None, "deadline": "22:30", "skip_readiness": False}
    values.update(changes)
    return SimpleNamespace(**values)


def _run(tmp_path, steps: _Steps, **arg_changes) -> int:
    log = scan_run.OpsLog(tmp_path / "reports_claude" / "_ops" / "scan_run_test.log")
    try:
        return scan_run.run_once(_args(**arg_changes), steps.as_steps(), log=log)
    finally:
        log.close()


def test_success_runs_begin_runner_verify_and_delivers_the_compat_brief(roots):
    steps = _Steps(roots)
    assert _run(roots, steps) == scan_run.EXIT_OK
    assert steps.calls == ["resolve_date", "live_runs", "wait_ready", "live_runs", "begin", "run",
                           "verify", "locate_brief", "deliver"]
    [sent] = steps.delivered
    assert sent["brief"] == steps.compat / "brief.md"
    assert sent["run_id"] == RUN_ID and DATE in sent["title"] and "✓" in sent["title"]
    assert sent["report_path"] == str(steps.compat)
    assert steps.notified == [] and steps.finalized == []
    summary = _summary(roots)
    assert summary["run_id"] == RUN_ID and summary["result"] == "FINISHED"
    assert summary["delivery"]["status"] == "SENT"


@pytest.mark.parametrize("stop", ["USAGE_LIMIT", "BLOCKED", "FINISH_FAILED"])
def test_recoverable_stop_preserves_active_capsule(roots, stop):
    steps = _Steps(roots, outcome={"finished": False, "stop_reason": stop,
                   "recoverable": True, "recovery_tasks": [{"task_id": "scan.assemble", "attempt": 2}],
                   "errors": [], "dispatches": []})
    assert _run(roots, steps) == scan_run.EXIT_FAILED
    assert steps.finalized == [] and steps.swept == [RUN_ID]
    summary = _summary(roots)
    assert summary["result"] == "RECOVERABLE" and summary["run_id"] == RUN_ID
    assert summary["recovery_tasks"] == steps.outcome["recovery_tasks"]
    assert "--resume-run-id" in summary["resume_command"]


def test_resume_uses_existing_run_and_skips_readiness_begin(roots):
    from dataclasses import replace
    steps = _Steps(roots, live=[{"run_id": RUN_ID}])
    resumes = []
    injected = replace(steps.as_steps(), resume=lambda rid: resumes.append(rid) or DATE)
    log = scan_run.OpsLog(roots / "reports_claude/_ops/resume.log")
    try:
        assert scan_run.run_once(_args(resume_run_id=RUN_ID), injected, log=log) == 0
    finally:
        log.close()
    assert resumes == [RUN_ID]
    assert "begin" not in steps.calls and "wait_ready" not in steps.calls
    assert "run" in steps.calls and _summary(roots)["resumed"]


def test_resume_refuses_live_own_runner_before_mutation(roots, monkeypatch):
    import fcntl

    from autoresearch.trace import capsule
    workspace = roots / "context_claude/scan_runs" / RUN_ID
    staging = workspace / "staging" / DATE
    lock = staging / "_dispatch/runner.lock"
    lock.parent.mkdir(parents=True)
    (workspace / "session").mkdir()
    (workspace / "state.json").write_text(json.dumps({"business_status": "ACTIVE"}))
    (workspace / "session/request.json").write_text(json.dumps({"host_profile": {
        "engine": "claude", "session_ref": "headless-test"}}))
    handle = SimpleNamespace(workspace=workspace, staging=staging, engine="claude",
                             analysis_date=DATE, contract=SimpleNamespace(run_kind="scan-market"))
    monkeypatch.setattr(capsule, "load_run", lambda _: handle)
    monkeypatch.setattr(scan_run, "_call", lambda *a, **k: pytest.fail("mutated live run"))
    with lock.open("a+") as owner:
        fcntl.flock(owner.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(RuntimeError, match="live runner"):
            scan_run.default_steps(_args(resume_run_id=RUN_ID), None).resume(RUN_ID)
    assert scan_run.default_steps(_args(resume_run_id=RUN_ID), None).resume(RUN_ID) == DATE


def test_timeout_preserves_durable_quota_failure(roots, monkeypatch):
    root = roots / "context_claude/scan_runs" / RUN_ID
    (root / "session").mkdir(parents=True)
    (root / "session/tasks.json").write_text(json.dumps({
        "run_id": RUN_ID, "engine": "claude", "tasks": {"intel": {
            "state": "FAILED", "attempt": 1, "error": {"code": "USAGE_LIMIT"},
            "spec": {"operation": None}}}}))
    monkeypatch.setattr(ws, "find_run_root", lambda _: root)
    monkeypatch.setattr(scan_run, "_call", lambda *a, **k: (None, ""))
    monkeypatch.setattr(scan_run, "_terminate_inflight_headless", lambda _: [])
    outcome = scan_run.default_steps(_args(), None).run(RUN_ID, 1)
    assert outcome["recoverable"] and outcome["recovery_tasks"][0]["task_id"] == "intel"


def test_resume_lock_race_does_not_stop_or_finalize_live_runner(roots):
    from dataclasses import replace
    steps = _Steps(roots, outcome={"finished": False, "stop_reason": "RUNNER_BUSY"})
    log = scan_run.OpsLog(roots / "reports_claude/_ops/race.log")
    try:
        assert scan_run.run_once(_args(resume_run_id=RUN_ID),
            replace(steps.as_steps(), resume=lambda rid: DATE), log=log) == run_lock.EXIT_HELD
    finally:
        log.close()
    assert steps.swept == [] and steps.finalized == []


def _summary(roots, date: str = DATE) -> dict:
    return json.loads((roots / "reports_claude" / "_ops" / f"scan_run_{date}.json")
                      .read_text(encoding="utf-8"))


def _log_text(roots) -> str:
    return (roots / "reports_claude" / "_ops" / "scan_run_test.log").read_text(encoding="utf-8")


# ── the summary never claims a delivery that did not happen (review M6) ────────────

@pytest.mark.parametrize("status", ["SKIPPED", "FAILED"])
def test_finished_run_without_delivery_is_not_reported_as_delivered(roots, status):
    steps = _Steps(roots, delivery={"channel": "none", "status": status})
    assert _run(roots, steps) == scan_run.EXIT_OK
    summary = _summary(roots)
    assert summary["result"] == "FINISHED" and summary["delivery"]["status"] == status
    assert "DELIVERED" not in json.dumps(summary)


# ── channel none is announced, not silent (review M8) ──────────────────────────────

def test_channel_none_is_announced_at_start_and_in_the_summary(roots):
    steps = _Steps(roots, channel="none", delivery={"channel": "none", "status": "SKIPPED"})
    assert _run(roots, steps) == scan_run.EXIT_OK
    text = _log_text(roots)
    assert text.count("送达渠道 = none") >= 2            # start line + end line
    first = text.splitlines()
    assert "送达渠道 = none" in first[1]                  # right after "start"
    summary = _summary(roots)
    assert summary["delivery_channel"] == "none"
    assert any("none" in warning for warning in summary["warnings"])


def test_a_real_channel_prints_no_warning(roots):
    assert _run(roots, _Steps(roots)) == scan_run.EXIT_OK
    assert "送达渠道 = none" not in _log_text(roots)
    assert _summary(roots)["delivery_channel"] == "bark"


# ── late / missed fires on a laptop (review I4) ───────────────────────────────────

def test_fire_the_next_morning_is_reported_as_a_missed_night(roots):
    steps = _Steps(roots, now=datetime(2026, 9, 29, 8, 0), missed=DATE)
    assert _run(roots, steps) == scan_run.EXIT_OK
    assert "resolve_date" not in steps.calls and "wait_ready" not in steps.calls
    [(title, body)] = steps.notified
    assert title == f"扫描 {DATE} 错过" and "睡眠" in body and "--date" in body
    assert _summary(roots)["result"] == "MISSED"


def test_fire_after_the_deadline_is_a_missed_night_for_today(roots):
    steps = _Steps(roots, now=datetime(2026, 9, 28, 22, 45), missed=DATE)
    assert _run(roots, steps) == scan_run.EXIT_OK
    assert steps.notified[0][0] == f"扫描 {DATE} 错过" and "begin" not in steps.calls


def test_a_night_that_already_has_a_summary_is_not_reported_again(roots):
    ops = roots / "reports_claude" / "_ops"
    ops.mkdir(parents=True)
    (ops / f"scan_run_{DATE}.json").write_text(json.dumps({"result": "FINISHED"}),
                                               encoding="utf-8")
    steps = _Steps(roots, now=datetime(2026, 9, 29, 8, 0), missed=DATE)
    assert _run(roots, steps) == scan_run.EXIT_OK
    assert steps.notified == [] and _summary(roots) == {"result": "FINISHED"}


def test_an_early_manual_trigger_before_the_window_is_not_a_miss(roots):
    steps = _Steps(roots, now=datetime(2026, 9, 28, 20, 0), missed=DATE)
    assert _run(roots, steps) == scan_run.EXIT_OK
    assert steps.notified == [] and "begin" not in steps.calls
    assert not (roots / "reports_claude" / "_ops" / f"scan_run_{DATE}.json").exists()


def test_explicit_date_bypasses_the_window(roots):
    steps = _Steps(roots, now=datetime(2026, 9, 29, 8, 0), date="2026-09-25")
    assert _run(roots, steps, date="2026-09-25", skip_readiness=True) == scan_run.EXIT_OK
    assert "begin" in steps.calls and steps.notified == []


def test_window_edges():
    assert scan_run.in_window(datetime(2026, 9, 28, 21, 10), "22:30")
    assert scan_run.in_window(datetime(2026, 9, 28, 22, 30), "22:30")
    assert not scan_run.in_window(datetime(2026, 9, 28, 21, 9, 59), "22:30")
    assert not scan_run.in_window(datetime(2026, 9, 28, 22, 31), "22:30")


def test_default_missed_date_is_the_latest_settled_trading_day(roots, monkeypatch):
    from autoresearch.scan import trade_date

    seen = []
    monkeypatch.setattr(trade_date, "resolve_scan_date",
                        lambda date, now=None: seen.append((date, now)) or DATE)
    stamp = datetime(2026, 9, 29, 8, 0)
    assert scan_run.default_steps(_args(), log=None).missed_date(stamp) == DATE
    assert seen == [(None, stamp)]


# ── absolute end-of-night deadline (review M2) ─────────────────────────────────────

def test_runner_budget_is_capped_by_the_hard_stop(roots):
    steps = _Steps(roots, after_ready=datetime(2026, 9, 28, 23, 30))
    assert _run(roots, steps) == scan_run.EXIT_OK
    assert steps.run_timeouts == [90 * 60]            # 23:30 → 01:00, below the 180-min cap


def test_runner_budget_is_the_run_cap_on_an_ordinary_night(roots):
    steps = _Steps(roots)
    assert _run(roots, steps) == scan_run.EXIT_OK
    assert steps.run_timeouts == [scan_run.RUN_TIMEOUT_MINUTES * 60]


def test_past_the_hard_stop_the_run_never_begins(roots):
    steps = _Steps(roots, after_ready=datetime(2026, 9, 29, 7, 0))   # the lid was closed
    assert _run(roots, steps) == scan_run.EXIT_FAILED
    assert "begin" not in steps.calls and "run" not in steps.calls
    assert "未开" in steps.notified[0][0] and "01:00" in steps.notified[0][1]


def test_explicit_date_replay_is_not_capped_by_the_hard_stop(roots):
    steps = _Steps(roots, now=datetime(2026, 9, 29, 0, 50), date="2026-09-25")
    assert _run(roots, steps, date="2026-09-25", skip_readiness=True) == scan_run.EXIT_OK
    assert steps.run_timeouts == [scan_run.RUN_TIMEOUT_MINUTES * 60]


# ── nothing is left behind: sweep, signals, exceptions (review M1 / M7) ─────────────

def test_unfinished_runner_sweeps_inflight_sessions_before_finalizing(roots):
    steps = _Steps(roots, outcome={"finished": False, "stop_reason": "BLOCKED", "errors": [],
                                   "dispatches": []})
    assert _run(roots, steps) == scan_run.EXIT_FAILED
    assert steps.swept == [RUN_ID]
    assert steps.calls.index("sweep") < steps.calls.index("finalize_failed")


def test_signal_during_the_runner_stops_everything_finalizes_and_notifies(roots):
    import signal

    steps = _Steps(roots, raises={"run": scan_run.ScanRunInterrupted(signal.SIGTERM)})
    assert _run(roots, steps) == scan_run.EXIT_FAILED
    assert steps.swept == [RUN_ID]
    [(run_id, error)] = steps.finalized
    assert run_id == RUN_ID and error["stop_reason"] == "SIGNAL"
    title, body = steps.notified[0]
    assert "FAILED" in title and "SIGTERM" in body and RUN_ID in body
    assert _summary(roots)["result"] == "INTERRUPTED"


def test_signal_before_begin_notifies_without_a_run_to_finalize(roots):
    import signal

    steps = _Steps(roots, raises={"wait_ready": scan_run.ScanRunInterrupted(signal.SIGHUP)})
    assert _run(roots, steps) == scan_run.EXIT_FAILED
    assert steps.finalized == [] and steps.swept == []
    assert "SIGHUP" in steps.notified[0][1] and _summary(roots)["result"] == "INTERRUPTED"


def test_unexpected_exception_after_begin_is_finalized_and_notified(roots):
    steps = _Steps(roots, raises={"verify": RuntimeError("verify-report exploded")})
    assert _run(roots, steps) == scan_run.EXIT_FAILED
    [(run_id, error)] = steps.finalized
    assert run_id == RUN_ID and error["stage"] == "verify"
    assert "verify-report exploded" in steps.notified[0][1]
    summary = _summary(roots)
    assert summary["result"] == "FAILED" and summary["stage"] == "verify"


def test_interrupt_handlers_raise_once_then_let_the_cleanup_finish():
    import signal

    previous = scan_run.install_interrupt_handlers()
    try:
        with pytest.raises(scan_run.ScanRunInterrupted) as caught:
            os.kill(os.getpid(), signal.SIGTERM)
            for _ in range(100):          # the handler runs between bytecodes
                pass
        assert caught.value.name == "SIGTERM"
        os.kill(os.getpid(), signal.SIGHUP)       # second signal during cleanup: ignored
        for _ in range(100):
            pass
    finally:
        scan_run.restore_handlers(previous)
    assert signal.getsignal(signal.SIGTERM) == previous[signal.SIGTERM]


def test_call_kills_its_child_group_when_the_orchestrator_is_interrupted(tmp_path):
    import signal
    import time

    pidfile = tmp_path / "child.pid"

    def boom(signum, frame):
        raise scan_run.ScanRunInterrupted(signal.SIGTERM)

    previous = signal.signal(signal.SIGALRM, boom)
    signal.setitimer(signal.ITIMER_REAL, 0.5)
    try:
        with pytest.raises(scan_run.ScanRunInterrupted):
            scan_run._call(["/bin/sh", "-c", f"sleep 30 & echo $! > {pidfile}; wait"],
                           env={"PATH": "/usr/bin:/bin"}, timeout=20)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
    pid = int(pidfile.read_text(encoding="utf-8").strip())
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        pytest.fail("the runner's group survived an interrupted scan_run")


def test_unverified_report_is_still_delivered_but_flagged_in_the_title(roots):
    steps = _Steps(roots, verification={"report_covered": True, "publication_ok": True,
                                        "orchestration_verified": True, "completeness_ok": False})
    assert _run(roots, steps) == scan_run.EXIT_OK
    assert "verify ✗" in steps.delivered[0]["title"]


def test_not_a_trading_day_exits_quietly(roots):
    steps = _Steps(roots, date=None)
    assert _run(roots, steps) == scan_run.EXIT_OK
    assert steps.calls == ["resolve_date"] and steps.notified == []


def test_live_manual_scan_run_blocks_the_unattended_run(roots):
    steps = _Steps(roots, live=[{"run_id": "20260928T130000000000Z", "age_minutes": 3}])
    assert _run(roots, steps) == run_lock.EXIT_HELD
    assert "begin" not in steps.calls
    assert steps.notified and "20260928T130000000000Z" in steps.notified[0][1]


def test_manual_run_that_appears_during_the_readiness_wait_blocks_begin(roots):
    """The readiness wait can last 70 minutes: live runs are re-checked right before begin
    (review I3)."""
    steps = _Steps(roots)
    answers = iter([[], [{"run_id": "20260928T134000000000Z", "age_minutes": 1}]])
    base = steps.as_steps()

    def live_runs():
        steps.calls.append("live_runs")
        return next(answers)

    base.live_runs = live_runs
    log = scan_run.OpsLog(roots / "reports_claude" / "_ops" / "scan_run_test.log")
    try:
        code = scan_run.run_once(_args(), base, log=log)
    finally:
        log.close()
    assert code == run_lock.EXIT_HELD
    assert steps.calls == ["resolve_date", "live_runs", "wait_ready", "live_runs", "notify"]
    assert "20260928T134000000000Z" in steps.notified[0][1]


def test_lake_not_ready_notifies_and_never_begins(roots):
    steps = _Steps(roots, ready=False)
    assert _run(roots, steps) == scan_run.EXIT_FAILED
    assert "begin" not in steps.calls
    assert "未开" in steps.notified[0][0] and "stk_factor_pro" in steps.notified[0][1]


def test_skip_readiness_goes_straight_to_begin(roots):
    steps = _Steps(roots, ready=False)
    assert _run(roots, steps, skip_readiness=True) == scan_run.EXIT_OK
    assert "wait_ready" not in steps.calls


def test_begin_failure_notifies_failed_with_the_log_path(roots):
    steps = _Steps(roots, begin_error="HOST_CAPABILITY_REQUIRED: web")
    assert _run(roots, steps) == scan_run.EXIT_FAILED
    title, body = steps.notified[0]
    assert "FAILED" in title and "begin" in body and "HOST_CAPABILITY_REQUIRED" in body
    assert "scan_run_test.log" in body
    assert steps.finalized == []          # no run exists yet


def test_unfinished_runner_finalizes_failed_and_notifies_stage_and_reason(roots):
    outcome = {
        "finished": False, "stop_reason": "BLOCKED", "status": "BLOCKED",
        "errors": [{"code": "TASK_FAILED", "task_id": "scan.l4.card.600519",
                    "message": "claude -p 超时 1500s"}],
        "dispatches": [{"task_id": "scan.l4.card.600519", "attempt": 2,
                        "outcome": "FAILED:TIMEOUT"}],
        "finish": None,
    }
    steps = _Steps(roots, outcome=outcome)
    assert _run(roots, steps) == scan_run.EXIT_FAILED
    assert "deliver" not in steps.calls
    [(run_id, error)] = steps.finalized
    assert run_id == RUN_ID and error["stage"] == "scan.l4.card.600519"
    assert error["stop_reason"] == "BLOCKED"
    title, body = steps.notified[0]
    assert "FAILED" in title and DATE in title
    assert "scan.l4.card.600519" in body and "超时" in body and RUN_ID in body
    assert "scan_run_test.log" in body


def test_runner_crash_without_outcome_is_failed_at_stage_runner(roots):
    steps = _Steps(roots, outcome=None)
    assert _run(roots, steps) == scan_run.EXIT_FAILED
    assert steps.finalized[0][1]["stage"] == "runner"


def test_headless_request_is_a_valid_session_v1_scan_request():
    from autoresearch.contracts.session_task import validate_begin_request
    from autoresearch.session_agent.hosts.base import observe_host
    from autoresearch.session_agent.origin import preflight_session_host

    request = scan_run.build_headless_request(DATE, engine="claude")
    validate_begin_request(request, expected_engine="claude")
    observe_host(request["host_profile"])
    preflight_session_host(request)
    profile = request["host_profile"]
    assert (request["kind"], request["requested_mode"], request["analysis_date"]) == (
        "scan-market", "AUTO", DATE)
    assert request["force_full"] is False           # pinned → SENTINEL_PINNED decides, no override
    assert profile["session_ref"].startswith("headless-")
    assert profile["independent_context"] is True and profile["web_search"] is True
    assert profile["safe_resume"] is False          # executor cannot re-attach
    assert profile["evidence_refs"] and not any(
        ref.startswith("transcript-file:") for ref in profile["evidence_refs"])
    assert scan_run.build_headless_request(DATE, engine="claude")["host_profile"]["session_ref"] != \
        profile["session_ref"]


def test_headless_request_for_a_codex_run_is_the_same_contract_on_the_codex_engine(monkeypatch):
    """2026-10-08:codex 场每个推理任务一个 `codex exec` 线程;请求与 claude 场同一份契约,只换引擎。"""
    from autoresearch.contracts.session_task import validate_begin_request
    from autoresearch.session_agent.hosts.base import observe_host

    monkeypatch.setattr(ws, "ENGINE", "codex")
    request = scan_run.build_headless_request(DATE)          # engine defaults to the workspace engine
    validate_begin_request(request, expected_engine="codex")
    observe_host(request["host_profile"])
    assert request["host_profile"]["engine"] == "codex"
    assert any(ref.endswith("headless_codex.py") for ref in request["host_profile"]["evidence_refs"])
    assert scan_run._env("R1")["AUTORESEARCH_ENGINE"] == "codex"
    assert scan_run._env("R1", engine="claude")["AUTORESEARCH_ENGINE"] == "claude"


def _spool(roots, run_id: str, *, status="ACTIVE", minutes_ago=5, lease=None) -> None:
    folder = roots / "context_claude" / ws.RUN_SPOOLS["scan-market"] / run_id
    folder.mkdir(parents=True)
    beat = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
    (folder / "state.json").write_text(json.dumps({
        "run_id": run_id, "business_status": status,
        "lease": lease or {"heartbeat": beat}, "updated_at": beat}), encoding="utf-8")


def test_live_scan_runs_lists_only_recent_active_scan_runs(roots):
    _spool(roots, "20260928T120000000000Z", minutes_ago=5)
    _spool(roots, "20260928T100000000000Z", minutes_ago=300)
    _spool(roots, "20260928T110000000000Z", status="SUCCEEDED", minutes_ago=1)
    live = scan_run.live_scan_runs()
    assert [item["run_id"] for item in live] == ["20260928T120000000000Z"]


def test_locate_compat_brief_follows_the_delivery_sidecar(roots):
    base = roots / "reports_claude" / "scan"
    compat = base / "20260928-0928_2120"
    compat.mkdir(parents=True)
    (compat / "brief.md").write_text("b", encoding="utf-8")
    (compat / "delivery.json").write_text(json.dumps({"run_id": RUN_ID}), encoding="utf-8")
    other = base / "20260925-0925_2130"        # sorts first: another run's compat view
    other.mkdir()
    (other / "brief.md").write_text("old", encoding="utf-8")
    (other / "delivery.json").write_text(json.dumps({"run_id": "X"}), encoding="utf-8")
    canonical = base / "runs" / RUN_ID / "p1"
    canonical.mkdir(parents=True)
    (canonical / "brief.md").write_text("b", encoding="utf-8")
    assert scan_run.locate_brief(RUN_ID, str(canonical)) == compat / "brief.md"


def test_locate_brief_falls_back_to_canonical_when_no_compat_view(roots):
    canonical = roots / "reports_claude" / "scan" / "runs" / RUN_ID / "p1"
    canonical.mkdir(parents=True)
    (canonical / "brief.md").write_text("b", encoding="utf-8")
    assert scan_run.locate_brief(RUN_ID, str(canonical)) == canonical / "brief.md"


def test_default_delivery_never_writes_into_the_canonical_publication(roots, monkeypatch):
    """The canonical root is a hashed, sealed directory: the delivery record for a
    canonical-only brief goes to $RPT/_ops/, never next to the sealed brief."""
    from autoresearch.scan import delivery

    canonical = roots / "reports_claude" / "scan" / "runs" / RUN_ID / "p1"
    canonical.mkdir(parents=True)
    (canonical / "brief.md").write_text("b", encoding="utf-8")
    seen = {}
    monkeypatch.setattr(delivery, "send", lambda brief, **kw: seen.update(kw, brief=brief) or {})
    steps = scan_run.default_steps(_args(), log=None)
    steps.deliver(canonical / "brief.md", run_id=RUN_ID, title="t", report_path=str(canonical))
    assert Path(seen["record_path"]).parent == roots / "reports_claude" / "_ops"


def test_default_begin_and_run_call_the_session_agent_cli(roots, monkeypatch):
    calls = []

    def fake_call(argv, *, env, timeout, stderr=None):
        calls.append((argv, env))
        if "begin" in argv:
            return 0, json.dumps({"run_id": RUN_ID})
        return 8, "log line\n" + json.dumps({"finished": False}) + "\n"

    monkeypatch.setattr(scan_run, "_call", fake_call)
    steps = scan_run.default_steps(_args(claude_bin="/opt/claude", max_parallel=6), log=None)
    assert steps.begin(roots / "req.json") == RUN_ID
    assert steps.run(RUN_ID, 600.0) == {"finished": False}
    (begin_argv, begin_env), (run_argv, run_env) = calls
    assert begin_argv[:3] == [sys.executable, "-m", "autoresearch.session_agent"]
    # scan_run holds the scan lock itself, so its own begin must pass the explicit override
    assert begin_argv[3:] == ["begin", "--orchestration", "session_v1", "--request-file",
                              str(roots / "req.json"), "--ignore-scan-lock",
                              "--executor", "headless"]    # 与 run 同一个执行器(复审 I-1)
    assert "AUTORESEARCH_RUN_ID" not in begin_env and begin_env["AUTORESEARCH_ENGINE"] == "claude"
    assert run_argv[3:] == ["run", "--run-id", RUN_ID, "--executor", "headless",
                            "--claude-bin", "/opt/claude", "--max-parallel", "6"]
    assert run_env["AUTORESEARCH_RUN_ID"] == RUN_ID


def test_default_wait_ready_also_guards_the_lake_partition(roots, monkeypatch):
    """Readiness is about tushare; the scan reads the lake — a half prewarm partition is
    quarantined right after readiness passes (review I1)."""
    from autoresearch.scan import readiness

    seen = []
    monkeypatch.setattr(readiness, "factor_rows_ready",
                        lambda *a, **k: pytest.fail("must guard the lake, not only probe"))
    monkeypatch.setattr(readiness, "wait_and_guard",
                        lambda date, **kw: seen.append((date, kw.get("deadline"))) or True)
    assert scan_run.default_steps(_args(), log=None).wait_ready(DATE, "22:30") is True
    assert seen == [(DATE, "22:30")]


def test_default_runner_timeout_is_a_failed_outcome_not_a_hang(roots, monkeypatch):
    monkeypatch.setattr(scan_run, "_call", lambda argv, **kw: (None, ""))
    killed = []
    monkeypatch.setattr(scan_run, "_terminate_inflight_headless",
                        lambda run_id: killed.append(run_id) or [111, 222])
    outcome = scan_run.default_steps(_args(), log=None).run(RUN_ID, 60.0)
    assert outcome["finished"] is False and outcome["stop_reason"] == "RUN_TIMEOUT"
    assert killed == [RUN_ID] and "2 个在飞" in outcome["errors"][0]["message"]
    assert "1 分钟" in outcome["errors"][0]["message"]


def test_terminate_inflight_kills_running_claude_groups_from_the_records(tmp_path):
    """A runner killed by the wall clock leaves ``claude -p`` sessions behind (their own
    process groups); the executor's call records name them, so they can be stopped."""
    import subprocess

    folder = tmp_path / "_dispatch" / "headless"
    folder.mkdir(parents=True)
    running = subprocess.Popen(["/bin/sh", "-c", "sleep 30"], start_new_session=True)
    finished = subprocess.Popen(["/bin/sh", "-c", "sleep 30"], start_new_session=True)
    (folder / "a.a1.json").write_text(json.dumps(
        {"task_id": "a", "state": "RUNNING", "pid": running.pid}), encoding="utf-8")
    # an EXITED record whose pid now belongs to some live process must be left alone
    (folder / "b.a1.json").write_text(json.dumps(
        {"task_id": "b", "state": "EXITED", "pid": finished.pid}), encoding="utf-8")
    try:
        assert scan_run.terminate_inflight(folder) == [running.pid]
        assert running.wait(timeout=10) is not None
        assert finished.poll() is None
    finally:
        for proc in (running, finished):
            if proc.poll() is None:
                proc.kill()
                proc.wait()


def test_terminate_inflight_escalates_to_sigkill_for_a_group_ignoring_sigterm(tmp_path):
    """review M1b: TERM only left a group that traps TERM running."""
    import subprocess

    import time

    folder = tmp_path / "_dispatch" / "headless"
    folder.mkdir(parents=True)
    ready = tmp_path / "trap.ready"
    stubborn = subprocess.Popen(["/bin/sh", "-c", f"trap '' TERM; touch {ready}; sleep 30"],
                                start_new_session=True)
    deadline = time.monotonic() + 10
    while not ready.exists():                 # TERM before the trap is set would just kill it
        assert time.monotonic() < deadline
        time.sleep(0.02)
    (folder / "a.a1.json").write_text(json.dumps(
        {"task_id": "a", "state": "RUNNING", "pid": stubborn.pid}), encoding="utf-8")
    try:
        assert scan_run.terminate_inflight(folder, grace=0.5) == [stubborn.pid]
        assert stubborn.wait(timeout=5) is not None
    finally:
        if stubborn.poll() is None:
            stubborn.kill()
            stubborn.wait()


def test_terminate_inflight_never_signals_a_pid_twin(tmp_path):
    import subprocess

    folder = tmp_path / "_dispatch" / "headless"
    folder.mkdir(parents=True)
    twin = subprocess.Popen(["/bin/sh", "-c", "sleep 30"], start_new_session=True)
    (folder / "a.a1.json").write_text(json.dumps(
        {"task_id": "a", "state": "RUNNING", "pid": twin.pid,
         "process_started_at": "Thu Jan  1 00:00:00 1970"}), encoding="utf-8")
    try:
        assert scan_run.terminate_inflight(folder, grace=0.2) == []
        assert twin.poll() is None
    finally:
        twin.kill()
        twin.wait()


def test_inflight_records_are_found_under_the_registered_path(roots, monkeypatch):
    from autoresearch.contracts import artifacts as ca

    run_root = roots / "context_claude" / "scan_runs" / RUN_ID
    folder = run_root / "staging" / DATE / Path(ca.by_name("dispatch_headless_calls").path).parent
    folder.mkdir(parents=True)
    monkeypatch.setattr(ws, "find_run_root", lambda run_id: run_root)
    seen = []
    monkeypatch.setattr(scan_run, "terminate_inflight", lambda path: seen.append(path) or [7])
    assert scan_run._terminate_inflight_headless(RUN_ID) == [7]
    assert seen == [folder]


def test_scan_layer_never_imports_session_agent():
    """scan sits below session_agent (tests/contracts/test_layering.py); scan_run talks to
    session_agent only through its CLI and the registered record path."""
    text = (Path(scan_run.__file__)).read_text(encoding="utf-8")
    assert "from autoresearch.session_agent" not in text
    assert "import autoresearch.session_agent" not in text


def test_call_kills_the_whole_process_group_on_timeout(tmp_path):
    pidfile = tmp_path / "child.pid"
    code, _ = scan_run._call(
        ["/bin/sh", "-c", f"sleep 30 & echo $! > {pidfile}; wait"],
        env={"PATH": "/usr/bin:/bin"}, timeout=0.5)
    assert code is None
    import time
    pid = int(pidfile.read_text(encoding="utf-8").strip())
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        pytest.fail("grandchild survived the runner timeout")


def test_main_refuses_when_the_lock_is_held(roots, monkeypatch, capsys):
    from autoresearch.scan import delivery

    told = []
    monkeypatch.setattr(delivery, "notify", lambda title, body, **kw: told.append(body) or {})
    held = run_lock.try_acquire(roots / "context_claude" / run_lock.LOCK_NAME)
    try:
        monkeypatch.setattr(scan_run, "run_once", lambda *a, **k: pytest.fail("must not run"))
        assert scan_run.main([]) == run_lock.EXIT_HELD
        assert f"pid {os.getpid()}" in capsys.readouterr().out
        assert told and f"pid {os.getpid()}" in told[0]
    finally:
        held.release()


def test_main_turns_termination_signals_into_a_clean_abort(roots, monkeypatch):
    """review M1d: while the scan runs, SIGTERM/SIGHUP/SIGINT are ours (→ abort path), and
    the previous handlers come back afterwards."""
    import signal

    seen = {}

    def fake_run_once(args, steps, *, log):
        seen.update({sig: signal.getsignal(sig) for sig in scan_run.INTERRUPT_SIGNALS})
        return 0

    monkeypatch.setattr(scan_run, "run_once", fake_run_once)
    monkeypatch.setattr(scan_run, "default_steps", lambda args, log: None)
    before = {sig: signal.getsignal(sig) for sig in scan_run.INTERRUPT_SIGNALS}
    assert scan_run.main([]) == 0
    for sig in scan_run.INTERRUPT_SIGNALS:
        assert callable(seen[sig]) and seen[sig] is not before[sig], sig
        assert signal.getsignal(sig) == before[sig]


def test_main_refuses_an_unknown_engine(roots, monkeypatch, capsys):
    monkeypatch.setattr(ws, "ENGINE", "gemini")
    assert scan_run.main([]) == scan_run.EXIT_USAGE
    assert scan_run.HEADLESS_ENGINES == ("claude", "codex")


def test_runner_step_passes_the_cli_path_of_the_run_engine_only(roots, monkeypatch):
    """claude 场传 --claude-bin,codex 场传 --codex-bin;另一引擎的路径不混进 argv。"""
    seen = {}

    def fake_call(argv, *, env, timeout, stderr=None):
        seen["argv"], seen["env"] = list(argv), dict(env)
        return 0, json.dumps({"finished": True})

    monkeypatch.setattr(scan_run, "_call", fake_call)
    args = SimpleNamespace(claude_bin="/opt/claude", codex_bin="/opt/codex", max_parallel=4)
    scan_run.default_steps(args, None).run("R1", 60.0)
    assert "--claude-bin" in seen["argv"] and "--codex-bin" not in seen["argv"]
    assert seen["env"]["AUTORESEARCH_ENGINE"] == "claude"
    monkeypatch.setattr(ws, "ENGINE", "codex")
    scan_run.default_steps(args, None).run("R1", 60.0)
    assert "--codex-bin" in seen["argv"] and "--claude-bin" not in seen["argv"]
    assert seen["argv"][seen["argv"].index("--executor") + 1] == "headless"
    assert seen["env"]["AUTORESEARCH_ENGINE"] == "codex"


def test_headless_request_matches_the_documented_scan_request_contract():
    """无人值守请求与文档样例必须是同一份契约(只有宿主与日期不同)。

    2026-09-30 请求升到 schema v4(research_context / card_research_profile / 宏观与行业 profile),
    交互样例跟着升了,这里的生成器还停在 v1 → 无人值守场的 DecisionFrame 没有日历来源证据。
    """
    example = json.loads(
        (Path(__file__).resolve().parents[2] / "docs/session-agent/examples/scan.request.json")
        .read_text(encoding="utf-8"))
    request = scan_run.build_headless_request(DATE)
    assert request["schema_version"] == example["schema_version"] == 4
    assert set(request) == set(example)
    varying = {"analysis_date", "host_profile", "force_full"}
    assert {k: v for k, v in request.items() if k not in varying} == {
        k: v for k, v in example.items() if k not in varying}


# ── 2026-10-10 token growth guard: post-run redline readout + opening breaker (M3 / M4) ──────

def _guarded(steps: _Steps, *, redline=None, breaker=None, acked=None, gate=None):
    import dataclasses

    acked = acked if acked is not None else []

    def record(run_id):
        steps.calls.append("redline")
        if isinstance(redline, Exception):
            raise redline
        return redline or {"verdict": "PASS", "findings": []}

    def ack(run_id):
        steps.calls.append("ack")
        acked.append(run_id)

    def replay_gate():
        steps.calls.append("replay_gate")
        if isinstance(gate, Exception):
            raise gate
        return gate or {"verdict": "PASS", "run_id": "20261009T130000000000Z", "checked": 26, "regressions": []}

    built = steps.as_steps()
    return dataclasses.replace(built, redline=record, breaker=lambda: breaker, ack_breaker=ack,
                               replay_gate=replay_gate)


def _run_guarded(tmp_path, steps, guarded, **arg_changes) -> int:
    log = scan_run.OpsLog(tmp_path / "reports_claude" / "_ops" / "scan_run_test.log")
    try:
        return scan_run.run_once(_args(**arg_changes), guarded, log=log)
    finally:
        log.close()


def test_success_records_the_redline_after_verify_without_changing_the_outcome(roots):
    steps = _Steps(roots)
    code = _run_guarded(roots, steps, _guarded(steps, redline={"verdict": "FAIL", "findings": ["FAIL:RUN_OVER_LINE"]}))
    assert code == scan_run.EXIT_OK                                  # a readout never blocks delivery
    assert steps.calls.index("verify") < steps.calls.index("redline") < steps.calls.index("deliver")
    summary = _summary(roots)
    assert summary["result"] == "FINISHED" and summary["redline"]["verdict"] == "FAIL"


def test_sealed_failure_is_measured_too_but_a_recoverable_stop_is_not(roots):
    steps = _Steps(roots, outcome={"finished": False, "stop_reason": "BLOCKED", "errors": [], "dispatches": []})
    _run_guarded(roots, steps, _guarded(steps))
    assert steps.calls.index("finalize_failed") < steps.calls.index("redline")
    paused = _Steps(roots, outcome={"finished": False, "stop_reason": "USAGE_LIMIT", "recoverable": True,
                                    "recovery_tasks": [], "errors": [], "dispatches": []})
    _run_guarded(roots, paused, _guarded(paused))
    assert "redline" not in paused.calls                             # the run is not over yet


def test_a_crashing_readout_is_logged_not_raised(roots):
    steps = _Steps(roots)
    assert _run_guarded(roots, steps, _guarded(steps, redline=RuntimeError("boom"))) == scan_run.EXIT_OK
    assert _summary(roots)["redline"]["verdict"] == "ERROR"


def test_an_unacknowledged_breaker_refuses_a_new_run_before_waiting_for_the_lake(roots):
    breaker = {"run_id": "20261009T130000000000Z", "readout": "/x.json",
               "findings": [{"level": "FAIL", "code": "PREFIX_DRIFT"}]}
    steps = _Steps(roots)
    code = _run_guarded(roots, steps, _guarded(steps, breaker=breaker))
    assert code == scan_run.EXIT_FAILED
    assert "wait_ready" not in steps.calls and "begin" not in steps.calls
    summary = _summary(roots)
    assert summary["result"] == "REFUSED_BUDGET" and summary["breaker"]["run_id"] == breaker["run_id"]
    [(title, body)] = steps.notified
    assert "--ack-redline 20261009T130000000000Z" in body and "PREFIX_DRIFT" in body


def test_ack_redline_for_that_run_releases_the_breaker(roots):
    breaker = {"run_id": "20261009T130000000000Z", "findings": []}
    steps, acked = _Steps(roots), []
    code = _run_guarded(roots, steps, _guarded(steps, breaker=breaker, acked=acked),
                        ack_redline="20261009T130000000000Z")
    assert code == scan_run.EXIT_OK and acked == ["20261009T130000000000Z"] and "begin" in steps.calls


def test_ack_for_another_run_does_not_release_it(roots):
    breaker = {"run_id": "20261009T130000000000Z", "findings": []}
    steps = _Steps(roots)
    code = _run_guarded(roots, steps, _guarded(steps, breaker=breaker), ack_redline="20260101T000000000000Z")
    assert code == scan_run.EXIT_FAILED and "ack" not in steps.calls


def test_resume_with_ack_for_the_same_run_acknowledges_before_resuming(roots):
    steps, acked = _Steps(roots), []
    guarded = _guarded(steps, acked=acked)
    import dataclasses

    guarded = dataclasses.replace(guarded, resume=lambda run_id: DATE)
    _run_guarded(roots, steps, guarded, resume_run_id=RUN_ID, ack_redline=RUN_ID)
    assert acked == [RUN_ID] and "begin" not in steps.calls


_REGRESSED = {"verdict": "FAIL", "run_id": "20261009T130000000000Z", "checked": 26,
              "regressions": [{"task_id": "l4.688578.a1.review2", "now": "REJECT", "reason": "精度契约"}]}


def test_a_validator_regression_on_last_nights_accepted_cards_refuses_the_run(roots):
    steps = _Steps(roots)
    code = _run_guarded(roots, steps, _guarded(steps, gate=_REGRESSED))
    assert code == scan_run.EXIT_FAILED and "wait_ready" not in steps.calls and "begin" not in steps.calls
    assert steps.calls.index("replay_gate") < len(steps.calls)
    summary = _summary(roots)
    assert summary["result"] == "REFUSED_REPLAY" and summary["replay_gate"]["regressions"] == ["l4.688578.a1.review2"]
    [(title, body)] = steps.notified
    assert "--ack-redline 20261009T130000000000Z" in body and "l4.688578.a1.review2" in body


def test_ack_for_the_replayed_run_lets_the_run_proceed(roots):
    steps, acked = _Steps(roots), []
    code = _run_guarded(roots, steps, _guarded(steps, gate=_REGRESSED, acked=acked),
                        ack_redline="20261009T130000000000Z")
    assert code == scan_run.EXIT_OK and acked == ["20261009T130000000000Z"] and "begin" in steps.calls


def test_a_crashing_gate_is_logged_and_does_not_cost_the_night(roots):
    steps = _Steps(roots)
    assert _run_guarded(roots, steps, _guarded(steps, gate=RuntimeError("boom"))) == scan_run.EXIT_OK
    assert _summary(roots)["replay_gate"]["verdict"] == "ERROR"
