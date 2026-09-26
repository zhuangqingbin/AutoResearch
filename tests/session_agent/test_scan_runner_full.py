"""Synthetic FULL scan over the real session_v1 plan graph, driven by the runner.

Real ``build_scan_plan`` / expansions / artifact registration / taskbook tickets /
production output validators; only the deterministic operations (a fake operation
runner that writes each task's declared outputs) and the models (a fake executor) are
stand-ins.  It is the smallest harness that walks every scan task kind through the
runner before a real run (batch 2–3 Task 5; audit
``docs/research/2026-09-26-session-plan-vs-workflow-audit.md``).
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from types import SimpleNamespace

from autoresearch.common import workspace as ws
from autoresearch.session_agent import artifacts, legacy_scan, runner, service
from autoresearch.session_agent.executors.base import DispatchResult
from autoresearch.session_agent.runner import ServiceHooks
from autoresearch.session_agent.workflows.scan import _sector_key

from ._runner_support import RUN_ID, profile
from .test_scan_runner_gaps import TEMPLATE_MARKET_VIEW

CODE = "600519"
SECTOR = "食品饮料"
#: The harness pins engine=codex, so its fake agents leave Codex-shaped transcripts.
CODEX_TRANSCRIPT = Path(__file__).resolve().parents[1] / "trace/fixtures/codex/rollout.jsonl"


def _write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, (dict, list)):
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    else:
        path.write_text(str(payload), encoding="utf-8")


class _FakeScanOperations:
    """Stand-in for the ``domain_ops`` subprocesses: writes each task's declared outputs."""

    def __init__(self, handle, *, branchy: bool = False, mode: str = "FULL"):
        self.handle = handle
        self.branchy = branchy      # intel on + L3 repair + pinned SELL double review
        self.mode = mode
        self.calls: list[str] = []

    def _content(self, operation: str, artifact_id: str):
        staging = Path(self.handle.staging)
        judged = staging / "_l3_judged.json"
        branchy = self.branchy
        book = staging / "_l4_tasks.json"
        ticket_attempt = (json.loads(book.read_text("utf-8"))["tasks"][CODE]["attempt"]
                          if book.is_file() else 1)
        rating = "Sell" if branchy else "Hold"
        trigger = "sell_review" if branchy else None
        special = {
            "scan.gate1.result": {"ok": True, "l4_budget": 13, "l3cap": 10, "max_cards": 13},
            "scan.run_mode": {"mode": self.mode, "pinned_codes": []},
            "scan.sector.list": {"schema_version": 1, "mode": "FULL", "sectors": [
                {"industry": SECTOR, "key": _sector_key(SECTOR), "reused": False}]},
            "scan.l3.validation": {"ok": not branchy},
            "scan.l3.repair.pack": {"codes": [CODE] if branchy else []},
            "scan.finalists": (f"code,name,sector,lane\n{CODE},贵州茅台,{SECTOR},"
                               f"{'pinned' if branchy else ''}\n"),
            "scan.l3.bench": "code\n",
            "scan.gate2.result": {"ok": True, "n": 1},
            "scan.l4.plan": {"codes": [CODE], "meta": {CODE: {
                "name": "贵州茅台", "sector": SECTOR, "pinned": branchy,
                "dossier_summary": "已知:高端白酒龙头" if branchy else ""}},
                "intel_enabled": branchy, "intel_max_queries": 20 if branchy else None},
            "scan.review.plan": {"schema_version": 1, "reviews": [
                {"code": CODE, "attempt": ticket_attempt, "rating": rating, "pinned": branchy,
                 "trigger": trigger}]},
            "scan.review.decision": {"schema_version": 1, "decisions": [
                {"code": CODE, "attempt": ticket_attempt, "rating": rating, "pinned": branchy,
                 "trigger": trigger, "review2_rating": rating if branchy else None,
                 "same_tier": True if branchy else None, "review3_required": False}]},
            "scan.l3.effective.judged": judged.read_text("utf-8") if judged.is_file() else "[]",
        }
        if self.mode == "SENTINEL_EMPTY":
            special.update({"scan.review.plan": {"schema_version": 1, "reviews": []},
                            "scan.review.decision": {"schema_version": 1, "decisions": []}})
        if artifact_id in special:
            return special[artifact_id]
        return {"schema_version": 1, "artifact": artifact_id, "operation": operation}

    def __call__(self, handle, stage, argv, invocation_id, attempt, subject, *, task_id):
        task = service._task(self.handle, task_id)
        self.calls.append(task["operation"])
        staging = Path(self.handle.staging)
        if task["operation"] == "scan.sector.prepare":
            _write(staging / "session_inputs/sectors" / f"{_sector_key(SECTOR)}.json",
                   {"industry": SECTOR})
        if task["operation"] == "scan.l4.prepare":
            _write(staging / f"_l4_prompt_{CODE}.md", f"任务包 {CODE}\n")
            legacy_scan.initialize_tickets(self.handle, [CODE],
                                           meta={CODE: {"ticker": "600519.SS"}})
        for artifact_id in task["output_artifact_ids"]:
            try:
                path = artifacts.artifact_path(self.handle, artifact_id)
            except KeyError:
                continue   # the service binds every declared output; unregistered ones fail there
            if artifact_id.startswith("scan.l4.") and artifact_id.endswith(".prompt"):
                continue   # written above, before the taskbook fingerprints it
            _write(path, self._content(task["operation"], artifact_id))
        if task["operation"] == "scan.l4.finalize":
            book = staging / "_l4_tasks.json"
            payload = json.loads(book.read_text(encoding="utf-8"))
            payload["tasks"][CODE]["status"] = "SUCCEEDED"
            book.write_text(json.dumps(payload), encoding="utf-8")
        return SimpleNamespace(exit_code=0, invocation={"status": "COMPLETED"})


