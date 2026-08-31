"""Repairs are overlays: the base capsule stays exactly as it was frozen."""

from __future__ import annotations

import json

import pytest

from autoresearch.common import workspace as ws
from autoresearch.common.run_identity import RunContract
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

    with pytest.raises(FileExistsError, match="collides with the frozen base"):
        repair(handle.run_id, reason="no-op", source=report_dir / "capsule")


def test_repair_fails_when_only_some_files_collide(frozen, tmp_path):
    """混合冲突整笔失败:碰撞件被静默跳过会让操作者以为证据都补回来了。"""
    handle, _, report_dir, base = frozen
    frozen_file = next(
        path for path in sorted((report_dir / "capsule").rglob("*")) if path.is_file()
    )
    mixed = tmp_path / "mixed"
    collision = mixed / frozen_file.relative_to(report_dir / "capsule")
    collision.parent.mkdir(parents=True, exist_ok=True)
    collision.write_bytes(b"rewritten")
    (mixed / "agents/raw").mkdir(parents=True, exist_ok=True)
    (mixed / "agents/raw/l4-card-600000-9.jsonl.gz").write_bytes(b"\x1f\x8b restored")

    with pytest.raises(FileExistsError, match="collides with the frozen base"):
        repair(handle.run_id, reason="mixed source", source=mixed)

    # 失败必须是空操作:没有叠加层、没有账本 revision、base root 不动。
    assert not (capsule_mod.repairs_root() / handle.run_id).exists()
    revisions = [
        row["revision"] for row in read_valid_ledger() if row["run_id"] == handle.run_id
    ]
    assert revisions == [1]
    assert capsule_mod._load_root(report_dir)["root_hash"] == base.root_hash


def test_repair_rejects_a_source_with_no_files(frozen, tmp_path):
    handle, _, _, _ = frozen
    empty = tmp_path / "empty"
    (empty / "nested").mkdir(parents=True)

    with pytest.raises(RuntimeError, match="repair adds nothing"):
        repair(handle.run_id, reason="nothing restored", source=empty)

    assert not (capsule_mod.repairs_root() / handle.run_id).exists()


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


# ---------------------------------------------------------------- D6.2: cross-kind regression


def _prepare_stock_research_run(
    analysis_date, *, config=None, run_id=None, engine=None,
    workspace_path=None, session_ref=None, now=None,
):
    """stock-research bootstrap 桩(与 `tests/trace/test_capsule_kinds.py` 同款)。"""
    echo = dict(config or {})
    return RunContract.build(
        analysis_date=analysis_date,
        user_config={"mode": echo.get("mode", "FULL"), "ticker": echo.get("ticker", "")},
        pinned={},
        data_policy={},
        stage_budgets={},
        artifact_schema_versions={},
        git_sha="abc1234",
        git_dirty=False,
        dirty_paths=[],
        run_kind="stock-research",
        engine=engine or ws.ENGINE,
        workspace_path=workspace_path,
        session_ref=session_ref,
        run_id=run_id,
        now=now,
    )


@pytest.fixture
def frozen_stock_research(tmp_path, monkeypatch):
    """A frozen `stock-research` capsule — repair() 此前只在 `scan-market`(碰巧与
    `repairs_root()`/`append_ledger_revision()` 的缺省 kind 相同)上测过,这个夹具
    专门撑一个**非缺省** kind 出来,让 `:3153`/`:3178`(修复前的行号)那两处漏传
    kind 的 bug 无处可藏。
    """
    from tests.forensic_fixtures import redirect_roots

    redirect_roots(monkeypatch, tmp_path)
    handle = capsule_mod.begin_run(
        "stock-research",
        "2026-08-27",
        "codex",
        {"mode": "FULL", "ticker": "600000.SS"},
        bootstrap=_prepare_stock_research_run,
    )
    report_dir = ws.reports_root() / "analyze" / handle.run_id
    report_dir.mkdir(parents=True)
    (report_dir / "summary.md").write_text("# synthetic\n", encoding="utf-8")
    checkpoint(handle.run_id, "harvest", "SUCCEEDED", [], {})
    result = finalize(handle.run_id, BusinessStatus.SUCCEEDED, report_dir)
    restored = tmp_path / "restored"
    (restored / "agents/raw").mkdir(parents=True)
    (restored / "agents/raw/company-intel-1.jsonl.gz").write_bytes(b"\x1f\x8b restored")
    return handle, restored, report_dir, result


def test_repair_lands_in_the_correct_kind_ledger_not_scan_market(frozen_stock_research):
    """D6.2:对一趟 `stock-research` run 走完整 repair 必须——

    (a) 不抛异常;(b) overlay 完整(有 `verification/ROOT.json`);
    (c) revision 落进 **analyze** 账本,scan 账本里一行都不该多出来。
    """
    handle, restored, _, base = frozen_stock_research

    result = repair(handle.run_id, reason="transcript restored", source=restored)  # (a)

    assert (result.overlay_path / "verification/ROOT.json").is_file()  # (b)
    assert result.base_root_hash == base.root_hash
    analyze_rows = [
        row for row in read_valid_ledger(kind="stock-research")
        if row["run_id"] == handle.run_id
    ]
    scan_rows = [
        row for row in read_valid_ledger(kind="scan-market")
        if row["run_id"] == handle.run_id
    ]
    assert [row["revision"] for row in analyze_rows] == [1, 2]  # (c)
    assert scan_rows == []
