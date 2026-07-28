"""StageResult: finite states, integrity, safe paths, and idempotent snapshots."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from autoresearch.scan.run_contract import RunContract, write_run_contract
from autoresearch.scan.stage_result import (
    StageResult,
    StageStatus,
    load_stage_result,
    main,
    record_stage_result,
    write_stage_result,
)

NOW = datetime(2026, 7, 28, 15, 0, tzinfo=timezone.utc)


def _result(*, status=StageStatus.SUCCEEDED, now=NOW):
    return StageResult.build(
        stage="gate1",
        analysis_date="2026-07-28",
        status=status,
        artifacts=["l2"],
        metrics={"l2_n": 200},
        warnings=[],
        error=None,
        contract_hash="a" * 64,
        now=now,
    )


def test_stage_result_round_trip_and_status_enum(tmp_path):
    path = write_stage_result(tmp_path, _result())
    loaded = load_stage_result(path)
    assert path == tmp_path / "stage_results" / "gate1.json"
    assert loaded.status == "SUCCEEDED"
    assert loaded.to_dict() == _result().to_dict()


def test_write_is_semantically_idempotent(tmp_path):
    first = write_stage_result(tmp_path, _result())
    before = first.read_bytes()
    later = datetime(2026, 7, 28, 16, 0, tzinfo=timezone.utc)
    second = write_stage_result(tmp_path, _result(now=later))
    assert second == first
    assert second.read_bytes() == before


def test_changed_semantics_replace_snapshot(tmp_path):
    path = write_stage_result(tmp_path, _result())
    before = path.read_bytes()
    write_stage_result(tmp_path, _result(status=StageStatus.FAILED))
    assert path.read_bytes() != before
    assert load_stage_result(path).status == "FAILED"


def test_load_rejects_tampered_result(tmp_path):
    path = write_stage_result(tmp_path, _result())
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["metrics"]["l2_n"] = 0
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="result_hash"):
        load_stage_result(path)


@pytest.mark.parametrize("stage", ["../gate1", "Gate 1", "", "x/y"])
def test_stage_name_rejects_unsafe_paths(stage):
    with pytest.raises(ValueError, match="stage"):
        StageResult.build(
            stage=stage,
            analysis_date="2026-07-28",
            status="SUCCEEDED",
            artifacts=[],
            metrics={},
            warnings=[],
            error=None,
            contract_hash=None,
            now=NOW,
        )


def test_record_binds_valid_run_contract(tmp_path):
    contract = RunContract.build(
        analysis_date="2026-07-28",
        user_config={},
        pinned={"kept": [], "expired": []},
        data_policy={"source": "tushare"},
        stage_budgets={},
        artifact_schema_versions={},
        git_sha="abc",
        now=NOW,
    )
    write_run_contract(tmp_path / "run_contract.json", contract)
    path = record_stage_result(
        tmp_path,
        stage="gate2",
        status="FAILED",
        artifacts=["finalists"],
        metrics={"budget": 10},
        warnings=[],
        error="finalists 空",
        now=NOW,
    )
    assert load_stage_result(path).contract_hash == contract.contract_hash


def test_show_cli_returns_verified_snapshot(tmp_path, capsys):
    record_stage_result(
        tmp_path,
        stage="gate1",
        status="SUCCEEDED",
        artifacts=["l2"],
        metrics={"sentinel_level": "full", "l4_budget": 10},
        warnings=[],
        error=None,
        now=NOW,
    )

    assert main(["show", str(tmp_path), "gate1"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["stage"] == "gate1"
    assert shown["status"] == "SUCCEEDED"
    assert shown["metrics"]["sentinel_level"] == "full"


def test_show_cli_fails_for_missing_or_corrupt_snapshot(tmp_path, capsys):
    assert main(["show", str(tmp_path), "gate1"]) == 2
    missing = json.loads(capsys.readouterr().out)
    assert missing["status"] == "INVALID"
    assert "FileNotFoundError" in missing["error"]

    path = tmp_path / "stage_results" / "gate1.json"
    path.parent.mkdir()
    path.write_text("{", encoding="utf-8")
    assert main(["show", str(tmp_path), "gate1"]) == 2
    corrupt = json.loads(capsys.readouterr().out)
    assert corrupt["status"] == "INVALID"
    assert "JSONDecodeError" in corrupt["error"]


def test_show_cli_rejects_contract_mismatch(tmp_path, capsys):
    first = RunContract.build(
        analysis_date=tmp_path.name,
        user_config={},
        pinned={"kept": [], "expired": []},
        data_policy={"source": "tushare"},
        stage_budgets={},
        artifact_schema_versions={},
        git_sha="first",
        now=NOW,
    )
    write_run_contract(tmp_path / "run_contract.json", first)
    record_stage_result(
        tmp_path,
        stage="gate1",
        status="SUCCEEDED",
        artifacts=["l2"],
        metrics={},
        warnings=[],
        error=None,
        now=NOW,
    )
    second = RunContract.build(
        analysis_date=tmp_path.name,
        user_config={"force_full": True},
        pinned={"kept": [], "expired": []},
        data_policy={"source": "tushare"},
        stage_budgets={},
        artifact_schema_versions={},
        git_sha="second",
        now=NOW,
    )
    write_run_contract(tmp_path / "run_contract.json", second)

    assert main(["show", str(tmp_path), "gate1"]) == 2
    error = json.loads(capsys.readouterr().out)
    assert "contract_hash mismatch" in error["error"]
