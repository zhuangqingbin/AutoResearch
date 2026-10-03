"""Item-level offline quality readouts; no model calls or automatic promotion."""
from __future__ import annotations

from autoresearch.common.atomic import canonical_json, sha256_bytes
from autoresearch.contracts.research_case import LABELS, validate_case
from autoresearch.research.evidence_refs import read_json_ref


def grade_exact_fields(reference_ref, candidate_ref, *, pointers):
    """Deterministic exact values/types only; semantic grading is separate."""
    reference, candidate = read_json_ref(reference_ref), read_json_ref(candidate_ref)
    if (not isinstance(pointers, list) or not pointers or len(pointers) != len(set(pointers))
            or any(not isinstance(p, str) or not p.startswith("/") for p in pointers)):
        raise ValueError("unique JSON pointers required")

    def lookup(value, pointer):
        try:
            for key in pointer[1:].split("/"):
                key = key.replace("~1", "/").replace("~0", "~")
                value = value[int(key)] if isinstance(value, list) else value[key]
            return True, value
        except (KeyError, IndexError, TypeError, ValueError):
            return False, None

    checks = []
    for pointer in pointers:
        has_expected, expected = lookup(reference, pointer)
        has_actual, actual = lookup(candidate, pointer)
        label = "INSUFFICIENT" if not has_expected or not has_actual else (
            "SUPPORTED" if type(expected) is type(actual) and canonical_json(expected) == canonical_json(actual)
            else "CONTRADICTED")
        checks.append({"pointer": pointer, "expected": expected, "actual": actual, "label": label})
    labels = {r["label"] for r in checks}
    return {"grader_version": "exact-json-fields-v1", "reference_ref": reference_ref,
            "candidate_ref": candidate_ref, "checks": checks,
            "label": "CONTRADICTED" if "CONTRADICTED" in labels else
                     "INSUFFICIENT" if "INSUFFICIENT" in labels else "SUPPORTED"}


def label_metrics(rows):
    rows = list(rows)
    seen, pairs = set(), []
    unknown, ungraded = 0, 0
    for row in rows:
        if row["case_id"] in seen or row["gold"] not in LABELS | {None} or row["predicted"] not in LABELS | {None}:
            raise ValueError("duplicate case or invalid quality label")
        seen.add(row["case_id"])
        unknown += row["gold"] is None
        ungraded += row["predicted"] is None
        if row["gold"] is not None and row["predicted"] is not None:
            pairs.append((row["gold"], row["predicted"]))
    predicted_pass = sum(p == "SUPPORTED" for _, p in pairs)
    wrong_pass = sum(p == "SUPPORTED" and g != p for g, p in pairs)
    predicted_fail = sum(p == "CONTRADICTED" for _, p in pairs)
    wrong_fail = sum(p == "CONTRADICTED" and g != p for g, p in pairs)
    known_gold = len(rows) - unknown
    buckets = {}
    for label in sorted(LABELS):
        actual = sum(row["gold"] == label for row in rows)
        predicted = sum(p == label for _, p in pairs)
        correct = sum(g == p == label for g, p in pairs)
        buckets[label] = {"actual": actual, "predicted": predicted, "correct": correct,
                          "recall": correct / actual if actual else None,
                          "precision": correct / predicted if predicted else None}
    return {"population": len(rows), "evaluated": len(pairs), "unknown_gold": unknown, "ungraded": ungraded,
            "known_gold": known_gold, "known_gold_ungraded": known_gold - len(pairs),
            "known_gold_coverage": len(pairs) / known_gold if known_gold else None,
            "omission_rate": (known_gold - len(pairs)) / known_gold if known_gold else None,
            "accuracy": sum(g == p for g, p in pairs) / len(pairs) if pairs else None,
            "accuracy_basis": "CONDITIONAL_ON_GOLD_AND_GRADE_PRESENT",
            "false_pass_count": wrong_pass, "predicted_pass_denominator": predicted_pass,
            "false_pass_rate": wrong_pass / predicted_pass if predicted_pass else None,
            "false_fail_count": wrong_fail, "predicted_fail_denominator": predicted_fail,
            "false_fail_rate": wrong_fail / predicted_fail if predicted_fail else None,
            "labels": buckets}


