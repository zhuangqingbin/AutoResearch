"""Paired one-factor experiments, clustered by event; no production routing."""
from __future__ import annotations

import math
import random
from collections import defaultdict

FACTORS = frozenset({"model", "effort", "context_profile", "search_profile"})
COMMON = ("engine", "event_id", "role", "difficulty", "input_hash", "allowed_tools_hash",
          "source_coverage", "cache_condition")


def _finite(value, *, nonnegative=True):
    return type(value) in {int, float} and math.isfinite(value) and (value >= 0 or not nonnegative)


def compare_stage_effort(pairs, *, protocol):
    required = {"factor", "baseline", "candidate", "min_events", "noninferiority_margin",
                "bootstrap_samples", "seed", "confidence"}
    if (not isinstance(protocol, dict) or not required <= set(protocol)
            or set(protocol) - required - {"model_resolution"} or protocol["factor"] not in FACTORS
            or protocol["baseline"] == protocol["candidate"]
            or type(protocol["min_events"]) is not int or protocol["min_events"] < 10
            or type(protocol["bootstrap_samples"]) is not int or protocol["bootstrap_samples"] < 100
            or type(protocol["seed"]) is not int or not _finite(protocol["noninferiority_margin"])
            or not _finite(protocol["confidence"]) or not 0 < protocol["confidence"] < 1):
        raise ValueError("invalid frozen one-factor protocol")
    resolution = protocol.get("model_resolution")
    if resolution is not None and (not isinstance(resolution, dict) or set(resolution) != {"baseline", "candidate"}
            or any(not isinstance(v, str) or not v or v == "UNKNOWN" for v in resolution.values())):
        raise ValueError("invalid frozen model resolution")
    rows, seen, by_event = [], set(), defaultdict(list)
    for pair in pairs:
        baseline, candidate = pair["baseline"], pair["candidate"]
        reasons = []
        for row in (baseline, candidate):
            identity = (row["engine"], row["run_id"])
            if identity in seen:
                raise ValueError("duplicate run in paired comparison")
            seen.add(identity)
            if set(row["settings"]) != FACTORS:
                raise ValueError("factor settings must be explicit")
            for key in ("quality_loss", "critical_errors", "access_faults", "publication_faults",
                        "total_tokens", "wall_seconds", "retry_count"):
                if row.get(key) is not None and not _finite(row[key]):
                    raise ValueError(f"invalid observation: {key}")
            for key in ("real_session", "metering_complete", "escalated"):
                if type(row.get(key)) is not bool:
                    raise ValueError(f"explicit observation flag required: {key}")
            if row["real_session"] is not True:
                reasons.append("NOT_REAL_SESSION")
            if row["metering_complete"] is not True or row["total_tokens"] is None or row["wall_seconds"] is None:
                reasons.append("METERING_INCOMPLETE")
            if not row.get("observed_model") or row["observed_model"] == "UNKNOWN":
                reasons.append("OBSERVED_MODEL_UNKNOWN")
            if row["quality_loss"] is None or any(row[key] is None for key in
                ("critical_errors", "access_faults", "publication_faults")):
                reasons.append("QUALITY_UNKNOWN")
        changed = {key for key in FACTORS if baseline["settings"][key] != candidate["settings"][key]}
        factor = protocol["factor"]
        if (changed != {factor} or baseline["settings"][factor] != protocol["baseline"]
                or candidate["settings"][factor] != protocol["candidate"]):
            raise ValueError("comparison must change exactly the registered factor")
        if any(baseline[key] != candidate[key] for key in COMMON):
            reasons.append("COHORT_OR_INPUT_MISMATCH")
        if factor != "model" and baseline["observed_model"] != candidate["observed_model"]:
            reasons.append("OBSERVED_MODEL_CHANGED")
        if factor == "model" and baseline["observed_model"] == candidate["observed_model"]:
            reasons.append("MODEL_TREATMENT_NOT_OBSERVED")
        if factor == "model" and (resolution is None or any(row["observed_model"] != resolution[name]
                for name, row in (("baseline", baseline), ("candidate", candidate)))):
            reasons.append("MODEL_RESOLUTION_NOT_VERIFIED")
        if factor == "effort" and any(row.get("observed_effort") != row["settings"]["effort"]
                                      for row in (baseline, candidate)):
            reasons.append("EFFORT_TREATMENT_NOT_OBSERVED")
        if factor != "effort" and (baseline.get("observed_effort") != candidate.get("observed_effort")
                or any(not row.get("observed_effort") or row.get("observed_effort") == "UNKNOWN"
                    or row["observed_effort"] != row["settings"]["effort"] for row in (baseline, candidate))):
            reasons.append("ACTUAL_EFFORT_CONTROL_NOT_VERIFIED")
        if baseline["source_coverage"] != "COMPLETE" or candidate["source_coverage"] != "COMPLETE":
            reasons.append("SOURCE_COVERAGE_INCOMPLETE")
        admitted = not reasons
        row = {"baseline_run_id": baseline["run_id"], "candidate_run_id": candidate["run_id"],
            "event_id": baseline["event_id"], "difficulty": baseline["difficulty"], "admitted": admitted,
            "reasons": sorted(set(reasons)), "quality_difference": None, "token_difference": None,
            "wall_difference": None, "candidate_escalated": candidate["escalated"],
            "candidate_retries": candidate["retry_count"],
            "hard_failure": any(candidate.get(k) is not None and candidate[k] > 0
                                for k in ("critical_errors", "access_faults", "publication_faults"))}
        if admitted:
            row.update(quality_difference=candidate["quality_loss"] - baseline["quality_loss"],
                token_difference=candidate["total_tokens"] - baseline["total_tokens"],
                wall_difference=candidate["wall_seconds"] - baseline["wall_seconds"],
                hard_failure=any(candidate[k] > 0 for k in ("critical_errors", "access_faults", "publication_faults")))
            by_event[baseline["event_id"]].append(row)
        rows.append(row)
    event_rows = [{"event_id": key, **{metric: sum(r[metric] for r in group) / len(group)
        for metric in ("quality_difference", "token_difference", "wall_difference")}}
        for key, group in sorted(by_event.items())]
    n = len(event_rows)
    bounds, verdict = None, "INSUFFICIENT"
    if n >= protocol["min_events"]:
        rng = random.Random(protocol["seed"])
        draws = sorted(sum(event_rows[rng.randrange(n)]["quality_difference"] for _ in range(n)) / n
                       for _ in range(protocol["bootstrap_samples"]))
        alpha = (1 - protocol["confidence"]) / 2
        bounds = [draws[int(alpha * (len(draws) - 1))], draws[math.ceil((1 - alpha) * (len(draws) - 1))]]
        verdict = "PASS" if bounds[1] <= protocol["noninferiority_margin"] else "FAIL"
    if any(row["hard_failure"] for row in rows):
        verdict = "FAIL"
    def mean(key):
        return sum(r[key] for r in event_rows) / n if n else None
    return {"schema_version": 1, "protocol": protocol, "pairs": rows, "event_means": event_rows,
        "admitted_pairs": sum(r["admitted"] for r in rows), "effective_events": n,
        "quality_noninferiority": verdict, "quality_difference_interval": bounds,
        "mean_token_difference": mean("token_difference"), "mean_wall_difference": mean("wall_difference"),
        "candidate_eligible": verdict == "PASS" and mean("token_difference") < 0 and all(r["admitted"] for r in rows),
        "production_adoption": "NOT_AUTHORIZED_BY_THIS_READOUT",
        "evidence_assurance": "CALLER_OBSERVATIONS_REQUIRE_HOST_AND_GRADER_PROVENANCE_REVIEW",
        "difficulty_counts": {key: sum(r["difficulty"] == key for r in rows) for key in sorted({r["difficulty"] for r in rows})}}


def main():
    import argparse
    import json
    from pathlib import Path

    from autoresearch.contracts.research_experiment import validate_spec
    from autoresearch.research.evidence_refs import read_json_ref, write_derived
    from autoresearch.research.registration import verify_engine

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    from autoresearch.research.evidence_refs import read_request

    request = read_request(args.request)
    spec = validate_spec(read_json_ref(request["spec_ref"]))
    verify_engine(spec)
    if spec["experiment_family"] != "stage_effort_v1":
        raise ValueError("unregistered stage effort experiment")
    observations = read_json_ref(request["pairs_ref"])
    result = compare_stage_effort(observations["pairs"], protocol=spec["quality_constraints"])
    result.update(spec_ref=request["spec_ref"], pairs_ref=request["pairs_ref"])
    print(json.dumps(write_derived(Path(args.output), result)))


if __name__ == "__main__":
    main()
