"""Headless Codex executor: one ``codex exec`` thread per inference attempt.

Every test drives a FAKE ``codex`` shell script written into ``tmp_path`` — the real CLI,
the subscription and the network are never touched.  Real C4 manifest / binding behaviour
is covered by test_task_access.py; here the binding is a recorded stand-in.
"""
from __future__ import annotations

import json
import os
import stat
import textwrap
import time
from pathlib import Path

import pytest

from autoresearch.session_agent.executors import headless_codex as hx
from autoresearch.session_agent.executors.base import (
    DispatchRequest,
    ExecutorTimeout,
    classify_error,
)

TASK = "scan.l4.card.600000"
THREAD = "019a2c1e-7b6a-7c3b-9f1d-5c2f3a4b6d7e"

#: Fake codex prelude: mode = open (no ``resume``) / work (``resume`` present); log argv per mode.
_PRELUDE = textwrap.dedent("""\
    #!/bin/sh
    mode=open
    for a in "$@"; do [ "$a" = "resume" ] && mode=work; done
    : > "$(dirname "$0")/argv_$mode.txt"
    for a in "$@"; do printf '%s\\n' "$a" >> "$(dirname "$0")/argv_$mode.txt"; done
    if [ "$mode" = "open" ]; then
      if [ -n "$FAKE_OPEN_BODY" ]; then eval "$FAKE_OPEN_BODY"; fi
      printf '{"type":"thread.started","thread_id":"%s"}\\n' "$FAKE_THREAD"
      echo '{"type":"turn.completed","usage":{"input_tokens":12,"output_tokens":1}}'
      exit 0
    fi
    """)

_WORK_OK = """\
    [ -f "$FAKE_MARKER" ] || { echo "UNBOUND: work started before the binding existed" >&2; exit 3; }
    mkdir -p "$(dirname "$FAKE_OUT")"; printf 'card' > "$FAKE_OUT"
    mkdir -p "$FAKE_SESSIONS/2026/10/08"
    printf '{"type":"session_meta","payload":{"id":"%s"}}\\n' "$FAKE_THREAD" > "$FAKE_SESSIONS/2026/10/08/rollout-2026-10-08T10-00-00-$FAKE_THREAD.jsonl"
    echo '{"type":"item.completed","item":{"type":"agent_message","text":"done"}}'
    echo '{"type":"turn.completed","usage":{"input_tokens":100,"cached_input_tokens":40,"output_tokens":9,"reasoning_output_tokens":4}}'
    """


def _fake_codex(tmp_path: Path, work_body: str = _WORK_OK) -> str:
    path = tmp_path / "bin" / "codex"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_PRELUDE + textwrap.dedent(work_body), encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def _request(tmp_path: Path, *, timeout=10.0, role="scan.l4.card", model="gpt-5.6-sol",
             effort="xhigh", config_role="l4_card", agent_spec=None, engine="codex",
             independent=False) -> DispatchRequest:
    return DispatchRequest(
        run_id="20261008T010203000000Z", engine=engine, task_id=TASK, attempt=1,
        role=role, agent_type="L4 card", config_role=config_role, model=model,
        effort=effort, agent_spec=agent_spec if agent_spec is not None else {"model": model, "reasoning_effort": effort},
        tier="critical", max_turns=None,
        prompt="执行 details/_l4_prompt_600000.md 的任务包,写决策卡",
        instruction_refs=(".claude/agents/l4-card.md",),
        input_paths={"p": str(tmp_path / "_l4_prompt_600000.md")},
        output_paths={"card": str(tmp_path / "staging" / "details" / "600000.md")},
        subject="600000", independent_context=independent, tool_policy="READ_WRITE_WEB",
        timeout_seconds=timeout, host_session_ref="headless-host",
    )


