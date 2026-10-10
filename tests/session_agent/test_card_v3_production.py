import json

import pytest

from autoresearch.common.atomic import atomic_write_json
from autoresearch.contracts.agent_output import OW_GATES
from autoresearch.session_agent import artifacts, validation
from tests.common.test_card_decision_v3 import decision_text

from .test_service import _handle, _request


def setup_card(tmp_path, subject, venue, text):
    handle = _handle(tmp_path)
    atomic_write_json(
        handle.capsule / "verification/profile.json", {"card_rules_version": "skills-gap-v3"}
    )
    atomic_write_json(handle.workspace / "session/request.json", dict(_request(), subject=subject))
    path = handle.staging / "decision.md"
    path.write_text(text)
    artifacts.register_artifact(handle, "stock.full.4_portfolio.decision", path, "WRITE")
    artifacts.register_artifact(handle, "stock.card.output", path, "WRITE")
    from autoresearch.common.execution_math import build_decision_frame

    frame = handle.staging / "frame.json"
    atomic_write_json(
        frame,
        build_decision_frame(
            analysis_session=handle.analysis_date,
            knowledge_cutoff="2026-09-13T12:00:00Z",
            venue=venue,
            research_depth="FULL",
            usage="standalone",
            sessions=[],
            calendar_quality="UNKNOWN",
        ),
    )
    artifacts.register_artifact(handle, "research.frame", frame, "READ")
    task = {
        "task_id": "stock.pm",
        "subject": subject,
        "output_artifact_ids": ["stock.full.4_portfolio.decision"],
    }
    submission = {"outputs": [{"artifact_id": "stock.full.4_portfolio.decision"}]}
    return handle, task, submission


@pytest.mark.parametrize("subject,venue", [("600519.SS", "XSHG"), ("NVDA", "XNAS")])
@pytest.mark.parametrize("mode", ["FULL", "LITE"])
def test_production_stock_validators_reject_failed_gate(tmp_path, subject, venue, mode):
    text = decision_text(subject=subject, venue=venue, gates=dict.fromkeys(OW_GATES, "FAIL"))
    handle, task, submission = setup_card(tmp_path, subject, venue, text)
    with pytest.raises(validation.DomainValidationError, match="three"):
        (validation._stock_pm if mode == "FULL" else validation._stock_lite)(
            handle, submission, task
        )


def test_new_profile_does_not_upgrade_frozen_v2(tmp_path):
    from autoresearch.trace.completeness import freeze_card_rules

    root = tmp_path / "capsule"
    assert freeze_card_rules(root, kind="stock-research") == "skills-gap-v3"
    atomic_write_json(root / "verification/profile.json", {"card_rules_version": "skills-gap-v2"})
    assert freeze_card_rules(root, kind="stock-research") == "skills-gap-v2"


@pytest.mark.parametrize("subject,venue", [("600519.SS", "XSHG"), ("NVDA", "XNAS")])
def test_full_publisher_rechecks_actual_card(tmp_path, monkeypatch, subject, venue):
    import sys

    from autoresearch.analyze import assemble
    from tests.analyze.test_assemble import _populate_required_files

    text = decision_text(subject=subject, venue=venue, gates=dict.fromkeys(OW_GATES, "UNKNOWN"))
    handle, _, _ = setup_card(tmp_path, subject, venue, text)
    root = tmp_path / f"{subject}_20260913"
    _populate_required_files(root, text)
    with artifacts.open_artifact(handle, "research.frame") as stream:
        frame = json.loads(stream.read())
    monkeypatch.setattr(sys, "argv", ["assemble.py", str(root)])
    reports = tmp_path / "reports_codex"
    assert (
        assemble.main(
            reports_root=reports,
            context_root=tmp_path / "context_codex",
            decision_context={"rules_version": "skills-gap-v3", "subject": subject, "frame": frame},
        )
        == 1
    )
    assert not reports.exists()


