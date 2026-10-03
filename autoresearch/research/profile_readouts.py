"""Bounded observations for existing profiles; missing measurements stay unknown."""
from __future__ import annotations

from collections import Counter

STOCK_STAGES = ("reality_check", "bull", "bear", "manager", "risk", "premortem", "pm")


def sector_coverage(l3_industries, baseline, candidate):
    required = set(l3_industries)
    if any(not isinstance(x, str) or not x.strip() for x in required):
        raise ValueError("L3 sector population must be explicit")
    result = {"required_sectors": sorted(required)}
    for name, rows in (("baseline", baseline), ("candidate", candidate)):
        unknown = sorted(key for key in required & set(rows) if rows[key].get("event_status") not in
                         {"NOT_TRIGGERED", "COMPLETED", "FAILED", "UNKNOWN"})
        result[name] = {"missing_sectors": sorted(required - set(rows)), "unexpected_sectors": sorted(set(rows) - required),
            "event_not_triggered": sorted(key for key in required & set(rows) if rows[key].get("event_status") == "NOT_TRIGGERED"),
            "event_pending_or_unknown": sorted(key for key in required & set(rows)
                if rows[key].get("event_status") in {"FAILED", "UNKNOWN"}) + unknown,
            "numeric_or_direction_gaps": sorted(key for key in required & set(rows)
                if rows[key].get("numeric_rebuilt") is not True or rows[key].get("direction_free") is not True)}
    result["comparable"] = bool(required) and all(not result[name][key] for name in ("baseline", "candidate")
        for key in ("missing_sectors", "unexpected_sectors", "event_pending_or_unknown", "numeric_or_direction_gaps"))
    result["quality_noninferiority"] = "REQUIRES_SOURCE_AND_EVENT_RECALL_EVALUATION"
    return result


def stable_reuse_readout(observations):
    rows = list(observations)
    reasons = Counter(reason for row in rows for reason in row["invalidations"])
    return {"population": len(rows), "reused": sum(row["reused"] is True for row in rows),
            "invalidation_counts": dict(sorted(reasons.items())),
            "market_only_invalidations": sum(set(row["invalidations"]) == {"MARKET_CHANGED"} for row in rows),
            "price_only_invalidations": None,
            "price_attribution": "MARKET_FINGERPRINT_INCLUDES_NON_PRICE_FIELDS",
            "fact_level_reuse_adoption": "REQUIRES_FACT_DEPENDENCY_EVIDENCE", "token_savings": None}


def stock_stage_value(observations):
    rows = list(observations)
    if any(row["stage"] not in STOCK_STAGES for row in rows):
        raise ValueError("unregistered stock synthesis stage")
    output = []
    for stage in STOCK_STAGES:
        observed = [r for r in rows if r["stage"] == stage]
        item = {"stage": stage, "observations": len(observed)}
        for key in ("new_facts", "corrected_errors", "counterevidence", "decision_changes", "tokens", "wall_seconds"):
            values = [r.get(key) for r in observed]
            item[key] = sum(values) if values and all(type(v) in {int, float} and v >= 0 for v in values) else None
        output.append(item)
    return {"stages": output, "grouped3_development": "DEFER_PENDING_BASELINE_EVIDENCE",
            "reason": "Stage costs alone cannot establish quality equivalence of merging synthesis tasks."}


def scheduling_readout(segments, *, tasks):
    unique = {}
    for row in segments:
        key = row["segment_id"]
        if key in unique and unique[key] != row:
            # Snapshots within one live segment are cumulative; only the latest
            # monotonic snapshot is retained. Equal elapsed conflicting bytes fail.
            if unique[key]["elapsed_seconds"] == row["elapsed_seconds"]:
                raise ValueError("conflicting scheduling snapshots")
            if unique[key]["elapsed_seconds"] > row["elapsed_seconds"]:
                continue
        unique[key] = row
    waits = [dict(value, task_attempt=key, segment_id=row["segment_id"])
             for row in unique.values() for key, value in row["ready_queue_wait"].items()]
    by_id = {row["task_id"]: row for row in tasks}
    if len(by_id) != len(tasks):
        raise ValueError("duplicate scheduling task")
    memo, visiting = {}, set()

    def longest(key):
        if key not in by_id:
            return None
        if key in memo:
            return memo[key]
        if key in visiting:
            raise ValueError("task dependency cycle")
        visiting.add(key)
        row = by_id[key]
        upstream = [longest(dep) for dep in row["dependencies"]]
        duration = row.get("observed_duration_seconds")
        value = None if type(duration) not in {int, float} or duration < 0 or any(v is None for v in upstream) else duration + max(upstream, default=0)
        visiting.remove(key)
        memo[key] = value
        return value

    lengths = [longest(key) for key in by_id]
    return {"segments": len(unique), "observed_segment_seconds": sum(r["elapsed_seconds"] for r in unique.values()),
        "slot_idle_seconds": sum(r["slot_idle_seconds"] for r in unique.values()),
        "deterministic_lane_busy_seconds": sum(r["deterministic_lane_busy_seconds"] for r in unique.values()),
        "ready_waits": waits, "net_wall_seconds": None, "historical_intervals": "UNKNOWN",
        "critical_path_seconds": max(lengths) if lengths and all(v is not None for v in lengths) else None,
        "critical_path_basis": "OBSERVED_TASK_DURATIONS_EXCLUDES_UNOBSERVED_QUEUES",
        "scheduler_change": "DEFER_UNTIL_MEASURED_BOTTLENECK", "effective_host_capacity": "REQUIRES_CURRENT_HOST_PROOF"}


