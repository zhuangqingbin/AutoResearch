"""Headless executor: one ``claude -p --agent <role>`` process per inference attempt.

Every test drives a FAKE ``claude`` shell script written into ``tmp_path`` — the real CLI
and the network are never touched.
"""
from __future__ import annotations

import json
import os
import stat
import textwrap
import time
from pathlib import Path

import pytest

from autoresearch.session_agent.executors import headless_claude as hc
from autoresearch.session_agent.executors.base import (
    DispatchRequest,
    ExecutorTimeout,
    classify_error,
)


@pytest.fixture(autouse=True)
def simulated_access_for_fake_cli(monkeypatch):
    # This suite exercises subprocess lifecycle, never claims host enforcement.
    # Real C4 manifest/binding behavior is covered by test_task_access.py.
    monkeypatch.setattr(hc, 'bind_task_access', lambda *args, **kwargs: {'enforcement': 'SIMULATED'})


TASK = "scan.l4.card.600000"

#: Fake claude prelude: pick the session id out of argv, log argv one per line.
_PRELUDE = textwrap.dedent("""\
    #!/bin/sh
    sid=""; prev=""
    for a in "$@"; do
      if [ "$prev" = "--session-id" ]; then sid="$a"; fi
      prev="$a"
    done
    : > "$(dirname "$0")/argv.txt"
    for a in "$@"; do printf '%s\\n' "$a" >> "$(dirname "$0")/argv.txt"; done
    """)


def _fake_claude(tmp_path: Path, body: str) -> str:
    path = tmp_path / "bin" / "claude"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_PRELUDE + textwrap.dedent(body), encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def _request(tmp_path: Path, *, timeout=10.0, role="scan.l4.card", model=None, effort="max",
             tier="critical", max_turns=None, independent=False) -> DispatchRequest:
    return DispatchRequest(
        run_id="20260913T010203000000Z", engine="claude", task_id=TASK, attempt=1,
        role=role, agent_type="l4-card", config_role="l4_card", model=model,
        effort=effort, agent_spec={"effort": effort}, tier=tier, max_turns=max_turns,
        prompt="执行 details/_l4_prompt_600000.md 的任务包,写决策卡",
        instruction_refs=(".claude/agents/l4-card.md",),
        input_paths={"p": str(tmp_path / "_l4_prompt_600000.md")},
        output_paths={"card": str(tmp_path / "staging" / "details" / "600000.md")},
        subject="600000", independent_context=independent, tool_policy="READ_WRITE_WEB",
        timeout_seconds=timeout, host_session_ref="headless-host",
    )


def _executor(tmp_path: Path, claude_bin: str, **kwargs) -> hc.HeadlessClaudeExecutor:
    return hc.HeadlessClaudeExecutor(
        tmp_path / "staging", claude_bin=claude_bin, cwd=tmp_path,
        transcript_root=tmp_path / "projects", **kwargs)


_WRITE_OUTPUT_AND_SUCCEED = """\
    mkdir -p "{out_dir}"; printf 'card' > "{out}"
    mkdir -p "{projects}"; printf '{{"type":"assistant"}}\\n' > "{projects}/$sid.jsonl"
    echo "{{\\"type\\":\\"result\\",\\"is_error\\":false,\\"session_id\\":\\"$sid\\",\\"total_cost_usd\\":0.5,\\"num_turns\\":3,\\"usage\\":{{\\"output_tokens\\":9}},\\"result\\":\\"ok\\"}}"
    """


def _success_body(tmp_path: Path) -> str:
    out = tmp_path / "staging" / "details" / "600000.md"
    return _WRITE_OUTPUT_AND_SUCCEED.format(
        out_dir=out.parent, out=out, projects=tmp_path / "projects")


def _record(tmp_path: Path) -> dict:
    path = tmp_path / "staging" / "_dispatch" / "headless" / f"{TASK}.a1.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_dispatch_success_returns_session_identity_usage_and_transcript(tmp_path):
    ex = _executor(tmp_path, _fake_claude(tmp_path, _success_body(tmp_path)))
    result = ex.dispatch(_request(tmp_path))
    assert result.ok is True, result.error
    assert result.session_ref and result.context_ref == result.session_ref
    assert result.parent_context_ref == "headless-host"
    assert result.usage == {"output_tokens": 9}
    assert result.transcript_path == str(tmp_path / "projects" / f"{result.session_ref}.jsonl")
    assert result.evidence_refs == ()   # the runner binds the transcript itself