class _FakeScanModels:
    name = "fake-models"

    def __init__(self, *, branchy: bool = False, transcripts: Path | None = None):
        self.requests = []
        self.branchy = branchy
        self.transcripts = transcripts      # outside the run workspace (capsule rule)

    def dispatch(self, request):
        self.requests.append(request)
        card = (f"# {CODE}\n**Rating**: Sell\nFINAL TRANSACTION PROPOSAL: **SELL**\n"
                if self.branchy else
                f"# {CODE}\n**Rating**: Hold\nFINAL TRANSACTION PROPOSAL: **HOLD**\n")
        text = {
            "macro.brief": TEMPLATE_MARKET_VIEW,
            "sector.brief": "## 地形段\n行业成交温和,估值处于近一年中位附近。\n",
            "scan.l3": json.dumps([{"code": CODE, "finalist": True, "thesis": "t"}]),
            "scan.l3.repair": json.dumps([{"code": CODE, "thesis": "fixed"}]),
            # Real intel differs between attempts (live web search).
            "scan.l4.intel": f"## 事件段\n- 无新增事件({request.task_id})\n## 声明行\n网查 3 条\n",
            "scan.l4.card": card,
            "scan.l4.review": card,
        }[request.role]
        for path in request.output_paths.values():
            _write(Path(path), text)
        evidence = (("host-binding:" + "1" * 64,) if request.independent_context else ())
        transcript = None
        if self.transcripts is not None:
            transcript = self.transcripts / f"{request.task_id}.a{request.attempt}.jsonl"
            transcript.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(CODEX_TRANSCRIPT, transcript)
        return DispatchResult(ok=True, session_ref="session-main",
                              context_ref=f"agent-{request.task_id}",
                              parent_context_ref="session-main", evidence_refs=evidence,
                              transcript_path=str(transcript) if transcript else None)


def _scan_run(tmp_path, monkeypatch, *, host=None, user_config=None):
    monkeypatch.setattr(ws, "ENGINE", "codex")
    monkeypatch.delenv("AUTORESEARCH_RUN_ID", raising=False)
    workspace = tmp_path / "context_codex/scan_runs" / RUN_ID
    staging = workspace / "staging/2026-09-13"
    staging.mkdir(parents=True)
    (workspace / "capsule/events").mkdir(parents=True)
    handle = SimpleNamespace(
        workspace=workspace, staging=staging, capsule=workspace / "capsule", engine="codex",
        run_id=RUN_ID, analysis_date="2026-09-13",
        contract=SimpleNamespace(contract_hash="a" * 64, config_hash="b" * 64,
                                 run_kind="scan-market", user_config=user_config or {}),
    )
    request = {
        "schema_version": 1, "kind": "scan-market", "requested_mode": "AUTO",
        "analysis_date": "2026-09-13", "subject": None, "peers": [], "asset_type": None,
        "name": None, "force_full": False, "host_profile": host or profile(),
        "predecessor_run_id": None,
    }
    service.begin(request, begin_capsule=lambda value: handle)
    return handle


