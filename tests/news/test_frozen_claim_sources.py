"""The current run's receipts/quotes, not model assertions, close material claims."""
import json
from types import SimpleNamespace

import pytest

from autoresearch.common.execution_math import build_decision_frame
from autoresearch.news.claim_extract import bundle_from_line
from autoresearch.news.material_claims import bind_material_claim, verify_material_claims
from autoresearch.trace.blobs import blob_path
from autoresearch.trace.source_receipts import read_receipts, record_response

TEXT = "2026-09-01 公司已完成回购 10 亿元"
CUTOFF = "2026-09-02T14:45:00+08:00"


def frame():
    return build_decision_frame(analysis_session="2026-09-01", knowledge_cutoff=CUTOFF,
        venue="XSHG", research_depth="LITE", usage="scan", sessions=[], calendar_quality="UNKNOWN")


def event():
    return bundle_from_line(TEXT, subject_code="600000", claim_id="claim")["event"]


def record(handle, payload, *, provider="host_tool", endpoint="web.fetch", first_available=CUTOFF,
           status="CURRENT", supersedes=(), url="https://issuer/a"):
    return record_response(handle, {
        "engine": handle.engine, "run_id": handle.run_id, "task_id": "intel.600000", "attempt": 1,
        "provider": provider, "endpoint": endpoint, "normalized_params": {"url": url},
        "started_at": "2026-09-02T07:00:00Z", "ended_at": "2026-09-02T07:00:01Z",
        "as_of": "2026-09-01", "available_at": first_available, "consumer_refs": [],
        "source_timing": {"published_at": first_available, "first_available_at": first_available,
            "received_at": "2026-09-02T07:00:01Z", "timestamp_precision": {
                "published_at": "second" if first_available else None,
                "first_available_at": "second" if first_available else None, "received_at": "second"}},
        "source_status": status, "supersedes_receipt_ids": list(supersedes),
    }, payload)


def setup_claim(tmp_path, *, provider="deterministic", source_event=None, first_available=CUTOFF):
    handle = SimpleNamespace(capsule=tmp_path / "capsule", engine="codex", run_id="20260902T064500000000Z")
    handle.capsule.mkdir()
    source = record(handle, TEXT, first_available=first_available)
    review = record(handle, {"schema_version": 1, "source_receipt_id": source["receipt_id"],
        "source_hash": source["payload_hash"], "event": source_event or event(),
        "checked_fields": list(event()), "reviewer": None}, provider=provider, endpoint="claim_fields.v1")
    claim = bind_material_claim(handle.capsule, engine=handle.engine, run_id=handle.run_id,
        task_id="intel.600000", attempt=1, claim_id="claim", statement=TEXT,
        source_receipt_ids=[source["receipt_id"]], calculation_ids=[],
        quote_refs=[{"blob_hash": source["payload_hash"], "start": 0, "end": len(TEXT), "text": TEXT}],
        claim_event=event(), review_receipt_ids=[review["receipt_id"]])
    return handle, source, review, claim


def test_v2_receipt_preserves_public_and_receive_clocks(tmp_path):
    handle, source, _, _ = setup_claim(tmp_path)
    assert source["schema_version"] == 2
    assert source["source_timing"]["received_at"] == "2026-09-02T07:00:01Z"
    assert source in read_receipts(handle.capsule)


def test_legacy_self_reported_deterministic_fields_are_not_authority(tmp_path):
    from autoresearch.news.material_claims import evaluate_material_claim
    handle, _, _, claim = setup_claim(tmp_path)
    result = evaluate_material_claim(handle.capsule, claim, decision_frame=frame())
    assert result["verdict"] == "UNKNOWN"
    assert result["source"] == result["timing"] == "PASS"
    assert result["semantic"] == "UNKNOWN"
    assert result["received_by_cutoff"] == "UNKNOWN"  # public as-of is not observed PIT


@pytest.mark.parametrize("change,reason", [("model", "semantic"), ("future", "timing"),
                                            ("hash", "source"), ("amount", "semantic")])
def test_bad_evidence_cannot_count_as_supported(tmp_path, change, reason):
    from autoresearch.news.material_claims import evaluate_material_claim
    handle, source, _, claim = setup_claim(tmp_path,
        provider="host_tool" if change == "model" else "deterministic",
        source_event=dict(event(), amount_value="100000000") if change == "amount" else None,
        first_available="2026-09-02T15:00:00+08:00" if change == "future" else CUTOFF)
    if change == "hash":
        blob_path(handle.capsule, source["payload_hash"]).write_text(TEXT + "更正")
    result = evaluate_material_claim(handle.capsule, claim, decision_frame=frame())
    assert result["verdict"] == "UNKNOWN"  # No registered producer verified this fabricated review.
    assert result[reason] != "PASS"


