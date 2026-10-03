"""Completeness answers a different question than integrity, and may disagree."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from autoresearch.contracts.profiles import ArtifactRule, RunProfile
from autoresearch.scan.run_profile import scan_profile
from autoresearch.trace.completeness import (
    build_expected,
    evaluate,
    profile_from_capsule,
    source_coverage,
    write_completeness,
    write_expected,
)


def _touch(path: Path, payload: bytes = b"{}") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def _complete_capsule(root: Path, *, roles=("strategist", "l4-card")) -> Path:
    capsule = root / "capsule"
    for name in (
        "identity/run_contract.json",
        "identity/environment.json",
        "identity/dependencies.txt",
        "identity/source_manifest.json",
        "identity/prompts/l4-card.md",
        "events/events.jsonl",
        "agents/index.json",
        "lineage/reads.jsonl",
        "lineage/coverage.json",
        "usage/_token_usage.json",
        "products/staging/summary.md",
        "capsule.json",
        "verification/replay.json",
    ):
        _touch(capsule / name)
    for stage in scan_profile().expected_stages:
        _touch(capsule / f"stages/{stage}/attempt-1/result.json")
        for channel in ("stdout", "stderr"):
            _touch(
                capsule / f"logs/{stage}/{stage}-1.{channel}.log.gz",
                gzip.compress(b""),
            )
    _touch(
        capsule / "agents/index.json",
        json.dumps(
            {
                "invocations": [
                    {"role": role, "status": "PRESENT"} for role in roles
                ],
                "coverage": {
                    "expected": len(roles),
                    "present": len(roles),
                    "missing": 0,
                },
            }
        ).encode(),
    )
    for role in roles:
        _touch(capsule / f"agents/raw/{role}-1.jsonl.gz", gzip.compress(b"{}\n"))
    return capsule


@pytest.mark.parametrize(
    "mode,expected_l4",
    [("FULL", "REQUIRED"), ("FORCED_FULL", "REQUIRED"), ("SENTINEL_EMPTY", "NOT_EXPECTED")],
)
def test_expected_evidence_is_mode_aware(mode, expected_l4):
    expected = build_expected(scan_profile(mode=mode))

    assert expected.rule("agents/l4-card/*").disposition == expected_l4


def test_failed_run_requires_failure_evidence_and_marks_downstream_not_reached():
    expected = build_expected(
        scan_profile(business_status="FAILED", last_stage="l3")
    )

    assert expected.rule("stages/l3/*/result.json").disposition == "REQUIRED"
    assert expected.rule("logs/l3/*.stderr.log.gz").disposition == "REQUIRED"
    assert expected.rule("stages/l4/*").disposition == "NOT_REACHED"
    assert expected.rule("failure.json").disposition == "REQUIRED"


def test_successful_run_does_not_owe_failure_evidence():
    expected = build_expected(scan_profile())

    assert expected.rule("failure.json").disposition == "NOT_EXPECTED"


def test_deleting_required_transcript_fails_completeness(tmp_path):
    capsule = _complete_capsule(tmp_path, roles=("strategist", "l4-card"))
    profile = scan_profile(agent_roles=("strategist", "l4-card"))
    assert evaluate(capsule, profile)["completeness_ok"] is True

    index = json.loads((capsule / "agents/index.json").read_text())
    index["invocations"] = [
        row for row in index["invocations"] if row["role"] != "l4-card"
    ]
    index["coverage"] = {"expected": 2, "present": 1, "missing": 1}
    (capsule / "agents/index.json").write_text(json.dumps(index))
    (capsule / "agents/raw/l4-card-1.jsonl.gz").unlink()

    result = evaluate(capsule, profile)

    assert result["completeness_ok"] is False
    assert "agents/l4-card/*" in result["missing_required"]
    assert result["coverage"]["agents"]["ok"] is False


def test_missing_stage_result_is_missing_not_not_reached(tmp_path):
    capsule = _complete_capsule(tmp_path)
    profile = scan_profile(agent_roles=("strategist", "l4-card"))
    for path in (capsule / "stages/l3").glob("*/result.json"):
        path.unlink()

    result = evaluate(capsule, profile)

    assert result["completeness_ok"] is False
    assert "stages/l3/*/result.json" in result["missing_required"]
    assert "stages/l3/*" not in result["not_reached"]


def test_completeness_never_calls_manifest_integrity(tmp_path, monkeypatch):
    capsule = _complete_capsule(tmp_path)
    profile = scan_profile(agent_roles=("strategist", "l4-card"))
    # A capsule with no MANIFEST at all must still answer the completeness question.
    assert not (capsule / "verification/MANIFEST.sha256").exists()

    result = write_completeness(capsule, profile)

    assert result["completeness_ok"] is True
    assert json.loads(
        (capsule / "verification/completeness.json").read_text()
    )["completeness_ok"] is True


def test_expected_and_profile_are_frozen_together(tmp_path):
    capsule = _complete_capsule(tmp_path)
    profile = scan_profile(
        mode="SENTINEL_EMPTY", business_status="FAILED", last_stage="l3"
    )

    write_expected(capsule, profile)
    stored = json.loads((capsule / "verification/profile.json").read_text())

    assert stored["mode"] == "SENTINEL_EMPTY"
    assert stored["last_stage"] == "l3"
    # evaluate() with no explicit profile recovers the frozen one, not a default.
    result = evaluate(capsule)
    assert "stages/l4/*" not in result["not_reached"]  # sentinel drops l4 entirely
    assert "failure.json" in result["missing_required"]


def test_card_source_survives_profile_round_trip(tmp_path):
    capsule = _complete_capsule(tmp_path)
    profile = scan_profile(card_source="research_json_v1")

    write_expected(capsule, profile)

    assert profile_from_capsule(capsule).card_source == "research_json_v1"


def test_source_coverage_counts_only_blobs_that_are_inside_the_capsule(tmp_path):
    capsule = _complete_capsule(tmp_path)
    digest = "a" * 64
    (capsule / "lineage/reads.jsonl").write_text(
        json.dumps({"endpoint": "daily", "blob_hash": digest}) + "\n"
        + json.dumps({"endpoint": "adj", "blob_hash": None}) + "\n",
        encoding="utf-8",
    )
    _touch(capsule / f"blobs/sha256/{digest[:2]}/{digest}", b"x")

    coverage = evaluate(capsule, scan_profile())["coverage"]["sources"]

    assert coverage == {
        "reads": 2,
        "covered": 1,
        "uncovered": ["adj"],
        "ok": False,
    }


def test_source_coverage_accepts_captured_failure_but_rejects_missing_success_payload(
    tmp_path,
):
    from types import SimpleNamespace

    from autoresearch.data.contracts import DataContractError
    from autoresearch.trace.blobs import blob_path
    from autoresearch.trace.source_receipts import record_response

    capsule = _complete_capsule(tmp_path)
    handle = SimpleNamespace(
        capsule=capsule,
        engine="codex",
        run_id="20260914T120000000000Z",
    )

    def freeze(outcome, endpoint):
        return record_response(
            handle,
            {
                "engine": handle.engine,
                "run_id": handle.run_id,
                "task_id": "scan.frame",
                "attempt": 1,
                "provider": "tushare",
                "endpoint": endpoint,
                "normalized_params": {},
                "started_at": "2026-09-14T12:00:00Z",
                "ended_at": "2026-09-14T12:00:01Z",
                "as_of": "20260914",
                "available_at": "2026-09-14T12:00:01Z",
                "consumer_refs": [],
            },
            outcome,
        )

    failed = freeze(DataContractError("bad schema"), "daily")
    succeeded = freeze({"ok": True}, "trade_cal")
    (capsule / "lineage/reads.jsonl").write_text(
        json.dumps({"endpoint": "daily", "source_receipt_id": failed["receipt_id"]})
        + "\n"
        + json.dumps(
            {"endpoint": "trade_cal", "source_receipt_id": succeeded["receipt_id"]}
        )
        + "\n",
        encoding="utf-8",
    )

    assert source_coverage(capsule)["ok"] is True
    blob_path(capsule, succeeded["payload_hash"]).unlink()
    coverage = source_coverage(capsule)
    assert coverage["ok"] is False
    assert coverage["uncovered"] == ["trade_cal"]


def test_session_origin_requires_a_recomputed_task_evidence_closure(tmp_path):
    capsule = _complete_capsule(tmp_path)
    _touch(
        capsule / "identity/execution_origin.json",
        json.dumps(
            {
                "schema_version": 1,
                "engine": "codex",
                "run_id": "20260914T120000000000Z",
                "run_kind": "scan-market",
                "orchestration": "session_v1",
                "entrypoint": "autoresearch.session_agent.begin",
                "plan_hash": "a" * 64,
                "host_profile_hash": "b" * 64,
                "legacy_reason": None,
                "created_at": "2026-09-14T12:00:00Z",
            }
        ).encode(),
    )

    result = evaluate(capsule, scan_profile())

    assert result["completeness_ok"] is False
    assert result["coverage"]["tasks"]["applicable"] is True
    assert "evidence/evidence_plan.json" in result["missing_required"]


def test_registered_main_transcript_is_part_of_completeness(tmp_path):
    capsule = _complete_capsule(tmp_path)
    _touch(
        capsule / "identity/session/host_evidence.json",
        json.dumps(
            {
                "schema_version": 1,
                "engine": "codex",
                "run_id": "20260914T120000000000Z",
                "session_ref": "session-main",
                "main_transcript": {
                    "status": "REGISTERED",
                    "source_path": "/external/main.jsonl",
                    "start_ordinal": 5,
                },
            }
        ).encode(),
    )

    result = evaluate(capsule, scan_profile())

    assert result["completeness_ok"] is False
    assert result["coverage"]["host"]["applicable"] is True
    assert "evidence/main_host.json" in result["missing_required"]


# ---------------------------------------------------------------- D6.5: evidence levels


def test_completeness_levels_bucketed(tmp_path):
    """两条 L0 一条 L1、命中 2/3 → `levels` 按 level 分桶,L1 那条 MISSING(D6.5)。"""
    capsule = tmp_path / "capsule"
    (capsule / "a.json").parent.mkdir(parents=True, exist_ok=True)
    (capsule / "a.json").write_bytes(b"{}")
    (capsule / "b.json").write_bytes(b"{}")
    # c.json 故意不写 → 那条 L1 规则 MISSING。
    profile = RunProfile(
        kind="scan-market",
        expected_stages=(),
        agent_roles=(),
        artifact_rules=(
            ArtifactRule("a", "a.json", "capsule", "always"),
            ArtifactRule("b", "b.json", "capsule", "always"),
            ArtifactRule("c", "c.json", "capsule", "always", evidence_level="L1"),
        ),
        replayable_stages=(),
    )

    result = evaluate(capsule, profile)

    assert result["levels"]["L0"] == {"required": 2, "hit": 2}
    assert result["levels"]["L1"] == {"required": 1, "hit": 0}
    assert "L2" not in result["levels"]


def test_scan_rules_default_l0():
    """scan profile 全部规则 `evidence_level == "L0"`(行为守恒探针,D6.5 红线)。"""
    profile = scan_profile()

    assert all(rule.evidence_level == "L0" for rule in profile.artifact_rules)


def test_evidence_level_never_changes_the_completeness_verdict(tmp_path):
    """把一条规则的 `evidence_level` 从 L0 换成 L2,判定必须逐字不变(D6.5 红线)。

    这是「只加字段不改判定」的变异式对照:同一份胶囊算两次,唯二的差异是
    `usage_ledger` 规则的 `evidence_level`——`completeness_ok`/`counts`/
    `missing_required`/`not_expected`/`not_reached` 必须逐字相同,只有新增的
    `levels` 键该变(因为它就是喂 `levels` 用的,别的字段读不到这个维度)。
    """
    from dataclasses import replace

    capsule = _complete_capsule(tmp_path, roles=("strategist", "l4-card"))
    base_profile = scan_profile(agent_roles=("strategist", "l4-card"))
    relabeled_rules = tuple(
        replace(rule, evidence_level="L2") if rule.key == "usage_ledger" else rule
        for rule in base_profile.artifact_rules
    )
    relabeled_profile = replace(base_profile, artifact_rules=relabeled_rules)

    baseline = evaluate(capsule, base_profile)
    relabeled = evaluate(capsule, relabeled_profile)

    for key in ("completeness_ok", "counts", "missing_required",
                "not_expected", "not_reached"):
        assert baseline[key] == relabeled[key], key
    assert baseline["levels"] != relabeled["levels"]
    assert relabeled["levels"]["L2"] == {"required": 1, "hit": 1}
    assert set(baseline["levels"]) == {"L0"}


def test_card_rule_version_is_frozen_and_missing_historical_field_is_legacy(tmp_path):
    from dataclasses import replace
    from autoresearch.scan.run_profile import scan_profile

    capsule = tmp_path / "capsule"
    profile = scan_profile(card_rules_version="skills-gap-v2")
    assert profile.card_rules_version == "skills-gap-v2"
    write_expected(capsule, profile)
    assert profile_from_capsule(capsule).card_rules_version == "skills-gap-v2"
    path = capsule / "verification/profile.json"
    payload = json.loads(path.read_text())
    payload.pop("card_rules_version")
    path.write_text(json.dumps(payload))
    assert profile_from_capsule(capsule).card_rules_version == "legacy-v1"
    # Re-finalization must preserve an already frozen historical version.
    write_expected(capsule, profile)
    assert profile_from_capsule(capsule).card_rules_version == "legacy-v1"
    assert profile_from_capsule(tmp_path / "historical").card_rules_version == "legacy-v1"
    with pytest.raises(ValueError):
        replace(profile, card_rules_version="unknown")
