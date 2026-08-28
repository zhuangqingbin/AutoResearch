"""Repairs are overlays: the base capsule stays exactly as it was frozen."""

from __future__ import annotations

import json

import pytest

from autoresearch.common import workspace as ws
from autoresearch.trace import capsule as capsule_mod
from autoresearch.trace.capsule import (
    BusinessStatus,
    checkpoint,
    finalize,
    read_valid_ledger,
    repair,
)


@pytest.fixture
def frozen(codex_run, tmp_path):
    handle, source = codex_run
    report_dir = ws.reports_root() / "scan" / handle.run_id
    report_dir.mkdir(parents=True)
    (report_dir / "summary.md").write_text("# synthetic\n", encoding="utf-8")
    checkpoint(handle.run_id, "l3", "SUCCEEDED", [], {})
    result = finalize(handle.run_id, BusinessStatus.SUCCEEDED, report_dir)
    # 修复的真实来源:事后找回来的证据被人放进一个暂存目录,而不是回头改冻结的现场。
    restored = tmp_path / "restored"
    (restored / "agents/raw").mkdir(parents=True)
    (restored / "agents/raw/l4-card-600000-9.jsonl.gz").write_bytes(b"\x1f\x8b restored")
    return handle, restored, report_dir, result


def test_repair_writes_overlay_and_preserves_base_root(frozen):
    handle, restored, report_dir, base = frozen

    result = repair(handle.run_id, reason="transcript restored", source=restored)

    assert result.revision == 2
    assert result.base_root_hash == base.root_hash
    assert result.composite_root_hash != base.root_hash
    assert result.overlay_path.name == "revision-2"
    assert capsule_mod._load_root(report_dir)["root_hash"] == base.root_hash
    assert any(name.startswith("capsule/agents/") for name in result.added)


def test_repair_cannot_overwrite_a_base_path(frozen):
    handle, _, report_dir, _ = frozen

    with pytest.raises(RuntimeError, match="repair adds nothing"):
        repair(handle.run_id, reason="no-op", source=report_dir / "capsule")


def test_repair_appends_one_ledger_revision_chained_to_the_first(frozen):
    handle, restored, _, base = frozen

    result = repair(handle.run_id, reason="transcript restored", source=restored)

    rows = [row for row in read_valid_ledger() if row["run_id"] == handle.run_id]
    assert [row["revision"] for row in rows] == [1, 2]
    assert rows[1]["prev_hash"] == rows[0]["row_hash"]
    assert rows[1]["base_root_hash"] == base.root_hash
    assert rows[1]["root_hash"] == result.composite_root_hash
    assert rows[1]["repair_reason"] == "transcript restored"


def test_repair_overlay_is_frozen_and_self_describing(frozen):
    handle, restored, _, _ = frozen

    result = repair(handle.run_id, reason="transcript restored", source=restored)

    root = json.loads(
        (result.overlay_path / "verification/ROOT.json").read_text(encoding="utf-8")
    )
    assert root["reason"] == "transcript restored"
    assert root["revision"] == 2
    assert (result.overlay_path / "verification/MANIFEST.sha256").is_file()
    with pytest.raises(PermissionError):
        (result.overlay_path / "verification/ROOT.json").write_text("x")


def test_repair_requires_a_reason_and_a_frozen_base(frozen):
    handle, _, _, _ = frozen

    with pytest.raises(ValueError, match="must state its reason"):
        repair(handle.run_id, reason="  ")
    with pytest.raises(FileNotFoundError):
        repair("20260827T999999999999Z", reason="nothing here")
