#!/usr/bin/env python3
"""卖方一致预期(tushare report_rc)前向积累 + 盈利修正 Δ(确定性,零 LLM)。

**为什么是前向积累**:report_rc 限频实测为**小时级窗口**(2026-07-02 与 2026-07-10 两轮
实测:同窗二拉/隔 10~15 分钟重试均拒;**报错文案称"1次/分钟"但不可信**)。历史按日回补
**可行**(2026-07-10 探针实证:历史 report_date 能拉到全量行),但只能 `--sleep 3700`
小时级慢灌或跨会话分日续跑(skip-existing 幂等);生产日用(每天 1 拉当日)完全兼容。
故本模块做四件事:`pull` 每日 1 拉入缓存、`backfill` 按交易日回补历史、`status` 看积累
进度、`consensus_delta` 算窗口间 FY EPS 中位修正。

**验证门(纪律,附录 B)**:积累 ≥60 个交易日后跑 factor_lab 风格 IC 验证,
两半样本稳、符号一致才谈入 L1 composite/channel;在那之前**不接线上**。

用法:
  uv run --no-sync python -m autoresearch.research.consensus pull [YYYY-MM-DD]   # 每日 1 拉(scan 前置)
  uv run --no-sync python -m autoresearch.research.consensus status
  uv run --no-sync python -m autoresearch.research.consensus backfill 2026-04-01 2026-07-09 [--max-calls 10 --sleep 3700]
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from autoresearch.common import workspace as ws

_DEFAULT_CACHE = ws.factor_lab_root() / "cache"


def _dir(cache_root: Path | None) -> Path:
    return Path(cache_root or _DEFAULT_CACHE) / "report_rc"


def pull(date: str, cache_root: Path | None = None) -> int:
    """拉 report_date=date 的研报流入缓存(已缓存跳过)。限频 1次/小时 → 每天只该调一次。"""
    d = date.replace("-", "")
    fp = _dir(cache_root) / f"{d}.pkl"
    if fp.exists():
        print(f"[consensus] {d} 已缓存,跳过")
        return 0
    import autoresearch.research.factor_lab as fl
    from autoresearch.data.tushare_source import _ts_call
    pro = fl._pro()
    df = _ts_call(lambda: pro.report_rc(report_date=d))
    fp.parent.mkdir(parents=True, exist_ok=True)
    pd.to_pickle(df, fp)
    print(f"[consensus] {d} rows={len(df)}")
    return len(df)


def _load_span(span: tuple[str, str], cache_root: Path | None) -> pd.DataFrame:
    root = _dir(cache_root)
    frames = []
    if root.exists():
        for fp in sorted(root.glob("*.pkl")):
            if span[0] <= fp.stem <= span[1]:
                df = pd.read_pickle(fp)
                if df is not None and len(df):
                    frames.append(df)
    if not frames:
        return pd.DataFrame(columns=["ts_code", "quarter", "eps"])
    return pd.concat(frames, ignore_index=True)


def _median_eps(df: pd.DataFrame, fy: str) -> pd.Series:
    """窗口内 FY 年度预测(quarter=<fy>Q4)的每股收益中位(逐股)。"""
    sub = df[df["quarter"].astype(str) == f"{fy}Q4"].copy()
    sub["code"] = sub["ts_code"].astype(str).str[:6]
    sub["eps"] = pd.to_numeric(sub["eps"], errors="coerce")
    sub = sub.dropna(subset=["eps"])
    return sub.groupby("code")["eps"].median()


def consensus_delta(date: str, old_span: tuple[str, str], new_span: tuple[str, str],
                    fy: str, cache_root: Path | None = None) -> pd.DataFrame:
    """两个窗口的 FY EPS 中位对比 → [code, eps_old, eps_new, eps_delta_pct]。

    覆盖稀疏是常态(卖方只覆盖热门票)→ 只对两窗都有覆盖的股票出 Δ;分母≤0 → NaN。
    """
    old = _median_eps(_load_span(old_span, cache_root), fy)
    new = _median_eps(_load_span(new_span, cache_root), fy)
    codes = sorted(set(old.index) & set(new.index))
    rows = []
    for c in codes:
        eo, en = float(old[c]), float(new[c])
        pct = (en / eo - 1.0) * 100.0 if eo > 0 else float("nan")
        rows.append({"code": c, "eps_old": eo, "eps_new": en, "eps_delta_pct": pct})
    return pd.DataFrame(rows, columns=["code", "eps_old", "eps_new", "eps_delta_pct"])


def status(cache_root: Path | None = None) -> dict:
    root = _dir(cache_root)
    files = sorted(root.glob("*.pkl")) if root.exists() else []
    stocks: set[str] = set()
    for fp in files:
        df = pd.read_pickle(fp)
        if df is not None and len(df) and "ts_code" in df.columns:
            stocks |= set(df["ts_code"].astype(str).str[:6])
    return {"n_days": len(files), "first": files[0].stem if files else None,
            "last": files[-1].stem if files else None, "n_stocks": len(stocks),
            "gate": "≥60 日后 factor_lab 验 IC(两半稳+符号一致)再谈入 composite"}


def backfill(start: str, end: str, cache_root: Path | None = None,
             max_calls: int | None = None, sleep_s: float = 0.0,
             pull_fn=None, days_fn=None) -> dict:
    """按交易日回补 report_rc 缓存(skip-existing → 幂等,可反复续跑)。

    2026-07-10 探针裁决"历史按日回补是否可行"(见 progress.md);限频应对:
    `--max-calls` 分片 + `--sleep` 节流;撞异常打印续跑提示后停,已落缓存不丢。
    """
    _pull = pull_fn or pull
    if days_fn is not None:
        days = days_fn(start, end)
    else:
        import autoresearch.research.factor_lab as fl
        from autoresearch.data.tushare_source import _trade_days
        days = _trade_days(fl._pro(), start.replace("-", ""), end.replace("-", ""))
    root = _dir(cache_root)
    pulled = skipped = 0
    stopped_by = None
    for d in days:
        d8 = str(d).replace("-", "")
        if (root / f"{d8}.pkl").exists():
            skipped += 1
            continue
        if max_calls is not None and pulled >= max_calls:
            stopped_by = "max_calls"
            break
        try:
            _pull(f"{d8[:4]}-{d8[4:6]}-{d8[6:]}", cache_root)
        except Exception as e:  # noqa: BLE001 — 限频/网络:停下可续跑
            stopped_by = f"error: {e}"
            print(f"[consensus] {d8} 拉取失败({e})→ 停;已缓存不丢,续跑同命令即可")
            break
        pulled += 1
        if sleep_s:
            import time
            time.sleep(sleep_s)
    print(f"[consensus] backfill: +{pulled} pulled, {skipped} skipped"
          + (f", stopped_by={stopped_by}" if stopped_by else ""))
    return {"pulled": pulled, "skipped": skipped, "stopped_by": stopped_by}


# ───────────────────────── 自动预注册触发(design 2026-08-03 §4.5)─────────────────────────
#
# 设计稿写死的触发条款:`n≥60 且 两半 IC 同号 且 |IC|>0.02` → 自动生成 PREREGISTERED spec
# (**人批才往前走**)。自动腿断言:status 输出的 n 必须**周周增长**。
#
# 为什么要那条断言:§0.3-4 的判例是「权重自动重标定连续 4 次 NO-OP,闭环唯一自动腿空转
# 两周无人察觉」。自动的腿必须有一个**会变的量**做断言,否则它死了也像活着。

PREREG_MIN_DAYS = 60
PREREG_MIN_ABS_IC = 0.02
GROWTH_WINDOW_DAYS = 7          # n 必须在这个窗口内增长
_HISTORY_NAME = "_status_history.jsonl"


def _history_path(cache_root: Path | None) -> Path:
    return _dir(cache_root).parent / _HISTORY_NAME


def record_status(today: str, cache_root: Path | None = None) -> dict:
    """把当日 status 快照追加进历史 —— `assert_still_growing` 的唯一数据源。"""
    snap = {"date": str(today)[:10], **{k: v for k, v in status(cache_root).items()
                                        if k in ("n_days", "n_stocks", "last")}}
    path = _history_path(cache_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(snap, ensure_ascii=False) + "\n")
    return snap


def read_status_history(cache_root: Path | None = None) -> list[dict]:
    path = _history_path(cache_root)
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def assert_still_growing(cache_root: Path | None = None, *,
                         window_days: int = GROWTH_WINDOW_DAYS) -> dict:
    """自动腿断言:`n_days` 必须**周周增长**。不增长 = 这条腿死了。

    返回 `{status, ...}`;`status ∈ {GROWING, STALLED, INSUFFICIENT_HISTORY}`。
    **不抛异常** —— 它是探针不是闸;但 `STALLED` 必须被渲染出来,否则又是一次空转两周。
    """
    history = read_status_history(cache_root)
    if len(history) < 2:
        return {"status": "INSUFFICIENT_HISTORY", "n_snapshots": len(history),
                "note": "历史不足两点 —— 还判不出增长与否(不是「没增长」)"}
    latest = history[-1]
    cutoff = str(latest.get("date", ""))
    earlier = [h for h in history[:-1] if str(h.get("date", "")) < cutoff]
    if not earlier:
        return {"status": "INSUFFICIENT_HISTORY", "n_snapshots": len(history),
                "note": "所有快照同日 —— 判不出跨日增长"}
    baseline = earlier[-min(len(earlier), window_days)]
    delta = int(latest.get("n_days", 0)) - int(baseline.get("n_days", 0))
    return {
        "status": "GROWING" if delta > 0 else "STALLED",
        "n_now": int(latest.get("n_days", 0)),
        "n_before": int(baseline.get("n_days", 0)),
        "delta": delta,
        "window_days": window_days,
        "note": ("" if delta > 0 else
                 "🚨 n 未增长 —— 这条自动腿可能已经死了(判例:权重重标定连续 4 次 "
                 "NO-OP,空转两周无人察觉)"),
    }


def prereg_trigger(ic_full: float | None, ic_first_half: float | None,
                   ic_second_half: float | None,
                   cache_root: Path | None = None) -> dict:
    """触发条款(写死,不可临场放宽):`n≥60 ∧ 两半 IC 同号 ∧ |IC|>0.02`。

    任一条不满足 → `HOLD` 并列出缺哪一条。满足 → `TRIGGERED`,由 `build_spec` 生成
    **PREREGISTERED** spec;**人批才往前走**(注册 ≠ 激活)。
    """
    n_days = int(status(cache_root).get("n_days", 0))
    reasons: list[str] = []
    if n_days < PREREG_MIN_DAYS:
        reasons.append(f"n_days {n_days} < {PREREG_MIN_DAYS}")
    if ic_full is None or ic_first_half is None or ic_second_half is None:
        reasons.append("IC 缺失(全样本/两半任一为空)")
    else:
        if abs(float(ic_full)) <= PREREG_MIN_ABS_IC:
            reasons.append(f"|IC| {abs(float(ic_full)):.4f} ≤ {PREREG_MIN_ABS_IC}")
        if float(ic_first_half) * float(ic_second_half) <= 0:
            reasons.append(f"两半 IC 反号({ic_first_half:+.4f} / {ic_second_half:+.4f})")
    growth = assert_still_growing(cache_root)
    return {
        "status": "HOLD" if reasons else "TRIGGERED",
        "n_days": n_days, "ic_full": ic_full,
        "ic_halves": [ic_first_half, ic_second_half],
        "unmet": reasons,
        "conditions": {"min_days": PREREG_MIN_DAYS, "min_abs_ic": PREREG_MIN_ABS_IC,
                       "halves_same_sign": True},
        "growth_assertion": growth,
        "human_approval_required": True,
        "note": "触发只生成 PREREGISTERED spec —— 注册 ≠ 激活,人批才往前走",
    }


def build_spec(trigger: dict, *, start: str, expires: str) -> dict:
    """触发结果 → registry spec(统一实验模板驱动)。`HOLD` 时抛错,不生成半个 spec。"""
    from autoresearch.common.ruler import MAIN_RULER
    from autoresearch.learning import experiment_registry as reg, experiment_template as et

    if trigger["status"] != "TRIGGERED":
        raise ValueError(f"未触发({trigger['unmet']})—— 不生成 spec")
    template = et.ExperimentTemplate(
        experiment_id="consensus_eps_revision",
        h0=f"卖方一致预期修正对主尺 {MAIN_RULER} 无增量",
        h1=f"卖方一致预期修正对主尺 {MAIN_RULER} 有正增量",
        data_cutoff="report_rc 按 report_date 滚动;只用已积累的交易日",
        paired_unit="scan_day", clustering="date_cluster",
        min_units=PREREG_MIN_DAYS, target_power=0.8,
        primary_metric="consensus_ic_delta", direction=et.HIGHER_IS_BETTER,
        equivalence_margin=PREREG_MIN_ABS_IC, no_harm=False,
        multiple_testing="single_hypothesis", n_hypotheses=1,
        stopping_rule=f"固定 ≥{PREREG_MIN_DAYS} 个交易日;不可提前停",
        rollback="未接线 —— 停止实验即回到现状",
        motivation="前向积累已达触发条款(n≥60 ∧ 两半同号 ∧ |IC|>0.02)")
    definition = et.to_registry_definition(template)
    return {
        "id": f"exp_{start.replace('-', '')}_consensus_eps_revision",
        "title": template.h1, "trial_family": "l1_composite_factor",
        "definition": definition, "start_date": start, "expires_date": expires,
        "primary_metric": template.primary_metric,
        "promotion_guards": {d: [{"metric": template.primary_metric, "op": "gt",
                                  "value": PREREG_MIN_ABS_IC}]
                             for d in reg.GUARD_DOMAINS},
        "rollback_guards": {d: [{"metric": template.primary_metric, "op": "gt",
                                 "value": -1.0}] for d in reg.GUARD_DOMAINS},
        "challenger_pointer": {"kind": "shadow_factor",
                               "pointer": "consensus:eps_revision",
                               "content_hash": reg.canonical_hash(definition)},
        "minimums": et.minimums_for(template), "rollback_window_runs": 5,
    }


def render_trigger(trigger: dict) -> str:
    lines = [f"# consensus 预注册触发 —— **{trigger['status']}**", "",
             f"- n_days {trigger['n_days']} · IC {trigger['ic_full']} · "
             f"两半 {trigger['ic_halves']}",
             f"- 条款:{trigger['conditions']}"]
    if trigger["unmet"]:
        lines += ["- 未满足:"] + [f"  - {r}" for r in trigger["unmet"]]
    growth = trigger["growth_assertion"]
    lines += ["", f"- 自动腿断言:**{growth['status']}**"
              + (f"(n {growth.get('n_before')}→{growth.get('n_now')})"
                 if "n_now" in growth else ""),
              f"  {growth.get('note') or ''}",
              "", f"> {trigger['note']}", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    import argparse
    from datetime import date as _date
    ap = argparse.ArgumentParser(description="卖方一致预期前向积累(report_rc)")
    ap.add_argument("mode", choices=["pull", "status", "backfill", "trigger"])
    ap.add_argument("date", nargs="?", help="pull 的日期 / backfill 的 start(YYYY-MM-DD)")
    ap.add_argument("end", nargs="?", help="backfill 的 end(YYYY-MM-DD)")
    ap.add_argument("--max-calls", type=int, default=None)
    ap.add_argument("--sleep", type=float, default=0.0)
    args = ap.parse_args(argv)
    if args.mode == "pull":
        pull(args.date or _date.today().isoformat())
        record_status(args.date or _date.today().isoformat())   # 自动腿断言的数据源
    elif args.mode == "backfill":
        if not (args.date and args.end):
            ap.error("backfill 需要 start end 两个日期")
        backfill(args.date, args.end, max_calls=args.max_calls, sleep_s=args.sleep)
    elif args.mode == "trigger":
        record_status(args.date or _date.today().isoformat())
        # IC 由 factor_lab 提供;未接线时传 None → 必然 HOLD(缺 IC 也是未满足条款之一)
        print(render_trigger(prereg_trigger(None, None, None)))
    else:
        print(status())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