def test_synthetic_full_scan_runs_through_the_runner_to_finish(tmp_path, monkeypatch):
    handle = _scan_run(tmp_path, monkeypatch)
    operations = _FakeScanOperations(handle)
    models = _FakeScanModels()
    finished = []
    hooks = ServiceHooks(
        handle_loader=lambda run_id: handle,
        operation_runner=operations,
        event_recorder=lambda *args, **kwargs: None,
        validator=None,                                   # production output contracts
        publisher=lambda current: finished.append("publish") or None,
        finalizer=lambda current, report: finished.append("finalize") or {"ok": True},
    )
    final = runner.run_loop(RUN_ID, models, max_parallel=4, poll_seconds=0.01,
                            max_rounds=3000, hooks=hooks)
    assert final["finished"] is True, (final["stop_reason"], final["errors"])
    assert finished == ["publish", "finalize"]
    # Zero general-purpose shells; the harness pins engine=codex → Codex project agents (M4).
    assert sorted({request.agent_type for request in models.requests}) == [
        "L3 rank", "L4 card", "scan strategist", "sector brief"]
    assert "scan.l4.ticket" not in operations.calls              # tickets are claimed, not run
    assert operations.calls[:2] == ["scan.frame", "scan.prelude"]
    assert operations.calls[-4:] == ["scan.assemble", "scan.gate4", "scan.usage", "scan.observe"]
    book = json.loads((Path(handle.staging) / "_l4_tasks.json").read_text(encoding="utf-8"))
    assert book["tasks"][CODE]["attempt"] == 1
    l3 = next(request for request in models.requests if request.role == "scan.l3")
    assert "按质 7~10 只" in l3.prompt
    card = next(request for request in models.requests if request.role == "scan.l4.card")
    assert card.prompt.startswith("执行 ") and f"details/{CODE}.md" in card.prompt


def test_synthetic_scan_with_intel_repair_and_independent_review(tmp_path, monkeypatch):
    from autoresearch.session_agent import host_evidence

    handle = _scan_run(tmp_path, monkeypatch,
                       host=profile(independent_context=True, web_search=True),
                       user_config={"l4_intel": {"enabled": True}})
    monkeypatch.setattr(host_evidence, "resolve_receipt_evidence",
                        lambda current, task, receipt: [receipt])
    operations = _FakeScanOperations(handle, branchy=True)
    models = _FakeScanModels(branchy=True)
    hooks = ServiceHooks(
        handle_loader=lambda run_id: handle, operation_runner=operations,
        event_recorder=lambda *args, **kwargs: None, validator=None,
        publisher=lambda current: None, finalizer=lambda current, report: {"ok": True})
    final = runner.run_loop(RUN_ID, models, max_parallel=4, poll_seconds=0.01,
                            max_rounds=3000, hooks=hooks)
    assert final["finished"] is True, (final["stop_reason"], final["errors"])
    roles = [request.role for request in models.requests]
    assert roles.count("scan.l4.review") == 1                   # same-tier early stop
    assert {"scan.l3.repair", "scan.l4.intel"} <= set(roles)
    assert {"scan.l3.repair.apply", "scan.l4.intel.status", "scan.review.decide"} <= set(
        operations.calls)
    assert "scan.review.none" not in operations.calls
    review = next(request for request in models.requests if request.role == "scan.l4.review")
    assert review.prompt.startswith("独立复核 run2(不知道其它 run 结论)")
    assert review.independent_context is True
    intel = next(request for request in models.requests if request.role == "scan.l4.intel")
    assert "已知底" in intel.prompt and "≤20 条" in intel.prompt
    receipts = list((Path(handle.capsule) / "agents/session/host_receipts").glob("*.json"))
    assert len(receipts) == 1


