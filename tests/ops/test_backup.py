"""D11 知识资产 nightly 备份(precedents.db 判例库 / dossier 档案 / jsonl 账本 /
factor_lab 权重,不可重算资产,此前零备份)。"""
from __future__ import annotations

import tarfile
from pathlib import Path

import pytest

from autoresearch.common import workspace as ws
from autoresearch.ops.backup import DEFAULT_KEEP, _purge_old, run_backup


@pytest.fixture(autouse=True)
def _claude_engine(monkeypatch):
    monkeypatch.setattr(ws, "ENGINE", "claude")


def _seed_assets(root):
    knowledge = root / "context_claude" / "knowledge"
    (knowledge / "dossiers").mkdir(parents=True)
    (knowledge / "precedents.db").write_bytes(b"fake-sqlite-bytes" * 10)
    (knowledge / "dossiers" / "000001.md").write_text("# dossier\n", encoding="utf-8")

    learning = root / "context_claude" / "learning"
    learning.mkdir(parents=True)
    (learning / "gate_ledger.jsonl").write_text('{"a": 1}\n', encoding="utf-8")
    (learning / "buy_ledger.jsonl").write_text('{"b": 2}\n', encoding="utf-8")
    # 非 jsonl 的 staging 文件不应被打进包(设计只要 *.jsonl)
    (learning / "shadow_buys.csv").write_text("date,code\n", encoding="utf-8")

    factor_lab = root / "context_claude" / "factor_lab"
    factor_lab.mkdir(parents=True)
    (factor_lab / "weights.json").write_text('{"w": 1}\n', encoding="utf-8")


def test_run_backup_packs_expected_members(tmp_path):
    _seed_assets(tmp_path)

    result = run_backup(root=tmp_path, keep=14)

    assert result["ok"] is True
    assert result["kept"] == 1
    assert "backups/knowledge_" in result["path"]
    assert result["bytes"] > 0

    tar_path = Path(result["path"])
    assert tar_path.exists()
    assert tar_path.stat().st_size == result["bytes"]

    with tarfile.open(tar_path, "r:gz") as tar:
        names = set(tar.getnames())

    assert "knowledge/precedents.db" in names
    assert "knowledge/dossiers/000001.md" in names
    assert "learning/gate_ledger.jsonl" in names
    assert "learning/buy_ledger.jsonl" in names
    assert "factor_lab/weights.json" in names
    # 非 jsonl 的 learning staging 文件必须被排除
    assert not any(n.endswith("shadow_buys.csv") for n in names)


def test_run_backup_is_idempotent_same_day(tmp_path):
    """同日重跑覆盖当日份(nightly 语义:一天一份快照)——不应产生第二个文件。"""
    _seed_assets(tmp_path)

    first = run_backup(root=tmp_path, keep=14)
    second = run_backup(root=tmp_path, keep=14)

    assert first["path"] == second["path"]
    assert second["kept"] == 1


def test_missing_sources_do_not_crash(tmp_path):
    """空环境(测试夹具没造任何资产)不应报错——生产环境三件通常都在,但防御性跳过缺失项。"""
    result = run_backup(root=tmp_path, keep=14)

    assert result["ok"] is True
    assert result["kept"] == 1
    assert Path(result["path"]).exists()


def test_run_backup_rotation_keeps_only_recent_14(tmp_path):
    """造 15 份旧 tar(日期早于'今天')→ run_backup 再产 1 份"今天"的 = 16 份,
    keep=14 → 删最旧的 2 份(2020-01-01/02),剩 14 份且都不早于 01-03(或是"今天")。"""
    _seed_assets(tmp_path)
    backups_dir = tmp_path / "backups"
    backups_dir.mkdir(parents=True)
    old_dates = [f"2020-01-{i:02d}" for i in range(1, 16)]  # 15 份,远早于"今天"
    for d in old_dates:
        (backups_dir / f"knowledge_{d}.tar.gz").write_bytes(b"stale")

    result = run_backup(root=tmp_path, keep=14)

    remaining = sorted(p.name for p in backups_dir.glob("knowledge_*.tar.gz"))
    assert len(remaining) == 14 == result["kept"]
    # 15 旧 + 1 今天 = 16 份,keep=14 → 删最旧的 2 份
    assert "knowledge_2020-01-01.tar.gz" not in remaining
    assert "knowledge_2020-01-02.tar.gz" not in remaining
    assert "knowledge_2020-01-03.tar.gz" in remaining
    assert result["path"].split("/")[-1] in remaining  # 今天新产的那份必在保留集里


def test_purge_old_deletes_oldest_first(tmp_path):
    """`_purge_old` 单元级:15 份 → keep=14,删的必须是文件名日期最小的那份。"""
    backups_dir = tmp_path / "backups"
    backups_dir.mkdir()
    names = [f"knowledge_2026-0{m}-{d:02d}.tar.gz" for m, d in
              [(1, 20), (1, 21), (1, 22), (1, 23), (1, 24), (1, 25), (1, 26),
               (1, 27), (1, 28), (1, 29), (1, 30), (1, 31), (2, 1), (2, 2), (2, 3)]]
    assert len(names) == 15
    for n in names:
        (backups_dir / n).write_bytes(b"x")

    kept = _purge_old(backups_dir, keep=14)

    assert kept == 14
    remaining = {p.name for p in backups_dir.glob("knowledge_*.tar.gz")}
    assert "knowledge_2026-01-20.tar.gz" not in remaining  # 最旧,必须被删
    assert "knowledge_2026-01-21.tar.gz" in remaining  # 次旧,必须保留
    assert len(remaining) == 14


def test_purge_old_no_op_when_under_limit(tmp_path):
    backups_dir = tmp_path / "backups"
    backups_dir.mkdir()
    (backups_dir / "knowledge_2026-08-01.tar.gz").write_bytes(b"x")
    (backups_dir / "knowledge_2026-08-02.tar.gz").write_bytes(b"x")

    kept = _purge_old(backups_dir, keep=14)

    assert kept == 2
    assert len(list(backups_dir.glob("knowledge_*.tar.gz"))) == 2


def test_default_keep_is_14():
    assert DEFAULT_KEEP == 14


def test_cli_main_uses_cwd(tmp_path, monkeypatch, capsys):
    """`python -m autoresearch.ops.backup` 走 `root=None` → `Path.cwd()` 分支。"""
    _seed_assets(tmp_path)
    monkeypatch.chdir(tmp_path)

    from autoresearch.ops.backup import main
    rc = main()

    assert rc == 0
    out = capsys.readouterr().out
    assert "[backup]" in out
    assert (tmp_path / "backups").exists()
