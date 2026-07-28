#!/usr/bin/env python3
"""夜间收盘后的**确定性**欠账补跑(零 LLM,零判断)。

design: docs/specs/2026-07-28-wave7-unified-roadmap-design.md §6 P5

治的是全项目最贵的病:**腿没人踢**。判断力基建大面积建成后闲置 —— retro 欠 3 天、
t1 快环欠 1 对、账本落后一个 run,而这些欠账里**确定性的那一半**(归因计算、记分卡构建、
账本刷新)本来就不需要人,只是没人按按钮。

分工不变(这是本模块的边界,不是省略):
  · 本模块只跑**确定性段** —— 算得出对错的部分;
  · **LLM 诊断段仍人工**(scan-retro / t1-review workflow)—— "为什么错、怎么改"要人在场。
所以 prelude 的提醒语义也随之改变:从「欠 N 天」变成「诊断段待跑 N 天(确定性已补)」——
欠账从"什么都没做"变成"数据齐了,就差你看一眼"。

各步独立 suppress:单步失败不连坐(prelude `_ledgers` 同款惯例),末尾汇总退出码恒 0
——夜间任务失败不该把 launchd 搞成红灯常亮,状态看汇总行。

  uv run --no-sync python -m autoresearch.learning.nightly_close [YYYY-MM-DD]
"""
from __future__ import annotations

import contextlib
from datetime import date as _date


def _step(name: str, fn) -> tuple[str, bool, str]:
    try:
        return name, True, str(fn() or "")
    except Exception as e:  # noqa: BLE001 — 夜间任务:单步失败不连坐,汇总里如实写
        return name, False, f"{type(e).__name__}: {e}"


def run(today: str) -> list[tuple[str, bool, str]]:
    """按依赖序跑确定性欠账;返回 [(步骤, 成功, 备注)]。"""
    out: list[tuple[str, bool, str]] = []

    def _retro_refresh() -> str:
        """已成熟未归因日 → 逐日 attribute(纯计算,幂等)。诊断叙事仍归 scan-retro。"""
        from autoresearch.learning import retro
        days = retro.pending_days(today) or []
        done = []
        for d in days:
            with contextlib.suppress(Exception):   # 单日失败不拖累其余日
                retro.attribute(d)
                done.append(d)
        return (f"归因 {len(done)}/{len(days)} 日({'、'.join(done) or '—'})"
                if days else "无待归因日")

    def _t1_backfill() -> str:
        from autoresearch.learning import t1_review
        pend = t1_review.pending_pairs(today) or []
        done = []
        for pair in pend:
            t = pair.get("t") if isinstance(pair, dict) else pair[0]
            with contextlib.suppress(Exception):
                t1_review.backfill_day(t)
                done.append(t)
        return (f"确定性回补 {len(done)}/{len(pend)} 对({'、'.join(done) or '—'})"
                if pend else "无待复盘对")

    def _tripwire() -> str:
        from autoresearch.learning.tripwire_watch import check
        hits = check(today)
        return f"⚡ {len(hits)} 条触发" if hits else "无触发"

    def _ledgers() -> str:
        import importlib
        names = ["journal", "buy_ledger", "cross_calib", "catalyst_ledger", "paper_nav",
                 "channel_ledger", "gate_ledger", "zero_buy_ledger", "changelog_ledger",
                 "earlystop_ledger", "pinned_ledger"]
        ok = 0
        for n in names:
            with contextlib.suppress(Exception):
                importlib.import_module(f"autoresearch.learning.{n}").main()
                ok += 1
        return f"{ok}/{len(names)} 刷新"

    for name, fn in (("retro_refresh", _retro_refresh), ("t1_backfill", _t1_backfill),
                     ("tripwire", _tripwire), ("ledgers", _ledgers)):
        out.append(_step(name, fn))
    return out


def render(results: list[tuple[str, bool, str]], today: str) -> str:
    lines = [f"═══ nightly_close · {today}(确定性段;LLM 诊断仍人工)═══"]
    lines += [f"  {'✓' if ok else '✗'} {name}: {note}" for name, ok, note in results]
    n_bad = sum(1 for _, ok, _ in results if not ok)
    lines.append(f"  —— {len(results) - n_bad}/{len(results)} 成功"
                 + ("" if not n_bad else f";{n_bad} 步失败(见上,不连坐)"))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="夜间确定性欠账补跑(零 LLM)")
    ap.add_argument("date", nargs="?", default=_date.today().isoformat())
    a = ap.parse_args(argv)
    print(render(run(a.date), a.date))
    return 0            # 恒 0:夜间任务失败不该让 launchd 红灯常亮,状态看汇总行


if __name__ == "__main__":
    raise SystemExit(main())