def test_full_publish_retains_model_and_machine_rating_with_unknown_execution(
    tmp_path, monkeypatch
):
    import sys

    from autoresearch.analyze import assemble
    from autoresearch.contracts.agent_output import RUBRIC_DIMENSIONS
    from tests.analyze.test_assemble import _populate_required_files

    text = decision_text(
        rating="Overweight",
        dimensions=dict.fromkeys(RUBRIC_DIMENSIONS, "中"),
        deviation="已核订单改善",
    )
    handle, _, _ = setup_card(tmp_path, "NVDA", "XNAS", text)
    root = tmp_path / "NVDA_20260913"
    _populate_required_files(root, text)
    with artifacts.open_artifact(handle, "research.frame") as stream:
        frame = json.loads(stream.read())
    monkeypatch.setattr(sys, "argv", ["assemble.py", str(root)])
    reports = tmp_path / "reports_codex"
    assert (
        assemble.main(
            reports_root=reports,
            context_root=tmp_path / "context_codex",
            decision_context={"rules_version": "skills-gap-v3", "subject": "NVDA", "frame": frame},
        )
        == 0
    )
    manifest = json.loads(next(reports.rglob("manifest.json")).read_text())
    assert manifest["rating"] == "Overweight"
    assert manifest["machine_suggestion"] == "Hold"
    assert manifest["execution"]["actionability_status"] == "UNKNOWN"
    assert text in next(reports.rglob("NVDA.md")).read_text()


def test_two_stage_delta_uses_v3_machine_dimensions(tmp_path):
    from autoresearch.session_agent.card_facts import validate_changes_output

    from .test_two_stage_cards import _assessment, _registered_stock, _write

    handle, tasks = _registered_stock(tmp_path)
    atomic_write_json(
        handle.capsule / "verification/profile.json", {"card_rules_version": "skills-gap-v3"}
    )
    initial = _write(handle, "stock.card.initial", _assessment(handle))
    _write(
        handle,
        "stock.card.changes",
        {
            "schema_version": 1,
            "subject": "600519.SS",
            "initial_hash": initial["sha256"],
            "changed_fields": [],
            "change_reason": "",
            "new_evidence_refs": [],
        },
    )
    text = decision_text(
        subject="600519.SS", venue="XSHG", rating="Hold", deviation="风险管理覆盖研究判断"
    )
    with pytest.raises(ValueError, match="actual initial/final delta"):
        validate_changes_output(handle, tasks["stock.card"], text)


def test_real_submit_rejects_failed_v3_gate_and_keeps_attempt_bytes(tmp_path, monkeypatch):
    from pathlib import Path
    from types import SimpleNamespace

    from autoresearch.common.atomic import canonical_json, sha256_bytes
    from autoresearch.session_agent import host_evidence, service, store

    from .test_two_stage_cards import _write

    handle = _handle(tmp_path)
    handle.contract.created_at = "2026-09-13T12:00:00Z"
    req = dict(
        _request(),
        schema_version=3,
        subject="NVDA",
        card_research_profile="single-stage-v1",
        research_context={"venue": "XNAS", "usage": "standalone", "calendar_source_path": None},
    )
    service.begin(req, begin_capsule=lambda _: handle)

    def loader(_):
        return handle

    monkeypatch.setattr(host_evidence, "resolve_receipt_evidence", lambda *args: [])
    service.claim(handle.run_id, "stock.harvest", 1, handle_loader=loader)

    def harvest(*args, **kwargs):
        for key in ("stock.slim", "stock.deep"):
            path = artifacts.declared_path(handle, key)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("raw facts")
        return SimpleNamespace(exit_code=0, invocation={"status": "COMPLETED"})

    service.execute(
        handle.run_id,
        "stock.harvest",
        1,
        {
            "ticker": "NVDA",
            "analysis_date": req["analysis_date"],
            "asset_type": "stock",
            "peers": [],
            "slim": True,
        },
        handle_loader=loader,
        runner=harvest,
    )
    claimed = service.claim(
        handle.run_id,
        "stock.card",
        1,
        handle_loader=loader,
        event_recorder=lambda *args, **kwargs: None,
    )
    text = decision_text(gates=dict.fromkeys(OW_GATES, "FAIL"))
    _write(handle, "stock.card.output", text)
    task = service._task(handle, "stock.card")
    output = Path(artifacts.output_paths(handle, task, 1)["stock.card.output"])
    receipt = {
        "schema_version": 1,
        "engine": "codex",
        "session_ref": "session-main",
        "context_ref": "decision-context",
        "parent_context_ref": "session-main",
        "task_id": "stock.card",
        "attempt": 1,
        "completed": True,
        "evidence_refs": ["host-binding:" + "1" * 64],
    }
    submission = {
        "schema_version": 1,
        "envelope": claimed["result"]["envelope"],
        "plan_hash": claimed["result"]["plan_hash"],
        "outputs": [
            {"artifact_id": "stock.card.output", "sha256": sha256_bytes(output.read_bytes())}
        ],
        "host_receipt_id": sha256_bytes(canonical_json(receipt).encode()),
    }
    with pytest.raises(validation.DomainValidationError, match="three"):
        service.submit(
            handle.run_id,
            submission,
            handle_loader=loader,
            host_receipt=receipt,
            event_recorder=lambda *args, **kwargs: None,
        )
    assert output.read_text() == text
    assert store.read_states(handle.workspace / "session/tasks.json")["stock.card"] != "SUCCEEDED"
    assert store.read_states(handle.workspace / "session/tasks.json")["stock.publish"] == "PENDING"


