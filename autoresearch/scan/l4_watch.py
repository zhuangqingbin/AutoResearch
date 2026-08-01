#!/usr/bin/env python3
"""L4 逐股出卡播报(确定性读盘,零 LLM)—— 唯一权威是 task_book。

**为什么不复用 progress.py**:那个模块按**产物文件存在性**反推阶段,分不清
「在跑 / 被跳过 / 挂了」,累犯三次误报(pr_20260717_004)。E1 已判它退役。

**为什么不直接 grep 卡片**:2026-07-28 首航临时挂的卡片级 grep 轮询读到了 601319
**写盘中途的草稿** —— 先播 Underweight,终稿是 Hold。文件存在 ≠ 写完了。

判据(与 W8-6 同一条纪律:完成态由账本定义,不由文件存在性定义):

- 只有 `_l4_tasks.json` 里 **status ∈ 终态** 的票才播;
- SUCCEEDED 还要求 **card.content_hash 已记**(账本确认卡是完稿)才去读评级;
- FAILED 直接播错误类别;
- 全部票进终态才算 done(FAILED 也是终态 —— 否则监视器永不退出)。

**消费进度归 watcher 自己**(Wave10 A8):此前 `seen` 只活在内存里 —— 进程一重启就把
所有已播事件再播一遍。修法**不是**往 task_book 里记「播过没有」:那本账是**任务状态**的
唯一事实源,往里塞消费者的进度会让两个关注点纠缠(而且两个 watcher 会互相覆盖)。
改为 watcher 自己维护 `outbox/l4_watch_cursor.json`:按 `consumer_id` 分栏记已确认的
事件 id,重启只播未确认的;要重播必须显式 `--replay-all`。
cursor 坏了 → **报错并要求人工选择**,不静默当空(静默当空 = 悄悄重播一整轮)。

用法:
  uv run --no-sync python -m autoresearch.scan.l4_watch <date>            # 打一次增量
  uv run --no-sync python -m autoresearch.scan.l4_watch <date> --watch    # 轮询,全终态即退出
  uv run --no-sync python -m autoresearch.scan.l4_watch <date> --replay-all  # 显式重播
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

SCAN_ROOT = Path("context/scan")
_TERMINAL = {"SUCCEEDED", "FAILED"}
_STALE_MIN_DEFAULT = 30

CURSOR_SCHEMA_VERSION = 1
DEFAULT_CONSUMER = "l4_watch"


class CursorCorrupt(RuntimeError):
    """cursor 读不懂。**不当空处理** —— 当空就等于悄悄重播一整轮。"""


def cursor_path(scan_dir: Path | str) -> Path:
    return Path(scan_dir) / "outbox" / "l4_watch_cursor.json"


def event_id(code: str, status: str, card: dict | None = None) -> str:
    """一次「进终态」事件的身份。

    带上卡的 content_hash:同一票 FAILED→重跑→SUCCEEDED 是**两个**事件,都该播;
    而同一份完稿被读两次是**同一个**事件,不该播两次。
    """
    digest = ((card or {}).get("content_hash") or "")[:12]
    return f"{code}:{status}:{digest}" if digest else f"{code}:{status}"


def load_cursor(scan_dir: Path | str, consumer_id: str = DEFAULT_CONSUMER) -> set[str]:
    """该 consumer 已确认的事件 id。文件不存在 → 空集(首次运行,合法)。

    文件存在但读不懂 → `CursorCorrupt`,让人决定是修还是 `--replay-all`。
    """
    path = cursor_path(scan_dir)
    if not path.exists():
        return set()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise CursorCorrupt(f"{path} 不是合法 JSON:{exc}") from exc
    if not isinstance(payload, dict) or "consumers" not in payload:
        raise CursorCorrupt(f"{path} 缺 consumers 段")
    if payload.get("schema_version") != CURSOR_SCHEMA_VERSION:
        raise CursorCorrupt(
            f"{path} schema_version={payload.get('schema_version')},"
            f"本程序只认 {CURSOR_SCHEMA_VERSION}")
    acked = (payload.get("consumers") or {}).get(consumer_id)
    if acked is None:
        return set()
    if not isinstance(acked, list):
        raise CursorCorrupt(f"{path} 的 consumers.{consumer_id} 不是列表")
    return {str(x) for x in acked}


def ack_events(scan_dir: Path | str, event_ids, *,
               consumer_id: str = DEFAULT_CONSUMER) -> Path:
    """把已播事件写进 cursor(原子)。**只动本 consumer 那一栏**,不碰别人的。"""
    path = cursor_path(scan_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schema_version": CURSOR_SCHEMA_VERSION, "consumers": {}}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(existing, dict) and isinstance(existing.get("consumers"), dict):
                payload = existing
                payload["schema_version"] = CURSOR_SCHEMA_VERSION
        except Exception:  # noqa: BLE001 — 走到这儿说明调用方已决定覆盖(--replay-all)
            pass
    merged = set(payload["consumers"].get(consumer_id) or []) | {str(x) for x in event_ids}
    payload["consumers"][consumer_id] = sorted(merged)
    temp = path.with_name(f"{path.name}.tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    temp.replace(path)
    return path


def _rating_of(card: Path) -> str | None:
    """从决策卡里抠 `**Rating**: X`;抠不到 → None(不猜)。"""
    try:
        for line in card.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("**Rating**"):
                return stripped.split(":", 1)[-1].replace("*", "").strip() or None
    except Exception:  # noqa: BLE001 — 读不动就是读不动,不臆测
        return None
    return None


def _names(scan_dir: Path) -> dict[str, str]:
    """code → name(取自 finalists.csv;缺文件则空表,播报退化为只有代码)。"""
    path = scan_dir / "finalists.csv"
    if not path.exists():
        return {}
    try:
        import pandas as pd
        df = pd.read_csv(path, dtype={"code": str})
        if "code" not in df.columns or "name" not in df.columns:
            return {}
        return {str(c).zfill(6): str(n) for c, n in zip(df["code"], df["name"])}
    except Exception:  # noqa: BLE001
        return {}


def snapshot(scan_dir: Path | str) -> dict:
    """读 task_book,返回逐票终态视图。账本不在 → ready=False(报,不猜)。"""
    scan_dir = Path(scan_dir)
    book = scan_dir / "_l4_tasks.json"
    if not book.exists():
        return {"ready": False, "done": False, "total": 0, "terminal": [], "running": []}
    try:
        payload = json.loads(book.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — 半写入的账本:这一轮当没读到,下一轮再看
        return {"ready": False, "done": False, "total": 0, "terminal": [], "running": []}

    tasks = payload.get("tasks") or {}
    names = _names(scan_dir)
    terminal: list[dict] = []
    running: list[dict] = []
    for code in payload.get("order") or tasks:
        task = tasks.get(code)
        if not task:
            continue
        status = task.get("status")
        if status not in _TERMINAL:
            if status == "RUNNING":
                running.append({"code": code, "started_at": task.get("started_at")})
            continue
        card = (task.get("artifacts") or {}).get("card") or {}
        rating = None
        if status == "SUCCEEDED":
            if not card.get("content_hash"):
                # 账本还没确认卡是完稿 —— 不读、不播,等下一轮。
                continue
            rating = _rating_of(scan_dir / "details" / f"{code}.md")
        terminal.append({
            "code": code,
            "name": names.get(str(code).zfill(6), ""),
            "status": status,
            "rating": rating,
            "error": task.get("last_error_class"),
            "event_id": event_id(code, status, card),
        })

    n_total = len(tasks)
    n_terminal_all = sum(1 for t in tasks.values() if t.get("status") in _TERMINAL)
    return {
        "ready": True,
        "done": bool(tasks) and n_terminal_all == n_total,
        "total": n_total,
        "terminal": terminal,
        "running": running,
    }


def render_events(snap: dict, seen: set[str], *, stale_min: int = _STALE_MIN_DEFAULT) -> list[str]:
    """把「本轮新进终态、且本 consumer 还没确认过的」事件渲染成播报行。

    去重键是**事件 id** 而不是 code:同一票 FAILED→重跑→SUCCEEDED 是两个事件,都该播。
    """
    if not snap.get("ready"):
        return []
    out: list[str] = []
    k = len(seen)
    for item in snap["terminal"]:
        if item.get("event_id", item["code"]) in seen:
            continue
        k += 1
        head = f"🃏 {k}/{snap['total']} {item['code']} {item['name']}".rstrip()
        if item["status"] == "FAILED":
            out.append(f"✗ {k}/{snap['total']} {item['code']} {item['name']} "
                       f"→ FAILED({item['error'] or '未记错误类别'})".rstrip())
        else:
            out.append(f"{head} → {item['rating'] or '(评级行读不到)'}")
    return out


def pending_fold_lines(scan_dir: Path | str) -> list[str]:
    """播报的是**卡片评级**,不是终评 —— 有折回分歧时显式提醒,别让人照卡片下结论。

    2026-07-28 实况:688766 卡片写 Underweight,而 sell_review 三跑
    [UW, Hold, Hold] 取中位 → 终评 **Hold**。我当时照卡片播了 UW 又更正。
    折回发生在 assemble(`decision_finalize`),本模块跑在它之前,看不到终评 ——
    所以不猜,只把「这票还有折回待结算」摆出来。
    """
    scan_dir = Path(scan_dir)
    out: list[str] = []
    for path in sorted(scan_dir.glob("_ensemble_*.json")):
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        code = str(rec.get("code") or path.stem.replace("_ensemble_", ""))
        median = rec.get("median")
        card = _rating_of(scan_dir / "details" / f"{code}.md")
        if median and card and median != card:
            out.append(f"↩️ {code} 卡片 {card} vs 复核中位 {median}"
                       f"(trigger={rec.get('trigger')},n={rec.get('n_runs')})"
                       f" → **终评以 assemble 折回后为准**")
    if out:
        out.append("   (以上为 ensemble 折回待结算项;跑完 assemble 再报最终评级)")
    return out


def _stale_lines(snap: dict, stale_min: int, warned: set[str], now: float) -> list[str]:
    """在飞超龄提示(只提示,不判死):每票只提醒一次。"""
    from datetime import datetime, timezone
    out = []
    for item in snap.get("running", []):
        code, started = item["code"], item.get("started_at")
        if code in warned or not started:
            continue
        try:
            begin = datetime.fromisoformat(str(started).replace("Z", "+00:00"))
        except Exception:  # noqa: BLE001
            continue
        mins = (datetime.now(timezone.utc) - begin).total_seconds() / 60
        if mins >= stale_min:
            warned.add(code)
            out.append(f"⏳ {code} 已跑 {int(mins)}min(未超时,仅提示)")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="L4 逐股出卡播报(读 _l4_tasks.json;卡片仅在账本确认完稿后才读)")
    ap.add_argument("date", help="分析日 YYYY-MM-DD")
    ap.add_argument("--watch", action="store_true", help="轮询;全部票进终态即退出")
    ap.add_argument("--interval", type=float, default=5.0, help="轮询间隔秒,默认 5")
    ap.add_argument("--stale-min", type=int, default=_STALE_MIN_DEFAULT,
                    help=f"在飞超过该分钟数提示一次,默认 {_STALE_MIN_DEFAULT}")
    ap.add_argument("--scan-root", default=str(SCAN_ROOT))
    ap.add_argument("--consumer-id", default=DEFAULT_CONSUMER,
                    help=f"消费者标识(多个 watcher 各记各的进度),默认 {DEFAULT_CONSUMER}")
    ap.add_argument("--replay-all", action="store_true",
                    help="忽略 cursor,从头重播(唯一的重播入口)")
    args = ap.parse_args(argv)

    scan_dir = Path(args.scan_root) / args.date
    warned: set[str] = set()

    if args.replay_all:
        seen: set[str] = set()
    else:
        try:
            seen = load_cursor(scan_dir, args.consumer_id)
        except CursorCorrupt as exc:
            # 不静默当空:当空就等于悄悄把一整轮重播一遍,而人以为这是新事件。
            print(f"✗ 播报游标损坏:{exc}", file=sys.stderr)
            print("  请人工选择:修好该文件,或用 --replay-all 显式从头重播。",
                  file=sys.stderr)
            return 2

    while True:
        snap = snapshot(scan_dir)
        for line in render_events(snap, seen, stale_min=args.stale_min):
            print(line, flush=True)
        fresh = [item.get("event_id", item["code"])
                 for item in snap.get("terminal", [])
                 if item.get("event_id", item["code"]) not in seen]
        if fresh:
            ack_events(scan_dir, fresh, consumer_id=args.consumer_id)
            seen.update(fresh)
        for line in _stale_lines(snap, args.stale_min, warned, time.time()):
            print(line, flush=True)
        if snap.get("done"):
            n_fail = sum(1 for i in snap["terminal"] if i["status"] == "FAILED")
            tail = f"(失败 {n_fail})" if n_fail else ""
            print(f"✅ L4 全部 {snap['total']} 票进终态{tail}", flush=True)
            for line in pending_fold_lines(scan_dir):
                print(line, flush=True)
            return 0
        if not args.watch:
            if not snap.get("ready"):
                print("⏳ task_book 未就绪(L4-prep 尚未落盘)", flush=True)
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
