from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from autoresearch.session_agent import service
from autoresearch.session_agent.progress import scan_progress


def test_scan_progress_reads_taskbook_terminals_and_does_not_treat_a_card_as_success(tmp_path):
    staging = tmp_path / "staging/2026-09-13"
    (staging / "details").mkdir(parents=True)
    (staging / "details/600519.md").write_text("**Rating**: Buy")
    (staging / "_l4_tasks.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "tasks": {
                    "600519": {"status": "RUNNING", "attempt": 1},
                    "000001": {
                        "status": "BLOCKED",
                        "attempt": 1,
                        "last_error_class": "DATA_INTEGRITY",
                    },
                },
            }
        )
    )
    handle = SimpleNamespace(staging=staging, analysis_date="2026-09-13")
    value = scan_progress(handle)
    assert value["checkpoint"] == "CP5"
    assert value["l4"] == {"total": 2, "succeeded": 0, "blocked": 1, "running": 1}
    assert value["events"] == [
        {"code": "000001", "status": "BLOCKED", "reason": "DATA_INTEGRITY"}
    ]


def test_status_exposes_scan_progress_without_rewriting_a_research_summary(tmp_path, monkeypatch):
    handle = _scan_handle(tmp_path)
    monkeypatch.setattr(service, "_state", lambda unused: ("WAITING", [], []))

    value = service.status(handle.run_id, handle_loader=lambda unused: handle)

    assert value["result"]["checkpoint"] == "CP5"
    assert value["result"]["l4"]["succeeded"] == 1


def test_scan_progress_respects_the_original_watcher_cursor(tmp_path):
    from autoresearch.scan.l4_watch import ack_events

    handle = _scan_handle(tmp_path)
    payload = json.loads((handle.staging / "_l4_tasks.json").read_text())
    payload["tasks"] = {
        "600519": {
            "status": "BLOCKED",
            "attempt": 1,
            "last_error_class": "DATA_INTEGRITY",
        }
    }
    (handle.staging / "_l4_tasks.json").write_text(json.dumps(payload))
    ack_events(handle.staging, ["600519:BLOCKED"])

    assert scan_progress(handle)["events"] == []


def _scan_handle(tmp_path):
    staging = tmp_path / "staging/2026-09-13"
    staging.mkdir(parents=True)
    (staging / "_l4_tasks.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "tasks": {"600519": {"status": "SUCCEEDED", "attempt": 1}},
            }
        )
    )
    return SimpleNamespace(
        run_id="20260913T010203000000Z",
        staging=staging,
        contract=SimpleNamespace(run_kind="scan-market"),
    )


def test_legacy_taskbook_terminals_cannot_prove_research_coverage(tmp_path):
    handle = _scan_handle(tmp_path)
    result = scan_progress(handle)["coverage"]
    assert result["terminal_tasks"]["terminal"] == 1
    assert result["successful_cards"]["completed"] == 0
    assert result["deep_research"]["unresolved_conditions"] == ["600519"]
    assert result["report_completeness"]["complete"] is False


def test_scan_without_frozen_population_cannot_claim_research_complete(tmp_path, monkeypatch):
    from .test_scan_runner_full import _scan_run
    handle = _scan_run(tmp_path, monkeypatch)
    coverage = service.status(handle.run_id, handle_loader=lambda _: handle)["result"]["coverage"]
    assert coverage["population"]["frozen"] is False
    assert coverage["report_completeness"]["research_complete"] is False


@pytest.mark.parametrize("missing", ["taskbook", "empty_taskbook", "session_entry"])
def test_terminal_coverage_keeps_frozen_denominator_when_owner_records_are_missing(tmp_path, monkeypatch, missing):
    from ._runner_support import begin_synthetic_run, det, inf

    run = begin_synthetic_run(tmp_path, monkeypatch, [
        det("l4.600519.a1", owner="L4_TASKBOOK", subject="600519"),
        inf("l4.600519.a1.card", subject="600519"),
    ], run_kind="scan-market")
    book = run.handle.staging / "_l4_tasks.json"
    book.write_text(json.dumps({"schema_version": 1, "tasks": {
        "600519": {"status": "SUCCEEDED", "attempt": 1},
    }}))
    task_store = service._store_path(run.handle)
    payload = json.loads(task_store.read_text())
    payload["tasks"]["l4.600519.a1.card"]["state"] = "SUCCEEDED"
    task_store.write_text(json.dumps(payload))
    assert scan_progress(run.handle)["coverage"]["terminal_tasks"] == {
        "total": 2, "terminal": 2, "succeeded": 2,
    }

    if missing == "taskbook":
        book.unlink()
    elif missing == "empty_taskbook":
        book.write_text(json.dumps({"schema_version": 1, "tasks": {}}))
    else:
        del payload["tasks"]["l4.600519.a1.card"]
        task_store.write_text(json.dumps(payload))

    coverage = scan_progress(run.handle)["coverage"]
    assert coverage["terminal_tasks"] == {"total": 2, "terminal": 1, "succeeded": 1}
    assert coverage["report_completeness"]["complete"] is False