def test_lite_publish_preserves_research_and_unknown_execution(tmp_path):
    from autoresearch.contracts.agent_output import RUBRIC_DIMENSIONS
    from autoresearch.session_agent.domain_ops import stock_prepare_publication, stock_validate
    from autoresearch.session_agent.workflows.stock import publish_stock

    text = decision_text(
        rating="Hold",
        dimensions=dict.fromkeys(RUBRIC_DIMENSIONS, "中"),
        gates=dict.fromkeys(OW_GATES, "UNKNOWN"),
    )
    handle, _, _ = setup_card(tmp_path, "NVDA", "XNAS", text)
    stock_validate(handle)
    artifacts.register_artifact(
        handle,
        "stock.card.validation",
        handle.staging / "session_outputs/card.validation.json",
        "READ",
    )
    stock_prepare_publication(handle)
    artifacts.register_artifact(
        handle,
        "stock.publication.bundle",
        handle.staging / "session_outputs/publication.json",
        "READ",
    )
    report = publish_stock(handle, reports_root=tmp_path / "reports_codex")
    manifest = json.loads((report / "manifest.json").read_text())
    assert manifest["rating"] == "Hold"
    assert manifest["machine_suggestion"] == "Hold"
    assert manifest["execution"]["actionability_status"] == "UNKNOWN"
    assert (report / "NVDA_lite.md").read_text() == text


def test_tracked_full_assembler_cannot_omit_frozen_decision_context(tmp_path, monkeypatch):
    import contextlib
    import sys

    from autoresearch.analyze import assemble
    from autoresearch.trace import write_guard
    from tests.analyze.test_assemble import _populate_required_files

    text = decision_text(gates=dict.fromkeys(OW_GATES, "FAIL"))
    handle, _, _ = setup_card(tmp_path, "NVDA", "XNAS", text)
    root = tmp_path / "NVDA_20260913"
    _populate_required_files(root, text)
    monkeypatch.setattr(sys, "argv", ["assemble.py", str(root)])

    @contextlib.contextmanager
    def guarded(_):
        yield handle

    monkeypatch.setattr(write_guard, "guarded_ambient_write", guarded)
    reports = tmp_path / "reports_codex"
    assert assemble.main(reports_root=reports) == 1
    assert not reports.exists()


@pytest.mark.parametrize("mode", ["FULL", "LITE", "SCAN"])
def test_frozen_rating_policy_is_shared_by_production_validators(tmp_path, mode):
    from autoresearch.contracts.agent_output import RUBRIC_DIMENSIONS

    dims = dict.fromkeys(RUBRIC_DIMENSIONS, "中")
    dims.update(基本面="强", 估值="强")
    subject = "600519" if mode == "SCAN" else "600519.SS"
    text = decision_text(subject=subject, venue="XSHG", rating="Overweight", dimensions=dims)
    handle, task, submission = setup_card(tmp_path, subject, "XSHG", text)
    atomic_write_json(
        handle.capsule / "verification/profile.json",
        {
            "card_rules_version": "skills-gap-v3",
            "card_rating_bands": {"Buy": 4, "Overweight": 3, "Hold": -1, "Underweight": -3},
        },
    )
    if mode == "SCAN":
        path = handle.staging / "finalists.csv"
        path.write_text("code,lane\n600519,ordinary\n")
        artifacts.register_artifact(handle, "scan.finalists", path, "READ")
    with pytest.raises(validation.DomainValidationError, match="deviation"):
        if mode == "SCAN":
            validation._research_card_semantics(handle, text, task, scan=True)
        else:
            (validation._stock_pm if mode == "FULL" else validation._stock_lite)(
                handle, submission, task
            )


