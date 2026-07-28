#!/usr/bin/env python3
"""Deterministic per-candidate first-death attribution."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from autoresearch.scan.decision_record import DecisionRecord
from autoresearch.scan.decision_read_model import (
    read_decisions,
    read_final_ratings,
)

BUY_RATINGS = {"Buy", "Overweight"}
GATES = ("主力真在", "业绩真兑现", "估值不透支")
GATE_STAGE = {
    "主力真在": "L4_GATE_MAIN",
    "业绩真兑现": "L4_GATE_EARNINGS",
    "估值不透支": "L4_GATE_VALUATION",
}
FIRST_DEATH_STAGES = {
    "L0_EXCLUDED",
    "L1_NOT_RECALLED",
    "L2_NOT_SELECTED",
    "PASS1_CUT",
    "L3_BENCH",
    "L4_EARLY_STOP",
    "L4_GATE_MAIN",
    "L4_GATE_EARNINGS",
    "L4_GATE_VALUATION",
    "L4_MULTI_GATE",
    "L4_RUBRIC_SCORE",
    "ENSEMBLE_FOLDED",
    "BOUGHT",
    "DATA_UNDECIDABLE",
}
_COLUMNS = [
    "date",
    "code",
    "first_rejection_stage",
    "final_rating",
    "final_action",
    "gate_state_quality",
    "buyable",
    "mature",
    "fwd_2_oc",
    "market_fwd_2",
    "excess_2",
    "opportunity",
    "evidence_ref",
]


@dataclass(frozen=True)
class _LegacyDecision:
    final_rating: str
    source_rating: str
    gate_states: dict[str, str]
    early_stop: dict | None
    first_rejection_stage: str | None


def classify_first_death(
    *,
    in_l1: bool,
    recalled: bool,
    in_l2: bool,
    in_pass1_cut: bool,
    in_judged: bool,
    in_finalists: bool,
    decision: DecisionRecord | _LegacyDecision | None,
) -> tuple[str, str]:
    """Return one first-death stage and its gate-state data quality."""
    if not in_l1:
        return "L0_EXCLUDED", "NOT_APPLICABLE"
    if not recalled:
        return "L1_NOT_RECALLED", "NOT_APPLICABLE"
    if not in_l2:
        return "L2_NOT_SELECTED", "NOT_APPLICABLE"
    if in_pass1_cut:
        return "PASS1_CUT", "NOT_APPLICABLE"
    if not in_finalists:
        return (
            ("L3_BENCH", "NOT_APPLICABLE")
            if in_judged
            else ("DATA_UNDECIDABLE", "UNKNOWN")
        )
    if decision is None:
        return "DATA_UNDECIDABLE", "UNKNOWN"
    if decision.final_rating in BUY_RATINGS:
        return "BOUGHT", "COMPLETE"
    if decision.early_stop is not None:
        return "L4_EARLY_STOP", "COMPLETE"
    if decision.first_rejection_stage in {"VERIFY", "ENSEMBLE"}:
        return "ENSEMBLE_FOLDED", "COMPLETE"

    states = {gate: decision.gate_states.get(gate, "UNKNOWN") for gate in GATES}
    failed = [gate for gate, state in states.items() if state == "FAIL"]
    if len(failed) >= 2:
        return "L4_MULTI_GATE", "COMPLETE"
    if any(state == "UNKNOWN" for state in states.values()):
        return "DATA_UNDECIDABLE", "UNKNOWN"
    if len(failed) == 1:
        return GATE_STAGE[failed[0]], "COMPLETE"
    if decision.first_rejection_stage == "L4_RUBRIC":
        return "L4_RUBRIC_SCORE", "COMPLETE"
    return "DATA_UNDECIDABLE", "UNKNOWN"


def _codes(path: Path) -> set[str]:
    if not path.exists():
        return set()
    frame = pd.read_csv(path, dtype={"code": str})
    if "code" not in frame.columns:
        return set()
    return {
        str(value).strip().split(".")[0].zfill(6)
        for value in frame["code"]
        if str(value).strip() not in {"", "nan"}
    }


def _legacy_decisions(scan: Path) -> dict[str, _LegacyDecision]:
    from autoresearch.agents.utils.rating import parse_rating
    from autoresearch.scan.assemble import (
        _load_ensemble,
        gate_status,
        parse_early_stop,
    )
    from autoresearch.scan.agents.l4_card import parse_ratings_from_details

    details = scan / "details"
    final = read_final_ratings(
        scan,
        card_fallback=lambda _scan: parse_ratings_from_details(details),
    )
    ensemble = _load_ensemble(scan)
    out = {}
    for code in _codes(scan / "finalists.csv"):
        path = details / f"{code}.md"
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        source = parse_rating(text)
        parsed = gate_status(text)
        states = dict.fromkeys(GATES, "UNKNOWN")
        if parsed is not None:
            states.update(
                {
                    gate: "FAIL" if is_failed else "PASS"
                    for gate, is_failed in parsed.items()
                }
            )
        first = None
        if source in BUY_RATINGS and final.get(code) not in BUY_RATINGS:
            first = "ENSEMBLE" if code in ensemble else "VERIFY"
        elif final.get(code) not in BUY_RATINGS:
            first = "L4_RUBRIC"
        out[code] = _LegacyDecision(
            final_rating=final.get(code, source),
            source_rating=source,
            gate_states=states,
            early_stop=parse_early_stop(text),
            first_rejection_stage=first,
        )
    return out


def _decision_map(scan: Path) -> dict[str, DecisionRecord | _LegacyDecision]:
    path = scan / "decision_records.json"
    return read_decisions(scan) if path.exists() else _legacy_decisions(scan)


def _bool_series(frame: pd.DataFrame, name: str, default: bool) -> pd.Series:
    if name not in frame.columns:
        return pd.Series(default, index=frame.index, dtype=bool)
    values = frame[name]
    if values.dtype == bool:
        return values.fillna(default)
    return values.astype(str).str.strip().str.lower().isin(
        {"true", "1", "yes", "是"}
    )


def _evidence_ref(stage: str, code: str) -> str:
    if stage == "L0_EXCLUDED":
        return f"L1_scored_full.csv#{code}"
    if stage == "L1_NOT_RECALLED":
        return f"L1_recall_top1000.csv#{code}"
    if stage == "L2_NOT_SELECTED":
        return f"L2_gbdt_top200.csv#{code}"
    if stage == "PASS1_CUT":
        return f"_l3_pass1_cut.csv#{code}"
    if stage == "L3_BENCH":
        return f"_l3_bench.csv#{code}"
    if stage in {
        "L4_EARLY_STOP",
        "L4_GATE_MAIN",
        "L4_GATE_EARNINGS",
        "L4_GATE_VALUATION",
        "L4_MULTI_GATE",
        "L4_RUBRIC_SCORE",
        "ENSEMBLE_FOLDED",
        "BOUGHT",
    }:
        return f"decision_records.json#{code}"
    return f"run_health.json#{code}"


def build_rejection_attribution(
    scan_dir: Path | str,
    attribution: pd.DataFrame,
) -> pd.DataFrame:
    """Join funnel membership, decision facts, and market-relative T+2."""
    scan = Path(scan_dir)
    attr = attribution.copy()
    if "code" not in attr.columns:
        raise ValueError("attribution missing code")
    attr["code"] = attr["code"].astype(str).str.zfill(6)
    attr["fwd_2_oc"] = pd.to_numeric(
        attr.get("fwd_2_oc"),
        errors="coerce",
    )
    buyable = _bool_series(attr, "buyable", True)
    tradable = _bool_series(attr, "tradable", True)
    mature = attr["fwd_2_oc"].notna()
    eligible = buyable & tradable & mature
    market = (
        float(attr.loc[eligible, "fwd_2_oc"].median())
        if eligible.any()
        else None
    )

    l1 = _codes(scan / "L1_scored_full.csv")
    recall = _codes(scan / "L1_recall_top1000.csv")
    l2 = _codes(scan / "L2_gbdt_top200.csv")
    pass1 = _codes(scan / "_l3_pass1_cut.csv")
    judged = _codes(scan / "L3_judged_full.csv")
    finalists = _codes(scan / "finalists.csv")
    decisions = _decision_map(scan)
    rows = []
    for pos, row in attr.iterrows():
        code = row["code"]
        stage, gate_quality = classify_first_death(
            in_l1=code in l1,
            recalled=code in recall,
            in_l2=code in l2,
            in_pass1_cut=code in pass1,
            in_judged=code in judged,
            in_finalists=code in finalists,
            decision=decisions.get(code),
        )
        decision = decisions.get(code)
        rating = (
            decision.final_rating
            if decision is not None
            else str(row.get("rating") or "")
        )
        fwd = row["fwd_2_oc"]
        excess = None if market is None or pd.isna(fwd) else float(fwd) - market
        is_buyable = bool(buyable.loc[pos] and tradable.loc[pos])
        opportunity = (
            stage != "BOUGHT"
            and is_buyable
            and excess is not None
            and excess >= 0.02
        )
        rows.append(
            {
                "date": scan.name,
                "code": code,
                "first_rejection_stage": stage,
                "final_rating": rating,
                "final_action": "BUY" if rating in BUY_RATINGS else "ABSTAIN",
                "gate_state_quality": gate_quality,
                "buyable": is_buyable,
                "mature": bool(mature.loc[pos]),
                "fwd_2_oc": None if pd.isna(fwd) else float(fwd),
                "market_fwd_2": market,
                "excess_2": excess,
                "opportunity": opportunity,
                "evidence_ref": _evidence_ref(stage, code),
            }
        )
    return pd.DataFrame(rows, columns=_COLUMNS)


def write_rejection_attribution(
    scan_dir: Path | str,
    attribution: pd.DataFrame,
) -> Path:
    scan = Path(scan_dir)
    target = scan / "retro" / "rejection_attribution.csv"
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f"{target.name}.tmp")
    build_rejection_attribution(scan, attribution).to_csv(
        temp,
        index=False,
    )
    temp.replace(target)
    return target
