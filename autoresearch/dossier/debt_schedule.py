#!/usr/bin/env python3
"""档案债清偿排期 —— **纯排期,不新建机制**(确定性,零 LLM)。

design: docs/specs/2026-08-03-scan-next-wave-brainstorm-design.md §4.5「档案债清偿排期」

原文:

> 季度对账 20251231 × 19 只 = 一次批跑(确定性);18 只待建档按 ≤3/晚 ≈ 6 晚消化;
> **不新建机制,纯排期**。

所以本模块只做一件事:把 `dossier.debt_slo.compute()` 的真读数换算成「哪天做什么」。
**排期由真实 pool/ledger 派生** —— 池子变了排期就得变(这是 §5-3 要求的那个会变的探针:
把排期写死成一张静态表,池扩张时它会一直说「6 晚」)。

  uv run --no-sync python -m autoresearch.dossier.debt_schedule plan 2026-08-04
"""
from __future__ import annotations

import argparse
import json
from datetime import date as _date, timedelta
from pathlib import Path

SCHEMA_VERSION = 1


def _next_days(start: str, n: int) -> list[str]:
    """自然日排期 —— 夜间建档不挑交易日(它不取盘中数据)。"""
    base = _date.fromisoformat(start)
    return [(base + timedelta(days=i + 1)).isoformat() for i in range(max(0, n))]


def plan(today: str, *, pool_path=None, slo: dict | None = None) -> dict:
    """真读数 → 排期。`nightly_cap` 取自 SLO 本身,不另设常量(两处不同步是常见坑)。"""
    from autoresearch.dossier.debt_slo import compute

    reading = slo if slo is not None else compute(today, pool_path=pool_path)
    pending = int(reading.get("pending_n") or 0)
    cap = max(1, int(reading.get("nightly_cap") or 3))
    nights = -(-pending // cap) if pending else 0        # 向上取整
    overdue = list(reading.get("reconcile_overdue") or [])

    return {
        "schema_version": SCHEMA_VERSION,
        "today": today,
        # ① 季度对账:一次批跑(确定性,不占夜间建档名额)
        "reconcile": {
            "n": len(overdue), "codes": overdue,
            "mode": "single_batch",
            "command": "uv run --no-sync python -m autoresearch.dossier.reconcile <period>",
            "note": "确定性批跑 —— 与逐晚建档互不占用",
        },
        # ② 待建档:≤cap/晚
        "init": {
            "pending_n": pending, "nightly_cap": cap, "nights_needed": nights,
            "dates": _next_days(today, nights),
            "note": f"≤{cap}/晚 —— 首覆是 LLM 段,夜间只排量不代跑判断",
        },
        "throughput": {
            "added_7d": reading.get("added_7d"),
            "digested_7d": reading.get("digested_7d"),
            "converging": (None if reading.get("digested_7d") is None
                           else int(reading.get("digested_7d") or 0)
                           >= int(reading.get("added_7d") or 0)),
            "note": "消化 < 新增 → 债务不收敛,排期再密也追不上",
        },
        "mechanism_note": "**不新建机制** —— 本模块只把既有 SLO 读数换算成排期",
        "derived_from": "dossier.debt_slo.compute(真实 pool/ledger)",
    }


def render(schedule: dict) -> str:
    rec, init, thr = schedule["reconcile"], schedule["init"], schedule["throughput"]
    lines = [f"# 档案债清偿排期 · {schedule['today']}", "",
             f"> {schedule['mechanism_note']}(派生自 {schedule['derived_from']})", "",
             f"## ① 季度对账:{rec['n']} 只 · {rec['mode']}", ""]
    if rec["n"]:
        lines += [f"- 待对账:{'、'.join(rec['codes'][:20])}"
                  + ("…" if rec["n"] > 20 else ""),
                  f"- `{rec['command']}`"]
    else:
        lines.append("- 无待对账")
    lines += [f"- {rec['note']}", "",
              f"## ② 待建档:{init['pending_n']} 只 · ≤{init['nightly_cap']}/晚 → "
              f"**{init['nights_needed']} 晚**", ""]
    if init["dates"]:
        lines.append("| 夜次 | 日期 |")
        lines.append("|---:|---|")
        lines += [f"| {i} | {d} |" for i, d in enumerate(init["dates"], 1)]
    else:
        lines.append("- 无待建档")
    converging = thr["converging"]
    lines += ["", f"- {init['note']}", "",
              f"## ③ 吞吐:7 日新增 {thr['added_7d']} / 消化 {thr['digested_7d']} —— "
              + ("—" if converging is None else ("✅ 收敛" if converging else "🚨 **不收敛**")),
              "", f"- {thr['note']}", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="档案债清偿排期(§4.5;纯排期)")
    ap.add_argument("cmd", choices=["plan"])
    ap.add_argument("date", nargs="?", default=None)
    ap.add_argument("--json-out", default=None)
    a = ap.parse_args(argv)
    schedule = plan(a.date or _date.today().isoformat())
    if a.json_out:
        out = Path(a.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(schedule, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
    print(render(schedule))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
