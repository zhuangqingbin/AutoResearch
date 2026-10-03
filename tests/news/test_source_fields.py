"""Deterministic parquet source fields, root permissions and actual claim consumers."""

import json
from types import SimpleNamespace

import pandas as pd
import pytest

from autoresearch.common.atomic import canonical_json, sha256_bytes
from autoresearch.news.claim_extract import extract_event
from autoresearch.news.material_claims import bind_material_claim, evaluate_material_claim
from autoresearch.session_agent import artifacts, service
from autoresearch.trace.blobs import blob_path
from autoresearch.trace.source_receipts import read_receipts, record_response
from tests.news.test_frozen_claim_sources import frame

ROW = {
    "ts_code": "600000.SH",
    "ann_date": "20260901",
    "end_date": "20260901",
    "proc": "完成",
    "exp_date": None,
    "vol": 10000.0,
    "amount": 1000000000.0,
    "high_limit": 10.0,
    "low_limit": 9.0,
}
TEXT = "2026-09-01 公司已完成回购 10 亿元"


def setup_run(
    tmp_path,
    *,
    rows=None,
    provider="tushare",
    endpoint="repurchase",
    state="CURRENT",
    source_attempt=1,
):
    from autoresearch.contracts.session_plan import plan_hash
    from autoresearch.session_agent.roles import roles_hash
    from tests.session_agent.test_service import _handle, _request

    handle = _handle(tmp_path)
    frame_path = handle.staging / "frame.json"
    frame_path.write_text(json.dumps(frame()))
    intel = handle.staging / "_l4_intel_600000.md"
    intel.write_text(TEXT + "\n")
    task_id = "scan.l4.600000.intel_status"

    def planner(request, current):
        task = {
            "task_id": task_id,
            "kind": "DETERMINISTIC",
            "role": None,
            "operation": "scan.l4.intel.status",
            "dependencies": [],
            "input_artifact_ids": ["research.frame", "intel.raw"],
            "output_artifact_ids": ["intel.status"],
            "expected_output_contract": "scan.l4.intel_status.v1",
            "owner": "SESSION",
            "subject": "600000",
            "independent_context": False,
            "parent_task": None,
        }
        value = {
            "schema_version": 1,
            "engine": current.engine,
            "run_id": current.run_id,
            "run_kind": request["kind"],
            "requested_mode": request["requested_mode"],
            "analysis_date": request["analysis_date"],
            "orchestration_version": "session_v1",
            "input_contract_hash": current.contract.contract_hash,
            "config_hash": sha256_bytes(canonical_json({}).encode()),
            "host_profile_hash": sha256_bytes(canonical_json(request["host_profile"]).encode()),
            "roles_hash": roles_hash(),
            "tasks": [task],
            "task_templates": [],
            "plan_hash": "0" * 64,
        }
        value["plan_hash"] = plan_hash(value)
        return value

    def register(request, current, plan):
        artifacts.register_artifact(current, "research.frame", frame_path, "READ")
        artifacts.register_artifact(current, "intel.raw", intel, "READ")
        artifacts.register_artifact(
            current, "intel.status", handle.staging / "status.json", "WRITE"
        )

    service.begin(
        _request(), begin_capsule=lambda _: handle, planner=planner, artifact_registrar=register
    )
    service.claim(
        handle.run_id,
        task_id,
        1,
        handle_loader=lambda _: handle,
        event_recorder=lambda *a, **k: None,
    )
    source = record_response(
        handle,
        {
            "engine": handle.engine,
            "run_id": handle.run_id,
            "task_id": task_id,
            "attempt": source_attempt,
            "provider": provider,
            "endpoint": endpoint,
            "normalized_params": {"ann_date": "20260901"},
            "started_at": "2026-09-02T06:00:00Z",
            "ended_at": "2026-09-02T06:00:01Z",
            "as_of": "2026-09-01",
            "available_at": "2026-09-02T06:00:01Z",
            "consumer_refs": [],
            "source_timing": {
                "published_at": "2026-09-01T00:00:00+08:00",
                "first_available_at": "2026-09-01T00:00:00+08:00",
                "received_at": "2026-09-02T06:00:01Z",
                "timestamp_precision": {
                    "published_at": "day",
                    "first_available_at": "day",
                    "received_at": "second",
                },
            },
            "source_status": state,
            "supersedes_receipt_ids": [],
        },
        pd.DataFrame(rows or [ROW]),
    )
    return handle, task_id, source


