#!/usr/bin/env python3
"""跨源核对 CLI —— 同账户、两来源共同覆盖期间内,按自然键三桶对账;**只报不裁**。

design: docs/specs/2026-08-27-broker-trades-ingest-design.md §8

这是「中国结算主干内容未证实」的长期保险丝:每期都跑,一致率掉了立刻可见。有差异不自动裁决,
列出来给人看(哪一边多了什么)。

    uv run --no-sync python -m autoresearch.broker.reconcile [--account tpy|gtht] [--since YYYY-MM-DD] [--root DIR]
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from itertools import combinations
from pathlib import Path

import pandas as pd

from autoresearch.broker import schema, store

_MAX_KEYS = 20


def _keys(df: pd.DataFrame) -> Counter:
    """只对 BUY/SELL 行计桶,键 = `schema.natural_key`(与 merge 同一定义)。"""
    t = df[df["side"].isin(("BUY", "SELL"))]
    return Counter(schema.natural_key(r) for r in t.itertuples(index=False))


def _turnover(df: pd.DataFrame) -> float:
    return float(df.loc[df["side"].isin(("BUY", "SELL")), "amount"].fillna(0).sum())


def compare(a: pd.DataFrame, b: pd.DataFrame) -> dict:
    ka, kb = _keys(a), _keys(b)
    return {
        "both": sum((ka & kb).values()),
        "only_a": sum((ka - kb).values()),
        "only_b": sum((kb - ka).values()),
        "amount_a": _turnover(a),
        "amount_b": _turnover(b),
        "other_a": int((a["side"] == "OTHER").sum()),
        "other_b": int((b["side"] == "OTHER").sum()),
        "only_a_keys": sorted((ka - kb).elements())[:_MAX_KEYS],
        "only_b_keys": sorted((kb - ka).elements())[:_MAX_KEYS],
    }


def report(root=None, *, account: str | None = None, since: str | None = None) -> str:
    raw_dir = store.root_or_default(root) / store.RAW_DIRNAME
    frames = ({p.stem: store.read_raw(p) for p in sorted(raw_dir.glob("*.csv"))}
              if raw_dir.exists() else {})
    frames = {s: f for s, f in frames.items() if len(f)}
    lines = ["[broker·reconcile] 只报不裁(窗口 = 两源成交日交集,按成交日推定、非导出覆盖期;桶只计 BUY/SELL)"]
    if not frames:
        lines.append("  无 raw 数据")
        return "\n".join(lines)
    if since:
        frames = {s: f[f["trade_date"] >= since] for s, f in frames.items()}
    accounts = [account] if account else sorted({a for f in frames.values() for a in f["account"]})
    for acct in accounts:
        per = {s: f[f["account"] == acct] for s, f in frames.items()}
        per = {s: f for s, f in per.items() if len(f)}
        if len(per) < 2:
            lines.append(f"  {acct}:只有 {sorted(per)} 一个来源,无从核对")
            continue
        for sa, sb in combinations(sorted(per), 2):
            lo = max(per[sa]["trade_date"].min(), per[sb]["trade_date"].min())
            hi = min(per[sa]["trade_date"].max(), per[sb]["trade_date"].max())
            if lo > hi:
                lines.append(f"  {acct} {sa}↔{sb}:无重叠期间")
                continue
            a = per[sa][(per[sa]["trade_date"] >= lo) & (per[sa]["trade_date"] <= hi)]
            b = per[sb][(per[sb]["trade_date"] >= lo) & (per[sb]["trade_date"] <= hi)]
            r = compare(a, b)
            lines.append(f"  {acct} {sa}↔{sb} {lo}..{hi}:两边都有 {r['both']} · 仅 {sa} {r['only_a']}"
                         f" · 仅 {sb} {r['only_b']} · 成交额 {r['amount_a']:,.0f} vs {r['amount_b']:,.0f}"
                         f"(差 {r['amount_a'] - r['amount_b']:,.0f})")
            if r["other_a"] or r["other_b"]:
                lines.append(f"      OTHER 行(不入桶):{sa} {r['other_a']} / {sb} {r['other_b']}"
                             "(红利/税/利息/转账;来源结构不同,差异不算不一致)")
            for k in r["only_a_keys"]:
                lines.append(f"      仅 {sa}:{' '.join(k[1:6])}")
            for k in r["only_b_keys"]:
                lines.append(f"      仅 {sb}:{' '.join(k[1:6])}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="券商成交跨源核对(只报不裁;设计稿 2026-08-27 §8)")
    ap.add_argument("--account", choices=schema.ACCOUNTS)
    ap.add_argument("--since", help="只看该日(含)之后")
    ap.add_argument("--root", help="产物根(缺省 workspace.broker_root())")
    args = ap.parse_args(argv)
    since = None
    if args.since:
        since = schema.parse_date(args.since)
        if since is None:
            print(f"[broker·reconcile] --since {args.since!r} 不是日期(要 YYYY-MM-DD)", file=sys.stderr)
            return 2
    text = report(args.root, account=args.account, since=since)
    from autoresearch.trace.operation_evidence import record_operation_evidence

    root = store.root_or_default(args.root)
    raw_dir = root / store.RAW_DIRNAME
    inputs = {
        f"broker.raw.{path.stem}": path
        for path in sorted(raw_dir.glob("*.csv"))
        if path.is_file()
    }
    record_operation_evidence(
        "broker.reconcile",
        parameters={"account": args.account, "since": since},
        inputs=inputs,
        outputs={"broker.reconcile.report": text + "\n"},
        effects=[],
        code_paths=[Path(__file__)],
        evidence_root=root / "_operation_evidence",
    )
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
