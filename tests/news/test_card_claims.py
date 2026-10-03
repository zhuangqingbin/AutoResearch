import json

import pytest

from autoresearch.common.atomic import sha256_bytes
from autoresearch.common.card_decision import card_from_decision_text
from autoresearch.news import card_claims
from tests.common.test_card_decision_v3 import decision_text
from tests.news.test_frozen_claim_sources import TEXT, frame, setup_claim


def usage(claim, target="gates.业绩真兑现", relation="REQUIRED", group="earnings"):
    return {"claim_id": claim["claim_id"], "statement_sha256": claim["statement_sha256"],
            "target": target, "support_group": group, "relation": relation,
            "rationale": "本前提决定该项判断"}


def assess(handle, claim, uses=None, declarations=None, text=None):
    text = text or decision_text(subject="600000", venue="XSHG", rating="Hold", deviation="风险管理")
    if uses is not None:
        text += "\n```decision-claim-uses-v1\n" + json.dumps({"schema_version": 1,
            "declarations": declarations or [], "uses": uses}, ensure_ascii=False) + "\n```\n"
    card = card_from_decision_text(text, subject="600000", venue="XSHG", analysis_date="2026-09-01")
    return card_claims.evaluate_card_claims(text, card=card, frame=frame(), capsule=handle.capsule,
        identity={"engine": handle.engine, "run_id": handle.run_id, "task_id": "card", "attempt": 1},
        accepted_attempts={"intel.600000": 1}, frame_hash="a" * 64)


@pytest.mark.parametrize("uses", [None, []])
def test_known_claim_cannot_disappear_with_missing_or_empty_mapping(tmp_path, uses):
    handle, _, _, claim = setup_claim(tmp_path)
    result = assess(handle, claim, uses)
    assert result["usage"]["coverage"]["known_claims"] == 1
    assert result["usage"]["coverage"]["unmapped"] == ["claim"]
    assert result["usage"]["semantic_completeness"] == "UNKNOWN"


@pytest.mark.parametrize("provider,expected", [("host_tool", "UNKNOWN"), ("deterministic", "UNKNOWN")])
def test_provider_label_alone_cannot_change_effective_gate(tmp_path, provider, expected):
    handle, _, _, claim = setup_claim(tmp_path, provider=provider)
    result = assess(handle, claim, [usage(claim)])
    assert result["effective_card"]["gates"]["业绩真兑现"] == expected
    assert result["usage"]["claims"][0]["verdict"] == expected
    assert result["usage"]["identity"]["task_id"] == "card"
    assert result["usage"]["frame_sha256"] == "a" * 64


def test_unregistered_review_removes_fact_eligibility_without_inventing_fail(tmp_path):
    from tests.news.test_frozen_claim_sources import event
    handle, _, _, claim = setup_claim(tmp_path, source_event=dict(event(), amount_value="1"))
    result = assess(handle, claim, [usage(claim, "dimensions.盈利质量")])
    assert result["usage"]["claims"][0]["verdict"] == "UNKNOWN"
    assert result["effective_card"]["dimensions"]["盈利质量"] == "未核"


def test_background_unknown_is_disclosed_only(tmp_path):
    handle, _, _, claim = setup_claim(tmp_path, provider="host_tool")
    result = assess(handle, claim, [usage(claim, "BACKGROUND", "BACKGROUND")])
    assert result["effective_card"]["gates"]["业绩真兑现"] == "PASS"
    assert result["usage"]["gaps"] == []
    assert result["usage"]["semantic_completeness"] == "UNKNOWN"


@pytest.mark.parametrize("bad", ["duplicate", "hash", "background"])
def test_usage_contract_rejects_bad_mapping(tmp_path, bad):
    handle, _, _, claim = setup_claim(tmp_path)
    rows = [usage(claim)]
    if bad == "duplicate":
        rows *= 2
    elif bad == "hash":
        rows[0]["statement_sha256"] = "0" * 64
    else:
        rows[0]["relation"] = "BACKGROUND"
    with pytest.raises(ValueError):
        assess(handle, claim, rows)


