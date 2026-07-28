#!/usr/bin/env python3
"""Compatibility adapter for L3 deterministic services.

Implementations live in :mod:`autoresearch.scan.l3`; this module preserves the
historical import path and CLI.
"""
from __future__ import annotations

import json
from pathlib import Path

from autoresearch.scan.l3.evidence import harvest_l3_evidence, load_l3_input
from autoresearch.scan.l3.merge import (
    _inject_pinned_finalists,
    _swap_lane_quota,
    merge_l3_finalists_v3,
    write_finalists,
)
from autoresearch.scan.l3.prompt import (
    _L3_COLS,
    _delta_filter,
    _fmt,
    _prev_l3_day,
    _render_lane_blocks,
    _row_lane,
    compact_table,
    l3_table_md,
    prepare_l3_table,
    row_profile,
)
from autoresearch.scan.l3.triage import triage_l2_for_l3
from autoresearch.scan.l3.validation import (
    _CODE_TOKEN_RE,
    _COUNT_SUFFIX,
    _DATE_TOKEN_RE,
    _FRACTION_RE,
    _IDENT_CHAR_RE,
    _MARKET_CTX,
    _MARKET_CTX_BACK,
    _NUM_RE,
    _PERIOD_SUFFIX,
    _YEAR_TOKEN_RE,
    _approx_in_pool,
    _atomic_json,
    _complement_pool,
    _fraction_exempt_values,
    _has_market_context,
    _json_safe_row,
    _lint_failures,
    _market_pool,
    _row_numeric_pool,
    _thesis_number_tokens,
    _thesis_number_tokens_pos,
    apply_repair_patch,
    build_repair_pack,
    lint_judged,
)


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="l3_select")
    ap.add_argument(
        "cmd",
        choices=["finalists", "prepare", "lint", "repair-pack", "apply-repair"],
    )
    ap.add_argument("date")
    ap.add_argument("--budget", type=int, default=30)
    ap.add_argument("--root", default=None)
    a = ap.parse_args(argv)
    if a.cmd == "finalists":
        res = write_finalists(a.date, budget=a.budget, root=a.root)
        print(f"[l3_select finalists] judged {res['judged_n']} → finalist tier {res['finalist_n']}"
              f" + bench {res['bench_n']}(finalists.csv 共 {res['finalists_n']} 行,含 pinned 追加)")
    elif a.cmd == "prepare":
        res = prepare_l3_table(a.date, root=a.root)
        print(f"[l3_select prepare] codes {res['codes']} → _l3_table.md {res['table_bytes']}B")
    elif a.cmd == "lint":
        res = lint_judged(a.date, root=a.root)
        print(json.dumps(res, ensure_ascii=False))
        return 0 if res.get("ok") else 1
    elif a.cmd == "repair-pack":
        res = build_repair_pack(a.date, root=a.root)
        print(json.dumps({
            "ok": True,
            "codes": res["codes"],
            "n": len(res["codes"]),
            "prompt": str(
                (Path(a.root) if a.root else Path("context/scan"))
                / a.date / "_l3_repair_prompt.md"
            ),
        }, ensure_ascii=False))
    else:
        res = apply_repair_patch(a.date, root=a.root)
        print(json.dumps({"ok": True, **res}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