def test_argv_carries_the_required_flags_and_never_skips_permissions(tmp_path):
    ex = _executor(tmp_path, _fake_claude(tmp_path, _success_body(tmp_path)))
    result = ex.dispatch(_request(tmp_path))
    argv = (tmp_path / "bin" / "argv.txt").read_text(encoding="utf-8").splitlines()
    assert argv[0] == "-p"
    assert argv[argv.index("--agent") + 1] == "l4-card"
    assert argv[argv.index("--output-format") + 1] == "json"
    assert argv[argv.index("--permission-mode") + 1] == "bypassPermissions"
    assert argv[argv.index("--session-id") + 1] == result.session_ref
    assert argv[argv.index("--max-turns") + 1] == str(hc.MAX_TURNS["scan.l4.card"])
    assert argv[argv.index("--effort") + 1] == "max"
    assert "--model" not in argv                       # model=None: frontmatter decides
    assert "--dangerously-skip-permissions" not in argv
    # 2026-10-10: research threads never load the auto-memory (MEMORY.md = user rulings + readouts).
    assert json.loads(argv[argv.index("--settings") + 1]) == {"autoMemoryEnabled": False}
    assert argv[-1] == "执行 details/_l4_prompt_600000.md 的任务包,写决策卡"


def test_model_is_passed_through_when_resolved(tmp_path):
    ex = _executor(tmp_path, _fake_claude(tmp_path, _success_body(tmp_path)))
    ex.dispatch(_request(tmp_path, model="sonnet"))
    argv = (tmp_path / "bin" / "argv.txt").read_text(encoding="utf-8").splitlines()
    assert argv[argv.index("--model") + 1] == "sonnet"


def test_every_call_is_recorded_with_a_redacted_prompt(tmp_path):
    ex = _executor(tmp_path, _fake_claude(tmp_path, _success_body(tmp_path)))
    result = ex.dispatch(_request(tmp_path))
    rec = _record(tmp_path)
    assert rec["exit_code"] == 0 and rec["state"] == "EXITED"
    assert rec["session_id"] == result.session_ref
    assert rec["total_cost_usd"] == 0.5 and rec["usage"] == {"output_tokens": 9}
    assert rec["transcript_path"] == result.transcript_path
    assert rec["task_id"] == TASK and rec["attempt"] == 1 and rec["role"] == "scan.l4.card"
    assert "--agent" in rec["argv"] and "l4-card" in rec["argv"]
    assert not any("任务包" in item for item in rec["argv"])   # prompt text never logged
    assert rec["argv"][-1].startswith("<prompt ")


def test_output_missing_after_exit_zero_is_a_failure(tmp_path):
    body = """\
        echo "{\\"is_error\\":false,\\"session_id\\":\\"$sid\\",\\"total_cost_usd\\":0.1,\\"usage\\":{},\\"result\\":\\"wrote it\\"}"
        """
    ex = _executor(tmp_path, _fake_claude(tmp_path, body))
    result = ex.dispatch(_request(tmp_path))
    assert result.ok is False
    assert "产物未落盘" in result.error and "600000.md" in result.error
    assert result.error_class == "CONTRACT_ERROR"
    assert _record(tmp_path)["outputs_missing"] == [
        str(tmp_path / "staging" / "details" / "600000.md")]


def test_is_error_json_fails_with_the_result_excerpt(tmp_path):
    body = """\
        echo "{\\"is_error\\":true,\\"subtype\\":\\"error_during_execution\\",\\"session_id\\":\\"$sid\\",\\"result\\":\\"API rate limited, retry later\\"}"
        """
    ex = _executor(tmp_path, _fake_claude(tmp_path, body))
    result = ex.dispatch(_request(tmp_path))
    assert result.ok is False and "API rate limited" in result.error
    assert classify_error(result.error, result.error_class) == "RATE_LIMIT"


def test_is_error_result_excerpt_is_capped_at_500_chars(tmp_path):
    long_text = "x" * 800
    body = f"""\
        echo "{{\\"is_error\\":true,\\"session_id\\":\\"$sid\\",\\"result\\":\\"{long_text}\\"}}"
        """
    ex = _executor(tmp_path, _fake_claude(tmp_path, body))
    result = ex.dispatch(_request(tmp_path))
    assert result.ok is False
    assert "x" * 500 in result.error and "x" * 501 not in result.error


def test_invalid_json_is_a_failure_even_with_exit_zero(tmp_path):
    body = """\
        mkdir -p "{out_dir}"; printf 'card' > "{out}"
        echo "Credit balance is too low"
        """.format(out_dir=tmp_path / "staging" / "details",
                   out=tmp_path / "staging" / "details" / "600000.md")
    ex = _executor(tmp_path, _fake_claude(tmp_path, body))
    result = ex.dispatch(_request(tmp_path))
    assert result.ok is False and "Credit balance is too low" in result.error


def test_nonzero_exit_reports_stderr(tmp_path):
    body = """\
        echo "connect ECONNREFUSED 127.0.0.1:443" >&2
        exit 1
        """
    ex = _executor(tmp_path, _fake_claude(tmp_path, body))
    result = ex.dispatch(_request(tmp_path))
    assert result.ok is False and "exit=1" in result.error
    assert classify_error(result.error, result.error_class) == "CONNECTION"
    assert _record(tmp_path)["exit_code"] == 1


def _dead(pid: int, deadline: float) -> bool:
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.05)
    return False


