"""常备覆盖池(spec ③;确定性,零 LLM)。进出规则见 plan Task 4 精确化。"""
from __future__ import annotations

import argparse
import contextlib
import json
import shutil
import sys
from pathlib import Path

from autoresearch.common import workspace as ws
from autoresearch.dossier import schema

_WS_SCAN_ROOT = ws.scan_root()  # B008 修法:默认值须为模块级单例(def 时求值,与旧字面量常量同语义)

POOL_PATH = ws.knowledge_root() / "coverage_pool.json"


def load_pool(path: Path | None = None) -> dict:
    p = Path(path) if path else POOL_PATH
    if p.exists():
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(d, dict) and isinstance(d.get("stocks"), dict):
                d.setdefault("cap", 30)
                return d
        except Exception:
            pass
        with contextlib.suppress(Exception):
            shutil.copy2(p, p.with_suffix(".json.bak"))   # 坏 json(语法或形状):备份后重建
    return {"stocks": {}, "cap": 30}


def _recent_scan_days(scan_root: Path, n: int = 20) -> list[str]:
    if not scan_root.exists():
        return []
    days = sorted((p.name for p in scan_root.iterdir()
                   if p.is_dir() and p.name[:2] == "20" and (p / "finalists.csv").exists()),
                  reverse=True)
    return days[:n]


def _selections(scan_root: Path, days: list[str]) -> dict[str, list[str]]:
    """code6 → 真选命中日列表(lane≠pinned)。"""
    import pandas as pd
    hits: dict[str, list[str]] = {}
    for d in days:
        with contextlib.suppress(Exception):
            df = pd.read_csv(scan_root / d / "finalists.csv", dtype={"code": str})
            if "code" not in df.columns:
                continue
            for _, r in df.iterrows():
                raw = str(r.get("code", "") or "").strip()
                if not raw or raw == "nan":
                    continue
                c = raw.split(".")[0].zfill(6)
                if str(r.get("lane", "") or "").strip() == "pinned":
                    hits.setdefault(c, hits.get(c, []))   # pinned 注入不计真选
                    continue
                hits.setdefault(c, []).append(d)
    return hits


def _pending_priority_meta(pool: dict) -> dict[str, dict]:
    """把 `pending_init` 数组(Wave9 R6:`post_run.enqueue_finalist_dossiers` 写入的插队标注,
    code → `{"priority", "last_seen", ...}`)规整成查表。既有条目可能是纯字符串(如
    `"600018"`),两种形态都要容忍——不崩溃、不丢弃,只是没有优先级信息可查(视同未标注)。"""
    meta: dict[str, dict] = {}
    raw = pool.get("pending_init")
    if not isinstance(raw, list):
        return meta
    for e in raw:
        if isinstance(e, dict):
            code = str(e.get("code", "") or "").strip().split(".")[0].zfill(6)
            if code:
                meta[code] = e
        elif isinstance(e, str):
            code = e.strip().split(".")[0].zfill(6)
            if code:
                meta.setdefault(code, {})
    return meta


def pending_init(pool: dict) -> list[str]:
    """常备覆盖池里"活跃但未建档"的 code 列表,按建档优先级排序。

    排序键(Wave9 R6,B-3 续):`priority == "finalist"`(当日插队,见
    `post_run.enqueue_finalist_dossiers`)优先,同级按 `last_seen` 升序(先来先建);
    两者都缺时退化成原有的按 code 字典序——对没有任何插队标注的池(今天绝大多数情形)
    逐字节等价于 Wave9 前的行为。

    **Wave9 final-fix I-1(2026-07-30)**:候选集合此前**只**来自 `pool["stocks"]`
    (active ∧ 无档案)——但入池闸(见 `refresh()`)只放 `pinned` 或真选 ≥2 次的票进
    `stocks`,首次入围的 finalist 插队(`enqueue_finalist_dossiers` 写进
    `pending_init` 数组)永远进不了 `stocks`,于是插队对消费者(本函数)恒不可见,
    「插队 N 只」的回执近乎 no-op(final-review Important-1,07-29 实测:入队 5 只,
    消费者可见新增 0 只)。现在候选集合改为 `stocks` 活跃票 **并上** `pending_init`
    数组里排队的码——只要还没建档,不管它是否"真正"在 `stocks` 里(队列本身就是
    "这票该建档"的证据,不该被"是否也满足常备池准入"这个不相关的问题挡住)。
    """
    stocks = pool.get("stocks", {})
    candidates = {c for c, s in stocks.items()
                  if s.get("status") == "active" and not schema.dossier_path(c).exists()}
    meta = _pending_priority_meta(pool)
    candidates |= {c for c in meta if not schema.dossier_path(c).exists()}

    def _key(c: str) -> tuple[int, str, str]:
        e = meta.get(c, {})
        return (0 if e.get("priority") == "finalist" else 1, e.get("last_seen", "") or "", c)

    return sorted(candidates, key=_key)


