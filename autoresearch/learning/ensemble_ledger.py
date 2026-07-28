#!/usr/bin/env python3
"""General OW/sell-review ensemble fold outcome ledger."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from autoresearch.scan.decision_read_model import read_decisions

ECONOMIC_BAND = 0.02
MIN_TRIGGER_FOLDS = 10
_COLUMNS = [
    "date",
    "code",
    "trigger",
    "source_rating",
    "run_ratings",
    "early_stopped",
    "degraded",
    "spread",
    "final_rating",
    "fwd_2_oc",
    "market_fwd_2",
    "excess_2",
    "verdict",
]
_MATURE_VERDICTS = {"FOLD_RIGHT", "FOLD_WRONG", "FOLD_NEUTRAL"}


def fold_outcome(
    source_rating: str,
    final_rating: str,
    trigger: str,
    excess_2: float | None,
    *,
    degraded: bool = False,
) -> str:
    """Classify the intervention, keeping failed reviews undecidable."""
    if degraded:
        return "UNDECIDABLE"
    if excess_2 is None or pd.isna(excess_2):
        return "IMMATURE"
    if source_rating == final_rating:
        return "NO_FOLD"
    if -ECONOMIC_BAND < excess_2 < ECONOMIC_BAND:
        return "FOLD_NEUTRAL"
    if trigger == "ow_review":
        return "FOLD_WRONG" if excess_2 >= ECONOMIC_BAND else "FOLD_RIGHT"
    if trigger == "sell_review":
        return "FOLD_RIGHT" if excess_2 >= ECONOMIC_BAND else "FOLD_WRONG"
    return "UNDECIDABLE"


def load_fold_facts(scan_dir: Path | str) -> dict[str, dict]:
    """Read source/final ratings from structured decision and ensemble facts."""
    from autoresearch.scan.decision_finalize import (
        _apply_ensemble_fold,
        _load_ensemble,
    )

    scan = Path(scan_dir)
    ensemble = _load_ensemble(scan)
    decisions = (
        read_decisions(scan)
        if (scan / "decision_records.json").exists()
        else {}
    )
    facts = {}
    for code, record in sorted(ensemble.items()):
        decision = decisions.get(code)
        ratings = list(
            decision.ensemble_ratings
            if decision is not None and decision.ensemble_ratings
            else record.get("ratings") or []
        )
        source = (
            decision.source_rating
            if decision is not None
            else (ratings[0] if ratings else "")
        )
        final = (
            decision.final_rating
            if decision is not None
            else _apply_ensemble_fold(source, record)
        )
        facts[code] = {
            "code": code,
            "trigger": str(record.get("trigger") or "ow_review"),
            "source_rating": source,
            "run_ratings": ratings,
            "early_stopped": bool(record.get("early_stopped", False)),
            "degraded": bool(record.get("degraded", False)),
            "spread": int(record.get("spread") or 0),
            "final_rating": final,
        }
    return facts


def _read_attr(scan: Path) -> pd.DataFrame | None:
    path = scan / "retro" / "attribution.csv"
    if not path.exists():
        return None
    try:
        frame = pd.read_csv(path, dtype={"code": str})
    except Exception:  # noqa: BLE001
        return None
    if "code" not in frame.columns:
        return None
    frame["code"] = frame["code"].astype(str).str.zfill(6)
    return frame.set_index("code")


def _bool_value(value, default: bool = True) -> bool:
    if value is None or pd.isna(value):
        return default
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "是"}
    return bool(value)


def _market_fwd2(attr: pd.DataFrame | None) -> float | None:
    if attr is None or "fwd_2_oc" not in attr.columns:
        return None
    values = pd.to_numeric(attr["fwd_2_oc"], errors="coerce")
    buyable = (
        attr["buyable"].map(_bool_value)
        if "buyable" in attr.columns
        else pd.Series(True, index=attr.index)
    )
    tradable = (
        attr["tradable"].map(_bool_value)
        if "tradable" in attr.columns
        else pd.Series(True, index=attr.index)
    )
    usable = values[buyable & tradable & values.notna()]
    return None if not len(usable) else float(usable.median())


def day_rows(scan_dir: Path | str) -> pd.DataFrame:
    scan = Path(scan_dir)
    facts = load_fold_facts(scan)
    attr = _read_attr(scan)
    market = _market_fwd2(attr)
    rows = []
    for code, fact in facts.items():
        fwd = None
        if attr is not None and code in attr.index and "fwd_2_oc" in attr.columns:
            row = attr.loc[code]
            if isinstance(row, pd.DataFrame):
                row = row.iloc[0]
            value = pd.to_numeric(
                pd.Series([row.get("fwd_2_oc")]),
                errors="coerce",
            ).iloc[0]
            if (
                not pd.isna(value)
                and _bool_value(row.get("buyable", True))
                and _bool_value(row.get("tradable", True))
            ):
                fwd = float(value)
        excess = None if fwd is None or market is None else fwd - market
        rows.append(
            {
                "date": scan.name,
                **fact,
                "fwd_2_oc": None if fwd is None else round(fwd, 6),
                "market_fwd_2": (
                    None if market is None else round(market, 6)
                ),
                "excess_2": None if excess is None else round(excess, 6),
                "verdict": fold_outcome(
                    fact["source_rating"],
                    fact["final_rating"],
                    fact["trigger"],
                    excess,
                    degraded=fact["degraded"],
                ),
            }
        )
    return pd.DataFrame(rows, columns=_COLUMNS)


def roll(scan_root: Path | str = "context/scan") -> pd.DataFrame:
    root = Path(scan_root)
    frames = []
    days = sorted(
        {
            path.parent
            for pattern in ("*/_ensemble.json", "*/_ensemble_*.json")
            for path in root.glob(pattern)
        }
    )
    for day in days:
        frame = day_rows(day)
        if len(frame):
            frames.append(frame)
    return (
        pd.concat(frames, ignore_index=True)
        if frames
        else pd.DataFrame(columns=_COLUMNS)
    )


def trigger_summary(rows: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "trigger",
        "n_records",
        "n_mature_folds",
        "fold_right",
        "fold_wrong",
        "fold_neutral",
        "status",
    ]
    if rows is None or not len(rows):
        return pd.DataFrame(columns=columns)
    output = []
    for trigger, group in rows.groupby("trigger"):
        mature = group[group["verdict"].isin(_MATURE_VERDICTS)]
        output.append(
            {
                "trigger": trigger,
                "n_records": len(group),
                "n_mature_folds": len(mature),
                "fold_right": int((mature["verdict"] == "FOLD_RIGHT").sum()),
                "fold_wrong": int((mature["verdict"] == "FOLD_WRONG").sum()),
                "fold_neutral": int(
                    (mature["verdict"] == "FOLD_NEUTRAL").sum()
                ),
                "status": (
                    "MATURE"
                    if len(mature) >= MIN_TRIGGER_FOLDS
                    else "IMMATURE"
                ),
            }
        )
    return pd.DataFrame(output, columns=columns).sort_values(
        "trigger"
    ).reset_index(drop=True)


def render(rows: pd.DataFrame) -> str:
    summary = trigger_summary(rows)
    lines = [
        "# Ensemble 折回结果账",
        "",
        "_OW 买单复核与 pinned SELL 复核分桶记账；不自动修改折回规则。_",
        "",
    ]
    if not len(summary):
        return "\n".join(lines + ["_无 ensemble 事实。_"]) + "\n"
    lines += [
        "| trigger | records | 成熟折回 | 折对 | 折错 | 中性 | 状态 |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for row in summary.itertuples(index=False):
        lines.append(
            f"| {row.trigger} | {row.n_records} | {row.n_mature_folds}"
            f" | {row.fold_right} | {row.fold_wrong} | {row.fold_neutral}"
            f" | {row.status} |"
        )
    lines += [
        "",
        "_每个 trigger family 成熟折回 n<10 时禁止修改机制；degraded 单列"
        " UNDECIDABLE，同档早止保留 early_stopped=true。_",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    rows = roll()
    output = Path("reports/learning/ensemble_ledger.md")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render(rows), encoding="utf-8")
    detail = output.with_suffix(".json")
    detail.write_text(
        json.dumps(rows.to_dict(orient="records"), ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    print(f"[ensemble_ledger] {len(rows)} folds → {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
