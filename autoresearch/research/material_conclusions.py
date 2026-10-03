"""Opt-in material_conclusion_audit_v1; declarations and review are not truth.

Consumes the same fence in stock LITE/FULL, macro FULL and sector FULL products.
This derived profile does not alter required production chapters or card gates.
References to existing source verdicts are reported, never granted here.
"""
from __future__ import annotations

import argparse
import json
import re

from autoresearch.common.atomic import canonical_json, sha256_bytes
from autoresearch.research.evidence_refs import read_json_ref, read_ref, write_derived

PROFILE = "material_conclusion_audit_v1"
WORKFLOWS = frozenset({"stock-lite", "stock-full", "macro-full", "sector-full"})
_FENCE = re.compile(r"```material-conclusions-v1\s*\n(.*?)\n```", re.S)
_FIELDS = {"conclusion_id", "statement", "kind", "target", "claim_ids",
           "calculation_refs", "premise_ids", "counterevidence_ids"}


def _strings(value):
    return (isinstance(value, list) and all(isinstance(v, str) and v.strip() for v in value)
            and len(value) == len(set(value)))


def _declarations(text):
    blocks = _FENCE.findall(text)
    if not blocks:
        return []
    if len(blocks) != 1:
        raise ValueError("one material conclusion block required")
    value = json.loads(blocks[0])
    if (not isinstance(value, dict) or set(value) != {"schema_version", "conclusions"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or not isinstance(value["conclusions"], list)):
        raise ValueError("invalid material conclusion declaration")
    seen = set()
    for row in value["conclusions"]:
        if (not isinstance(row, dict) or set(row) != _FIELDS
                or any(not isinstance(row[k], str) or not row[k].strip()
                       for k in ("conclusion_id", "statement", "target"))
                or row["conclusion_id"] in seen or row["kind"] not in {"FACT", "INFERENCE", "HYPOTHESIS"}
                or any(not _strings(row[k]) for k in ("claim_ids", "premise_ids", "counterevidence_ids"))
                or not isinstance(row["calculation_refs"], list)):
            raise ValueError("invalid or duplicate material conclusion")
        seen.add(row["conclusion_id"])
    return value["conclusions"]


def audit_material_conclusions(document_ref, *, workflow, claim_usage_refs=(), review_refs=(), declaration_ref=None):
    if workflow not in WORKFLOWS:
        raise ValueError("unsupported audit workflow")
    text = read_ref(document_ref).decode("utf-8")
    if declaration_ref is None:
        rows = _declarations(text)
    else:
        declaration = read_json_ref(declaration_ref)
        if (set(declaration) != {"schema_version", "document_sha256", "conclusions"}
                or declaration["document_sha256"] != document_ref["sha256"]):
            raise ValueError("declaration document binding mismatch")
        rows = _declarations("```material-conclusions-v1\n" + json.dumps({
            "schema_version": declaration["schema_version"], "conclusions": declaration["conclusions"]}) + "\n```")
    claims = {}
    for ref in claim_usage_refs:
        usage = read_json_ref(ref)
        from autoresearch.common import workspace as ws
        identity = usage.get("identity", {})
        if (usage.get("schema_version") != 1 or usage.get("card_sha256") != document_ref["sha256"]
                or identity.get("engine") != ws.ENGINE or not identity.get("run_id")
                or not identity.get("task_id") or type(identity.get("attempt")) is not int
                or identity["attempt"] < 1 or not isinstance(usage.get("claims"), list)):
            raise ValueError("claim usage lacks document/producer identity binding")
        for row in usage.get("claims", []):
            key = row["claim_id"]
            if key in claims and claims[key] != row:
                raise ValueError("conflicting referenced claim versions")
            claims[key] = row
    omissions, conflicts, conclusions, mapped = [], [], [], 0
    for row in rows:
        dependencies = sorted(set(row["claim_ids"] + row["premise_ids"] + row["counterevidence_ids"]))
        unresolved = []
        for key in dependencies:
            claim = claims.get(key, {})
            if claim.get("verdict") != "PASS":
                unresolved.append({"conclusion_id": row["conclusion_id"], "claim_id": key,
                                   "reason": claim.get("verdict", "MISSING_REFERENCE")})
            elif key in row["counterevidence_ids"]:
                unresolved.append({"conclusion_id": row["conclusion_id"], "claim_id": key,
                                   "reason": "SUPPORTED_COUNTEREVIDENCE_REQUIRES_REVIEW"})
        calculations = []
        for ref in row["calculation_refs"]:
            from autoresearch.contracts.calculation import validate_calculation
            calculation = validate_calculation(read_json_ref(ref))
            calculations.append(calculation["calculation_id"])
            if calculation["status"] != "SUCCEEDED":
                unresolved.append({"conclusion_id": row["conclusion_id"],
                                   "calculation_id": calculation["calculation_id"], "reason": "CALCULATION_FAILED"})
        has_basis = bool(row["claim_ids"] or calculations)
        if row["kind"] in {"INFERENCE", "HYPOTHESIS"} and not row["premise_ids"]:
            unresolved.append({"conclusion_id": row["conclusion_id"], "reason": "PREMISES_NOT_DECLARED"})
        if not has_basis:
            unresolved.append({"conclusion_id": row["conclusion_id"], "reason": "BASIS_NOT_DECLARED"})
        if has_basis and all(key in claims for key in dependencies):
            mapped += 1
        conflicts.extend(unresolved)
        conclusions.append({**row, "dependency_state": "UNRESOLVED" if unresolved else "REFERENCED_NOT_REVERIFIED",
                            "truth_status": "NOT_DETERMINED",
                            "source_receipt_ids": sorted({rid for key in dependencies
                                for rid in claims.get(key, {}).get("source_receipt_ids", [])})})
    reviewed = set()
    declarations_sha256 = sha256_bytes(canonical_json(rows).encode())
    ids = {row["conclusion_id"] for row in rows}
    for ref in review_refs:
        review = read_json_ref(ref)
        if (set(review) != {"schema_version", "document_sha256", "declarations_sha256", "reviewer_kind", "scope",
                           "omission_candidates", "unresolved_conflicts"}
                or type(review["schema_version"]) is not int or review["schema_version"] != 1
                or review["document_sha256"] != document_ref["sha256"]
                or review["declarations_sha256"] != declarations_sha256
                or review["reviewer_kind"] not in {"HUMAN", "MODEL"}
                or not _strings(review["scope"]) or not set(review["scope"]) <= ids
                or not _strings(review["omission_candidates"])
                or not _strings(review["unresolved_conflicts"])):
            raise ValueError("invalid review or document binding")
        reviewed.update(review["scope"])
        omissions.extend(review["omission_candidates"])
        conflicts.extend({"reason": item, "review_ref": ref} for item in review["unresolved_conflicts"])
    state = "DECLARED_ONLY"
    if review_refs:
        state = "AUDITED" if rows and reviewed == ids and not omissions and not conflicts else "AUDITED_WITH_GAPS"
    return {"schema_version": 1, "profile": PROFILE, "workflow": workflow,
            "document_ref": document_ref, "declaration_ref": declaration_ref, "claim_usage_refs": list(claim_usage_refs),
            "declarations_sha256": declarations_sha256,
            "review_refs": list(review_refs), "coverage_state": state,
            "known_material_count": len(rows), "mapped_count": mapped,
            "reviewed_conclusion_ids": sorted(reviewed), "omission_candidates": sorted(set(omissions)),
            "unresolved_conflicts": conflicts, "conclusions": conclusions,
            "semantic_completeness": "UNKNOWN", "semantic_scope": "DECLARED_CONCLUSIONS_ONLY",
            "source_verdict_authority": "REFERENCED_UPSTREAM_NOT_REVERIFIED",
            "reviewer_identity_assurance": "EXTERNAL_ATTESTATION_NOT_AUTHENTICATION",
            "production_effect": "NONE"}


def prepare_material_audit(spec_ref, document_ref, *, workflow):
    """Prepare an explicit offline host task without editing production reports.

    Root runs this opt-in experiment outside the production task DAG; the host
    supplies a declaration sidecar and an independent review, both bound to the
    exact original document. This function invokes no model API.
    """
    from autoresearch.contracts.research_experiment import validate_spec
    from autoresearch.research.registration import verify_engine

    spec = validate_spec(read_json_ref(spec_ref))
    verify_engine(spec)
    if spec["experiment_family"] != PROFILE or workflow not in WORKFLOWS:
        raise ValueError("unregistered material audit profile/workflow")
    text = read_ref(document_ref).decode("utf-8")
    return {"schema_version": 1, "profile": PROFILE, "spec_ref": spec_ref,
        "document_ref": document_ref, "workflow": workflow,
        "input_refs": [document_ref], "document_chars": len(text),
        "required_targets": ["three_gate_basis", "key_catalyst", "material_negative",
            "exact_ev_rr_premises", "allocation_assumptions"],
        "instruction": "读取冻结原文及原权限内的来源。逐条登记重大结论，区分FACT/INFERENCE/HYPOTHESIS；"
            "引用既有claim_id/计算ref，推断列premise_ids及counterevidence_ids。不得自填来源PASS。"
            "无适用目标说明理由，不得删除负面或未核项。声明写独立sidecar，不修改原文。"
            "独立审查者通读原文后列遗漏候选、冲突及已审conclusion_id；支持前提不等于推断成立。",
        "declaration_contract": {"schema_version": 1, "document_sha256": document_ref["sha256"],
                                  "conclusion_fields": sorted(_FIELDS)},
        "review_contract": {"schema_version": 1, "document_sha256": document_ref["sha256"],
            "declarations_sha256": "SHA256_OF_CANONICAL_COMPLETE_CONCLUSIONS_LIST",
            "reviewer_kind_enum": ["MODEL", "HUMAN"], "scope": "REVIEWED_CONCLUSION_IDS",
            "omission_candidates": "LIST_OF_TEXT", "unresolved_conflicts": "LIST_OF_TEXT"},
        "access_scope": "ORIGINAL_REGISTERED_INPUT_ACCESS_ONLY", "production_effect": "NONE"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare", action="store_true", help="freeze offline host task inputs before inference")
    parser.add_argument("--request", required=True, help="JSON with frozen spec/document/claim_usage/review refs")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    from pathlib import Path

    from autoresearch.contracts.research_experiment import validate_spec
    from autoresearch.research.evidence_refs import read_request
    from autoresearch.research.registration import verify_engine

    request = read_request(args.request)
    spec = validate_spec(read_json_ref(request["spec_ref"]))
    verify_engine(spec)
    if spec["experiment_family"] != PROFILE:
        raise ValueError("material audit requires registered experiment family")
    if args.prepare:
        result = prepare_material_audit(request["spec_ref"], request["document_ref"], workflow=request["workflow"])
        print(json.dumps(write_derived(Path(args.output), result)))
        return
    result = audit_material_conclusions(request["document_ref"], workflow=request["workflow"],
        claim_usage_refs=request.get("claim_usage_refs", []), review_refs=request.get("review_refs", []),
        declaration_ref=request.get("declaration_ref"))
    result["spec_ref"] = request["spec_ref"]
    print(json.dumps(write_derived(Path(args.output), result)))


if __name__ == "__main__":
    main()
