"""Executable registration: a spec must bind the runtime, code, inputs, and test window."""
from __future__ import annotations

import json
import subprocess

import pytest

from autoresearch.research import registration as reg


@pytest.mark.parametrize(
    ("policy", "expected"),
    [
        ("scan_days >= 20", 20),
        ("common.stats.maturity_verdict(scan_days >= 60)", 60),
    ],
)
def test_parse_supported_maturity_policy(policy, expected):
    assert reg.parse_maturity_policy(policy) == expected


@pytest.mark.parametrize(
    "policy", ["20 days", "scan_days > 20", "scan_days >= twenty", "scan_days >= 0"]
)
def test_unsupported_maturity_prose_is_rejected(policy):
    with pytest.raises(ValueError, match="maturity policy"):
        reg.parse_maturity_policy(policy)


def test_file_manifest_binds_content_path_and_date_slice(tmp_path):
    a, b = tmp_path / "a.csv", tmp_path / "b.csv"
    a.write_text("a", encoding="utf-8")
    b.write_text("b", encoding="utf-8")
    date_slice = {"test_start": "20260101", "test_end": "20260902", "semantics": "[start,end)"}

    first = reg.file_manifest([b, a], date_slice)
    second = reg.file_manifest([a, b], date_slice)

    assert first == second
    assert reg.manifest_digest(first) == reg.manifest_digest(second)
    b.write_text("changed", encoding="utf-8")
    assert reg.manifest_digest(reg.file_manifest([a, b], date_slice)) != reg.manifest_digest(first)


def test_registered_test_range_is_normalized_and_half_open():
    spec = {"split": {"test": ["2026-01-01", "2026-09-02"]}}
    assert reg.registered_test_range(spec) == ("20260101", "20260902")


def test_wrong_engine_is_rejected(monkeypatch):
    monkeypatch.setattr(reg.ws, "ENGINE", "codex")
    with pytest.raises(ValueError, match="engine"):
        reg.verify_engine({"engine": "claude"})


def test_code_provenance_rejects_dirty_behavior_path(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    behavior = tmp_path / "behavior.py"
    behavior.write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "behavior.py"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=tmp_path, check=True)
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    behavior.write_text("VALUE = 2\n", encoding="utf-8")

    with pytest.raises(ValueError, match="dirty behavior"):
        reg.verify_code_provenance(
            {"code_sha": sha}, ["behavior.py"], repo_root=tmp_path
        )


def test_code_provenance_rejects_a_fake_sha(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    with pytest.raises(ValueError, match="cannot be verified"):
        reg.verify_code_provenance(
            {"code_sha": "a" * 40}, [], repo_root=tmp_path
        )


def test_code_provenance_rejects_committed_behavior_drift(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    behavior = tmp_path / "behavior.py"
    behavior.write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "behavior.py"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=tmp_path, check=True)
    old_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    behavior.write_text("VALUE = 2\n", encoding="utf-8")
    subprocess.run(["git", "commit", "-qam", "change"], cwd=tmp_path, check=True)

    with pytest.raises(ValueError, match="behavior drift"):
        reg.verify_code_provenance(
            {"code_sha": old_sha}, ["behavior.py"], repo_root=tmp_path
        )
