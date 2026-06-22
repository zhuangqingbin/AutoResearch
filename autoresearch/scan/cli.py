#!/usr/bin/env python3
"""scan-market 统一 CLI —— `run` / `capture` / `check` 三子命令。

design: docs/specs/2026-06-22-autoresearch-arch-redesign-design.md §A(CLI)/§E(parity)。

子命令:
  run <date>      → 跑确定性漏斗(L0→L1→L2)。**双产出**:
                    ① 旧 staging `context/scan/<date>/*.csv`(L3/L4/L5 下游照旧读)——
                       由 `autoresearch.scan.universe.run` 逐值写出(行为保持)。
                    ② typed trace(`reports/scan/<run_id>/`)——由 `autoresearch.scan.pipeline.Pipeline`
                       在同一 lake/weights/champion 上重跑写出。golden-parity 锁死两路 ≡。
  capture <date>  → 把现 `universe.run` 的确定性产物快照成 golden(对拍基线)。`autoresearch.scan.parity.capture`。
  check <date>    → 跑新 Pipeline 与 golden 对拍(集合 + 名次 + composite 1e-9)。`autoresearch.scan.parity.check`。

用法:
  uv run --no-sync python -m autoresearch.scan run 2026-06-20 [--recall-n 1000 --l2-n 200 \
      --cap-floor 30 --source tushare --exclude-bj]
  uv run --no-sync python -m autoresearch.scan capture 2026-06-20 --golden context/scan/golden/2026-06-20
  uv run --no-sync python -m autoresearch.scan check   2026-06-20 --golden context/scan/golden/2026-06-20
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from autoresearch.scan.config import ScanConfig
from autoresearch.scan.context import RunContext
from autoresearch.scan.pipeline import Pipeline


def _config_from_args(args: argparse.Namespace) -> ScanConfig:
    """把 CLI flags 收成 ScanConfig(与 universe.run 默认对齐)。"""
    return ScanConfig(
        recall_n=args.recall_n,
        l2_n=args.l2_n,
        cap_floor=args.cap_floor,
        include_bj=not args.exclude_bj,
        source=args.source,
        recall_mode=args.recall_mode,
        recall_channels=(args.recall_channels.split(",") if args.recall_channels else None),
    )


def cmd_run(args: argparse.Namespace) -> int:
    """跑确定性漏斗:旧 staging(universe.run)+ typed trace(Pipeline),同一份 universe。

    先 `universe.run` 写旧 staging（`context/scan/<date>/*.csv`，下游 L3/L4/L5 读的就是它）；
    再 `Pipeline.run` 写 typed trace（`reports/scan/<run_id>/`）。golden-parity 已锁死两路逐值一致。
    """
    analysis_date = args.date or date.today().isoformat()
    cfg = _config_from_args(args)

    # ① 旧 staging（行为保持）：context/scan/<date>/{L1_recall_top1000,L1_scored_full,
    #    L2_gbdt_top200,sectors}.csv + meta.json —— 下游 L3/L4/L5 照旧读这套。
    from autoresearch.scan import universe as smu
    res = smu.run(analysis_date, cap_floor_yi=cfg.cap_floor, include_bj=cfg.include_bj,
                  recall_n=cfg.recall_n, l2_n=cfg.l2_n, source=cfg.source)

    # ② typed trace：同一 lake/weights/champion 上跑新 Pipeline → reports/scan/<run_id>/。
    ctx = RunContext(analysis_date=analysis_date, config=cfg)
    run_id = Pipeline().run(ctx)

    print(f"\nL0 universe={res['universe']} → 轻门 {res['after_gate_a']} → 召回 top{res['recall_n']} "
          f"→ L2 {res['l2_engine']} top{res['l2_n']} (板块概览 {res['sectors']} 个)"
          f"\n→ staging: {res['outdir']}/L2_gbdt_top200.csv"
          f"\n→ trace:   {ctx.trace.run_dir(run_id)}  (run_id={run_id})")
    return 0


def cmd_capture(args: argparse.Namespace) -> int:
    """把现 universe.run 的确定性产物快照成 golden(对拍基线)。"""
    from autoresearch.scan import parity
    analysis_date = args.date or date.today().isoformat()
    cfg = _config_from_args(args)
    golden = Path(args.golden) if args.golden else Path("context/scan/golden") / analysis_date
    paths = parity.capture(analysis_date, golden, config=cfg)
    print(f"[capture] golden → {golden}")
    for name, p in paths.items():
        print(f"  - {name}: {p}")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    """跑新 Pipeline 与 golden 对拍;diff 非空 → 返回码 1。"""
    from autoresearch.scan import parity
    analysis_date = args.date or date.today().isoformat()
    cfg = _config_from_args(args)
    golden = Path(args.golden) if args.golden else Path("context/scan/golden") / analysis_date
    res = parity.check(analysis_date, golden, config=cfg)
    print(res.summary())
    for k in ("l1_set_diff", "l1_order_diff", "l1_composite_diff", "l2_set_diff", "l2_order_diff"):
        diff = getattr(res, k)
        if diff:
            print(f"  {k}: {diff[:20]}", file=sys.stderr)
    return 0 if res.ok else 1


def _add_common_funnel_flags(p: argparse.ArgumentParser) -> None:
    """漏斗口径 flags(run/capture/check 共用,与 universe CLI 默认逐一对齐)。"""
    p.add_argument("date", nargs="?", help="分析日 YYYY-MM-DD(缺省=今天)")
    p.add_argument("--recall-n", type=int, default=1000, help="召回数(复合分 top N),默认 1000")
    p.add_argument("--l2-n", type=int, default=200, help="L2 粗排数(champion 重排 top N),默认 200")
    p.add_argument("--cap-floor", type=float, default=30.0, help="市值地板(亿),默认 30")
    p.add_argument("--exclude-bj", action="store_true", help="排除北交所(默认纳入)")
    p.add_argument("--source", choices=["em", "tushare"], default="tushare",
                   help="universe 取数源:tushare=默认(push2 常被封);em=东财 push2")
    p.add_argument("--recall-mode", choices=["multi", "composite"], default="multi",
                   help="L1 召回:multi=多路策略召回(默认)| composite=单复合分(对拍/回退)")
    p.add_argument("--recall-channels", default=None,
                   help="启用的 channel 子集(逗号分隔;缺省=全 8 路)")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="python -m autoresearch.scan",
        description="scan-market 统一 CLI:run(漏斗)/ capture(快照 golden)/ check(对拍)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="跑确定性漏斗 L0→L1→L2(旧 staging + typed trace)")
    _add_common_funnel_flags(p_run)
    p_run.set_defaults(func=cmd_run)

    p_cap = sub.add_parser("capture", help="把现 universe.run 产物快照成 golden(对拍基线)")
    _add_common_funnel_flags(p_cap)
    p_cap.add_argument("--golden", default=None, help="golden 输出目录(缺省 context/scan/golden/<date>)")
    p_cap.set_defaults(func=cmd_capture)

    p_chk = sub.add_parser("check", help="跑新 Pipeline 与 golden 对拍(集合 + 名次 + composite)")
    _add_common_funnel_flags(p_chk)
    p_chk.add_argument("--golden", default=None, help="golden 目录(缺省 context/scan/golden/<date>)")
    p_chk.set_defaults(func=cmd_check)

    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