def test_new_declaration_keeps_unknown_population(tmp_path):
    handle, _, _, claim = setup_claim(tmp_path)
    new = {"claim_id": "new", "statement_sha256": sha256_bytes(TEXT.encode())}
    result = assess(handle, claim, [usage(claim), usage(new, "entry")],
                    [{"claim_id": "new", "statement": TEXT}])
    assert result["usage"]["coverage"]["known_claims"] == 2
    assert result["usage"]["entry_status"] == "PENDING_EVIDENCE"
    assert result["usage"]["claims"][1]["verdict"] == "UNKNOWN"


@pytest.mark.parametrize("independent", [False, True])
def test_independent_bytes_still_need_authorized_field_verification(tmp_path, independent):
    from autoresearch.news.material_claims import bind_material_claim
    from tests.news.test_frozen_claim_sources import event, record
    handle, original, _, claim = setup_claim(tmp_path, provider="host_tool")
    body = TEXT + "\n公告编号B" if independent else TEXT
    source = record(handle, body, url="https://independent/b") if independent else original
    review = record(handle, {"schema_version": 1, "source_receipt_id": source["receipt_id"],
        "source_hash": source["payload_hash"], "event": event(),
        "checked_fields": list(event()), "reviewer": None}, provider="deterministic", endpoint="claim_fields.v1")
    alternate = bind_material_claim(handle.capsule, engine=handle.engine, run_id=handle.run_id,
        task_id="intel.600000", attempt=1, claim_id="alternative", statement=TEXT,
        source_receipt_ids=[source["receipt_id"]], quote_refs=[{"blob_hash": source["payload_hash"],
            "start": 0, "end": len(body), "text": body}], calculation_ids=[],
        claim_event=event(), review_receipt_ids=[review["receipt_id"]])
    result = assess(handle, claim, [usage(claim), usage(alternate, relation="ALTERNATIVE")])
    assert result["effective_card"]["gates"]["业绩真兑现"] == "UNKNOWN"


def test_alternative_without_required_premise_is_not_self_authorizing(tmp_path):
    handle, _, _, claim = setup_claim(tmp_path)
    result = assess(handle, claim, [usage(claim, relation="ALTERNATIVE")])
    assert result["effective_card"]["gates"]["业绩真兑现"] == "UNKNOWN"


def test_pass_never_upgrades_model_unknown_gate(tmp_path):
    from autoresearch.contracts.agent_output import OW_GATES
    handle, _, _, claim = setup_claim(tmp_path)
    text = decision_text(subject="600000", venue="XSHG", rating="Hold", deviation="风险管理",
                         gates=dict.fromkeys(OW_GATES, "UNKNOWN"))
    result = assess(handle, claim, [usage(claim)], text=text)
    assert result["effective_card"]["gates"]["业绩真兑现"] == "UNKNOWN"


def test_late_attempt_does_not_replace_accepted_claim(tmp_path):
    from autoresearch.news.material_claims import bind_material_claim
    handle, _, _, claim = setup_claim(tmp_path)
    bind_material_claim(handle.capsule, engine=handle.engine, run_id=handle.run_id,
        task_id="intel.600000", attempt=2, claim_id="claim", statement="迟到改稿",
        source_receipt_ids=[], quote_refs=[], calculation_ids=[], review_receipt_ids=[])
    result = assess(handle, claim, [usage(claim)])
    assert result["effective_card"]["gates"]["业绩真兑现"] == "UNKNOWN"
    assert result["usage"]["coverage"]["known_claims"] == 1


def test_cross_engine_sidecar_rejected_before_support(tmp_path):
    from autoresearch.news.material_claims import bind_material_claim
    handle, _, _, claim = setup_claim(tmp_path)
    bind_material_claim(handle.capsule, engine="claude", run_id=handle.run_id,
        task_id="intel.600000", attempt=1, claim_id="foreign", statement="wrong identity",
        source_receipt_ids=[], quote_refs=[], calculation_ids=[], review_receipt_ids=[])
    with pytest.raises(ValueError, match="engine/run"):
        assess(handle, claim, [usage(claim)])


