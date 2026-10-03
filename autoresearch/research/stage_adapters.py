"""Frozen-artifact adapters for stage comparisons; never writes production state.

Unknown and failed cases remain in the population. Rating changes are not final BUY
changes. E6 ablations replay the frozen baseline before changing a single face.
"""

from __future__ import annotations

import copy
import json
import math
from collections import Counter
from pathlib import Path

from autoresearch.common.atomic import sha256_file

ADAPTER_VERSION = "stage_adapters.v1"
FACES = ("target_align", "recall_strength", "evidence", "risk_safety")
RATINGS = frozenset({"Buy", "Overweight", "Hold", "Underweight", "Sell"})
QUALITY_FIELDS = (
    "software_errors",
    "future_information",
    "boundary_violations",
    "required_evidence_missing",
    "incorrect_publications",
)


def load_frozen(reference):
    if not isinstance(reference, dict) or set(reference) != {"path", "sha256"}:
        raise ValueError("frozen artifact path and digest required")
    path = Path(reference["path"]).absolute()
    if path.resolve() != path or not path.is_file():
        raise ValueError("frozen artifact must be a canonical regular file")
    from autoresearch.common import workspace as ws

    other = "claude" if ws.ENGINE == "codex" else "codex"
    if any(part in {f"context_{other}", f"reports_{other}"} for part in path.parts):
        raise ValueError("other engine raw output forbidden; import frozen evidence explicitly")
    if sha256_file(path) != reference["sha256"]:
        raise ValueError("frozen artifact changed")
    return json.loads(path.read_text())


def _rows(document):
    result = {}
    for row in document["rows"]:
        key = row["date"], row["code"]
        if key in result or not all(isinstance(v, str) and v for v in key):
            raise ValueError("duplicate or invalid candidate identity")
        result[key] = row
    return result


def _identity(document, reference):
    if document is None:
        return None
    value = document.get("identity", {})
    required = {
        "run_id",
        "profile",
        "engine",
        "input_hash",
        "code_sha",
        "prompt_hashes",
        "config_hashes",
    }
    if not required <= set(value) or any(not value[k] for k in required):
        raise ValueError("frozen run/profile/input/code/prompt/config identity required")
    return {
        **copy.deepcopy(value),
        "artifact": dict(reference),
        "metering_ref": document.get("metering_ref"),
        "force_full": document.get("force_full"),
        "evidence_depth": document.get("evidence_depth"),
    }


def _selected(rating, ratings):
    return None if not isinstance(rating, str) or rating not in RATINGS else rating in ratings


def coverage(rows):
    dates = {r["date"] for r in rows}
    unknown = [r for r in rows if r["baseline"] is None or r["refined"] is None]
    unknown_dates = {r["date"] for r in unknown}
    sectors = Counter(r["sector"] for r in rows if r.get("sector"))
    total = sum(sectors.values())
    return {
        "status": "UNKNOWN" if unknown else "COMPLETE",
        "date_count": len(dates),
        "security_days": len(rows),
        "unknown_rows": len(unknown),
        "paired_dates": len(dates - unknown_dates),
        "failed_rows": sum(r.get("status") in {"FAILED", "MISSING", "INCOMPLETE"} for r in rows),
        "rejected_rows": sum(r["refined"] is False for r in rows),
        "unselected_rows": sum(r["baseline"] is False and r["refined"] is False for r in rows),
        "missing_outcomes": sum(r.get("value") is None for r in rows),
        "industry_counts": dict(sorted(sectors.items())),
        "industry_missing": len(rows) - total,
        "industry_hhi": sum((n / total) ** 2 for n in sectors.values()) if total else None,
    }


def quality_gate(counts):
    if set(counts) != set(QUALITY_FIELDS) or any(
        type(v) is not int or v < 0 for v in counts.values()
    ):
        raise ValueError("all zero-tolerance quality counts must be explicitly measured")
    violations = [k for k in QUALITY_FIELDS if counts[k]]
    return {
        "status": "PASS" if not violations else "FAIL",
        "violations": violations,
        "promotion_allowed": not violations,
        "automatic_production_change": False,
    }


