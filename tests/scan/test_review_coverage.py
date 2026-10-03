import pytest

from autoresearch.scan import decision_finalize as decisions
from autoresearch.scan.decision_record import DecisionRecord
from tests.scan.test_decision_record import _record


@pytest.mark.parametrize("rating,pinned,record,status,trigger", [
    ("Hold", False, None, "NOT_REQUIRED", None),
    ("Overweight", False, None, "MISSING", "ow_review"),
    ("Sell", True, None, "MISSING", "sell_review"),
    ("Sell", False, None, "NOT_REQUIRED", None),
    ("Overweight", False, {"ratings": ["Overweight", "Overweight"], "early_stopped": True}, "COMPLETE", "ow_review"),
    ("Overweight", False, {"ratings": ["Overweight", "Hold"]}, "INCOMPLETE", "ow_review"),
    ("Overweight", False, {"ratings": ["Overweight", "Hold", "Hold"]}, "COMPLETE", "ow_review"),
    ("Overweight", False, {"ratings": ["Overweight", "Hold"], "degraded": True}, "INCOMPLETE", "ow_review"),
    ("Sell", True, {"ratings": ["Sell", "Hold", "Hold"], "trigger": "sell_review"}, "COMPLETE", "sell_review"),
])
def test_review_coverage_tracks_actual_results(rating, pinned, record, status, trigger):
    value = decisions.review_coverage(rating, pinned=pinned, record=record)
    assert value["review_status"] == status
    assert value["review_trigger"] == trigger
    assert value["review_required"] is (trigger is not None)
    assert value["review_policy_version"] and value["review_coverage_reason"]


def test_historical_record_preserves_original_hash_and_unknown_coverage():
    from autoresearch.scan.run_contract import sha256_json
    raw = _record().to_dict()
    raw["schema_version"] = 1
    for field in list(raw):
        if field.startswith("review_") or field == "post_verify_rating":
            raw.pop(field)
    raw["record_hash"] = sha256_json({k: v for k, v in raw.items() if k != "record_hash"})
    loaded = DecisionRecord.from_dict(raw)
    assert loaded.review_status == "UNKNOWN"
    assert loaded.to_dict() == raw


def test_unknown_initial_rating_does_not_claim_outside_review_policy():
    value = decisions.review_coverage("—", pinned=False, record=None)
    assert value["review_status"] == "UNKNOWN"
    assert value["review_required"] is None


def test_v2_record_must_explicitly_declare_review_fields():
    raw = _record().to_dict()
    raw.pop("review_status")
    with pytest.raises(ValueError, match="fields"):
        DecisionRecord.from_dict(raw)


@pytest.mark.parametrize("record", [
    {"ratings": ["Overweight", {}, "Hold"]},
    {"ratings": ["Overweight", "Hold", "Hold"], "median": "Buy"},
    {"ratings": ["Overweight", "Hold", "Hold"], "n_runs": 1},
    {"ratings": 3},
])
def test_malformed_review_never_crashes_or_claims_complete(record):
    value = decisions.review_coverage("Overweight", pinned=False, record=record)
    assert value["review_status"] == "INCOMPLETE"


def test_review_complete_requires_actual_results_in_record_builder():
    raw = _record().to_dict()
    raw.pop("record_hash")
    raw.pop("schema_version")
    raw.update(review_policy_version="ow-pinned-sell-v1", review_trigger="ow_review",
               review_required=True, review_status="COMPLETE", review_coverage_reason="three_results_observed")
    with pytest.raises(ValueError, match="review results"):
        DecisionRecord.build(**raw)


def test_incomplete_new_review_cannot_fold_but_legacy_replay_is_preserved():
    record = {"median": "Hold"}
    current = decisions.review_coverage("Overweight", pinned=False, record=record)
    historical = decisions.review_coverage("Overweight", pinned=False, record=record, legacy=True)
    assert decisions.fold_review("Overweight", record, strict=True, coverage=current) == "Overweight"
    assert decisions.fold_review("Overweight", record, strict=False, coverage=historical) == "Hold"
    assert historical["review_status"] == "UNKNOWN"


def test_malformed_json_review_is_isolated_before_report_consumers(tmp_path):
    import json
    path = tmp_path / "_ensemble_000001.json"
    text = json.dumps({"code": "000001", "ratings": ["Overweight", {}], "median": "Hold", "spread": "bad"})
    path.write_text(text)
    record = decisions._load_ensemble(tmp_path)["000001"]
    assert record["degraded"] is True
    assert decisions._apply_ensemble_fold("Overweight", record) == "Overweight"
    assert decisions._ensemble_flag(record)
    assert path.read_text() == text
