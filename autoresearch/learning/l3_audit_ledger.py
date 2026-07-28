#!/usr/bin/env python3
"""L3 shadow audit basket and T+2 outcome ledger.

The basket measures a small, deterministic slice of the L3 bench.  It is never
used as a finalist, rating, dispatch, or BUY source.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pandas as pd

from autoresearch.learning.t1_review import _NON_GENUINE_LANES

AUDIT_SHARE = 0.20
MIN_FORWARD_SCAN_DAYS = 20
_SUMMARY_KEYS = (
    "candidate_n",
    "mature_n",
    "opportunity_n",
    "mean_excess_2",
    "main_finalist_mature_n",
    "main_finalist_mean_excess_2",
)


def select_audit_candidates(
    bench: pd.DataFrame,
    *,
    finalist_max: int,
) -> pd.DataFrame:
    """Select a deterministic 20% shadow slice from the L3 bench."""
    if "code" not in bench.columns:
        raise ValueError("L3 bench missing code")
    frame = bench.copy()
    frame["code"] = frame["code"].astype(str).str.split(".").str[0].str.zfill(6)
    for column in ("conviction", "fragility"):
        if column not in frame.columns:
            frame[column] = float("nan")
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    cap = max(0, math.ceil(max(0, int(finalist_max)) * AUDIT_SHARE))
    return (
        frame.sort_values(
            ["conviction", "fragility", "code"],
            ascending=[False, False, True],
            na_position="last",
            kind="stable",
        )
        .head(cap)
        .reset_index(drop=True)
    )


def write_audit_candidates(
    scan_dir: Path | str,
    *,
    finalist_max: int,
) -> Path:
    """Atomically write the shadow-only candidate CSV."""
    scan = Path(scan_dir)
    bench = pd.read_csv(scan / "_l3_bench.csv", dtype={"code": str})
    selected = select_audit_candidates(bench, finalist_max=finalist_max)
    target = scan / "shadow" / "l3_audit_candidates.csv"
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(target.suffix + ".tmp")
    selected.to_csv(temp, index=False, lineterminator="\n")
    temp.replace(target)
    return target


def _bool_column(frame: pd.DataFrame, column: str, default: bool) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(default, index=frame.index, dtype=bool)
    values = frame[column]
    if values.dtype == bool:
        return values.fillna(default)
    return values.astype(str).str.strip().str.lower().isin(
        {"true", "1", "yes", "是"}
    )


def _round_or_none(value: float | None) -> float | None:
    if value is None or pd.isna(value):
        return None
    return round(float(value), 5)


def build_day_ledger(
    scan_dir: Path | str,
    attribution: pd.DataFrame,
) -> dict:
    """Join the shadow cohort and genuine L3 finalists to mature T+2 facts."""
    scan = Path(scan_dir)
    candidates = pd.read_csv(
        scan / "shadow" / "l3_audit_candidates.csv",
        dtype={"code": str},
    )
    candidates["code"] = candidates["code"].astype(str).str.zfill(6)

    attr = attribution.copy()
    if "code" not in attr.columns:
        raise ValueError("attribution missing code")
    attr["code"] = attr["code"].astype(str).str.zfill(6)
    attr["fwd_2_oc"] = pd.to_numeric(attr.get("fwd_2_oc"), errors="coerce")
    attr["buyable"] = _bool_column(attr, "buyable", True)
    attr["tradable"] = _bool_column(attr, "tradable", True)
    eligible = attr["buyable"] & attr["tradable"] & attr["fwd_2_oc"].notna()
    market = (
        float(attr.loc[eligible, "fwd_2_oc"].median())
        if eligible.any()
        else None
    )
    facts = attr.set_index("code")

    rows = []
    excesses = []
    for candidate in candidates.to_dict(orient="records"):
        code = candidate["code"]
        fact = facts.loc[code] if code in facts.index else None
        fwd = None
        buyable = tradable = False
        if fact is not None:
            if isinstance(fact, pd.DataFrame):
                fact = fact.iloc[0]
            value = pd.to_numeric(pd.Series([fact.get("fwd_2_oc")]), errors="coerce").iloc[0]
            fwd = None if pd.isna(value) else float(value)
            buyable = bool(fact.get("buyable", True))
            tradable = bool(fact.get("tradable", True))
        mature = fwd is not None and buyable and tradable and market is not None
        excess = (fwd - market) if mature else None
        if excess is not None:
            excesses.append(excess)
        rows.append(
            {
                "code": code,
                "conviction": _round_or_none(candidate.get("conviction")),
                "fragility": _round_or_none(candidate.get("fragility")),
                "lane": str(candidate.get("lane") or ""),
                "mature": mature,
                "fwd_2_oc": _round_or_none(fwd),
                "market_fwd_2": _round_or_none(market),
                "excess_2": _round_or_none(excess),
                "opportunity": bool(mature and excess is not None and excess >= 0.02),
            }
        )

    finalist_excesses: list[float] = []
    finalist_path = scan / "finalists.csv"
    if finalist_path.exists():
        finalists = pd.read_csv(finalist_path, dtype={"code": str})
        if "code" in finalists.columns:
            finalists["code"] = finalists["code"].astype(str).str.zfill(6)
            if "lane" in finalists.columns:
                finalists = finalists[
                    ~finalists["lane"].fillna("").astype(str).isin(
                        _NON_GENUINE_LANES
                    )
                ]
            if market is not None:
                for code in finalists["code"]:
                    if code not in facts.index:
                        continue
                    fact = facts.loc[code]
                    if isinstance(fact, pd.DataFrame):
                        fact = fact.iloc[0]
                    value = pd.to_numeric(
                        pd.Series([fact.get("fwd_2_oc")]),
                        errors="coerce",
                    ).iloc[0]
                    if (
                        not pd.isna(value)
                        and bool(fact.get("buyable", True))
                        and bool(fact.get("tradable", True))
                    ):
                        finalist_excesses.append(float(value) - market)

    mature_n = len(excesses)
    summary = {
        "candidate_n": int(len(rows)),
        "mature_n": mature_n,
        "opportunity_n": sum(bool(row["opportunity"]) for row in rows),
        "mean_excess_2": _round_or_none(
            sum(excesses) / mature_n if mature_n else None
        ),
        "main_finalist_mature_n": len(finalist_excesses),
        "main_finalist_mean_excess_2": _round_or_none(
            sum(finalist_excesses) / len(finalist_excesses)
            if finalist_excesses
            else None
        ),
    }
    return {
        "schema_version": 1,
        "date": scan.name,
        "cohort": "L3_BENCH_SHADOW_AUDIT",
        "production_effect": "NONE",
        "minimum_forward_scan_days": MIN_FORWARD_SCAN_DAYS,
        "forward_scan_days": 1 if mature_n else 0,
        "sample_status": "IMMATURE",
        "summary": summary,
        "candidates": rows,
    }


def write_day_ledger(
    scan_dir: Path | str,
    attribution: pd.DataFrame,
) -> Path:
    """Atomically persist the per-day audit fact under retro/."""
    scan = Path(scan_dir)
    payload = build_day_ledger(scan, attribution)
    target = scan / "retro" / "l3_audit_ledger.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(target.suffix + ".tmp")
    temp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temp.replace(target)
    return target


def roll(scan_root: Path | str = "context/scan") -> tuple[pd.DataFrame, dict]:
    """Aggregate immutable per-day ledgers without changing the cohort."""
    rows = []
    summaries = []
    for path in sorted(Path(scan_root).glob("*/retro/l3_audit_ledger.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        summaries.append(payload["summary"])
        rows.extend({"date": payload["date"], **row} for row in payload["candidates"])
    frame = pd.DataFrame(rows)
    forward_days = sum(
        int(summary.get("mature_n", 0)) > 0 for summary in summaries
    )
    summary = {
        key: sum(
            int(item.get(key, 0))
            for item in summaries
        )
        for key in ("candidate_n", "mature_n", "opportunity_n")
    }
    summary["forward_scan_days"] = forward_days
    summary["sample_status"] = (
        "MATURE" if forward_days >= MIN_FORWARD_SCAN_DAYS else "IMMATURE"
    )
    return frame, summary


def render(rows: pd.DataFrame, summary: dict) -> str:
    """Render the aggregate measurement lane."""
    status = summary.get("sample_status", "IMMATURE")
    lines = [
        "# L3 影子审计篮",
        "",
        "_只测量 bench 高位票，不进入 finalists、L4、评级或 BUY。_",
        "",
        f"- 前向扫描日:{summary.get('forward_scan_days', 0)}/{MIN_FORWARD_SCAN_DAYS}"
        f"；样本状态:**{status}**",
        f"- 候选:{summary.get('candidate_n', 0)}；成熟:{summary.get('mature_n', 0)}"
        f"；捕获 +2pp 机会:{summary.get('opportunity_n', 0)}",
    ]
    if status == "IMMATURE":
        lines.append("- ⚠ 未满 20 个前向扫描日，不得据此改 L3 选择规则。")
    if len(rows):
        lines += [
            "",
            "| 日期 | 代码 | conviction | fragility | excess2 | opportunity |",
            "|---|---|---:|---:|---:|---|",
        ]
        for row in rows.itertuples(index=False):
            excess = "—" if row.excess_2 is None or pd.isna(row.excess_2) else f"{row.excess_2:+.2%}"
            lines.append(
                f"| {row.date} | {row.code} | {row.conviction} | {row.fragility}"
                f" | {excess} | {'是' if row.opportunity else '否'} |"
            )
    return "\n".join(lines) + "\n"


def main() -> int:
    rows, summary = roll()
    output = Path("reports/learning/l3_audit_ledger.md")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render(rows, summary), encoding="utf-8")
    print(f"[l3_audit_ledger] {summary['candidate_n']} 候选 → {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
