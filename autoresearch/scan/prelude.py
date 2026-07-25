#!/usr/bin/env python3
"""scan-market · 确定性前奏一键化(零 LLM)——开扫前全部确定性步骤一条命令跑完。

design: docs/specs/2026-07-03-scan-run-reliability-design.md §2

首航(07-02)人肉串前奏 ~10 分钟且有漏跑风险;本模块把它收编:
attribution 刷新 → retro pending 列出(**只备料不代跑诊断**)→ consensus 拉(限频容忍)
→ universe(regime-aware 默认开,含影子)→ 日历 → 菜单/预算/哨兵
→ journal + buy_ledger 刷新 → 覆盖池日检(进退复+待建档)。各步 try 包裹失败不阻断,末尾汇总屏。
(观察单日检步骤已退役 —— 用户裁定 fb_20260714_002,别再加回。)

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


def _retro_input_nag(scan_root: Path | str | None = None) -> str:
    """retro_input.md 已备料但未收尾(无 done.json)→ 提醒行(D1 清欠;仿 `assemble._proposals_nag` 语气)。

    比既有 `retro_pending` 步骤(只看"够资格复盘")更进一步的欠账信号:这里专挑"scan-retro 已经
    跑过 write_retro_input 却从没 mark_done"——诊断会话烂尾比"还没开始"更该催办(勘察 D1:
    07-07/07-08 两日就是这个状态)。presence-gated:无 context/scan / 无烂尾日 → ""。
    """
    scan_root = Path(scan_root or "context/scan")
    if not scan_root.exists():
        return ""
    stalled = sorted(p.name for p in scan_root.iterdir()
                     if p.is_dir() and (p / "retro" / "retro_input.md").exists()
                     and not (p / "retro" / "done.json").exists())
    if not stalled:
        return ""
    return ("retro_input 已备料但未收尾(无 done.json):" + "、".join(stalled)
            + " ← scan-retro 诊断烂尾,去补 mark_done 或重跑诊断,别让欠账攒着")


def prewarm_line(date: str, scan_root: Path | str | None = None) -> str:
    """夜间预热是否真跑过(Wave5 ④B:写了没装的优化必须当天可见,不靠事后考古)。"""
    import datetime as _dt
    p = Path(scan_root or "context/scan") / date / "_prewarm.json"
    if not p.is_file():
        return ("预热(夜间):✗ 未跑 —— L0/L1/L2 本次全额取数(~8-10m)。"
                "装载检查:`launchctl list | grep scan-prewarm`")
    ts = _dt.datetime.fromtimestamp(p.stat().st_mtime).strftime("%m-%d %H:%M")
    return f"预热(夜间):✓ 已跑({ts})—— universe/evidence 应全湖命中"


def render_summary(date: str, results: list[dict], scan_root: Path | str | None = None) -> str:
    """prelude 汇总屏文本(纯函数,可单测 + 可落盘)。

    此前这段是 run_prelude 里的一串 print —— 结果被 scan-market.js「只回报 stdout 末 15 行」
    结构性截断(12 步 ✓/✗ + 建议行 + 下一步 > 15 行)。提成纯函数后既能照常打印,也能落盘
    供 workflow 指路、主会话全量转播。
    """
    out = ["═" * 30 + f" prelude 汇总 · {date} " + "═" * 30]
    for r in results:
        mark = "✓" if r["ok"] else "✗"
        out.append(f"  {mark} {r['step']}: {r['note']}")
    out.append(f"  {prewarm_line(date, scan_root)}")
    pend = next((r["note"] for r in results
                 if r["step"] == "retro_pending" and "待诊断" in r["note"]), None)
    if pend:
        out.append(f"  ⚠️  {pend}")
    try:
        stalled = _retro_input_nag()
        if stalled:
            out.append(f"  ⚠️  {stalled}")
    except Exception as e:  # noqa: BLE001 — nag 可选,缺了不挡前奏
        print(f"[prelude] ✗ retro_input_nag: {e}", file=sys.stderr)
    try:
        clines = calib_suggestion_lines()
        if clines:
            out.append("  当日件建议行(📐 贴 _l4_shared_instructions.md;🔁 贴 L3 校准块旁;"
                       "🚪 贴 skeptic/PM 先验;**含「禁注」的行勿贴**):")
            out += [f"    {ln}" for ln in clines]
    except Exception as e:  # noqa: BLE001 — 建议行可选,缺了不挡前奏
        print(f"[prelude] ✗ calib_lines: {e}", file=sys.stderr)
    out.append("  下一步(LLM 段):哨兵档 → 直接 assemble(日历已跑);"
               "全扫 → 策略师 → L3 → L4(见 SKILL 流程)")
    return "\n".join(out)


def write_summary(date: str, results: list[dict],
                  scan_root: Path | str | None = None) -> Path:
    """汇总屏落 `context/scan/<date>/_prelude_summary.md`,返回路径(目录缺则建)。"""
    det = Path(scan_root or "context/scan") / date
    det.mkdir(parents=True, exist_ok=True)
    p = det / "_prelude_summary.md"
    p.write_text(render_summary(date, results, scan_root=scan_root) + "\n", encoding="utf-8")
    return p


def dossier_reconcile_nag(date: str, *, pool_path=None) -> str:
    """池内已建档票缺「季度对账 <period>」痕迹 → 当日件建议行(纯读,可单测)。

    I-1(2026-07-24 终审):`autoresearch.dossier.reconcile` 是手工 CLI,全仓零调用点
    **零提醒** —— 8 月中报季会静默地什么都不发生(FN-1 家族:write_base_rates /
    force_full_card / 权重自动重标定 NO-OP 之后第 N 例)。这里给它一个能被看见的入口。

    period 用 `dossier.mainbz._recent_periods` 同款滞后逻辑取"最近应已披露的报告期"
    (年报 4/30、中报 8/31 披露截止后才算已披露)——该滞后判定本身就是"当前披露窗口"
    的判据。presence-gated:池空 / 无档案 / 未首覆 / 已对账 → ""(不打印)。
    """
    from autoresearch.dossier import delta as _delta, pool as _pool, schema as _schema
    from autoresearch.dossier.mainbz import _recent_periods
    period = _recent_periods(date, 1)[0]
    todo: list[str] = []
    for code, st in sorted(_pool.load_pool(pool_path).get("stocks", {}).items()):
        if st.get("status") != "active":
            continue
        p = _schema.dossier_path(code)
        if not p.exists():                       # 未建档 → 归 pending_init,不催对账
            continue
        text = p.read_text(encoding="utf-8")
        if not _schema.parse_frontmatter(text).get("initiated"):
            continue
        if f"季度对账 {period}" not in _delta.section_body(text, 4):   # §5 风险矩阵
            todo.append(code)
    if not todo:
        return ""
    return (f"📐 季度对账待跑:{len(todo)} 只(period={period})"
            f"→ uv run --no-sync python -m autoresearch.dossier.reconcile {period}")


def dossier_staleness_nag(date: str, *, pool_path=None) -> str:
    """池内已建档票 `last_refresh`(缺退 `initiated`)距 today 超 `schema.STALE_DAYS` 日
    → 当日件陈旧告警;`ref` 存在但格式畸形的票单独标「日期畸形」,不静默跳过(I-1)。

    Task 3(2026-07-24 终审 M-13 + spec 风险节):`last_refresh` 至今零写者零读者,档案
    陈旧目前没有任何探针。与 `dossier_reconcile_nag` 姊妹但判据不同——那个盯"有没有
    做过对账",这个盯"上次全量刷新距今多久"。天数与阈值统一取 `schema.staleness_age`/
    `schema.STALE_DAYS`,不再从 `staleness_issues` 的自然语言文案里反解(I-3,2026-07-24
    终审:反解在文案改动/阈值改动两种变异下都活体存活过,读出垃圾或自相矛盾读数)。
    presence-gated:池空/无档案/未首覆/未超期 → ""(不打印)。
    """
    from autoresearch.dossier import pool as _pool, schema as _schema
    stale: list[str] = []
    for code, st in sorted(_pool.load_pool(pool_path).get("stocks", {}).items()):
        if st.get("status") != "active":
            continue
        p = _schema.dossier_path(code)
        if not p.exists():                       # 未建档 → 归 pending_init,不催陈旧
            continue
        text = p.read_text(encoding="utf-8")
        if not _schema.parse_frontmatter(text).get("initiated"):
            continue
        age = _schema.staleness_age(text, date)
        if age is None:
            if _schema.staleness_issues(text, date):   # ref 存在但畸形,与"两者皆空"区分(I-1)
                stale.append(f"{code}(日期畸形)")
            continue
        if age > _schema.STALE_DAYS:
            stale.append(f"{code}({age}日)")
    if not stale:
        return ""
    return (f"🕰️ 档案陈旧 {len(stale)} 只(>{_schema.STALE_DAYS}日未全量刷新):"
            + "、".join(stale[:6]) + ("…" if len(stale) > 6 else ""))


def _write_t0(scan_dir: Path) -> None:
    """墙钟 t0 标记:mtime 即 stage_timing 的起点锚,内容仅自述。
    已存在不覆盖(prelude-retry/重跑不重置起点);失败不挡 prelude。"""
    try:
        scan_dir.mkdir(parents=True, exist_ok=True)
        fp = scan_dir / "_t0.json"
        if not fp.exists():
            fp.write_text('{"purpose": "stage_timing 起点锚(mtime)"}', encoding="utf-8")
    except Exception:  # noqa: BLE001 — 计时锚可选
        pass


def run_prelude(date: str, regime_aware: bool = True, skip: tuple[str, ...] = ()) -> list[dict]:
    scan_dir = Path("context/scan") / date
    _write_t0(scan_dir)

    def _refresh():
        from autoresearch.learning.retro import refresh_attributions
        done = refresh_attributions()
        return f"刷新 {len(done)} 日" + (f"({'、'.join(done)})" if done else "")

    def _pending():
        from autoresearch.learning.retro import pending_days
        days = pending_days(today=date)
        return ("待诊断 retro 日:" + "、".join(days) + " ← 开扫前先用 scan-retro 补诊断") \
            if days else "无待复盘日"

    def _t1_pending():
        # 快环(T+1 判断层复盘,fb_20260717_001):T 报告真选票 vs T+1 收盘,D+1 即可复盘
        from autoresearch.learning.t1_review import pending_pairs
        pairs = pending_pairs(today=date)
        return ("T+1 快环待复盘:" + "、".join(f"{p['t']}→{p['t1']}" for p in pairs)
                + " ← 最新对跑 t1-review workflow,更早的 backfill") if pairs else "快环无欠账"

    def _learning_health():
        # 学习环健康三查(全只读,子项各自 suppress:一项炸不连坐)
        import contextlib
        lines: list[str] = []
        with contextlib.suppress(Exception):
            from autoresearch.learning.changelog_ledger import heartbeat
            lines.append(heartbeat())
        with contextlib.suppress(Exception):
            from autoresearch.learning.lesson_yield import guard_coverage_line
            lines.append(guard_coverage_line())
        with contextlib.suppress(Exception):
            import autoresearch.learning.feedback_store as fs
            nag = getattr(fs, "proposals_nag_lines", None)   # 看板自清洁(可选,缺=旧版 parity)
            if nag:
                lines.extend(nag(max_lines=3))
        return "\n           ".join(lines) if lines else "(学习环健康查不可用)"

    def _consensus():
        from autoresearch.research.consensus import pull
        pull(date)
        return "一致预期已拉(或今日已有)"

    def _temperature():
        from autoresearch.scan.temperature import rollup
        out = rollup(date, date)
        if not len(out):
            return "无新增(空回填/取数失败·presence-gated,详见 stderr)"
        row = out.iloc[-1]
        return f"score={row['score']} phase={row['phase']}"

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
        import contextlib

        from autoresearch.learning import (
            buy_ledger,
            catalyst_ledger,
            changelog_ledger,
            channel_ledger,
            cross_calib,
            earlystop_ledger,
            gate_ledger,
            journal,
            paper_nav,
            zero_buy_ledger,
        )
        # 九个 ledger 串行但各自 suppress:单点故障不再连坐(改前五个共享一个 try,一炸全炸)。
        # 07-12 D3 清欠:channel/gate/zero_buy/changelog 四个"存在但不会自己长大"的账本纳入白名单
        # (此前只能靠人/Claude 手跑 CLI,见 docs/research/2026-07-12-learning-system-survey.md §1)。
        # watchlist_ledger 随观察单日检退役(fb_20260714_002),模块已于 2026-07-17 删除(P3 清欠)。
        for mod in (journal, buy_ledger, cross_calib, catalyst_ledger, paper_nav,
                    channel_ledger, gate_ledger, zero_buy_ledger, changelog_ledger,
                    earlystop_ledger):
            with contextlib.suppress(Exception):
                mod.main()
        return ("journal + buy_ledger + cross_calib + catalyst + paper_nav + "
                "channel + gate + zero_buy + changelog + earlystop 已刷新")

    def _dossier_pool():
        import contextlib

        from autoresearch.dossier import pool
        out = pool.refresh(date)
        delta = f"进{len(out['entered'])}退{len(out['retired'])}复{len(out['revived'])}"
        pend = out["pending_init"]
        pend_txt = f"待建档 {len(pend)} 只({','.join(pend[:6])})" if pend else "待建档 0"
        moved = out["entered"] or out["retired"] or out["revived"]
        note = f"池 {out['n_active']} active · {delta if moved else '无变动'} · {pend_txt}"
        nag = ""
        with contextlib.suppress(Exception):   # 对账提醒可选,坏档不挡池日检
            nag = dossier_reconcile_nag(date)
        stale = ""
        with contextlib.suppress(Exception):   # 陈旧告警可选,坏档不挡池日检
            stale = dossier_staleness_nag(date)
        extra = " · ".join(x for x in (nag, stale) if x)
        return f"{note} · {extra}" if extra else note

    all_steps = [("retro_refresh", _refresh), ("retro_pending", _pending),
                 ("t1_pending", _t1_pending), ("learning_health", _learning_health),
                 ("consensus", _consensus), ("temperature", _temperature),
                 ("universe", _universe), ("calendar", _calendar),
                 ("catalyst", _catalyst), ("menu", _menu),
                 ("ledgers", _ledgers), ("dossier_pool", _dossier_pool)]
    results = _run_steps([(n, f) for n, f in all_steps if n not in skip])

    # 汇总屏:打印 + 落盘(Wave5 ①)。落盘是为了绕开 scan-market.js「只回报 stdout 末 15 行」
    # 的结构性截断 —— 12 步 ✓/✗ + 建议行 + 下一步放不进 15 行,workflow 改为指路该文件。
    print("\n" + render_summary(date, results))
    import contextlib
    with contextlib.suppress(Exception):      # 落盘可选,写不了不挡前奏(stdout 仍有全文)
        print(f"  (汇总屏已落盘:{write_summary(date, results)})")
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
