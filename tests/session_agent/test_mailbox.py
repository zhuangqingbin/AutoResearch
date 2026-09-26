"""Host-mode (mailbox) executor: request/result files under <staging>/_dispatch/."""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from autoresearch.session_agent import runner
from autoresearch.session_agent.executors import mailbox
from autoresearch.session_agent.executors.base import DispatchRequest, ExecutorTimeout

from ._runner_support import CARD_TEXT, begin_synthetic_run, det, inf

REPO = Path(__file__).resolve().parents[2]


def _request(tmp_path, task_id="scan.l4.card.600000", attempt=1, timeout=5.0) -> DispatchRequest:
    return DispatchRequest(
        run_id="20260913T010203000000Z", engine="codex", task_id=task_id, attempt=attempt,
        role="scan.l4.card", agent_type="l4-card", config_role="l4_card", model=None,
        effort="max", agent_spec={"effort": "max"}, tier="critical", max_turns=None,
        prompt="执行任务包", instruction_refs=(".claude/agents/l4-card.md",),
        input_paths={"p": str(tmp_path / "_l4_prompt_600000.md")},
        output_paths={"card": str(tmp_path / "details/600000.md")},
        subject="600000", independent_context=False, tool_policy="READ_WRITE",
        timeout_seconds=timeout, host_session_ref="session-main",
    )


def test_mailbox_roundtrip_and_stale_result_ignored(tmp_path):
    ex = mailbox.MailboxExecutor(tmp_path, poll_seconds=0.02)
    # A late result of the timed-out attempt 1 is on disk first: it must be ignored.
    (tmp_path / "_dispatch").mkdir()
    (tmp_path / "_dispatch" / "scan.l4.card.600000.a1.result.json").write_text(json.dumps({
        "schema_version": 1, "task_id": "scan.l4.card.600000", "attempt": 1, "ok": True,
        "session_ref": "old", "context_ref": "old", "parent_context_ref": None}), encoding="utf-8")
    seen = {}

    def answer():
        doc = mailbox.wait_request(tmp_path, timeout=2, poll_seconds=0.02)
        seen.update(doc)
        mailbox.write_result(tmp_path, doc["task_id"], doc["attempt"], ok=True,
                             session_ref="s", context_ref="c", parent_context_ref="s")

    thread = threading.Thread(target=answer)
    thread.start()
    result = ex.dispatch(_request(tmp_path, attempt=2))
    thread.join()
    assert seen["kind"] == "REQUEST" and seen["attempt"] == 2
    assert (result.ok, result.session_ref, result.context_ref) == (True, "s", "c")


def test_mailbox_times_out_with_path_in_message(tmp_path):
    ex = mailbox.MailboxExecutor(tmp_path, poll_seconds=0.02)
    with pytest.raises(ExecutorTimeout, match=re.escape("_dispatch/scan.l4.card.600000.a1.result.json")):
        ex.dispatch(_request(tmp_path, timeout=0.2))


def test_request_file_is_the_rendered_request(tmp_path):
    request = _request(tmp_path, timeout=0.05)
    with pytest.raises(ExecutorTimeout):
        mailbox.MailboxExecutor(tmp_path, poll_seconds=0.01).dispatch(request)
    doc = json.loads((tmp_path / "_dispatch/scan.l4.card.600000.a1.request.json").read_text("utf-8"))
    assert doc["agent_type"] == "l4-card" and doc["effort"] == "max" and doc["prompt"] == "执行任务包"
    issued = {key: value for key, value in doc.items() if key != "issued_at"}
    assert DispatchRequest.from_json(issued) == request