def test_correction_visible_without_erasing_original_receipt(tmp_path):
    from autoresearch.news.material_claims import evaluate_material_claim
    handle, source, _, claim = setup_claim(tmp_path)
    correction = record(handle, "更正原公告", status="CORRECTED", supersedes=[source["receipt_id"]])
    result = evaluate_material_claim(handle.capsule, claim, decision_frame=frame())
    assert result["verdict"] == "UNKNOWN" and result["conflict"] == "UNKNOWN"
    assert correction["receipt_id"] in result["related_receipt_ids"]
    assert source in read_receipts(handle.capsule)


def test_unbound_material_claim_stays_in_denominator(tmp_path):
    value = bind_material_claim(tmp_path, engine="codex", run_id="20260902T064500000000Z",
        task_id="intel.600000", attempt=1, claim_id="missing", statement="公司受益",
        source_receipt_ids=[], quote_refs=[], calculation_ids=[], claim_event=event(), review_receipt_ids=[])
    result = verify_material_claims(tmp_path, decision_frame=frame())
    assert result["claims"] == 1 and result["supported"] == 0 and result["unknown"] == 1
    assert result["results"][0]["claim_id"] == value["claim_id"]


def test_intel_guard_consumes_frozen_run_receipts_and_keeps_unparsed_denominator(tmp_path, monkeypatch):
    from autoresearch.scan.l4.intel_guard import guard_intel
    from autoresearch.session_agent import artifacts
    from autoresearch.trace import capsule
    handle, source, _, _ = setup_claim(tmp_path)
    handle.workspace = tmp_path
    handle.staging = tmp_path / "staging" / "2026-09-01"
    handle.staging.mkdir(parents=True)
    path = handle.staging / "frame.json"
    path.write_text(json.dumps(frame()))
    artifacts.register_artifact(handle, "research.frame", path, "READ")
    monkeypatch.setenv("AUTORESEARCH_RUN_ID", handle.run_id)
    monkeypatch.setenv("AUTORESEARCH_TASK_ID", "intel.600000")
    monkeypatch.setattr(capsule, "require_active_run", lambda _: handle)
    draft = handle.staging / "_l4_intel_600000.md"
    draft.write_text("## 事件段\n| 日期 | 时效窗 | 事件 | 源 | 净分 |\n|---|---|---|---|---|\n"
        "| 2026-09-01 | T0 | 公司已完成回购 10 亿元 | https://issuer/a | 1 |\n"
        "| 2026-09-01 | T0 | 行业政策可能利好公司 | https://issuer/unknown | 1 |\n"
        "## 声明行\n网查 2 条\n")
    result = guard_intel(handle.staging, "600000", hard_cap=30)
    payload = json.loads((handle.staging / result["claim_events"]["sidecar"]).read_text())
    assert payload["binding"] == "frozen_run_sources"
    assert len(payload["events"]) == 2
    assert payload["events"][0]["verdict"] == "UNKNOWN"
    assert payload["events"][1]["verdict"] == "UNKNOWN"
    assert payload["coverage"] == {"claims": 2, "supported": 0, "refuted": 0, "unknown": 2}


def test_receipt_v1_remains_readable(tmp_path):
    handle = SimpleNamespace(capsule=tmp_path, engine="codex", run_id="20260902T064500000000Z")
    context = {"engine": handle.engine, "run_id": handle.run_id, "task_id": "old", "attempt": 1,
        "provider": "host_tool", "endpoint": "web.fetch", "normalized_params": {},
        "started_at": "2026-09-02T07:00:00Z", "ended_at": "2026-09-02T07:00:01Z",
        "as_of": None, "available_at": None, "consumer_refs": []}
    receipt = record_response(handle, context, TEXT)
    assert receipt["schema_version"] == 1 and read_receipts(tmp_path) == [receipt]


def test_source_with_unknown_public_time_does_not_borrow_received_time(tmp_path):
    from autoresearch.news.material_claims import evaluate_material_claim
    handle, source, _, claim = setup_claim(tmp_path, first_available=None)
    result = evaluate_material_claim(handle.capsule, claim, decision_frame=frame())
    assert result["timing"] == "UNKNOWN" and result["verdict"] == "UNKNOWN"


def test_source_with_minute_precision_cannot_assert_it_was_known_at_start_of_minute(tmp_path):
    from autoresearch.contracts.source_receipt import source_receipt_id
    from autoresearch.news.material_claims import evaluate_material_claim
    handle, source, review, claim = setup_claim(tmp_path)
    # A distinct receipt records the original minute precision, not invented seconds.
    source["source_timing"]["timestamp_precision"]["first_available_at"] = "minute"
    source["receipt_id"] = source_receipt_id(source)
    (handle.capsule / "lineage/source_receipts.jsonl").write_text(json.dumps(source) + "\n")
    claim["source_receipt_ids"] = [source["receipt_id"]]
    claim["review_receipt_ids"] = []
    from autoresearch.news.material_claims import _id
    claim["sidecar_id"] = _id(claim)
    result = evaluate_material_claim(handle.capsule, claim, decision_frame=frame())
    assert result["verdict"] == "UNKNOWN" and result["timing"] == "UNKNOWN"