def evaluate_cases(casebook_ref, *, candidate_ref, grader_spec):
    """Consume precomputed grades, bound to case bytes and grader versions.

    Human review remains external; model grader calibration is reported separately
    and never upgrades proposed cases. Missing results stay in the denominator.
    """
    book = read_json_ref(casebook_ref)
    candidate = read_json_ref(candidate_ref)
    if (set(grader_spec) != {"kind", "version", "prompt_hash", "model", "split", "calibration_ref"}
            or grader_spec["kind"] not in {"DETERMINISTIC", "MODEL"}
            or grader_spec["split"] not in {"train", "validation", "test"}
            or not grader_spec["version"]):
        raise ValueError("invalid frozen grader specification")
    grader_hash = sha256_bytes(canonical_json(grader_spec).encode())
    if candidate.get("grader_spec_hash") != grader_hash or candidate.get("casebook_sha256") != casebook_ref["sha256"]:
        raise ValueError("candidate grader/casebook identity mismatch")
    indexed = {}
    cases = {row["case_id"]: validate_case(row) for row in book["cases"]}
    if len(cases) != len(book["cases"]):
        raise ValueError("duplicate frozen case")
    for row in candidate["grades"]:
        if row["case_id"] in indexed or row["case_id"] not in cases:
            raise ValueError("duplicate or unknown graded case")
        expected = sha256_bytes(canonical_json(cases[row["case_id"]]).encode())
        if row.get("case_sha256") != expected or row.get("label") not in LABELS or not row.get("rationale"):
            raise ValueError("grade lacks case binding, label or rationale")
        indexed[row["case_id"]] = row
    rows = []
    for case in cases.values():
        if case["split"] != grader_spec["split"]:
            continue
        grade = indexed.get(case["case_id"])
        gold = case["gold_label"] if case["eligibility"] == "ELIGIBLE" else None
        rows.append({"case_id": case["case_id"], "gold": gold, "predicted": grade["label"] if grade else None,
                     "grade": grade, "failure_type": case["failure_type"], "eligibility": case["eligibility"],
                     "exclusion_reason": case["exclusion_reason"]})
    calibration = None
    if grader_spec["calibration_ref"] is not None:
        calibration = read_json_ref(grader_spec["calibration_ref"])
        if calibration.get("split") == "test" or calibration.get("grader_version") != grader_spec["version"]:
            raise ValueError("invalid grader calibration provenance")
    return {"schema_version": 1, "casebook_ref": casebook_ref, "candidate_ref": candidate_ref,
            "grader_spec": grader_spec, "rows": rows, "metrics": label_metrics(rows),
            "by_failure_type": {key: label_metrics([r for r in rows if r["failure_type"] == key])
                                for key in sorted({r["failure_type"] for r in rows})},
            "grader_calibration": calibration,
            "calibration_state": "NOT_APPLICABLE" if grader_spec["kind"] == "DETERMINISTIC" else
                "REFERENCED" if calibration else "UNKNOWN",
            "quality_improvement": "NOT_ESTABLISHED", "confidence_interval": None,
            "confidence_note": "No independent event sample or interval estimator declared; descriptive counts only.",
            "promotion": "MANUAL_REVIEW_REQUIRED"}


def main():
    import argparse
    import json
    from pathlib import Path

    from autoresearch.research.evidence_refs import write_derived

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    from autoresearch.research.evidence_refs import read_request

    request = read_request(args.request)
    result = evaluate_cases(request["casebook_ref"], candidate_ref=request["candidate_ref"],
                            grader_spec=request["grader_spec"])
    print(json.dumps(write_derived(Path(args.output), result)))


if __name__ == "__main__":
    main()