def _result(rows, *, sides, ratings, kind):
    if (
        not isinstance(ratings, list)
        or not ratings
        or any(r not in RATINGS for r in ratings)
        or len(ratings) != len(set(ratings))
    ):
        raise ValueError("fixed explicit rating selection predicate required")
    return {
        "adapter_version": ADAPTER_VERSION,
        "comparison": kind,
        "sides": sides,
        "selection_predicate": {"rating_in": list(ratings)},
        "selection_semantics": "RATING_SELECTION_NOT_FINAL_BUY",
        "rows": rows,
        "coverage": coverage(rows),
        "missing_metadata": [
            f"{side}.{key}"
            for side, item in sides.items()
            for key in ("metering_ref", "force_full", "evidence_depth")
            if item is None or item.get(key) is None
        ],
    }


def ensemble_pairs(reference, *, selected_ratings):
    """Required review population, post-verify/pre-ensemble versus complete final review."""
    document = load_frozen(reference)
    identity = _identity(document, reference)
    rows = []
    for row in _rows(document).values():
        if row.get("review_required") is not True:
            continue
        baseline = row.get("post_verify_rating")
        if baseline is None and row.get("verify_artifact") is not None:
            verified = load_frozen(row["verify_artifact"])
            if (verified.get("date"), verified.get("code")) != (row["date"], row["code"]):
                raise ValueError("verify artifact candidate mismatch")
            baseline = verified.get("post_verify_rating")
        final = row.get("final_rating") if row.get("review_status") == "COMPLETE" else None
        rows.append(
            {
                **row,
                "baseline": _selected(baseline, selected_ratings),
                "refined": _selected(final, selected_ratings),
                "status": row.get("review_status", "MISSING"),
            }
        )
    return _result(
        rows,
        sides={"baseline": identity, "refined": identity},
        ratings=selected_ratings,
        kind="ensemble_post_verify",
    )


def b3_pairs(one_stage, two_stage, *, selected_ratings):
    """Two separately produced artifacts; initial judgment is only an evidence-change readout."""
    left, right = (load_frozen(ref) if ref is not None else None for ref in (one_stage, two_stage))
    if left is None and right is None:
        raise ValueError("at least one actual artifact required to preserve its population")
    sides = {"baseline": _identity(left, one_stage), "refined": _identity(right, two_stage)}
    for document, profile in ((left, "one_stage"), (right, "two_stage")):
        if document and document["identity"]["profile"] != profile:
            raise ValueError("wrong frozen stage profile")
    if left and right:
        for key in ("engine", "input_hash", "code_sha", "prompt_hashes", "config_hashes"):
            if left["identity"][key] != right["identity"][key]:
                raise ValueError(f"comparison identity mismatch: {key}")
        if one_stage == two_stage or left["identity"]["run_id"] == right["identity"]["run_id"]:
            raise ValueError("two actual independent artifacts required")
    first, second = (_rows(doc) if doc else {} for doc in (left, right))
    rows, changes = [], []
    for key in sorted(first.keys() | second.keys()):
        a, b = first.get(key, {}), second.get(key, {})
        if a.get("value") is not None and b.get("value") is not None and a["value"] != b["value"]:
            raise ValueError("paired label mismatch")
        row = {
            **(a or b),
            "baseline": _selected(a.get("final_rating"), selected_ratings),
            "refined": _selected(b.get("final_rating"), selected_ratings),
        }
        if a.get("status") in {"FAILED", "MISSING", "INCOMPLETE"}:
            row["baseline"] = None
        if b.get("status") in {"FAILED", "MISSING", "INCOMPLETE"}:
            row["refined"] = None
        rows.append(row)
        if "initial_rating" in b:
            changes.append(
                {
                    "date": key[0],
                    "code": key[1],
                    "initial": b["initial_rating"],
                    "final": b.get("final_rating"),
                    "changed": b["initial_rating"] != b.get("final_rating"),
                    "semantics": "ADDITIONAL_EVIDENCE_CHANGE_NOT_CANDIDATE_VALUE",
                }
            )
    result = _result(rows, sides=sides, ratings=selected_ratings, kind="one_stage_vs_two_stage")
    result["additional_evidence_changes"] = changes
    return result


def _number(value):
    if type(value) not in {int, float} or not math.isfinite(value):
        raise ValueError("finite frozen sorting value required")
    return value


