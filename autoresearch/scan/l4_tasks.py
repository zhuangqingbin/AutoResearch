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
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator

from autoresearch.scan import structural_audit

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
            status = "FAILED"
            task["status"] = status
            task["last_error_class"] = "STALE_TASK"
            task["last_error"] = f"running for {int(age)}s"
            reason = "STALE_TASK"
            # 一次执行悄悄蒸发了：既没 mark_success 也没 mark_failure，账本一直以为它在跑。
            structural_audit.record(
                payload, code6, structural_audit.COMPLETION_MISJUDGED,
                f"RUNNING {int(age)}s 无终态回写", now=now)
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
