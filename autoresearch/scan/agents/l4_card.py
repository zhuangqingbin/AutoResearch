#!/usr/bin/env python3
"""Compatibility adapter for L4 deterministic services.

Implementations live in :mod:`autoresearch.scan.l4`; this module preserves the
historical import path and CLI.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from autoresearch.scan.l4.context import (
    _BASE_RATE_THIN_N,
    _base_rate_mark,
    _cat_mark,
    _dist_mark,
    _dossier_summary_mark,
    _dossier_summary_text,
    _fund_mark,
    _inst_mark,
    _market_ctx,
    _misread_mark,
    _pledge_mark,
    _precedent_mark,
    _seat_mark,
    _target_calib_mark,
    compose_funnel_brief,
    write_base_rates,
)
from autoresearch.scan.l4.dispatch import dispatch_plan
from autoresearch.scan.l4.parsers import (
    parse_ratings_from_details,
    pick_opportunity_candidates,
)
from autoresearch.scan.l4.producers import (
    _SLIM_ANCHORS,
    _SLIM_CLOSE_RE,
    _default_harvest_slim,
    _slim_defect,
    _tushare_fund_hold,
    _tushare_pledge,
    _tushare_seats_by_date,
    fetch_consensus,
    fetch_fund_hold,
    fetch_pledge,
    fetch_seats,
    harvest_slim_batch,
)
from autoresearch.scan.l4.prompts import (
    write_dispatch_pack,
    write_shared_instructions,
)
from autoresearch.scan.l4.rubric import (
    _DIM_SCORE,
    _OW_GATES,
    _RUBRIC_DIMS,
    _norm_dim,
    force_full_card,
    rubric_rating,
)


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="L4 确定性件 CLI(派发包落稿/质押预旗,零 LLM)")
    ap.add_argument("cmd", choices=["shared", "prompts", "pledge", "seats", "consensus",
                                    "harvest-slim", "dispatch-plan"],
                    help="shared = 写 _l4_shared_instructions.md 当日共享块(prompts 之前跑);"
                         "prompts = 写 _harvest_list.txt + _l4_prompt_<code>.md;"
                         "pledge = finalists 批量质押 → pledge.csv(简报自动带 ⚠质押旗);"
                         "seats = finalists 龙虎榜席位聚合 → seats.csv(_seat_mark 注简报);"
                         "consensus = finalists 卖方一致预期修正 → consensus.csv"
                         "(+条件 fund_hold.csv 基金重仓;_inst_mark/_fund_mark 注简报);"
                         "harvest-slim = 按 _harvest_list.txt 批量 harvest slim;"
                         "dispatch-plan = 派发感知 TTL 复用(dispatch/reused 分流)")
    ap.add_argument("date", help="scan 日 YYYY-MM-DD")
    ap.add_argument("--root", default=None, help="scan 根目录(默认 context/scan)")
    ap.add_argument("--workers", type=int, default=4, help="slim 批量并发数(1=串行)")
    ap.add_argument(
        "--stable-context",
        action="store_true",
        help="共享市场/行业/档案块置前并写 hash manifest(缺省 legacy 字节路径)",
    )
    args = ap.parse_args(argv)
    if args.cmd == "shared":
        import json
        base = Path(args.root) if args.root else Path("context/scan")
        n = write_shared_instructions(base / args.date)
        print(json.dumps({"ok": True, "bytes": n}, ensure_ascii=False))
        return 0
    if args.cmd == "dispatch-plan":
        import json
        print(json.dumps(dispatch_plan(args.date, root=args.root), ensure_ascii=False))
        return 0
    if args.cmd == "harvest-slim":
        import json
        res = harvest_slim_batch(args.date, root=args.root, workers=args.workers)
        print(json.dumps({"ok": res["ok"],
                          "reason": ("ok" if res["ok"]
                                     else f"{len(res['failures'])}/{res['n']} slim 失败:"
                                          + ", ".join(f"{f['ticker']}({f['bytes']}B)"
                                                      for f in res["failures"])),
                          "failures": res["failures"]}, ensure_ascii=False))
        return 0 if res["ok"] else 1
    if args.cmd == "pledge":
        base = Path(args.root) if args.root else Path("context/scan")
        df = fetch_pledge(base / args.date)
        n_flag = int((pd.to_numeric(df.get("pledge_ratio"), errors="coerce") > 20).sum()) if len(df) else 0
        print(f"[l4_card pledge] {len(df)} 票落 pledge.csv(>20% 偏高/红旗 {n_flag} 票);"
              f"派发前跑,简报自动注 ⚠质押旗")
        return 0
    if args.cmd == "seats":
        base = Path(args.root) if args.root else Path("context/scan")
        df = fetch_seats(base / args.date)
        n_inst = int((df["inst_net_wan"] > 0).sum()) if len(df) else 0
        print(f"[l4_card seats] {len(df)} 票落 seats.csv(机构净买>0 {n_inst} 票=Phase A 反指候选)")
        return 0
    if args.cmd == "consensus":
        base = Path(args.root) if args.root else Path("context/scan")
        sd = base / args.date
        fp = sd / "finalists.csv"
        codes = pd.read_csv(fp, dtype={"code": str})["code"].tolist() if fp.exists() else None
        df = fetch_consensus(sd, codes=codes)
        try:
            fh = fetch_fund_hold(sd, codes=codes)
        except Exception:  # noqa: BLE001 — 基金行独立分支,不拖累卖方修正主线
            fh = pd.DataFrame()
        extra = f";{len(fh)} 票落 fund_hold.csv(基金重仓,季度滞后)" if len(fh) else ""
        print(f"[l4_card consensus] {len(df)} 票落 consensus.csv(卖方一致预期修正,advisory){extra}")
        return 0
    res = write_dispatch_pack(
        (Path(args.root) if args.root else Path("context/scan")) / args.date,
        stable_context=args.stable_context,
    )
    print(f"[l4_card prompts] {res['n_prompts']} 份 prompt + _harvest_list({len(res['tickers'])} 票,"
          f"已归一 yfinance 后缀);跳过已有卡 {res['n_skipped']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