@pytest.fixture
def fake_env(tmp_path, monkeypatch):
    out = tmp_path / "staging" / "details" / "600000.md"
    marker = tmp_path / "bound.marker"
    monkeypatch.setenv("FAKE_THREAD", THREAD)
    monkeypatch.setenv("FAKE_OUT", str(out))
    monkeypatch.setenv("FAKE_MARKER", str(marker))
    monkeypatch.setenv("FAKE_SESSIONS", str(tmp_path / "sessions"))
    bound: list[str] = []

    def fake_bind(request, session_id, *, repo_root):
        bound.append(session_id)
        marker.write_text(session_id, encoding="utf-8")
        return {"enforcement": "SIMULATED",
                "read_commands": {"p": f"/venv/python -I -S broker.py '{{\"session_id\":\"{session_id}\"}}'"},
                "write_commands": {"card": "/venv/python -I -S broker.py '{...}'"}}

    monkeypatch.setattr(hx, "bind_task_access", fake_bind)
    return {"out": out, "marker": marker, "bound": bound}


def _agents_dir(tmp_path: Path) -> Path:
    folder = tmp_path / ".codex" / "agents"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "l4_card.toml").write_text(
        'name = "L4 card"\nmodel = "gpt-5.6-sol"\nmodel_reasoning_effort = "xhigh"\n'
        'developer_instructions = """C4:只读冻结片段;卡片契约只读 `.claude/agents/l4-card.md`。\n第二行 "引号" 与 \\\\ 反斜杠。"""\n',
        encoding="utf-8")
    return folder


def _executor(tmp_path: Path, codex_bin: str, **kwargs) -> hx.HeadlessCodexExecutor:
    return hx.HeadlessCodexExecutor(
        tmp_path / "staging", codex_bin=codex_bin, cwd=tmp_path,
        sessions_root=tmp_path / "sessions", agents_dir=_agents_dir(tmp_path), **kwargs)


def _argv(tmp_path: Path, mode: str) -> list[str]:
    return (tmp_path / "bin" / f"argv_{mode}.txt").read_text(encoding="utf-8").splitlines()


def _record(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "staging" / "_dispatch" / "headless" / f"{TASK}.a1.json").read_text(encoding="utf-8"))


def _override(argv: list[str], key: str) -> str:
    values = [argv[i + 1] for i, item in enumerate(argv) if item == "-c" and argv[i + 1].startswith(key + "=")]
    assert len(values) == 1, (key, argv)
    return json.loads(values[0][len(key) + 1:])      # TOML basic string == JSON string here


def test_dispatch_opens_binds_then_works_and_returns_thread_identity(tmp_path, fake_env):
    ex = _executor(tmp_path, _fake_codex(tmp_path))
    result = ex.dispatch(_request(tmp_path))
    assert result.ok is True, result.error
    assert result.session_ref == THREAD and result.context_ref == THREAD
    assert result.parent_context_ref == "headless-host"
    assert fake_env["bound"] == [THREAD]                       # bound exactly once, with the thread id
    assert result.usage == {"input_tokens": 100, "cached_input_tokens": 40, "output_tokens": 9,
                            "reasoning_output_tokens": 4}
    assert result.transcript_path == str(tmp_path / "sessions" / "2026" / "10" / "08"
                                         / f"rollout-2026-10-08T10-00-00-{THREAD}.jsonl")
    assert result.evidence_refs == ()                           # the runner binds the transcript itself
    assert fake_env["out"].read_text(encoding="utf-8") == "card"


def test_open_turn_carries_the_role_tier_and_instructions_without_tools_and_never_ephemeral(tmp_path, fake_env):
    # 2026-10-08 probes: `resume -c developer_instructions=` is dropped, and changing the model
    # between turns injects Codex's whole base prompt — so the open turn carries both verbatim.
    ex = _executor(tmp_path, _fake_codex(tmp_path))
    ex.dispatch(_request(tmp_path))
    argv = _argv(tmp_path, "open")
    assert argv[:2] == ["exec", "--json"]
    assert argv[argv.index("-C") + 1] == str(tmp_path)
    assert argv[argv.index("--sandbox") + 1] == "read-only"
    assert _override(argv, "model") == "gpt-5.6-sol"
    assert _override(argv, "model_reasoning_effort") == "xhigh"
    assert _override(argv, "approval_policy") == "never"
    instructions = _override(argv, "developer_instructions")
    assert instructions.startswith("C4:只读冻结片段") and '"引号"' in instructions and "\\ 反斜杠" in instructions
    assert argv[-1] == hx.OPEN_PROMPT
    assert "--ephemeral" not in argv and "resume" not in argv


