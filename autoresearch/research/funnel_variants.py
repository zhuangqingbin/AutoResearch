#!/usr/bin/env python3
"""Evidence-only, fixed-budget funnel variant reconstruction.

This module is deliberately outside the production scan path.  It rebuilds
candidate membership from frozen daily artifacts and evaluates variants on the
same dates and return population.  It never mutates a scan directory.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from autoresearch.common import ruler, workspace as ws
from autoresearch.common.atomic import atomic_write_bytes, atomic_write_json, sha256_file
from autoresearch.common.stats import day_equal_bootstrap
from autoresearch.scan.l3.triage import triage_l2_for_l3
from autoresearch.scan.recall.l2_stratify import select_l2

CURRENT = "current"
COMPOSITE_ONLY = "composite_only"
HYBRID = "composite_plus_diversifiers"
VARIANTS = (CURRENT, COMPOSITE_ONLY, HYBRID)
STAGE_FLAGS = {"l1": "in_l1", "l2": "in_l2", "pass1": "pass1_kept"}
SCHEMA_VERSION = 1
RULE_VERSION = "funnel_variants.v1"


def _normalise(frame: pd.DataFrame, *, name: str, required: tuple[str, ...] = ("code",)) -> pd.DataFrame:
    if frame is None or set(required) - set(frame.columns):
        raise ValueError(f"{name} lacks columns {sorted(set(required) - set(getattr(frame, 'columns', ())))}")
    out = frame.copy()
    out["code"] = out["code"].astype(str).str.split(".").str[0].str.zfill(6)
    if out["code"].isna().any() or out["code"].duplicated().any():
        raise ValueError(f"{name} has missing or duplicate code")
    return out


def _score_sort(frame: pd.DataFrame, *, extra: tuple[str, ...] = ()) -> pd.DataFrame:
    work = frame.copy()
    work["_score"] = pd.to_numeric(work["composite"], errors="coerce").fillna(-np.inf)
    columns = [*extra, "_score", "code"]
    ascending = [False] * (len(extra) + 1) + [True]
    return work.sort_values(columns, ascending=ascending, kind="stable").drop(columns="_score")


def _with_provenance(full: pd.DataFrame, current_l1: pd.DataFrame) -> pd.DataFrame:
    """Attach only observed recall provenance; missing provenance stays missing."""
    out = full.copy()
    cols = [c for c in ("recall_channels", "n_channels", "best_rank", "pinned")
            if c in current_l1.columns and c not in out.columns]
    if cols:
        out = out.merge(current_l1[["code", *cols]], on="code", how="left", validate="one_to_one")
    return out


def _hybrid_l1(full: pd.DataFrame, current_l1: pd.DataFrame, *, recall_n: int,
               core_fraction: float) -> pd.DataFrame:
    if not 0.0 <= core_fraction <= 1.0:
        raise ValueError("hybrid_core_fraction must be within [0, 1]")
    n = min(max(0, int(recall_n)), len(full))
    core_n = min(n, int(np.floor(n * core_fraction)))
    enriched = _with_provenance(full, current_l1)
    ranked = _score_sort(enriched)
    core = ranked.head(core_n).copy()
    core["l1_selection_reason"] = "composite_core"
    have = set(core["code"])

    diversifiers = current_l1[~current_l1["code"].isin(have)].copy()
    if "n_channels" not in diversifiers:
        diversifiers["n_channels"] = 0
    diversifiers["_n_channels"] = pd.to_numeric(diversifiers["n_channels"], errors="coerce").fillna(0)
    diversifiers = _score_sort(diversifiers, extra=("_n_channels",)).drop(columns="_n_channels")
    picked = diversifiers.head(n - len(core)).copy()
    picked["l1_selection_reason"] = "diversifier"
    have |= set(picked["code"])

    missing = n - len(core) - len(picked)
    backfill = ranked[~ranked["code"].isin(have)].head(missing).copy()
    backfill["l1_selection_reason"] = "composite_backfill"
    return pd.concat([core, picked, backfill], ignore_index=True, sort=False)


def _challenger_stages(l1: pd.DataFrame, *, l2_n: int, pass1_n: int,
                       floors: dict[str, int] | None, sector_cap_frac: float,
                       preserve_provenance: bool) -> tuple[pd.DataFrame, pd.DataFrame]:
    source = l1.copy()
    enabled = None
    if not preserve_provenance:
        source = source.drop(columns=["recall_channels", "n_channels", "best_rank"], errors="ignore")
        enabled = []
    elif "recall_channels" in source:
        enabled = sorted({token for value in source["recall_channels"].dropna().astype(str)
                          for token in value.split("|")
                          if token and token not in {"(backfill)", "pinned"}})
    source = _score_sort(source)
    l2, _ = select_l2(
        source, int(l2_n), floors=floors, sector_cap_frac=float(sector_cap_frac),
        enabled_channels=enabled,
    )
    kept, _ = triage_l2_for_l3(l2, target=int(pass1_n))
    return l2, kept


def _reason_map(frame: pd.DataFrame, column: str = "selection_reason") -> dict[str, str]:
    if column not in frame:
        return {}
    return dict(zip(frame["code"], frame[column].fillna("").astype(str), strict=False))


def build_day(date: str, *, full: pd.DataFrame, current_l1: pd.DataFrame,
              current_l2: pd.DataFrame, current_pass1: pd.DataFrame,
              recall_n: int, l2_n: int, pass1_n: int,
              floors: dict[str, int] | None = None, sector_cap_frac: float = 0.20,
              hybrid_core_fraction: float = 0.80) -> pd.DataFrame:
    """Return one row per date/code/variant with explicit stage membership."""
    full = _normalise(full, name="full", required=("code", "composite"))
    current_l1 = _normalise(current_l1, name="current_l1")
    current_l2 = _normalise(current_l2, name="current_l2")
    current_pass1 = _normalise(current_pass1, name="current_pass1")
    universe = set(full["code"])
    for name, frame in (("current_l1", current_l1), ("current_l2", current_l2),
                        ("current_pass1", current_pass1)):
        if not set(frame["code"]) <= universe:
            raise ValueError(f"{name} contains code absent from full population")
    if not set(current_pass1["code"]) <= set(current_l2["code"]):
        raise ValueError("current_pass1 must be a subset of current_l2")
    if not set(current_l2["code"]) <= set(current_l1["code"]):
        raise ValueError("current_l2 must be a subset of current_l1")

    comp_l1 = _score_sort(full).head(min(int(recall_n), len(full))).copy()
    comp_l1["l1_selection_reason"] = "composite"
    hybrid_l1 = _hybrid_l1(full, current_l1, recall_n=int(recall_n),
                           core_fraction=float(hybrid_core_fraction))
    comp_l2, comp_pass1 = _challenger_stages(
        comp_l1, l2_n=l2_n, pass1_n=pass1_n, floors=floors,
        sector_cap_frac=sector_cap_frac, preserve_provenance=False,
    )
    hybrid_l2, hybrid_pass1 = _challenger_stages(
        hybrid_l1, l2_n=l2_n, pass1_n=pass1_n, floors=floors,
        sector_cap_frac=sector_cap_frac, preserve_provenance=True,
    )
    variants = {
        CURRENT: (current_l1, current_l2, current_pass1),
        COMPOSITE_ONLY: (comp_l1, comp_l2, comp_pass1),
        HYBRID: (hybrid_l1, hybrid_l2, hybrid_pass1),
    }

    rows: list[dict] = []
    for variant, (l1, l2, pass1) in variants.items():
        l1_codes, l2_codes, pass1_codes = map(lambda x: set(x["code"]), (l1, l2, pass1))
        l1_reasons = _reason_map(l1, "l1_selection_reason")
        if variant == CURRENT and not l1_reasons:
            l1_reasons = {code: "current" for code in l1_codes}
        l2_reasons, p1_reasons = _reason_map(l2), _reason_map(pass1)
        l2_details, p1_details = _reason_map(l2, "selection_detail"), _reason_map(pass1, "selection_detail")
        for code in sorted(universe):
            rows.append({
                "date": str(date), "code": code, "variant": variant,
                "in_l1": code in l1_codes, "in_l2": code in l2_codes,
                "pass1_kept": code in pass1_codes,
                "l1_selection_reason": l1_reasons.get(code, ""),
                "l2_selection_reason": l2_reasons.get(code, ""),
                "l2_selection_detail": l2_details.get(code, ""),
                "pass1_selection_reason": p1_reasons.get(code, ""),
                "pass1_selection_detail": p1_details.get(code, ""),
            })
    return pd.DataFrame(rows)


def daily_metrics(membership: pd.DataFrame, outcomes: pd.DataFrame, *,
                  ruler_name: str = ruler.MAIN_RULER,
                  stages: tuple[str, ...] = tuple(STAGE_FLAGS)) -> pd.DataFrame:
    """Day-equal building blocks; missing selected outcomes never become zero."""
    required_m = {"date", "code", "variant", *(STAGE_FLAGS[s] for s in stages)}
    required_o = {"date", "code", ruler_name}
    if required_m - set(membership) or required_o - set(outcomes):
        raise ValueError("membership/outcomes missing required columns")
    if membership.duplicated(["date", "code", "variant"]).any() or outcomes.duplicated(["date", "code"]).any():
        raise ValueError("duplicate date/code identity")
    left, right = membership.copy(), outcomes.copy()
    for frame in (left, right):
        frame["date"] = frame["date"].astype(str)
        frame["code"] = frame["code"].astype(str).str.split(".").str[0].str.zfill(6)
    values = pd.to_numeric(right[ruler_name], errors="coerce")
    if np.isinf(values.fillna(0).to_numpy(dtype=float)).any():
        raise ValueError("infinite return")
    right["_value"] = values
    status_col = f"status_{ruler_name}"
    right["_mature"] = right[status_col].eq("MATURE") if status_col in right else values.notna()
    right["_tradable"] = ruler.entry_tradable(right, ruler_name=ruler_name)
    right["_usable"] = right["_mature"] & right["_tradable"] & right["_value"].notna()
    market = right[right["_usable"]].groupby("date")["_value"].agg(["median", "mean", "size"])
    joined = left.merge(right, on=["date", "code"], how="left", validate="many_to_one")
    rows: list[dict] = []
    for (date, variant), group in joined.groupby(["date", "variant"], sort=True):
        market_row = market.loc[date] if date in market.index else None
        for stage in stages:
            selected = group[STAGE_FLAGS[stage]].astype(bool)
            selected_rows = group[selected]
            tradable = selected_rows["_tradable"].fillna(False).astype(bool)
            needed = selected_rows[tradable]
            missing = int((~needed["_mature"].fillna(False) | needed["_value"].isna()).sum())
            usable = needed[needed["_mature"].fillna(False) & needed["_value"].notna()]
            status = ("INCOMPLETE_OUTCOMES" if missing else
                      "EMPTY_SELECTION" if len(usable) == 0 or market_row is None else "COMPLETE")
            raw = float(usable["_value"].mean()) if status == "COMPLETE" else None
            med = float(market_row["median"]) if market_row is not None else None
            exit_flagged = 0
            if ruler.EXIT_FLAG in usable:
                exit_flagged = int(usable[ruler.EXIT_FLAG].fillna(False).astype(bool).sum())
            rows.append({
                "date": date, "variant": variant, "stage": stage, "status": status,
                "n_selected": int(selected.sum()), "n_tradable": int(tradable.sum()),
                "n_scored": int(len(usable)), "missing_outcomes": missing,
                "raw_mean": raw, "market_median": med,
                "excess_vs_market_median": raw - med if raw is not None and med is not None else None,
                "win_rate": float((usable["_value"] > 0).mean()) if status == "COMPLETE" else None,
                "n_exit_flagged": exit_flagged,
                "market_n": int(market_row["size"]) if market_row is not None else 0,
            })
    return pd.DataFrame(rows)


def paired_summary(daily: pd.DataFrame, *, baseline: str, challenger: str, stage: str,
                   metric: str = "excess_vs_market_median", min_days: int = 20,
                   n_boot: int = 2000, seed: int = 20260913) -> dict:
    """Compare only dates on which both variants have complete observations."""
    sub = daily[(daily["stage"] == stage) & daily["variant"].isin([baseline, challenger])].copy()
    sub.loc[sub["status"] != "COMPLETE", metric] = np.nan
    pivot = sub.pivot(index="date", columns="variant", values=metric)
    if baseline not in pivot or challenger not in pivot:
        common = pd.DataFrame(columns=["date", "delta"])
    else:
        common = pivot[[baseline, challenger]].dropna().reset_index()
        common["delta"] = common[challenger] - common[baseline]
    interval = day_equal_bootstrap(common, "delta", date_col="date", n_boot=n_boot,
                                   alpha=0.10, seed=seed)
    n_days = int(interval.n_clusters)
    evidence = ("INSUFFICIENT_EVIDENCE" if n_days < int(min_days) else
                "PROMOTION_EVIDENCE" if interval.lo is not None and interval.lo > 0 else
                "NO_SWITCH_EVIDENCE")
    return {
        "baseline": baseline, "challenger": challenger, "stage": stage, "metric": metric,
        "n_common_complete_days": n_days, "point": interval.point,
        "lo": interval.lo, "hi": interval.hi, "alpha": interval.alpha,
        "evidence_status": evidence, "min_days": int(min_days),
    }


def _read(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(path)
    return pd.read_csv(path, dtype={"code": str})


def run(*, scan_dirs: list[Path], outcomes_path: Path, out_dir: Path,
        hybrid_core_fraction: float = 0.80, floors: dict[str, int] | None = None,
        sector_cap_frac: float = 0.20) -> Path:
    """Build an immutable research bundle from explicit frozen inputs."""
    if out_dir.exists():
        raise FileExistsError(out_dir)
    outcomes = _read(outcomes_path)
    membership_parts: list[pd.DataFrame] = []
    inputs: list[dict] = []
    for scan in sorted(map(Path, scan_dirs), key=lambda p: p.name):
        paths = {
            "full": scan / "L1_scored_full.csv",
            "current_l1": scan / "L1_recall_top1000.csv",
            "current_l2": scan / "L2_gbdt_top200.csv",
            "current_pass1": scan / "_l3_pass1_kept.csv",
        }
        frames = {name: _read(path) for name, path in paths.items()}
        membership_parts.append(build_day(
            scan.name, **frames, recall_n=len(frames["current_l1"]),
            l2_n=len(frames["current_l2"]), pass1_n=len(frames["current_pass1"]),
            floors=floors, sector_cap_frac=sector_cap_frac,
            hybrid_core_fraction=hybrid_core_fraction,
        ))
        inputs.extend({"path": str(path.resolve()), "sha256": sha256_file(path)} for path in paths.values())
    membership = pd.concat(membership_parts, ignore_index=True) if membership_parts else pd.DataFrame()
    daily = daily_metrics(membership, outcomes)
    summaries = [paired_summary(daily, baseline=CURRENT, challenger=challenger, stage=stage)
                 for challenger in (COMPOSITE_ONLY, HYBRID) for stage in STAGE_FLAGS]
    out_dir.mkdir(parents=True, exist_ok=False)
    atomic_write_bytes(out_dir / "membership.csv", membership.to_csv(index=False).encode("utf-8"))
    atomic_write_bytes(out_dir / "daily_metrics.csv", daily.to_csv(index=False).encode("utf-8"))
    atomic_write_json(out_dir / "paired_summary.json", summaries)
    inputs.append({"path": str(outcomes_path.resolve()), "sha256": sha256_file(outcomes_path)})
    atomic_write_json(out_dir / "manifest.json", {
        "schema_version": SCHEMA_VERSION, "rule_version": RULE_VERSION, "engine": ws.ENGINE,
        "created_at": datetime.now(timezone.utc).isoformat(), "inputs": inputs,
        "hybrid_core_fraction": hybrid_core_fraction, "sector_cap_frac": sector_cap_frac,
        "variants": list(VARIANTS), "ruler": ruler.MAIN_RULER,
    })
    return out_dir


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="fixed-budget funnel variant research")
    ap.add_argument("--scan-dir", action="append", required=True, type=Path)
    ap.add_argument("--outcomes", required=True, type=Path)
    ap.add_argument("--experiment-id", required=True)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--hybrid-core-fraction", type=float, default=0.80)
    args = ap.parse_args(argv)
    out = args.out or ws.reports_root() / "research" / "funnel_variants" / args.experiment_id
    result = run(scan_dirs=args.scan_dir, outcomes_path=args.outcomes, out_dir=out,
                 hybrid_core_fraction=args.hybrid_core_fraction)
    print(json.dumps({"ok": True, "out": str(result)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