def _e6_selection(document, remove_face=None):
    pool = document["pool"]
    if pool not in {"composite", "finalists"}:
        raise ValueError("unknown selection pool")
    for key in ("tiering", "exclude_pinned"):
        if type(document[key]) is not bool:
            raise ValueError("explicit frozen selection switches required")
    max_buys = document["rule_params"]["max_buys"]
    if type(max_buys) is not int or max_buys < 1:
        raise ValueError("invalid frozen max_buys")
    rows, seen = [], set()
    for row in document["candidates"]:
        if row["code"] in seen:
            raise ValueError("duplicate E6 candidate")
        seen.add(row["code"])
        if any(type(row[k]) is not bool for k in ("eligible", "pinned", "in_pool")):
            raise ValueError("explicit candidate flags required")
        gates = row["hard_gate"]
        if (
            not gates
            or any(type(v) is not bool for v in gates.values())
            or all(gates.values()) != row["eligible"]
        ):
            raise ValueError("hard gate/eligibility mismatch")
        if (
            row["eligible"]
            and row["in_pool"]
            and not (document["exclude_pinned"] and row["pinned"])
        ):
            rows.append(row)
    names = (
        ["target_align", "amount", "code"]
        if pool == "composite"
        else ["relative_decision_score", "target_align", "amount", "code"]
    )
    keys = document["selection"]["sort_keys"]
    if keys["names"] != names or keys["directions"] != ["desc"] * (len(names) - 1) + ["asc"]:
        raise ValueError("unsupported frozen sort keys")
    if set(keys["values"]) != {r["code"] for r in rows}:
        raise ValueError("frozen sort population mismatch")
    values = {}
    for row in rows:
        code = row["code"]
        recorded = dict(zip(names, keys["values"][code], strict=True))
        faces = row["faces"]
        if set(faces) != set(FACES) or any(not 0 <= _number(v) <= 1 for v in faces.values()):
            raise ValueError("frozen percentile faces required")
        score = round(sum(faces.values()) / 4, 6)
        if (
            score != row["relative_decision_score"]
            or recorded["target_align"] != faces["target_align"]
            or recorded["code"] != code
        ):
            raise ValueError("baseline frozen score mismatch")
        if pool == "finalists" and recorded["relative_decision_score"] != score:
            raise ValueError("baseline sorting score mismatch")
        amount = recorded["amount"]
        if amount is not None:
            _number(amount)
        if remove_face is not None:
            score = round(sum(faces[name] for name in FACES if name != remove_face) / 3, 6)
        values[code] = {
            "relative_decision_score": score,
            "target_align": faces["target_align"],
            "amount": amount or 0,
            "code": code,
        }
    ordered = sorted(
        rows,
        key=lambda row: tuple(
            values[row["code"]][k] if k == "code" else -values[row["code"]][k] for k in names
        ),
    )
    chosen, tier, basis = ordered, None, "relative"
    if document["tiering"]:
        a = [r for r in ordered if r["card_context"]["entry_stance"] == "ALLOWED"]
        chosen = a or [r for r in ordered if r["card_context"]["entry_stance"] != "ALLOWED"]
        tier, basis = ("A", "card_backed") if a else ("R", "relative_forced")
    buys = [
        {"code": row["code"], "basis": basis, "rank": i, **({"tier": tier} if tier else {})}
        for i, row in enumerate(chosen[:max_buys], 1)
    ]
    return {"codes": [row["code"] for row in ordered], "buys": buys}


def e6_ablation(reference, *, remove_face):
    if remove_face not in {"recall_strength", "evidence"}:
        raise ValueError("only preregistered recall/evidence ablations supported")
    document = load_frozen(reference)
    baseline = _e6_selection(document)
    if baseline["codes"] != document["selection"]["codes"] or baseline["buys"] != document["buys"]:
        raise ValueError("baseline selection/buys/tier replay mismatch")
    used = document["pool"] == "finalists"
    refined = _e6_selection(document, remove_face) if used else copy.deepcopy(baseline)
    return {
        "adapter_version": ADAPTER_VERSION,
        "artifact": reference,
        "date": document["date"],
        "remove_face": remove_face,
        "baseline": baseline,
        "refined": refined,
        "factor_used_in_selection": used,
        "unchanged": baseline == refined,
        "candidates": copy.deepcopy(document["candidates"]),
        "selection_predicate": "FROZEN_ELIGIBLE_POOL_PINNED_TIER_MAX_BUYS",
        "sides": {"baseline": document.get("identity"), "refined": document.get("identity")},
        "metering_ref": document.get("metering_ref"),
        "missing_metadata": [
            k
            for k in ("identity", "metering_ref", "force_full", "evidence_depth")
            if document.get(k) is None
        ],
    }


