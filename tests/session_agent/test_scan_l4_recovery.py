from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from autoresearch.session_agent import (
    artifacts,
    legacy_scan,
    plan as plan_service,
    service,
    store,
)
from autoresearch.session_agent.workflows.scan import (
    build_scan_plan,
    ensemble_record,
    l4_retry_expansion,
)

from .test_scan_prelude import context, request


def _handle(tmp_path):
    staging = tmp_path / "staging/2026-09-13"
    staging.mkdir(parents=True)
    return SimpleNamespace(
        run_id="20260913T010203000000Z",
        analysis_date="2026-09-13",
        staging=staging,
    )


def test_ensemble_same_tier_early_stop_is_not_degraded():
    value = ensemble_record(
        "000001", "Overweight", "Overweight", None, trigger="ow_review", review3_dispatched=False
    )
    assert value == {
        "code": "000001",
        "ratings": ["Overweight", "Overweight"],
        "median": "Overweight",
        "spread": 0,
        "degraded": False,
        "trigger": "ow_review",
        "n_runs": 2,
        "early_stopped": True,
        "role": "ens_review",
        "n_dispatch": 1,
        "review_policy_version": "ow-pinned-sell-v1",
        "review_trigger": "ow_review",
        "review_required": True,
        "review_status": "COMPLETE",
        "review_coverage_reason": "same_tier_median_fixed",
    }


def test_failed_third_review_is_disclosed_as_degraded():
    value = ensemble_record(
        "000001", "Buy", "Hold", None, trigger="ow_review", review3_dispatched=True
    )
    assert value["ratings"] == ["Buy", "Hold"]
    assert value["degraded"] is True
    assert value["n_dispatch"] == 2
    assert value["review_status"] == "INCOMPLETE"


def test_ticket_success_is_only_written_after_prompt_slim_and_card_are_verified(tmp_path):
    handle = _handle(tmp_path)
    (handle.staging / "_l4_prompt_600519.md").write_text("task pack")
    legacy_scan.initialize_tickets(handle, ["600519"])
    legacy_scan.claim_ticket(handle, "600519", 1)
    book = handle.staging / "_l4_tasks.json"
    task = json.loads(book.read_text())["tasks"]["600519"]
    slim = task["artifacts"]["slim"]["path"]
    card = task["artifacts"]["card"]["path"]
    from pathlib import Path

    Path(slim).parent.mkdir(parents=True, exist_ok=True)
    Path(slim).write_text(
        "\n".join(
            [
                "## Verified market snapshot",
                "### Latest verified OHLCV row",
                "| Close | 12.34 |",
                "## Market context",
                "## Fundamentals overview",
                "x" * 5000,
            ]
        )
    )
    Path(card).parent.mkdir(parents=True, exist_ok=True)
    Path(card).write_text("**Rating**: Hold\nFINAL TRANSACTION PROPOSAL: **HOLD**\n")
    completed = legacy_scan.complete_ticket(handle, "600519", 1)
    assert completed["status"] == "SUCCEEDED"
    assert legacy_scan.ticket_states(handle) == {"l4.600519.a1": "SUCCEEDED"}


def test_second_ticket_attempt_gets_a_new_child_subtree(tmp_path):
    plan = build_scan_plan(request(), context(tmp_path))
    expansion = l4_retry_expansion(
        plan,
        "600519",
        2,
        [{"artifact_id": "scan.l4.600519.a1.prompt", "sha256": "a" * 64}],
        intel_enabled=True,
    )

    ids = [task["task_id"] for task in expansion["tasks"]]
    assert ids == [
        "l4.600519.a2",
        "l4.600519.a2.slim",
        "l4.600519.a2.intel",
        "l4.600519.a2.intel_status",
        "l4.600519.a2.card",
    ]
    children = [task for task in expansion["tasks"] if task["parent_task"]]
    assert all(task["parent_task"]["attempt"] == 2 for task in children)
    assert all(".a2." in task["output_artifact_ids"][0] for task in children)


def test_retry_expansion_rejects_an_attempt_beyond_the_taskbook_cap(tmp_path):
    plan = build_scan_plan(request(), context(tmp_path))

    with pytest.raises(ValueError, match="attempt cap"):
        l4_retry_expansion(
            plan,
            "600519",
            3,
            [{"artifact_id": "scan.l4.600519.a1.prompt", "sha256": "a" * 64}],
            intel_enabled=False,
        )