def compare_macro_refs(baseline_ref, candidate_ref, *, quality_refs):
    """Hash-verify input/product/review refs before the existing macro comparator."""
    from autoresearch.macro.grouped_products import (
        QUALITY_CHECKS,
        compare_macro_candidate,
    )
    from autoresearch.research.evidence_refs import read_json_ref, read_ref

    baseline, candidate = read_json_ref(baseline_ref), read_json_ref(candidate_ref)
    if set(quality_refs) != set(QUALITY_CHECKS):
        raise ValueError("all six macro quality evidence refs required")
    from autoresearch.common import workspace as ws
    if baseline["engine"] != candidate["engine"] or baseline["engine"] != ws.ENGINE:
        raise ValueError("macro engine mismatch")
    if baseline.get("profile") != "serial21" or candidate.get("profile") != "six_groups_v1":
        raise ValueError("macro profile identity mismatch")
    for row in (baseline, candidate):
        if not {"raw_pack", "intel", "frame", "cutoff"} <= set(row["inputs"]):
            raise ValueError("macro comparison requires complete frozen input identity")
        for ref in row["inputs"].values():
            read_ref(ref)
    checks = {}
    for key, ref in quality_refs.items():
        review = read_json_ref(ref)
        if (review.get("baseline_run_id") != baseline["run_id"] or review.get("candidate_run_id") != candidate["run_id"]
                or review.get("baseline_sha256") != baseline_ref["sha256"]
                or review.get("candidate_sha256") != candidate_ref["sha256"]):
            raise ValueError("quality review is not bound to this pair")
        checks[key] = {"verdict": review["verdict"], "evidence_refs": [ref["path"] + "#sha256=" + ref["sha256"]]}
    def products(row):
        return {key: read_ref(ref).decode() for key, ref in row["products"].items()}

    def identities(row):
        return {key: ref["sha256"] for key, ref in row["inputs"].items()}
    result = compare_macro_candidate(engine=baseline["engine"], baseline_run_id=baseline["run_id"],
        candidate_run_id=candidate["run_id"], input_identity_equal=identities(baseline) == identities(candidate),
        config_identity_equal=baseline["settings"] == candidate["settings"]
            and baseline["optional_products"] == candidate["optional_products"],
        baseline_products=products(baseline), candidate_products=products(candidate),
        quality_checks=checks, optional_products=candidate["optional_products"])
    result.update(baseline_ref=baseline_ref, candidate_ref=candidate_ref, quality_refs=quality_refs,
                  cost_savings=None, production_adoption="REQUIRES_REAL_COHORT_AND_PREREGISTERED_QUALITY")
    return result


def main():
    import argparse
    import json
    from pathlib import Path

    from autoresearch.research.evidence_refs import read_json_ref, write_derived

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=["macro", "sector", "stock-stage", "reuse", "schedule"])
    parser.add_argument("--request", required=True, help="JSON containing hash-bound observation refs")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    from autoresearch.research.evidence_refs import read_request

    request = read_request(args.request)
    if args.kind == "macro":
        result = compare_macro_refs(request["baseline_ref"], request["candidate_ref"], quality_refs=request["quality_refs"])
    elif args.kind == "sector":
        population = read_json_ref(request["population_ref"])
        result = sector_coverage(population["l3_industries"], read_json_ref(request["baseline_ref"])["sectors"],
                                 read_json_ref(request["candidate_ref"])["sectors"])
    elif args.kind == "schedule":
        result = scheduling_readout([read_json_ref(ref) for ref in request["segment_refs"]],
                                    tasks=read_json_ref(request["tasks_ref"])["tasks"])
    else:
        rows = read_json_ref(request["observations_ref"])["observations"]
        result = stock_stage_value(rows) if args.kind == "stock-stage" else stable_reuse_readout(rows)
    result["input_refs"] = request
    print(json.dumps(write_derived(Path(args.output), result)))


if __name__ == "__main__":
    main()