def test_both_turns_declare_the_same_tier_so_codex_never_injects_model_switch(tmp_path):
    ex = _executor(tmp_path, _fake_codex(tmp_path))
    keys = ("model", "model_reasoning_effort")
    request = _request(tmp_path)
    opened = ex.argv_open(request)
    worked = ex.argv_work(request, THREAD, "prompt", tmp_path / "last.md")
    assert [_override(opened, key) for key in keys] == [_override(worked, key) for key in keys]

    # A request that declares no tier declares none on either turn (both fall through to the config).
    bare = _request(tmp_path, model=None, effort=None, agent_spec={})
    argv = ex.argv_open(bare) + ex.argv_work(bare, THREAD, "prompt", tmp_path / "last.md")
    assert not any(item.startswith(f"{key}=") for key in keys for item in argv)


def test_work_turn_resumes_the_bound_thread_with_the_role_tier_and_the_frozen_prompt(tmp_path, fake_env):
    ex = _executor(tmp_path, _fake_codex(tmp_path))
    ex.dispatch(_request(tmp_path))
    argv = _argv(tmp_path, "work")
    assert argv[:2] == ["exec", "--json"]
    assert argv[argv.index("--sandbox") + 1] == "workspace-write"
    assert argv[argv.index("-o") + 1].endswith(f"{TASK}.a1.last_message.md")
    assert argv.index(THREAD) > argv.index("resume")         # … resume [-c …]* <thread_id> <prompt>
    assert _override(argv, "model") == "gpt-5.6-sol"
    assert _override(argv, "model_reasoning_effort") == "xhigh"
    assert _override(argv, "approval_policy") == "never"
    # `resume -c developer_instructions=` is silently dropped by codex 0.160.1 — it rides the open turn.
    assert not any(item.startswith("developer_instructions=") for item in argv)
    prompt = "\n".join(argv[argv.index(THREAD) + 1:])          # the prompt spans several logged lines
    assert prompt.startswith("执行 details/_l4_prompt_600000.md 的任务包")
    assert "read_commands" in prompt and THREAD in prompt      # broker commands carry the bound identity
    assert "--ephemeral" not in argv and "--dangerously-bypass-approvals-and-sandbox" not in argv
    assert not any(item.startswith("web_search=") for item in argv)   # card role: config default


def test_intel_role_passes_live_web_search_through(tmp_path, fake_env):
    ex = _executor(tmp_path, _fake_codex(tmp_path))
    request = _request(tmp_path, role="scan.l4.intel", config_role="l4_intel",
                       agent_spec={"model": "gpt-5.6-sol", "reasoning_effort": "xhigh", "web_search": "live"})
    ex.dispatch(request)
    argv = _argv(tmp_path, "work")
    assert _override(argv, "web_search") == "live"
    # no l4_intel.toml in the fixture → no instructions on either turn
    assert not any(item.startswith("developer_instructions=") for item in argv + _argv(tmp_path, "open"))
    assert not any(item.startswith("web_search=") for item in _argv(tmp_path, "open"))   # tools only on work


def test_work_before_binding_is_impossible_by_construction(tmp_path, fake_env, monkeypatch):
    # The fake refuses to work unless the binding marker exists: with the real order
    # (open → bind → work) it succeeds; a binding that never happens makes the work turn fail.
    monkeypatch.setattr(hx, "bind_task_access", lambda request, session_id, *, repo_root: {"enforcement": "SIMULATED"})
    ex = _executor(tmp_path, _fake_codex(tmp_path))
    result = ex.dispatch(_request(tmp_path))
    assert result.ok is False and "UNBOUND" in result.error and "exit=3" in result.error


