"""C5 stock-local review dependencies with the original full-report join."""
from copy import deepcopy

import pytest

from autoresearch.contracts.session_plan import plan_hash
from autoresearch.session_agent import plan as plans
from autoresearch.session_agent.workflows import scan

from .test_scan_prelude import context, request


def _l4(tmp_path):
    plan = scan.build_scan_plan(request(), context(tmp_path))
    expansion = scan.l4_expansion(plan, {"mode": "FULL"},
        [{"code": code, "lane": "pinned" if code == "000001" else ""}
         for code in ("000001", "000002", "000003")],
        [{"artifact_id": "scan.finalists", "sha256": "a" * 64}], intel_enabled=False)
    return plan, expansion


def test_each_review_plan_waits_only_for_its_own_card(tmp_path):
    plan, expansion = _l4(tmp_path)
    tasks = {task["task_id"]: task for task in expansion["tasks"]}
    assert "scan.review.plan" not in tasks
    for code in ("000001", "000002", "000003"):
        task = tasks[f"scan.review.plan.{code}"]
        assert task["dependencies"] == [f"l4.{code}.a1.card"]
        assert [item for item in task["input_artifact_ids"] if item.endswith(".card")] == [f"scan.l4.{code}.a1.card"]
    assert scan.per_stock_reviews(plan)


def test_stock_third_review_can_start_without_other_stock_results(tmp_path):
    plan, _ = _l4(tmp_path)
    expansion = scan.review3_expansion(plan, {"decisions": [{
        "code": "000001", "rating": "Sell", "trigger": "sell_review", "same_tier": False,
    }]}, [{"artifact_id": "scan.review.decision.000001", "sha256": "b" * 64}], scope="000001")
    task = next(item for item in expansion["tasks"] if item["role"] == "scan.l4.review")
    assert task["dependencies"] == ["scan.review.decide.000001"]
    assert all(item.get("subject") == "000001" for item in expansion["tasks"])
    assert not any(item["operation"] == "scan.assemble" for item in expansion["tasks"])


def test_old_frozen_plan_retains_global_review_barrier(tmp_path):
    plan, _ = _l4(tmp_path)
    plan = deepcopy(plan)
    plan["task_templates"] = [item for item in plan["task_templates"] if item["template_id"] != "scan.review.join"]
    for item in plan["task_templates"]:
        if item["template_id"] in {"scan.reviews", "scan.review3"}:
            item["expander"] = item["template_id"]
    plan["plan_hash"] = plan_hash(plan)
    expansion = scan.l4_expansion(plan, {"mode": "FULL"}, [{"code": "000001"}, {"code": "000002"}],
                                 [{"artifact_id": "scan.finalists", "sha256": "a" * 64}], intel_enabled=False)
    barrier = next(item for item in expansion["tasks"] if item["task_id"] == "scan.review.plan")
    assert barrier["dependencies"] == ["l4.000001.a1.card", "l4.000002.a1.card"]
    assert not scan.per_stock_reviews(plan)


def test_scoped_expansions_are_independent_but_same_stock_inputs_remain_frozen(tmp_path):
    plan, _ = _l4(tmp_path)
    for code in ("000001", "000002"):
        plan["tasks"].append(scan._task(f"scan.review.plan.{code}", "DETERMINISTIC", dependencies=[],
            inputs=[], outputs=[f"scan.review.plan.{code}"], contract="scan.review.plan.v1",
            operation="scan.review.plan", subject=code))
    plan["plan_hash"] = plan_hash(plan)
    existing = list(plan["tasks"])
    for code in ("000001", "000002"):
        expanded = scan.review_expansion(plan, {"reviews": [{"code": code, "trigger": None}]},
            [{"artifact_id": f"scan.review.plan.{code}", "sha256": "a" * 64}], scope=code)
        plans.persist_expansion(tmp_path, plan, expanded, existing_tasks=existing)
        existing += expanded["tasks"]
    changed = scan.review_expansion(plan, {"reviews": [{"code": "000001", "trigger": None}]},
        [{"artifact_id": "scan.review.plan.000001", "sha256": "b" * 64}], scope="000001")
    with pytest.raises(RuntimeError, match="input conflict"):
        plans.persist_expansion(tmp_path, plan, changed, existing_tasks=existing)


def test_final_join_keeps_every_stock_and_all_publication_gates(tmp_path):
    plan, _ = _l4(tmp_path)
    expanded = scan.review_join_expansion(plan, [{"code": code, "attempt": attempt}
        for code, attempt in (("000001", 1), ("000002", 1), ("000003", 2))], [])
    tasks = {task["task_id"]: task for task in expanded["tasks"]}
    assert tasks["scan.l4.complete"]["dependencies"] == [
        "l4.000001.a1.finalize", "l4.000002.a1.finalize", "l4.000003.a2.finalize"]
    assert tasks["scan.assemble"]["dependencies"] == ["scan.l4.complete"]
    assert tasks["scan.gate4"]["dependencies"] == ["scan.assemble"]