def test_graph_is_not_read_while_an_expansion_is_being_synced(tmp_path, monkeypatch):
    """An execute persists its expansion before syncing artifacts/store; a ticket claimed
    inside that window takes the taskbook but loses its frozen claim evidence."""
    import time

    handle = _scan_run(tmp_path, monkeypatch)
    original = service._sync_expansion

    def slow_sync(current, request, expansion):
        time.sleep(0.2)
        return original(current, request, expansion)

    monkeypatch.setattr(service, "_sync_expansion", slow_sync)
    hooks = ServiceHooks(
        handle_loader=lambda run_id: handle, operation_runner=_FakeScanOperations(handle),
        event_recorder=lambda *args, **kwargs: None, validator=None,
        publisher=lambda current: None, finalizer=lambda current, report: {"ok": True})
    final = runner.run_loop(RUN_ID, _FakeScanModels(), max_parallel=4, poll_seconds=0.01,
                            max_rounds=5000, hooks=hooks)
    assert final["finished"] is True, (final["stop_reason"], final["errors"])
    assert final["skipped"] == []
    claim = (Path(handle.capsule) / "evidence/attempt_records" / f"l4.{CODE}.a1" / "a1"
             / "claim.json")
    assert claim.is_file()


def test_a_failed_expansion_sync_stops_the_run_instead_of_crashing_the_runner(
        tmp_path, monkeypatch):
    """Expansion persisted, store never synced: READY tasks the store does not know."""
    handle = _scan_run(tmp_path, monkeypatch)
    original = service._sync_expansion

    def broken_sync(current, request, expansion):
        if expansion["template_id"] == "scan.l3":
            raise RuntimeError("simulated crash between persist and store sync")
        return original(current, request, expansion)

    monkeypatch.setattr(service, "_sync_expansion", broken_sync)
    hooks = ServiceHooks(
        handle_loader=lambda run_id: handle, operation_runner=_FakeScanOperations(handle),
        event_recorder=lambda *args, **kwargs: None, validator=None,
        publisher=lambda current: None, finalizer=lambda current, report: {"ok": True})
    final = runner.run_loop(RUN_ID, _FakeScanModels(), max_parallel=4, poll_seconds=0.01,
                            max_rounds=200, hooks=hooks)
    assert final["finished"] is False
    assert final["stop_reason"] == "STALLED"
    assert any("simulated crash" in str(error.get("message")) for error in final["errors"])


def test_synthetic_sentinel_empty_scan_takes_only_skip_operations(tmp_path, monkeypatch):
    handle = _scan_run(tmp_path, monkeypatch)
    operations = _FakeScanOperations(handle, mode="SENTINEL_EMPTY")
    models = _FakeScanModels()
    hooks = ServiceHooks(
        handle_loader=lambda run_id: handle, operation_runner=operations,
        event_recorder=lambda *args, **kwargs: None, validator=None,
        publisher=lambda current: None, finalizer=lambda current, report: {"ok": True})
    final = runner.run_loop(RUN_ID, models, max_parallel=4, poll_seconds=0.01,
                            max_rounds=2000, hooks=hooks)
    assert final["finished"] is True, (final["stop_reason"], final["errors"])
    assert [request.role for request in models.requests] == ["macro.brief"]
    assert operations.calls == [
        "scan.frame", "scan.prelude", "scan.gate1", "scan.sector.skip", "scan.gate2.skip",
        "scan.l4.skip", "scan.review.skip", "scan.review3.skip", "scan.assemble",
        "scan.gate4", "scan.usage", "scan.observe"]


def test_synthetic_sentinel_pinned_scan_reviews_holdings_and_finishes(tmp_path, monkeypatch):
    handle = _scan_run(tmp_path, monkeypatch)
    operations = _FakeScanOperations(handle, mode="SENTINEL_PINNED")
    models = _FakeScanModels()
    hooks = ServiceHooks(
        handle_loader=lambda run_id: handle, operation_runner=operations,
        event_recorder=lambda *args, **kwargs: None, validator=None,
        publisher=lambda current: None, finalizer=lambda current, report: {"ok": True})
    final = runner.run_loop(RUN_ID, models, max_parallel=4, poll_seconds=0.01,
                            max_rounds=2000, hooks=hooks)
    assert final["finished"] is True, (final["stop_reason"], final["errors"])
    assert [request.role for request in models.requests] == ["macro.brief", "scan.l4.card"]
    assert "scan.l3.prepare" not in operations.calls and "scan.gate2.skip" in operations.calls


