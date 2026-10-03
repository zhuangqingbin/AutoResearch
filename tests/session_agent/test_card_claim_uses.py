import json

import pytest

from autoresearch.common.atomic import atomic_write_json, sha256_bytes
from autoresearch.news.material_claims import bind_material_claim
from autoresearch.session_agent import artifacts, validation
from tests.common.test_card_decision_v3 import decision_text
from tests.news.test_card_claims import usage
from tests.session_agent.test_card_v3_production import setup_card


def setup_usage(tmp_path, *, mode="FULL", relation="REQUIRED", target="gates.业绩真兑现", rating="Buy",
                card_text=None):
    subject, venue = ("600000", "XSHG") if mode == "scan" else ("NVDA", "XNAS")
    text = card_text
    if text is None:
        text = decision_text(subject=subject, venue=venue, rating=rating, deviation="风险管理")
        if mode == "scan":
            text += "进入P4倾向: 中性\n"
        mapping = {"schema_version": 1, "declarations": [], "uses": [usage(
            {"claim_id": "claim", "statement_sha256": sha256_bytes("公司确定受益".encode())}, target, relation)]}
        text += "\n```decision-claim-uses-v1\n" + json.dumps(mapping, ensure_ascii=False) + "\n```\n"
    handle, task, submission = setup_card(tmp_path, subject, venue, text)
    if mode == "LITE":
        task.update(task_id="stock.card", output_artifact_ids=["stock.card.output"])
    if mode == "scan":
        task.update(task_id="l4.600000.a1.card", role="scan.l4.card")
        finalists = handle.staging / "finalists.csv"
        finalists.write_text("code,lane\n600000,selected\n")
        artifacts.register_artifact(handle, "scan.finalists", finalists, "READ")
    task["dependencies"] = ["intel"]
    atomic_write_json(handle.workspace / "session/tasks.json", {
        "schema_version": 1, "engine": handle.engine, "run_id": handle.run_id, "tasks": {
            "intel": {"state": "SUCCEEDED", "attempt": 1,
                      "spec": {"task_id": "intel", "dependencies": [], "output_artifact_ids": ["intel"]}},
            task["task_id"]: {"state": "RUNNING", "attempt": 1, "spec": task}}})
    bind_material_claim(handle.capsule, engine=handle.engine, run_id=handle.run_id,
        task_id="intel", attempt=1, claim_id="claim", statement="公司确定受益",
        source_receipt_ids=[], quote_refs=[], calculation_ids=[], review_receipt_ids=[])
    return handle, task, submission, text


@pytest.mark.parametrize("mode", ["FULL", "LITE", "scan"])
def test_submit_owner_rejects_unsupported_buy_and_retains_binding(tmp_path, mode):
    handle, task, submission, text = setup_usage(tmp_path, mode=mode)
    validator = {"FULL": validation._stock_pm, "LITE": validation._stock_lite,
                 "scan": validation._scan_l4_card}[mode]
    with pytest.raises(validation.DomainValidationError, match="required material support"):
        validator(handle, submission, task)
    attachment = json.loads(next((handle.capsule / "evidence/card_claim_uses").glob("*.json")).read_text())
    assert attachment["claims"][0]["verdict"] == "UNKNOWN"
    assert attachment["identity"]["task_id"] == task["task_id"]
    assert attachment["frame_sha256"] == artifacts.snapshot_artifact(handle, "research.frame")["sha256"]
    assert (handle.staging / "decision.md").read_text() == text


def test_full_publish_rechecks_claims_not_only_raw_gate(tmp_path, monkeypatch):
    import sys

    from autoresearch.analyze import assemble
    from autoresearch.news.card_claims import bound_claim_context
    from tests.analyze.test_assemble import _populate_required_files
    handle, _, _, text = setup_usage(tmp_path)
    root = tmp_path / "NVDA_20260913"
    _populate_required_files(root, text)
    monkeypatch.setattr(sys, "argv", ["assemble.py", str(root)])
    context = {"rules_version": "skills-gap-v3", "subject": "NVDA",
        "frame": json.loads(artifacts.read_bytes(handle, "research.frame")),
        "frame_hash": artifacts.snapshot_artifact(handle, "research.frame")["sha256"],
        "claim_context": bound_claim_context(handle, artifact_id="stock.full.4_decision.decision")}
    assert assemble.main(reports_root=tmp_path / "reports", context_root=tmp_path / "context_codex",
                         decision_context=context) == 1
    assert not (tmp_path / "reports").exists()