def test_new_policy_is_frozen_once_from_declared_config(tmp_path):
    from autoresearch.trace.completeness import freeze_card_rules

    root = tmp_path / "capsule"
    cfg = {"l4": {"rubric": {"rating_bands": {"Overweight": 3}}}}
    freeze_card_rules(root, kind="stock-research", config=cfg)
    path = root / "verification/profile.json"
    original = path.read_bytes()
    assert json.loads(original)["card_rating_bands"]["Overweight"] == 3
    cfg["l4"]["rubric"]["rating_bands"]["Overweight"] = 1
    freeze_card_rules(root, kind="stock-research", config=cfg)
    assert path.read_bytes() == original


def test_scan_published_row_uses_frozen_bands_not_mutable_scan_config(tmp_path, monkeypatch):
    from autoresearch.contracts.agent_output import RUBRIC_DIMENSIONS
    from autoresearch.scan.l4 import rubric
    from autoresearch.scan.l4.parsers import _finalist_row

    dims = dict.fromkeys(RUBRIC_DIMENSIONS, "中")
    dims.update(基本面="强", 估值="强")
    text = decision_text(subject="600519", venue="XSHG", rating="Overweight", dimensions=dims)
    handle, _, _ = setup_card(tmp_path, "600519", "XSHG", text)
    atomic_write_json(
        handle.capsule / "verification/profile.json",
        {
            "card_rules_version": "skills-gap-v3",
            "card_rating_bands": {"Buy": 4, "Overweight": 3, "Hold": -1, "Underweight": -3},
        },
    )
    details = handle.staging / "details"
    details.mkdir()
    (details / "600519.md").write_text(text)
    monkeypatch.setattr(
        rubric,
        "rubric_cfg",
        lambda: {"rating_bands": {"Buy": 4, "Overweight": 1, "Hold": -1, "Underweight": -3}},
    )
    row = _finalist_row(handle.staging, {"code": "600519", "lane": "ordinary"})
    assert row["rubric_suggest"] == "Hold"
    assert row["card_incomplete"] is True


def test_full_publish_uses_the_frozen_custom_bands(tmp_path, monkeypatch):
    import sys

    from autoresearch.analyze import assemble
    from autoresearch.contracts.agent_output import RUBRIC_DIMENSIONS
    from tests.analyze.test_assemble import _populate_required_files

    dims = dict.fromkeys(RUBRIC_DIMENSIONS, "中")
    dims.update(基本面="强", 估值="强")
    text = decision_text(rating="Overweight", dimensions=dims)
    handle, _, _ = setup_card(tmp_path, "NVDA", "XNAS", text)
    root = tmp_path / "NVDA_20260913"
    _populate_required_files(root, text)
    with artifacts.open_artifact(handle, "research.frame") as stream:
        frame = json.loads(stream.read())
    monkeypatch.setattr(sys, "argv", ["assemble.py", str(root)])
    reports = tmp_path / "reports_codex"
    assert (
        assemble.main(
            reports_root=reports,
            decision_context={
                "rules_version": "skills-gap-v3",
                "subject": "NVDA",
                "frame": frame,
                "rating_bands": {"Buy": 4, "Overweight": 3, "Hold": -1, "Underweight": -3},
            },
        )
        == 1
    )
    assert not reports.exists()


def test_actual_capsule_begin_freezes_declared_rating_bands(tmp_path, monkeypatch):
    from autoresearch.trace.capsule import begin_run
    from tests.trace.test_capsule import DATE, NOW, _redirect_roots

    _redirect_roots(monkeypatch, tmp_path)
    handle = begin_run(
        "scan-market",
        DATE,
        "codex",
        {"l4": {"rubric": {"rating_bands": {"Overweight": 3}}}},
        now=NOW,
    )
    profile = json.loads((handle.capsule / "verification/profile.json").read_text())
    assert profile["card_rating_bands"]["Overweight"] == 3


def test_finalization_expectations_do_not_erase_frozen_rating_policy(tmp_path):
    from autoresearch.scan.run_profile import scan_profile
    from autoresearch.trace.completeness import freeze_card_rules, write_expected

    root = tmp_path / "capsule"
    freeze_card_rules(
        root, kind="scan-market", config={"l4": {"rubric": {"rating_bands": {"Overweight": 3}}}}
    )
    write_expected(root, scan_profile())
    assert (
        json.loads((root / "verification/profile.json").read_text())["card_rating_bands"][
            "Overweight"
        ]
        == 3
    )
