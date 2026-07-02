#!/usr/bin/env python3
"""卖方一致预期(tushare report_rc)前向积累 + 盈利修正 Δ(确定性,零 LLM)。

**为什么是前向积累**:report_rc 限频 **1次/小时**(2026-07-02 实测)→ 历史按日回补
(~282 天)不可行;但生产日用(每天 1 拉当日研报流)完全兼容。故本模块只做三件事:
`pull` 每日 1 拉入缓存、`status` 看积累进度、`consensus_delta` 算窗口间 FY EPS 中位修正。

**验证门(纪律,附录 B)**:积累 ≥60 个交易日后跑 factor_lab 风格 IC 验证,
两半样本稳、符号一致才谈入 L1 composite/channel;在那之前**不接线上**。

用法:
  uv run --no-sync python -m autoresearch.research.consensus pull [YYYY-MM-DD]   # 每日 1 拉(scan 前置)
  uv run --no-sync python -m autoresearch.research.consensus status
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


def main(argv: list[str] | None = None) -> int:
    import argparse
    from datetime import date as _date
    ap = argparse.ArgumentParser(description="卖方一致预期前向积累(report_rc,限频 1/h)")
    ap.add_argument("mode", choices=["pull", "status"])
    ap.add_argument("date", nargs="?", help="pull 的日期 YYYY-MM-DD(缺省=今天)")
    args = ap.parse_args(argv)
    if args.mode == "pull":
        pull(args.date or _date.today().isoformat())
    else:
        print(status())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