def _closure_missing(handle) -> list[str]:
    return json.loads((Path(handle.capsule) / "verification/evidence_closure.json")
                      .read_text(encoding="utf-8"))["missing"]


def _attempt_agnostic(missing) -> set[str]:
    """One logical gap per task: ``l4.X.a2.card:a1`` and ``l4.X.a1.card:a1`` compare equal,
    so a retry run may only repeat gaps the harness already has without a retry."""
    result = set()
    for item in missing:
        value = re.sub(r"\.a\d+(?=[.:]|$)", ".aN", item)
        value = re.sub(r"-a\d+(?=[-:]|$)", "-aN", value)
        result.add(re.sub(r":a\d+(?=:|$)", ":aK", value))
    return result


def _baseline_missing(tmp_path, monkeypatch, *, branchy=False, host=None, user_config=None):
    """The same scan without any retry (same harness gaps, same transcripts)."""
    from autoresearch.session_agent import host_evidence

    handle = _scan_run(tmp_path / "baseline", monkeypatch, host=host, user_config=user_config)
    if branchy:
        monkeypatch.setattr(host_evidence, "resolve_receipt_evidence",
                            lambda current, task, receipt: [receipt])
    final = runner.run_loop(
        RUN_ID, _FakeScanModels(branchy=branchy, transcripts=tmp_path / "baseline_transcripts"),
        max_parallel=4, poll_seconds=0.01, max_rounds=3000,
        hooks=_plain_hooks(handle, _FakeScanOperations(handle, branchy=branchy)))
    assert final["finished"] is True, (final["stop_reason"], final["errors"])
    missing = _closure_missing(handle)
    assert not any(item.startswith("TRANSCRIPT_MISSING:l4.") and ".card:" in item
                   for item in missing), "harness must bind card transcripts"
    return missing


def _abandonment(handle, task_id: str, attempt: int) -> dict:
    path = (Path(handle.capsule) / "evidence/attempt_records" / task_id / f"a{attempt}"
            / "abandoned.json")
    return json.loads(path.read_text(encoding="utf-8"))


def test_l4_card_timeout_drives_one_taskbook_retry_to_finish(tmp_path, monkeypatch):
    from autoresearch.session_agent.executors.base import ExecutorTimeout

    baseline = _baseline_missing(tmp_path, monkeypatch)
    handle = _scan_run(tmp_path, monkeypatch)
    operations = _FakeScanOperations(handle)
    models = _FakeScanModels(transcripts=tmp_path / "host_transcripts")
    original = models.dispatch

    def dispatch(request):
        if request.task_id == f"l4.{CODE}.a1.card":
            models.requests.append(request)
            raise ExecutorTimeout(f"等待 {request.task_id} 的结果文件超时")
        return original(request)

    models.dispatch = dispatch
    hooks = ServiceHooks(
        handle_loader=lambda run_id: handle, operation_runner=operations,
        event_recorder=lambda *args, **kwargs: None, validator=None,
        publisher=lambda current: None, finalizer=lambda current, report: {"ok": True})
    final = runner.run_loop(RUN_ID, models, max_parallel=4, poll_seconds=0.01,
                            max_rounds=3000, hooks=hooks)
    assert final["finished"] is True, (final["stop_reason"], final["errors"], final["skipped"])
    cards = [request.task_id for request in models.requests if request.role == "scan.l4.card"]
    assert cards == [f"l4.{CODE}.a1.card", f"l4.{CODE}.a2.card"]      # exactly one retry
    book = json.loads((Path(handle.staging) / "_l4_tasks.json").read_text(encoding="utf-8"))
    assert book["tasks"][CODE]["attempt"] == 2
    promoted = (Path(handle.staging) / "details" / f"{CODE}.md").read_text(encoding="utf-8")
    assert "**Rating**: Hold" in promoted                              # a2 card promoted
    # Review I4: the retry run adds no evidence gap the no-retry baseline lacks.
    missing = _closure_missing(handle)
    assert _attempt_agnostic(missing) - _attempt_agnostic(baseline) == set(), missing
    plan = json.loads((Path(handle.capsule) / "evidence/evidence_plan.json").read_text("utf-8"))
    tickets = {key["task_id"]: key["attempt"] for key in plan["task_keys"]
               if key["owner"] == "L4_TASKBOOK"}
    assert tickets == {f"l4.{CODE}.a1": 1, f"l4.{CODE}.a2": 2}      # keyed by the id's attempt
    abandoned = _abandonment(handle, f"l4.{CODE}.a1.card", 1)
    assert abandoned["status"] == "ABANDONED" and "超时" in abandoned["reason"]


