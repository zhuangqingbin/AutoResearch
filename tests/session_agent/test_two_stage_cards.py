"""The optional two-stage card has a frozen facts boundary and immutable initial."""
import base64
import json
from pathlib import Path

import pytest

from autoresearch.common.atomic import sha256_bytes
from autoresearch.session_agent import artifacts, workflows
from autoresearch.session_agent.workflows import scan, stock

from .test_scan_prelude import context, request as scan_request
from .test_service import _handle, _request


def candidate(request=None):
    value = dict(request or _request(), schema_version=2, card_research_profile="two-stage-v1")
    value["host_profile"] = dict(value["host_profile"], independent_context=True)
    return value


def test_stock_candidate_has_two_independent_calls_and_default_stays_single(tmp_path):
    handle = _handle(tmp_path)
    baseline = stock.build_stock_plan(_request(), handle)
    assert [t["task_id"] for t in baseline["tasks"]] == [
        "stock.harvest", "stock.card", "stock.validate", "stock.publish"]
    plan = workflows.build_plan(candidate(), handle)
    tasks = {t["task_id"]: t for t in plan["tasks"]}
    assert "stock.initial" in tasks
    assert tasks["stock.initial"]["role"] == tasks["stock.card"]["role"] == "stock.card"
    assert tasks["stock.initial"]["independent_context"]
    assert tasks["stock.card"]["independent_context"]
    assert tasks["stock.initial"]["input_artifact_ids"] == ["stock.card.facts", "research.frame"]
    assert tasks["stock.initial"]["expected_output_contract"] == "research.card.initial.v1"
    assert tasks["stock.card"]["expected_output_contract"] == "research.card.decision.v1"
    assert tasks["stock.card"]["output_artifact_ids"] == ["stock.card.output", "stock.card.changes"]
    assert "stock.initial" in tasks["stock.card"]["dependencies"]


def test_scan_candidate_mode_is_frozen_in_expander_identity(tmp_path):
    handle = context(tmp_path)
    base = scan.build_scan_plan(scan_request(), handle)
    plan = scan.build_scan_plan(candidate(scan_request()), handle)
    assert next(t for t in base["task_templates"] if t["template_id"] == "scan.l4")["expander"] == "scan.l4"
    assert next(t for t in plan["task_templates"] if t["template_id"] == "scan.l4")["expander"] == "scan.l4.two-stage-v1"
    rows = [{"code": "600519", "lane": "pinned"}]
    result = scan.l4_expansion(plan, {"mode": "FULL"}, rows,
                               [{"artifact_id": "scan.finalists", "sha256": "a" * 64}], intel_enabled=False)
    tasks = {t["task_id"]: t for t in result["tasks"]}
    initial = tasks["l4.600519.a1.initial"]
    assert initial["role"] == tasks["l4.600519.a1.card"]["role"]
    assert initial["input_artifact_ids"] == ["scan.l4.600519.a1.facts", "scan.l4.600519.a1.deep"]
    assert sum(t["owner"] == "L4_TASKBOOK" for t in tasks.values()) == 1
    assert "l4.600519.a1.card" in tasks["scan.review.plan.600519"]["dependencies"]


def test_projection_keeps_raw_risks_and_lineage_but_excludes_prior_members():
    from autoresearch.session_agent.card_facts import project_facts

    forbidden = ["UNIQUE_CONVICTION_99", "UNIQUE_L3_THESIS", "UNIQUE_OLD_BUY", "UNIQUE_RECALL_RANK"]
    encode = lambda text: base64.b64encode(text.encode()).decode()
    bundle = {"schema_version": 1, "phase": "l4_source", "files": {
        "finalists.csv": encode("code,lane,conviction,thesis\n600519,pinned," + ",".join(forbidden[:2]) + "\n"),
        "_l4_prompt_600519.md": encode(" ".join(forbidden)),
        "L2_gbdt_top200.csv": encode(" ".join(forbidden[2:])),
        "pledge.csv": encode("code,pledge_ratio,end_date\n600519,98,20260912\n"),
        "calendar.csv": encode("code,date,event\n600519,20260915,material risk\n"),
    }}
    raw = {"scan.l4.600519.a1.slim": b"raw price 100; risk lawsuit; https://example.com/report",
           "scan.l4.600519.a1.deep": b"raw debt 999; insolvency warning",
           "scan.l4.source.bundle": json.dumps(bundle).encode(), "research.frame": b"{}"}
    value = project_facts("600519", raw)
    text = json.dumps(value, ensure_ascii=False)
    assert not any(marker in text for marker in forbidden)
    assert "raw debt 999" in text and "risk lawsuit" in text and "98" in text and "material risk" in text
    for source in value["sources"]:
        assert source["sha256"] == sha256_bytes(source["content"].encode())
        assert source["source_artifact_id"] in raw
        assert source["source_sha256"] == sha256_bytes(raw[source["source_artifact_id"]])


