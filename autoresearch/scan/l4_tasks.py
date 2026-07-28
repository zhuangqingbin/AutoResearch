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
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator

SCHEMA_VERSION = 1
MAX_ATTEMPTS = 2
TRANSIENT_ERRORS = frozenset({"RATE_LIMIT", "CONNECTION", "TIMEOUT", "STALE_TASK"})
REQUIRED_CAPS = ("tushare", "web_search", "web_fetch", "l4_stock")
DEFAULT_CAPS = {name: 4 for name in REQUIRED_CAPS}


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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact(path: Path, *, content_hash: str | None = None) -> dict:
    return {
        "path": str(path),
        "status": "PRESENT" if path.is_file() and path.stat().st_size else "MISSING",
        "content_hash": content_hash,
    }


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


def _atomic_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.name}.tmp")
    temp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temp.replace(path)


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


def _read(path: Path | str) -> tuple[Path, dict]:
    book_path = Path(path)
    payload = json.loads(book_path.read_text(encoding="utf-8"))
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
    base = Path(root) if root is not None else Path("context/scan")
    scan_dir = base / date
    ctx = Path(context_root) if context_root is not None else Path("context")
    path = scan_dir / "_l4_tasks.json"
    cap_values = _normalize_caps(caps)
    ordered = list(dict.fromkeys(str(code).split(".")[0].zfill(6) for code in codes))
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
        payload["effective_cap"] = max(
            1, min(cap_values.values()) - int(payload.get("rate_limit_failures") or 0)
        )
        payload["updated_at"] = _stamp(now)
        _atomic_write(path, payload)
    return {
        "ok": True,
        "path": str(path),
        "n": len(ordered),
        "codes": ordered,
        "effective_cap": payload["effective_cap"],
    }


def _verified(task: dict) -> bool:
    for name in ("prompt", "slim", "card"):
        ref = task.get("artifacts", {}).get(name) or {}
        content_hash = ref.get("content_hash")
        path = Path(str(ref.get("path") or ""))
        if not content_hash or not path.is_file() or path.stat().st_size == 0:
            return False
        if _sha256(path) != content_hash:
            return False
    return True


def preflight(
    book: Path | str,
    code: str,
    *,
    now: datetime | None = None,
    stale_after_seconds: int = 3600,
) -> dict:
    """为一票领取一次执行权；SUCCEEDED 只在三件产物指纹仍匹配时可复用。"""
    path = Path(book)
    code6 = str(code).split(".")[0].zfill(6)
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
        status = task["status"]
        if status == "SUCCEEDED":
            if _verified(task):
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
            status = "FAILED"
            task["status"] = status
            task["last_error_class"] = "STALE_TASK"
            task["last_error"] = f"running for {int(age)}s"
            reason = "STALE_TASK"
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
                _atomic_write(path, payload)
                return {
                    "ok": True,
                    "code": code6,
                    "action": "BLOCKED",
                    "attempt": task["attempt"],
                    "reason": error_class or "NON_TRANSIENT_FAILURE",
                }
            reason = reason or error_class
        task["attempt"] = int(task.get("attempt") or 0) + 1
        task["status"] = "RUNNING"
        task["started_at"] = stamp
        task["updated_at"] = stamp
        _atomic_write(path, payload)
        return {
            "ok": True,
            "code": code6,
            "action": "RUN",
            "attempt": task["attempt"],
            "reason": reason or "PENDING",
        }