def test_l4_card_timeout_with_intel_keeps_the_bound_a1_intel_intact(tmp_path, monkeypatch):
    """The a2 intel must not overwrite the bound a1 intel file (review I4 b)."""
    from autoresearch.session_agent import host_evidence
    from autoresearch.session_agent.executors.base import ExecutorTimeout

    host = profile(independent_context=True, web_search=True)
    config = {"l4_intel": {"enabled": True}}
    baseline = _baseline_missing(tmp_path, monkeypatch, branchy=True, host=host,
                                 user_config=config)
    handle = _scan_run(tmp_path, monkeypatch, host=host, user_config=config)
    monkeypatch.setattr(host_evidence, "resolve_receipt_evidence",
                        lambda current, task, receipt: [receipt])
    models = _FakeScanModels(branchy=True, transcripts=tmp_path / "host_transcripts")
    original = models.dispatch

    def dispatch(request):
        if request.task_id == f"l4.{CODE}.a1.card":
            raise ExecutorTimeout(f"等待 {request.task_id} 的结果文件超时")
        return original(request)

    models.dispatch = dispatch
    final = runner.run_loop(RUN_ID, models, max_parallel=4, poll_seconds=0.01, max_rounds=3000,
                            hooks=_plain_hooks(handle, _FakeScanOperations(handle, branchy=True)))
    assert final["finished"] is True, (final["stop_reason"], final["errors"], final["skipped"])
    intel = [request.task_id for request in models.requests if request.role == "scan.l4.intel"]
    assert intel == [f"l4.{CODE}.a1.intel", f"l4.{CODE}.a2.intel"]
    with artifacts.open_artifact(handle, f"scan.l4.{CODE}.a1.intel") as stream:
        assert f"l4.{CODE}.a1.intel" in stream.read().decode("utf-8")   # a1 bytes untouched
    missing = _closure_missing(handle)
    assert _attempt_agnostic(missing) - _attempt_agnostic(baseline) == set(), missing


def test_session_task_timeout_retry_adds_no_evidence_gap(tmp_path, monkeypatch):
    """A SESSION retry (sector brief a1 abandoned) must not leave a1's missing transcript
    in the closure: no evidence can exist for work that was never reported (I4 c)."""
    from autoresearch.session_agent.executors.base import ExecutorTimeout

    baseline = _baseline_missing(tmp_path, monkeypatch)
    handle = _scan_run(tmp_path, monkeypatch)
    models = _FakeScanModels(transcripts=tmp_path / "host_transcripts")
    original = models.dispatch

    def dispatch(request):
        if request.role == "sector.brief" and request.attempt == 1:
            raise ExecutorTimeout(f"等待 {request.task_id} 的结果文件超时")
        return original(request)

    models.dispatch = dispatch
    final = runner.run_loop(RUN_ID, models, max_parallel=4, poll_seconds=0.01, max_rounds=3000,
                            hooks=_plain_hooks(handle, _FakeScanOperations(handle)))
    assert final["finished"] is True, (final["stop_reason"], final["errors"])
    briefs = [(r.task_id, r.attempt) for r in models.requests if r.role == "sector.brief"]
    assert [attempt for _, attempt in briefs] == [2]
    missing = _closure_missing(handle)
    assert _attempt_agnostic(missing) - _attempt_agnostic(baseline) == set(), missing
    assert _abandonment(handle, briefs[0][0], 1)["status"] == "ABANDONED"