def test_lite_prepare_preserves_background_unknown_and_reports_coverage(tmp_path):
    from autoresearch.session_agent.domain_ops import stock_prepare_publication, stock_validate
    handle, _, _, _ = setup_usage(tmp_path, mode="LITE", relation="BACKGROUND", target="BACKGROUND", rating="Hold")
    stock_validate(handle)
    path = handle.staging / "session_outputs/card.validation.json"
    artifacts.register_artifact(handle, "stock.card.validation", path, "READ")
    value = stock_prepare_publication(handle)
    assert value["rating"] == "Hold"
    assert value["claim_usage"]["coverage"]["fraction"] == 1
    assert value["claim_usage"]["semantic_completeness"] == "UNKNOWN"


def test_scan_publish_rechecks_unsupported_accepted_card(tmp_path, monkeypatch):
    from autoresearch.scan.publisher import publish_details
    from autoresearch.trace import capsule
    handle, task, _, text = setup_usage(tmp_path, mode="scan")
    owner_path = handle.workspace / "session/tasks.json"
    owner = json.loads(owner_path.read_text())
    owner["tasks"][task["task_id"]].update(state="SUCCEEDED", outputs=[{
        "artifact_id": task["output_artifact_ids"][0], "sha256": sha256_bytes(text.encode())}])
    atomic_write_json(owner_path, owner)
    details = handle.staging / "details"
    details.mkdir()
    (details / "600000.md").write_text(text)
    monkeypatch.setenv("AUTORESEARCH_RUN_ID", handle.run_id)
    monkeypatch.setattr(capsule, "require_active_run", lambda _: handle)
    destination = tmp_path / "published"
    destination.mkdir()
    with pytest.raises(ValueError, match="required material support"):
        publish_details(handle.staging, destination)
    assert list(destination.iterdir()) == []


@pytest.mark.parametrize("target,holding,error", [
    ("dimensions.盈利质量", True, "PINNED_QUALITY_UNREVIEWED"),
    ("gates.业绩真兑现", False, "unmapped material claims"),
])
def test_hold_cannot_hide_holding_gap_or_missing_known_mapping(tmp_path, target, holding, error):
    from autoresearch.news.card_claims import bound_claim_context, validate_claimed_decision
    handle, task, _, text = setup_usage(tmp_path, rating="Hold", target=target)
    if holding:
        text = text.replace('"holding": false', '"holding": true')
    else:
        text = text.split("```decision-claim-uses-v1")[0]
    with pytest.raises(ValueError, match=error):
        validate_claimed_decision(text, subject="NVDA",
            frame=json.loads(artifacts.read_bytes(handle, "research.frame")),
            frame_hash=artifacts.snapshot_artifact(handle, "research.frame")["sha256"],
            context=bound_claim_context(handle, task=task), holding=holding)


def test_two_stage_scan_publication_binds_only_card_bytes(tmp_path, monkeypatch):
    from autoresearch.scan.l4.card_io import frozen_claim_semantics
    from autoresearch.trace import capsule
    handle, task, _, text = setup_usage(tmp_path, mode="scan", rating="Hold", relation="BACKGROUND", target="BACKGROUND")
    owner_path = handle.workspace / "session/tasks.json"
    owner = json.loads(owner_path.read_text())
    entry = owner["tasks"][task["task_id"]]
    entry["spec"]["output_artifact_ids"].append("scan.l4.600000.a1.changes")
    entry.update(state="SUCCEEDED", outputs=[{"artifact_id": task["output_artifact_ids"][0],
        "sha256": sha256_bytes(text.encode())}, {"artifact_id": "scan.l4.600000.a1.changes", "sha256": "b" * 64}])
    atomic_write_json(owner_path, owner)
    monkeypatch.setenv("AUTORESEARCH_RUN_ID", handle.run_id)
    monkeypatch.setattr(capsule, "require_active_run", lambda _: handle)
    result = frozen_claim_semantics(handle.staging, text, code="600000")
    assert result["claim_usage"]["card_sha256"] == sha256_bytes(text.encode())
    with pytest.raises(ValueError, match="accepted producer"):
        frozen_claim_semantics(handle.staging, text + "changed", code="600000")