def test_only_new_declaration_can_request_root_statement_hash(tmp_path):
    handle, _, _, claim = setup_claim(tmp_path)
    new = {"claim_id": "new", "statement_sha256": None}
    result = assess(handle, claim, [usage(claim), usage(new, "entry")],
                    [{"claim_id": "new", "statement": TEXT}])
    assert result["usage"]["uses"][1]["statement_sha256"] == sha256_bytes(TEXT.encode())
    row = usage(claim)
    row["statement_sha256"] = None
    with pytest.raises(ValueError, match="statement hash"):
        assess(handle, claim, [row])


def test_claim_entry_pending_survives_gate4_upgrade(tmp_path, monkeypatch):
    from autoresearch.news.card_claims import constrain_claim_execution
    from autoresearch.scan import exec_anchor
    block = constrain_claim_execution({"analysis_date": "2026-09-01", "actionability_status": "ACTIONABLE",
                                       "ready_quality": "estimated"}, ["600000"], ["600000"])
    (tmp_path / "manifest.json").write_text(json.dumps({"execution": block}))
    monkeypatch.setattr(exec_anchor, "_upgrade_with_gate4", lambda *_: {
        "analysis_date": "2026-09-01", "actionability_status": "ACTIONABLE", "ready_quality": "measured"})
    assert exec_anchor.read_execution(tmp_path)["actionability_status"] == "PENDING_EVIDENCE"
    assert constrain_claim_execution({"actionability_status": "ACTIONABLE"}, ["600000"], ["000001"])["actionability_status"] == "ACTIONABLE"


@pytest.mark.parametrize("url", ["https://issuer/a", "https://issuer/b?new=1", "https://sub.issuer/a"])
def test_refetched_or_same_publisher_source_is_not_independent(tmp_path, url):
    from autoresearch.news.material_claims import bind_material_claim
    from tests.news.test_frozen_claim_sources import event, record
    handle, _, _, claim = setup_claim(tmp_path, provider="host_tool")
    body = TEXT + "刷新网页文本"
    source = record(handle, body, url=url)
    review = record(handle, {"schema_version": 1, "source_receipt_id": source["receipt_id"],
        "source_hash": source["payload_hash"], "event": event(),
        "checked_fields": list(event()), "reviewer": None}, provider="deterministic", endpoint="claim_fields.v1")
    other = bind_material_claim(handle.capsule, engine=handle.engine, run_id=handle.run_id,
        task_id="intel.600000", attempt=1, claim_id="alternate", statement=TEXT,
        source_receipt_ids=[source["receipt_id"]], quote_refs=[{"blob_hash": source["payload_hash"],
            "start":0, "end":len(body), "text":body}], calculation_ids=[], claim_event=event(),
        review_receipt_ids=[review["receipt_id"]])
    result = assess(handle, claim, [usage(claim), usage(other, relation="ALTERNATIVE")])
    assert result["effective_card"]["gates"]["业绩真兑现"] == "UNKNOWN"


def test_other_stock_claim_from_global_ancestor_is_outside_card_population(tmp_path):
    handle, _, _, claim = setup_claim(tmp_path)
    population, _ = card_claims.claim_population(handle.capsule,
        identity={"engine": handle.engine, "run_id": handle.run_id, "task_id":"card", "attempt":1},
        accepted_attempts={"intel.600000":1}, frame=frame(), subject="000001.SZ",
        task_subjects={"intel.600000":"600000"})
    assert population == {}