def request(source):
    return {
        "source_receipt_id": source["receipt_id"],
        "adapter_id": "tushare.repurchase.v1",
        "selector": {"ts_code": "600000.SH", "ann_date": "20260901", "end_date": "20260901"},
    }


def produce(handle, task_id, source):
    return service.source_fields(
        handle.run_id, task_id, 1, request(source), handle_loader=lambda _: handle
    )["result"]


def bind(handle, task_id, source, review, statement=TEXT):
    return bind_material_claim(
        handle.capsule,
        engine=handle.engine,
        run_id=handle.run_id,
        task_id=task_id,
        attempt=1,
        claim_id="q05",
        statement=statement,
        source_receipt_ids=[source["receipt_id"]],
        quote_refs=[],
        calculation_ids=[],
        claim_event=extract_event(statement, subject_code="600000")["event"],
        review_receipt_ids=[review["receipt_id"]],
    )


def test_real_producer_parquet_to_material_claim(tmp_path):
    handle, task_id, source = setup_run(tmp_path)
    review = produce(handle, task_id, source)
    payload = json.loads(blob_path(handle.capsule, review["payload_hash"]).read_bytes())
    assert set(payload) == {
        "schema_version",
        "source_receipt_id",
        "source_hash",
        "event",
        "checked_fields",
        "reviewer",
    }
    assert payload["source_hash"] == source["payload_hash"]
    assert review["normalized_params"]["adapter_id"] == "tushare.repurchase.v1"
    assert review["normalized_params"]["field_paths"]["amount_value"] == "/rows/0/amount"
    claim = bind(handle, task_id, source, review)
    assert (
        evaluate_material_claim(handle.capsule, claim, decision_frame=frame())["verdict"] == "PASS"
    )


@pytest.mark.parametrize(
    "row,statement,expected",
    [
        (ROW, TEXT.replace("10 亿元", "1 亿元"), "FAIL"),
        ({**ROW, "proc": "股东大会通过"}, TEXT, "FAIL"),
        (ROW, "2026-09-01 公司拟回购 10 亿元", "FAIL"),
        ({**ROW, "proc": "停止实施"}, TEXT, "FAIL"),
        ({**ROW, "proc": "未知状态"}, TEXT, "UNKNOWN"),
    ],
)
def test_identity_does_not_depend_on_amount_or_lifecycle(tmp_path, row, statement, expected):
    handle, task_id, source = setup_run(tmp_path, rows=[row])
    review = produce(handle, task_id, source)
    result = evaluate_material_claim(
        handle.capsule, bind(handle, task_id, source, review, statement), decision_frame=frame()
    )
    assert result["verdict"] == expected


@pytest.mark.parametrize(
    "change",
    [
        "provider",
        "endpoint",
        "revoked",
        "corrected",
        "source_attempt",
        "stale",
        "hash",
        "truncated",
        "ambiguous",
        "frozen_input",
        "selector",
        "extra",
        "subject",
    ],
)
def test_adversarial_producer_requests_rejected(tmp_path, change):
    kwargs = (
        {"provider": "host_tool"}
        if change == "provider"
        else {"endpoint": "news"}
        if change == "endpoint"
        else {"state": "RETRACTED"}
        if change == "revoked"
        else {"state": "CORRECTED"}
        if change == "corrected"
        else {"source_attempt": 2}
        if change == "source_attempt"
        else {"rows": [ROW, ROW]}
        if change == "ambiguous"
        else {}
    )
    handle, task_id, source = setup_run(tmp_path, **kwargs)
    req = request(source)
    if change == "hash":
        blob_path(handle.capsule, source["payload_hash"]).write_bytes(b"corrupted")
    if change == "truncated":
        # Hash-correct bytes with invalid parquet must still be rejected.
        bad = record_response(
            handle,
            {
                key: source[key]
                for key in (
                    "engine",
                    "run_id",
                    "task_id",
                    "attempt",
                    "provider",
                    "endpoint",
                    "normalized_params",
                    "started_at",
                    "ended_at",
                    "as_of",
                    "available_at",
                    "consumer_refs",
                )
            },
            b"PAR1truncated",
        )
        from autoresearch.contracts.source_receipt import source_receipt_id

        bad["codec"] = "dataframe.parquet.v1"
        bad["receipt_id"] = source_receipt_id(bad)
        (handle.capsule / "lineage/source_receipts.jsonl").write_text(json.dumps(bad) + "\n")
        req["source_receipt_id"] = bad["receipt_id"]
    if change == "stale":
        path = handle.workspace / "session/tasks.json"
        doc = json.loads(path.read_bytes())
        doc["tasks"][task_id]["attempt"] = 2
        path.write_text(json.dumps(doc))
    if change == "frozen_input":
        (handle.staging / "frame.json").write_text("{}")
    if change == "selector":
        req["selector"]["amount"] = "1000000000"
    if change == "extra":
        req["event"] = extract_event(TEXT, subject_code="600000")["event"]
    if change == "subject":
        req["selector"]["ts_code"] = "600001.SH"
    with pytest.raises((ValueError, RuntimeError, KeyError)):
        service.source_fields(handle.run_id, task_id, 1, req, handle_loader=lambda _: handle)