def test_wait_hands_out_each_request_once_and_can_reoffer_taken(tmp_path):
    for attempt in (1, 2):
        mailbox.issue_request(tmp_path, _request(tmp_path, task_id=f"t.{attempt}"))
    first = mailbox.wait_request(tmp_path, timeout=0.2, poll_seconds=0.01)
    second = mailbox.wait_request(tmp_path, timeout=0.2, poll_seconds=0.01)
    third = mailbox.wait_request(tmp_path, timeout=0.1, poll_seconds=0.01)
    assert {first["task_id"], second["task_id"]} == {"t.1", "t.2"}
    assert third["kind"] == "IDLE" and sorted(third["taken_unanswered"]) == ["t.1.a1", "t.2.a1"]
    again = mailbox.wait_request(tmp_path, timeout=0.2, poll_seconds=0.01, include_taken=True)
    assert again["kind"] == "REQUEST"


def test_wait_reports_runner_exit_when_nothing_is_pending(tmp_path):
    (tmp_path / "_dispatch").mkdir()
    (tmp_path / "_dispatch/runner.json").write_text(json.dumps(
        {"state": "EXITED", "outcome": {"stop_reason": "FINISHED"}}), encoding="utf-8")
    doc = mailbox.wait_request(tmp_path, timeout=5, poll_seconds=0.01)
    assert doc["kind"] == "RUNNER_EXITED"
    assert doc["runner"]["outcome"]["stop_reason"] == "FINISHED"


def test_complete_requires_an_issued_request_and_is_write_once(tmp_path):
    with pytest.raises(ValueError, match="no issued request"):
        mailbox.write_result(tmp_path, "scan.l4.card.600000", 1, ok=True, context_ref="c")
    mailbox.issue_request(tmp_path, _request(tmp_path))
    mailbox.write_result(tmp_path, "scan.l4.card.600000", 1, ok=True, context_ref="c")
    with pytest.raises(mailbox.MailboxConflict):
        mailbox.write_result(tmp_path, "scan.l4.card.600000", 1, ok=True, context_ref="other")