def mark_failure(
    book: Path | str,
    code: str,
    error_class: str,
    *,
    error: str | None = None,
    now: datetime | None = None,
) -> dict:
    path = Path(book)
    code6 = str(code).split(".")[0].zfill(6)
    kind = str(error_class).strip().upper()
    with _locked(path):
        _, payload = _read(path)
        task = payload["tasks"][code6]
        task["attempt"] = max(1, int(task.get("attempt") or 0))
        task["last_error_class"] = kind
        task["last_error"] = error or kind
        task["status"] = "FAILED" if kind in TRANSIENT_ERRORS else "BLOCKED"
        task["updated_at"] = _stamp(now)
        if kind == "RATE_LIMIT":
            payload["rate_limit_failures"] = int(
                payload.get("rate_limit_failures") or 0
            ) + 1
        _atomic_write(path, payload)
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
    now: datetime | None = None,
) -> dict:
    path = Path(book)
    code6 = str(code).split(".")[0].zfill(6)
    with _locked(path):
        _, payload = _read(path)
        task = payload["tasks"][code6]
        refs = task["artifacts"]
        missing = []
        for name in ("prompt", "slim", "card"):
            artifact_path = Path(refs[name]["path"])
            if not artifact_path.is_file() or artifact_path.stat().st_size == 0:
                missing.append(name)
                continue
            refs[name] = _artifact(
                artifact_path, content_hash=_sha256(artifact_path)
            )
        if missing:
            raise ValueError(f"L4 success missing artifacts:{','.join(missing)}")
        from autoresearch.scan.agents.l4_card import _slim_defect

        _, defect = _slim_defect(Path(refs["slim"]["path"]), 4096)
        if defect:
            raise ValueError(f"L4 success invalid slim:{defect}")
        task["status"] = "SUCCEEDED"
        task["last_error_class"] = None
        task["last_error"] = None
        task["updated_at"] = _stamp(now)
        _atomic_write(path, payload)
    return {
        "ok": True,
        "code": code6,
        "status": "SUCCEEDED",
        "attempt": task["attempt"],
    }


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
    from autoresearch.scan.agents.l4_card import _default_harvest_slim, _slim_defect

    harvest = harvest_fn or (
        lambda symbol, date: _default_harvest_slim(symbol, date, slim_path.parent)
    )
    size, defect = _slim_defect(slim_path, min_bytes)
    attempts = 0
    if defect:
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
    with _locked(path):
        _, latest = _read(path)
        current = latest["tasks"][code6]
        current["slim_attempts"] = int(current.get("slim_attempts") or 0) + attempts
        current["artifacts"]["slim"] = _artifact(
            slim_path,
            content_hash=_sha256(slim_path) if defect is None else None,
        )
        current["updated_at"] = _stamp(now)
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
    }


def dispatch_batches(
    book: Path | str,
    *,
    caps: dict | None = None,
    now: datetime | None = None,
) -> dict:
    """按四种独立资源帽的最小值稳定切批；限频事件只收窄调度宽度。"""
    path = Path(book)
    with _locked(path):
        _, payload = _read(path)
        cap_values = _normalize_caps(caps if caps is not None else payload.get("caps"))
        effective = max(
            1, min(cap_values.values()) - int(payload.get("rate_limit_failures") or 0)
        )
        codes = []
        for code in payload.get("order") or payload["tasks"]:
            task = payload["tasks"].get(code)
            if not task:
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
    }


def _book_path(date: str, root: str | None) -> Path:
    return (Path(root) if root else Path("context/scan")) / date / "_l4_tasks.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="l4_tasks")
    parser.add_argument(
        "cmd",
        choices=["init", "preflight", "prepare", "success", "failure", "batches"],
    )
    parser.add_argument("first", help="init/batches:DATE；其余:CODE")
    parser.add_argument("second", nargs="?", help="preflight/prepare/success/failure:DATE")
    parser.add_argument("--root", default=None)
    parser.add_argument("--error-class", default=None)
    parser.add_argument("--error", default=None)
    parser.add_argument("--caps-json", default=None)
    args = parser.parse_args(argv)
    caps = json.loads(args.caps_json) if args.caps_json else None
    if args.cmd == "init":
        from autoresearch.scan.agents.l4_card import dispatch_plan

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
        result["dispatch_batches"] = dispatch_batches(result["path"])["batches"]
    elif args.cmd == "batches":
        result = dispatch_batches(_book_path(args.first, args.root), caps=caps)
    else:
        if not args.second:
            parser.error(f"{args.cmd} requires CODE DATE")
        book = _book_path(args.second, args.root)
        if args.cmd == "preflight":
            result = preflight(book, args.first)
        elif args.cmd == "prepare":
            result = prepare_slim(book, args.first)
        elif args.cmd == "success":
            result = mark_success(book, args.first)
        else:
            if not args.error_class:
                parser.error("failure requires --error-class")
            result = mark_failure(
                book,
                args.first,
                args.error_class,
                error=args.error,
            )
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
