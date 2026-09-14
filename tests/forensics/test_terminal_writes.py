from __future__ import annotations

import json
import sys
import threading

import pytest

from autoresearch.analyze import assemble, runctl as analyze_runctl
from autoresearch.common import workspace as ws
from autoresearch.dossier import builder as dossier_builder, pool as dossier_pool
from autoresearch.macro import state as macro_state
from autoresearch.scan.post_run import publish_run_observation
from autoresearch.trace.write_guard import run_write_lock

from .support import KINDS


@pytest.mark.parametrize("forensic_case", [(kind, "business") for kind in KINDS], indirect=True)
def test_terminal_run_cannot_create_another_report(forensic_case):
    forensic_case.finish()
    before = forensic_case.snapshot_persistent_tree()

    with pytest.raises(RuntimeError, match="RUN_NOT_ACTIVE"):
        forensic_case.produce_changed_report()

    assert forensic_case.snapshot_persistent_tree() == before


def test_legacy_stock_assembler_checks_terminal_state_before_creating_report(
    forensic_case,
    monkeypatch,
):
    root = forensic_case.handle.staging / "analyze/600519.SS_20260914"
    required = [assemble.DECISION_REL] + [
        relative
        for _, items in assemble.SPINE + assemble.APPENDIX
        for _, relative, optional in items
        if not optional
    ]
    for relative in required:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "**Rating**: Hold\nFINAL TRANSACTION PROPOSAL: **HOLD**\n"
            if relative == assemble.DECISION_REL
            else "synthetic section\n",
            encoding="utf-8",
        )
    forensic_case.finish()
    before = forensic_case.snapshot_persistent_tree()
    monkeypatch.setenv("AUTORESEARCH_RUN_ID", forensic_case.handle.run_id)
    monkeypatch.setattr(sys, "argv", ["assemble", str(root)])

    with pytest.raises(RuntimeError, match="RUN_NOT_ACTIVE"):
        assemble.main()

    assert forensic_case.snapshot_persistent_tree() == before


def test_terminal_checkpoint_cannot_copy_another_business_artifact(
    forensic_case,
    monkeypatch,
):
    source = forensic_case.root / "scratch/new-report.md"
    source.parent.mkdir(parents=True)
    source.write_text("new business result", encoding="utf-8")
    forensic_case.finish()
    before = forensic_case.snapshot_persistent_tree()
    monkeypatch.setenv("AUTORESEARCH_RUN_ID", forensic_case.handle.run_id)

    with pytest.raises(RuntimeError, match="RUN_NOT_ACTIVE"):
        analyze_runctl.record_stage("assemble", outputs=[source])

    assert forensic_case.snapshot_persistent_tree() == before


def test_terminal_macro_state_writer_fails_before_creating_state(
    forensic_case_factory,
    monkeypatch,
):
    case = forensic_case_factory("macro-research")
    source = case.handle.staging / "macro/2026-09-14/1_spine/decision.md"
    source.parent.mkdir(parents=True)
    source.write_text("- OVERALL 风险档: **Rating**: Hold\n", encoding="utf-8")
    target = case.handle.staging / "late-state"
    case.finish()
    before = case.snapshot_persistent_tree()
    monkeypatch.setenv("AUTORESEARCH_RUN_ID", case.handle.run_id)

    with pytest.raises(RuntimeError, match="RUN_NOT_ACTIVE"):
        macro_state.write_macro_state(source.parents[1], out_dir=target)

    assert not target.exists()
    assert case.snapshot_persistent_tree() == before


def test_terminal_dossier_builder_fails_before_creating_candidate(
    forensic_case_factory,
    monkeypatch,
):
    case = forensic_case_factory("dossier-init")
    prefetch = case.handle.staging / "prefetch.json"
    prefetch.write_text(json.dumps({"mainbz": [], "fwd_eps": {}, "val_band": None}))
    target = case.handle.staging / "late-dossier.md"
    case.finish()
    before = case.snapshot_persistent_tree()
    monkeypatch.setenv("AUTORESEARCH_RUN_ID", case.handle.run_id)

    with pytest.raises(RuntimeError, match="RUN_NOT_ACTIVE"):
        dossier_builder.build_skeleton(
            "600519",
            "2026-09-14",
            output_path=target,
            prefetch_path=prefetch,
        )

    assert not target.exists()
    assert case.snapshot_persistent_tree() == before


def test_terminal_scan_observer_fails_before_mutating_staging(
    forensic_case_factory,
    monkeypatch,
):
    case = forensic_case_factory("scan-market")
    case.finish()
    before = case.snapshot_persistent_tree()
    monkeypatch.setenv("AUTORESEARCH_RUN_ID", case.handle.run_id)

    with pytest.raises(RuntimeError, match="RUN_NOT_ACTIVE"):
        publish_run_observation(case.handle.staging, real_scan=False)

    assert case.snapshot_persistent_tree() == before


def test_terminal_scan_prelude_cannot_refresh_dossier_pool(
    forensic_case_factory,
    monkeypatch,
):
    case = forensic_case_factory("scan-market")
    target = ws.knowledge_root() / "coverage_pool.json"
    case.finish()
    before = case.snapshot_persistent_tree()
    monkeypatch.setenv("AUTORESEARCH_RUN_ID", case.handle.run_id)

    with pytest.raises(RuntimeError, match="RUN_NOT_ACTIVE"):
        dossier_pool.refresh(
            "2026-09-14",
            scan_root=case.handle.staging,
            pool_path=target,
        )

    assert not target.exists()
    assert case.snapshot_persistent_tree() == before


def test_finalize_uses_the_same_run_lock_as_business_writes(forensic_case):
    started = threading.Event()
    completed = threading.Event()
    errors = []

    def seal():
        started.set()
        try:
            forensic_case.finish()
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)
        finally:
            completed.set()

    with run_write_lock(forensic_case.handle.run_id):
        thread = threading.Thread(target=seal, daemon=True)
        thread.start()
        assert started.wait(timeout=1)
        assert not completed.wait(timeout=0.2), "finalize bypassed the run write lock"

    assert completed.wait(timeout=5)
    thread.join(timeout=1)
    assert errors == []

    with pytest.raises(RuntimeError, match="RUN_NOT_ACTIVE"):
        forensic_case.produce_changed_report()
