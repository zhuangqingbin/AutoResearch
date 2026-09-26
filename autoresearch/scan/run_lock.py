"""扫描场互斥锁 —— 无人值守场(`scan.scan_run`)与人工会话不同跑(spec §6 C2)。

macOS 没有 ``flock(1)``(也没有 ``timeout(1)``),锁用 ``fcntl.flock`` 实现,落在
``$CTX/.scan_run.lock``(``context_<engine>/``,引擎隔离)。持锁者 = 整场无人值守扫描的
Python 进程,进程在锁在、进程死锁自动释放(内核回收,不会留下陈旧锁)。锁文件内容是
持锁者自报的 ``{pid, started_at, note}``,只供提示,不参与判定。

人工会话开扫前先问一次(scan-market SKILL 步骤 0):

  uv run --no-sync python -m autoresearch.scan.run_lock check   # 0=空闲;3=被占(打印持锁 pid)
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

LOCK_NAME = ".scan_run.lock"
#: ``check`` / ``scan_run`` 的「锁被占」退出码。
EXIT_HELD = 3


def lock_path() -> Path:
    from autoresearch.common import workspace as ws

    return ws.context_root() / LOCK_NAME


class ScanRunLock:
    """一把已持有的锁;``release()`` 或进程退出即释放。"""

    def __init__(self, path: Path, stream):
        self.path = path
        self._stream = stream

    def release(self) -> None:
        if self._stream is None:
            return
        try:
            fcntl.flock(self._stream.fileno(), fcntl.LOCK_UN)
        finally:
            self._stream.close()
            self._stream = None

    def __enter__(self) -> ScanRunLock:
        return self

    def __exit__(self, *exc) -> None:
        self.release()


def try_acquire(path: Path | str | None = None, *, note: str | None = None) -> ScanRunLock | None:
    """不等待:拿到 → 写持锁者信息并返回锁;被占 → ``None``。"""
    target = Path(path) if path is not None else lock_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    stream = target.open("a+", encoding="utf-8")
    try:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        stream.close()
        return None
    stream.seek(0)
    stream.truncate()
    stream.write(json.dumps({
        "pid": os.getpid(),
        "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "note": note,
    }, ensure_ascii=False))
    stream.flush()
    return ScanRunLock(target, stream)


def holder(path: Path | str | None = None) -> dict | None:
    """锁空闲 → ``None``;被占 → 持锁者自报信息(读不出也返回 ``{"pid": None}``)。"""
    target = Path(path) if path is not None else lock_path()
    if not target.exists():
        return None
    with target.open("r", encoding="utf-8") as stream:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            try:
                value = json.loads(stream.read() or "{}")
            except json.JSONDecodeError:
                value = {}
            return {"pid": None, **value} if isinstance(value, dict) else {"pid": None}
        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
    return None


def describe(info: dict) -> str:
    return (f"扫描锁被占:pid {info.get('pid')}(since {info.get('started_at') or '?'}"
            f"{' · ' + info['note'] if info.get('note') else ''})")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m autoresearch.scan.run_lock",
                                 description="扫描场互斥锁(0=空闲,3=被占)")
    sub = ap.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check", help="锁在不在;被占时打印持锁 pid")
    check.add_argument("--lock", default=None, help="锁文件(缺省 $CTX/.scan_run.lock)")
    args = ap.parse_args(argv)
    info = holder(args.lock)
    if info is None:
        print("[run_lock] free")
        return 0
    print(f"[run_lock] {describe(info)} —— 无人值守扫描在跑,不要再开一场")
    return EXIT_HELD


__all__ = ["EXIT_HELD", "LOCK_NAME", "ScanRunLock", "describe", "holder", "lock_path",
           "main", "try_acquire"]


if __name__ == "__main__":
    sys.exit(main())
