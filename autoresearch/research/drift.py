"""Explicit drift triggers for manual canary review, never prompt mutation."""
from __future__ import annotations

FIELDS = ("observed_model", "host_adapter_hash", "source_schema_hash", "prompt_hash", "code_sha")


def drift_report(previous, current):
    if not isinstance(previous, dict) or not isinstance(current, dict):
        raise ValueError("version observations must be objects")
    unknown = [key for key in FIELDS if any(value.get(key) in (None, "", "UNKNOWN") for value in (previous, current))]
    changed = [key for key in FIELDS if key not in unknown and previous[key] != current[key]]
    model_state = "UNKNOWN" if "observed_model" in unknown else "CHANGED" if "observed_model" in changed else "SAME_OBSERVED_ID"
    return {"schema_version": 1, "previous": previous, "current": current, "changed_fields": changed,
        "unknown_fields": unknown, "model_state": model_state, "canary_required": bool(changed or unknown),
        "automatic_prompt_update": False, "automatic_gate_relaxation": False,
        "canary_scope": "FROZEN_UNCONTAMINATED_FACT_NUMERIC_ACCESS_CONTRACT_COST_CASES",
        "interpretation": "A canary PASS covers only its frozen cases; requested model identity is not observation."}


def main():
    import argparse
    import json
    from pathlib import Path

    from autoresearch.research.evidence_refs import read_json_ref, write_derived

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True, help="JSON with previous_ref/current_ref")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    from autoresearch.research.evidence_refs import read_request

    request = read_request(args.request)
    result = drift_report(read_json_ref(request["previous_ref"]), read_json_ref(request["current_ref"]))
    result["input_refs"] = request
    print(json.dumps(write_derived(Path(args.output), result)))


if __name__ == "__main__":
    main()
