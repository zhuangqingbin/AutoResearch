#!/usr/bin/env python3
"""Compatibility adapter for L3 deterministic services.

Implementations live in :mod:`autoresearch.scan.l3`; this module preserves the
historical import path and CLI.
"""
from __future__ import annotations

import json
from pathlib import Path

from autoresearch.common import workspace as ws
from autoresearch.scan.l3.merge import (
    write_finalists,
)
from autoresearch.scan.l3.prompt import (
    prepare_l3_table,
)
from autoresearch.scan.l3.validation import (
    apply_repair_patch,
    build_repair_pack,
    lint_judged,
)
from autoresearch.scan.l3.evidence import harvest_l3_evidence  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.l3.evidence import load_l3_input  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.l3.merge import _inject_pinned_finalists  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.l3.merge import _swap_lane_quota  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.l3.merge import merge_l3_finalists_v3  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.l3.prompt import _L3_COLS  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.l3.prompt import _delta_filter  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.l3.prompt import _fmt  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.l3.prompt import _prev_l3_day  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.l3.prompt import _render_lane_blocks  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.l3.prompt import _row_lane  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.l3.prompt import compact_table  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.l3.prompt import l3_table_md  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.l3.prompt import row_profile  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.l3.triage import triage_l2_for_l3  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.l3.validation import _CODE_TOKEN_RE  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.l3.validation import _COUNT_SUFFIX  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.l3.validation import _DATE_TOKEN_RE  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.l3.validation import _FRACTION_RE  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.l3.validation import _IDENT_CHAR_RE  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.l3.validation import _MARKET_CTX  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.l3.validation import _MARKET_CTX_BACK  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.l3.validation import _NUM_RE  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.l3.validation import _PERIOD_SUFFIX  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.l3.validation import _YEAR_TOKEN_RE  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.l3.validation import _approx_in_pool  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.l3.validation import _atomic_json  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.l3.validation import _complement_pool  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.l3.validation import _fraction_exempt_values  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.l3.validation import _has_market_context  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.l3.validation import _json_safe_row  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.l3.validation import _lint_failures  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.l3.validation import _market_pool  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.l3.validation import _row_numeric_pool  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.l3.validation import _thesis_number_tokens  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.l3.validation import _thesis_number_tokens_pos  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)


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
                (Path(a.root) if a.root else ws.scan_root())
                / a.date / "_l3_repair_prompt.md"
            ),
        }, ensure_ascii=False))
    else:
        res = apply_repair_patch(a.date, root=a.root)
        print(json.dumps({"ok": True, **res}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