def test_guard_produces_and_consumes_structured_fields_without_url(tmp_path, monkeypatch):
    from autoresearch.scan.l4.intel_guard import guard_intel
    from autoresearch.session_agent.source_fields import intel_source_context
    from autoresearch.trace import capsule

    handle, task_id, source = setup_run(tmp_path)
    monkeypatch.setenv("AUTORESEARCH_RUN_ID", handle.run_id)
    monkeypatch.setenv("AUTORESEARCH_TASK_ID", task_id)
    monkeypatch.setenv("AUTORESEARCH_ATTEMPT", "1")
    monkeypatch.setattr(capsule, "require_active_run", lambda _: handle)
    result = guard_intel(handle.staging, "600000", source_context=intel_source_context(handle.staging))
    assert result["claim_events"]["n"] == 1
    payload = json.loads((handle.staging / result["claim_events"]["sidecar"]).read_bytes())
    assert payload["events"][0]["verdict"] == "PASS"
    reviews = [row for row in read_receipts(handle.capsule) if row["endpoint"] == "claim_fields.v1"]
    assert len(reviews) == 1


def test_forged_legacy_provider_does_not_authorize_fields(tmp_path):
    from tests.news.test_frozen_claim_sources import setup_claim

    handle, _, _, claim = setup_claim(tmp_path)
    assert (
        evaluate_material_claim(handle.capsule, claim, decision_frame=frame())["verdict"]
        == "UNKNOWN"
    )


@pytest.mark.parametrize(
    "statement,expected", [(TEXT, "PASS"), (TEXT.replace("10 亿元", "1 亿元"), "UNKNOWN")]
)
def test_registered_source_changes_effective_card(tmp_path, statement, expected):
    from autoresearch.common.card_decision import card_from_decision_text
    from autoresearch.news.card_claims import evaluate_card_claims
    from tests.common.test_card_decision_v3 import decision_text

    handle, task_id, source = setup_run(tmp_path)
    review = produce(handle, task_id, source)
    claim = bind(handle, task_id, source, review, statement)
    text = decision_text(subject="600000", venue="XSHG", rating="Hold", deviation="风险管理")
    text += (
        "\n```decision-claim-uses-v1\n"
        + json.dumps(
            {
                "schema_version": 1,
                "declarations": [],
                "uses": [
                    {
                        "claim_id": "q05",
                        "statement_sha256": claim["statement_sha256"],
                        "target": "gates.业绩真兑现",
                        "support_group": "repurchase",
                        "relation": "REQUIRED",
                        "rationale": "原始回购数据支持",
                    }
                ],
            }
        )
        + "\n```\n"
    )
    card = card_from_decision_text(text, subject="600000", venue="XSHG", analysis_date="2026-09-01")
    result = evaluate_card_claims(
        text,
        card=card,
        frame=frame(),
        capsule=handle.capsule,
        identity={
            "engine": handle.engine,
            "run_id": handle.run_id,
            "task_id": "card",
            "attempt": 1,
        },
        accepted_attempts={task_id: 1},
        frame_hash="a" * 64,
    )
    assert result["effective_card"]["gates"]["业绩真兑现"] == expected
    assert result["usage"]["claims"][0]["verdict"] == ("PASS" if expected == "PASS" else "FAIL")