def test_real_submit_rejects_new_unsupported_premise_without_promoting_attempt(tmp_path, monkeypatch):
    from pathlib import Path
    from types import SimpleNamespace

    from autoresearch.common.atomic import canonical_json
    from autoresearch.session_agent import host_evidence, service, store
    from tests.session_agent.test_service import _handle, _request
    from tests.session_agent.test_two_stage_cards import _write
    handle = _handle(tmp_path)
    handle.contract.created_at = "2026-09-13T12:00:00Z"
    request = dict(_request(), schema_version=3, subject="NVDA",
        card_research_profile="single-stage-v1",
        research_context={"venue": "XNAS", "usage": "standalone", "calendar_source_path": None})
    service.begin(request, begin_capsule=lambda _: handle)
    def loader(_):
        return handle
    monkeypatch.setattr(host_evidence, "resolve_receipt_evidence", lambda *args: [])
    service.claim(handle.run_id, "stock.harvest", 1, handle_loader=loader)
    def harvest(*args, **kwargs):
        for key in ("stock.slim", "stock.deep"):
            path = artifacts.declared_path(handle, key)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("raw facts")
        return SimpleNamespace(exit_code=0, invocation={"status": "COMPLETED"})
    service.execute(handle.run_id, "stock.harvest", 1,
        {"ticker": "NVDA", "analysis_date": request["analysis_date"], "asset_type": "stock", "peers": [], "slim": True},
        handle_loader=loader, runner=harvest)
    claimed = service.claim(handle.run_id, "stock.card", 1, handle_loader=loader,
                            event_recorder=lambda *args, **kwargs: None)
    mapping = {"schema_version": 1, "declarations": [{"claim_id": "new", "statement": "公司确定受益"}],
        "uses": [usage({"claim_id": "new", "statement_sha256": None})]}
    text = decision_text() + "\n```decision-claim-uses-v1\n" + json.dumps(mapping) + "\n```\n"
    _write(handle, "stock.card.output", text)
    task = service._task(handle, "stock.card")
    output = Path(artifacts.output_paths(handle, task, 1)["stock.card.output"])
    receipt = {"schema_version": 1, "engine": "codex", "session_ref": "session-main", "context_ref": "decision-context",
        "parent_context_ref": "session-main", "task_id": "stock.card", "attempt": 1,
        "completed": True, "evidence_refs": ["host-binding:" + "1" * 64]}
    submission = {"schema_version": 1, "envelope": claimed["result"]["envelope"], "plan_hash": claimed["result"]["plan_hash"],
        "outputs": [{"artifact_id": "stock.card.output", "sha256": sha256_bytes(output.read_bytes())}],
        "host_receipt_id": sha256_bytes(canonical_json(receipt).encode())}
    with pytest.raises(validation.DomainValidationError, match="required material support"):
        service.submit(handle.run_id, submission, handle_loader=loader, host_receipt=receipt,
                       event_recorder=lambda *args, **kwargs: None)
    assert output.read_text() == text
    assert store.read_states(handle.workspace / "session/tasks.json")["stock.card"] != "SUCCEEDED"
    assert store.read_states(handle.workspace / "session/tasks.json")["stock.publish"] == "PENDING"


# ───── 2026-10-02 首场 session_v1 真扫:三张卡被「机器视图」拒掉,整场不能发布 ─────
#
# 卡内新声明的事实(读自冻结 slim/deep)目前没有根来源绑定(S01 未接线),恒为 UNKNOWN/SOURCE_NOT_BOUND;
# 被它们 REQUIRED 支撑的维度在有效卡里降为 未核。复核检查若读这张有效卡:持仓卡恒报
# PINNED_QUALITY_UNREVIEWED(688981 Underweight、300750 Hold 都挂),与自评一致的非 Hold 卡被要求写
# 偏离理由(688202)——而不映射事实的卡反而按自评放行。复核检查改读「复核视图」:只因本卡自有声明
# 未证而降级的项保留模型复核值;任何未证的上游前提照旧降级;机器视图照旧记录、Buy/OW 支持门不变。

_UPSTREAM = {"claim_id": "claim", "statement_sha256": sha256_bytes("公司确定受益".encode())}
_UW_DIMS = {"基本面": "中", "估值": "弱", "技术资金": "中", "盈利质量": "未核", "偿付": "未核", "催化": "弱"}
_UW_GATES = {"主力真在": "PASS", "业绩真兑现": "PASS", "估值不透支": "FAIL"}
_UW_TARGETS = ["dimensions.基本面", "dimensions.估值", "dimensions.技术资金", "dimensions.催化"]
_HOLDING_DIMS = {"基本面": "强", "估值": "强", "技术资金": "弱", "盈利质量": "中", "偿付": "强", "催化": "弱"}


