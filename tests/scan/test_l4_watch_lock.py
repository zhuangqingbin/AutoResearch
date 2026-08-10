"""C4-2(design 2026-08-10):同日第二个 --watch 必须拒绝 —— 双挂会共用 cursor 重播事件
(2026-08-09 实测双 Monitor 重播 4 条 = 4 次多余的主会话全上下文唤醒)。"""
from __future__ import annotations

import json
import os

import pytest

from autoresearch.scan.l4_watch import acquire_watch_lock, release_watch_lock


def test_second_watcher_refused_while_first_alive(tmp_path):
    acquire_watch_lock(tmp_path)                     # 本进程 = 活着的第一个 watcher
    with pytest.raises(SystemExit):
        acquire_watch_lock(tmp_path)


def test_dead_lock_is_taken_over(tmp_path):
    lock = tmp_path / "outbox" / "l4_watch.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(json.dumps({"pid": 99999999}), encoding="utf-8")   # 不存在的 pid
    path = acquire_watch_lock(tmp_path)              # 残骸 → 接管,不炸
    assert json.loads(path.read_text())["pid"] == os.getpid()


def test_release_removes_lock(tmp_path):
    path = acquire_watch_lock(tmp_path)
    release_watch_lock(path)
    assert not path.exists()
