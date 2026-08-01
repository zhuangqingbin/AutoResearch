#!/usr/bin/env python3
"""覆盖档案的欠账 SLO —— 把「今天清零了吗」换成「这条流水线是否稳态」(确定性,零 LLM)。

design: docs/specs/2026-08-01-wave10-report-ops-slimdown-zerobuy-design.md §A10

**为什么不以「某日绝对清零」为成功条件**:池会持续吸纳新票(pinned / 反复入选),
所以 `pending_init == 0` 是个**随时会被正常运营打破**的目标。把它当 KPI 只有两个结局:
要么天天红、读的人麻木;要么为了让它绿而放宽入池 —— 两个都比现在更糟。

改成三条**稳态**判据(§A10),必须同时成立:

    ① oldest_pending_age ≤ 48h    —— 最老的一只等了多久(不是有几只在等)
    ② overdue_count == 0          —— 季度对账债清零
    ③ 7 日消化数 ≥ 7 日新增数      —— 流水线不欠速(积压不增长)

② 的 `reconcile_overdue` **单列**,不与 `pending_init` 混数:一个是"还没建档",
一个是"建过档但没做季度对账",治法和责任人都不同,加在一起只会得到一个没人能行动的数。

  uv run --no-sync python -m autoresearch.dossier.debt_slo 2026-08-01
"""
from __future__ import annotations

from datetime import date as _date
from pathlib import Path

MAX_PENDING_AGE_DAYS = 2        # 48h
THROUGHPUT_WINDOW_DAYS = 7
NIGHTLY_CAP = 3                 # 帽 ≤3/晚不变(§A10)


def _days_between(earlier: str, later: str) -> int | None:
    try:
        return (_date.fromisoformat(str(later)) - _date.fromisoformat(str(earlier))).days
    except (TypeError, ValueError):
        return None


def _built_on(code: str) -> str | None:
    """档案的建成日 —— 取 frontmatter 的 `initiated`;缺则回退文件 mtime(并标注为代理)。"""
    from autoresearch.dossier import schema

    path = schema.dossier_path(code)
    if not path.exists():
        return None
    try:
        stamp = schema.parse_frontmatter(path.read_text(encoding="utf-8")).get("initiated")
    except Exception:  # noqa: BLE001
        stamp = None
    if stamp:
        return str(stamp)[:10]
    try:
        return _date.fromtimestamp(path.stat().st_mtime).isoformat()
    except OSError:
        return None


def reconcile_overdue(today: str, *, pool_path=None) -> list[str]:
    """已建档但缺「季度对账 <period>」痕迹的票 —— 与 `pending_init` **是两本账**。"""
    from autoresearch.dossier import delta as _delta, pool as _pool, schema as _schema
    from autoresearch.dossier.mainbz import _recent_periods

    period = _recent_periods(today, 1)[0]
    todo: list[str] = []
    for code, st in sorted(_pool.load_pool(pool_path).get("stocks", {}).items()):
        if st.get("status") != "active":
            continue
        path = _schema.dossier_path(code)
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        if not _schema.parse_frontmatter(text).get("initiated"):
            continue
        if f"季度对账 {period}" not in _delta.section_body(text, 4):
            todo.append(code)
    return todo


def compute(today: str, *, pool_path=None) -> dict:
    """三条 SLO 的读数。任何一条算不出 → 该条 `None`,`ok` 也为 None(不判 ≠ 通过)。"""
    from autoresearch.dossier import pool as _pool

    pool = _pool.load_pool(pool_path)
    stocks = pool.get("stocks", {})
    pending = _pool.pending_init(pool)

    ages = {c: age for c in pending
            if (age := _days_between((stocks.get(c) or {}).get("entered", ""), today))
            is not None}
    oldest_code = max(ages, key=lambda c: ages[c]) if ages else None
    oldest_age = ages.get(oldest_code) if oldest_code else None

    overdue = reconcile_overdue(today, pool_path=pool_path)

    added = [c for c, st in stocks.items()
             if (d := _days_between(st.get("entered", ""), today)) is not None
             and 0 <= d < THROUGHPUT_WINDOW_DAYS]
    digested = [c for c in stocks
                if (built := _built_on(c))
                and (d := _days_between(built, today)) is not None
                and 0 <= d < THROUGHPUT_WINDOW_DAYS]

    meets_age = None if oldest_age is None else oldest_age <= MAX_PENDING_AGE_DAYS
    meets_overdue = len(overdue) == 0
    meets_throughput = len(digested) >= len(added)
    checks = (meets_age if pending else True, meets_overdue, meets_throughput)
    return {
        "today": today,
        "pending_n": len(pending),
        "oldest_pending_code": oldest_code,
        "oldest_pending_age_days": oldest_age,
        "max_pending_age_days": MAX_PENDING_AGE_DAYS,
        "reconcile_overdue_n": len(overdue),
        "reconcile_overdue": overdue,
        "added_7d": len(added),
        "digested_7d": len(digested),
        "nightly_cap": NIGHTLY_CAP,
        "meets_age": meets_age,
        "meets_overdue": meets_overdue,
        "meets_throughput": meets_throughput,
        "ok": None if any(c is None for c in checks) else all(checks),
    }


def render(slo: dict) -> str:
    """一行 SLO。绿灯不写成「清零」—— 清零不是目标,稳态才是。"""
    age = slo["oldest_pending_age_days"]
    age_txt = ("—" if age is None else f"{age}d") + f"/≤{slo['max_pending_age_days']}d"
    marks = {True: "✅", False: "🚨", None: "—"}
    return (
        f"{marks[slo['ok']]} 档案欠账 SLO:"
        f"①最老待建档 {age_txt}{marks[slo['meets_age']]}"
        f" · ②季度对账债 {slo['reconcile_overdue_n']}{marks[slo['meets_overdue']]}"
        f" · ③7日消化 {slo['digested_7d']}≥新增 {slo['added_7d']}"
        f"{marks[slo['meets_throughput']]}"
        f"(在等 {slo['pending_n']} 只,帽 ≤{slo['nightly_cap']}/晚;"
        f"**清零不是目标,稳态才是**)"
    )


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json

    ap = argparse.ArgumentParser(description="覆盖档案欠账 SLO(确定性)")
    ap.add_argument("today", nargs="?", default=_date.today().isoformat())
    ap.add_argument("--pool", default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    slo = compute(args.today, pool_path=Path(args.pool) if args.pool else None)
    print(json.dumps(slo, ensure_ascii=False, indent=2) if args.json else render(slo))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
