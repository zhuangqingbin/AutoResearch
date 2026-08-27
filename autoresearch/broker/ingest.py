#!/usr/bin/env python3
"""券商成交导入 CLI —— 文件 → adapter → 归一 → 两级契约 → raw 幂等落盘 → 重建 trades.csv → 摘要屏。

design: docs/specs/2026-08-27-broker-trades-ingest-design.md §11

    uv run --no-sync python -m autoresearch.broker.ingest <文件|目录>... [--account tpy|gtht]
        [--source chinaclear|gtht|tpy|screenshot] [--force] [--dry-run] [--root DIR]

来源缺省按父目录名识别(inbox/<src>/…)。A 级违约的文件**整份拒收、不落 raw**,但记进 ingest_log
(status=rejected)并继续处理其余文件;退出码 1。`DataContractError` 只在这里被按文件捕获,
从不静默:打印 + 日志 + 非零退出。
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from autoresearch.broker import adapters, schema, store
from autoresearch.data.contracts import DataContractError

_SKIP_NAMES = {".DS_Store"}


def iter_files(paths) -> list[Path]:
    out: list[Path] = []
    for p in paths:
        p = Path(p)
        if p.is_dir():
            out.extend(q for q in sorted(p.rglob("*"))
                       if q.is_file() and not q.name.startswith(".") and q.name not in _SKIP_NAMES)
        elif p.is_file():
            out.append(p)
        else:
            raise FileNotFoundError(f"{p} 不存在")
    return out


def ingest_file(path: Path, *, source_kind: str, account: str | None, root, force: bool,
                dry_run: bool, today: date | None = None, now: str | None = None) -> dict:
    now = now or datetime.now().isoformat(timespec="seconds")
    sha = store.sha256_of(path)
    entry: dict = {"at": now, "file": path.name, "sha256": sha, "source_kind": source_kind,
                   "status": "ok"}
    if not force and sha in store.ingested_shas(root):
        entry["status"] = "skipped"
        return entry
    try:
        raw = adapters.parse(path, source_kind, account=account)
        df = schema.normalize(raw, source_kind=source_kind, source_file=path.name, ingested_at=now)
        rep = schema.validate(df, today=today)
    except DataContractError as e:
        entry.update(status="rejected", a_error=str(e))
        print(f"[broker] ✗ 拒收 {path.name}:{e}", file=sys.stderr)
        if not dry_run:
            store.append_log(entry, root)
        return entry
    entry.update(accounts=list(rep.accounts), period=list(rep.period or ()), rows=rep.rows,
                 b_degradations=rep.b_degradations, warnings=rep.warnings)
    if dry_run:
        entry["status"] = "dry-run"
        return entry
    n_new, n_dup = store.upsert_raw(df, root)
    entry.update(new=n_new, dup=n_dup)
    store.append_log(entry, root)
    return entry


def _fmt_money(v: float) -> str:
    return f"{v:,.0f}"


def render_summary(trades: pd.DataFrame | None, entries: list[dict]) -> str:
    n = {s: sum(1 for e in entries if e["status"] == s)
         for s in ("ok", "skipped", "rejected", "dry-run")}
    lines = [f"[broker] 文件 {len(entries)} · 新导入 {n['ok']} · 已导入跳过 {n['skipped']}"
             f" · 拒收 {n['rejected']} · 试跑 {n['dry-run']}"]
    for e in entries:
        if e["status"] == "rejected":
            lines.append(f"  ✗ 拒收 {e['file']}:{str(e.get('a_error', '')).splitlines()[0]}")
        elif e["status"] == "dry-run":
            lines.append(f"  ○ 试跑 {e['file']}:{e.get('rows', 0)} 行 · 账户 "
                         f"{'/'.join(e.get('accounts', []))} · B降级 {e.get('b_degradations') or '无'}"
                         "(未写盘)")
    if trades is None:
        return "\n".join(lines)
    degr: dict[str, dict[str, int]] = {}
    for e in entries:
        if e["status"] == "ok":
            for acct in e.get("accounts", []):
                for reason, k in (e.get("b_degradations") or {}).items():
                    degr.setdefault(acct, {})[reason] = degr.get(acct, {}).get(reason, 0) + k
    for acct, g in trades.groupby("account", sort=True):
        is_trade = g["side"].isin(("BUY", "SELL"))
        counts = {s: int((g["side"] == s).sum()) for s in schema.SIDES}
        turnover = float(g.loc[is_trade, "amount"].fillna(0).sum())
        fees = float(g.loc[is_trade, list(schema.FEE_COLUMNS)].fillna(0).sum().sum())
        d = degr.get(acct, {})
        dtxt = (f"{sum(d.values())}(" + ", ".join(f"{r} ×{k}" for r, k in d.items()) + ")"
                if d else "0")
        lines.append(f"  {acct:<5} {g['trade_date'].min()}..{g['trade_date'].max()}  "
                     f"BUY {counts['BUY']} / SELL {counts['SELL']} / OTHER {counts['OTHER']}   "
                     f"成交额 {_fmt_money(turnover)}  费用 {_fmt_money(fees)}  A违规 0  B降级 {dtxt}")
    multi = int(trades["sources"].astype(str).str.contains(r"\+").sum()) if len(trades) else 0
    lines.append(f"trades.csv 重建:{len(trades)} 行 · 多源匹配 {multi} 行 · 单源 {len(trades) - multi} 行")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="券商成交导入(确定性、零 LLM;设计稿 2026-08-27)")
    ap.add_argument("paths", nargs="+", help="文件或目录(目录递归;来源按父目录名 inbox/<src>/ 识别)")
    ap.add_argument("--account", choices=schema.ACCOUNTS, help="券商源/截图源的账户别名")
    ap.add_argument("--source", choices=schema.SOURCE_KINDS, help="强制指定来源类型")
    ap.add_argument("--force", action="store_true", help="已导入过的文件也重新解析(仍按 row_hash 去重)")
    ap.add_argument("--dry-run", action="store_true", help="只解析+契约,不写任何文件")
    ap.add_argument("--root", help="产物根(缺省 workspace.broker_root())")
    args = ap.parse_args(argv)
    try:
        files = iter_files(args.paths)
    except FileNotFoundError as e:
        print(f"[broker] {e}", file=sys.stderr)
        return 2
    entries: list[dict] = []
    for f in files:
        kind = args.source or adapters.detect_source_kind(f)
        if kind is None:
            print(f"[broker] {f}:无法识别来源(父目录须为 {'/'.join(schema.SOURCE_KINDS)} 之一,"
                  "或传 --source)", file=sys.stderr)
            return 2
        try:
            entries.append(ingest_file(f, source_kind=kind, account=args.account, root=args.root,
                                       force=args.force, dry_run=args.dry_run))
        except ValueError as e:
            print(f"[broker] {f}:{e}", file=sys.stderr)
            return 2
    trades = None if args.dry_run else store.merge(args.root)
    print(render_summary(trades, entries))
    return 1 if any(e["status"] == "rejected" for e in entries) else 0


if __name__ == "__main__":
    sys.exit(main())
