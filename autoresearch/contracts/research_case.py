"""Versioned research-process cases; proposed labels never become human gold."""
from __future__ import annotations

import re
from datetime import date, datetime

LABELS = frozenset({"SUPPORTED", "CONTRADICTED", "INSUFFICIENT"})
FAILURE_TYPES = frozenset({"FACT_ERROR", "TIME_ERROR", "METRIC_ERROR", "MATERIAL_OMISSION",
    "INFERENCE_OVERREACH", "DUPLICATE_EVIDENCE", "ACCESS_PUBLICATION_FAILURE",
    "EXECUTION_MISMATCH", "ABSTENTION", "SUCCESS"})
REF_FIELDS = ("input_refs", "attempt_refs", "claim_refs", "decision_refs", "outcome_refs", "review_refs")
FIELDS = frozenset({"schema_version", "case_id", "engine", "event_family", "security", "analysis_date",
    "workflow", "profile", "knowledge_cutoff", "question", "expected_behavior", *REF_FIELDS,
    "label_state", "gold_label", "reviewer", "reviewed_at", "label_version", "disagreements",
    "failure_type", "severity", "suspected_stage", "confirmed_cause", "counterexample", "split",
    "group_id", "eligibility", "exclusion_reason"})


def _aware(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("case timestamp requires timezone")
    return parsed


def validate_case(value: dict) -> dict:
    if not isinstance(value, dict) or set(value) != FIELDS:
        raise ValueError("case fields differ from schema")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ValueError("unsupported case schema")
    for key in ("case_id", "event_family", "security", "workflow", "profile", "question",
                "expected_behavior", "label_version", "suspected_stage", "counterexample", "group_id"):
        if not isinstance(value[key], str) or not value[key].strip():
            raise ValueError(f"case requires {key}")
    if (value["engine"] not in {"codex", "claude"} or value["failure_type"] not in FAILURE_TYPES
            or value["severity"] not in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
            or value["split"] not in {"train", "validation", "test"}
            or value["eligibility"] not in {"CANDIDATE", "ELIGIBLE", "EXCLUDED"}):
        raise ValueError("invalid case category")
    if date.fromisoformat(value["analysis_date"]).isoformat() != value["analysis_date"]:
        raise ValueError("noncanonical case date")
    _aware(value["knowledge_cutoff"])
    for key in REF_FIELDS:
        if not isinstance(value[key], list):
            raise ValueError("case refs must be lists")
        for ref in value[key]:
            if (not isinstance(ref, dict) or set(ref) != {"path", "sha256"}
                    or not isinstance(ref["path"], str) or not ref["path"]
                    or not re.fullmatch(r"[0-9a-f]{64}", str(ref["sha256"]))):
                raise ValueError("invalid case evidence ref")
    for key in ("reviewer", "disagreements"):
        if (not isinstance(value[key], list) or any(not isinstance(x, str) or not x.strip() for x in value[key])
                or len(value[key]) != len(set(value[key]))):
            raise ValueError("invalid case review metadata")
    state = value["label_state"]
    if state == "PROPOSED":
        if any(value[k] for k in ("gold_label", "reviewer", "reviewed_at", "review_refs", "confirmed_cause")):
            raise ValueError("proposed case cannot impersonate human review")
        if value["eligibility"] == "ELIGIBLE":
            raise ValueError("proposed case is not eligible gold")
    elif state in {"HUMAN_SINGLE", "HUMAN_REVIEWED"}:
        if value["gold_label"] not in LABELS or not value["review_refs"]:
            raise ValueError("human label requires review evidence")
        required = 1 if state == "HUMAN_SINGLE" else 2
        if len(value["reviewer"]) != required or not value["reviewed_at"]:
            raise ValueError("reviewer count does not establish stated review")
        _aware(value["reviewed_at"])
        if value["eligibility"] == "ELIGIBLE":
            if not value["input_refs"] or value["disagreements"]:
                raise ValueError("eligible gold requires inputs and resolved disagreement")
            if value["severity"] in {"HIGH", "CRITICAL"} and state != "HUMAN_REVIEWED":
                raise ValueError("severe case requires second human review")
    else:
        raise ValueError("invalid label state")
    if value["eligibility"] == "ELIGIBLE":
        if value["exclusion_reason"] is not None:
            raise ValueError("eligible case cannot have exclusion reason")
    elif not isinstance(value["exclusion_reason"], str) or not value["exclusion_reason"].strip():
        raise ValueError("ineligible case requires reason")
    if value["confirmed_cause"] is not None and not isinstance(value["confirmed_cause"], str):
        raise ValueError("invalid confirmed cause")
    return value