def test_offline_guard_replays_frozen_context_without_original_workspace(tmp_path, monkeypatch):
    import shutil

    from autoresearch.scan.l4.intel_guard import guard_intel
    from autoresearch.session_agent import source_fields
    from autoresearch.session_agent.source_fields import intel_source_context
    from autoresearch.trace import capsule
    from autoresearch.trace.replay import REPLAY_ENV

    handle, task_id, _ = setup_run(tmp_path / "live")
    monkeypatch.setenv("AUTORESEARCH_RUN_ID", handle.run_id)
    monkeypatch.setenv("AUTORESEARCH_TASK_ID", task_id)
    monkeypatch.setenv("AUTORESEARCH_ATTEMPT", "1")
    monkeypatch.setattr(capsule, "require_active_run", lambda _: handle)
    result = guard_intel(handle.staging, "600000", source_context=intel_source_context(handle.staging))
    live = json.loads((handle.staging / result["claim_events"]["sidecar"]).read_bytes())
    frozen = handle.capsule / "evidence/attempt_records" / task_id / "a1/claim_source_context.json"
    assert frozen.is_file()
    inputs = tmp_path / "offline/inputs"
    (inputs / "artifacts").mkdir(parents=True)
    shutil.copyfile(frozen, inputs / "artifacts/claim.source_context")
    source_capsule = inputs / "source_capsule"
    shutil.copytree(handle.capsule / "lineage", source_capsule / "lineage")
    shutil.copytree(handle.capsule / "blobs", source_capsule / "blobs")
    receipts = tuple(read_receipts(source_capsule))
    context = SimpleNamespace(
        inputs=inputs,
        work=tmp_path / "offline/work",
        env={REPLAY_ENV: str(source_capsule)},
        source_receipts=receipts,
    )
    shutil.rmtree(handle.workspace)
    monkeypatch.delenv("AUTORESEARCH_RUN_ID")
    replay = source_fields.restore_replay_context(context)
    staging = tmp_path / "offline/staging/2026-09-13"
    staging.mkdir(parents=True)
    (staging / "_l4_intel_600000.md").write_text(TEXT + "\n")
    result = guard_intel(staging, "600000", source_context=replay)
    actual = json.loads((staging / result["claim_events"]["sidecar"]).read_bytes())
    assert actual["run_id"] == live["run_id"]
    assert actual["coverage"] == live["coverage"]
    assert actual["events"][0]["support"] == live["events"][0]["support"]


@pytest.mark.parametrize("state", ["PENDING", "SUCCEEDED", "FAILED", "CANCELLED"])
def test_service_only_issues_during_running_attempt(tmp_path, state):
    handle, task_id, source = setup_run(tmp_path)
    path = handle.workspace / "session/tasks.json"
    doc = json.loads(path.read_bytes())
    doc["tasks"][task_id]["state"] = state
    path.write_text(json.dumps(doc))
    with pytest.raises(RuntimeError, match="not running"):
        produce(handle, task_id, source)


def test_admitted_ancestor_receipt_requires_succeeded_current_attempt(tmp_path):
    from autoresearch.contracts.source_receipt import source_receipt_id

    handle, task_id, source = setup_run(tmp_path)
    path = handle.workspace / "session/tasks.json"
    doc = json.loads(path.read_bytes())
    current = doc["tasks"][task_id]
    current["spec"]["dependencies"] = ["harvest"]
    doc["tasks"]["harvest"] = {"state": "SUCCEEDED", "attempt": 1, "spec": {"dependencies": []}}
    path.write_text(json.dumps(doc))
    source["task_id"] = "harvest"
    source["receipt_id"] = source_receipt_id(source)
    (handle.capsule / "lineage/source_receipts.jsonl").write_text(json.dumps(source) + "\n")
    assert produce(handle, task_id, source)["endpoint"] == "claim_fields.v1"
    doc["tasks"]["harvest"]["attempt"] = 2
    path.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match="outside admitted"):
        produce(handle, task_id, source)


def test_review_replay_rejects_self_filled_provider_and_tampered_projection(tmp_path):
    from autoresearch.news.source_fields import replay_review

    handle, task_id, source = setup_run(tmp_path)
    real = produce(handle, task_id, source)
    payload = json.loads(blob_path(handle.capsule, real["payload_hash"]).read_bytes())
    # Same exact projection/params, fresh provider-tagged receipt, no root issuance.
    fake = record_response(
        handle,
        {
            key: real[key]
            for key in (
                "engine",
                "run_id",
                "task_id",
                "attempt",
                "provider",
                "endpoint",
                "normalized_params",
                "started_at",
                "ended_at",
                "as_of",
                "available_at",
                "consumer_refs",
            )
        },
        payload,
    )
    with pytest.raises(ValueError, match="issuance event"):
        replay_review(handle.capsule, fake, source, frame())
    payload["event"]["amount_value"] = "1"
    blob_path(handle.capsule, real["payload_hash"]).write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="hash"):
        replay_review(handle.capsule, real, source, frame())


