"""Production workflows must put every deterministic module behind capture."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

WORKFLOWS = (
    Path(".claude/workflows/scan-market.js"),
    Path(".claude/workflows/l4-stock.js"),
)
_NODE = shutil.which("node")

_BOUNDARY_ACK_JS = r"""
const boundaryAck = (prompt) => {
  const eventType = prompt.match(/agent-event \S+ (AGENT_[A-Z]+)/)[1];
  const invocationId = [...prompt.matchAll(/--invocation-id ([^ ]+)/g)].at(-1)[1];
  const controlId = prompt.match(/--control-invocation-id ([^ `]+)/)[1];
  const role = prompt.match(/--role ([^ ]+)/)[1];
  const subject = prompt.match(/--subject ([^ ]+)/)[1];
  const attempt = Number([...prompt.matchAll(/--attempt (\d+)/g)].at(-1)[1]);
  const runId = prompt.match(/agent-event (\S+) AGENT_/)[1];
  const engine = prompt.match(/AUTORESEARCH_ENGINE=([^ ]+)/)[1];
  const event = (seq, type, id, eventRole, eventHash) => ({
    schema_version: 1, seq, run_id: runId,
    ts: '2026-08-27T01:02:03.456789Z', engine, stage: 'l4',
    invocation_id: id, attempt, subject, event_type: type,
    payload: {role: eventRole, result: null, error: null},
    prev_hash: '0'.repeat(64), event_hash: eventHash,
  });
  const target = event(3, eventType, invocationId, role, 'a'.repeat(64));
  const completed = event(4, 'AGENT_COMPLETED', controlId, 'trace-control', 'c'.repeat(64));
  completed.payload.result = {target_event_hash: target.event_hash};
  return {ok: true, event: target, control_events: [
    event(2, 'AGENT_DISPATCHED', controlId, 'trace-control', 'b'.repeat(64)),
    completed,
  ]};
};
"""


def _probe_workflow(path: Path, args: dict) -> dict:
    script = textwrap.dedent(
        """
        const fs = require('fs');
        const AsyncFunction = Object.getPrototypeOf(async function(){}).constructor;
        let src = fs.readFileSync(process.argv[1], 'utf8').replace(/^export const meta/m, 'const meta');
        __BOUNDARY_ACK__
        let first = null;
        const agent = (prompt) => {
          if (/autoresearch\\.trace\\.capsule agent-event/.test(prompt)) return boundaryAck(prompt);
          first = prompt;
          throw new Error('STOP_AT_FIRST_AGENT');
        };
        const fn = new AsyncFunction('agent','parallel','pipeline','log','phase','args','budget','workflow', src);
        fn(agent, null, null, () => {}, () => {}, JSON.parse(process.argv[2]), {total:null}, null)
          .then(() => console.log(JSON.stringify({first, error:null})))
          .catch(e => console.log(JSON.stringify({first, error:e.message})));
        """
    ).replace("__BOUNDARY_ACK__", _BOUNDARY_ACK_JS)
    completed = subprocess.run(
        [_NODE, "-e", script, str(path), json.dumps(args)],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return json.loads(completed.stdout.splitlines()[-1])


def _executable_source(path: Path) -> str:
    return "\n".join(
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("//")
    )


@pytest.mark.parametrize("path", WORKFLOWS)
def test_workflow_requires_run_id_and_uses_run_scoped_staging(path):
    source = _executable_source(path)
    assert "run_id" in source
    assert "AUTORESEARCH_RUN_ID" in source
    assert "args.run_id" in source or "A.run_id" in source
    assert "/scan_runs/${RUN_ID}/staging/${date}" in source
    assert "`context_${ENGINE}/scan/${date}`" not in source
    assert "`${CTX}/scan/${date}`" not in source


@pytest.mark.parametrize("path", WORKFLOWS)
def test_no_deterministic_python_module_command_bypasses_capture(path):
    source = _executable_source(path)
    raw_modules = [
        match.group(0) for match in re.finditer(r"python -m autoresearch\.[A-Za-z0-9_.]+", source)
    ]
    assert raw_modules == ["python -m autoresearch.trace.exec_capture"]
    assert "${R}" not in source


def test_scan_workflow_has_stable_distinct_retry_and_stage_invocation_ids():
    source = _executable_source(WORKFLOWS[0])
    assert "const PYC = (stage, invocation" in source
    assert "PYC('frame', 'pack-check-attempt-1')" in source
    assert "PYC('frame', 'pack-check-attempt-2'" in source
    assert "PYC('l3', 'sector-list-attempt-1')" in source
    assert "PY('frame', 'frame-attempt-1')" in source
    assert "PY('frame', 'frame-attempt-2', 2)" in source
    assert "PY('prelude', 'prelude-attempt-1')" in source
    assert "PY('prelude', 'prelude-attempt-2', 2)" in source
    assert "PY('gate1', 'gate1-attempt-1')" in source
    assert "PY('gate2', 'gate2-attempt-1')" in source


def test_scan_handoff_propagates_run_and_engine_to_stock_workflows():
    source = _executable_source(WORKFLOWS[0])
    assert source.count("run_id: RUN_ID") >= 3
    assert source.count("engine: ENGINE") >= 3
    assert source.count("dispatch_attempt: 1") >= 3


def test_l4_workflow_invocation_ids_bind_stock_and_attempt():
    source = _executable_source(WORKFLOWS[1])
    assert "args.attempt 必填" in source
    assert "Math.max(1, Number(A.attempt) || 1)" not in source
    assert "PY('l4', `l4-preflight-${code}-attempt-${taskAttempt}`, taskAttempt, code)" in source
    assert "PY('l4', `l4-prepare-${code}-attempt-${taskAttempt}`" in source
    assert "PY('l4', `l4-failure-${code}-attempt-${taskAttempt}`" in source
    assert "PY('l4', `l4-success-${code}-attempt-${taskAttempt}`" in source
    assert source.count("--expected-attempt ${taskAttempt}") >= 3


def test_l4_workflow_routes_every_business_agent_through_boundary_wrapper():
    source = _executable_source(WORKFLOWS[1])
    assert "const rawAgent = agent" in source
    assert "async function tracedAgent(" in source
    assert "AGENT_DISPATCHED" in source
    assert "AGENT_COMPLETED" in source
    assert "AGENT_FAILED" in source
    assert "autoresearch.trace.capsule agent-event" in source
    assert "await emitAgentEvent('AGENT_DISPATCHED'" in source
    assert "await emitAgentEvent('AGENT_COMPLETED'" in source
    assert "await emitAgentEvent('AGENT_FAILED'" in source
    # All legacy call sites must use tracedAgent.  The raw primitive is owned only by
    # the wrapper and its evidence writer; no direct `agent(...)` bypass remains.
    assert not re.search(r"(?<![A-Za-z])agent\s*\(", source)
    assert source.count("tracedAgent(") >= 9
    assert "l4-card-${code}-${taskAttempt}" in source
    assert "l4-intel-${code}-${taskAttempt}" in source
    assert source.count("rawAgent(") == 2
    emit_body = source.split("const emitAgentEvent =", 1)[1].split(
        "async function tracedAgent", 1
    )[0]
    assert emit_body.count("rawAgent(") == 1
    assert "--control-invocation-id ${controlInvocationId}" in emit_body
    assert "trace-control-${invocationId}-${eventType.toLowerCase()}" in emit_body
    assert "required: ['ok', 'event', 'control_events']" in source
    for field in (
        "schema_version", "seq", "run_id", "ts", "engine", "stage",
        "invocation_id", "attempt", "subject", "event_type", "payload",
        "prev_hash", "event_hash",
    ):
        assert field in source
    assert "target_event_hash" in source
    assert "TRACE_CONTROL_CALLS_PER_TARGET = 2" in source
    wrapper_body = source.split("async function tracedAgent", 1)[1].split(
        "const recordL4", 1
    )[0]
    assert wrapper_body.count("rawAgent(") == 1
    assert wrapper_body.count("emitAgentEvent(") == 3


@pytest.mark.skipif(_NODE is None, reason="requires node workflow probe")
@pytest.mark.parametrize(
    "ack_mode",
    [
        "valid",
        "ok-false",
        "target-mismatch",
        "control-mismatch",
        "wrong-engine",
        "missing-hash",
        "wrong-linkage",
        "wrong-terminal",
    ],
)
def test_l4_trace_control_ack_is_strictly_validated_but_remains_best_effort(
    ack_mode
):
    path = WORKFLOWS[1]
    script = textwrap.dedent(
        r"""
        const fs = require('fs');
        const AsyncFunction = Object.getPrototypeOf(async function(){}).constructor;
        let src = fs.readFileSync(process.argv[1], 'utf8').replace(/^export const meta/m, 'const meta');
        const mode = process.argv[3];
        const logs = [];
        const boundaryAck = (prompt) => {
          const eventType = prompt.match(/agent-event \S+ (AGENT_[A-Z]+)/)[1];
          const invocationId = [...prompt.matchAll(/--invocation-id ([^ ]+)/g)].at(-1)[1];
          const controlId = prompt.match(/--control-invocation-id ([^ `]+)/)[1];
          const role = prompt.match(/--role ([^ ]+)/)[1];
          const subject = prompt.match(/--subject ([^ ]+)/)[1];
          const attempt = Number([...prompt.matchAll(/--attempt (\d+)/g)].at(-1)[1]);
          const runId = prompt.match(/agent-event (\S+) AGENT_/)[1];
          const event = (seq, type, id, eventRole, eventHash) => ({
            schema_version: 1, seq,
            run_id: runId, ts: '2026-08-27T01:02:03.456789Z',
            engine: 'claude', stage: 'l4', invocation_id: id,
            attempt, subject, event_type: type,
            payload: {role: eventRole, result: null, error: null},
            prev_hash: '0'.repeat(64), event_hash: eventHash,
          });
          const target = event(3, eventType, invocationId, role, 'a'.repeat(64));
          const ack = {
            ok: true,
            event: target,
            control_events: [
              event(2, 'AGENT_DISPATCHED', controlId, 'trace-control', 'b'.repeat(64)),
              event(4, 'AGENT_COMPLETED', controlId, 'trace-control', 'c'.repeat(64)),
            ],
          };
          ack.control_events[1].payload.result = {target_event_hash: target.event_hash};
          if (mode === 'ok-false') ack.ok = false;
          if (mode === 'target-mismatch') ack.event.invocation_id = 'wrong-target';
          if (mode === 'control-mismatch') ack.control_events[1].subject = '600001';
          if (mode === 'wrong-engine') ack.event.engine = 'codex';
          if (mode === 'missing-hash') delete ack.event.event_hash;
          if (mode === 'wrong-linkage') ack.control_events[1].payload.result.target_event_hash = 'd'.repeat(64);
          if (mode === 'wrong-terminal') ack.control_events[1].event_type = 'AGENT_FAILED';
          return ack;
        };
        const agent = async (prompt) => {
          if (/autoresearch\.trace\.capsule agent-event/.test(prompt)) return boundaryAck(prompt);
          return {ok: true, action: 'BLOCKED', attempt: 0, reason: 'fixture'};
        };
        const fn = new AsyncFunction('agent','parallel','pipeline','log','phase','args','budget','workflow', src);
        fn(agent, null, null, value => logs.push(String(value)), () => {}, JSON.parse(process.argv[2]), {total:null}, null)
          .then(result => console.log(JSON.stringify({logs, result, error:null})))
          .catch(error => console.log(JSON.stringify({logs, result:null, error:error.message})));
        """
    )
    args = {
        "date": "2026-01-01",
        "run_id": "20260827T010203456789Z",
        "code": "600000",
        "attempt": 1,
        "allow_empty_config": True,
    }
    completed = subprocess.run(
        [_NODE, "-e", script, str(path), json.dumps(args), ack_mode],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    result = json.loads(completed.stdout.splitlines()[-1])
    assert result["error"] is None
    warnings = [line for line in result["logs"] if "取证失败" in line]
    assert (warnings == []) is (ack_mode == "valid")


@pytest.mark.skipif(_NODE is None, reason="requires node workflow probe")
@pytest.mark.parametrize("business_failure", [False, True])
def test_l4_boundary_wrapper_emits_one_dispatch_and_one_terminal_with_same_id(
    business_failure
):
    path = WORKFLOWS[1]
    script = textwrap.dedent(
        """
        const fs = require('fs');
        const AsyncFunction = Object.getPrototypeOf(async function(){}).constructor;
        let src = fs.readFileSync(process.argv[1], 'utf8').replace(/^export const meta/m, 'const meta');
        __BOUNDARY_ACK__
        const fail = JSON.parse(process.argv[3]);
        const calls = [];
        const agent = async (prompt, options) => {
          calls.push({prompt, label: options && options.label});
        if (/autoresearch\\.trace\\.capsule agent-event/.test(prompt)) return boundaryAck(prompt);
          if (fail) throw new Error('BUSINESS_AGENT_FAILED');
          return {ok: true, action: 'BLOCKED', attempt: 0, reason: 'fixture'};
        };
        const fn = new AsyncFunction('agent','parallel','pipeline','log','phase','args','budget','workflow', src);
        fn(agent, null, null, () => {}, () => {}, JSON.parse(process.argv[2]), {total:null}, null)
          .then(result => console.log(JSON.stringify({calls, result, error:null})))
          .catch(error => console.log(JSON.stringify({calls, result:null, error:error.message})));
        """
    ).replace("__BOUNDARY_ACK__", _BOUNDARY_ACK_JS)
    args = {
        "date": "2026-01-01",
        "run_id": "20260827T010203456789Z",
        "code": "600000",
        "attempt": 1,
        "allow_empty_config": True,
    }
    completed = subprocess.run(
        [_NODE, "-e", script, str(path), json.dumps(args), json.dumps(business_failure)],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    result = json.loads(completed.stdout.splitlines()[-1])
    boundary_prompts = [
        item["prompt"]
        for item in result["calls"]
        if "autoresearch.trace.capsule agent-event" in item["prompt"]
    ]
    assert len(boundary_prompts) == 2
    assert "AGENT_DISPATCHED" in boundary_prompts[0]
    terminal = "AGENT_FAILED" if business_failure else "AGENT_COMPLETED"
    assert terminal in boundary_prompts[1]
    ids = [re.findall(r"--invocation-id ([^ ]+)", prompt)[-1] for prompt in boundary_prompts]
    assert ids == ["gp-shell-600000-1-task-preflight-600000"] * 2
    control_ids = [
        re.search(r"--control-invocation-id ([^ ]+)", prompt).group(1)
        for prompt in boundary_prompts
    ]
    assert control_ids == [
        "trace-control-gp-shell-600000-1-task-preflight-600000-agent_dispatched",
        "trace-control-gp-shell-600000-1-task-preflight-600000-"
        + ("agent_failed" if business_failure else "agent_completed"),
    ]


@pytest.mark.skipif(_NODE is None, reason="requires node workflow probe")
@pytest.mark.parametrize(
    "path,args",
    [
        (WORKFLOWS[0], {"date": "2026-01-01;touch /tmp/pwn", "run_id": "20260827T010203456789Z"}),
        (WORKFLOWS[0], {"date": "2026-02-30", "run_id": "20260827T010203456789Z"}),
        (WORKFLOWS[0], {"date": "2026-01-01", "run_id": "20260827T010203456789Z;id"}),
        (
            WORKFLOWS[1],
            {
                "date": "2026-01-01;id",
                "run_id": "20260827T010203456789Z",
                "code": "600000",
                "attempt": 1,
            },
        ),
        (
            WORKFLOWS[1],
            {
                "date": "2026-02-30",
                "run_id": "20260827T010203456789Z",
                "code": "600000",
                "attempt": 1,
            },
        ),
        (
            WORKFLOWS[1],
            {
                "date": "2026-01-01",
                "run_id": "20260827T010203456789Z",
                "code": "600000;id",
                "attempt": 1,
            },
        ),
        (
            WORKFLOWS[1],
            {
                "date": "2026-01-01",
                "run_id": "20260827T010203456789Z;id",
                "code": "600000",
                "attempt": 1,
            },
        ),
    ],
)
def test_workflow_rejects_unsafe_shell_and_path_tokens_before_dispatch(path, args):
    args.update({"allow_empty_config": True})
    result = _probe_workflow(path, args)
    assert result["first"] is None
    assert "非法" in result["error"]


@pytest.mark.skipif(_NODE is None, reason="requires node workflow probe")
@pytest.mark.parametrize("attempt", [None, 0, -1, 1.5, "1", "1;id"])
def test_l4_workflow_requires_authoritative_positive_integer_attempt(attempt):
    args = {
        "date": "2026-01-01",
        "run_id": "20260827T010203456789Z",
        "code": "600000",
        "allow_empty_config": True,
    }
    if attempt is not None:
        args["attempt"] = attempt
    result = _probe_workflow(WORKFLOWS[1], args)
    assert result["first"] is None
    assert "attempt" in result["error"]


@pytest.mark.skipif(_NODE is None, reason="requires node workflow probe")
def test_l4_retry_attempts_produce_distinct_preflight_invocation_ids():
    base = {
        "date": "2026-01-01",
        "run_id": "20260827T010203456789Z",
        "code": "600000",
        "allow_empty_config": True,
    }
    first = _probe_workflow(WORKFLOWS[1], {**base, "attempt": 1})["first"]
    second = _probe_workflow(WORKFLOWS[1], {**base, "attempt": 2})["first"]
    assert "l4-preflight-600000-attempt-1" in first
    assert "--attempt 1" in first
    assert "--expected-attempt 1" in first
    assert "l4-preflight-600000-attempt-2" in second
    assert "--attempt 2" in second
    assert "--expected-attempt 2" in second
    assert first != second


@pytest.mark.parametrize("path", WORKFLOWS)
def test_workflow_propagates_engine_and_run_identity(path):
    source = _executable_source(path)
    assert "AUTORESEARCH_ENGINE=${ENGINE}" in source
    assert "--run-id ${RUN_ID}" in source
    assert "--stage ${stage}" in source
    assert "--invocation-id ${invocation}" in source
