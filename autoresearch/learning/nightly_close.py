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
        """已成熟未归因日 → 逐日 attribute + write_retro_input(纯计算,幂等)。

        **两步必须成对**:`write_retro_input` 吃的是 `attribute()` 的**内存帧**(CSV 落盘时
        丢了 `tradable` 等派生列,从 CSV 重读会 KeyError)。首版只跑了 attribute,备料做
        一半 —— 人第二天打开 scan-retro 才发现 retro_input.md 不在,等于自动化只省了半步。
        诊断叙事仍归 scan-retro(LLM 段),这里只把它的输入备齐。
        """
        from autoresearch.learning import retro
        days = retro.pending_days(today) or []
        done = []
        for d in days:
            with contextlib.suppress(Exception):   # 单日失败不拖累其余日
                retro.write_retro_input(d, retro.attribute(d))
                done.append(d)
        return (f"归因+备料 {len(done)}/{len(days)} 日({'、'.join(done) or '—'})"
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

    def _t1_gap_finalize() -> str:
        """D+2 晚:隔夜 gap 终判回填(2026-08-05 用户裁定,对外准不准口径 = gap)。

        `gap_finalize_pending` 内部逐日独立 try/except(取数+计算+写盘整段纳入同一
        per-day 边界),本函数不需要像 `_t1_backfill` 那样外层再循环一次;但仍要把它
        返回的失败日名单如实拼进汇总行(不能让「补了几日就断了」悄悄消失)。
        """
        from autoresearch.learning import t1_review
        n, failed = t1_review.gap_finalize_pending(today)
        if not n and not failed:
            return "无待终判日"
        note = f"gap 终判回填 {n} 日"
        if failed:
            note += f";{len(failed)} 日失败({'、'.join(failed)})"
        return note

    def _tripwire() -> str:
        from autoresearch.learning.tripwire_watch import check
        hits = check(today)
        return f"⚡ {len(hits)} 条触发" if hits else "无触发"

    def _ledgers() -> str:
        import importlib
        # 序有意义:gate_attribution 先于 gate_ledger(后者渲染前者的 v3 分布);
        # evidence_manifest 收尾(它读所有账本,必须在它们刷新之后)。
        # ⚠️ 动态调用面:本表以字符串拼名 import —— 删任何 learning 模块前先查这张表(2026-08-06 D3 勘误教训)
        names = ["journal", "buy_ledger", "cross_calib", "catalyst_ledger", "paper_nav",
                 "channel_ledger", "gate_attribution",
                 # Wave12-T11:shadow_buys(近 miss「差一点」节的数据源)此前不在这张表——
                 # 它的唯一写入路径是 publisher.py 的 is_real 门控块,失败即被
                 # contextlib.suppress 静默吞掉、无补救,是该节 5/6 run 静默缺席的根因
                 # (docs/specs/2026-08-08-wave12-seven-topics-design.md)。main()=backfill()
                 # 幂等补全部历史 scan 日,补在这里当夜间兜底重跑。
                 "shadow_buys",
                 "gate_ledger", "zero_buy_ledger",
                 "changelog_ledger", "earlystop_ledger", "pinned_ledger",
                 # Wave10:哨兵校准(A12/C4)与 L3→L4 对齐(C2.1)都是纯读账本,
                 # 必须排在 gate_attribution 之后(对齐账本读它的 participation)。
                 "sentinel_audit", "l3_l4_alignment",
                 # 下一波(2026-08-03):门重标定影子账本与 L3 边际价值。两者都**只读**
                 # 既有产物、只写自己的报表,排在 gate_attribution 之后(gate_recal 读它)。
                 "gate_recal", "l3_marginal",
                 "evidence_manifest"]
        ok = 0
        for n in names:
            with contextlib.suppress(Exception):
                importlib.import_module(f"autoresearch.learning.{n}").main()
                ok += 1
        # B5 判据(§B5):结构失败账本住在 scan 侧(它读 task-book,不读学习账本),
        # 但只有夜间踢它才有连续读数 —— 靠人回忆的判据等于没判据。
        # 下一波:L2 winner-capture SLO 同理(scan 侧、纯读产物、需要连续读数才有报警线)。
        scan_side = [("autoresearch.scan.structural_audit", []),
                     ("autoresearch.scan.l2_slo", [])]
        total = len(names) + len(scan_side)
        for module, argv in scan_side:
            with contextlib.suppress(Exception):
                importlib.import_module(module).main(argv)
                ok += 1
        return f"{ok}/{total} 刷新"

    for name, fn in (("retro_refresh", _retro_refresh), ("t1_backfill", _t1_backfill),
                     ("t1_gap_finalize", _t1_gap_finalize),
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