def evaluate_adapter(result, *, stage, evidence_root, seed, n_boot, min_scan_days=60):
    """Use existing statistics; an unknown selection blocks the entire affected day."""
    import pandas as pd

    from autoresearch.research.stage_value import evaluate_candidates, summarize_daily

    rows = result["rows"]
    blocked = {r["date"] for r in rows if r["baseline"] is None or r["refined"] is None}
    known = [
        {k: r.get(k) for k in ("date", "code", "baseline", "refined", "value")}
        for r in rows
        if r["date"] not in blocked
    ]
    evidence = None
    daily = pd.DataFrame(columns=["date", "baseline", "refined", "delta", "status"])
    if known:
        daily, evidence = evaluate_candidates(
            pd.DataFrame(known), stage=stage, ruler="gap_c1_o2", evidence_root=evidence_root
        )
    if blocked:
        daily = pd.concat(
            [
                daily,
                pd.DataFrame(
                    [
                        {
                            "date": day,
                            "baseline": None,
                            "refined": None,
                            "delta": None,
                            "status": "UNKNOWN_SELECTION",
                        }
                        for day in sorted(blocked)
                    ]
                ),
            ],
            ignore_index=True,
        )
    return {
        "daily": daily.to_dict("records"),
        "statistics": summarize_daily(daily, seed=seed, n_boot=n_boot, min_scan_days=min_scan_days),
        "coverage": result["coverage"],
        "operation_evidence": evidence,
        "adapter": result,
    }


def e6_pairs(reference, outcomes, *, remove_face):
    comparison = e6_ablation(reference, remove_face=remove_face)
    labels = _rows(load_frozen(outcomes))
    left = {row["code"] for row in comparison["baseline"]["buys"]}
    right = {row["code"] for row in comparison["refined"]["buys"]}
    rows = []
    for candidate in comparison["candidates"]:
        label = labels.get((comparison["date"], candidate["code"]), {})
        rows.append(
            {
                "date": comparison["date"],
                "code": candidate["code"],
                "baseline": candidate["code"] in left,
                "refined": candidate["code"] in right,
                "value": label.get("value") if label.get("status") == "MATURE" else None,
                "sector": candidate.get("sector"),
            }
        )
    return {**comparison, "outcomes": dict(outcomes), "rows": rows, "coverage": coverage(rows)}


def main(argv=None):
    """Produce an exclusive comparison artifact; failed attempts remain on disk."""
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    ensemble = sub.add_parser("ensemble")
    ensemble.add_argument("--reference", required=True, help="JSON {path,sha256}")
    ensemble.add_argument("--selected-rating", action="append", required=True)
    b3 = sub.add_parser("b3")
    b3.add_argument("--one-stage", help="frozen reference JSON; missing remains UNKNOWN")
    b3.add_argument("--two-stage", help="frozen reference JSON; missing remains UNKNOWN")
    b3.add_argument("--selected-rating", action="append", required=True)
    e6 = sub.add_parser("e6")
    e6.add_argument("--reference", required=True)
    e6.add_argument("--outcomes", help="frozen label reference JSON")
    e6.add_argument("--remove-face", choices=["recall_strength", "evidence"], required=True)
    for child in (ensemble, b3, e6):
        child.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    from autoresearch.research.forward_study import _safe

    def read(path):
        return json.loads(_safe(path).read_text()) if path else None

    with _safe(args.output).open("x") as stream:
        try:
            if args.command == "ensemble":
                result = ensemble_pairs(read(args.reference), selected_ratings=args.selected_rating)
            elif args.command == "b3":
                result = b3_pairs(
                    read(args.one_stage),
                    read(args.two_stage),
                    selected_ratings=args.selected_rating,
                )
            elif args.outcomes:
                result = e6_pairs(
                    read(args.reference), read(args.outcomes), remove_face=args.remove_face
                )
            else:
                result = e6_ablation(read(args.reference), remove_face=args.remove_face)
            value = {"status": "COMPLETE", "comparison": result}
            code = 0
        except (ValueError, KeyError, TypeError, OSError) as exc:
            value = {
                "status": "FAILED",
                "adapter_version": ADAPTER_VERSION,
                "command": args.command,
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
            code = 2
        json.dump(value, stream, ensure_ascii=False, allow_nan=False, sort_keys=True)
        stream.write("\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