def test_taskbook_projects_only_the_current_attempt_as_authoritative(tmp_path):
    handle = _handle(tmp_path)
    (handle.staging / "_l4_prompt_600519.md").write_text("task pack")
    legacy_scan.initialize_tickets(handle, ["600519"])
    legacy_scan.claim_ticket(handle, "600519", 1)
    legacy_scan.fail_ticket(handle, "600519", 1, "TIMEOUT", "session lost")
    legacy_scan.claim_ticket(handle, "600519", 2)
    owners = [
        {"task_id": "l4.600519.a1", "subject": "600519"},
        {"task_id": "l4.600519.a2", "subject": "600519"},
    ]

    assert legacy_scan.ticket_states(handle, owners) == {
        "l4.600519.a1": "SUPERSEDED",
        "l4.600519.a2": "RUNNING",
    }


@pytest.mark.parametrize("retained_reuse,crash_before_sync", [(False, False), (True, False), (True, True)])
def test_retry_service_freezes_a2_and_defers_the_old_card_dependency(tmp_path, monkeypatch, retained_reuse, crash_before_sync):
    handle = context(tmp_path / "context_codex/scan_runs/20260913T010203000000Z")
    handle.workspace.mkdir(parents=True)
    handle.staging.mkdir(parents=True)
    handle.analysis_date = "2026-09-13"
    handle.capsule = handle.workspace / "capsule"
    handle.capsule.mkdir()
    plan = build_scan_plan(request(), handle)
    session = handle.workspace / "session"
    session.mkdir()
    (session / "request.json").write_text(json.dumps(request()))
    (session / "host_profile.json").write_text(json.dumps(request()["host_profile"]))
    (session / "plan.json").write_text(json.dumps(plan))
    store.initialize(session / "tasks.json", plan)
    (handle.staging / "_l4_prompt_600519.md").write_text("task pack")
    scan = __import__(
        "autoresearch.session_agent.workflows.scan", fromlist=["l4_expansion"]
    )
    gate2 = scan._task(
        "scan.gate2",
        "DETERMINISTIC",
        dependencies=["scan.gate1"],
        inputs=["scan.run_mode"],
        outputs=["scan.finalists"],
        contract="scan.gate2.v1",
        operation="scan.gate2.skip",
    )
    prior = scan._expansion(
        plan,
        "scan.l3.repair",
        [{"artifact_id": "scan.run_mode", "sha256": "e" * 64}],
        [gate2],
    )
    plan_service.persist_expansion(session, plan, prior, existing_tasks=plan["tasks"])
    store.register_tasks(session / "tasks.json", prior["tasks"], plan_hash=plan["plan_hash"])
    expansion = scan.l4_expansion(
        plan,
        {"mode": "FULL"},
        [{"code": "600519"}],
        [{"artifact_id": "scan.finalists", "sha256": "f" * 64}],
        intel_enabled=retained_reuse,
    )
    plan_service.persist_expansion(
        session, plan, expansion, existing_tasks=[*plan["tasks"], gate2]
    )
    store.register_tasks(session / "tasks.json", expansion["tasks"], plan_hash=plan["plan_hash"])
    from autoresearch.session_agent.workflows.scan import register_scan_expansion_artifacts

    register_scan_expansion_artifacts(request(), handle, expansion)
    original_card = handle.staging / "details/600519.md"
    original_card.parent.mkdir(parents=True)
    original_card.write_text("invalid first attempt")
    first_binding = artifacts.bind_artifact_hash(
        handle, "scan.l4.600519.a1.card"
    )
    legacy_scan.initialize_tickets(handle, ["600519"])
    legacy_scan.claim_ticket(handle, "600519", 1)
    legacy_scan.fail_ticket(handle, "600519", 1,
                            "DOMAIN_VALIDATION" if retained_reuse else "TIMEOUT", "lost response")
    if retained_reuse:
        prefix = "scan.l4.600519.a1"
        retained = {"intel_task_id": "l4.600519.a1.intel",
                    "status_task_id": "l4.600519.a1.intel_status",
                    "intel_artifact_id": prefix + ".intel",
                    "status_artifact_id": prefix + ".intel_status", "snapshots": []}
        for artifact_id in (retained["intel_artifact_id"], retained["status_artifact_id"]):
            path = artifacts.declared_path(handle, artifact_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("accepted old evidence")
            frozen = artifacts.bind_artifact_hash(handle, artifact_id)
            retained["snapshots"].append({"artifact_id": artifact_id, "sha256": frozen["sha256"]})
        owner_path = session / "tasks.json"
        owner = json.loads(owner_path.read_text())
        for task_id in (retained["intel_task_id"], retained["status_task_id"]):
            owner["tasks"][task_id]["state"] = "SUCCEEDED"
        owner_path.write_text(json.dumps(owner))
        monkeypatch.setattr("autoresearch.session_agent.intel_reuse.accepted_retry_intel",
                            lambda *args, **kwargs: retained)

    if crash_before_sync:
        original_sync = service._sync_expansion
        def crash(*args):
            raise RuntimeError("interrupted after immutable expansion freeze")
        monkeypatch.setattr(service, "_sync_expansion", crash)
        with pytest.raises(RuntimeError, match="interrupted"):
            service.retry_l4(handle.run_id, "600519", 2, handle_loader=lambda _: handle)
        monkeypatch.setattr(service, "_sync_expansion", original_sync)
        monkeypatch.setattr("autoresearch.session_agent.intel_reuse.accepted_retry_intel",
                            lambda *args, **kwargs: pytest.fail("recomputed frozen admission after crash"))

    result = service.retry_l4(
        handle.run_id,
        "600519",
        2,
        handle_loader=lambda unused: handle,
    )

    assert result["state"] == "READY"
    assert service._task(handle, "l4.600519.a2.card")["parent_task"]["attempt"] == 2
    assert store.read_entry(session / "tasks.json", "l4.600519.a1.card")["state"] == "WAITING_RETRY"
    assert (session / "recoveries").is_dir()
    if retained_reuse:
        frozen_files = {path.name: path.read_bytes() for path in (session / "recoveries").glob("*.json")}
        monkeypatch.setattr("autoresearch.session_agent.intel_reuse.accepted_retry_intel",
                            lambda *args, **kwargs: pytest.fail("recomputed frozen reuse admission"))
        repeated = service.retry_l4(handle.run_id, "600519", 2, handle_loader=lambda _: handle)
        assert repeated["result"]["expansion"] == result["result"]["expansion"]
        assert {path.name: path.read_bytes() for path in (session / "recoveries").glob("*.json")} == frozen_files
        assert store.read_entry(session / "tasks.json", retained["intel_task_id"])["state"] == "SUCCEEDED"

    retry_card = handle.staging / "session_attempts/600519/a2/card.md"
    retry_card.parent.mkdir(parents=True)
    retry_card.write_text("verified retry card")
    second_binding = artifacts.bind_artifact_hash(
        handle, "scan.l4.600519.a2.card"
    )
    service._promote_l4_retry_output(
        handle, service._task(handle, "l4.600519.a2.card")
    )

    assert original_card.read_text() == "verified retry card"
    assert first_binding["sha256"] != second_binding["sha256"]
    assert (
        artifacts.binding_sha256(handle, "scan.l4.600519.a1.card")
        == second_binding["sha256"]
    )
    assert store.read_entry(session / "tasks.json", "l4.600519.a1.card")["state"] == "SUPERSEDED"

    # resume replays promotion for every successful inference. The already accepted
    # alias must be checked without rewriting it or rejecting a completed retry.
    promoted_stat = original_card.stat()
    service._promote_l4_retry_output(
        handle, service._task(handle, "l4.600519.a2.card")
    )
    assert original_card.stat().st_mtime_ns == promoted_stat.st_mtime_ns
    assert artifacts.binding_sha256(handle, "scan.l4.600519.a1.card") == second_binding["sha256"]

    # Idempotency is not a bypass for a projection that changed after acceptance.
    original_card.write_text("changed after acceptance")
    with pytest.raises((RuntimeError, ValueError), match="changed|hash|identity"):
        service._promote_l4_retry_output(
            handle, service._task(handle, "l4.600519.a2.card")
        )