def test_retry_reuses_successful_initial_and_its_facts(tmp_path):
    plan = scan.build_scan_plan(candidate(scan_request()), context(tmp_path))
    result = scan.l4_retry_expansion(plan, "600519", 2,
        [{"artifact_id": "scan.l4.600519.a1.initial", "sha256": "a" * 64}],
        intel_enabled=False, retained_initial={
            "task_id": "l4.600519.a1.initial",
            "input_artifact_ids": ["scan.l4.600519.a1.facts", "research.frame"],
            "output_artifact_ids": ["scan.l4.600519.a1.initial"],
        })
    tasks = {t["task_id"]: t for t in result["tasks"]}
    assert not any(name.endswith((".initial", ".facts")) for name in tasks)
    final = tasks["l4.600519.a2.card"]
    assert "scan.l4.600519.a1.initial" in final["input_artifact_ids"]
    assert "scan.l4.600519.a1.facts" in final["input_artifact_ids"]
    assert "l4.600519.a1.initial" in final["dependencies"]


def _assessment(handle, subject="600519.SS", fact_id="stock.card.facts"):
    from autoresearch.contracts.agent_output import OW_GATES, RUBRIC_DIMENSIONS
    return {"schema_version": 1, "subject": subject,
            "frame_hash": artifacts.snapshot_artifact(handle, "research.frame")["sha256"],
            "fact_manifest_hash": artifacts.snapshot_artifact(handle, fact_id)["sha256"],
            "initial_dimensions": dict.fromkeys(RUBRIC_DIMENSIONS, "未核"),
            "initial_gates": dict.fromkeys(OW_GATES, "UNKNOWN"),
            "initial_rating": "Hold", "key_risks": ["raw risk"],
            "missing_evidence": ["deep not read"], "evidence_refs": ["fact.1"]}


def _write(handle, artifact_id, value):
    if artifacts.layout_version(handle) >= 2:
        from autoresearch.session_agent import service, store
        task = next(row for row in service._all_tasks(handle) if artifact_id in row['output_artifact_ids'])
        entry = store.read_entry(handle.workspace / 'session/tasks.json', task['task_id'])
        path = Path(artifacts.output_paths(handle, task, max(1, entry['attempt']))[artifact_id])
    else:
        path = artifacts.artifact_path(handle, artifact_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False))
    if artifacts.layout_version(handle) >= 2:
        return {'sha256': sha256_bytes(path.read_bytes())}
    return artifacts.bind_artifact_hash(handle, artifact_id)


def _registered_stock(tmp_path):
    from autoresearch.session_agent.card_facts import build_registered_facts
    from autoresearch.session_agent.decision_frame import register_frame
    handle = _handle(tmp_path)
    req = candidate()
    plan = workflows.build_plan(req, handle)
    register_frame(req, handle, calendar_loader=lambda *_: ([], "UNKNOWN"))
    stock.register_stock_artifacts(req, handle, plan)
    _write(handle, "stock.slim", "Raw price 100 and material risk https://example.com/filing")
    _write(handle, "stock.deep", "Raw debt 500 and cash 1")
    tasks = {t["task_id"]: t for t in plan["tasks"]}
    build_registered_facts(handle, tasks["stock.card.facts"])
    artifacts.bind_artifact_hash(handle, "stock.card.facts")
    return handle, tasks


@pytest.mark.parametrize("field,value", [("subject", "NVDA"), ("frame_hash", "0" * 64),
    ("fact_manifest_hash", "0" * 64), ("evidence_refs", ["invented-source"])])
def test_initial_submit_binds_subject_hashes_and_evidence(tmp_path, field, value):
    from autoresearch.session_agent.validation import (
        DomainValidationError,
        validate_registered_contract,
    )
    handle, tasks = _registered_stock(tmp_path)
    initial = _assessment(handle)
    initial[field] = value
    _write(handle, "stock.card.initial", initial)
    with pytest.raises(DomainValidationError):
        validate_registered_contract(handle, {"outputs": [{"artifact_id": "stock.card.initial"}]}, tasks["stock.initial"])


def test_initial_uses_its_contract_before_scan_card_dispatch(tmp_path):
    from autoresearch.session_agent.validation import validate_registered_contract
    handle, tasks = _registered_stock(tmp_path)
    _write(handle, "stock.card.initial", _assessment(handle))
    task = dict(tasks["stock.initial"], role="scan.l4.card")
    validate_registered_contract(handle, {"outputs": [{"artifact_id": "stock.card.initial"}]}, task)


