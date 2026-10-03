"""C2 local chain failures remain visible while unrelated ready work proceeds."""
from pathlib import Path

import pytest

from autoresearch.session_agent import service, store

from ._runner_support import begin_synthetic_run, inf


def test_local_failure_reports_its_dependents_while_another_stock_is_ready(tmp_path, monkeypatch):
    run = begin_synthetic_run(tmp_path, monkeypatch, [
        inf("a.card", subject="000001"),
        inf("a.review", subject="000001", deps=["a.card"]),
        inf("b.card", subject="000002"),
    ])
    def loader(_):
        return run.handle
    service.claim(run.run_id, "a.card", 1, handle_loader=loader, event_recorder=lambda *a, **k: None)
    service.fail(run.run_id, "a.card", 1, "CONTRACT_ERROR", "invalid structure", handle_loader=loader)
    result = service.next(run.run_id, handle_loader=loader)
    assert result["state"] == "READY"
    assert [task["task_id"] for task in result["tasks"]] == ["b.card"]
    blocked = {row["task_id"]: row for row in result["errors"]}
    assert blocked["a.card"]["reason"] == "CONTRACT_ERROR"
    assert blocked["a.review"]["blocked_by"] == ["a.card"]
    assert blocked["a.review"]["scope"] == "STOCK"
    assert store.read_entry(service._store_path(run.handle), "a.review")["state"] == "PENDING"
    with pytest.raises(RuntimeError, match="incomplete"):
        service.finish(run.run_id, handle_loader=loader)


@pytest.mark.parametrize("damage", ["missing", "changed"])
def test_unavailable_global_input_blocks_dependents_with_reason(tmp_path, monkeypatch, damage):
    run = begin_synthetic_run(tmp_path, monkeypatch, [
        inf("global.pack", outputs=["global.pack.out"]),
        inf("a.card", deps=["global.pack"], inputs=["global.pack.out"], subject="000001"),
        inf("b.card", deps=["global.pack"], inputs=["global.pack.out"], subject="000002"),
    ])
    source = Path(run.handle.staging) / "in/synthetic.brief.md"
    source.unlink() if damage == "missing" else source.write_text("corrupted global source")
    result = service.next(run.run_id, handle_loader=lambda _: run.handle)
    assert result["state"] == "BLOCKED"
    blocked = {row["task_id"]: row for row in result["errors"]}
    assert blocked["global.pack"]["code"] == "INPUT_UNAVAILABLE"
    assert blocked["global.pack"]["scope"] == "GLOBAL"
    assert blocked["a.card"]["blocked_by"] == ["global.pack"]
    assert blocked["b.card"]["scope"] == "GLOBAL"


def test_dependency_diagnostics_merge_all_roots_in_non_topological_order():
    tasks = [inf("final", deps=["stock", "indirect"]),
             inf("indirect", deps=["global"]), inf("global"),
             inf("stock", subject="000001")]
    errors = service._dependency_errors(tasks, {"stock": "FAILED", "global": "BLOCKED"}, {}, {})
    final = next(row for row in errors if row["task_id"] == "final")
    assert final["blocked_by"] == ["global", "stock"]
    assert final["scope"] == "GLOBAL"


def test_sector_subject_is_not_classified_as_a_stock_failure():
    task = inf("sector", role="sector.brief", subject="食品饮料")
    errors = service._dependency_errors([task], {"sector": "BLOCKED"}, {}, {})
    assert errors[0]["scope"] == "GLOBAL"


def test_corrupted_accepted_shared_pack_is_a_global_source_failure(tmp_path, monkeypatch):
    from autoresearch.session_agent import artifacts

    from ._runner_support import det
    run = begin_synthetic_run(tmp_path, monkeypatch, [
        det("global.pack", outputs=["global.pack.out"]),
        inf("a.card", deps=["global.pack"], inputs=["global.pack.out"], subject="000001"),
        inf("b.card", deps=["global.pack"], inputs=["global.pack.out"], subject="000002"),
    ])
    def loader(_):
        return run.handle
    service.claim(run.run_id, "global.pack", 1, handle_loader=loader)
    service.execute(run.run_id, "global.pack", 1, {"message": "pack"}, handle_loader=loader,
                    runner=run.operation_runner)
    source = artifacts.artifact_path(run.handle, "global.pack.out")
    source.chmod(0o600)
    source.write_text("corrupted accepted shared pack")
    result = service.next(run.run_id, handle_loader=loader)
    assert result["state"] == "BLOCKED"
    assert all(row["scope"] == "GLOBAL" for row in result["errors"])
    assert all(row["artifact_id"] == "global.pack.out" for row in result["errors"])