@pytest.mark.parametrize("predicate", ["增持", "减持", "中标"])
def test_unsupported_predicates_have_no_structured_adapter(tmp_path, predicate):
    from autoresearch.news.source_fields import matching_requests

    handle, _, source = setup_run(tmp_path)
    claim = extract_event(TEXT.replace("回购", predicate), subject_code="600000")["event"]
    assert matching_requests(handle.capsule, [source], claim) == []


def test_date_precision_does_not_become_start_of_day_availability(tmp_path):
    handle, task_id, source = setup_run(tmp_path)
    root_frame = frame()
    root_frame["knowledge_cutoff"] = "2026-09-01T15:00:00+08:00"
    from autoresearch.news.source_fields import require_timing

    with pytest.raises(ValueError, match="availability"):
        require_timing(source, root_frame)


def test_unique_row_selector_does_not_resolve_ambiguous_claim_identity(tmp_path):
    handle, task_id, source = setup_run(tmp_path, rows=[ROW, {**ROW, "ann_date": "20260902"}])
    review = produce(handle, task_id, source)
    claim = bind(handle, task_id, source, review)
    assert (
        evaluate_material_claim(handle.capsule, claim, decision_frame=frame())["verdict"]
        == "UNKNOWN"
    )


def test_unrelated_claim_task_cannot_borrow_authorized_review(tmp_path):
    handle, task_id, source = setup_run(tmp_path)
    review = produce(handle, task_id, source)
    claim = bind(handle, "unrelated.task", source, review)
    assert (
        evaluate_material_claim(handle.capsule, claim, decision_frame=frame())["verdict"]
        == "UNKNOWN"
    )


def test_scan_intel_status_compute_replays_verified_source(tmp_path, monkeypatch):
    import shutil

    from autoresearch.scan.l4.intel_guard import guard_intel
    from autoresearch.session_agent import domain_ops
    from autoresearch.session_agent.replay_adapters.scan import execute
    from autoresearch.session_agent.source_fields import intel_source_context
    from autoresearch.trace import capsule
    from autoresearch.trace.replay import REPLAY_ENV
    from tests.forensics.test_scan_replay_matrix import _AdapterContext, _request

    handle, task_id, _ = setup_run(tmp_path / "live")
    monkeypatch.setenv("AUTORESEARCH_RUN_ID", handle.run_id)
    monkeypatch.setenv("AUTORESEARCH_TASK_ID", task_id)
    monkeypatch.setenv("AUTORESEARCH_ATTEMPT", "1")
    monkeypatch.setattr(capsule, "require_active_run", lambda _: handle)
    guard_intel(handle.staging, "600000", source_context=intel_source_context(handle.staging))
    frozen = handle.capsule / "evidence/attempt_records" / task_id / "a1/claim_source_context.json"
    frozen_config = handle.staging / "_session_inputs"
    frozen_config.mkdir(exist_ok=True)
    (frozen_config / "scan_config.jsonc").write_text("{}")
    (frozen_config / "pinned.jsonc").write_text("[]")
    (frozen_config / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "present": {
                    "scan_config.jsonc": True,
                    "pinned.jsonc": True,
                    "L1_weights.json": False,
                },
            }
        )
    )
    bundle = domain_ops.collect_scan_staging_bundle(handle.staging, phase="l4_source")
    prefix = "scan.l4.600000.a1"
    unit = {
        "task_id": task_id,
        "attempt": 1,
        "operation": "scan.l4.intel.status",
        "subject": "600000",
        "input_refs": [
            {"artifact_id": name}
            for name in ["scan.l4.source.bundle", prefix + ".intel", "claim.source_context"]
        ],
        "expected_outputs": [
            {"artifact_id": prefix + suffix} for suffix in [".intel_status", ".intel_bundle"]
        ],
    }
    req = _request()
    req["analysis_date"] = "2026-09-13"
    context = _AdapterContext(
        tmp_path / "offline",
        unit,
        req,
        {
            "scan.l4.source.bundle": json.dumps(bundle).encode(),
            prefix + ".intel": (TEXT + "\n").encode(),
            "claim.source_context": frozen.read_bytes(),
        },
    )
    source_capsule = context.inputs / "source_capsule"
    shutil.copytree(handle.capsule / "lineage", source_capsule / "lineage")
    shutil.copytree(handle.capsule / "blobs", source_capsule / "blobs")
    context.source_receipts = tuple(read_receipts(source_capsule))
    context.env[REPLAY_ENV] = str(source_capsule)
    original_run = handle.run_id
    shutil.rmtree(handle.workspace)
    monkeypatch.delenv("AUTORESEARCH_RUN_ID")
    assert execute(unit, context) == []
    status = json.loads(context.output_path(prefix + ".intel_status").read_bytes())
    assert "材料断言 UNKNOWN 0/1" in status["note"]
    result_bundle = json.loads(context.output_path(prefix + ".intel_bundle").read_bytes())
    assert result_bundle
    sidecars = list(context.work.rglob("_l4_claims_600000.json"))
    assert len(sidecars) == 1
    events = json.loads(sidecars[0].read_bytes())
    assert events["run_id"] == original_run
    assert events["events"][0]["support"]["verdict"] == "PASS"


