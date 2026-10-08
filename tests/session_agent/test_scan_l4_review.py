from __future__ import annotations

from autoresearch.session_agent.validation import (
    DomainValidationError,
    validate_registered_contract,
)
from autoresearch.session_agent.workflows.scan import (
    build_scan_plan,
    review3_expansion,
    review_expansion,
)

from .test_scan_prelude import context, request


def _plan(tmp_path):
    return build_scan_plan(request(), context(tmp_path))


def _snapshots():
    return [{"artifact_id": "scan.review.plan", "sha256": "a" * 64}]


def test_review2_only_runs_for_buy_or_pinned_sell_candidates(tmp_path):
    expansion = review_expansion(
        _plan(tmp_path),
        {
            "reviews": [
                {"code": "600519", "rating": "Hold", "pinned": False, "trigger": None},
                {"code": "000001", "rating": "Overweight", "pinned": False, "trigger": "ow_review"},
                {"code": "300750", "rating": "Sell", "pinned": True, "trigger": "sell_review"},
                {"code": "601318", "rating": "Sell", "pinned": False, "trigger": None},
            ]
        },
        _snapshots(),
    )
    reviewers = [task for task in expansion["tasks"] if task["role"] == "scan.l4.review"]
    assert [task["task_id"] for task in reviewers] == [
        "l4.000001.a1.review2",
        "l4.300750.a1.review2",
    ]
    assert all(task["independent_context"] is True for task in reviewers)
    for task in reviewers:
        assert set(task["input_artifact_ids"]) == {
            *(f"scan.l4.{task['subject']}.a1.{kind}" for kind in ("prompt", "slim", "deep", "intel_status")),
            "scan.finalists",
        }
        assert not any(item.endswith((".card", ".review2", ".review3")) for item in task["input_artifact_ids"])
    decide = next(task for task in expansion["tasks"] if task["task_id"] == "scan.review.decide")
    assert set(decide["dependencies"]) == {
        "scan.review.none.600519",
        "l4.000001.a1.review2",
        "l4.300750.a1.review2",
        "scan.review.none.601318",
    }


def test_review3_is_skipped_when_review2_matches_original_tier(tmp_path):
    expansion = review3_expansion(
        _plan(tmp_path),
        {
            "decisions": [
                {
                    "code": "000001",
                    "trigger": "ow_review",
                    "rating": "Overweight",
                    "review2_rating": "Overweight",
                    "same_tier": True,
                }
            ]
        },
        _snapshots(),
    )
    assert not any(task["role"] == "scan.l4.review" for task in expansion["tasks"])
    final = next(task for task in expansion["tasks"] if task["operation"] == "scan.l4.finalize")
    assert final["task_id"] == "l4.000001.a1.finalize"
    assert "scan.l4.000001.a1.review3" not in final["input_artifact_ids"]


def test_review3_runs_after_a_real_disagreement_and_keeps_parent_attempt(tmp_path):
    expansion = review3_expansion(
        _plan(tmp_path),
        {
            "decisions": [
                {
                    "code": "000001",
                    "trigger": "ow_review",
                    "rating": "Buy",
                    "review2_rating": "Hold",
                    "same_tier": False,
                }
            ]
        },
        _snapshots(),
    )
    review3 = next(task for task in expansion["tasks"] if task["role"] == "scan.l4.review")
    assert review3["task_id"] == "l4.000001.a1.review3"
    assert review3["parent_task"] == {
        "owner": "L4_TASKBOOK",
        "subject": "000001",
        "attempt": 1,
    }
    final = next(task for task in expansion["tasks"] if task["operation"] == "scan.l4.finalize")
    assert final["dependencies"] == [review3["task_id"]]
    assert "scan.l4.000001.a1.review3" in final["input_artifact_ids"]


def test_reviews_follow_the_authoritative_second_ticket_attempt(tmp_path):
    expansion = review_expansion(
        _plan(tmp_path),
        {
            "reviews": [
                {
                    "code": "000001",
                    "attempt": 2,
                    "rating": "Overweight",
                    "pinned": False,
                    "trigger": "ow_review",
                }
            ]
        },
        _snapshots(),
    )
    review = next(task for task in expansion["tasks"] if task["kind"] == "INFERENCE")
    assert review["task_id"] == "l4.000001.a2.review2"
    assert review["parent_task"]["attempt"] == 2
    assert set(review["input_artifact_ids"]) == {
        *(f"scan.l4.000001.a2.{kind}" for kind in ("prompt", "slim", "deep", "intel_status")),
        "scan.finalists",
    }
    assert not any(item.endswith((".card", ".review2", ".review3")) for item in review["input_artifact_ids"])


def test_no_review_candidate_still_gets_one_ticket_finalizer(tmp_path):
    expansion = review3_expansion(
        _plan(tmp_path),
        {
            "decisions": [
                {
                    "code": "600519",
                    "trigger": None,
                    "rating": "Hold",
                    "review2_rating": None,
                    "same_tier": None,
                }
            ]
        },
        _snapshots(),
    )
    finals = [task for task in expansion["tasks"] if task["operation"] == "scan.l4.finalize"]
    assert [task["task_id"] for task in finals] == ["l4.600519.a1.finalize"]


def test_scan_card_contract_validates_the_dynamic_output_instead_of_stock_lite_artifact(monkeypatch):
    task = {
        "role": "scan.l4.card",
        "subject": "600519",
        "expected_output_contract": "stock.lite.v1",
        "output_artifact_ids": ["scan.l4.600519.a1.card"],
    }
    monkeypatch.setattr(
        "autoresearch.session_agent.validation._open_outputs",
        lambda handle, submission, spec: {
            "scan.l4.600519.a1.card": (
                "# 决策卡 — 600519\n**Rating**: Hold\n"
                "FINAL TRANSACTION PROPOSAL: **HOLD**\n"
            )
        },
    )
    validate_registered_contract(object(), {}, task)
    monkeypatch.setattr(
        "autoresearch.session_agent.validation._open_outputs",
        lambda handle, submission, spec: {
            "scan.l4.600519.a1.card": (
                "# 决策卡 — 600519\n**Rating**: Hold\n"
                "FINAL TRANSACTION PROPOSAL: **BUY**\n"
            )
        },
    )
    import pytest

    with pytest.raises(DomainValidationError, match="disagree"):
        validate_registered_contract(object(), {}, task)


def test_reviews_read_the_same_intel_doc_as_the_card_when_intel_is_on(tmp_path):
    # 2026-10-03 run 20261003T101330575299Z: the pinned-holding review2 got intel_status but not the
    # intel text, so the independent re-check judged on less evidence than the card it reviews.
    plan = _plan(tmp_path)
    review2 = next(task for task in review_expansion(
        plan, {"reviews": [{"code": "300750", "rating": "Sell", "pinned": True, "trigger": "sell_review"}]},
        _snapshots(), intel_enabled=True)["tasks"] if task["kind"] == "INFERENCE")
    review3 = next(task for task in review3_expansion(
        plan, {"decisions": [{"code": "300750", "trigger": "sell_review", "rating": "Underweight",
                              "review2_rating": "Hold", "same_tier": False}]},
        _snapshots(), intel_enabled=True)["tasks"] if task["kind"] == "INFERENCE")
    for review in (review2, review3):
        assert "scan.l4.300750.a1.intel_doc" in review["input_artifact_ids"]
        assert not any(item.endswith((".card", ".review2", ".review3")) for item in review["input_artifact_ids"])
