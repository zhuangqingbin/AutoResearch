from __future__ import annotations

import json
from types import SimpleNamespace

import pandas as pd

from autoresearch.session_agent import domain_ops


def _handle(tmp_path):
    workspace = tmp_path / "run"
    staging = workspace / "staging/2026-09-13"
    session = workspace / "session"
    staging.mkdir(parents=True)
    session.mkdir()
    request = {
        "analysis_date": "2026-09-13",
        "force_full": False,
    }
    (session / "request.json").write_text(json.dumps(request))
    return SimpleNamespace(
        run_id="20260913T010203000000Z",
        engine="codex",
        analysis_date="2026-09-13",
        workspace=workspace,
        staging=staging,
        contract=SimpleNamespace(
            config_hash="c" * 64,
            user_config={
                "l4_intel": {"enabled": True, "max_queries": 12},
                "performance": {"streaming_l4": True},
            },
        ),
    )


def test_l4_prepare_writes_prompts_then_initializes_the_existing_taskbook(tmp_path, monkeypatch):
    handle = _handle(tmp_path)
    pd.DataFrame(
        [
            {
                "code": "600519",
                "ticker": "600519.SS",
                "name": "贵州茅台",
                "sector": "食品饮料",
                "lane": "pinned",
            }
        ]
    ).to_csv(handle.staging / "finalists.csv", index=False)
    monkeypatch.setattr("autoresearch.scan.l4.prompts.write_shared_instructions", lambda root: 1)
    def dispatch(root):
        (root / "_l4_prompt_600519.md").write_text("task pack")
        return {"n_prompts": 1, "tickers": ["600519.SS"], "pinned": ["600519"]}

    monkeypatch.setattr("autoresearch.scan.l4.prompts.write_dispatch_pack", dispatch)
    monkeypatch.setattr("autoresearch.scan.l4.producers.fetch_pledge", lambda root: None)
    monkeypatch.setattr("autoresearch.scan.l4.producers.fetch_seats", lambda root: None)
    monkeypatch.setattr("autoresearch.scan.l4.producers.fetch_consensus", lambda root: None)
    monkeypatch.setattr("autoresearch.scan.l4.producers.fetch_fund_hold", lambda root: None)
    monkeypatch.setattr("autoresearch.scan.calendar.main", lambda argv: 0)
    value = domain_ops.scan_l4_prepare(handle)
    assert value["codes"] == ["600519"]
    taskbook = json.loads((handle.staging / "_l4_tasks.json").read_text())
    assert taskbook["tasks"]["600519"]["pinned"] is True
    assert taskbook["tasks"]["600519"]["ticker"] == "600519.SS"
    assert json.loads((handle.staging / "session_outputs/l4.plan.json").read_text())["intel_enabled"] is True


def test_review_plan_uses_card_rating_and_frozen_pinned_lane(tmp_path):
    handle = _handle(tmp_path)
    (handle.staging / "details").mkdir()
    (handle.staging / "details/000001.md").write_text(
        "# 决策卡 — 000001\n**Rating**: Overweight\nFINAL TRANSACTION PROPOSAL: **BUY**\n"
    )
    (handle.staging / "details/300750.md").write_text(
        "# 决策卡 — 300750\n**Rating**: Sell\nFINAL TRANSACTION PROPOSAL: **SELL**\n"
    )
    pd.DataFrame(
        [
            {"code": "000001", "lane": "selected"},
            {"code": "300750", "lane": "pinned"},
        ]
    ).to_csv(handle.staging / "finalists.csv", index=False)
    value = domain_ops.scan_review_plan(handle)
    assert [(row["code"], row["trigger"]) for row in value["reviews"]] == [
        ("000001", "ow_review"),
        ("300750", "sell_review"),
    ]


def test_review_decision_only_requests_a_third_run_on_tier_disagreement(tmp_path):
    handle = _handle(tmp_path)
    (handle.staging / "ensemble").mkdir()
    plan = {
        "schema_version": 1,
        "reviews": [
            {"code": "000001", "rating": "Buy", "pinned": False, "trigger": "ow_review"},
            {"code": "300750", "rating": "Sell", "pinned": True, "trigger": "sell_review"},
        ],
    }
    out = handle.staging / "session_outputs"
    out.mkdir(exist_ok=True)
    (out / "review.plan.json").write_text(json.dumps(plan))
    (handle.staging / "ensemble/000001.run2.md").write_text("**Rating**: Hold")
    (handle.staging / "ensemble/300750.run2.md").write_text("**Rating**: Sell")
    value = domain_ops.scan_review_decide(handle)
    assert [row["same_tier"] for row in value["decisions"]] == [False, True]
    assert [row["review3_required"] for row in value["decisions"]] == [True, False]
