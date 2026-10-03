"""Derived audit cannot grant source PASS or claim semantic completeness."""
import importlib
import json

import pytest

from autoresearch.common import workspace as ws
from autoresearch.common.atomic import canonical_json, sha256_bytes


def audit_module():
    return importlib.import_module("autoresearch.research.material_conclusions")


def ref(tmp_path, monkeypatch, name, value):
    monkeypatch.chdir(tmp_path)
    root = ws.context_root() / "audit"
    root.mkdir(parents=True, exist_ok=True)
    data = value.encode() if isinstance(value, str) else json.dumps(value).encode()
    path = root / name
    path.write_bytes(data)
    return {"path": str(path), "sha256": sha256_bytes(data)}


def declaration(**changes):
    return dict({"conclusion_id": "c1", "statement": "计划回购",
                 "kind": "FACT", "target": "catalyst", "claim_ids": ["claim1"],
                 "calculation_refs": [], "premise_ids": [], "counterevidence_ids": []}, **changes)


def document(rows):
    return "# Report\n```material-conclusions-v1\n" + json.dumps(
        {"schema_version": 1, "conclusions": rows}) + "\n```\n"


def declarations_hash(rows=None):
    return sha256_bytes(canonical_json(rows or [declaration()]).encode())


def run(tmp_path, monkeypatch, rows=None, reviews=(), claims=None, workflow="stock-full"):
    doc = ref(tmp_path, monkeypatch, "report.md", document(rows or [declaration()]))
    evidence = ref(tmp_path, monkeypatch, "claims.json", {"schema_version": 1,
        "identity": {"engine": ws.ENGINE, "run_id": "synthetic-test", "task_id": "t1", "attempt": 1},
        "card_sha256": doc["sha256"], "claims": claims or [
        {"claim_id": "claim1", "verdict": "PASS", "source_receipt_ids": ["r1"]}]})
    return audit_module().audit_material_conclusions(
        doc, workflow=workflow, claim_usage_refs=[evidence], review_refs=reviews)


@pytest.mark.parametrize("workflow", ["stock-lite", "stock-full", "macro-full", "sector-full"])
def test_all_workflows_declared_only(tmp_path, monkeypatch, workflow):
    result = run(tmp_path, monkeypatch, workflow=workflow)
    assert result["coverage_state"] == "DECLARED_ONLY"
    assert result["known_material_count"] == result["mapped_count"] == 1
    assert result["semantic_completeness"] == "UNKNOWN"
    assert result["production_effect"] == "NONE"


def test_supported_premises_never_prove_inference(tmp_path, monkeypatch):
    result = run(tmp_path, monkeypatch, [declaration(kind="INFERENCE", premise_ids=["claim1"])])
    assert result["conclusions"][0]["truth_status"] == "NOT_DETERMINED"
    assert result["conclusions"][0]["dependency_state"] == "REFERENCED_NOT_REVERIFIED"


def test_missing_premise_and_counterevidence_remain_unresolved(tmp_path, monkeypatch):
    result = run(tmp_path, monkeypatch, [declaration(kind="INFERENCE", premise_ids=["missing"],
                                                    counterevidence_ids=["debt"])])
    assert {r["claim_id"] for r in result["unresolved_conflicts"]} == {"missing", "debt"}


@pytest.mark.parametrize("finding", ["正文关键催化未声明", "关键负面误列背景", "原文只支持计划", "经理摘要遗漏偿债反证"])
def test_review_omissions_are_candidates_not_source_verdicts(tmp_path, monkeypatch, finding):
    doc = ref(tmp_path, monkeypatch, "report.md", document([declaration()]))
    review = ref(tmp_path, monkeypatch, "review.json", {
        "schema_version": 1, "document_sha256": doc["sha256"], "reviewer_kind": "MODEL",
        "declarations_sha256": declarations_hash(),
        "scope": ["c1"], "omission_candidates": [finding], "unresolved_conflicts": []})
    result = run(tmp_path, monkeypatch, reviews=[review])
    assert result["coverage_state"] == "AUDITED_WITH_GAPS"
    assert result["omission_candidates"] == [finding]
    assert result["conclusions"][0]["truth_status"] == "NOT_DETERMINED"


def test_review_wrong_document_rejected(tmp_path, monkeypatch):
    review = ref(tmp_path, monkeypatch, "review.json", {
        "schema_version": 1, "document_sha256": "0" * 64, "reviewer_kind": "HUMAN",
        "declarations_sha256": declarations_hash(),
        "scope": ["c1"], "omission_candidates": [], "unresolved_conflicts": []})
    with pytest.raises(ValueError, match="document"):
        run(tmp_path, monkeypatch, reviews=[review])


