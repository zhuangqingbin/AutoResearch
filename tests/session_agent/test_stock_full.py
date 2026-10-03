from __future__ import annotations

from types import SimpleNamespace

from autoresearch.session_agent.workflows.stock import build_stock_plan

from .test_service import _request


def _context(tmp_path):
    return SimpleNamespace(
        run_id="20260913T010203000000Z",
        engine="codex",
        analysis_date="2026-09-13",
        workspace=tmp_path,
        staging=tmp_path / "staging/2026-09-13",
        contract=SimpleNamespace(contract_hash="a" * 64, config_hash="b" * 64),
    )


def _full_request(*, subject="600519.SS", peers=None):
    return {
        **_request(),
        "requested_mode": "FULL",
        "subject": subject,
        "peers": peers or [],
    }

def test_full_plan_preserves_research_dependency_order(tmp_path):
    plan = build_stock_plan(_full_request(), _context(tmp_path))
    tasks = {task["task_id"]: task for task in plan["tasks"]}
    assert tasks["stock.intel"]["role"] == "company.intel"
    assert tasks["stock.bear"]["dependencies"] == ["stock.bull"]
    assert tasks["stock.manager"]["dependencies"] == ["stock.bear"]
    assert "stock.manager" in tasks["stock.premortem"]["dependencies"]
    assert tasks["stock.pm"]["dependencies"] == ["stock.premortem"]
    assert tasks["stock.assemble"]["dependencies"] == ["stock.full.validate"]


def test_full_plan_selects_market_intel_and_declared_peer_lens(tmp_path):
    ashare = build_stock_plan(_full_request(peers=["000858.SZ"]), _context(tmp_path))
    assert any(task["task_id"] == "stock.peer" for task in ashare["tasks"])
    us = build_stock_plan(_full_request(subject="NVDA"), _context(tmp_path))
    tasks = {task["task_id"]: task for task in us["tasks"]}
    assert tasks["stock.intel"]["role"] == "us.intel"
    assert "stock.peer" not in tasks


def test_full_pm_atomically_owns_four_required_products(tmp_path):
    plan = build_stock_plan(_full_request(), _context(tmp_path))
    pm = next(task for task in plan["tasks"] if task["task_id"] == "stock.pm")
    assert set(pm["output_artifact_ids"]) == {
        "stock.full.4_portfolio.decision",
        "stock.full.4_portfolio.calendar",
        "stock.full.2_research.variant",
        "stock.full.2_research.faceoff",
    }


def test_full_assemble_writes_under_the_operation_its_plan_registers(tmp_path):
    """domain_ops 传给装配器的写身份,必须就是计划里 `stock.assemble` 任务登记的那个操作。"""
    import inspect

    from autoresearch.session_agent import domain_ops

    plan = build_stock_plan(_full_request(), _context(tmp_path))
    operation = next(task["operation"] for task in plan["tasks"] if task["task_id"] == "stock.assemble")
    assert operation == domain_ops.FULL_ASSEMBLE_OPERATION
    assert "write_operation=FULL_ASSEMBLE_OPERATION" in inspect.getsource(domain_ops.stock_full_assemble)