def test_current_producer_wrong_stock_declaration_is_preserved_as_unknown(tmp_path):
    handle, _, _, claim = setup_claim(tmp_path)
    population, _ = card_claims.claim_population(handle.capsule,
        identity={"engine": handle.engine, "run_id": handle.run_id, "task_id":"intel.600000", "attempt":1},
        accepted_attempts={}, frame=frame(), subject="000001.SZ")
    assert population[claim["claim_id"]]["verdict"] == "UNKNOWN"
    assert population[claim["claim_id"]]["reason"] == "SUBJECT_MISMATCH"


def test_rejected_intel_claims_remain_unknown_in_decision_denominator(tmp_path, monkeypatch):
    from autoresearch.scan.l4.intel_guard import guard_intel
    from autoresearch.trace import frozen_sources
    from autoresearch.trace.source_receipts import read_receipts
    handle, _, _, _ = setup_claim(tmp_path)
    # Remove the independently created fixture claim; the guard must create its own population.
    for path in (handle.capsule / "evidence/material_claims").glob("*.json"):
        path.unlink()
    stage = tmp_path / "2026-09-01"
    stage.mkdir()
    (stage / "_l4_intel_600000.md").write_text(TEXT + " https://issuer/a\n网查 36 条\n")
    monkeypatch.setattr(frozen_sources, "intel_claim_sources", lambda _: {
        "handle":handle, "frame":frame(), "task_id":"intel.600000", "attempt":1,
        "receipts":read_receipts(handle.capsule)})
    guarded = guard_intel(stage, "600000", hard_cap=30)
    assert guarded["action"] == "REJECTED" and guarded["claim_events"]["n"] == 1
    result = assess(handle, {}, [])
    assert result["usage"]["coverage"]["known_claims"] == 1
    assert result["usage"]["coverage"]["unmapped"]
    assert result["usage"]["claims"][0]["verdict"] == "UNKNOWN"


def test_tracked_claim_binding_failure_blocks_missing_population(tmp_path, monkeypatch):
    from autoresearch.scan.l4 import intel_guard
    from autoresearch.trace import frozen_sources
    handle, _, _, _ = setup_claim(tmp_path)
    stage = tmp_path / "2026-09-01"
    stage.mkdir()
    (stage / "_l4_intel_600000.md").write_text(TEXT + "\n网查 36 条\n")
    monkeypatch.setattr(frozen_sources, "intel_claim_sources", lambda _: {
        "handle":handle, "frame":frame(), "task_id":"intel.600000", "attempt":1, "receipts":[]})
    def fail(*args, **kwargs):
        raise OSError("cannot persist source")
    monkeypatch.setattr(intel_guard, "_bind_frozen_event", fail)
    with pytest.raises(ValueError, match="population incomplete"):
        intel_guard.guard_intel(stage, "600000", hard_cap=30)


def test_alternative_follows_complete_correction_lineage(tmp_path):
    from autoresearch.news.material_claims import bind_material_claim
    from tests.news.test_frozen_claim_sources import event, record
    handle, original, _, claim = setup_claim(tmp_path, provider="host_tool")
    middle = record(handle, TEXT + "B", url="https://second.example/a", supersedes=[original["receipt_id"]])
    body = TEXT + "C"
    source = record(handle, body, url="https://third.example/a", supersedes=[middle["receipt_id"]])
    review = record(handle, {"schema_version":1, "source_receipt_id":source["receipt_id"],
        "source_hash":source["payload_hash"], "event":event(), "checked_fields":list(event()),
        "reviewer":None}, provider="deterministic", endpoint="claim_fields.v1")
    alternate = bind_material_claim(handle.capsule, engine=handle.engine, run_id=handle.run_id,
        task_id="intel.600000", attempt=1, claim_id="alternate", statement=TEXT,
        source_receipt_ids=[source["receipt_id"]], quote_refs=[{"blob_hash":source["payload_hash"],
            "start":0, "end":len(body), "text":body}], calculation_ids=[], claim_event=event(),
        review_receipt_ids=[review["receipt_id"]])
    result = assess(handle, claim, [usage(claim), usage(alternate, relation="ALTERNATIVE")])
    assert result["effective_card"]["gates"]["业绩真兑现"] == "UNKNOWN"
    row = next(row for row in result["usage"]["claims"] if row["claim_id"] == "alternate")
    assert original["receipt_id"] in row["source_receipt_ids"]


