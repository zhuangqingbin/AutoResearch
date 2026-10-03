#!/usr/bin/env python3
"""卡上影子机读行的事后计分(2026-10-03 复盘稿 B5 / Q4「卡片预测计分」;离线、只读)。

读已发布 run 的 `trace/staging/_card_shadow_fields.json`(`scan.l4.shadow_fields`)与结果账本
`recommendations.csv`(主尺 `gap_c1_o2`、T+1 收盘),逐卡给出三样事后读数:

- 兑现机制三态 → 主尺均值(成立的那批事后是否真的更好);
- 卡自报 EV(三档情景加权)→ 与实现主尺的秩相关;
- 价格类入场否决在 T+1 收盘是否会触发 → 触发 / 未触发两批的主尺均值。

只用成熟 ∧ 可执行 ∧ 交易日历可信的行。只记账:不回写卡、评级、门或 E6。

  uv run --no-sync python -m autoresearch.research.card_shadow_scoring --out <dir>
"""
from __future__ import annotations

import argparse
import csv
import json
import operator
from pathlib import Path

import numpy as np
import pandas as pd

_OPS = {"<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge}


def _num(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _ledger_rows(reports_scan_root: Path) -> dict[tuple[str, str], dict]:
    path = Path(reports_scan_root) / "_ledger" / "recommendations.csv"
    if not path.is_file():
        return {}
    out = {}
    with path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            if (row.get("outcome_status") == "MATURE" and row.get("actionability") == "ACTIONABLE"
                    and row.get("calendar_quality") == "trade_cal"
                    and _num(row.get("gap_c1_o2")) is not None):
                out[(str(row["run_id"]), str(row["code"]).zfill(6))] = row
    return out


def readout(reports_scan_root: Path) -> list[dict]:
    root = Path(reports_scan_root)
    ledger = _ledger_rows(root)
    rows: list[dict] = []
    for path in sorted(root.glob("*/trace/staging/_card_shadow_fields.json")):
        run = path.parents[2].name
        try:
            cards = json.loads(path.read_text(encoding="utf-8")).get("cards") or {}
        except (OSError, json.JSONDecodeError):
            continue
        for code, fields in sorted(cards.items()):
            ledger_row = ledger.get((run, str(code).zfill(6)))
            if ledger_row is None:
                continue
            close = _num(ledger_row.get("t1_close"))
            vetoes = fields.get("entry_vetoes") or []
            fired = (None if not vetoes or close is None
                     else any(_OPS[v["op"]](close, float(v["threshold"])) for v in vetoes
                              if v.get("op") in _OPS))
            rows.append({"run": run, "code": str(code).zfill(6),
                         "mechanism": fields.get("mechanism"), "ev": fields.get("ev"),
                         "veto_fired": fired, "gap": _num(ledger_row.get("gap_c1_o2"))})
    return rows


def summarize(rows: list[dict]) -> dict:
    def cell(values: list[float]) -> dict:
        return {"n": len(values), "mean_gap": round(float(np.mean(values)), 6)}

    mechanism = {state: cell([r["gap"] for r in rows if r["mechanism"] == state])
                 for state in ("成立", "不成立", "未核")
                 if any(r["mechanism"] == state for r in rows)}
    ev_rows = [(r["ev"], r["gap"]) for r in rows if r["ev"] is not None]
    ev_ic = None
    if len(ev_rows) >= 5:
        frame = pd.DataFrame(ev_rows, columns=["ev", "gap"])
        if frame["ev"].nunique() > 1 and frame["gap"].nunique() > 1:
            ev_ic = float(frame["ev"].rank().corr(frame["gap"].rank()))
    fired = [r["gap"] for r in rows if r["veto_fired"] is True]
    clear = [r["gap"] for r in rows if r["veto_fired"] is False]
    return {"n_cards": len(rows), "mechanism": mechanism, "ev_rank_ic": ev_ic,
            "veto_fired": cell(fired) if fired else None,
            "veto_clear": cell(clear) if clear else None}


def main(argv: list[str] | None = None) -> int:
    from autoresearch.common import workspace as ws

    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--reports-root", default=None)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    root = Path(args.reports_root) if args.reports_root else ws.reports_root() / "scan"
    rows = readout(root)
    summary = summarize(rows)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(out / "card_shadow_scoring.csv", index=False)
    (out / "card_shadow_scoring.md").write_text(
        "# 卡片影子机读行计分(只记账)\n\n" + json.dumps(summary, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