def _refresh_unlocked(today: str, *, scan_root: str | Path = _WS_SCAN_ROOT,
                      pool_path: Path | None = None,
                      pinned_path: Path | None = None) -> dict:
    scan_root = Path(scan_root)
    pool = load_pool(pool_path)
    stocks = pool["stocks"]
    days = _recent_scan_days(scan_root)
    window_first = days[-1] if days else None
    sel = _selections(scan_root, days)

    from autoresearch.scan.user_config import load_pinned
    kept: dict[str, str] = {}
    with contextlib.suppress(Exception):
        kw = {"path": pinned_path} if pinned_path else {}
        for e in load_pinned(today, **kw).get("kept", []):
            kept[str(e.get("code", "")).zfill(6)] = str(e.get("note", "") or "")

    entered, retired, revived = [], [], []

    def _touch(c: str, reason: str, note: str = ""):
        st = stocks.get(c)
        last = max(sel.get(c, []), default=None)
        if st is None:
            stocks[c] = {"name": "", "status": "active", "entered": today,
                         "entry_reason": reason, "last_selected": last, "note": note}
            entered.append(c)
        else:
            if st.get("status") == "retired":
                st["status"] = "active"
                revived.append(c)
            if last and (st.get("last_selected") or "") < last:
                st["last_selected"] = last

    for c, note in kept.items():                     # pinned 即入/保活
        _touch(c, "pinned", note)
    for c, ds in sel.items():                        # 真选 ≥2 入
        if len(ds) >= 2:
            _touch(c, "finalist_2x")
        elif c in stocks and stocks[c].get("status") == "active":
            _touch(c, stocks[c].get("entry_reason", "manual"))   # 已在池:单次也刷新 last_selected

    for c, st in stocks.items():                     # 退池
        if st.get("status") != "active" or c in kept:
            continue
        last = st.get("last_selected")
        out_of_window = (last is None and st.get("entered", "") < (window_first or today)) or \
                        (last is not None and window_first is not None and last < window_first)
        if out_of_window:
            st["status"] = "retired"
            retired.append(c)

    actives = [c for c, s in stocks.items() if s.get("status") == "active"]
    if len(actives) > pool.get("cap", 30):           # cap LRU:按 last_selected 升序驱逐,不是入池序 FIFO(pinned 永不被 cap 退)
        evictable = sorted((c for c in actives if c not in kept),
                           key=lambda c: stocks[c].get("last_selected") or "")
        for c in evictable[:len(actives) - pool["cap"]]:
            stocks[c]["status"] = "retired"
            retired.append(c)

    pool["as_of"] = today
    p = Path(pool_path) if pool_path else POOL_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(pool, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"entered": sorted(entered), "retired": sorted(retired), "revived": sorted(revived),
            "pending_init": pending_init(pool),
            "n_active": sum(1 for s in stocks.values() if s.get("status") == "active")}


def refresh(today: str, *, scan_root: str | Path = _WS_SCAN_ROOT,
            pool_path: Path | None = None, pinned_path: Path | None = None) -> dict:
    """Refresh the pool only inside an active scan prelude write window."""
    from autoresearch.trace.write_guard import assert_output_path, guarded_ambient_write

    with guarded_ambient_write("scan.prelude") as tracked:
        if tracked is not None:
            assert_output_path(Path(pool_path) if pool_path else POOL_PATH, ws.context_root())
        return _refresh_unlocked(
            today,
            scan_root=scan_root,
            pool_path=pool_path,
            pinned_path=pinned_path,
        )


def _print_status(pool: dict) -> None:
    stocks = pool.get("stocks", {})
    active = sorted(c for c, s in stocks.items() if s.get("status") == "active")
    retired = sorted(c for c, s in stocks.items() if s.get("status") == "retired")
    pend = pending_init(pool)
    print(f"active({len(active)}): {', '.join(active) if active else '(空)'}")
    print(f"retired({len(retired)}): {', '.join(retired) if retired else '(空)'}")
    print(f"pending_init({len(pend)}): {', '.join(pend) if pend else '(空)'}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="常备覆盖池(确定性,零 LLM)")
    ap.add_argument("today", nargs="?", help="今日 YYYY-MM-DD(--status/--add/--remove 也需要)")
    ap.add_argument("--status", action="store_true", help="只打印 active/retired/pending_init,不 refresh")
    ap.add_argument("--add", metavar="CODE", help="手动入池(entry_reason=manual)")
    ap.add_argument("--remove", metavar="CODE", help="手动退池(置 retired)")
    ap.add_argument("--note", default="", help="配合 --add 的备注")
    args = ap.parse_args(argv)

    if not args.today:
        ap.error("today 位置参必填(YYYY-MM-DD)")

    pool_path = Path(POOL_PATH)

    if args.add:
        pool = load_pool(pool_path)
        code = str(args.add).split(".")[0].zfill(6)
        st = pool["stocks"].get(code)
        if st is None:
            pool["stocks"][code] = {"name": "", "status": "active", "entered": args.today,
                                     "entry_reason": "manual", "last_selected": None,
                                     "note": args.note}
        else:
            st["status"] = "active"
            st["note"] = args.note or st.get("note", "")
        pool["as_of"] = args.today
        pool_path.parent.mkdir(parents=True, exist_ok=True)
        pool_path.write_text(json.dumps(pool, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"[pool] +{code} (manual)")
        return 0

    if args.remove:
        pool = load_pool(pool_path)
        code = str(args.remove).split(".")[0].zfill(6)
        st = pool["stocks"].get(code)
        if st is None:
            print(f"[pool] {code} 不在池中", file=sys.stderr)
            return 1
        st["status"] = "retired"
        pool["as_of"] = args.today
        pool_path.parent.mkdir(parents=True, exist_ok=True)
        pool_path.write_text(json.dumps(pool, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"[pool] -{code} (retired)")
        return 0

    if args.status:
        _print_status(load_pool(pool_path))
        return 0

    out = refresh(args.today, pool_path=pool_path)
    print(f"entered({len(out['entered'])}): {', '.join(out['entered']) or '(无)'}")
    print(f"retired({len(out['retired'])}): {', '.join(out['retired']) or '(无)'}")
    print(f"revived({len(out['revived'])}): {', '.join(out['revived']) or '(无)'}")
    print(f"n_active={out['n_active']} pending_init={len(out['pending_init'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
