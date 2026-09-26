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


def test_timeout_kills_the_whole_process_group(tmp_path):
    pidfile = tmp_path / "grandchild.pid"
    body = f"""\
        sleep 30 &
        echo $! > "{pidfile}"
        wait
        """
    ex = _executor(tmp_path, _fake_claude(tmp_path, body), kill_grace_seconds=0.2)
    started = time.monotonic()
    with pytest.raises(ExecutorTimeout, match="超时"):
        ex.dispatch(_request(tmp_path, timeout=0.8))
    assert time.monotonic() - started < 8
    grandchild = int(pidfile.read_text(encoding="utf-8").strip())
    assert _dead(grandchild, time.monotonic() + 5), "grandchild `sleep` survived the timeout"
    rec = _record(tmp_path)
    assert rec["timed_out"] is True and rec["state"] == "KILLED"


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
    out = run.output_path("synthetic.review.out")
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
    assert (calls.root, calls.path, calls.kind) == ("staging", "_dispatch/headless/*.json", "json")
    assert (streams.root, streams.path) == ("staging", "_dispatch/headless/*.std*")
    assert calls.presence == streams.presence == "conditional"


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


def test_cli_run_headless_refuses_a_codex_run(tmp_path, monkeypatch, capsys):
    from autoresearch.session_agent import __main__ as cli, runner

    run = _cli_run(tmp_path, monkeypatch, "codex")
    monkeypatch.setattr(runner, "run_loop", lambda *a, **k: pytest.fail("must not run"))
    assert cli.main(["run", "--run-id", run.run_id, "--executor", "headless"]) == 2
    assert "claude" in json.loads(capsys.readouterr().out)["errors"][0]["message"]