@pytest.mark.parametrize("changes", [[], ["rating"], ["dimensions.基本面"]])
def test_decision_sidecar_requires_exact_actual_diff(tmp_path, changes):
    from autoresearch.session_agent.validation import (
        DomainValidationError,
        validate_registered_contract,
    )
    handle, tasks = _registered_stock(tmp_path)
    original = _write(handle, "stock.card.initial", _assessment(handle))
    _write(handle, "stock.card.output", "600519.SS\n**Rating**: Underweight\n"
           "**早停**: 停于 P3 ｜ 停因:其他\nFINAL TRANSACTION PROPOSAL: **SELL**\n")
    _write(handle, "stock.card.changes", {"schema_version": 1, "subject": "600519.SS",
        "initial_hash": original["sha256"], "changed_fields": changes,
        "change_reason": "Corrected interpretation of existing facts", "new_evidence_refs": []})
    submission = {"outputs": [{"artifact_id": key} for key in tasks["stock.card"]["output_artifact_ids"]]}
    if changes == ["rating"]:
        validate_registered_contract(handle, submission, tasks["stock.card"])
    else:
        with pytest.raises(DomainValidationError, match="changed_fields"):
            validate_registered_contract(handle, submission, tasks["stock.card"])


def test_initial_dispatch_actual_allowlist_and_prompt_do_not_expose_priors(tmp_path):
    from autoresearch.session_agent.card_facts import build_registered_facts
    from autoresearch.session_agent.decision_frame import register_frame
    from autoresearch.session_agent.dispatch import build_request
    handle = _handle(tmp_path)
    req = candidate(dict(scan_request(), analysis_date=handle.analysis_date))
    register_frame(req, handle, calendar_loader=lambda *_: ([], "UNKNOWN"))
    plan = scan.build_scan_plan(req, handle)
    expansion = scan.l4_expansion(plan, {"mode": "FULL"}, [{"code": "600519"}],
        [{"artifact_id": "scan.finalists", "sha256": "a" * 64}], intel_enabled=False)
    tasks = {t["task_id"]: t for t in expansion["tasks"]}
    from autoresearch.session_agent.decision_frame import attach_expansion
    expansion = attach_expansion(expansion, artifacts.snapshot_artifact(handle, "research.frame"))
    tasks = {t["task_id"]: t for t in expansion["tasks"]}
    # Register the actual task allowlist paths, plus contaminated legacy producers.
    ids = set(tasks["l4.600519.a1.facts"]["input_artifact_ids"])
    ids |= set(tasks["l4.600519.a1.initial"]["input_artifact_ids"])
    ids |= set(tasks["l4.600519.a1.initial"]["output_artifact_ids"])
    for artifact_id in ids - {"research.frame"}:
        path, _ = scan._paths_for_artifact(handle, tasks["l4.600519.a1.initial"], artifact_id)
        artifacts.register_artifact(handle, artifact_id, path, "WRITE")
    markers = "UNIQUE_CONVICTION_99 UNIQUE_L3_THESIS UNIQUE_OLD_BUY UNIQUE_RECALL_RANK UNIQUE_FORCE_FULL"
    encode = lambda text: base64.b64encode(text.encode()).decode()
    _write(handle, "scan.l4.source.bundle", {"schema_version": 1, "phase": "l4_source", "files": {
        "finalists.csv": encode("code,lane,thesis\n600519,main," + markers + "\n"),
        "_l4_prompt_600519.md": encode(markers),
        "market_view.md": encode(markers),
        "pledge.csv": encode("code,pledge_ratio\n600519,99\n"),
    }})
    _write(handle, "scan.l4.600519.a1.slim", "raw facts and material risk")
    _write(handle, "scan.l4.600519.a1.deep", "raw deep facts")
    build_registered_facts(handle, tasks["l4.600519.a1.facts"])
    artifacts.bind_artifact_hash(handle, "scan.l4.600519.a1.facts")
    dispatch = build_request(handle, tasks["l4.600519.a1.initial"], 1, host_profile=req["host_profile"])
    text = dispatch.prompt
    from pathlib import Path
    text += "\n".join(Path(path).read_text() for path in dispatch.input_paths.values())
    assert not any(marker in text for marker in markers.split())
    assert "research.card.initial.v1" in dispatch.prompt
    assert not any(key.endswith((".prompt", ".source.bundle")) or key == "scan.finalists"
                   for key in dispatch.input_paths)


