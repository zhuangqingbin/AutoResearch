"""Completeness answers a different question than integrity, and may disagree."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from autoresearch.scan.run_profile import scan_profile
from autoresearch.trace.completeness import (
    build_expected,
    evaluate,
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