def test_conflicting_event_versions_never_merge_to_supported_claim(tmp_path, monkeypatch):
    from autoresearch.news.material_claims import bind_material_claim
    from tests.news.test_frozen_claim_sources import event
    handle, _, _, claim = setup_claim(tmp_path)
    bind_material_claim(handle.capsule, engine=handle.engine, run_id=handle.run_id,
        task_id="intel.600000", attempt=1, claim_id=claim["claim_id"], statement=TEXT,
        source_receipt_ids=[], quote_refs=[], calculation_ids=[],
        claim_event=dict(event(), amount_value="1"), review_receipt_ids=[])
    # Even individually supported versions cannot resolve conflicting event identity.
    monkeypatch.setattr(card_claims, "evaluate_material_claim", lambda *a, **k: {
        "claim_id":claim["claim_id"], "verdict":"PASS", "source":"PASS", "semantic":"PASS",
        "timing":"PASS", "conflict":"PASS", "received_by_cutoff":"PASS"})
    result = assess(handle, claim, [usage(claim)])
    assert result["effective_card"]["gates"]["业绩真兑现"] == "UNKNOWN"
    assert result["usage"]["claims"][0]["reason"] == "CURRENT_VERSION_AMBIGUOUS"


# ───── 复核视图(2026-10-02 真扫):本卡自有声明未证 ≠ 未复核;未证的上游前提照旧降级 ─────

_OWN = {"claim_id": "own", "statement_sha256": None}
_OWN_DECLARATION = [{"claim_id": "own", "statement": "2026H1 经营现金流/净利 1.18"}]


def test_own_unbound_declaration_keeps_reviewed_value_and_discloses_gap(tmp_path):
    handle, _, _, claim = setup_claim(tmp_path)
    result = assess(handle, claim, [usage(claim, "BACKGROUND", "BACKGROUND"),
                                    usage(_OWN, "dimensions.盈利质量", group="quality")], _OWN_DECLARATION)
    assert result["effective_card"]["dimensions"]["盈利质量"] == "未核"
    assert result["reviewed_card"]["dimensions"]["盈利质量"] == "强"
    assert [row["target"] for row in result["usage"]["gaps"]] == ["dimensions.盈利质量"]


def test_unverified_upstream_premise_also_demotes_reviewed_value(tmp_path):
    from tests.news.test_frozen_claim_sources import event
    handle, _, _, claim = setup_claim(tmp_path, source_event=dict(event(), amount_value="1"))
    result = assess(handle, claim, [usage(claim, "dimensions.盈利质量")])
    assert result["usage"]["claims"][0]["verdict"] == "UNKNOWN"
    assert result["reviewed_card"]["dimensions"]["盈利质量"] == "未核"


def test_group_with_any_unverified_upstream_premise_is_demoted_for_review(tmp_path):
    from tests.news.test_frozen_claim_sources import event
    handle, _, _, claim = setup_claim(tmp_path, source_event=dict(event(), amount_value="1"))
    result = assess(handle, claim, [usage(claim, "dimensions.盈利质量", group="quality"),
                                    usage(_OWN, "dimensions.盈利质量", group="quality")], _OWN_DECLARATION)
    assert result["reviewed_card"]["dimensions"]["盈利质量"] == "未核"


def test_own_declaration_offered_only_as_alternative_is_still_demoted_for_review(tmp_path):
    handle, _, _, claim = setup_claim(tmp_path)
    result = assess(handle, claim, [usage(claim, "BACKGROUND", "BACKGROUND"),
                                    usage(_OWN, relation="ALTERNATIVE")], _OWN_DECLARATION)
    assert result["reviewed_card"]["gates"]["业绩真兑现"] == "UNKNOWN"
