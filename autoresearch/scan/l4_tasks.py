#!/usr/bin/env python3
"""L4 单票可恢复任务簿。

这里只拥有调度事实，不拥有选股、评级或研究深度。一次失败只改变本票状态；
只有明确的瞬时错误可重试一次，结构/契约/数据完整性错误直接阻断本票。
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import stat
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from autoresearch.common import workspace as ws
from autoresearch.scan import structural_audit
from autoresearch.trace.capsule import require_active_run
from autoresearch.trace.events import append_event

SCHEMA_VERSION = 1
MAX_ATTEMPTS = 2
TRANSIENT_ERRORS = frozenset({"RATE_LIMIT", "CONNECTION", "TIMEOUT", "STALE_TASK"})
REQUIRED_CAPS = ("tushare", "web_search", "web_fetch", "l4_stock")
# l4_stock 退出「资源」语义,升为派发帽;缺省 64 = 事实无上限(Wave11 C1)。
# tushare/web_search/web_fetch 仍是独立资源帽,但不再参与 effective_cap 的运算 ——
# 它们喂的是 prepare_slim 的操作级信号量(T2),不是这里的批次切片宽度。
DEFAULT_CAPS = {"tushare": 4, "web_search": 4, "web_fetch": 4, "l4_stock": 64}


def _stamp(now: datetime | None = None) -> str:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _parse_stamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _file_signature(info: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _directory_identity(info: os.stat_result) -> tuple[int, int, int]:
    return (info.st_dev, info.st_ino, info.st_mode)


def _secure_artifact_evidence(path: Path) -> tuple[str, str | None]:
    """Hash one stable regular file without following a final symlink."""
    try:
        path_before = path.lstat()
        if stat.S_ISLNK(path_before.st_mode) or not stat.S_ISREG(path_before.st_mode):
            return "UNREADABLE", None
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags)
        try:
            fd_before = os.fstat(fd)
            if (
                not stat.S_ISREG(fd_before.st_mode)
                or (fd_before.st_dev, fd_before.st_ino)
                != (path_before.st_dev, path_before.st_ino)
            ):
                return "UNREADABLE", None
            if fd_before.st_size == 0:
                return "EMPTY", None
            digest = hashlib.sha256()
            while chunk := os.read(fd, 1024 * 1024):
                digest.update(chunk)
            fd_after = os.fstat(fd)
        finally:
            os.close(fd)
        path_after = path.lstat()
        if (
            _file_signature(path_before) != _file_signature(fd_before)
            or _file_signature(fd_before) != _file_signature(fd_after)
            or _file_signature(fd_after) != _file_signature(path_after)
        ):
            return "UNREADABLE", None
        return "PRESENT", digest.hexdigest()
    except FileNotFoundError:
        return "MISSING", None
    except OSError:
        return "UNREADABLE", None


def _open_contained_parent(
    root_fd: int, parts: tuple[str, ...]
) -> tuple[int, tuple[tuple[int, int, int], ...]]:
    current = os.dup(root_fd)
    signatures = []
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        for part in parts:
            next_fd = os.open(part, flags, dir_fd=current)
            os.close(current)
            current = next_fd
            info = os.fstat(current)
            if not stat.S_ISDIR(info.st_mode):
                raise OSError(f"artifact parent component is not a directory: {part}")
            signatures.append(_directory_identity(info))
        return current, tuple(signatures)
    except Exception:
        os.close(current)
        raise


def _secure_contained_artifact_evidence(
    root: Path, relative: Path
) -> tuple[str, str | None]:
    """Hash through trusted dirfds, rejecting symlinks in every path component."""
    parts = relative.parts
    if relative.is_absolute() or not parts or any(part in {"", ".", ".."} for part in parts):
        return "UNREADABLE", None
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    root_fd = parent_fd = file_fd = None
    try:
        root_path_before = root.lstat()
        if stat.S_ISLNK(root_path_before.st_mode) or not stat.S_ISDIR(
            root_path_before.st_mode
        ):
            return "UNREADABLE", None
        root_fd = os.open(root, directory_flags)
        root_before = os.fstat(root_fd)
        if _directory_identity(root_path_before) != _directory_identity(root_before):
            return "UNREADABLE", None
        parent_fd, parent_signatures = _open_contained_parent(root_fd, parts[:-1])
        path_before = os.stat(parts[-1], dir_fd=parent_fd, follow_symlinks=False)
        if stat.S_ISLNK(path_before.st_mode) or not stat.S_ISREG(path_before.st_mode):
            return "UNREADABLE", None
        file_flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        file_fd = os.open(parts[-1], file_flags, dir_fd=parent_fd)
        fd_before = os.fstat(file_fd)
        if (
            not stat.S_ISREG(fd_before.st_mode)
            or (fd_before.st_dev, fd_before.st_ino)
            != (path_before.st_dev, path_before.st_ino)
        ):
            return "UNREADABLE", None
        if fd_before.st_size == 0:
            return "EMPTY", None
        digest = hashlib.sha256()
        while chunk := os.read(file_fd, 1024 * 1024):
            digest.update(chunk)
        fd_after = os.fstat(file_fd)
        path_after = os.stat(parts[-1], dir_fd=parent_fd, follow_symlinks=False)
        rebound_fd, rebound_signatures = _open_contained_parent(root_fd, parts[:-1])
        try:
            rebound = os.stat(parts[-1], dir_fd=rebound_fd, follow_symlinks=False)
        finally:
            os.close(rebound_fd)
        root_path_after = root.lstat()
        if (
            _directory_identity(root_before) != _directory_identity(root_path_after)
            or parent_signatures != rebound_signatures
            or _file_signature(path_before) != _file_signature(fd_before)
            or _file_signature(fd_before) != _file_signature(fd_after)
            or _file_signature(fd_after) != _file_signature(path_after)
            or _file_signature(path_after) != _file_signature(rebound)
        ):
            return "UNREADABLE", None
        return "PRESENT", digest.hexdigest()
    except FileNotFoundError:
        return "MISSING", None
    except OSError:
        return "UNREADABLE", None
    finally:
        for fd in (file_fd, parent_fd, root_fd):
            if fd is not None:
                os.close(fd)


def _sha256(path: Path) -> str:
    status, digest = _secure_artifact_evidence(path)
    if status != "PRESENT" or digest is None:
        raise OSError(f"artifact is not a stable regular file: {path} ({status})")
    return digest


def _artifact(path: Path, *, content_hash: str | None = None) -> dict:
    return {
        "path": str(path),
        "status": "PRESENT" if path.is_file() and path.stat().st_size else "MISSING",
        "content_hash": content_hash,
    }


def _prompt_file_ok(scan_dir: Path, code: str) -> bool:
    """prompt 任务包在且非空 —— C1 硬门的唯一判据(直接 stat 文件,不信账本旧记录)。"""
    code6 = str(code).split(".")[0].zfill(6)
    p = Path(scan_dir) / f"_l4_prompt_{code6}.md"
    status, _ = _secure_artifact_evidence(p)
    return status == "PRESENT"


def _normalize_caps(caps: dict | None) -> dict[str, int]:
    raw = DEFAULT_CAPS if caps is None else caps
    missing = sorted(set(REQUIRED_CAPS) - set(raw))
    extra = sorted(set(raw) - set(REQUIRED_CAPS))
    if missing or extra:
        raise ValueError(f"concurrency caps mismatch:missing={missing},extra={extra}")
    out = {}
    for name in REQUIRED_CAPS:
        value = raw[name]
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"concurrency cap {name} must be a positive integer")
        out[name] = value
    return out


def _atomic_write(path: Path, payload: dict) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.name}.tmp")
    content = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode(
        "utf-8"
    )
    temp.write_bytes(content)
    temp.replace(path)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _lexical_absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _active_artifact_layout(
    path: Path,
    payload: dict,
    code: str,
    *,
    handle=None,
) -> dict[str, tuple[Path, Path]] | None:
    run_id = ws.active_run_id()
    if run_id is None:
        return None
    active = handle or require_active_run(run_id)
    expected_book = active.staging / "_l4_tasks.json"
    if _lexical_absolute(path) != _lexical_absolute(expected_book):
        raise ValueError(f"task book does not belong to active run: {path}")
    date = ws.validate_scan_date(payload.get("date"))
    if date != active.analysis_date:
        raise ValueError("task book date does not match active run")
    task = payload["tasks"][code]
    if task.get("code") != code or task.get("ticker") != _ticker(code):
        raise ValueError(f"invalid active task identity:{code}")
    prefix = Path("staging") / date
    relatives = {
        "prompt": prefix / f"_l4_prompt_{code}.md",
        "slim": prefix / "_external_inputs" / f"{task['ticker']}_{date}_slim.md",
        "card": prefix / "details" / f"{code}.md",
    }
    for name, relative in relatives.items():
        recorded = Path(str((task.get("artifacts") or {}).get(name, {}).get("path") or ""))
        expected = active.workspace / relative
        if not str(recorded) or _lexical_absolute(recorded) != _lexical_absolute(expected):
            raise ValueError(f"artifact path mismatch for {name}: {recorded}")
    return {name: (active.workspace, relative) for name, relative in relatives.items()}


def _task_artifact_evidence(
    task: dict,
    name: str,
    *,
    trusted: dict[str, tuple[Path, Path]] | None = None,
) -> tuple[str, str | None]:
    ref = (task.get("artifacts") or {}).get(name) or {}
    raw_path = str(ref.get("path") or "")
    if not raw_path:
        return "MISSING", None
    if trusted is not None:
        root, relative = trusted[name]
        return _secure_contained_artifact_evidence(root, relative)
    path = Path(raw_path)
    return _secure_artifact_evidence(path)


def _record_task_transition(
    path: Path,
    payload: dict,
    code: str,
    *,
    task_book_hash: str,
    event_type: str,
    old_status: str,
    error_class: str | None,
) -> None:
    """Best-effort append while the task-book lock still owns this exact snapshot."""
    try:
        run_id = ws.active_run_id()
        if run_id is None:
            return
        handle = require_active_run(run_id)
        expected = handle.staging / "_l4_tasks.json"
        if path.resolve() != expected.resolve():
            raise ValueError(
                f"task book does not belong to active run: {path} != {expected}"
            )
        task = payload["tasks"][code]
        trusted = _active_artifact_layout(
            path, payload, code, handle=handle
        )
        attempt = int(task.get("attempt") or 0)
        if attempt < 1:
            raise ValueError(f"authoritative task attempt is not positive: {attempt}")
        evidence: dict[str, object] = {
            "attempt": attempt,
            "error_class": error_class,
            "new_status": str(task.get("status") or ""),
            "old_status": str(old_status),
            "task_book_hash": task_book_hash,
        }
        for name in ("prompt", "slim", "card"):
            status, content_hash = _task_artifact_evidence(
                task, name, trusted=trusted
            )
            evidence[f"{name}_status"] = status
            evidence[f"{name}_hash"] = content_hash
        invocation_id = str(
            os.environ.get("AUTORESEARCH_INVOCATION_ID", "")
        ).strip() or f"l4-task-{code}-attempt-{attempt}"
        stage = str(os.environ.get("AUTORESEARCH_STAGE", "")).strip() or "l4"
        append_event(
            handle.capsule / "events/events.jsonl",
            run_id=handle.run_id,
            engine=handle.engine,
            stage=stage,
            invocation_id=invocation_id,
            attempt=attempt,
            subject=code,
            event_type=event_type,
            payload=evidence,
        )
    except Exception as exc:  # noqa: BLE001 - evidence cannot change task semantics
        print(
            f"[l4_tasks] {event_type} 取证失败:{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )


@contextmanager
def _locked(path: Path) -> Iterator[None]:
    """跨独立 stock workflow 串行化 read-modify-write，避免原子替换仍丢更新。"""
    lock = path.with_name(f"{path.name}.lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def _tushare_slot(scan_dir: Path, k: int, *, poll_seconds: float = 5.0,
                  heartbeat_seconds: int = 60) -> Iterator[int]:
    """K 槽 fcntl 信号量:只限 slim 取数,不限派发。持有者进程死亡 flock 自动释放,
    无需 mtime stale 回收。等待期每 heartbeat_seconds 打一行心跳 —— 08-05 事故的另一半药:
    安静的长等待会被上层(人或壳)误判「卡住」。"""
    sem_dir = scan_dir / "_sem"
    sem_dir.mkdir(parents=True, exist_ok=True)
    waited = 0.0
    while True:
        for slot in range(max(1, int(k))):
            fh = (sem_dir / f"tushare.{slot}.lock").open("a+")
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                fh.close()
                continue
            try:
                yield slot
                return
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)
                fh.close()
        time.sleep(poll_seconds)
        waited += poll_seconds
        if waited % heartbeat_seconds < poll_seconds:
            print(f"[prepare_slim] 等 tushare 槽 {int(waited)}s(K={k})…", flush=True)


def _read_active_task_book(book_path: Path) -> bytes:
    run_id = ws.active_run_id()
    if run_id is None:
        raise ValueError("active task-book read requires AUTORESEARCH_RUN_ID")
    handle = require_active_run(run_id)
    expected = handle.staging / "_l4_tasks.json"
    if _lexical_absolute(book_path) != _lexical_absolute(expected):
        raise ValueError(f"task book is outside active staging: {book_path}")
    relative = expected.relative_to(handle.workspace)
    root_fd = parent_fd = fd = None
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        root_path_before = handle.workspace.lstat()
        root_fd = os.open(handle.workspace, directory_flags)
        root_before = os.fstat(root_fd)
        if _directory_identity(root_path_before) != _directory_identity(root_before):
            raise ValueError("active task-book root changed before secure read")
        parent_fd, parent_before = _open_contained_parent(
            root_fd, relative.parts[:-1]
        )
        before_path = os.stat(
            relative.parts[-1], dir_fd=parent_fd, follow_symlinks=False
        )
        if stat.S_ISLNK(before_path.st_mode) or not stat.S_ISREG(before_path.st_mode):
            raise ValueError("active task book must be a non-symlink regular file")
        flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        fd = os.open(relative.parts[-1], flags, dir_fd=parent_fd)
        before_fd = os.fstat(fd)
        if (
            not stat.S_ISREG(before_fd.st_mode)
            or _file_signature(before_path) != _file_signature(before_fd)
        ):
            raise ValueError("task book changed before secure read")
        chunks = []
        while chunk := os.read(fd, 1024 * 1024):
            chunks.append(chunk)
        after_fd = os.fstat(fd)
        after_path = os.stat(
            relative.parts[-1], dir_fd=parent_fd, follow_symlinks=False
        )
        rebound_fd, parent_after = _open_contained_parent(
            root_fd, relative.parts[:-1]
        )
        try:
            rebound = os.stat(
                relative.parts[-1], dir_fd=rebound_fd, follow_symlinks=False
            )
        finally:
            os.close(rebound_fd)
        root_path_after = handle.workspace.lstat()
    finally:
        for open_fd in (fd, parent_fd, root_fd):
            if open_fd is not None:
                os.close(open_fd)
    if (
        _directory_identity(root_before) != _directory_identity(root_path_after)
        or parent_before != parent_after
        or _file_signature(before_fd) != _file_signature(after_fd)
        or _file_signature(after_fd) != _file_signature(after_path)
        or _file_signature(after_path) != _file_signature(rebound)
    ):
        raise ValueError("task book changed while reading")
    return b"".join(chunks)


def _read(path: Path | str) -> tuple[Path, dict]:
    book_path = Path(path)
    if ws.active_run_id() is None:
        text = book_path.read_text(encoding="utf-8")
    else:
        text = _read_active_task_book(book_path).decode("utf-8")
    payload = json.loads(text)
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"unsupported L4 task book schema:{payload.get('schema_version')}")
    if not isinstance(payload.get("tasks"), dict):
        raise ValueError("L4 task book tasks must be an object")
    return book_path, payload


def _ticker(code: str) -> str:
    from autoresearch.dataflows.symbol_utils import normalize_symbol

    return normalize_symbol(code)


def _new_task(
    code: str,
    *,
    date: str,
    scan_dir: Path,
    context_root: Path,
    meta: dict,
    now: datetime | None,
) -> dict:
    code6 = str(code).split(".")[0].zfill(6)
    ticker = str(meta.get("ticker") or _ticker(code6))
    return {
        "code": code6,
        "ticker": ticker,
        "pinned": bool(meta.get("pinned")),
        "status": "PENDING",
        "attempt": 0,
        "slim_attempts": 0,
        "last_error_class": None,
        "last_error": None,
        "started_at": None,
        "updated_at": _stamp(now),
        "artifacts": {
            "prompt": _artifact(scan_dir / f"_l4_prompt_{code6}.md"),
            "slim": _artifact(context_root / f"{ticker}_{date}_slim.md"),
            "card": _artifact(scan_dir / "details" / f"{code6}.md"),
        },
    }


def initialize(
    date: str,
    codes: list[str],
    *,
    root: Path | str | None = None,
    context_root: Path | str | None = None,
    meta: dict[str, dict] | None = None,
    caps: dict | None = None,
    now: datetime | None = None,
) -> dict:
    """初始化或合并 `_l4_tasks.json`；既有单票状态不被其它票重置。"""
    date = ws.validate_scan_date(date)
    base = Path(root) if root is not None else ws.scan_root()
    scan_dir = base / date
    ctx = (Path(context_root) if context_root is not None
           else ws.scan_input_dir(date, scan_dir=scan_dir))
    path = scan_dir / "_l4_tasks.json"
    cap_values = _normalize_caps(caps)
    ordered = list(dict.fromkeys(str(code).split(".")[0].zfill(6) for code in codes))
    # C1a(design 2026-08-10):prompts 是每张卡的任务包 —— 缺着派发 = 整轮盲跑
    # (2026-08-09 实跑 12 股 ≈$23 全废)。init 是派发前最后一个确定性闸口,在这里拒绝,
    # 一个 LLM token 都还没花。prompts 幂等(实测 3.5s),修复 = 重跑 prompts 再 init。
    missing_prompts = [c for c in ordered if not _prompt_file_ok(scan_dir, c)]
    if missing_prompts:
        return {
            "ok": False,
            "path": str(path),
            "n": len(ordered),
            "codes": ordered,
            "reason": (f"prompts 缺失 {len(missing_prompts)} 票 —— 拒绝初始化任务簿;"
                       f"先跑 `python -m autoresearch.scan.agents.l4_card prompts {date}`"),
            "missing_prompts": missing_prompts,
            "effective_cap": 0,
            "dispatch_batches": [],
        }
    with _locked(path):
        if path.exists():
            _, payload = _read(path)
            tasks = payload["tasks"]
        else:
            tasks = {}
            payload = {
                "schema_version": SCHEMA_VERSION,
                "date": date,
                "created_at": _stamp(now),
                "rate_limit_failures": 0,
                "tasks": tasks,
                # B5 判据的落脚点。**只在新建账本时写**：既有账本若无此键，说明它出生在
                # 计量上线前，那天的活体事件早已丢失 —— 此时补建等于给它伪造「已计量」
                # 身份，而 structural_audit 正是靠这个键把「没看」和「没有」分开。
                structural_audit.EVENTS_KEY: structural_audit.new_log(),
            }
        metadata = meta or {}
        for code in ordered:
            if code not in tasks:
                tasks[code] = _new_task(
                    code,
                    date=date,
                    scan_dir=scan_dir,
                    context_root=ctx,
                    meta=metadata.get(code) or {},
                    now=now,
                )
        # 本次 dispatch 顺序是稳定批次的事实源；旧日残留任务不进入本次 order。
        payload["order"] = ordered
        payload["caps"] = cap_values
        # 派发帽=caps.l4_stock;不再 min 四帽、不再被 rate_limit_failures 收窄(Wave11 C1)。
        payload["effective_cap"] = max(1, int(cap_values["l4_stock"]))
        payload["updated_at"] = _stamp(now)
        _atomic_write(path, payload)
    return {
        "ok": True,
        "path": str(path),
        "n": len(ordered),
        "codes": ordered,
        "effective_cap": payload["effective_cap"],
    }


def _verified(
    task: dict,
    *,
    trusted: dict[str, tuple[Path, Path]] | None = None,
) -> bool:
    for name in ("prompt", "slim", "card"):
        ref = task.get("artifacts", {}).get(name) or {}
        content_hash = ref.get("content_hash")
        status, actual_hash = _task_artifact_evidence(
            task, name, trusted=trusted
        )
        if not content_hash or status != "PRESENT":
            return False
        if actual_hash != content_hash:
            return False
    return True


def preflight(
    book: Path | str,
    code: str,
    *,
    expected_attempt: int | None = None,
    now: datetime | None = None,
    stale_after_seconds: int = 3600,
) -> dict:
    """为一票领取一次执行权；SUCCEEDED 只在三件产物指纹仍匹配时可复用。"""
    if expected_attempt is not None and (
        type(expected_attempt) is not int or expected_attempt < 1
    ):
        raise ValueError("expected_attempt must be a positive integer")
    path = Path(book)
    code6 = str(code).split(".")[0].zfill(6)
    # C1b(design 2026-08-10):prompt 任务包是出卡的前提 —— 缺着认领 = 盲卡。
    # 无论有无任务簿都在这里拦(SENTINEL_PINNED 路不建账本,这是它唯一的每股闸口)。
    # 不认领、不写盘:BLOCK 是「别跑」,不是一次失败。
    scan_dir = path.parent
    if not _prompt_file_ok(scan_dir, code6):
        return {"ok": True, "code": code6, "action": "BLOCKED",
                "attempt": 0, "reason": "PROMPT_MISSING"}

    def _intel_resume() -> bool:
        # 续传是省钱件不是正确性件:判定失败一律不续传(失败闭合)。但**降级必须留痕** ——
        # 项目铁律「降级不留痕才是真病」:裸 except Exception 会把 resumable() 里的真 bug
        # (打错字/schema 变更)吞成"今天恰好没得续传",省不到钱还没人知道。故只吞 IO/解析类,
        # 且吞之前先喊一声。
        from autoresearch.scan.l4.intel_status import mark_resumed, resumable
        try:
            if not resumable(scan_dir, code6):
                return False
            mark_resumed(scan_dir, code6)   # 披露先于消费;幂等
            return True
        except (OSError, ValueError, TypeError, KeyError) as exc:
            print(f"[l4_tasks] intel 续传判定失败({type(exc).__name__}: {exc})→ 本票照常盲搜",
                  file=sys.stderr)
            return False

    # bookless(直接单独重跑单股 workflow / SENTINEL_PINNED):python 接管原壳命令里的
    # `else echo LEGACY` 分支(壳零判断铁律)——行为与旧壳逐字节等价。
    if not path.exists():
        return {"ok": True, "code": code6, "action": "LEGACY",
                "attempt": 0, "reason": "NO_TASK_BOOK", "intel_resume": _intel_resume()}
    stamp = _stamp(now)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    reason = None
    with _locked(path):
        _, payload = _read(path)
        if code6 not in payload["tasks"]:
            raise KeyError(f"unknown L4 task:{code6}")
        task = payload["tasks"][code6]
        trusted = _active_artifact_layout(path, payload, code6)
        old_status = str(task["status"])
        status = task["status"]
        if status == "SUCCEEDED":
            if _verified(task, trusted=trusted):
                # 守卫拦住了对已成功票的重派 —— 断点续跑的正常现象，记作观察量不进失败数。
                # 真正的失败是这里被绕过、成功票又跑一遍（TERMINAL_RERUN，见 structural_audit）。
                structural_audit.record(
                    payload, code6, structural_audit.TERMINAL_REDISPATCH_BLOCKED,
                    f"attempt={task['attempt']}", now=now)
                _atomic_write(path, payload)
                return {
                    "ok": True,
                    "code": code6,
                    "action": "SKIP",
                    "attempt": task["attempt"],
                    "reason": "VERIFIED_SUCCESS",
                }
            status = "FAILED"
            task["status"] = status
            task["last_error_class"] = "ARTIFACT_CHANGED"
            task["last_error"] = "successful artifact hash no longer matches"
            reason = "ARTIFACT_CHANGED"
            structural_audit.record(
                payload, code6, structural_audit.ARTIFACT_HASH_MISMATCH,
                "SUCCEEDED 票的产物指纹已变，降级重跑", now=now)
        if status == "RUNNING":
            started = _parse_stamp(task.get("started_at"))
            age = (current - started).total_seconds() if started else float("inf")
            if age < stale_after_seconds:
                return {
                    "ok": True,
                    "code": code6,
                    "action": "WAIT",
                    "attempt": task["attempt"],
                    "reason": "ALREADY_RUNNING",
                }
            stale_attempt = int(task.get("attempt") or 0)
            if stale_attempt < MAX_ATTEMPTS:
                stale_next_attempt = stale_attempt + 1
                if (
                    expected_attempt is not None
                    and expected_attempt != stale_next_attempt
                ):
                    raise ValueError(
                        f"expected attempt {expected_attempt} does not match "
                        f"next attempt {stale_next_attempt}"
                    )
            exhausted_stale = stale_attempt >= MAX_ATTEMPTS
            status = "BLOCKED" if exhausted_stale else "FAILED"
            task["status"] = status
            task["last_error_class"] = "STALE_TASK"
            task["last_error"] = f"running for {int(age)}s"
            task["updated_at"] = stamp
            reason = "STALE_TASK"
            # 一次执行悄悄蒸发了：既没 mark_success 也没 mark_failure，账本一直以为它在跑。
            structural_audit.record(
                payload, code6, structural_audit.COMPLETION_MISJUDGED,
                f"RUNNING {int(age)}s 无终态回写", now=now)
            book_hash = _atomic_write(path, payload)
            _record_task_transition(
                path,
                payload,
                code6,
                task_book_hash=book_hash,
                event_type=(
                    "TASK_RETRY_SCHEDULED"
                    if not exhausted_stale
                    else "TASK_FAILED"
                ),
                old_status="RUNNING",
                error_class="STALE_TASK",
            )
            if exhausted_stale:
                return {
                    "ok": True,
                    "code": code6,
                    "action": "BLOCKED",
                    "attempt": task["attempt"],
                    "reason": "STALE_TASK",
                }
            old_status = "FAILED"
        if status == "BLOCKED":
            return {
                "ok": True,
                "code": code6,
                "action": "BLOCKED",
                "attempt": task["attempt"],
                "reason": task.get("last_error_class"),
            }
        if status == "FAILED":
            error_class = str(task.get("last_error_class") or "")
            if error_class not in TRANSIENT_ERRORS or int(task["attempt"]) >= MAX_ATTEMPTS:
                task["status"] = "BLOCKED"
                task["updated_at"] = stamp
                book_hash = _atomic_write(path, payload)
                terminal_event = (
                    "TASK_FAILED"
                    if error_class in TRANSIENT_ERRORS
                    and int(task["attempt"]) >= MAX_ATTEMPTS
                    else "TASK_BLOCKED"
                )
                already_recorded_exhaustion = (
                    old_status == "FAILED"
                    and error_class in TRANSIENT_ERRORS
                    and int(task["attempt"]) >= MAX_ATTEMPTS
                )
                if not already_recorded_exhaustion:
                    _record_task_transition(
                        path,
                        payload,
                        code6,
                        task_book_hash=book_hash,
                        event_type=terminal_event,
                        old_status=old_status,
                        error_class=error_class or "NON_TRANSIENT_FAILURE",
                    )
                return {
                    "ok": True,
                    "code": code6,
                    "action": "BLOCKED",
                    "attempt": task["attempt"],
                    "reason": error_class or "NON_TRANSIENT_FAILURE",
                }
            reason = reason or error_class
        next_attempt = int(task.get("attempt") or 0) + 1
        if expected_attempt is not None and expected_attempt != next_attempt:
            raise ValueError(
                f"expected attempt {expected_attempt} does not match next attempt {next_attempt}"
            )
        task["attempt"] = next_attempt
        task["status"] = "RUNNING"
        task["started_at"] = stamp
        task["updated_at"] = stamp
        book_hash = _atomic_write(path, payload)
        _record_task_transition(
            path,
            payload,
            code6,
            task_book_hash=book_hash,
            event_type="TASK_CLAIMED",
            old_status=old_status,
            error_class=(str(task.get("last_error_class") or "") or None),
        )
        return {
            "ok": True,
            "code": code6,
            "action": "RUN",
            "attempt": task["attempt"],
            "reason": reason or "PENDING",
            "intel_resume": _intel_resume(),
        }


def mark_failure(
    book: Path | str,
    code: str,
    error_class: str,
    *,
    error: str | None = None,
    expected_attempt: int | None = None,
    now: datetime | None = None,
) -> dict:
    active_run = ws.active_run_id()
    if active_run is not None and expected_attempt is None:
        raise ValueError("expected_attempt is required for an active run terminal")
    if expected_attempt is not None and (
        type(expected_attempt) is not int or expected_attempt < 1
    ):
        raise ValueError("expected_attempt must be a positive integer")
    path = Path(book)
    code6 = str(code).split(".")[0].zfill(6)
    kind = str(error_class).strip().upper()
    with _locked(path):
        _, payload = _read(path)
        task = payload["tasks"][code6]
        _active_artifact_layout(path, payload, code6)
        old_status = str(task["status"])
        authoritative_attempt = int(task.get("attempt") or 0)
        if expected_attempt is not None and expected_attempt != authoritative_attempt:
            raise ValueError(
                f"expected attempt {expected_attempt} does not match "
                f"authoritative attempt {authoritative_attempt}"
            )
        terminal_status = (
            "FAILED"
            if kind in TRANSIENT_ERRORS and authoritative_attempt < MAX_ATTEMPTS
            else "BLOCKED"
        )
        terminal_error = error or kind
        if expected_attempt is not None and old_status != "RUNNING":
            if (
                task.get("last_error_class") == kind
                and task.get("last_error") == terminal_error
                and old_status == terminal_status
            ):
                return {
                    "ok": True,
                    "code": code6,
                    "status": terminal_status,
                    "attempt": authoritative_attempt,
                    "error_class": kind,
                    "idempotent": True,
                }
            raise ValueError(
                f"contradictory terminal for attempt {authoritative_attempt}: "
                f"authoritative status {old_status}"
            )
        task["attempt"] = max(1, int(task.get("attempt") or 0))
        task["last_error_class"] = kind
        task["last_error"] = terminal_error
        task["status"] = terminal_status
        task["updated_at"] = _stamp(now)
        if kind == "RATE_LIMIT":
            payload["rate_limit_failures"] = int(
                payload.get("rate_limit_failures") or 0
            ) + 1
        book_hash = _atomic_write(path, payload)
        if kind not in TRANSIENT_ERRORS:
            event_type = "TASK_BLOCKED"
        elif int(task["attempt"]) >= MAX_ATTEMPTS:
            event_type = "TASK_FAILED"
        else:
            event_type = "TASK_RETRY_SCHEDULED"
        _record_task_transition(
            path,
            payload,
            code6,
            task_book_hash=book_hash,
            event_type=event_type,
            old_status=old_status,
            error_class=kind,
        )
    return {
        "ok": True,
        "code": code6,
        "status": task["status"],
        "attempt": task["attempt"],
        "error_class": kind,
    }


def mark_success(
    book: Path | str,
    code: str,
    *,
    expected_attempt: int | None = None,
    now: datetime | None = None,
) -> dict:
    active_run = ws.active_run_id()
    if active_run is not None and expected_attempt is None:
        raise ValueError("expected_attempt is required for an active run terminal")
    if expected_attempt is not None and (
        type(expected_attempt) is not int or expected_attempt < 1
    ):
        raise ValueError("expected_attempt must be a positive integer")
    path = Path(book)
    code6 = str(code).split(".")[0].zfill(6)
    with _locked(path):
        _, payload = _read(path)
        task = payload["tasks"][code6]
        trusted = _active_artifact_layout(path, payload, code6)
        old_status = str(task["status"])
        authoritative_attempt = int(task.get("attempt") or 0)
        if expected_attempt is not None and expected_attempt != authoritative_attempt:
            raise ValueError(
                f"expected attempt {expected_attempt} does not match "
                f"authoritative attempt {authoritative_attempt}"
            )
        if expected_attempt is not None and old_status != "RUNNING":
            if old_status == "SUCCEEDED":
                return {
                    "ok": True,
                    "code": code6,
                    "status": "SUCCEEDED",
                    "attempt": authoritative_attempt,
                    "idempotent": True,
                }
            raise ValueError(
                f"contradictory terminal for attempt {authoritative_attempt}: "
                f"authoritative status {old_status}"
            )
        refs = task["artifacts"]
        missing = []
        for name in ("prompt", "slim", "card"):
            artifact_path = Path(refs[name]["path"])
            status, content_hash = _task_artifact_evidence(
                task, name, trusted=trusted
            )
            if status != "PRESENT" or content_hash is None:
                missing.append(name)
                continue
            refs[name] = {
                "path": str(artifact_path),
                "status": "PRESENT",
                "content_hash": content_hash,
            }
        # 调用方以为跑成了、账本查出没跑成 —— 这就是「完成态误判」。原先只抛异常，
        # 异常一被上层吞掉这件事就再无痕迹，B5 的判据也就永远查不到它。
        if missing:
            structural_audit.record(
                payload, code6, structural_audit.COMPLETION_MISJUDGED,
                f"mark_success 缺产物:{','.join(missing)}", now=now)
            _atomic_write(path, payload)
            raise ValueError(f"L4 success missing artifacts:{','.join(missing)}")
        from autoresearch.scan.l4.producers import _slim_defect

        _, defect = _slim_defect(Path(refs["slim"]["path"]), 4096)
        if not defect:
            for name in ("prompt", "slim", "card"):
                status, content_hash = _task_artifact_evidence(
                    task, name, trusted=trusted
                )
                if status != "PRESENT" or content_hash != refs[name]["content_hash"]:
                    defect = f"{name} changed while validating"
                    break
        if defect:
            structural_audit.record(
                payload, code6, structural_audit.COMPLETION_MISJUDGED,
                f"mark_success slim 不合格:{defect}", now=now)
            _atomic_write(path, payload)
            raise ValueError(f"L4 success invalid slim:{defect}")
        task["status"] = "SUCCEEDED"
        task["last_error_class"] = None
        task["last_error"] = None
        task["updated_at"] = _stamp(now)
        book_hash = _atomic_write(path, payload)
        _record_task_transition(
            path,
            payload,
            code6,
            task_book_hash=book_hash,
            event_type="TASK_SUCCEEDED",
            old_status=old_status,
            error_class=None,
        )
    return {
        "ok": True,
        "code": code6,
        "status": "SUCCEEDED",
        "attempt": task["attempt"],
    }


def reconcile(book: Path | str, *, now: datetime | None = None) -> dict:
    """收尾自愈(E1b):卡已在盘而 book 卡在 RUNNING 的票,按盘上事实补记 SUCCEEDED。

    只处理 `status == "RUNNING"` 的票(设计稿 `2026-08-18-e6-activation-learning-
    slimdown-design.md` §3 E1b 原文:「对 status=RUNNING 的票…补记 SUCCEEDED」)——
    **不碰 FAILED/BLOCKED/PENDING**,尤其不碰 FAILED。FAILED 是一次已经给出理由的
    显式判断(`last_error_class`/`last_error` 记着为什么失败,含 `preflight` 从
    SUCCEEDED 降级来的 `ARTIFACT_CHANGED`、从 RUNNING 降级来的 `STALE_TASK`,以及
    `mark_failure` 直接判的瞬时错误);仅凭"产物碰巧还在盘上"就把它翻回 SUCCEEDED,
    等于用文件推翻一个已经有理由的失败判断,会放一张不该放的票进买入候选——
    contract 门拦它本来就是拦对的。08-12 事故的真实形态是**卡死在 RUNNING**
    (workflow 认领后崩溃,`mark_success` 从未执行,任务簿没能力知道执行其实已经
    完成),收窄到只处理 RUNNING 不影响目标场景。

    只认盘上产物:prompt/slim/card 三件齐 + slim 合格才补记(content_hash 现算,
    绝不编造);缺产物的票原样保留 —— contract 门拦它拦得对。补记行打
    `recovered=True`,账目可辨。SUCCEEDED/FAILED/BLOCKED/PENDING 均直接跳过(幂等)。
    立案:2026-08-12 九票卡全在盘、book 全 RUNNING → E6 contract 门团灭(spec §2.1)。
    复核修复轮 1(2026-08-19):原实现「非 SUCCEEDED 皆自愈」范围过宽(会把显式
    FAILED/BLOCKED 的票也翻成 SUCCEEDED),按设计稿收窄到仅 RUNNING。
    """
    from autoresearch.scan.l4.producers import _slim_defect

    path = Path(book)
    recovered: list[str] = []
    recovered_from: dict[str, str] = {}
    skipped: list[dict] = []
    with _locked(path):
        _, payload = _read(path)
        for code6 in sorted(payload["tasks"]):
            task = payload["tasks"][code6]
            if task.get("status") != "RUNNING":
                continue
            trusted = _active_artifact_layout(path, payload, code6)
            refs = task.get("artifacts") or {}
            missing = []
            content_hashes = {}
            for name in ("prompt", "slim", "card"):
                status, content_hash = _task_artifact_evidence(
                    task, name, trusted=trusted
                )
                if status != "PRESENT" or content_hash is None:
                    missing.append(name)
                else:
                    content_hashes[name] = content_hash
            if not missing:
                _, defect = _slim_defect(Path(refs["slim"]["path"]), 4096)
                if defect:
                    missing = [f"slim:{defect}"]
            if not missing:
                for name in ("prompt", "slim", "card"):
                    status, content_hash = _task_artifact_evidence(
                        task, name, trusted=trusted
                    )
                    if status != "PRESENT" or content_hash != content_hashes[name]:
                        missing = [name]
                        break
            if missing:
                skipped.append({"code": code6, "missing": missing})
                continue
            for name in ("prompt", "slim", "card"):
                p = Path(refs[name]["path"])
                refs[name] = {
                    "path": str(p),
                    "status": "PRESENT",
                    "content_hash": content_hashes[name],
                }
            task["status"] = "SUCCEEDED"
            task["recovered"] = True
            task["last_error_class"] = None
            task["last_error"] = None
            task["updated_at"] = _stamp(now)
            recovered.append(code6)
            recovered_from[code6] = "RUNNING"
        if recovered:
            book_hash = _atomic_write(path, payload)
            for code6 in recovered:
                _record_task_transition(
                    path,
                    payload,
                    code6,
                    task_book_hash=book_hash,
                    event_type="TASK_SUCCEEDED",
                    old_status=recovered_from[code6],
                    error_class=None,
                )
    return {"ok": True, "recovered": recovered, "skipped": skipped}


def prepare_slim(
    book: Path | str,
    code: str,
    *,
    harvest_fn: Callable[[str, str], Path] | None = None,
    retries: int = 1,
    min_bytes: int = 4096,
    now: datetime | None = None,
) -> dict:
    """仅准备一票 slim；已有合格文件零网络，失败最多轻量重拉一次。"""
    path, payload = _read(book)
    code6 = str(code).split(".")[0].zfill(6)
    task = payload["tasks"][code6]
    ticker = task["ticker"]
    slim_path = Path(task["artifacts"]["slim"]["path"])
    slim_path.parent.mkdir(parents=True, exist_ok=True)
    from autoresearch.scan.l4.producers import _default_harvest_slim, _slim_defect

    harvest = harvest_fn or (
        lambda symbol, date: _default_harvest_slim(symbol, date, slim_path.parent)
    )
    size, defect = _slim_defect(slim_path, min_bytes)
    attempts = 0
    sem_wait = 0.0
    if defect:
        try:
            k = max(1, int((payload.get("caps") or DEFAULT_CAPS)["tushare"])
                    - int(payload.get("rate_limit_failures") or 0))
            _t0 = time.monotonic()
            with _tushare_slot(path.parent, k):
                sem_wait = time.monotonic() - _t0
                for _ in range(max(0, retries) + 1):
                    attempts += 1
                    try:
                        produced = Path(harvest(ticker, payload["date"]))
                        size, defect = _slim_defect(produced, min_bytes)
                        slim_path = produced
                    except Exception as exc:  # noqa: BLE001 — 转为单票失败事实
                        size, defect = 0, f"harvest 异常:{exc}"
                    if defect is None:
                        break
        except Exception as exc:  # noqa: BLE001 — k 计算/信号量获取结构性失败,
            # 必须转为 defect 落记账块,不能让异常从这里冒出去——那样任务簿永远到不了
            # 下面的 with _locked(path),该票会卡 RUNNING 到 stale_after_seconds 超时才被
            # 拉回,期间零失败痕迹(review Important-2:与本 task 要治的「静默卡住」同类病)。
            size, defect = 0, f"tushare 信号量获取异常:{exc!r}"
    with _locked(path):
        _, latest = _read(path)
        current = latest["tasks"][code6]
        current["slim_attempts"] = int(current.get("slim_attempts") or 0) + attempts
        current["artifacts"]["slim"] = _artifact(
            slim_path,
            content_hash=_sha256(slim_path) if defect is None else None,
        )
        current["updated_at"] = _stamp(now)
        current["sem_wait_s"] = round(sem_wait, 1)
        if defect:
            current["last_error_class"] = "DATA_INTEGRITY"
            current["last_error"] = defect
        _atomic_write(path, latest)
    return {
        "ok": defect is None,
        "code": code6,
        "ticker": ticker,
        "bytes": int(size),
        "attempts": attempts,
        "reason": defect or "ok",
        "sem_wait_s": round(sem_wait, 1),
    }


def _age_minutes(started_at: str | None, now: datetime | None = None) -> int | None:
    """在飞时长(分钟);缺 started_at(旧账本)返回 None —— 缺字段不等于不在飞。"""
    started = _parse_stamp(started_at)
    if started is None:
        return None
    ref = now or datetime.now(timezone.utc)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)
    return max(0, int((ref - started).total_seconds() // 60))


def dispatch_batches(
    book: Path | str,
    *,
    caps: dict | None = None,
    now: datetime | None = None,
) -> dict:
    """派发帽=caps.l4_stock(一次全派);tushare/web 资源帽由 prepare_slim 的操作级信号量
    执行(T2),限频事件收窄的是取数槽不是派发。"""
    path = Path(book)
    with _locked(path):
        _, payload = _read(path)
        cap_values = _normalize_caps(caps if caps is not None else payload.get("caps"))
        effective = max(1, int(cap_values["l4_stock"]))
        codes = []
        running: list[dict] = []
        for code in payload.get("order") or payload["tasks"]:
            task = payload["tasks"].get(code)
            if not task:
                continue
            # W8-6:在飞的票必须可见。`batches` 为空有两种截然相反的含义 ——
            # 「都跑完了」还是「都还在飞」;07-28 主会话把后者读成前者,提前派了下一批。
            # 完成判据是 task_book 全 SUCCEEDED,不是 batches 为空。
            if task["status"] == "RUNNING":
                running.append({"code": code, "age_min": _age_minutes(task.get("started_at"), now)})
                continue
            if task["status"] in {"PENDING", "FAILED"}:
                if (
                    task["status"] == "FAILED"
                    and task.get("last_error_class") not in TRANSIENT_ERRORS
                ):
                    continue
                if int(task.get("attempt") or 0) >= MAX_ATTEMPTS:
                    continue
                codes.append(code)
        batches = [
            codes[index:index + effective]
            for index in range(0, len(codes), effective)
        ]
        payload["caps"] = cap_values
        payload["effective_cap"] = effective
        payload["updated_at"] = _stamp(now)
        _atomic_write(path, payload)
    return {
        "ok": True,
        "caps": cap_values,
        "effective_cap": effective,
        "batches": batches,
        "pending": len(codes),
        "running": running,   # 在飞票(code + age_min);空 batches ∧ 空 running 才是完成态
    }


def stats(book: Path | str) -> dict:
    """纯读账本:全并发首跑后一眼看限频/排队(Wave11 C4)。

    聚合 `last_error_class` 计数、`sem_wait_s` 峰值/均值、`slim_attempts` 总数。
    只读不写 —— 不落 `.tmp`、不改任务簿一个字节。老账本没有 `sem_wait_s`/
    `last_error_class` 是正常现象(两者都晚于任务簿本身上线);缺失按「无该项」
    处理,均值只对记录过该值的票取平均 —— 0.0(湖命中零等待)是记录,缺字段不是,
    两者不可互相顶替(否则均值会被不存在的"零等待"稀释)。
    """
    _, payload = _read(book)
    tasks = payload["tasks"]
    error_classes: dict[str, int] = {}
    sem_waits: list[float] = []
    slim_attempts_total = 0
    for task in tasks.values():
        error_class = task.get("last_error_class")
        if error_class:
            error_classes[error_class] = error_classes.get(error_class, 0) + 1
        sem_wait = task.get("sem_wait_s")
        if sem_wait is not None:
            sem_waits.append(float(sem_wait))
        slim_attempts_total += int(task.get("slim_attempts") or 0)
    return {
        "ok": True,
        "n_tasks": len(tasks),
        "error_classes": error_classes,
        "sem_wait_max_s": max(sem_waits) if sem_waits else None,
        "sem_wait_mean_s": (sum(sem_waits) / len(sem_waits)) if sem_waits else None,
        "slim_attempts_total": slim_attempts_total,
    }


def _book_path(date: str, root: str | None) -> Path:
    date = ws.validate_scan_date(date)
    return (Path(root) if root else ws.scan_root()) / date / "_l4_tasks.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="l4_tasks")
    parser.add_argument(
        "cmd",
        choices=["init", "preflight", "prepare", "success", "failure", "batches", "stats",
                 "reconcile"],
    )
    parser.add_argument("first", help="init/batches/stats/reconcile:DATE；其余:CODE")
    parser.add_argument("second", nargs="?", help="preflight/prepare/success/failure:DATE")
    parser.add_argument("--root", default=None)
    parser.add_argument("--error-class", default=None)
    parser.add_argument("--error", default=None)
    parser.add_argument("--caps-json", default=None)
    parser.add_argument("--expected-attempt", type=int, default=None)
    args = parser.parse_args(argv)
    if args.cmd in {"init", "batches", "stats", "reconcile"}:
        args.first = ws.validate_scan_date(args.first)
    elif args.second:
        args.second = ws.validate_scan_date(args.second)
    caps = json.loads(args.caps_json) if args.caps_json else None
    if args.cmd == "init":
        from autoresearch.scan.l4.dispatch import dispatch_plan

        if caps is None:
            from autoresearch.scan.user_config import load_user_config

            caps = (
                (load_user_config().get("budgets") or {}).get("concurrency")
                or None
            )
        plan = dispatch_plan(args.first, root=args.root)
        result = initialize(
            args.first,
            plan["dispatch"],
            root=args.root,
            meta=plan.get("meta") or {},
            caps=caps,
        )
        if result.get("ok"):
            result["dispatch_batches"] = dispatch_batches(result["path"])["batches"]
    elif args.cmd == "batches":
        result = dispatch_batches(_book_path(args.first, args.root), caps=caps)
    elif args.cmd == "stats":
        result = stats(_book_path(args.first, args.root))
    elif args.cmd == "reconcile":
        result = reconcile(_book_path(args.first, args.root))
    else:
        if not args.second:
            parser.error(f"{args.cmd} requires CODE DATE")
        book = _book_path(args.second, args.root)
        if args.cmd == "preflight":
            if args.expected_attempt is None:
                parser.error("preflight requires --expected-attempt")
            result = preflight(book, args.first, expected_attempt=args.expected_attempt)
        elif args.cmd == "prepare":
            result = prepare_slim(book, args.first)
        elif args.cmd == "success":
            result = mark_success(
                book, args.first, expected_attempt=args.expected_attempt
            )
        else:
            if not args.error_class:
                parser.error("failure requires --error-class")
            result = mark_failure(
                book,
                args.first,
                args.error_class,
                error=args.error,
                expected_attempt=args.expected_attempt,
            )
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
