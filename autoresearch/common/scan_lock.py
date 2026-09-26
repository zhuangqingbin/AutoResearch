"""扫描场互斥锁的**读侧**(谁占着、begin 该不该拒)—— 批 4 复审 I3。

锁本身由无人值守场 ``scan.scan_run`` 经 ``scan.run_lock.try_acquire`` 持有(fcntl,
``$CTX/.scan_run.lock``,进程死锁即放)。人工开扫的两个入口 —— ``trace.capsule begin
scan-market``(legacy Workflow)与 ``session_agent begin``(session_v1)—— 都要在代码里问它:
SKILL 里一行 bash 注释拦不住人。读侧下沉到 common,因为 capsule 在 trace 层,而
trace → scan 是分层棘轮正在收紧的向上边(tests/contracts/test_layering.py)。
"""
from __future__ import annotations

import fcntl
import json
from pathlib import Path

LOCK_NAME = ".scan_run.lock"
#: ``run_lock check`` / ``scan_run`` / 被拒的 begin 的「锁被占」退出码。
EXIT_HELD = 3
#: 人工确需与无人值守场并跑时的显式口子(scan_run 自己的 begin 持锁,也走它)。
IGNORE_FLAG = "--ignore-scan-lock"


def lock_path() -> Path:
    from autoresearch.common import workspace as ws

    return ws.context_root() / LOCK_NAME


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


def begin_refusal(kind: str | None, *, ignore: bool = False,
                  path: Path | str | None = None) -> str | None:
    """``scan-market`` 的 begin 遇锁被占 → 一句带持锁 pid 的拒绝;否则 ``None``。"""
    if kind != "scan-market" or ignore:
        return None
    info = holder(path)
    if info is None:
        return None
    return f"{describe(info)} —— 无人值守扫描在跑,拒绝开扫(确需并跑:{IGNORE_FLAG})"


__all__ = ["EXIT_HELD", "IGNORE_FLAG", "LOCK_NAME", "begin_refusal", "describe", "holder",
           "lock_path"]