def test_hash_and_symlink_escape_rejected(tmp_path, monkeypatch):
    doc = ref(tmp_path, monkeypatch, "report.md", document([declaration()]))
    doc["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="hash"):
        audit_module().audit_material_conclusions(doc, workflow="stock-full")
    outside = tmp_path / "outside"
    outside.write_text("secret")
    link = ws.context_root() / "audit" / "link"
    link.symlink_to(outside)
    with pytest.raises(ValueError, match="scope"):
        audit_module().audit_material_conclusions({"path": str(link), "sha256": "0" * 64}, workflow="stock-full")


def test_no_mapping_does_not_mean_no_material_claims(tmp_path, monkeypatch):
    doc = ref(tmp_path, monkeypatch, "report.md", "# buy because revenue doubled")
    result = audit_module().audit_material_conclusions(doc, workflow="stock-full")
    assert result["coverage_state"] == "DECLARED_ONLY"
    assert result["known_material_count"] == 0
    assert result["semantic_completeness"] == "UNKNOWN"


def test_audited_only_covers_declared_scope(tmp_path, monkeypatch):
    doc = ref(tmp_path, monkeypatch, "report.md", document([declaration()]))
    review = ref(tmp_path, monkeypatch, "review.json", {
        "schema_version": 1, "document_sha256": doc["sha256"], "reviewer_kind": "HUMAN",
        "declarations_sha256": declarations_hash(),
        "scope": ["c1"], "omission_candidates": [], "unresolved_conflicts": []})
    result = run(tmp_path, monkeypatch, reviews=[review])
    assert result["coverage_state"] == "AUDITED"
    assert result["semantic_completeness"] == "UNKNOWN"


def test_supported_counterevidence_is_not_silently_resolved(tmp_path, monkeypatch):
    result = run(tmp_path, monkeypatch, [declaration(counterevidence_ids=["claim1"])])
    assert result["unresolved_conflicts"][0]["reason"] == "SUPPORTED_COUNTEREVIDENCE_REQUIRES_REVIEW"


def test_duplicate_claim_versions_rejected(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="conflicting"):
        run(tmp_path, monkeypatch, claims=[{"claim_id": "x", "verdict": "PASS"},
                                         {"claim_id": "x", "verdict": "FAIL"}])


def test_unbound_pass_blob_is_rejected(tmp_path, monkeypatch):
    doc = ref(tmp_path, monkeypatch, "report.md", document([declaration()]))
    claims = ref(tmp_path, monkeypatch, "claims.json", {"claims": [{"claim_id": "claim1", "verdict": "PASS"}]})
    with pytest.raises(ValueError, match="binding"):
        audit_module().audit_material_conclusions(doc, workflow="stock-full", claim_usage_refs=[claims])


@pytest.mark.parametrize("workflow", ["stock-lite", "stock-full", "macro-full", "sector-full"])
def test_offline_producer_handles_existing_prose_with_bound_sidecar(tmp_path, monkeypatch, workflow):
    from tests.research.test_experiment_io import spec
    doc = ref(tmp_path, monkeypatch, "report.md", "# Original FULL report\n计划回购仍未完成。")
    spec_ref = ref(tmp_path, monkeypatch, "spec.json", spec(engine=ws.ENGINE,
        experiment_family="material_conclusion_audit_v1"))
    task = audit_module().prepare_material_audit(spec_ref, doc, workflow=workflow)
    assert task["document_ref"] == doc and "material_negative" in task["required_targets"]
    sidecar = ref(tmp_path, monkeypatch, "declarations.json", {"schema_version": 1,
        "document_sha256": doc["sha256"], "conclusions": [declaration()]})
    result = audit_module().audit_material_conclusions(doc, workflow=workflow, declaration_ref=sidecar)
    assert result["known_material_count"] == 1 and result["production_effect"] == "NONE"


def test_stale_review_cannot_cover_changed_sidecar(tmp_path, monkeypatch):
    doc = ref(tmp_path, monkeypatch, "report.md", "原始正文未改")
    sidecar = ref(tmp_path, monkeypatch, "changed.json", {"schema_version": 1,
        "document_sha256": doc["sha256"], "conclusions": [declaration(statement="回购已完成")]})
    review = ref(tmp_path, monkeypatch, "review.json", {"schema_version": 1,
        "document_sha256": doc["sha256"], "declarations_sha256": declarations_hash(),
        "reviewer_kind": "MODEL", "scope": ["c1"], "omission_candidates": [], "unresolved_conflicts": []})
    with pytest.raises(ValueError, match="binding"):
        audit_module().audit_material_conclusions(doc, workflow="stock-full",
            declaration_ref=sidecar, review_refs=[review])
