import hashlib
import json
from datetime import datetime, timezone

import pytest


def frozen_run(tmp_path, *, profile="two-stage-v1"):
    from autoresearch.common import workspace as ws
    from autoresearch.scan.decision_record import DecisionRecord, write_decision_records
    from autoresearch.scan.research_provenance import freeze_research_provenance
    from autoresearch.scan.run_contract import RunContract, write_run_contract
    from autoresearch.trace.capsule import write_manifest

    run = tmp_path / "published"
    stage = run / "trace/staging"
    stage.mkdir(parents=True)
    run_id = "20260930T010000000000Z"
    date = "2026-09-30"
    contract = RunContract.build(
        analysis_date=date,
        user_config={"rule": "frozen"},
        pinned={},
        data_policy={},
        stage_budgets={},
        artifact_schema_versions={},
        git_sha="a" * 40,
        git_dirty=False,
        dirty_paths=[],
        prompt_hashes={"card": "b" * 64},
        run_id=run_id,
        engine=ws.ENGINE,
        workspace_path=ws.scan_run_root(run_id),
        now=datetime(2026, 9, 30, tzinfo=timezone.utc),
    )
    write_run_contract(stage / "run_contract.json", contract)
    (run / "manifest.json").write_text(json.dumps({"run_id": run_id, "analysis_date": date}))
    (stage / "L1_recall_top1000.csv").write_text(
        "code,name,industry,composite\n600000,A,bank,90\n600001,B,bank,80\n"
    )
    (stage / "finalists.csv").write_text(
        "code,conviction,lane\n600000,80,pinned\n600001,70,healthy\n"
    )
    record = DecisionRecord.build(
        analysis_date=date,
        contract_hash=contract.contract_hash,
        code="600000",
        source_rating="Buy",
        post_verify_rating="Overweight",
        rubric_rating="Buy",
        gate_states={},
        early_stop=None,
        ensemble_ratings=[],
        final_rating="Hold",
        proposal="HOLD",
        reason="missing review",
        evidence_refs=[],
        first_rejection_stage=None,
        review_policy_version="review.v1",
        review_trigger="ow_review",
        review_required=True,
        review_status="MISSING",
        review_coverage_reason="no artifact",
    )
    # The book's historical writer requires a date-named directory.
    bookdir = tmp_path / date
    bookdir.mkdir()
    write_run_contract(bookdir / "run_contract.json", contract)
    path = write_decision_records(bookdir, [record])
    (stage / path.name).write_bytes(path.read_bytes())
    source = tmp_path / "accepted.txt"
    source.write_text("shared frozen base")
    freeze_research_provenance(
        stage / "_research_provenance.json",
        run_id=run_id,
        analysis_date=date,
        contract_hash=contract.contract_hash,
        profile=profile,
        candidates={
            "600000": {"initial_rating": "Buy", "force_full": True, "deep_declared": True},
            "600001": {},
        },
        base_inputs=[
            {
                "artifact_id": "card.base",
                "path": str(source),
                "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            }
        ],
    )
    write_manifest(run)
    return run


def test_export_keeps_actual_post_verify_and_missing_review_population(tmp_path):
    from autoresearch.research.production_export import export_run
    from autoresearch.research.stage_adapters import b3_pairs, ensemble_pairs

    run = frozen_run(tmp_path)
    target = export_run(run, tmp_path / "export")
    result = json.loads((target / "export.json").read_text())
    document = json.loads((target / "ensemble.json").read_text())
    assert document["identity"]["run_id"] == "20260930T010000000000Z"
    assert document["identity"]["original_profile"] == "two-stage-v1"
    assert document["rows"][0]["post_verify_rating"] == "Overweight"
    compared = ensemble_pairs(
        result["references"]["ensemble"], selected_ratings=["Overweight", "Buy"]
    )
    assert compared["rows"][0]["baseline"] is True
    assert compared["rows"][0]["refined"] is None and compared["coverage"]["unknown_rows"] == 1
    b3 = b3_pairs(None, result["references"]["b3"], selected_ratings=["Buy"])
    assert len(b3["rows"]) == 2 and b3["coverage"]["unknown_rows"] == 2
    assert b3["additional_evidence_changes"][0]["semantics"].startswith("ADDITIONAL_EVIDENCE")
    assert (target / "population.csv").is_file()
    inputs = json.loads((target / "forward-inputs.json").read_text())
    assert inputs[0]["artifact_id"] == "population"
    assert "deep_read_verified:BOUND_TRANSCRIPT_PROOF_MISSING" in result["missing"]
    with pytest.raises(FileExistsError):
        export_run(run, target)


def test_export_requires_same_run_bytes_not_shared_staging(tmp_path):
    from autoresearch.research.production_export import export_run
    from autoresearch.trace.capsule import write_manifest

    run = frozen_run(tmp_path)
    metadata = run / "trace/staging/_research_provenance.json"
    value = json.loads(metadata.read_text())
    value["run_id"] = "20260930T020000000000Z"
    metadata.write_text(json.dumps(value))
    write_manifest(run)
    with pytest.raises(ValueError, match="identity"):
        export_run(run, tmp_path / "mismatch")


def test_export_rejects_frozen_input_tamper_and_missing_manifest(tmp_path):
    from autoresearch.research.production_export import export_run

    run = frozen_run(tmp_path)
    (run / "trace/staging/research_inputs/0000.bin").write_text("later bytes")
    with pytest.raises(ValueError, match="hash"):
        export_run(run, tmp_path / "tamper")
    (run / "capsule/verification/MANIFEST.sha256").unlink()
    with pytest.raises(FileNotFoundError):
        export_run(run, tmp_path / "unbound")


def test_export_cli_references_replay_exact_e6_and_forward_population(tmp_path):
    from autoresearch.research.forward_study import _frozen_population
    from autoresearch.research.production_export import main
    from autoresearch.research.stage_adapters import e6_ablation, main as adapter_main
    from autoresearch.trace.capsule import write_manifest
    from tests.research.test_stage_adapters import decision

    run = frozen_run(tmp_path)
    e6 = decision()
    e6["date"] = "2026-09-30"
    (run / "trace/staging/_relative_buy_decision.json").write_text(json.dumps(e6))
    write_manifest(run)
    target = tmp_path / "export"
    assert main(["--run-dir", str(run), "--output-dir", str(target)]) == 0
    ref = json.loads((target / "e6.ref.json").read_text())
    result = e6_ablation(ref, remove_face="evidence")
    assert result["baseline"]["buys"] == e6["buys"]
    assert (
        adapter_main(
            [
                "e6",
                "--reference",
                str(target / "e6.ref.json"),
                "--remove-face",
                "evidence",
                "--output",
                str(tmp_path / "comparison.json"),
            ]
        )
        == 0
    )
    population = _frozen_population(
        (target / "population.csv").read_bytes(), suffix=".csv", analysis_date="2026-09-30"
    )
    assert len(population["rows"]) == 2


def test_b3_exports_from_different_real_runs_share_only_actual_base_input_hash(tmp_path):
    from autoresearch.research.production_export import export_run
    from autoresearch.research.stage_adapters import b3_pairs
    from autoresearch.scan.run_contract import sha256_json
    from autoresearch.trace.capsule import write_manifest

    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    a = frozen_run(first, profile="single-stage-v1")
    b = frozen_run(second)
    # Simulate independently frozen identities, not two profiles relabelled in one run.
    run_id = "20260930T020000000000Z"
    manifest = json.loads((b / "manifest.json").read_text())
    manifest["run_id"] = run_id
    (b / "manifest.json").write_text(json.dumps(manifest))
    path = b / "trace/staging/run_contract.json"
    contract = json.loads(path.read_text())
    contract["run_id"] = run_id
    contract["workspace_path"] = contract["workspace_path"].replace("010000", "020000")
    contract["contract_hash"] = sha256_json(
        {k: v for k, v in contract.items() if k != "contract_hash"}
    )
    path.write_text(json.dumps(contract))
    path = b / "trace/staging/_research_provenance.json"
    doc = json.loads(path.read_text())
    doc["run_id"] = run_id
    doc["contract_hash"] = contract["contract_hash"]
    path.write_text(json.dumps(doc))
    path = b / "trace/staging/decision_records.json"
    book = json.loads(path.read_text())
    book["contract_hash"] = contract["contract_hash"]
    for row in book["records"]:
        row["contract_hash"] = contract["contract_hash"]
        row["record_hash"] = sha256_json({k: v for k, v in row.items() if k != "record_hash"})
    book["records_hash"] = sha256_json(book["records"])
    path.write_text(json.dumps(book))
    write_manifest(b)
    left = export_run(a, tmp_path / "one")
    right = export_run(b, tmp_path / "two")
    lref = json.loads((left / "b3.ref.json").read_text())
    rref = json.loads((right / "b3.ref.json").read_text())
    result = b3_pairs(lref, rref, selected_ratings=["Buy"])
    assert result["sides"]["baseline"]["run_id"] != result["sides"]["refined"]["run_id"]
    assert result["sides"]["baseline"]["input_hash"] == result["sides"]["refined"]["input_hash"]


def test_label_export_uses_frozen_study_calendar_for_admission(tmp_path, monkeypatch):
    import pandas as pd

    from autoresearch.common.outcome_sessions import calendar_digest
    from autoresearch.research import forward_study as fs, production_export as export
    from tests.scan.test_populations import CODES, DAYS, _lake, _run
    calendar = {'source':'fixture:sourced-calendar', 'sessions':[{'date': d} for d in DAYS]}
    monkeypatch.setattr(fs, 'read_protocol', lambda _: {'calendar': calendar, 'days':[{'date':'2026-08-03'}]})
    lake = _lake(tmp_path, days=DAYS[:3], gaps={CODES[0]:.04})
    run = _run(tmp_path, top1000=[CODES[0]], finalists=[CODES[0]])
    output = export.export_labels(run, tmp_path / 'labels', study_dir=tmp_path / 'study',
        lake_daily=lake, today=DAYS[2])
    frame = pd.read_csv(output / 'labels.csv', dtype={'code':str})
    assert frame.calendar_digest.iloc[0] == calendar_digest(DAYS,'trade_cal')
    ref = json.loads((output / 'labels.ref.json').read_text())
    fs._validate_labels((output / 'labels.csv').read_bytes(),suffix='.csv',analysis_date='2026-08-03',
        expected_sessions=DAYS,price_inputs=ref['price_inputs'])
