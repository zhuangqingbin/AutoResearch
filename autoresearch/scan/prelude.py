#!/usr/bin/env python3
"""scan-market · 确定性前奏一键化(零 LLM)——开扫前全部确定性步骤一条命令跑完。

design: docs/specs/2026-07-03-scan-run-reliability-design.md §2

首航(07-02)人肉串前奏 ~10 分钟且有漏跑风险;本模块把它收编:
attribution 刷新 → retro pending 列出(**只备料不代跑诊断**)→ consensus 拉(限频容忍)
→ universe(regime-aware 默认开,含影子)→ 日历 → 观察单日检(触发置顶)→ 菜单/预算/哨兵
→ journal + buy_ledger 刷新。各步 try 包裹失败不阻断,末尾汇总屏。

  uv run --no-sync python -m autoresearch.scan.prelude 2026-07-03
  uv run --no-sync python -m autoresearch.scan.prelude 2026-07-03 --no-regime-aware --skip universe
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _run_steps(steps) -> list[dict]:
    """[(name, fn)] 顺序执行,单步异常不阻断 → [{'step','ok','note'}]。骨架可单测。"""
    out: list[dict] = []
    for name, fn in steps:
        try:
            note = fn()
            out.append({"step": name, "ok": True, "note": "" if note is None else str(note)})
        except Exception as e:  # noqa: BLE001
            out.append({"step": name, "ok": False, "note": f"{type(e).__name__}: {e}"})
            print(f"[prelude] ✗ {name}: {e}", file=sys.stderr)
    return out


def calib_suggestion_lines(scan_root=None) -> list[str]:
    """当日件建议行(spec 2026-07-05 §8 验收⑤):📐 触价校准 + 🔁 L3 翻案 + 🚪 门柱。

    组件各自 thin 禁注(样本不足的行自带"禁注"字样,编排层勿贴);报表落盘由 _ledgers
    步骤负责,本函数只收集行(纯读,可单测)。
    """
    from autoresearch.learning.buy_ledger import calibration_line, target_calibration
    from autoresearch.learning.cross_calib import flip_stats, gate_stats, suggestion_lines
    lines = [ln for ln in [calibration_line(target_calibration(scan_root))] if ln]
    lines += suggestion_lines(flip_stats(scan_root), gate_stats(scan_root))
    return lines


def run_prelude(date: str, regime_aware: bool = True, skip: tuple[str, ...] = ()) -> list[dict]:
    scan_dir = Path("context/scan") / date

    def _refresh():
        from autoresearch.learning.retro import refresh_attributions
        done = refresh_attributions()
        return f"刷新 {len(done)} 日" + (f"({'、'.join(done)})" if done else "")

    def _pending():
        from autoresearch.learning.retro import pending_days
        days = pending_days(today=date)
        return ("待诊断 retro 日:" + "、".join(days) + " ← 开扫前先用 scan-retro 补诊断") \
            if days else "无待复盘日"

    def _consensus():
        from autoresearch.research.consensus import pull
        pull(date)
        return "一致预期已拉(或今日已有)"

    def _universe():
        from autoresearch.scan.universe import run
        res = run(date, regime_aware=regime_aware)
        return f"L0 {res['universe']} → 召回 {res['recall_n']} → L2 {res['l2_n']}({res['l2_engine']})"

    def _calendar():
        import pandas as pd

        from autoresearch.scan.calendar import harvest_calendar
        codes: set[str] = set()
        for fname in ("L2_gbdt_top200.csv", "finalists.csv"):
            p = scan_dir / fname
            if p.exists():
                df = pd.read_csv(p, dtype={"code": str})
                if "code" in df.columns:
                    codes |= set(df["code"].astype(str).str.zfill(6))
        if not codes:
            return "跳过(无 L2 staging)"
        df = harvest_calendar(date, codes)
        n_u = int((df["kind"] == "unlock").sum()) if len(df) else 0
        n_d = int((df["kind"] == "disclosure").sum()) if len(df) else 0
        return f"解禁 {n_u} + 披露 {n_d}"

    def _watchlist():
        from autoresearch.scan.watchlist import run_check
        st = run_check(date, scan_dir)
        if st is None or not len(st):
            return "观察单空/无 L1"
        trig = st[st["status"].astype(str).str.startswith("触发")]
        if len(trig):
            return "🔔 触发:" + "、".join(f"{r['name']}({r['code']})" for _, r in trig.iterrows())
        return f"{len(st)} 条在监控,无触发"

    def _catalyst():
        import pandas as pd

        from autoresearch.scan.agents.l3_catalyst import harvest_catalyst
        p = scan_dir / "L2_gbdt_top200.csv"
        if not p.exists():
            return "跳过(无 L2 staging)"
        codes = pd.read_csv(p, dtype={"code": str})["code"].astype(str).str.zfill(6).tolist()
        df = harvest_catalyst(date, codes)
        pos = [c for c in ("rep_impl", "rep_plan", "holder_in", "surv_n") if c in df.columns]
        n = int((df[pos].fillna(0).sum(axis=1) > 0).sum()) if len(df) and pos else 0
        return f"催化旗 {n}/{len(df)} 只(回购/增持/调研)"

    def _menu():
        from autoresearch.scan.menu import l4_budget, menu_health, sentinel_advice
        mh = menu_health(scan_dir)
        n, why = l4_budget(scan_dir)
        level, reason = sentinel_advice(scan_dir)
        print(mh or "(菜单体检:staging 缺)")
        return f"L4 预算 {n}({why});sentinel={level}({reason})"

    def _ledgers():
        from autoresearch.learning import buy_ledger, catalyst_ledger, cross_calib, journal, paper_nav
        journal.main()
        buy_ledger.main()
        cross_calib.main()
        catalyst_ledger.main()
        paper_nav.main()
        return "journal + buy_ledger + cross_calib + catalyst + paper_nav 已刷新"

    all_steps = [("retro_refresh", _refresh), ("retro_pending", _pending),
                 ("consensus", _consensus), ("universe", _universe), ("calendar", _calendar),
                 ("watchlist", _watchlist), ("catalyst", _catalyst), ("menu", _menu),
                 ("ledgers", _ledgers)]
    results = _run_steps([(n, f) for n, f in all_steps if n not in skip])

    print("\n" + "═" * 30 + f" prelude 汇总 · {date} " + "═" * 30)
    for r in results:
        mark = "✓" if r["ok"] else "✗"
        print(f"  {mark} {r['step']}: {r['note']}")
    trig = next((r["note"] for r in results if r["step"] == "watchlist" and "🔔" in r["note"]), None)
    if trig:
        print(f"  ⚠️  {trig} —— 触发票走直通车(append_express)直达 L4 复核")
    pend = next((r["note"] for r in results if r["step"] == "retro_pending" and "待诊断" in r["note"]), None)
    if pend:
        print(f"  ⚠️  {pend}")
    try:                                      # 当日件建议行(spec 2026-07-05;含"禁注"的行勿贴)
        clines = calib_suggestion_lines()
        if clines:
            print("  当日件建议行(📐 贴 _l4_shared_instructions.md;🔁 贴 L3 校准块旁;"
                  "🚪 贴 skeptic/PM 先验;**含「禁注」的行勿贴**):")
            for ln in clines:
                print(f"    {ln}")
    except Exception as e:  # noqa: BLE001 — 建议行可选,缺了不挡前奏
        print(f"[prelude] ✗ calib_lines: {e}", file=sys.stderr)
    print("  下一步(LLM 段):哨兵档 → 策略师+红队×2;全扫 → 策略师 → L3 → L4(见 SKILL 流程)")
    return results


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="scan 确定性前奏一键化(零 LLM)")
    ap.add_argument("date", help="scan 日 YYYY-MM-DD")
    ap.add_argument("--no-regime-aware", action="store_true", help="关 regime 权重(默认开)")
    ap.add_argument("--skip", default="", help="跳过步骤(逗号分隔:universe,consensus,...)")
    args = ap.parse_args(argv)
    results = run_prelude(args.date, regime_aware=not args.no_regime_aware,
                          skip=tuple(s for s in args.skip.split(",") if s))
    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
