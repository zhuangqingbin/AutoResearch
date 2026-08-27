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

import pandas as pd

from autoresearch.broker import schema, store

_MAX_KEYS = 20


def _keys(df: pd.DataFrame) -> Counter:
    return Counter((r.trade_date, r.code, r.side, schema.fmt_num(r.price), schema.fmt_num(r.qty))
                   for r in df.itertuples(index=False))


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
        "only_a_keys": sorted((ka - kb).elements())[:_MAX_KEYS],
        "only_b_keys": sorted((kb - ka).elements())[:_MAX_KEYS],
    }


def report(root=None, *, account: str | None = None, since: str | None = None) -> str:
    raw_dir = store.root_or_default(root) / store.RAW_DIRNAME
    frames = ({p.stem: store.read_raw(p) for p in sorted(raw_dir.glob("*.csv"))}
              if raw_dir.exists() else {})
    frames = {s: f for s, f in frames.items() if len(f)}
    lines = ["[broker·reconcile] 只报不裁"]
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
            for k in r["only_a_keys"]:
                lines.append(f"      仅 {sa}:{' '.join(k)}")
            for k in r["only_b_keys"]:
                lines.append(f"      仅 {sb}:{' '.join(k)}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="券商成交跨源核对(只报不裁;设计稿 2026-08-27 §8)")
    ap.add_argument("--account", choices=schema.ACCOUNTS)
    ap.add_argument("--since", help="只看该日(含)之后")
    ap.add_argument("--root", help="产物根(缺省 workspace.broker_root())")
    args = ap.parse_args(argv)
    print(report(args.root, account=args.account, since=args.since))
    return 0


if __name__ == "__main__":
    sys.exit(main())