def test_binding_failure_stops_before_any_work_turn(tmp_path, fake_env, monkeypatch):
    def refuse(request, session_id, *, repo_root):
        raise ValueError("legacy frozen request lacks C4 access manifest; start a new run")

    monkeypatch.setattr(hx, "bind_task_access", refuse)
    ex = _executor(tmp_path, _fake_codex(tmp_path))
    result = ex.dispatch(_request(tmp_path))
    assert result.ok is False and result.error_class == "CONTRACT_ERROR"
    assert "C4 access binding unavailable" in result.error and result.session_ref == THREAD
    assert not (tmp_path / "bin" / "argv_work.txt").exists()
    assert _record(tmp_path)["state"] == "BIND_FAILED"


def test_open_without_a_thread_id_fails_without_binding(tmp_path, fake_env, monkeypatch):
    monkeypatch.setenv("FAKE_OPEN_BODY", 'echo "{\\"type\\":\\"turn.completed\\",\\"usage\\":{}}"; exit 0')
    ex = _executor(tmp_path, _fake_codex(tmp_path))
    result = ex.dispatch(_request(tmp_path))
    assert result.ok is False and "thread.started" in result.error
    assert fake_env["bound"] == [] and _record(tmp_path)["state"] == "OPEN_FAILED"


def test_quota_during_open_preserves_observed_thread_and_rollout(tmp_path, fake_env, monkeypatch):
    monkeypatch.setenv("FAKE_OPEN_BODY", '''
        mkdir -p "$FAKE_SESSIONS/2026/10/08"
        printf '{"type":"session_meta","payload":{"id":"%s"}}\\n' "$FAKE_THREAD" > "$FAKE_SESSIONS/2026/10/08/rollout-2026-10-08T10-00-00-$FAKE_THREAD.jsonl"
        printf '{"type":"thread.started","thread_id":"%s"}\\n' "$FAKE_THREAD"
        echo '{"type":"turn.failed","error":{"message":"usage limit reached"}}'
        exit 1
    ''')
    result = _executor(tmp_path, _fake_codex(tmp_path)).dispatch(_request(tmp_path))
    assert not result.ok and result.error_class == "USAGE_LIMIT"
    assert result.context_ref == THREAD and Path(result.transcript_path).is_file()
    assert _record(tmp_path)["thread_id"] == THREAD
    assert fake_env["bound"] == []


def test_open_exit_nonzero_reports_stderr_and_is_classified(tmp_path, fake_env, monkeypatch):
    monkeypatch.setenv("FAKE_OPEN_BODY", 'echo "stream error: 503 service unavailable" >&2; exit 1')
    ex = _executor(tmp_path, _fake_codex(tmp_path))
    result = ex.dispatch(_request(tmp_path))
    assert result.ok is False and "exit=1" in result.error
    assert classify_error(result.error, result.error_class) == "CONNECTION"   # transient → one retry


def test_turn_failed_event_is_a_failure_even_with_exit_zero(tmp_path, fake_env):
    body = """\
        mkdir -p "$(dirname "$FAKE_OUT")"; printf 'card' > "$FAKE_OUT"
        echo '{"type":"turn.failed","error":{"message":"usage limit reached, retry after 2026-10-08T15:13Z"}}'
        """
    ex = _executor(tmp_path, _fake_codex(tmp_path, body))
    result = ex.dispatch(_request(tmp_path))
    assert result.ok is False and "turn.failed" in result.error and "usage limit" in result.error


def test_output_missing_after_exit_zero_is_a_contract_error(tmp_path, fake_env):
    body = """\
        echo '{"type":"turn.completed","usage":{"input_tokens":1,"output_tokens":1}}'
        """
    ex = _executor(tmp_path, _fake_codex(tmp_path, body))
    result = ex.dispatch(_request(tmp_path))
    assert result.ok is False and result.error_class == "CONTRACT_ERROR"
    assert "产物未落盘" in result.error and "600000.md" in result.error
    assert _record(tmp_path)["outputs_missing"] == [str(fake_env["out"])]