def test_real_stock_begin_claim_submit_resume_and_distinct_context(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from autoresearch.common.atomic import canonical_json
    from autoresearch.session_agent import host_evidence, service, store
    from autoresearch.session_agent.card_facts import build_registered_facts

    handle = _handle(tmp_path)
    req = candidate()
    service.begin(req, begin_capsule=lambda _: handle)
    loader = lambda _: handle
    noop = lambda *args, **kwargs: None
    # Only exported-host resolution is a synthetic fixture. The workflow,
    # envelopes, receipts, artifact binding and domain submit gates are real.
    monkeypatch.setattr(host_evidence, "resolve_receipt_evidence", lambda *args: [])
    result = SimpleNamespace(exit_code=0, invocation={"status": "COMPLETED"})
    service.claim(handle.run_id, "stock.harvest", 1, handle_loader=loader)
    def harvest(*args, **kwargs):
        for key, text in (("stock.slim", "raw price 100 and risk"), ("stock.deep", "raw debt 500")):
            path = artifacts.declared_path(handle, key)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        return result
    service.execute(handle.run_id, "stock.harvest", 1,
        {"ticker": req["subject"], "analysis_date": req["analysis_date"], "asset_type": "stock", "peers": [], "slim": True},
        handle_loader=loader, runner=harvest)
    service.claim(handle.run_id, "stock.card.facts", 1, handle_loader=loader)
    def facts_runner(*args, **kwargs):
        build_registered_facts(handle, service._task(handle, "stock.card.facts"))
        return result
    service.execute(handle.run_id, "stock.card.facts", 1, {}, handle_loader=loader, runner=facts_runner)

    def submit(task_id, context_ref, claimed):
        task = service._task(handle, task_id)
        receipt = {"schema_version": 1, "engine": "codex", "session_ref": "session-main",
            "context_ref": context_ref, "parent_context_ref": "session-main", "task_id": task_id,
            "attempt": 1, "completed": True, "evidence_refs": ["host-binding:" + "1" * 64]}
        submission = {"schema_version": 1, "envelope": claimed["result"]["envelope"],
            "plan_hash": claimed["result"]["plan_hash"],
            "outputs": [{"artifact_id": key, "sha256": sha256_bytes(Path(artifacts.output_paths(handle, task, 1)[key]).read_bytes())}
                        for key in task["output_artifact_ids"]],
            "host_receipt_id": sha256_bytes(canonical_json(receipt).encode())}
        return service.submit(handle.run_id, submission, handle_loader=loader, host_receipt=receipt, event_recorder=noop)

    first = service.claim(handle.run_id, "stock.initial", 1, handle_loader=loader, event_recorder=noop)
    original = _write(handle, "stock.card.initial", _assessment(handle))
    submit("stock.initial", "initial-context", first)
    with pytest.raises(artifacts.ArtifactConflict, match='no accepted generation'):
        artifacts.artifact_path(handle, 'stock.card.output')
    assert store.read_states(handle.workspace / "session/tasks.json")["stock.publish"] == "PENDING"
    service.resume(handle.run_id, handle_loader=loader, event_recorder=noop)
    second = service.claim(handle.run_id, "stock.card", 1, handle_loader=loader, event_recorder=noop)
    from autoresearch.contracts.agent_output import OW_GATES, RUBRIC_DIMENSIONS
    from tests.common.test_card_decision_v3 import decision_text
    _write(handle, "stock.card.output", decision_text(subject="600519.SS", venue="XSHG", rating="Hold",
        dimensions=dict.fromkeys(RUBRIC_DIMENSIONS, "未核"), gates=dict.fromkeys(OW_GATES, "UNKNOWN")))
    _write(handle, "stock.card.changes", {"schema_version": 1, "subject": req["subject"],
        "initial_hash": original["sha256"], "changed_fields": [], "change_reason": "", "new_evidence_refs": []})
    with pytest.raises(ValueError, match="distinct from initial"):
        submit("stock.card", "initial-context", second)
    submit("stock.card", "decision-context", second)
    service.resume(handle.run_id, handle_loader=loader, event_recorder=noop)
    assert artifacts.snapshot_artifact(handle, "stock.card.initial")["sha256"] == original["sha256"]
    assert store.read_states(handle.workspace / "session/tasks.json")["stock.card"] == "SUCCEEDED"


def test_store_retry_preserves_successful_initial(tmp_path):
    from autoresearch.session_agent import store
    plan = scan.build_scan_plan(candidate(scan_request()), context(tmp_path))
    expansion = scan.l4_expansion(plan, {"mode": "FULL"}, [{"code": "600519"}],
        [{"artifact_id": "scan.finalists", "sha256": "a" * 64}], intel_enabled=False)
    path = tmp_path / "tasks.json"
    store.initialize(path, plan)
    store.register_tasks(path, expansion["tasks"], plan_hash=plan["plan_hash"])
    payload = json.loads(path.read_text())
    payload["tasks"]["l4.600519.a1.initial"]["state"] = "SUCCEEDED"
    path.write_text(json.dumps(payload))
    store.prepare_l4_retry(path, "600519", 1)
    assert store.read_states(path)["l4.600519.a1.initial"] == "SUCCEEDED"


def test_candidate_raw_attempt_paths_are_not_mutable_harvest_cache(tmp_path):
    handle = _handle(tmp_path)
    plan = scan.build_scan_plan(candidate(scan_request()), handle)
    expansion = scan.l4_expansion(plan, {"mode": "FULL"}, [{"code": "600519"}],
        [{"artifact_id": "scan.finalists", "sha256": "a" * 64}], intel_enabled=False)
    slim = next(t for t in expansion["tasks"] if t["task_id"].endswith(".slim"))
    for key in ("slim", "deep"):
        path, _ = scan._paths_for_artifact(handle, slim, f"scan.l4.600519.a1.{key}")
        assert path == handle.staging / f"session_attempts/600519/a1/{key}.md"


def test_single_stage_scan_retry_keeps_the_frozen_frame_on_the_a2_subtree(tmp_path):
    # 2026-10-03 production run 20261003T101330575299Z: every single-stage a2 card was rejected
    # (no research-decision-v2 block) because retry_l4 attached the frame only for two-stage plans,
    # so dispatch never rendered the decision window / card contract / claim population.
    from autoresearch.session_agent import legacy_scan, plan as plan_service, service, store
    from autoresearch.session_agent.decision_frame import attach_expansion, register_frame
    handle = _handle(tmp_path)
    req = dict(scan_request(), schema_version=2, card_research_profile="single-stage-v1")
    req["host_profile"] = dict(req["host_profile"], independent_context=True, web_search=True, web_fetch=True)
    handle.contract.run_kind = "scan-market"
    session = handle.workspace / "session"
    session.mkdir()
    plan = workflows.build_plan(req, handle)
    assert not scan.two_stage_plan(plan)
    register_frame(req, handle, calendar_loader=lambda *_: ([], "UNKNOWN"))
    for name, value in (("request", req), ("host_profile", req["host_profile"]), ("plan", plan)):
        (session / f"{name}.json").write_text(json.dumps(value))
    store.initialize(session / "tasks.json", plan)
    gate2 = scan._task("scan.gate2", "DETERMINISTIC", dependencies=["scan.gate1"],
        inputs=["scan.run_mode"], outputs=["scan.finalists"], contract="scan.gate2.v1", operation="scan.gate2.skip")
    prior = scan._expansion(plan, "scan.l3.repair", [{"artifact_id": "scan.run_mode", "sha256": "e" * 64}], [gate2])
    plan_service.persist_expansion(session, plan, prior, existing_tasks=plan["tasks"])
    store.register_tasks(session / "tasks.json", prior["tasks"], plan_hash=plan["plan_hash"])
    expansion = scan.l4_expansion(plan, {"mode": "FULL"}, [{"code": "600519"}],
        [{"artifact_id": "scan.finalists", "sha256": "f" * 64}], intel_enabled=True)
    expansion = attach_expansion(expansion, artifacts.snapshot_artifact(handle, "research.frame"))
    plan_service.persist_expansion(session, plan, expansion, existing_tasks=[*plan["tasks"], gate2])
    store.register_tasks(session / "tasks.json", expansion["tasks"], plan_hash=plan["plan_hash"])
    workflows.register_expansion_artifacts(req, handle, expansion)
    _write(handle, "scan.l4.600519.a1.prompt", "task pack")
    legacy_scan.initialize_tickets(handle, ["600519"])
    legacy_scan.claim_ticket(handle, "600519", 1)
    legacy_scan.fail_ticket(handle, "600519", 1, "TIMEOUT", "intel answered after its window")

    service.retry_l4(handle.run_id, "600519", 2, handle_loader=lambda _: handle)

    first = service._task(handle, "l4.600519.a1.card")
    retry = service._task(handle, "l4.600519.a2.card")
    assert "research.frame" in first["input_artifact_ids"]
    assert "research.frame" in retry["input_artifact_ids"]
    assert retry["expected_output_contract"] == first["expected_output_contract"]
    assert "research.frame" in service._task(handle, "l4.600519.a2.intel")["input_artifact_ids"]


@pytest.mark.parametrize("initial_succeeded", [True, False])
def test_scan_retry_service_uses_frozen_graph_and_reuses_only_successful_initial(tmp_path, initial_succeeded):
    from autoresearch.session_agent import legacy_scan, plan as plan_service, service, store
    handle = _handle(tmp_path)
    req = candidate(scan_request())
    handle.contract.run_kind = "scan-market"
    session = handle.workspace / "session"
    session.mkdir()
    plan = workflows.build_plan(req, handle)
    from autoresearch.session_agent.decision_frame import attach_expansion, register_frame
    register_frame(req, handle, calendar_loader=lambda *_: ([], "UNKNOWN"))
    for name, value in (("request", req), ("host_profile", req["host_profile"]), ("plan", plan)):
        (session / f"{name}.json").write_text(json.dumps(value))
    store.initialize(session / "tasks.json", plan)
    gate2 = scan._task("scan.gate2", "DETERMINISTIC", dependencies=["scan.gate1"],
        inputs=["scan.run_mode"], outputs=["scan.finalists"], contract="scan.gate2.v1", operation="scan.gate2.skip")
    prior = scan._expansion(plan, "scan.l3.repair", [{"artifact_id": "scan.run_mode", "sha256": "e" * 64}], [gate2])
    plan_service.persist_expansion(session, plan, prior, existing_tasks=plan["tasks"])
    store.register_tasks(session / "tasks.json", prior["tasks"], plan_hash=plan["plan_hash"])
    expansion = scan.l4_expansion(plan, {"mode": "FULL"}, [{"code": "600519", "lane": "pinned"}],
        [{"artifact_id": "scan.finalists", "sha256": "f" * 64}], intel_enabled=False)
    expansion = attach_expansion(expansion, artifacts.snapshot_artifact(handle, "research.frame"))
    plan_service.persist_expansion(session, plan, expansion, existing_tasks=[*plan["tasks"], gate2])
    store.register_tasks(session / "tasks.json", expansion["tasks"], plan_hash=plan["plan_hash"])
    workflows.register_expansion_artifacts(req, handle, expansion)
    _write(handle, "scan.l4.600519.a1.prompt", "legacy priors")
    old_hashes = {}
    for kind in ("facts", "initial", "deep"):
        artifact_id = f"scan.l4.600519.a1.{kind}"
        old_hashes[artifact_id] = _write(handle, artifact_id, f"frozen {kind}")["sha256"]
    states = json.loads((session / "tasks.json").read_text())
    states["tasks"]["l4.600519.a1.initial"]["state"] = "SUCCEEDED" if initial_succeeded else "FAILED"
    (session / "tasks.json").write_text(json.dumps(states))
    legacy_scan.initialize_tickets(handle, ["600519"])
    legacy_scan.claim_ticket(handle, "600519", 1)
    legacy_scan.fail_ticket(handle, "600519", 1, "TIMEOUT", "decision failed")
    # Mutable runtime config must neither downgrade the two-stage candidate nor
    # add an intel task absent from its frozen attempt graph.
    handle.contract.user_config = {"card_research_profile": "single-stage-v1", "l4_intel": {"enabled": True}}
    service.retry_l4(handle.run_id, "600519", 2, handle_loader=lambda _: handle)
    final = service._task(handle, "l4.600519.a2.card")
    assert "research.frame" in final["input_artifact_ids"]
    assert final["expected_output_contract"] == "research.card.decision.v1"
    assert not any(t["task_id"] == "l4.600519.a2.intel" for t in service._all_tasks(handle))
    if initial_succeeded:
        assert "scan.l4.600519.a1.initial" in final["input_artifact_ids"]
        assert store.read_states(session / "tasks.json")["l4.600519.a1.initial"] == "SUCCEEDED"
        assert not any(t["task_id"] == "l4.600519.a2.initial" for t in service._all_tasks(handle))
        for key, digest in old_hashes.items():
            assert artifacts.snapshot_artifact(handle, key)["sha256"] == digest
    else:
        assert "scan.l4.600519.a2.initial" in final["input_artifact_ids"]
        initial = service._task(handle, "l4.600519.a2.initial")
        assert "scan.l4.600519.a2.deep" in initial["input_artifact_ids"]


def test_facts_compute_replay_has_same_bytes_without_live_profile(tmp_path):
    from types import SimpleNamespace

    from autoresearch.session_agent.card_facts import replay
    from autoresearch.session_agent.replay_adapters.common import input_path
    handle, tasks = _registered_stock(tmp_path)
    unit = {"input_refs": [{"artifact_id": key} for key in tasks["stock.card.facts"]["input_artifact_ids"]],
            "expected_outputs": [{"artifact_id": "stock.card.facts"}]}
    output = tmp_path / "replayed.json"
    context = SimpleNamespace(inputs=tmp_path / "replay-inputs", unit=unit,
        operation_request={"subject": "600519.SS"}, output_path=lambda _: output)
    for row in unit["input_refs"]:
        target = input_path(context, row["artifact_id"])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(artifacts.artifact_path(handle, row["artifact_id"]).read_bytes())
    replay(unit, context)
    assert output.read_bytes() == artifacts.artifact_path(handle, "stock.card.facts").read_bytes()


def test_scan_candidate_requires_independent_context_at_begin(tmp_path):
    req = candidate(scan_request())
    req["host_profile"]["independent_context"] = False
    with pytest.raises(ValueError, match="independent_context"):
        scan.build_scan_plan(req, context(tmp_path))


def test_real_replay_unit_runs_scan_compute_without_task_fields_or_profile(tmp_path):
    from autoresearch.contracts.replay import validate_replay_unit
    from autoresearch.session_agent.replay_adapters.__main__ import main
    from autoresearch.session_agent.replay_adapters.common import safe_name
    task_id = "l4.600519.a1.intel_status"
    encode = lambda text: base64.b64encode(text.encode()).decode()
    payloads = {
        "session.request": json.dumps(candidate(scan_request())),
        f"operation.request:{task_id}:a1": json.dumps({"schema_version": 1,
            "task_id": task_id, "attempt": 1, "operation": "scan.l4.intel.disabled",
            "subject": "600519", "params": {}, "frozen_clock": "2026-09-13T00:00:00Z"}),
        "scan.l4.source.bundle": json.dumps({"schema_version": 1, "phase": "l4_source", "files": {
            "_l4_tasks.json": encode(json.dumps({"tasks": {"600519": {"attempt": 1, "status": "PENDING", "artifacts": {}}}})),
            "_l4_prompt_600519.md": encode("legacy task pack"),
        }}),
        "scan.l4.600519.a1.prompt": "legacy task pack",
        "research.frame": "{}",
    }
    output_ids = ["scan.l4.600519.a1.intel_status", "scan.l4.600519.a1.intel_bundle"]
    def ref(key):
        return {"artifact_id": key, "sha256": sha256_bytes(payloads.get(key, "").encode()), "captured_path": safe_name(key)}
    unit = validate_replay_unit({"unit_id": f"{task_id}:a1", "task_id": task_id,
        "attempt": 1, "operation": "scan.l4.intel.disabled", "mode": "COMPUTE",
        "dependencies": [], "input_refs": [ref(key) for key in payloads],
        "expected_outputs": [ref(key) for key in output_ids], "source_receipt_ids": [],
        "comparison_policy": {"policy": "CANONICAL_JSON", "version": 1, "ignored_fields": []},
        "failure_expectation": None})
    assert "subject" not in unit and "output_artifact_ids" not in unit
    inputs = tmp_path / "inputs/artifacts"
    inputs.mkdir(parents=True)
    for key, value in payloads.items():
        (inputs / safe_name(key)).write_text(value)
    request_file = tmp_path / "replay.json"
    result = tmp_path / "result.json"
    request_file.write_text(json.dumps({"run_id": "20260913T010203000000Z", "unit": unit,
        "inputs": str(inputs.parent), "work": str(tmp_path / "work"),
        "outputs": str(tmp_path / "outputs"), "effects": str(tmp_path / "effects"),
        "source_receipts": [], "result": str(result)}))
    assert main(["--request", str(request_file)]) == 0, result.read_text()
    output = json.loads((tmp_path / "outputs" / safe_name(output_ids[1])).read_text())
    assert output["code"] == "600519" and output["enabled"] is False


def test_real_replay_unit_registers_candidate_raw_inputs_from_frozen_request(tmp_path):
    from types import SimpleNamespace

    from autoresearch.session_agent.replay_adapters.common import virtual_handle
    from autoresearch.session_agent.replay_adapters.scan import _register
    req = candidate(scan_request())
    replay = SimpleNamespace(work=tmp_path, replay_run_id="20260913T010203000000Z", env={"AUTORESEARCH_ENGINE": "codex"})
    handle = virtual_handle(replay, req)
    unit = {"expected_outputs": [{"artifact_id": "scan.review.plan"}],
            "input_refs": [{"artifact_id": f"scan.l4.600519.a1.{kind}"} for kind in ("slim", "deep", "initial", "changes")]}
    _register(req, handle, unit)
    for kind in ("slim", "deep"):
        assert artifacts.artifact_path(handle, f"scan.l4.600519.a1.{kind}") == handle.staging / f"session_attempts/600519/a1/{kind}.md"


@pytest.mark.parametrize('context_ref,expected', [('decision-context', 'VERIFIED'), ('initial-context', 'INVALID')])
def test_two_stage_precheck_uses_locked_owner_snapshot_without_reentrant_lock(tmp_path, context_ref, expected):
    """SYNTHETIC exported archives, with real receipt and independent-context checks."""
    import signal
    from types import SimpleNamespace

    from autoresearch.common.atomic import canonical_json
    from autoresearch.contracts.agent_output import OW_GATES, RUBRIC_DIMENSIONS
    from autoresearch.session_agent import host_evidence, service, store
    from autoresearch.session_agent.card_facts import build_registered_facts
    from tests.common.test_card_decision_v3 import decision_text
    from tests.forensics.test_host_evidence import _rollout
    from tests.session_agent.test_precheck_submission import snapshot

    handle = _handle(tmp_path)
    req = candidate()
    service.begin(req, begin_capsule=lambda _: handle)
    loader = lambda _: handle
    noop = lambda *args, **kwargs: None
    execution = SimpleNamespace(exit_code=0, invocation={'status': 'COMPLETED'})
    service.claim(handle.run_id, 'stock.harvest', 1, handle_loader=loader)
    def harvest(*args, **kwargs):
        for key in ('stock.slim', 'stock.deep'):
            path = artifacts.declared_path(handle, key)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('SYNTHETIC raw facts')
        return execution
    service.execute(handle.run_id, 'stock.harvest', 1,
        {'ticker': req['subject'], 'analysis_date': req['analysis_date'], 'asset_type': 'stock', 'peers': [], 'slim': True},
        handle_loader=loader, runner=harvest)
    service.claim(handle.run_id, 'stock.card.facts', 1, handle_loader=loader)
    def facts_runner(*args, **kwargs):
        build_registered_facts(handle, service._task(handle, 'stock.card.facts'))
        return execution
    service.execute(handle.run_id, 'stock.card.facts', 1, {}, handle_loader=loader, runner=facts_runner)
    source = _rollout(tmp_path)

    def bound_submission(claimed, context):
        task_id = claimed['result']['envelope']['task_id']
        task = service._task(handle, task_id)
        binding = host_evidence.bind_task_transcript(handle.run_id, task_id, 1, source,
            context_ref=context, parent_context_ref='session-main', session_ref='session-main',
            start_ordinal=0, end_ordinal=13, context_source='SUBAGENT', handle_loader=loader)
        receipt = {'schema_version': 1, 'engine': 'codex', 'session_ref': 'session-main',
            'context_ref': context, 'parent_context_ref': 'session-main', 'task_id': task_id,
            'attempt': 1, 'completed': True, 'evidence_refs': [binding['evidence_ref']]}
        value = {'schema_version': 1, 'envelope': claimed['result']['envelope'],
            'plan_hash': claimed['result']['plan_hash'],
            'outputs': [{'artifact_id': key, 'sha256': sha256_bytes(Path(path).read_bytes())}
                        for key, path in artifacts.output_paths(handle, task, 1).items()],
            'host_receipt_id': sha256_bytes(canonical_json(receipt).encode())}
        return value, receipt

    first = service.claim(handle.run_id, 'stock.initial', 1, handle_loader=loader, event_recorder=noop)
    original = _write(handle, 'stock.card.initial', _assessment(handle))
    initial_submission, initial_receipt = bound_submission(first, 'initial-context')
    service.submit(handle.run_id, initial_submission, handle_loader=loader,
                   host_receipt=initial_receipt, event_recorder=noop)
    second = service.claim(handle.run_id, 'stock.card', 1, handle_loader=loader, event_recorder=noop)
    _write(handle, 'stock.card.output', decision_text(subject='600519.SS', venue='XSHG', rating='Hold',
        dimensions=dict.fromkeys(RUBRIC_DIMENSIONS, '未核'), gates=dict.fromkeys(OW_GATES, 'UNKNOWN')))
    _write(handle, 'stock.card.changes', {'schema_version': 1, 'subject': req['subject'],
        'initial_hash': original['sha256'], 'changed_fields': [], 'change_reason': '', 'new_evidence_refs': []})
    submission, receipt = bound_submission(second, context_ref)
    before = snapshot(handle)
    def timed_out(*_):
        pytest.fail('two-stage precheck deadlocked while holding task owner lock')
    previous_handler = signal.signal(signal.SIGALRM, timed_out)
    try:
        signal.alarm(3)
        result = service.precheck(handle.run_id, submission, handle_loader=loader, host_receipt=receipt)['result']
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous_handler)
    assert result['domain_status'] == 'PASS'
    assert result['host_evidence_status'] == expected
    assert result['can_submit'] is (expected == 'VERIFIED')
    if expected == 'INVALID':
        assert 'distinct from initial' in ' '.join(result['errors'])
    assert snapshot(handle) == before
    assert store.read_states(handle.workspace / 'session/tasks.json')['stock.card'] == 'RUNNING'
