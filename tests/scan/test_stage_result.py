"""StageResult: finite states, integrity, safe paths, and idempotent snapshots."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from autoresearch.common import workspace as ws
from autoresearch.scan.run_contract import RunContract, write_run_contract
from autoresearch.scan.stage_result import (
    StageResult,
    StageStatus,
    load_stage_result,
    main,
    record_stage_result,
    safe_record_stage_result,
    write_stage_result,
)
from autoresearch.trace.capsule import begin_run

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
        analysis_date="2026-07-28",
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
        analysis_date="2026-07-28",
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


def _begin_capsule(tmp_path, monkeypatch):
    monkeypatch.setattr(ws, "ENGINE", "codex")
    monkeypatch.setattr(ws, "context_root", lambda: tmp_path / "context_codex")
    monkeypatch.setattr(ws, "reports_root", lambda: tmp_path / "reports_codex")
    monkeypatch.delenv("AUTORESEARCH_RUN_ID", raising=False)
    monkeypatch.setattr(
        "autoresearch.scan.user_config.DEFAULT_PINNED_PATH",
        tmp_path / "missing-pinned.jsonc",
    )
    return begin_run(
        "scan-market",
        "2026-08-27",
        "codex",
        {},
        now=datetime(2026, 8, 27, 1, 2, 3, 456789, tzinfo=timezone.utc),
    )


def test_safe_stage_result_keeps_latest_snapshot_and_appends_attempts(
    tmp_path, monkeypatch
):
    handle = _begin_capsule(tmp_path, monkeypatch)
    monkeypatch.setenv("AUTORESEARCH_RUN_ID", handle.run_id)
    safe_record_stage_result(
        handle.staging,
        stage="gate1",
        status="FAILED",
        artifacts=[],
        metrics={},
        warnings=[],
        error="bad",
    )
    safe_record_stage_result(
        handle.staging,
        stage="gate1",
        status="SUCCEEDED",
        artifacts=[],
        metrics={},
        warnings=[],
        error=None,
    )
    assert load_stage_result(
        handle.staging / "stage_results/gate1.json"
    ).status == "SUCCEEDED"
    attempts = sorted((handle.capsule / "stages/gate1").glob("attempt-*/result.json"))
    assert len(attempts) == 2
    assert json.loads(attempts[0].read_text(encoding="utf-8"))["error"] == "bad"


def test_record_stage_result_remains_environment_unaware(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTORESEARCH_RUN_ID", "20260827T010203456789Z")
    monkeypatch.setattr(
        "autoresearch.trace.capsule.checkpoint",
        lambda *a, **k: pytest.fail("deterministic API called capsule"),
    )
    assert record_stage_result(
        tmp_path / "2026-08-27",
        stage="gate1",
        status="SUCCEEDED",
        artifacts=[],
        metrics={},
        warnings=[],
        error=None,
    ).is_file()


def test_safe_stage_result_capsule_failure_is_best_effort(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("AUTORESEARCH_RUN_ID", "20260827T010203456789Z")
    monkeypatch.setattr(
        "autoresearch.trace.capsule.checkpoint",
        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")),
    )
    path = safe_record_stage_result(
        tmp_path / "2026-08-27",
        stage="gate1",
        status="SUCCEEDED",
        artifacts=[],
        metrics={},
        warnings=[],
        error=None,
    )
    assert path is not None and path.is_file()
    assert "[capsule]" in capsys.readouterr().err


def test_safe_stage_result_resolves_l2_artifact_id_to_real_file(tmp_path, monkeypatch):
    handle = _begin_capsule(tmp_path, monkeypatch)
    monkeypatch.setenv("AUTORESEARCH_RUN_ID", handle.run_id)
    source = handle.staging / "L2_gbdt_top200.csv"
    source.write_bytes(b"code\n600000\n")
    safe_record_stage_result(
        handle.staging,
        stage="gate1",
        status="SUCCEEDED",
        artifacts=["l2"],
        metrics={},
        warnings=[],
        error=None,
    )
    outputs = json.loads(
        (handle.capsule / "stages/gate1/attempt-1/outputs.json").read_text(
            encoding="utf-8"
        )
    )["artifacts"]
    assert outputs == [
        {
            "bytes": len(b"code\n600000\n"),
            "captured_path": "products/staging/gate1/attempt-1/scan/L2_gbdt_top200.csv",
            "logical_id": "l2",
            "path": "L2_gbdt_top200.csv",
            "pattern": "L2_gbdt_top200.csv",
            "root": "scan",
            "sha256": outputs[0]["sha256"],
            "status": "PRESENT",
        }
    ]
    assert (
        handle.capsule
        / "products/staging/gate1/attempt-1/scan/L2_gbdt_top200.csv"
    ).read_bytes() == source.read_bytes()


def test_safe_stage_result_resolves_multifile_and_report_root_specs(
    tmp_path, monkeypatch
):
    handle = _begin_capsule(tmp_path, monkeypatch)
    monkeypatch.setenv("AUTORESEARCH_RUN_ID", handle.run_id)
    details = handle.staging / "details"
    details.mkdir()
    (details / "600000.md").write_text("one", encoding="utf-8")
    (details / "600001.md").write_text("two", encoding="utf-8")
    report = tmp_path / "reports_codex/scan/20260827_1200"
    report.mkdir(parents=True)
    (report / "summary.md").write_text("summary", encoding="utf-8")
    safe_record_stage_result(
        handle.staging,
        stage="assemble",
        status="SUCCEEDED",
        artifacts=["l4_cards", "summary"],
        metrics={},
        warnings=[],
        error=None,
        report_dir=report,
    )
    rows = json.loads(
        (handle.capsule / "stages/assemble/attempt-1/outputs.json").read_text(
            encoding="utf-8"
        )
    )["artifacts"]
    assert [(row["logical_id"], row["root"], row["path"]) for row in rows] == [
        ("l4_cards", "scan", "details/600000.md"),
        ("l4_cards", "scan", "details/600001.md"),
        ("summary", "report", "summary.md"),
    ]
    for row in rows:
        assert (handle.capsule / row["captured_path"]).is_file()


def test_assemble_checkpoint_captures_report_candidate_inside_same_run_staging(
    tmp_path, monkeypatch, capsys
):
    handle = _begin_capsule(tmp_path, monkeypatch)
    monkeypatch.setenv("AUTORESEARCH_RUN_ID", handle.run_id)
    candidate = handle.staging / "session_outputs/report_build/scan.assemble/a1/candidate"
    candidate.mkdir(parents=True)
    summary = candidate / "summary.md"
    summary.write_text("assembled candidate", encoding="utf-8")
    safe_record_stage_result(handle.staging, stage="assemble", status="SUCCEEDED",
                             artifacts=["summary"], metrics={}, warnings=[], error=None,
                             report_dir=candidate)
    assert "checkpoint 失败" not in capsys.readouterr().err
    rows = json.loads((handle.capsule / "stages/assemble/attempt-1/outputs.json").read_text())["artifacts"]
    assert [(row["logical_id"], row["root"], row["path"], row["status"]) for row in rows] == [
        ("summary", "report", "summary.md", "PRESENT")]
    assert (handle.capsule / rows[0]["captured_path"]).read_bytes() == summary.read_bytes()


def test_explicit_staged_candidate_also_captures_literal_report_artifact(tmp_path, monkeypatch):
    from autoresearch.trace.capsule import checkpoint

    handle = _begin_capsule(tmp_path, monkeypatch)
    candidate = handle.staging / "session_outputs/report_build/candidate"
    candidate.mkdir(parents=True)
    summary = candidate / "summary.md"
    summary.write_text("literal candidate", encoding="utf-8")
    checkpoint(handle.run_id, "assemble", "SUCCEEDED", [summary], {},
               staged_report_dir=candidate)
    rows = json.loads((handle.capsule / "stages/assemble/attempt-1/outputs.json").read_text())["artifacts"]
    assert rows[0]["root"] == "report" and rows[0]["path"] == "summary.md"


@pytest.mark.parametrize("stage", ["gate1", "l4", "post_run"])
def test_staged_report_optin_is_restricted_to_assemble(tmp_path, monkeypatch, stage):
    from autoresearch.trace.capsule import checkpoint

    handle = _begin_capsule(tmp_path, monkeypatch)
    candidate = handle.staging / "candidate"
    candidate.mkdir()
    with pytest.raises(ValueError, match="assemble"):
        checkpoint(handle.run_id, stage, "SUCCEEDED", ["summary"], {},
                   staged_report_dir=candidate)
    assert not (handle.capsule / f"stages/{stage}").exists()


@pytest.mark.parametrize("escape", ["outside", "parent", "staging-root", "symlink"])
def test_staged_report_rejects_escape_root_and_symlink_before_capture(
    tmp_path, monkeypatch, escape
):
    from autoresearch.trace.capsule import checkpoint

    handle = _begin_capsule(tmp_path, monkeypatch)
    outside = tmp_path / "outside"
    outside.mkdir()
    if escape == "outside":
        candidate = outside
    elif escape == "parent":
        candidate = handle.staging / ".." / "staging" / "candidate"
        (handle.staging / "candidate").mkdir()
    elif escape == "staging-root":
        candidate = handle.staging
    else:
        candidate = handle.staging / "linked"
        candidate.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="staging|symlink|travers"):
        checkpoint(handle.run_id, "assemble", "SUCCEEDED", ["summary"], {},
                   staged_report_dir=candidate)
    assert not (handle.capsule / "stages/assemble").exists()


def test_report_candidate_never_qualifies_as_published_report_dir(tmp_path, monkeypatch):
    from autoresearch.trace.capsule import checkpoint, _validate_report_dir

    handle = _begin_capsule(tmp_path, monkeypatch)
    candidate = handle.staging / "candidate"
    candidate.mkdir()
    with pytest.raises(ValueError, match="reports root"):
        _validate_report_dir(candidate)
    with pytest.raises(ValueError, match="reports root"):
        checkpoint(handle.run_id, "assemble", "SUCCEEDED", ["summary"], {},
                   report_dir=candidate)


def test_checkpoint_report_and_candidate_roots_are_mutually_exclusive(tmp_path, monkeypatch):
    from autoresearch.trace.capsule import checkpoint

    handle = _begin_capsule(tmp_path, monkeypatch)
    candidate = handle.staging / "candidate"
    candidate.mkdir()
    published = tmp_path / "reports_codex/scan/published"
    published.mkdir(parents=True)
    with pytest.raises(ValueError, match="mutually exclusive"):
        checkpoint(handle.run_id, "assemble", "SUCCEEDED", ["summary"], {},
                   report_dir=published, staged_report_dir=candidate)


def test_safe_stage_result_rejects_ambient_run_bound_to_other_staging(
    tmp_path, monkeypatch, capsys
):
    first = _begin_capsule(tmp_path, monkeypatch)
    monkeypatch.delenv("AUTORESEARCH_RUN_ID", raising=False)
    second = begin_run(
        "scan-market",
        "2026-08-27",
        "codex",
        {},
        now=datetime(2026, 8, 27, 1, 2, 3, 456790, tzinfo=timezone.utc),
    )
    monkeypatch.setenv("AUTORESEARCH_RUN_ID", first.run_id)
    result = safe_record_stage_result(
        second.staging,
        stage="gate1",
        status="SUCCEEDED",
        artifacts=[],
        metrics={},
        warnings=[],
        error=None,
    )
    assert result is not None and result.is_file()
    assert not (first.capsule / "stages/gate1").exists()
    assert "[capsule]" in capsys.readouterr().err


def test_assemble_stage_result_carries_both_halves_of_the_publish_bundle(
    tmp_path, monkeypatch
):
    """§6.2:summary 与 appendix 是**一个发布包**。控制面用 StageResult 声明 L5 实际
    产物 —— 只登记 summary 会让「附录没落盘」在控制面上完全不可见。"""
    handle = _begin_capsule(tmp_path, monkeypatch)
    monkeypatch.setenv("AUTORESEARCH_RUN_ID", handle.run_id)
    report = tmp_path / "reports_codex/scan/20260828_2100"
    report.mkdir(parents=True)
    (report / "summary.md").write_text("决策层", encoding="utf-8")
    (report / "appendix.md").write_text("现场层", encoding="utf-8")
    safe_record_stage_result(
        handle.staging,
        stage="assemble",
        status="SUCCEEDED",
        artifacts=["summary", "appendix"],
        metrics={},
        warnings=[],
        error=None,
        report_dir=report,
    )
    rows = json.loads(
        (handle.capsule / "stages/assemble/attempt-1/outputs.json").read_text(
            encoding="utf-8"
        )
    )["artifacts"]
    assert [(row["logical_id"], row["root"], row["path"]) for row in rows] == [
        ("summary", "report", "summary.md"),
        ("appendix", "report", "appendix.md"),
    ]
    for row in rows:
        assert (handle.capsule / row["captured_path"]).is_file()