def test_timeout_kills_the_whole_process_group(tmp_path, monkeypatch):
    pidfile = tmp_path / "grandchild.pid"
    body = f"""\
        sleep 30 &
        echo $! > "{pidfile}"
        wait
        """
    ex = _executor(tmp_path, _fake_claude(tmp_path, body), kill_grace_seconds=0.2)
    real_popen = hc.subprocess.Popen
    group_started = []

    def started_group(*args, **kwargs):
        proc = real_popen(*args, **kwargs)
        if kwargs.get("start_new_session"):
            # Start the timeout assertion only after its grandchild exists.
            # Slow shell startup must not turn this into a leader-only test.
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if pidfile.is_file() and pidfile.read_text().strip().isdigit():
                    group_started.append(time.monotonic())
                    return proc
                time.sleep(0.01)
            try:
                hc.os.killpg(proc.pid, hc.signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait(timeout=2)
            pytest.fail("fixture did not start its grandchild")
        return proc

    monkeypatch.setattr(hc.subprocess, "Popen", started_group)
    with pytest.raises(ExecutorTimeout, match="超时"):
        ex.dispatch(_request(tmp_path, timeout=0.8))
    assert time.monotonic() - group_started[0] < 8
    grandchild = int(pidfile.read_text(encoding="utf-8").strip())
    assert _dead(grandchild, time.monotonic() + 5), "grandchild `sleep` survived the timeout"
    rec = _record(tmp_path)
    assert rec["timed_out"] is True and rec["state"] == "KILLED"


@pytest.mark.parametrize("probe_error,expected", [
    (None, False), (ProcessLookupError(), True), (PermissionError(), None), (OSError(), None),
])
def test_group_cancellation_confirmation_requires_observed_absence(monkeypatch, probe_error, expected):
    def probe(pgid, sig):
        assert (pgid, sig) == (12345, 0)
        if probe_error is not None:
            raise probe_error

    monkeypatch.setattr(hc.os, "killpg", probe)
    assert hc._group_stopped(12345) is expected


def test_timeout_does_not_claim_group_cancelled_when_only_leader_stopped(tmp_path, monkeypatch):
    ex = _executor(tmp_path, _fake_claude(tmp_path, "exec sleep 30"))

    def only_leader(proc):
        proc.kill()
        proc.wait(timeout=2)
        return False

    monkeypatch.setattr(ex, "_kill_group", only_leader)
    with pytest.raises(ExecutorTimeout, match="未确认"):
        ex.dispatch(_request(tmp_path, timeout=0.05))
    record = _record(tmp_path)
    assert record["cancel_capability"] == "PROCESS_GROUP"
    assert record["cancel_confirmed"] is False
    assert record["state"] == "CANCEL_UNCONFIRMED"


def test_orphan_stop_records_unconfirmed_group(tmp_path, monkeypatch):
    ex = _executor(tmp_path, "unused")
    path = tmp_path / "orphan.json"
    record = {"state": "RUNNING", "pid": 12345}
    monkeypatch.setattr(hc, "owns_group", lambda _: True)
    monkeypatch.setattr(hc, "stop_group", lambda *_: True)
    monkeypatch.setattr(hc, "_group_stopped", lambda _: False, raising=False)
    ex._stop_orphans([(path, record)], "next")
    result = json.loads(path.read_text())
    assert result["cancel_confirmed"] is False
    assert result["state"] == "CANCEL_UNCONFIRMED"


def test_max_turns_explicit_request_wins_then_role_then_tier(tmp_path):
    ex = _executor(tmp_path, "claude")
    assert ex.max_turns_for(_request(tmp_path, max_turns=7)) == 7
    assert ex.max_turns_for(_request(tmp_path)) == hc.MAX_TURNS["scan.l4.card"]
    unknown_role = _request(tmp_path, role="stock.card", tier="analytical")
    assert ex.max_turns_for(unknown_role) == hc.TIER_MAX_TURNS["analytical"]
    assert ex.max_turns_for(_request(tmp_path, role="stock.card", tier=None)) == hc.DEFAULT_MAX_TURNS


def test_max_turns_cover_the_longest_observed_host_runs():
    """Caps sit above the longest real subagent runs (unique assistant messages, 30 days
    before 2026-09-26: macro-brief 25, sector-brief 14, l3-rank 32, l4-intel 34,
    l4-card 35): a cap below them would turn a normal card into error_max_turns → BLOCKED."""
    observed = {"macro.brief": 25, "sector.brief": 14, "scan.l3": 32, "scan.l4.intel": 34,
                "scan.l4.card": 35, "scan.l4.review": 35}
    for role, turns in observed.items():
        assert hc.MAX_TURNS[role] >= 1.5 * turns, role


def test_headless_timeouts_follow_the_spec_role_tiers():
    assert hc.HEADLESS_TIMEOUTS["scan.l4.intel"] == 12 * 60
    assert hc.HEADLESS_TIMEOUTS["scan.l4.card"] == 25 * 60
    assert hc.HEADLESS_TIMEOUTS["scan.l3"] == 30 * 60


def test_codex_requests_are_refused(tmp_path):
    ex = _executor(tmp_path, _fake_claude(tmp_path, _success_body(tmp_path)))
    request = DispatchRequest.from_json({**_request(tmp_path).to_json(), "engine": "codex"})
    result = ex.dispatch(request)
    assert result.ok is False and "claude" in result.error
    assert not (tmp_path / "bin" / "argv.txt").exists()     # nothing was launched


def test_missing_binary_is_reported_not_raised(tmp_path):
    ex = _executor(tmp_path, str(tmp_path / "no-such-claude"))
    result = ex.dispatch(_request(tmp_path))
    assert result.ok is False and "no-such-claude" in result.error


def test_transcript_is_none_when_the_session_file_never_appeared(tmp_path):
    body = """\
        mkdir -p "{out_dir}"; printf 'card' > "{out}"
        echo "{{\\"is_error\\":false,\\"session_id\\":\\"$sid\\",\\"usage\\":{{}},\\"result\\":\\"ok\\"}}"
        """.format(out_dir=tmp_path / "staging" / "details",
                   out=tmp_path / "staging" / "details" / "600000.md")
    ex = _executor(tmp_path, _fake_claude(tmp_path, body))
    result = ex.dispatch(_request(tmp_path))
    assert result.ok is True and result.transcript_path is None


def test_executor_is_protocol_conformant_and_does_not_claim_reattach(tmp_path):
    from autoresearch.session_agent.executors.base import InferenceExecutor, supports_reattach

    ex = _executor(tmp_path, "claude")
    assert isinstance(ex, InferenceExecutor) and ex.name == "headless"
    assert supports_reattach(ex) is False


def test_resolve_claude_bin_prefers_explicit_then_env(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTORESEARCH_CLAUDE_BIN", "/opt/x/claude")
    assert hc.resolve_claude_bin("/explicit/claude") == "/explicit/claude"
    assert hc.resolve_claude_bin(None) == "/opt/x/claude"


def test_call_record_names_the_process_group_while_running(tmp_path):
    """The record is written with state RUNNING + pid (= pgid, own session) before the
    wait, so ``scan.scan_run`` can stop in-flight sessions if the runner is killed."""
    body = f"""\
        cat "{tmp_path / 'staging' / '_dispatch' / 'headless' / (TASK + '.a1.json')}" > "{tmp_path / 'seen.json'}"
        """
    ex = _executor(tmp_path, _fake_claude(tmp_path, body))
    ex.dispatch(_request(tmp_path))
    seen = json.loads((tmp_path / "seen.json").read_text(encoding="utf-8"))
    assert seen["state"] == "RUNNING" and isinstance(seen["pid"], int) and seen["pid"] > 1
    assert os.getpgid(os.getpid()) != seen["pid"]      # its own group, not the runner's


# ── runner × headless: transcript evidence binding (batch 4 Task 2) ────────────

def test_runner_binds_the_headless_transcript_as_independent_review_evidence(tmp_path, monkeypatch):
    """The runner's existing evidence hook (not a second binder) turns a headless session
    into ``host-binding`` evidence: context = the ``claude -p`` session, parent = the run's
    host ref, so an independent review passes on the process boundary."""
    from autoresearch.session_agent import host_evidence, runner

    from . import _runner_support as support

    monkeypatch.setattr(support, "ENGINE", "claude")
    run = support.begin_synthetic_run(
        tmp_path, monkeypatch,
        [support.inf("synthetic.review", role="scan.l4.review", subject="600519",
                     independent=True, inputs=("synthetic.prompt",))],
        host=support.profile(independent_context=True, session_ref="headless-host"),
    )
    from autoresearch.session_agent import artifacts
    out = Path(artifacts.output_paths(run.handle, run.tasks[0], 1)['synthetic.review.out'])
    projects = tmp_path / "projects"
    body = f"""\
        mkdir -p "{out.parent}"; printf 'card' > "{out}"
        mkdir -p "{projects}"
        printf '{{"type":"user"}}\\n{{"type":"assistant","message":{{"id":"m1"}}}}\\n' > "{projects}/$sid.jsonl"
        echo "{{\\"is_error\\":false,\\"session_id\\":\\"$sid\\",\\"total_cost_usd\\":0.4,\\"usage\\":{{\\"output_tokens\\":7}},\\"result\\":\\"ok\\"}}"
        """
    bound, receipts = [], []

    def fake_bind(run_id, task_id, attempt, source_path, **kwargs):
        bound.append({"task_id": task_id, "attempt": attempt, "source": str(source_path), **kwargs})
        return {"evidence_ref": "host-binding:" + "2" * 64}

    monkeypatch.setattr(host_evidence, "bind_task_transcript", fake_bind)
    monkeypatch.setattr(host_evidence, "resolve_receipt_evidence",
                        lambda handle, task, receipt: receipts.append(receipt) or [receipt])
    ex = hc.HeadlessClaudeExecutor(run.handle.staging, claude_bin=_fake_claude(tmp_path, body),
                                   cwd=tmp_path, transcript_root=projects)
    final = runner.run_loop(run.run_id, ex, poll_seconds=0.01, max_rounds=200, hooks=run.hooks())
    assert final["finished"] is True, final
    [binding] = bound
    session_id = binding["context_ref"]
    assert binding["source"] == str(projects / f"{session_id}.jsonl")
    assert binding["session_ref"] == session_id and binding["parent_context_ref"] == "headless-host"
    assert (binding["context_source"], binding["start_ordinal"], binding["end_ordinal"]) == (
        "SUBAGENT", 0, 1)
    [receipt] = receipts
    assert receipt["context_ref"] == session_id != receipt["parent_context_ref"]
    assert receipt["evidence_refs"] == ["host-binding:" + "2" * 64]
    ledger = [json.loads(line) for line in (Path(run.handle.staging) / "_dispatch" / "ledger.jsonl")
              .read_text(encoding="utf-8").splitlines()]
    assert ledger[-1]["usage"] == {"output_tokens": 7}
    assert ledger[-1]["transcript_path"] == binding["source"]


# ── registration & bundle isolation of _dispatch/headless/ ─────────────────────

def test_headless_call_records_are_registered_staging_artifacts():
    from autoresearch.contracts import artifacts as ca

    calls = ca.by_name("dispatch_headless_calls")
    streams = ca.by_name("dispatch_headless_streams")
    stale = ca.by_name("dispatch_headless_stale")
    assert (calls.root, calls.path, calls.kind) == ("staging", "_dispatch/headless/*.json", "json")
    assert (streams.root, streams.path) == ("staging", "_dispatch/headless/*.std*")
    assert (stale.root, stale.path) == ("staging", "_dispatch/headless/stale/*.stale")
    assert calls.presence == streams.presence == stale.presence == "conditional"


def test_scan_staging_bundles_never_capture_headless_records(tmp_path):
    from autoresearch.session_agent.domain_ops import collect_scan_staging_bundle

    ex = _executor(tmp_path, _fake_claude(tmp_path, _success_body(tmp_path)))
    ex.dispatch(_request(tmp_path))
    staging = tmp_path / "staging"
    assert list((staging / "_dispatch" / "headless").glob("*.json"))
    bundle = collect_scan_staging_bundle(staging, phase="prelude")
    assert list(bundle["files"]) == ["details/600000.md"]


# ── CLI: `session_agent run --executor headless` ────────────────────────────────

def _cli_run(tmp_path, monkeypatch, engine: str):
    from autoresearch.trace import capsule

    from . import _runner_support as support

    monkeypatch.setattr(support, "ENGINE", engine)
    run = support.begin_synthetic_run(tmp_path, monkeypatch, [support.inf("synthetic.inference")])
    monkeypatch.setenv("AUTORESEARCH_ENGINE", engine)
    monkeypatch.setenv("AUTORESEARCH_RUN_ID", run.run_id)
    monkeypatch.setattr(capsule, "require_active_run", lambda run_id: run.handle)
    return run


def test_cli_run_headless_builds_the_executor_with_headless_timeouts(tmp_path, monkeypatch, capsys):
    from autoresearch.session_agent import __main__ as cli, runner

    run = _cli_run(tmp_path, monkeypatch, "claude")
    seen = {}

    def fake_loop(run_id, executor, **kwargs):
        seen.update(kwargs, executor=executor)
        return {"status": "DONE", "finished": True, "stop_reason": "FINISHED"}

    monkeypatch.setattr(runner, "run_loop", fake_loop)
    assert cli.main(["run", "--run-id", run.run_id, "--executor", "headless",
                     "--claude-bin", "/opt/fake/claude", "--max-parallel", "3"]) == 0
    executor = seen["executor"]
    assert isinstance(executor, hc.HeadlessClaudeExecutor)
    assert executor.claude_bin == "/opt/fake/claude"
    assert executor.staging == Path(run.handle.staging)
    assert seen["timeouts"] == dict(hc.HEADLESS_TIMEOUTS) and seen["max_parallel"] == 3


def test_cli_run_mailbox_keeps_host_timeouts(tmp_path, monkeypatch, capsys):
    from autoresearch.session_agent import __main__ as cli, runner

    run = _cli_run(tmp_path, monkeypatch, "claude")
    seen = {}
    monkeypatch.setattr(runner, "run_loop", lambda run_id, executor, **kwargs: seen.update(
        kwargs, name=executor.name) or {"finished": True, "stop_reason": "FINISHED"})
    assert cli.main(["run", "--run-id", run.run_id, "--executor", "mailbox"]) == 0
    assert seen["name"] == "mailbox" and seen.get("timeouts") is None


def test_cli_run_headless_builds_the_codex_executor_for_a_codex_run(tmp_path, monkeypatch, capsys):
    """2026-10-08:同一个 `run --executor headless`,按 run 的引擎选传输(codex = `codex exec`)。"""
    from autoresearch.session_agent import __main__ as cli, runner
    from autoresearch.session_agent.executors.headless_codex import HeadlessCodexExecutor

    run = _cli_run(tmp_path, monkeypatch, "codex")
    seen = {}
    monkeypatch.setattr(runner, "run_loop", lambda run_id, executor, **kwargs: seen.update(
        kwargs, executor=executor) or {"finished": True, "stop_reason": "FINISHED"})
    assert cli.main(["run", "--run-id", run.run_id, "--executor", "headless",
                     "--codex-bin", "/opt/codex", "--max-parallel", "2"]) == 0
    assert isinstance(seen["executor"], HeadlessCodexExecutor)
    assert seen["executor"].codex_bin == "/opt/codex" and seen["executor"].name == "headless"
    assert seen["timeouts"] == dict(hc.HEADLESS_TIMEOUTS) and seen["max_parallel"] == 2


# ── child environment: no auth routing, no project secrets (review I2) ─────────

_ROUTING_AND_SECRETS = {
    "ANTHROPIC_API_KEY": "sk-ant-api-SECRET-VALUE-1",
    "ANTHROPIC_AUTH_TOKEN": "SECRET-VALUE-2",
    "ANTHROPIC_BASE_URL": "https://api.deepseek.example/anthropic",
    "ANTHROPIC_MODEL": "deepseek-chat-model-x",
    "ANTHROPIC_DEFAULT_OPUS_MODEL": "deepseek-reasoner-model-x",
    "ANTHROPIC_DEFAULT_SONNET_MODEL": "deepseek-chat-model-y",
    "ANTHROPIC_SMALL_FAST_MODEL": "deepseek-chat-model-z",
    "CLAUDE_CODE_SUBAGENT_MODEL": "deepseek-subagent-model",
    "CLAUDE_CODE_EFFORT_LEVEL": "low-effort-override",
    "CLAUDE_CODE_USE_BEDROCK": "bedrock-on",
    "CLAUDE_CODE_USE_VERTEX": "vertex-on",
    "BARK_TOKEN": "SECRET-VALUE-3",
    "DELIVERY_MAIL_TO": "someone@example.invalid",
    "TUSHARE_TOKEN": "SECRET-VALUE-4",
    "FRED_API_KEY": "SECRET-VALUE-5",
    "OPENAI_API_KEY": "SECRET-VALUE-6",
    "GITHUB_TOKEN": "SECRET-VALUE-7",
}


def test_child_env_drops_auth_routing_and_secrets_and_records_only_names(tmp_path, monkeypatch):
    """`claude -p` must bill the subscription login: an API key / base URL / model override
    inherited from `.env` or a `cc-ds` shell would silently move the nightly scan."""
    for key, value in _ROUTING_AND_SECRETS.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "subscription-oauth-value")
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    body = f"""\
        env > "{tmp_path / 'child_env.txt'}"
        """ + _success_body(tmp_path)
    ex = _executor(tmp_path, _fake_claude(tmp_path, body))
    assert ex.dispatch(_request(tmp_path)).ok is True
    seen = dict(line.split("=", 1) for line in
                (tmp_path / "child_env.txt").read_text(encoding="utf-8").splitlines()
                if "=" in line)
    leaked = sorted(set(_ROUTING_AND_SECRETS) & set(seen))
    assert leaked == [], f"leaked into claude -p: {leaked}"
    for kept in ("HOME", "PATH", "LANG", "CLAUDE_CODE_OAUTH_TOKEN"):
        assert kept in seen, kept
    rec = _record(tmp_path)
    assert set(_ROUTING_AND_SECRETS) <= set(rec["env_stripped"])
    text = json.dumps(rec, ensure_ascii=False)
    for value in (*_ROUTING_AND_SECRETS.values(), "subscription-oauth-value"):
        assert value not in text, "a stripped value reached the call record"


def _records(tmp_path: Path) -> list[dict]:
    folder = tmp_path / "staging" / "_dispatch" / "headless"
    return [json.loads(path.read_text(encoding="utf-8")) for path in sorted(folder.glob("*.json"))]


def _attempt(request: DispatchRequest, attempt: int) -> DispatchRequest:
    return DispatchRequest.from_json({**request.to_json(), "attempt": attempt})


def _live_group(tmp_path: Path, *, ignore_term: bool = False):
    import subprocess

    script = ("trap '' TERM; " if ignore_term else "") + "sleep 30"
    return subprocess.Popen(["/bin/sh", "-c", script], start_new_session=True)


# ── re-dispatch of an attempt number: never overwrite, stop the orphan (review M9) ──

def test_redispatch_of_the_same_attempt_never_overwrites_the_earlier_record(tmp_path):
    ex = _executor(tmp_path, _fake_claude(tmp_path, _success_body(tmp_path)))
    first = ex.dispatch(_request(tmp_path))
    second = ex.dispatch(_request(tmp_path))          # a restarted runner, same attempt
    assert first.ok and second.ok and first.session_ref != second.session_ref
    sessions = sorted(rec["session_id"] for rec in _records(tmp_path))
    assert sessions == sorted([first.session_ref, second.session_ref])
    assert _record(tmp_path)["session_id"] == first.session_ref       # original untouched


def test_prelaunch_stops_a_still_running_session_of_the_same_task(tmp_path):
    from autoresearch.trace import process_probe

    orphan = _live_group(tmp_path)
    folder = tmp_path / "staging" / "_dispatch" / "headless"
    folder.mkdir(parents=True)
    (folder / f"{TASK}.a1.json").write_text(json.dumps({
        "task_id": TASK, "attempt": 1, "state": "RUNNING", "pid": orphan.pid,
        "process_started_at": process_probe.started_at(orphan.pid)}), encoding="utf-8")
    try:
        ex = _executor(tmp_path, _fake_claude(tmp_path, _success_body(tmp_path)))
        assert ex.dispatch(_attempt(_request(tmp_path), 2)).ok is True
        assert orphan.wait(timeout=10) is not None, "the orphaned session kept running"
        old = json.loads((folder / f"{TASK}.a1.json").read_text(encoding="utf-8"))
        # The record precedes this parent's wait/reap. Group absence may not yet
        # have been observable when cancellation was recorded.
        assert old["cancel_requested"] is True and old["superseded_by"]
        assert old["cancel_capability"] == "PROCESS_GROUP"
        expected = "KILLED" if old["cancel_confirmed"] is True else "CANCEL_UNCONFIRMED"
        assert old["state"] == expected
    finally:
        if orphan.poll() is None:
            orphan.kill()
            orphan.wait()


def test_prelaunch_leaves_other_tasks_and_pid_twins_alone(tmp_path):
    other, twin = _live_group(tmp_path), _live_group(tmp_path)
    folder = tmp_path / "staging" / "_dispatch" / "headless"
    folder.mkdir(parents=True)
    (folder / "scan.l4.card.600001.a1.json").write_text(json.dumps({
        "task_id": "scan.l4.card.600001", "state": "RUNNING", "pid": other.pid}),
        encoding="utf-8")
    (folder / f"{TASK}.a1.json").write_text(json.dumps({   # pid reused by an unrelated process
        "task_id": TASK, "state": "RUNNING", "pid": twin.pid,
        "process_started_at": "Thu Jan  1 00:00:00 1970"}), encoding="utf-8")
    try:
        ex = _executor(tmp_path, _fake_claude(tmp_path, _success_body(tmp_path)))
        assert ex.dispatch(_attempt(_request(tmp_path), 2)).ok is True
        assert other.poll() is None and twin.poll() is None
    finally:
        for proc in (other, twin):
            proc.kill()
            proc.wait()


# ── retries never accept a stale output (review M5) ─────────────────────────────

def test_retry_moves_a_stale_output_aside_so_exit_zero_without_writing_fails(tmp_path):
    card = tmp_path / "staging" / "details" / "600000.md"
    card.parent.mkdir(parents=True)
    card.write_text("written by attempt 1, which then timed out", encoding="utf-8")
    body = """\
        echo "{\\"type\\":\\"result\\",\\"is_error\\":false,\\"session_id\\":\\"$sid\\",\\"usage\\":{},\\"result\\":\\"done\\"}"
        """
    ex = _executor(tmp_path, _fake_claude(tmp_path, body))
    result = ex.dispatch(_attempt(_request(tmp_path), 2))
    assert result.ok is False and result.error_class == "CONTRACT_ERROR"
    assert not card.exists()
    [moved] = [rec for rec in _records(tmp_path) if rec.get("attempt") == 2][0]["outputs_moved_aside"]
    assert moved["from"] == str(card)
    stale = Path(moved["to"])
    assert stale.read_text(encoding="utf-8").startswith("written by attempt 1")
    assert stale.name.endswith(".stale") and ".a1." in stale.name
    assert stale.parent == tmp_path / "staging" / "_dispatch" / "headless" / "stale"


def test_retry_output_written_fresh_is_accepted(tmp_path):
    card = tmp_path / "staging" / "details" / "600000.md"
    card.parent.mkdir(parents=True)
    card.write_text("stale", encoding="utf-8")
    ex = _executor(tmp_path, _fake_claude(tmp_path, _success_body(tmp_path)))
    assert ex.dispatch(_attempt(_request(tmp_path), 2)).ok is True
    assert card.read_text(encoding="utf-8") == "card"


def test_first_attempt_leaves_a_preexisting_output_in_place(tmp_path):
    card = tmp_path / "staging" / "details" / "600000.md"
    card.parent.mkdir(parents=True)
    card.write_text("pre", encoding="utf-8")
    ex = _executor(tmp_path, _fake_claude(tmp_path, _success_body(tmp_path)))
    assert ex.dispatch(_request(tmp_path)).ok is True
    assert _record(tmp_path)["outputs_moved_aside"] == []


# ── a clean exit still sweeps the process group (review M1a) ────────────────────

def test_normal_exit_sweeps_leftover_processes_in_the_group(tmp_path):
    pidfile = tmp_path / "leftover.pid"
    body = f"""\
        sleep 30 &
        echo $! > "{pidfile}"
        """ + _success_body(tmp_path)
    ex = _executor(tmp_path, _fake_claude(tmp_path, body), kill_grace_seconds=0.5)
    assert ex.dispatch(_request(tmp_path)).ok is True
    leftover = int(pidfile.read_text(encoding="utf-8").strip())
    assert _dead(leftover, time.monotonic() + 5), "a grandchild outlived its claude -p"


# ── transient API errors are retried once (review M4) ────────────────────────────

@pytest.mark.parametrize("text", [
    'API Error: 529 {"type":"error","error":{"type":"overloaded_error","message":"Overloaded"}}',
    "API Error: 500 Internal server error",
    "API Error: 503 Service Unavailable",
])
def test_transient_api_errors_are_classified_as_connection(tmp_path, text):
    payload = json.dumps({"type": "result", "is_error": True, "subtype": "success",
                          "result": text}).replace('"', '\\"')
    body = f"""\
        echo "{payload}"
        exit 1
        """
    ex = _executor(tmp_path, _fake_claude(tmp_path, body))
    result = ex.dispatch(_request(tmp_path))
    assert result.ok is False
    assert classify_error(result.error, result.error_class) == "CONNECTION"


# ── small items (review M11) ─────────────────────────────────────────────────────

def test_result_parsing_prefers_the_result_object_over_trailing_json(tmp_path):
    doc = hc._parse_result('{"type":"result","is_error":false,"session_id":"s"}\n'
                           '{"type":"system","subtype":"hook"}\n')
    assert doc["type"] == "result"
    assert hc._parse_result('{"is_error":false}')["is_error"] is False   # untyped fallback


def test_call_record_names_the_resolved_cli_binary(tmp_path):
    real = _fake_claude(tmp_path, _success_body(tmp_path))
    link = tmp_path / "claude-link"
    link.symlink_to(real)
    ex = _executor(tmp_path, str(link))
    assert ex.dispatch(_request(tmp_path)).ok is True
    assert _record(tmp_path)["claude_bin_resolved"] == str(Path(real).resolve())


def test_child_env_keeps_the_login_basics():
    env, stripped = hc.child_env({"HOME": "/h", "PATH": "/p", "USER": "u", "TMPDIR": "/t",
                                  "LANG": "C", "CLAUDE_CONFIG_DIR": "/c",
                                  "AUTORESEARCH_ENGINE": "claude", "ANTHROPIC_API_KEY": "x"})
    assert env == {"HOME": "/h", "PATH": "/p", "USER": "u", "TMPDIR": "/t", "LANG": "C",
                   "CLAUDE_CONFIG_DIR": "/c", "AUTORESEARCH_ENGINE": "claude"}
    assert stripped == ["ANTHROPIC_API_KEY"]


@pytest.mark.parametrize("error_stage", ["term", "term_probe", "kill", "kill_probe"])
def test_group_cancellation_retries_until_absence_is_observed(tmp_path, monkeypatch, error_stage):
    ex = _executor(tmp_path, "unused", kill_grace_seconds=0.2)
    calls = []
    clock = [0.0]
    probe_count = [0]

    class Leader:
        pid = 12345

        def wait(self, timeout):
            return -15

        def poll(self):
            return -15

    def killpg(pgid, sig):
        assert pgid == Leader.pid
        calls.append(sig)
        if (error_stage == "term" and sig == hc.signal.SIGTERM
                or error_stage == "kill" and sig == hc.signal.SIGKILL):
            raise PermissionError(1, "Operation not permitted")
        if sig == 0:
            probe_count[0] += 1
            if probe_count[0] >= 3:
                raise ProcessLookupError(3, "No such process")
            if (error_stage == "term_probe" and probe_count[0] == 1
                    or error_stage == "kill_probe" and probe_count[0] == 2):
                raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(hc.os, "killpg", killpg)
    monkeypatch.setattr(hc.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(hc.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    assert ex._kill_group(Leader()) is True
    assert hc.signal.SIGKILL in calls
    assert probe_count[0] >= 3


@pytest.mark.parametrize("observable", [True, False])
def test_group_cancellation_stays_unconfirmed_without_observed_absence(tmp_path, monkeypatch, observable):
    ex = _executor(tmp_path, "unused", kill_grace_seconds=0.2)
    calls = []
    clock = [0.0]

    class Leader:
        pid = 12345

        def wait(self, timeout):
            return -15

    def killpg(pgid, sig):
        calls.append(sig)
        if not observable:
            raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(hc.os, "killpg", killpg)
    monkeypatch.setattr(hc.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(hc.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    assert ex._kill_group(Leader()) is (False if observable else None)
    assert hc.signal.SIGKILL in calls
    assert 5 <= clock[0] <= 5.2


def test_auto_memory_is_off_unless_the_session_config_turns_it_on(tmp_path, monkeypatch):
    from autoresearch.session_agent import config as session_config
    real = session_config.session_cfg()
    monkeypatch.setattr(session_config, "session_cfg",
                        lambda cfg=None: {**real, "context": {**real["context"], "claude_auto_memory": True}})
    ex = _executor(tmp_path, _fake_claude(tmp_path, _success_body(tmp_path)))
    ex.dispatch(_request(tmp_path))
    argv = (tmp_path / "bin" / "argv.txt").read_text(encoding="utf-8").splitlines()
    assert "--settings" not in argv
    assert hc.AUTO_MEMORY is False