def test_failed_l3_repair_degrades_to_the_original_judged_and_finishes(tmp_path, monkeypatch):
    """legacy scan-market.js:519–535: a repair that does not land → continue with the
    unrepaired judged set.  session_v1 supersedes repair/apply; GATE2 must still claim."""
    handle = _scan_run(tmp_path, monkeypatch,
                       host=profile(independent_context=True, web_search=True),
                       user_config={"l4_intel": {"enabled": True}})
    from autoresearch.session_agent import host_evidence

    monkeypatch.setattr(host_evidence, "resolve_receipt_evidence",
                        lambda current, task, receipt: [receipt])
    operations = _FakeScanOperations(handle, branchy=True)
    models = _FakeScanModels(branchy=True)
    original = models.dispatch

    def dispatch(request):
        if request.role == "scan.l3.repair":          # patch rows lack thesis → contract error
            models.requests.append(request)
            for path in request.output_paths.values():
                _write(Path(path), [{"code": CODE}])
            return DispatchResult(ok=True, session_ref="session-main",
                                  context_ref="agent-repair", parent_context_ref="session-main")
        return original(request)

    models.dispatch = dispatch
    hooks = ServiceHooks(
        handle_loader=lambda run_id: handle, operation_runner=operations,
        event_recorder=lambda *args, **kwargs: None, validator=None,
        publisher=lambda current: None, finalizer=lambda current, report: {"ok": True})
    final = runner.run_loop(RUN_ID, models, max_parallel=4, poll_seconds=0.01,
                            max_rounds=3000, hooks=hooks)
    assert final["finished"] is True, (final["stop_reason"], final["errors"], final["skipped"])
    assert "scan.l3.repair.apply" not in operations.calls
    repair = json.loads((Path(handle.staging) / "session_outputs/l3.repair.json").read_text("utf-8"))
    assert repair["status"] == "DEGRADED" and repair["preserved_original"] is True


def test_review_timeout_stops_blocked_without_respending_intel_and_card(tmp_path, monkeypatch):
    """Review I2: retry-l4 rebuilds ticket/slim/intel/card only, never the review —
    a review TASK_ATTEMPT failure must stop the run BLOCKED with REVIEW_FAILED, not
    spend another intel + card and then stall on a missing ensemble file."""
    from autoresearch.session_agent import host_evidence
    from autoresearch.session_agent.executors.base import ExecutorTimeout

    handle = _scan_run(tmp_path, monkeypatch,
                       host=profile(independent_context=True, web_search=True),
                       user_config={"l4_intel": {"enabled": True}})
    monkeypatch.setattr(host_evidence, "resolve_receipt_evidence",
                        lambda current, task, receipt: [receipt])
    models = _FakeScanModels(branchy=True)
    original = models.dispatch

    def dispatch(request):
        if request.role == "scan.l4.review":
            models.requests.append(request)
            raise ExecutorTimeout(f"等待 {request.task_id} 的结果文件超时")
        return original(request)

    models.dispatch = dispatch
    hooks = ServiceHooks(
        handle_loader=lambda run_id: handle, operation_runner=_FakeScanOperations(handle, branchy=True),
        event_recorder=lambda *args, **kwargs: None, validator=None,
        publisher=lambda current: None, finalizer=lambda current, report: {"ok": True})
    final = runner.run_loop(RUN_ID, models, max_parallel=4, poll_seconds=0.01,
                            max_rounds=3000, hooks=hooks)
    assert final["stop_reason"] == "BLOCKED", (final["stop_reason"], final["errors"])
    l4 = [request.task_id for request in models.requests
          if request.role in {"scan.l4.intel", "scan.l4.card", "scan.l4.review"}]
    assert l4 == [f"l4.{CODE}.a1.intel", f"l4.{CODE}.a1.card", f"l4.{CODE}.a1.review2"]
    assert any(error.get("code") == "REVIEW_FAILED:TIMEOUT" and error.get("subject") == CODE
               for error in final["errors"]), final["errors"]
    assert final["skipped"] == []


class _RunnerCrash(BaseException):
    """Stands in for SIGKILL/OOM: nothing in the runner may catch it."""


def _plain_hooks(handle, operations):
    return ServiceHooks(
        handle_loader=lambda run_id: handle, operation_runner=operations,
        event_recorder=lambda *args, **kwargs: None, validator=None,
        publisher=lambda current: None, finalizer=lambda current, report: {"ok": True})