def own_fact_card(*, rating, dims, targets, gates=None, holding=False, subject="NVDA", venue="XNAS"):
    """`targets` rest only on facts this card declares itself; the known upstream claim is background."""
    text = decision_text(subject=subject, venue=venue, rating=rating, dimensions=dims, gates=gates,
                         holding=holding)
    if rating in {"Underweight", "Sell"}:
        text = text.replace("FINAL TRANSACTION PROPOSAL: HOLD", "FINAL TRANSACTION PROPOSAL: SELL")
    declarations = [{"claim_id": f"own{i}", "statement": f"冻结 slim 读数 {i}"} for i in range(len(targets))]
    uses = [usage(_UPSTREAM, "BACKGROUND", "BACKGROUND")] + [
        usage({"claim_id": row["claim_id"], "statement_sha256": None}, target, group=target)
        for row, target in zip(declarations, targets)]
    mapping = {"schema_version": 1, "declarations": declarations, "uses": uses}
    return text + "\n```decision-claim-uses-v1\n" + json.dumps(mapping, ensure_ascii=False) + "\n```\n"


def claimed(handle, task, text, *, holding):
    from autoresearch.news.card_claims import bound_claim_context, validate_claimed_decision
    return validate_claimed_decision(text, subject="NVDA",
        frame=json.loads(artifacts.read_bytes(handle, "research.frame")),
        frame_hash=artifacts.snapshot_artifact(handle, "research.frame")["sha256"],
        context=bound_claim_context(handle, task=task), holding=holding)


def test_reviewed_holding_quality_resting_on_own_facts_is_not_unreviewed(tmp_path):
    text = own_fact_card(rating="Hold", dims=_HOLDING_DIMS, holding=True,
                         targets=["dimensions.盈利质量", "dimensions.偿付"])
    handle, task, _, text = setup_usage(tmp_path, card_text=text)
    result = claimed(handle, task, text, holding=True)
    assert sorted(row["target"] for row in result["claim_usage"]["gaps"]) == ["dimensions.偿付", "dimensions.盈利质量"]
    assert result["effective_card"]["dimensions"]["盈利质量"] == "未核"


def test_rating_matching_own_scores_needs_no_deviation_reason_for_unbound_own_facts(tmp_path):
    text = own_fact_card(rating="Underweight", dims=_UW_DIMS, gates=_UW_GATES, targets=_UW_TARGETS)
    handle, task, _, text = setup_usage(tmp_path, card_text=text)
    result = claimed(handle, task, text, holding=False)
    assert result["machine_suggestion"] == "Hold"


def test_rating_off_own_scores_still_needs_deviation_reason(tmp_path):
    text = own_fact_card(rating="Hold", dims=_UW_DIMS, gates=_UW_GATES, targets=_UW_TARGETS)
    handle, task, _, text = setup_usage(tmp_path, card_text=text)
    with pytest.raises(ValueError, match="rating deviation requires explicit justification"):
        claimed(handle, task, text, holding=False)


def test_scan_row_accepts_reviewed_holding_card_and_keeps_verified_gate_facts(tmp_path, monkeypatch):
    from autoresearch.scan.l4.parsers import _finalist_row
    from autoresearch.trace import capsule
    text = own_fact_card(rating="Hold", dims=_HOLDING_DIMS, holding=True, subject="600000", venue="XSHG",
                         targets=["dimensions.盈利质量", "dimensions.偿付", "gates.业绩真兑现"])
    handle, task, _, text = setup_usage(tmp_path, mode="scan", card_text=text)
    owner_path = handle.workspace / "session/tasks.json"
    owner = json.loads(owner_path.read_text())
    owner["tasks"][task["task_id"]].update(state="SUCCEEDED", outputs=[{
        "artifact_id": task["output_artifact_ids"][0], "sha256": sha256_bytes(text.encode())}])
    atomic_write_json(owner_path, owner)
    details = handle.staging / "details"
    details.mkdir()
    (details / "600000.md").write_text(text)
    monkeypatch.setenv("AUTORESEARCH_RUN_ID", handle.run_id)
    monkeypatch.setattr(capsule, "require_active_run", lambda _: handle)
    row = _finalist_row(handle.staging, {"code": "600000", "lane": "pinned"})
    assert row["card_incomplete"] is False, row.get("card_validation_error")
    assert row["_card_facts"]["gate_states"]["业绩真兑现"] == "UNKNOWN"