def test_material_quote_span_corruption_fails_integrity(tmp_path):
    from autoresearch.news.material_claims import _id, evaluate_material_claim
    handle, _, _, claim = setup_claim(tmp_path)
    claim["quote_refs"][0]["start"] = 1
    claim["sidecar_id"] = _id(claim)
    result = evaluate_material_claim(handle.capsule, claim, decision_frame=frame())
    assert result["source"] == "UNKNOWN" and result["verdict"] == "UNKNOWN"


def test_report_shows_machine_support_dimensions_and_unknown_denominator(tmp_path, monkeypatch):
    from autoresearch.scan.report_sections import _stage_token_estimate
    from autoresearch.session_agent import artifacts
    from autoresearch.trace import capsule
    handle, _, _, _ = setup_claim(tmp_path)
    handle.workspace = tmp_path
    handle.staging = tmp_path / "staging" / "2026-09-01"
    handle.staging.mkdir(parents=True)
    path = handle.staging / "frame.json"
    path.write_text(json.dumps(frame()))
    artifacts.register_artifact(handle, "research.frame", path, "READ")
    bind_material_claim(handle.capsule, engine=handle.engine, run_id=handle.run_id,
        task_id="intel.600000", attempt=1, claim_id="unknown", statement="公司受益",
        source_receipt_ids=[], quote_refs=[], calculation_ids=[], claim_event=None, review_receipt_ids=[])
    monkeypatch.setenv("AUTORESEARCH_RUN_ID", handle.run_id)
    monkeypatch.setattr(capsule, "require_active_run", lambda _: handle)
    text = "\n".join(_stage_token_estimate(handle.staging))
    assert "材料断言机检" in text and "UNKNOWN 2/2" in text
    assert all(label in text for label in ("来源", "语义", "时效", "冲突"))
    assert "未证实不等于假" in text


def test_report_does_not_borrow_another_runs_claims(tmp_path, monkeypatch):
    from autoresearch.scan.report_sections import _material_claim_note
    from autoresearch.trace import capsule
    handle, _, _, _ = setup_claim(tmp_path)
    handle.staging = tmp_path / "other"
    monkeypatch.setenv("AUTORESEARCH_RUN_ID", handle.run_id)
    monkeypatch.setattr(capsule, "require_active_run", lambda _: handle)
    assert _material_claim_note(tmp_path / "this") == []


def test_malformed_review_is_unknown_and_remains_reported(tmp_path):
    from autoresearch.news.material_claims import _id, evaluate_material_claim
    handle, _, _, claim = setup_claim(tmp_path)
    bad = record(handle, "not a structured field review", provider="deterministic", endpoint="claim_fields.v1")
    claim["review_receipt_ids"] = [bad["receipt_id"]]
    claim["sidecar_id"] = _id(claim)
    result = evaluate_material_claim(handle.capsule, claim, decision_frame=frame())
    assert result["verdict"] == "UNKNOWN" and result["semantic"] == "UNKNOWN"


def test_human_name_alone_never_grants_authorized_review(tmp_path):
    from autoresearch.news.material_claims import _id, evaluate_material_claim
    handle, source, _, claim = setup_claim(tmp_path, provider="human_review")
    assert evaluate_material_claim(handle.capsule, claim, decision_frame=frame())["verdict"] == "UNKNOWN"
    review = record(handle, {"schema_version": 1, "source_receipt_id": source["receipt_id"],
        "source_hash": source["payload_hash"], "event": event(), "checked_fields": list(event()),
        "reviewer": "reviewer-17"}, provider="human_review", endpoint="claim_fields.v1")
    claim["review_receipt_ids"] = [review["receipt_id"]]
    claim["sidecar_id"] = _id(claim)
    assert evaluate_material_claim(handle.capsule, claim, decision_frame=frame())["verdict"] == "UNKNOWN"


