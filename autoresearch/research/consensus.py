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

from pathlib import Path

import pandas as pd

_DEFAULT_CACHE = Path("context/factor_lab/cache")


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


def main(argv: list[str] | None = None) -> int:
    import argparse
    from datetime import date as _date
    ap = argparse.ArgumentParser(description="卖方一致预期前向积累(report_rc)")
    ap.add_argument("mode", choices=["pull", "status", "backfill"])
    ap.add_argument("date", nargs="?", help="pull 的日期 / backfill 的 start(YYYY-MM-DD)")
    ap.add_argument("end", nargs="?", help="backfill 的 end(YYYY-MM-DD)")
    ap.add_argument("--max-calls", type=int, default=None)
    ap.add_argument("--sleep", type=float, default=0.0)
    args = ap.parse_args(argv)
    if args.mode == "pull":
        pull(args.date or _date.today().isoformat())
    elif args.mode == "backfill":
        if not (args.date and args.end):
            ap.error("backfill 需要 start end 两个日期")
        backfill(args.date, args.end, max_calls=args.max_calls, sleep_s=args.sleep)
    else:
        print(status())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