def test_nonzero_work_exit_reports_the_event_stream_detail(tmp_path, fake_env):
    body = """\
        echo '{"type":"error","message":"connect ECONNRESET api.openai.com:443"}'
        exit 1
        """
    ex = _executor(tmp_path, _fake_codex(tmp_path, body))
    result = ex.dispatch(_request(tmp_path))
    assert result.ok is False and "exit=1" in result.error and "ECONNRESET" in result.error
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


def test_timeout_kills_the_work_process_group_and_raises(tmp_path, fake_env):
    pidfile = tmp_path / "grandchild.pid"
    body = f"""\
        sleep 30 &
        echo $! > "{pidfile}"
        wait
        """
    ex = _executor(tmp_path, _fake_codex(tmp_path, body), kill_grace_seconds=0.2)
    with pytest.raises(ExecutorTimeout, match="codex exec 超时"):
        ex.dispatch(_request(tmp_path, timeout=0.8))
    rec = _record(tmp_path)
    assert rec["timed_out"] is True and rec["state"] == "KILLED" and rec["thread_id"] == THREAD
    assert _dead(int(pidfile.read_text().strip()), time.monotonic() + 5)


def test_record_is_redacted_and_names_the_engine_thread_and_open_turn(tmp_path, fake_env, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-SECRET-VALUE-NEVER-LOGGED")
    ex = _executor(tmp_path, _fake_codex(tmp_path))
    result = ex.dispatch(_request(tmp_path))
    rec = _record(tmp_path)
    assert rec["engine"] == "codex" and rec["transport"] == "codex exec"
    assert rec["thread_id"] == THREAD == rec["session_id"] and rec["state"] == "EXITED"
    assert rec["open"]["exit_code"] == 0 and rec["open"]["usage"] == {"input_tokens": 12, "output_tokens": 1}
    assert rec["open"]["argv"][-1] == hx.OPEN_PROMPT
    assert rec["argv"][-1].startswith("<prompt ") and not any("任务包" in item for item in rec["argv"])
    assert rec["usage"] == result.usage and rec["transcript_path"] == result.transcript_path
    assert rec["task_id"] == TASK and rec["attempt"] == 1 and rec["role"] == "scan.l4.card"
    assert "OPENAI_API_KEY" in rec["env_stripped"]             # dropped NAMES are recorded …
    assert "sk-SECRET-VALUE-NEVER-LOGGED" not in json.dumps(rec)   # … values never are


def test_engine_guard_refuses_a_claude_request(tmp_path, fake_env):
    ex = _executor(tmp_path, _fake_codex(tmp_path))
    result = ex.dispatch(_request(tmp_path, engine="claude"))
    assert result.ok is False and "codex exec" in result.error and fake_env["bound"] == []


def test_child_env_drops_every_billing_switch_but_keeps_the_codex_home():
    parent = {"PATH": "/usr/bin", "CODEX_HOME": "/Users/me/.codex", "OPENAI_API_KEY": "sk-x",
              "OPENAI_BASE_URL": "https://proxy", "CODEX_API_KEY": "ck-x", "ANTHROPIC_API_KEY": "a",
              "TUSHARE_TOKEN": "t", "CLAUDECODE": "1", "FAKE_THREAD": "ok"}
    env, dropped = hx.codex_child_env(parent)
    assert env == {"PATH": "/usr/bin", "CODEX_HOME": "/Users/me/.codex", "FAKE_THREAD": "ok"}
    assert dropped == ["ANTHROPIC_API_KEY", "CLAUDECODE", "CODEX_API_KEY", "OPENAI_API_KEY",
                       "OPENAI_BASE_URL", "TUSHARE_TOKEN"]


def test_parse_events_sums_usage_and_collects_failures():
    text = "\n".join([
        "codex v0.160 progress line (not json)",
        '{"type":"thread.started","thread_id":"t-1"}',
        '{"type":"turn.completed","usage":{"input_tokens":10,"cached_input_tokens":4,"output_tokens":2}}',
        '{"type":"turn.completed","usage":{"input_tokens":5,"output_tokens":1,"reasoning_output_tokens":1}}',
        '{"type":"turn.failed","error":{"message":"boom"}}',
        '{"type":"error","message":"stream closed"}',
        "{not json",
    ])
    events = hx.parse_events(text)
    assert events["thread_id"] == "t-1"
    assert events["usage"] == {"input_tokens": 15, "cached_input_tokens": 4, "output_tokens": 3,
                               "reasoning_output_tokens": 1}
    assert events["failed"] == ['{"message": "boom"}'] and events["errors"] == ["stream closed"]
    assert hx.parse_events("") == {"thread_id": None, "usage": None, "failed": [], "errors": []}


def test_toml_value_round_trips_through_json_escapes():
    text = 'line1\n"quoted" \\ 反斜杠 \t tab'
    encoded = hx.toml_value(text)
    assert encoded.startswith('"') and json.loads(encoded) == text and "\n" not in encoded


def test_find_rollout_prefers_the_newest_file_of_the_thread(tmp_path):
    root = tmp_path / "sessions"
    old = root / "2026" / "10" / "07" / f"rollout-2026-10-07T23-00-00-{THREAD}.jsonl"
    new = root / "2026" / "10" / "08" / f"rollout-2026-10-08T00-30-00-{THREAD}.jsonl"
    for path in (old, new):
        path.parent.mkdir(parents=True)
        path.write_text("{}\n", encoding="utf-8")
    os.utime(old, (1, 1))
    assert hx.find_rollout(THREAD, root) == str(new)
    assert hx.find_rollout("missing", root) is None and hx.find_rollout("", root) is None


def test_restart_keys_the_second_record_by_call_id_and_moves_the_stale_output_aside(tmp_path, fake_env):
    ex = _executor(tmp_path, _fake_codex(tmp_path))
    assert ex.dispatch(_request(tmp_path)).ok is True
    first = _record(tmp_path)
    assert ex.dispatch(_request(tmp_path)).ok is True        # a restarted runner re-dispatching a1
    records = sorted((tmp_path / "staging" / "_dispatch" / "headless").glob(f"{TASK}.a1*.json"))
    assert len(records) == 2 and _record(tmp_path) == first   # the earlier record is never overwritten
    second = json.loads(records[-1].read_text(encoding="utf-8")) if records[-1].name != f"{TASK}.a1.json" else json.loads(records[0].read_text(encoding="utf-8"))
    assert second["outputs_moved_aside"] and second["outputs_moved_aside"][0]["to"].endswith(".stale")


def test_open_timeout_defaults_to_the_session_config_knob(tmp_path, monkeypatch):
    from autoresearch.session_agent import config as session_config

    monkeypatch.setattr(session_config, "session_cfg", lambda cfg=None: {"timeouts": {"codex_open_s": 42.0}})
    ex = hx.HeadlessCodexExecutor(tmp_path / "staging", codex_bin="/opt/codex", cwd=tmp_path)
    assert ex.open_timeout_seconds == 42.0
    ex = hx.HeadlessCodexExecutor(tmp_path / "staging", codex_bin="/opt/codex", cwd=tmp_path,
                                  open_timeout_seconds=7)
    assert ex.open_timeout_seconds == 7.0


def test_cli_build_executor_picks_the_codex_transport_for_a_codex_run(tmp_path):
    from argparse import Namespace
    from types import SimpleNamespace

    from autoresearch.session_agent.mailbox_cli import _build_executor

    handle = SimpleNamespace(engine="codex", staging=tmp_path / "staging", run_id="R",
                             contract=SimpleNamespace(user_config={}))
    executor, timeouts = _build_executor(Namespace(executor="headless", run_id="R", codex_bin="/opt/codex",
                                                   claude_bin=None), handle)
    assert isinstance(executor, hx.HeadlessCodexExecutor)
    assert executor.codex_bin == "/opt/codex"
    assert executor.open_timeout_seconds == 180.0 and timeouts == dict(hx.HEADLESS_CODEX_TIMEOUTS)