def test_restart_retries_an_l4_ticket_that_failed_before_the_crash(tmp_path, monkeypatch):
    """Review I3: the retry-l4 intent must come from the taskbook, not runner memory."""
    import pytest

    from autoresearch.session_agent.executors.base import ExecutorTimeout

    handle = _scan_run(tmp_path, monkeypatch)
    operations = _FakeScanOperations(handle)
    models = _FakeScanModels()
    original = models.dispatch

    def dispatch(request):
        if request.task_id == f"l4.{CODE}.a1.card":
            models.requests.append(request)
            raise ExecutorTimeout("timed out")
        return original(request)

    models.dispatch = dispatch
    real_retry = service.retry_l4

    def crash(*args, **kwargs):
        raise _RunnerCrash()

    monkeypatch.setattr(service, "retry_l4", crash)
    with pytest.raises(_RunnerCrash):
        runner.run_loop(RUN_ID, models, max_parallel=4, poll_seconds=0.01, max_rounds=3000,
                        hooks=_plain_hooks(handle, operations))
    monkeypatch.setattr(service, "retry_l4", real_retry)
    final = runner.run_loop(RUN_ID, models, max_parallel=4, poll_seconds=0.01, max_rounds=3000,
                            hooks=_plain_hooks(handle, operations))
    assert final["finished"] is True, (final["stop_reason"], final["errors"], final["skipped"])
    cards = [request.task_id for request in models.requests if request.role == "scan.l4.card"]
    assert cards == [f"l4.{CODE}.a1.card", f"l4.{CODE}.a2.card"]


def test_restart_does_not_turn_a_review_failure_into_an_l4_retry(tmp_path, monkeypatch):
    from autoresearch.session_agent import host_evidence
    from autoresearch.session_agent.executors.base import ExecutorTimeout

    handle = _scan_run(tmp_path, monkeypatch,
                       host=profile(independent_context=True, web_search=True),
                       user_config={"l4_intel": {"enabled": True}})
    monkeypatch.setattr(host_evidence, "resolve_receipt_evidence",
                        lambda current, task, receipt: [receipt])
    models = _FakeScanModels(branchy=True)
    original = models.dispatch

    def dispatch(request):
        if request.role == "scan.l4.review":
            raise ExecutorTimeout("timed out")
        return original(request)

    models.dispatch = dispatch
    operations = _FakeScanOperations(handle, branchy=True)
    first = runner.run_loop(RUN_ID, models, max_parallel=4, poll_seconds=0.01,
                            max_rounds=3000, hooks=_plain_hooks(handle, operations))
    assert first["stop_reason"] == "BLOCKED"
    before = len(models.requests)
    second = runner.run_loop(RUN_ID, models, max_parallel=4, poll_seconds=0.01,
                             max_rounds=3000, hooks=_plain_hooks(handle, operations))
    assert second["stop_reason"] == "BLOCKED"
    assert len(models.requests) == before                       # nothing re-spent
    assert any(error.get("code") == "REVIEW_FAILED:TIMEOUT" for error in second["errors"])


def test_unresolvable_l4_retry_stops_the_run_instead_of_spinning(tmp_path, monkeypatch):
    """retry-l4 refused as 'not quiescent' while none of that ticket's children is in
    flight here means a child is RUNNING without a live owner: report, do not spin."""
    from autoresearch.session_agent.executors.base import ExecutorTimeout

    handle = _scan_run(tmp_path, monkeypatch)
    models = _FakeScanModels()
    original = models.dispatch

    def dispatch(request):
        if request.task_id == f"l4.{CODE}.a1.card":
            raise ExecutorTimeout("timed out")
        return original(request)

    models.dispatch = dispatch

    def refuse(run_id, code, attempt, **kwargs):
        raise RuntimeError("L4 retry requires every previous child to be quiescent")

    monkeypatch.setattr(service, "retry_l4", refuse)
    hooks = ServiceHooks(
        handle_loader=lambda run_id: handle, operation_runner=_FakeScanOperations(handle),
        event_recorder=lambda *args, **kwargs: None, validator=None,
        publisher=lambda current: None, finalizer=lambda current, report: {"ok": True})
    final = runner.run_loop(RUN_ID, models, max_parallel=4, poll_seconds=0.01,
                            max_rounds=400, hooks=hooks)
    assert final["stop_reason"] == "BLOCKED"
    assert any("quiescent" in str(error.get("message")) for error in final["errors"])
