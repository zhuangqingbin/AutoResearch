import importlib

import pytest


def module():
    return importlib.import_module("autoresearch.research.quality_eval")


def test_labels_and_denominators_are_not_profit():
    rows = [{"case_id": "a", "gold": "CONTRADICTED", "predicted": "SUPPORTED"},
            {"case_id": "b", "gold": "SUPPORTED", "predicted": "SUPPORTED"},
            {"case_id": "c", "gold": None, "predicted": "SUPPORTED"},
            {"case_id": "d", "gold": "INSUFFICIENT", "predicted": None}]
    result = module().label_metrics(rows)
    assert result["population"] == 4 and result["evaluated"] == 2
    assert result["accuracy"] == 0.5
    assert result["false_pass_rate"] == 0.5
    assert result["unknown_gold"] == 1 and result["ungraded"] == 1


def test_zero_denominator_is_unknown():
    result = module().label_metrics([])
    assert result["accuracy"] is None and result["false_pass_rate"] is None


def test_duplicate_and_invalid_labels_rejected():
    for rows in [[{"case_id": "a", "gold": "WINNER", "predicted": None}],
                 [{"case_id": "a", "gold": None, "predicted": None}] * 2]:
        with pytest.raises(ValueError):
            module().label_metrics(rows)


def test_evaluate_bound_candidate_preserves_unreviewed_denominator(tmp_path, monkeypatch):
    from autoresearch.common import workspace as ws
    from autoresearch.common.atomic import canonical_json, sha256_bytes
    from autoresearch.research.casebook import freeze_casebook
    from autoresearch.research.evidence_refs import read_json_ref, write_derived
    from tests.contracts.test_research_case import case
    from tests.research.test_casebook import SPLITS
    monkeypatch.chdir(tmp_path)
    book = freeze_casebook([case(engine=ws.ENGINE)], split_spec=SPLITS,
                          output_dir=ws.context_root() / "book")
    frozen = read_json_ref(book)["cases"][0]
    grader = {"kind": "MODEL", "version": "v1", "prompt_hash": "a" * 64,
              "model": "UNKNOWN", "split": "train", "calibration_ref": None}
    candidate = {"casebook_sha256": book["sha256"],
        "grader_spec_hash": sha256_bytes(canonical_json(grader).encode()), "grades": [{
            "case_id": frozen["case_id"], "case_sha256": sha256_bytes(canonical_json(frozen).encode()),
            "label": "SUPPORTED", "rationale": "synthetic model proposal"}]}
    candidate_ref = write_derived(ws.context_root() / "candidate.json", candidate)
    result = module().evaluate_cases(book, candidate_ref=candidate_ref, grader_spec=grader)
    assert result["metrics"]["population"] == 1
    assert result["metrics"]["evaluated"] == 0
    assert result["calibration_state"] == "UNKNOWN"
    assert result["quality_improvement"] == "NOT_ESTABLISHED"
    with pytest.raises(ValueError, match="identity"):
        module().evaluate_cases(book, candidate_ref=candidate_ref, grader_spec=dict(grader, version="v2"))


def test_deterministic_fields_preserve_missing_and_types(tmp_path, monkeypatch):
    from autoresearch.common import workspace as ws
    from autoresearch.research.evidence_refs import write_derived
    monkeypatch.chdir(tmp_path)
    gold = write_derived(ws.context_root() / "gold.json", {"amount": 1, "unit": "万元", "date": "2026-01-01"})
    candidate = write_derived(ws.context_root() / "candidate.json", {"amount": True, "unit": "亿元"})
    result = module().grade_exact_fields(gold, candidate, pointers=["/amount", "/unit", "/date"])
    assert [r["label"] for r in result["checks"]] == ["CONTRADICTED", "CONTRADICTED", "INSUFFICIENT"]
    assert result["label"] == "CONTRADICTED"


def test_recall_counts_all_known_gold_including_ungraded():
    rows = [{"case_id": str(i), "gold": "SUPPORTED", "predicted": "SUPPORTED" if i == 0 else None}
            for i in range(10)]
    result = module().label_metrics(rows)
    assert result["labels"]["SUPPORTED"]["actual"] == 10
    assert result["labels"]["SUPPORTED"]["recall"] == 0.1
    assert result["known_gold_coverage"] == 0.1
    assert result["omission_rate"] == 0.9


def test_nested_boolean_cannot_equal_numeric(tmp_path, monkeypatch):
    from autoresearch.common import workspace as ws
    from autoresearch.research.evidence_refs import write_derived
    monkeypatch.chdir(tmp_path)
    gold = write_derived(ws.context_root() / "gold.json", {"object": {"a": 1}, "list": [1]})
    candidate = write_derived(ws.context_root() / "candidate.json", {"object": {"a": True}, "list": [True]})
    result = module().grade_exact_fields(gold, candidate, pointers=["/object", "/list"])
    assert all(r["label"] == "CONTRADICTED" for r in result["checks"])