def test_source_fields_is_not_a_research_broker_operation():
    from autoresearch.session_agent import task_access

    identity = task_access.broker_identity(engine="codex")
    command = task_access.broker_command(
        "session", "agent", "source-fields", "source", identity=identity
    )
    with pytest.raises(ValueError, match="invalid broker operation"):
        task_access.decode_broker_command(command, identity)


def test_compute_plan_includes_frozen_claim_context_and_sources(tmp_path):
    from autoresearch.session_agent.replay_registry import build_replay_plan
    from tests.forensics.test_replay_core import _capsule

    handle, capsule = _capsule(tmp_path, operation="scan.l4.intel.status", with_source=True)
    path = capsule / "evidence/attempt_records/calculate/a1/claim_source_context.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    source_ids = [row["receipt_id"] for row in read_receipts(capsule)]
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "run_id": handle.run_id,
                "engine": handle.engine,
                "task_id": "calculate",
                "attempt": 1,
                "source_receipt_ids": source_ids,
            }
        )
    )
    unit = build_replay_plan(handle)["units"][0]
    assert unit["mode"] == "COMPUTE"
    ref = next(ref for ref in unit["input_refs"] if ref["artifact_id"] == "claim.source_context")
    assert ref["sha256"] == sha256_bytes(path.read_bytes())
    assert set(source_ids) <= set(unit["source_receipt_ids"])
    payload = json.loads(path.read_bytes())
    payload["attempt"] = 2
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="owner mismatch"):
        build_replay_plan(handle)


@pytest.mark.parametrize("change", ["future", "unrelated"])
def test_inadmissible_source_cannot_change_asof_unique_identity(tmp_path, change):
    from autoresearch.contracts.source_receipt import source_receipt_id
    handle, task_id, source = setup_run(tmp_path)
    review = produce(handle, task_id, source)
    claim = bind(handle, task_id, source, review)
    assert evaluate_material_claim(handle.capsule, claim, decision_frame=frame())["verdict"] == "PASS"
    extra = record_response(handle, {k:source[k] for k in ("engine","run_id","task_id","attempt","provider","endpoint","normalized_params","started_at","ended_at","as_of","available_at","consumer_refs")}, pd.DataFrame([{**ROW,"ann_date":"20260902"}]))
    if change == "future":
        extra["available_at"] = "2027-01-01T00:00:00Z"
    else:
        extra["task_id"] = "unrelated"
        extra["occurrence"] = 1
    old_id = extra["receipt_id"]
    extra["receipt_id"] = source_receipt_id(extra)
    path=handle.capsule / "lineage/source_receipts.jsonl"
    rows=[row for row in read_receipts(handle.capsule) if row["receipt_id"]!=old_id]+[extra]
    path.write_text("".join(json.dumps(row)+"\n" for row in rows))
    assert evaluate_material_claim(handle.capsule, claim, decision_frame=frame())["verdict"] == "PASS"


def test_registered_intel_operation_injects_root_producer(tmp_path, monkeypatch):
    from autoresearch.session_agent import domain_ops
    from autoresearch.trace import capsule

    handle, task_id, _ = setup_run(tmp_path)
    monkeypatch.setenv("AUTORESEARCH_RUN_ID", handle.run_id)
    monkeypatch.setenv("AUTORESEARCH_TASK_ID", task_id)
    monkeypatch.setenv("AUTORESEARCH_ATTEMPT", "1")
    monkeypatch.setattr(capsule, "require_active_run", lambda _: handle)
    domain_ops.scan_l4_intel_status(handle, code="600000")
    value = json.loads((handle.staging / "_l4_claims_600000.json").read_bytes())
    assert value["events"][0]["verdict"] == "PASS"
    assert (handle.capsule / "evidence/attempt_records" / task_id / "a1/claim_source_context.json").is_file()
