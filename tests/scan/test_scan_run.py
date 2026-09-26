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
        self.compat = tmp_path / "reports_claude" / "scan" / "20260928-0928_2120"
        assert not changes, changes

    def as_steps(self) -> scan_run.Steps:
        def resolve_date(explicit):
            self.calls.append("resolve_date")
            return self.date

        def live_runs():
            self.calls.append("live_runs")
            return self.live

        def wait_ready(date, deadline):
            self.calls.append("wait_ready")
            return self.ready

        def begin(request_path):
            self.calls.append("begin")
            self.request = json.loads(Path(request_path).read_text(encoding="utf-8"))
            if self.begin_error:
                raise RuntimeError(self.begin_error)
            return RUN_ID

        def run(run_id):
            self.calls.append("run")
            return self.outcome

        def verify(canonical, run_id):
            self.calls.append("verify")
            return self.verification

        def locate_brief(run_id, canonical):
            self.calls.append("locate_brief")
            self.compat.mkdir(parents=True, exist_ok=True)
            (self.compat / "brief.md").write_text("brief", encoding="utf-8")
            return self.compat / "brief.md"

        def deliver(brief, **kwargs):
            self.calls.append("deliver")
            self.delivered.append({"brief": brief, **kwargs})
            return {"status": "SENT"}

        def finalize_failed(run_id, error):
            self.calls.append("finalize_failed")
            self.finalized.append((run_id, error))

        def notify(title, body):
            self.calls.append("notify")
            self.notified.append((title, body))
            return {"status": "SENT"}

        return scan_run.Steps(
            resolve_date=resolve_date, live_runs=live_runs, wait_ready=wait_ready, begin=begin,
            run=run, verify=verify, locate_brief=locate_brief, deliver=deliver,
            finalize_failed=finalize_failed, notify=notify)


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
    assert steps.calls == ["resolve_date", "live_runs", "wait_ready", "begin", "run", "verify",
                           "locate_brief", "deliver"]
    [sent] = steps.delivered
    assert sent["brief"] == steps.compat / "brief.md"
    assert sent["run_id"] == RUN_ID and DATE in sent["title"] and "✓" in sent["title"]
    assert sent["report_path"] == str(steps.compat)
    assert steps.notified == [] and steps.finalized == []
    summary = json.loads((roots / "reports_claude" / "_ops" / f"scan_run_{DATE}.json")
                         .read_text(encoding="utf-8"))
    assert summary["run_id"] == RUN_ID and summary["result"] == "DELIVERED"


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

    request = scan_run.build_headless_request(DATE)
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
    assert scan_run.build_headless_request(DATE)["host_profile"]["session_ref"] != \
        profile["session_ref"]


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
    assert steps.run(RUN_ID) == {"finished": False}
    (begin_argv, begin_env), (run_argv, run_env) = calls
    assert begin_argv[:3] == [sys.executable, "-m", "autoresearch.session_agent"]
    assert begin_argv[3:] == ["begin", "--orchestration", "session_v1", "--request-file",
                              str(roots / "req.json")]
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
    outcome = scan_run.default_steps(_args(run_timeout_minutes=1), log=None).run(RUN_ID)
    assert outcome["finished"] is False and outcome["stop_reason"] == "RUN_TIMEOUT"
    assert killed == [RUN_ID] and "2 个在飞" in outcome["errors"][0]["message"]


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


def test_main_refuses_a_codex_engine(roots, monkeypatch, capsys):
    monkeypatch.setattr(ws, "ENGINE", "codex")
    assert scan_run.main([]) == scan_run.EXIT_USAGE