def test_concurrent_completes_cannot_both_win(tmp_path, monkeypatch):
    mailbox.issue_request(tmp_path, _request(tmp_path))
    real_replace = mailbox.os.replace

    def slow_replace(src, dst):          # widen the check-then-commit window
        time.sleep(0.05)
        real_replace(src, dst)

    monkeypatch.setattr(mailbox.os, "replace", slow_replace)
    wins, conflicts = [], []
    barrier = threading.Barrier(6)

    def complete(i):
        barrier.wait()
        try:
            mailbox.write_result(tmp_path, "scan.l4.card.600000", 1, ok=True, context_ref=f"c{i}")
            wins.append(i)
        except mailbox.MailboxConflict:
            conflicts.append(i)

    threads = [threading.Thread(target=complete, args=(i,)) for i in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(wins) == 1 and len(conflicts) == 5
    doc = mailbox.read_result(tmp_path, "scan.l4.card.600000", 1)
    assert doc["context_ref"] == f"c{wins[0]}"


def test_dispatch_reattaches_to_an_existing_request_without_rewriting(tmp_path):
    request = _request(tmp_path)
    path = mailbox.issue_request(tmp_path, request)
    issued = json.loads(path.read_text("utf-8"))
    issued["issued_at"] = "2000-01-01T00:00:00Z"          # the host already saw this one
    path.write_text(json.dumps(issued), encoding="utf-8")
    before = path.read_bytes()
    mailbox.write_result(tmp_path, request.task_id, 1, ok=True, context_ref="c")
    result = mailbox.MailboxExecutor(tmp_path, poll_seconds=0.01).dispatch(request)
    assert result.ok and result.context_ref == "c"
    assert path.read_bytes() == before
    assert mailbox.MailboxExecutor.supports_reattach is True


def test_result_whose_body_names_another_attempt_is_ignored(tmp_path):
    request = _request(tmp_path, timeout=0.2)
    mailbox.issue_request(tmp_path, request)
    (tmp_path / "_dispatch/scan.l4.card.600000.a1.result.json").write_text(json.dumps({
        "schema_version": 1, "task_id": "scan.l4.card.600000", "attempt": 7, "ok": True,
        "context_ref": "wrong"}), encoding="utf-8")
    with pytest.raises(ExecutorTimeout):
        mailbox.MailboxExecutor(tmp_path, poll_seconds=0.01).dispatch(request)


def test_failure_result_is_returned_not_raised(tmp_path):
    request = _request(tmp_path)
    mailbox.issue_request(tmp_path, request)
    mailbox.write_result(tmp_path, request.task_id, 1, ok=False,
                         error="API Error: 429 rate limit", error_class=None)
    result = mailbox.MailboxExecutor(tmp_path, poll_seconds=0.01).dispatch(request)
    assert result.ok is False and "429" in result.error


def test_runner_and_mailbox_complete_a_run_end_to_end(tmp_path, monkeypatch):
    run = begin_synthetic_run(tmp_path, monkeypatch, [
        det("synthetic.one"),
        inf("synthetic.inference.a", deps=["synthetic.one"], inputs=["synthetic.one.out"]),
        inf("synthetic.inference.b", deps=["synthetic.one"], inputs=["synthetic.one.out"]),
    ])
    staging = Path(run.handle.staging)
    stop = threading.Event()
    served = []

    def host():                          # what the interactive session does
        while not stop.is_set():
            doc = mailbox.wait_request(staging, timeout=0.5, poll_seconds=0.01)
            if doc["kind"] == "RUNNER_EXITED":
                return
            if doc["kind"] != "REQUEST":
                continue
            for path in doc["output_paths"].values():
                Path(path).parent.mkdir(parents=True, exist_ok=True)
                Path(path).write_text(CARD_TEXT, encoding="utf-8")
            mailbox.write_result(staging, doc["task_id"], doc["attempt"], ok=True,
                                 session_ref="session-main", context_ref=f"agent-{len(served)}",
                                 parent_context_ref="session-main")
            served.append(doc["task_id"])

    thread = threading.Thread(target=host)
    thread.start()
    try:
        final = runner.run_loop(run.run_id, mailbox.MailboxExecutor(staging, poll_seconds=0.01),
                                poll_seconds=0.01, max_rounds=2000, hooks=run.hooks())
    finally:
        stop.set()
        thread.join(timeout=5)
    assert final["finished"] is True
    assert sorted(served) == ["synthetic.inference.a", "synthetic.inference.b"]
    ledger = [json.loads(line) for line in
              (staging / "_dispatch/ledger.jsonl").read_text("utf-8").splitlines()]
    assert {row["task_id"] for row in ledger if row["outcome"] == "SUBMITTED"} == set(served)


# ── CLI ─────────────────────────────────────────────────────────────────────────

def _cli_run(tmp_path, monkeypatch, **kwargs):
    run = begin_synthetic_run(tmp_path, monkeypatch, [inf("synthetic.inference")], **kwargs)
    monkeypatch.setenv("AUTORESEARCH_ENGINE", "codex")
    # main() exports AUTORESEARCH_RUN_ID; pre-set it through monkeypatch so it is undone.
    monkeypatch.setenv("AUTORESEARCH_RUN_ID", run.run_id)
    from autoresearch.trace import capsule

    monkeypatch.setattr(capsule, "require_active_run", lambda run_id: run.handle)
    return run


def test_cli_wait_and_complete_round_trip(tmp_path, monkeypatch, capsys):
    from autoresearch.session_agent import __main__ as cli

    run = _cli_run(tmp_path, monkeypatch)
    staging = Path(run.handle.staging)
    mailbox.issue_request(staging, _request(staging, task_id="synthetic.inference"))
    assert cli.main(["mailbox", "wait", "--run-id", run.run_id, "--timeout", "1"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["kind"] == "REQUEST" and doc["agent_type"] == "l4-card"
    assert cli.main(["mailbox", "complete", "--run-id", run.run_id, "--task-id",
                     "synthetic.inference", "--attempt", "1", "--context-ref", "agent-1"]) == 0
    written = json.loads(capsys.readouterr().out)
    result = mailbox.read_result(staging, "synthetic.inference", 1)
    assert result["ok"] is True and result["context_ref"] == "agent-1"
    assert result["session_ref"] == "session-main"            # defaults to the host profile
    assert result["parent_context_ref"] == "session-main"
    assert written["result_path"].endswith("_dispatch/synthetic.inference.a1.result.json")


def test_cli_complete_with_error_writes_a_failure(tmp_path, monkeypatch, capsys):
    from autoresearch.session_agent import __main__ as cli

    run = _cli_run(tmp_path, monkeypatch)
    staging = Path(run.handle.staging)
    mailbox.issue_request(staging, _request(staging, task_id="synthetic.inference"))
    assert cli.main(["mailbox", "complete", "--run-id", run.run_id, "--task-id",
                     "synthetic.inference", "--attempt", "1", "--context-ref", "agent-1",
                     "--error", "subagent crashed", "--error-class", "CONNECTION"]) == 0
    result = mailbox.read_result(staging, "synthetic.inference", 1)
    assert result["ok"] is False and result["error_class"] == "CONNECTION"


def test_cli_complete_derives_the_subagent_transcript(tmp_path, monkeypatch, capsys):
    from autoresearch.session_agent import __main__ as cli
    from autoresearch.trace import usage_harvest

    run = _cli_run(tmp_path, monkeypatch)
    staging = Path(run.handle.staging)
    subagents = tmp_path / "projects/slug/session-main/subagents"
    subagents.mkdir(parents=True)
    (subagents / "agent-abc123.jsonl").write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(usage_harvest, "find_session_dir", lambda session_id: subagents)
    mailbox.issue_request(staging, _request(staging, task_id="synthetic.inference"))
    assert cli.main(["mailbox", "complete", "--run-id", run.run_id, "--task-id",
                     "synthetic.inference", "--attempt", "1", "--context-ref", "abc123"]) == 0
    result = mailbox.read_result(staging, "synthetic.inference", 1)
    assert result["transcript_path"] == str(subagents / "agent-abc123.jsonl")


def test_cli_run_resolves_parallel_cap_from_frozen_config(tmp_path, monkeypatch, capsys):
    from autoresearch.session_agent import __main__ as cli

    run = _cli_run(tmp_path, monkeypatch,
                   user_config={"budgets": {"concurrency": {"l4_stock": 3}}})
    seen = {}

    def fake_loop(run_id, executor, **kwargs):
        seen.update(kwargs, run_id=run_id, executor=executor.name)
        return {"status": "DONE", "finished": True, "stop_reason": "FINISHED"}

    monkeypatch.setattr(runner, "run_loop", fake_loop)
    assert cli.main(["run", "--run-id", run.run_id, "--executor", "mailbox",
                     "--poll-seconds", "0.5"]) == 0
    assert seen["run_id"] == run.run_id and seen["executor"] == "mailbox"
    assert seen["max_parallel"] == 3 and seen["poll_seconds"] == 0.5
    assert json.loads(capsys.readouterr().out)["stop_reason"] == "FINISHED"


def test_cli_run_exit_code_reflects_an_unfinished_run(tmp_path, monkeypatch, capsys):
    from autoresearch.session_agent import __main__ as cli

    run = _cli_run(tmp_path, monkeypatch)
    monkeypatch.setattr(runner, "run_loop", lambda run_id, executor, **kwargs: {
        "status": "BLOCKED", "finished": False, "stop_reason": "BLOCKED"})
    assert cli.main(["run", "--run-id", run.run_id, "--executor", "mailbox",
                     "--max-parallel", "2"]) == 8


# ── registration & isolation of _dispatch/ ──────────────────────────────────────

def test_dispatch_mailbox_is_a_registered_staging_artifact():
    from autoresearch.contracts import artifacts as ca

    requests = ca.by_name("dispatch_requests")
    results = ca.by_name("dispatch_results")
    assert (requests.root, requests.path) == ("staging", "_dispatch/*.request.json")
    assert (results.root, results.path) == ("staging", "_dispatch/*.result.json")
    assert requests.presence == results.presence == "conditional"
    assert ca.by_name("dispatch_runner_status").path == "_dispatch/runner.json"


def test_scan_staging_bundles_never_capture_the_mailbox(tmp_path):
    from autoresearch.session_agent.domain_ops import collect_scan_staging_bundle

    (tmp_path / "_dispatch").mkdir()
    (tmp_path / "_dispatch/x.a1.request.json").write_text("{}", encoding="utf-8")
    (tmp_path / "market_view.md").write_text("mv", encoding="utf-8")
    bundle = collect_scan_staging_bundle(tmp_path, phase="prelude")
    assert list(bundle["files"]) == ["market_view.md"]


# ── verbatim legacy prompts ─────────────────────────────────────────────────────

def _js_template(workflow: str, needle: str) -> str:
    line = next(item for item in (REPO / ".claude/workflows" / workflow).read_text(
        "utf-8").splitlines() if needle in item)
    return line[line.index("`") + 1: line.rindex("`")]


def _fill(template: str, values: dict) -> str:
    for key, value in values.items():
        template = template.replace("${" + key + "}", str(value))
    return template


@pytest.fixture
def scan_handle(tmp_path):
    from autoresearch.session_agent import artifacts

    staging = tmp_path / "staging/2026-09-13"
    (staging / "session_outputs").mkdir(parents=True)
    handle = SimpleNamespace(workspace=tmp_path, staging=staging, engine="codex",
                             run_id="20260913T010203000000Z", analysis_date="2026-09-13")
    (staging / "session_outputs/gate1.json").write_text(json.dumps(
        {"ok": True, "l4_budget": 13, "l3cap": 10, "max_cards": 13}), encoding="utf-8")
    (staging / "session_outputs/l4.plan.json").write_text(json.dumps({
        "meta": {"600519": {"name": "贵州茅台", "sector": "食品饮料", "pinned": False,
                            "dossier_summary": "已知:高端白酒龙头"}},
        "intel_max_queries": 20}, ensure_ascii=False), encoding="utf-8")
    artifacts.register_artifact(handle, "scan.gate1.result", staging / "session_outputs/gate1.json", "READ")
    artifacts.register_artifact(handle, "scan.l4.plan", staging / "session_outputs/l4.plan.json", "READ")
    return handle


def _render(handle, task_id, role, inputs, outputs, subject=None):
    from autoresearch.session_agent.dispatch import render_prompt

    task = {"task_id": task_id, "role": role, "subject": subject}
    staging = Path(handle.staging)
    return render_prompt(
        handle, task, 1,
        inputs={key: str(staging / value) for key, value in inputs.items()},
        outputs={key: str(staging / value) for key, value in outputs.items()},
    )


def test_macro_brief_prompt_is_the_legacy_wording(scan_handle):
    from autoresearch.session_agent.dispatch import display_path

    sd = display_path(scan_handle.staging)
    prompt = _render(scan_handle, "scan.market_view", "macro.brief",
                     {"scan.strategist.pack": "strategist_pack.json"},
                     {"scan.market.view": "market_view.md"})
    assert prompt == _fill(_js_template("scan-market.js", "strategist_pack.json 的 pack 段"),
                           {"SD": sd})


def test_l3_rank_prompt_takes_l3cap_from_frozen_gate1(scan_handle):
    from autoresearch.session_agent.dispatch import display_path

    sd = display_path(scan_handle.staging)
    prompt = _render(scan_handle, "scan.l3.rank", "scan.l3",
                     {"scan.l3.table": "_l3_table.md"}, {"scan.l3.judged": "_l3_judged.json"})
    expected = _fill(_js_template("scan-market.js", "L3 精排 · 日期"),
                     {"SD": sd, "date": "2026-09-13", "l3cap": 10, "l3lo": 7})
    assert prompt == expected
    assert "7~10 只" in prompt


def test_l3_prompt_lower_bound_follows_a_small_l3cap(scan_handle):
    from autoresearch.session_agent import artifacts

    path = Path(scan_handle.staging) / "session_outputs/gate1b.json"
    path.write_text(json.dumps({"ok": True, "l4_budget": 5}), encoding="utf-8")
    registry = Path(scan_handle.workspace) / "session/artifacts.json"
    payload = json.loads(registry.read_text("utf-8"))
    del payload["artifacts"]["scan.gate1.result"]
    registry.write_text(json.dumps(payload), encoding="utf-8")
    artifacts.register_artifact(scan_handle, "scan.gate1.result", path, "READ")
    prompt = _render(scan_handle, "scan.l3.rank", "scan.l3",
                     {"scan.l3.table": "_l3_table.md"}, {"scan.l3.judged": "_l3_judged.json"})
    assert "按质 5~5 只" in prompt          # old frozen GATE1: l3cap = min(10, l4_budget)


def test_l3_repair_prompt_is_the_legacy_wording(scan_handle):
    from autoresearch.session_agent.dispatch import display_path

    prompt = _render(scan_handle, "scan.l3.repair", "scan.l3.repair",
                     {"scan.l3.repair.prompt": "_l3_repair_prompt.md"},
                     {"scan.l3.repair.patch": "_l3_repair_patch.json"})
    assert prompt == _fill(_js_template("scan-market.js", "_l3_repair_prompt.md，"),
                           {"SD": display_path(scan_handle.staging)})


def test_sector_brief_prompt_is_the_legacy_wording_on_session_paths(scan_handle):
    from autoresearch.session_agent.dispatch import display_path

    staging = Path(scan_handle.staging)
    prompt = _render(scan_handle, "scan.sector.k.brief", "sector.brief",
                     {"scan.sector.k.pack": "session_inputs/sectors/k.json"},
                     {"scan.sector.k.brief": "sector_briefs/食品饮料.md"}, subject="食品饮料")
    template = _js_template("scan-market.js", "你是行业分析师。读")
    template = template.replace("${CTX}/sector/${date}/${sec}.json",
                                display_path(staging / "session_inputs/sectors/k.json"))
    template = template.replace("${SD}/sector_briefs/${sec}.md",
                                display_path(staging / "sector_briefs/食品饮料.md"))
    assert prompt == template


def test_l4_intel_prompt_is_the_legacy_wording(scan_handle):
    from autoresearch.session_agent.dispatch import display_path

    sd = display_path(scan_handle.staging)
    prompt = _render(scan_handle, "l4.600519.a1.intel", "scan.l4.intel",
                     {"scan.l4.600519.a1.prompt": "_l4_prompt_600519.md"},
                     {"scan.l4.600519.a1.intel": "_l4_intel_600519.md"}, subject="600519")
    known_template = _js_template("l4-stock.js", "## 已知底(覆盖档案摘要")
    known = _fill(known_template.replace("\\n", "\n"), {"dossierSummary": "已知:高端白酒龙头"})
    expected = _fill(_js_template("l4-stock.js", "活体情报采集:"), {
        "code": "600519", "name": "贵州茅台", "sector": "食品饮料", "date": "2026-09-13",
        "maxQ": 20, "SD": sd, "knownBase": known})
    assert prompt == expected


def test_l4_card_prompt_is_the_legacy_wording(scan_handle):
    from autoresearch.session_agent.dispatch import display_path

    sd = display_path(scan_handle.staging)
    prompt = _render(scan_handle, "l4.600519.a1.card", "scan.l4.card",
                     {"scan.l4.600519.a1.prompt": "_l4_prompt_600519.md",
                      "scan.l4.600519.a1.slim": "_external_inputs/600519.SS_2026-09-13_slim.md"},
                     {"scan.l4.600519.a1.card": "details/600519.md"}, subject="600519")
    assert prompt == _fill(_js_template("l4-stock.js", "先读整个任务包"),
                           {"SD": sd, "code": "600519"})


def test_l4_review_prompt_is_the_legacy_wording(scan_handle):
    from autoresearch.session_agent.dispatch import display_path

    sd = display_path(scan_handle.staging)
    for run_index in (2, 3):
        prompt = _render(scan_handle, f"l4.600519.a1.review{run_index}", "scan.l4.review",
                         {"scan.l4.600519.a1.prompt": "_l4_prompt_600519.md"},
                         {f"scan.l4.600519.a1.review{run_index}":
                          f"ensemble/600519.run{run_index}.md"}, subject="600519")
        assert prompt == _fill(_js_template("l4-stock.js", "(不知道其它 run 结论)"),
                               {"SD": sd, "code": "600519", "i": run_index})


# ── I1 (review 2026-09-26): host-mode timing is measured from ``.taken`` ────────────

class _FakeTime:
    """Single-threaded fake clock: ``sleep`` advances time and runs due host actions."""

    def __init__(self):
        self.now = 1_000.0
        self.events: list[tuple[float, object]] = []

    def at(self, when: float, action) -> None:
        self.events.append((self.now + when, action))
        self.events.sort(key=lambda item: item[0])

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds
        while self.events and self.events[0][0] <= self.now:
            _, action = self.events.pop(0)
            action()


def _fake_executor(tmp_path, fake: _FakeTime) -> mailbox.MailboxExecutor:
    return mailbox.MailboxExecutor(tmp_path, poll_seconds=0.5, clock=fake.clock,
                                   sleep=fake.sleep, wall=fake.clock)


def _host_take(tmp_path, fake: _FakeTime, task_id="scan.l4.card.600000"):
    def take():
        doc = mailbox.wait_request(tmp_path, timeout=0, poll_seconds=0, wall=fake.clock)
        assert doc["kind"] == "REQUEST" and doc["task_id"] == task_id
    return take


def _host_complete(tmp_path, task_id="scan.l4.card.600000", attempt=1):
    def complete():
        mailbox.write_result(tmp_path, task_id, attempt, ok=True, session_ref="s",
                             context_ref="agent-late", parent_context_ref="s",
                             transcript_path="/t/agent-late.jsonl")
    return complete


def test_timeout_counts_from_taken_not_from_issue(tmp_path):
    """A request that waited in the queue longer than its role timeout is not failed:
    the per-role budget starts when the host takes it."""
    fake = _FakeTime()
    fake.at(30.0, _host_take(tmp_path, fake))          # queued 3× the role timeout
    fake.at(38.0, _host_complete(tmp_path))            # agent took 8 s of its 10 s
    result = _fake_executor(tmp_path, fake).dispatch(_request(tmp_path, timeout=10.0))
    assert result.ok and result.context_ref == "agent-late"


def test_taken_request_times_out_on_the_role_budget_after_taken(tmp_path):
    fake = _FakeTime()
    fake.at(30.0, _host_take(tmp_path, fake))
    with pytest.raises(ExecutorTimeout, match="taken"):
        _fake_executor(tmp_path, fake).dispatch(_request(tmp_path, timeout=10.0))
    assert 40.0 <= fake.now - 1_000.0 < 42.0


def test_never_taken_request_times_out_only_after_the_generous_limit(tmp_path):
    fake = _FakeTime()
    with pytest.raises(ExecutorTimeout, match="never taken"):
        _fake_executor(tmp_path, fake).dispatch(_request(tmp_path, timeout=10.0))
    waited = fake.now - 1_000.0
    assert mailbox.NEVER_TAKEN_FACTOR * 10.0 <= waited < mailbox.NEVER_TAKEN_FACTOR * 10.0 + 1


def test_timed_out_attempt_is_abandoned_and_never_handed_out_again(tmp_path):
    fake = _FakeTime()
    with pytest.raises(ExecutorTimeout):
        _fake_executor(tmp_path, fake).dispatch(_request(tmp_path, timeout=1.0))
    assert mailbox.is_abandoned(tmp_path, "scan.l4.card.600000", 1)
    assert mailbox.pending_requests(tmp_path, include_taken=True) == []
    mailbox.issue_request(tmp_path, _request(tmp_path, attempt=2))
    doc = mailbox.wait_request(tmp_path, timeout=0, poll_seconds=0)
    assert (doc["kind"], doc["attempt"]) == ("REQUEST", 2)          # a2, never the dead a1
    idle = mailbox.wait_request(tmp_path, timeout=0, poll_seconds=0)
    assert idle["kind"] == "IDLE" and idle["taken_unanswered"] == ["scan.l4.card.600000.a2"]


def test_late_result_of_an_abandoned_attempt_is_refused_but_kept_as_evidence(tmp_path):
    fake = _FakeTime()
    with pytest.raises(ExecutorTimeout):
        _fake_executor(tmp_path, fake).dispatch(_request(tmp_path, timeout=1.0))
    with pytest.raises(mailbox.MailboxAbandoned) as refused:
        _host_complete(tmp_path)()
    assert "ABANDONED" in str(refused.value)
    late = mailbox.read_late_result(tmp_path, "scan.l4.card.600000", 1)
    assert late["transcript_path"] == "/t/agent-late.jsonl"
    # A reattach (runner restart) never resurrects an abandoned attempt.
    with pytest.raises(ExecutorTimeout, match="abandoned"):
        mailbox.MailboxExecutor(tmp_path, poll_seconds=0.01).dispatch(
            _request(tmp_path, timeout=5.0))


def test_a_result_written_before_the_timeout_decision_wins(tmp_path):
    mailbox.issue_request(tmp_path, _request(tmp_path))
    _host_complete(tmp_path)()
    assert mailbox.abandon_request(tmp_path, "scan.l4.card.600000", 1, reason="t") is False
    assert not mailbox.is_abandoned(tmp_path, "scan.l4.card.600000", 1)


def test_runner_exited_is_reported_even_with_abandoned_or_taken_requests(tmp_path):
    fake = _FakeTime()
    with pytest.raises(ExecutorTimeout):
        _fake_executor(tmp_path, fake).dispatch(_request(tmp_path, timeout=1.0))
    mailbox.issue_request(tmp_path, _request(tmp_path, task_id="t.taken"))
    assert mailbox.wait_request(tmp_path, timeout=0, poll_seconds=0)["task_id"] == "t.taken"
    (tmp_path / "_dispatch/runner.json").write_text(json.dumps(
        {"state": "EXITED", "outcome": {"stop_reason": "BLOCKED"}}), encoding="utf-8")
    doc = mailbox.wait_request(tmp_path, timeout=5, poll_seconds=0.01)
    assert doc["kind"] == "RUNNER_EXITED"
    assert doc["unanswered"] == ["t.taken.a1"]


def test_cli_complete_of_an_abandoned_attempt_says_ABANDONED(tmp_path, monkeypatch, capsys):
    from autoresearch.session_agent import __main__ as cli

    run = _cli_run(tmp_path, monkeypatch)
    staging = Path(run.handle.staging)
    mailbox.issue_request(staging, _request(staging, task_id="synthetic.inference"))
    assert mailbox.abandon_request(staging, "synthetic.inference", 1, reason="TIMEOUT") is True
    assert cli.main(["mailbox", "complete", "--run-id", run.run_id, "--task-id",
                     "synthetic.inference", "--attempt", "1", "--context-ref", "agent-1"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["kind"] == "ABANDONED"
    assert "discarded" in doc["message"]


def test_cli_wait_default_stays_under_the_host_bash_timeout():
    from autoresearch.session_agent import __main__ as cli

    args = cli._parser().parse_args(["mailbox", "wait", "--run-id", "x"])
    assert args.timeout <= 100