@pytest.mark.parametrize("failure", ["wrong_subject", "corrupt_frame"])
def test_guard_failure_keeps_all_material_claims_unknown(tmp_path, monkeypatch, failure):
    from autoresearch.scan.l4.intel_guard import guard_intel
    from autoresearch.session_agent import artifacts
    from autoresearch.trace import capsule
    handle, _, _, _ = setup_claim(tmp_path, source_event=dict(event(), subject_code="600001"))
    handle.workspace = tmp_path
    handle.staging = tmp_path / "staging" / "2026-09-01"
    handle.staging.mkdir(parents=True)
    path = handle.staging / "frame.json"
    path.write_text(json.dumps(frame()))
    artifacts.register_artifact(handle, "research.frame", path, "READ")
    monkeypatch.setenv("AUTORESEARCH_RUN_ID", handle.run_id)
    monkeypatch.setenv("AUTORESEARCH_TASK_ID", "intel.600000")
    monkeypatch.setattr(capsule, "require_active_run", lambda _: handle)
    header = "## 事件段\n| 日期 | 时效窗 | 事件 | 源 | 净分 |\n|---|---|---|---|---|\n"
    rows = ("| 2026-09-01 | T0 | 公司已完成回购 10 亿元 | https://issuer/a | 1 |\n"
            "| 2026-09-01 | T0 | 行业政策可能利好公司 | https://issuer/unknown | 1 |\n")
    (handle.staging / "_l4_intel_600000.md").write_text(header + rows + "## 声明行\n网查 2 条\n")
    if failure == "corrupt_frame":
        path.write_text(json.dumps(dict(frame(), knowledge_cutoff="2026-09-02T15:00:00+08:00")))
        (handle.staging / "_l4_intel_600000.pretrim").write_text(header + rows +
            "| 2026-09-01 | 背景 | 已裁重大业务变化 | https://issuer/old | 0 |\n")
    if failure == "corrupt_frame":
        with pytest.raises(ValueError, match="population incomplete"):
            guard_intel(handle.staging, "600000", hard_cap=30)
        sidecar = "_l4_claims_600000.json"
    else:
        result = guard_intel(handle.staging, "600000", hard_cap=30)
        sidecar = result["claim_events"]["sidecar"]
    assert sidecar is not None
    payload = json.loads((handle.staging / sidecar).read_text())
    n = 3 if failure == "corrupt_frame" else 2
    assert payload["coverage"] == {"claims": n, "supported": 0, "refuted": 0, "unknown": n}
    assert all(row["verdict"] == "UNKNOWN" for row in payload["events"])
    if failure == "corrupt_frame":
        assert payload["binding"] == "frozen_sources_unavailable"
        assert payload["events"][-1]["retained"] is False
        assert {row["reason"] for row in payload["events"]} == {"FROZEN_SOURCES_UNAVAILABLE"}
        from autoresearch.scan.report_sections import _material_claim_note
        note = "\n".join(_material_claim_note(handle.staging))
        assert "UNKNOWN 3/3" in note and "冻结来源不可核验" in note


def test_honest_unknown_v2_is_complete_but_claimed_missing_reference_is_not(tmp_path):
    bind_material_claim(tmp_path, engine="codex", run_id="20260902T064500000000Z",
        task_id="intel.600000", attempt=1, claim_id="unverified", statement="公司受益",
        source_receipt_ids=[], quote_refs=[], calculation_ids=[], claim_event=None, review_receipt_ids=[])
    result = verify_material_claims(tmp_path, decision_frame=frame())
    assert result["ok"] is True and result["claims"] == 1 and result["unknown"] == 1
    assert result["results"][0]["source"] == "UNKNOWN"
    bind_material_claim(tmp_path, engine="codex", run_id="20260902T064500000000Z",
        task_id="intel.600000", attempt=1, claim_id="broken-link", statement="另一条事实",
        source_receipt_ids=["f" * 64], quote_refs=[], calculation_ids=[], claim_event=None, review_receipt_ids=[])
    assert verify_material_claims(tmp_path)["ok"] is False


def test_retry_preserves_history_without_inflating_current_claim_denominator(tmp_path):
    handle, source, review, claim = setup_claim(tmp_path)
    def repeat(statement=TEXT, task="intel.600000.a2"):
        return bind_material_claim(handle.capsule, engine=handle.engine, run_id=handle.run_id,
            task_id=task, attempt=2, claim_id="claim", statement=statement,
            source_receipt_ids=[source["receipt_id"]], calculation_ids=[],
            quote_refs=[{"blob_hash": source["payload_hash"], "start": 0, "end": len(TEXT), "text": TEXT}],
            claim_event=event(), review_receipt_ids=[review["receipt_id"]])
    repeat()
    result = verify_material_claims(handle.capsule, decision_frame=frame())
    assert result["claims"] == 1 and result["supported"] == 0 and result["unknown"] == 1
    assert result["history_sidecars"] == 2
    repeat("更改后的同一主张版本", task="intel.600000.a3")
    result = verify_material_claims(handle.capsule, decision_frame=frame())
    assert result["claims"] == 1 and result["unknown"] == 1 and result["supported"] == 0
    assert result["history_sidecars"] == 3
    assert result["results"][0]["reason"] == "CURRENT_VERSION_AMBIGUOUS"
