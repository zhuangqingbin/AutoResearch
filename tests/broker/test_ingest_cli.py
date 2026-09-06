"""ingest CLI:真跑一条截图路样本 —— 落盘/幂等/拒收不落盘/试跑不写/摘要屏/退出码。"""
from __future__ import annotations

import json

import pytest

from autoresearch.broker import ingest, store
from autoresearch.broker.adapters import screenshot

HEADER = ",".join(screenshot.SCREENSHOT_HEADER)
BUY = "2026-08-25,09:31:05,000001,平安银行,证券买入,12.34,100,1234.00,5.00,0.00,0.01,0,-1239.01,100"
SELL = "2026-08-26,14:55:10,000001,平安银行,证券卖出,12.50,100,1250.00,5.00,0.63,0.01,0,1244.36,0"


@pytest.fixture
def inbox(tmp_path):
    d = tmp_path / "inbox" / "screenshot"
    d.mkdir(parents=True)
    (d / "gtht_20260825-20260826.csv").write_text(f"{HEADER}\n{BUY}\n{SELL}\n", encoding="utf-8")
    return tmp_path / "inbox"


def _run(inbox, root, *extra):
    return ingest.main([str(inbox), "--root", str(root), *extra])


def _log(root):
    return [json.loads(x) for x in store.log_path(root).read_text(encoding="utf-8").splitlines()]


def test_ingest_writes_raw_trades_and_log(inbox, tmp_path, capsys):
    root = tmp_path / "root"
    assert _run(inbox, root) == 0
    assert len(store.read_raw(store.raw_path(root, "screenshot"))) == 2
    assert store.trades_path(root).exists()
    log = _log(root)
    assert len(log) == 1 and log[0]["status"] == "ok" and log[0]["new"] == 2
    assert "sha256" in log[0] and "period" in log[0] and "rows" in log[0]
    out = capsys.readouterr().out
    assert "gtht  2026-08-25..2026-08-26  BUY 1 / SELL 1 / OTHER 0" in out
    assert "成交额 2,484" in out and "费用 11" in out and "B降级(本次) 0" in out
    assert "拒收 0" in out
    assert "trades.csv 重建:2 行" in out


def test_second_run_skips_and_is_byte_stable(inbox, tmp_path, capsys):
    root = tmp_path / "root"
    _run(inbox, root)
    before = store.trades_path(root).read_bytes()
    assert _run(inbox, root) == 0
    assert store.trades_path(root).read_bytes() == before
    out = capsys.readouterr().out
    assert "已导入跳过 1" in out and "↷ 已导入 gtht_20260825-20260826.csv" in out
    assert len(_log(root)) == 1


def test_force_reparses_but_adds_nothing(inbox, tmp_path):
    root = tmp_path / "root"
    _run(inbox, root)
    before = store.trades_path(root).read_bytes()
    assert _run(inbox, root, "--force") == 0
    assert store.trades_path(root).read_bytes() == before
    assert _log(root)[-1]["new"] == 0 and _log(root)[-1]["dup"] == 2


def test_rejected_file_writes_nothing_but_log_and_exit_1(inbox, tmp_path, capsys):
    root = tmp_path / "root"
    bad = inbox / "screenshot" / "tpy_20260801-20260826.csv"
    bad.write_text(f"{HEADER}\n{BUY.replace('1234.00', '9999')}\n", encoding="utf-8")
    assert _run(inbox, root) == 1
    assert not store.raw_path(root, "tpy").exists()
    assert {e["status"] for e in _log(root)} == {"ok", "rejected"}
    assert "拒收" in capsys.readouterr().out
    assert len(store.read_raw(store.raw_path(root, "screenshot"))) == 2   # 好文件照常入
    rej = [e for e in _log(root) if e["status"] == "rejected"][0]
    assert "第1行" not in rej["a_error"] and "9999" not in rej["a_error"]   # §13:不记逐笔明细
    assert rej["a_problems"] == 1


def test_dry_run_writes_nothing(inbox, tmp_path, capsys):
    root = tmp_path / "root"
    assert _run(inbox, root, "--dry-run") == 0
    assert not root.exists()
    assert "试跑" in capsys.readouterr().out


def test_unknown_source_dir_exits_2_before_any_write(inbox, tmp_path):
    d = inbox / "misc"
    d.mkdir()
    (d / "x.csv").write_text("a\n", encoding="utf-8")
    root = tmp_path / "root"
    assert _run(inbox, root) == 2          # inbox 里还有一份好文件,也不许先写它
    assert not root.exists()


def test_source_flag_overrides_dir_name(tmp_path):
    d = tmp_path / "misc"
    d.mkdir()
    (d / "gtht_20260825-20260826.csv").write_text(f"{HEADER}\n{BUY}\n", encoding="utf-8")
    root = tmp_path / "root"
    assert ingest.main([str(d), "--root", str(root), "--source", "screenshot"]) == 0
    assert len(store.read_raw(store.raw_path(root, "screenshot"))) == 1


def test_account_flag_conflicting_with_filename_rejects_not_relabels(inbox, tmp_path):
    (inbox / "screenshot" / "tpy_20260825-20260826.csv").write_text(f"{HEADER}\n{BUY}\n",
                                                                     encoding="utf-8")
    root = tmp_path / "root"
    assert _run(inbox, root, "--account", "gtht") == 1
    raw = store.read_raw(store.raw_path(root, "screenshot"))
    assert set(raw.account) == {"gtht"} and len(raw) == 2     # tpy 行没有被改名成 gtht 后吞掉
    assert [e["file"] for e in _log(root) if e["status"] == "rejected"] == ["tpy_20260825-20260826.csv"]


def test_zero_byte_file_is_rejected_and_batch_still_completes(inbox, tmp_path):
    (inbox / "screenshot" / "tpy_20260801-20260826.csv").write_bytes(b"")
    root = tmp_path / "root"
    assert _run(inbox, root) == 1
    assert store.trades_path(root).exists()
    assert len(store.read_raw(store.raw_path(root, "screenshot"))) == 2
    assert {e["status"] for e in _log(root)} == {"ok", "rejected"}


def test_source_without_adapter_exits_2(tmp_path):
    d = tmp_path / "inbox" / "chinaclear"
    d.mkdir(parents=True)
    (d / "x.pdf").write_bytes(b"%PDF-1.4")
    assert ingest.main([str(d), "--root", str(tmp_path / "root")]) == 2
    assert not (tmp_path / "root").exists()


def test_iter_files_skips_hidden_and_recurses(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / ".DS_Store").write_bytes(b"")
    (tmp_path / "a" / "x.csv").write_text("x", encoding="utf-8")
    (tmp_path / "a" / "b").mkdir()
    (tmp_path / "a" / "b" / "y.csv").write_text("y", encoding="utf-8")
    assert {p.name for p in ingest.iter_files([tmp_path / "a"])} == {"x.csv", "y.csv"}
    with pytest.raises(FileNotFoundError):
        ingest.iter_files([tmp_path / "nope"])


def test_help_works():
    with pytest.raises(SystemExit) as e:
        ingest.main(["--help"])
    assert e.value.code == 0
