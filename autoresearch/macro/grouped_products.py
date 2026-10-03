"""Frozen six-group macro candidate and its evidence-gated comparison.

The default workflow remains serial21. This module neither changes defaults nor
infers cost savings from task counts. Artifact paths remain assembler-owned.
"""
from __future__ import annotations

from collections.abc import Mapping

from autoresearch.macro import assemble

# Every tuple names an independently validated artifact, not a merged document.
_GROUPS = (
    ("regional", ("3_regional/us.md", "3_regional/china.md", "3_regional/global.md")),
    ("crossasset", ("4_crossasset/rates.md", "4_crossasset/fx.md", "4_crossasset/equities.md",
                    "4_crossasset/commodities.md", "4_crossasset/crypto.md")),
    ("meso", ("2_meso/flows.md", "2_meso/sentiment.md", "2_meso/themes.md")),
    ("sinous", ("5_sinous/divergence.md", "5_sinous/desync.md", "5_sinous/geopolitics.md",
                "5_sinous/relative.md", "1_spine/variant.md")),
    ("risk", ("1_spine/crossfire.md", "1_spine/calendar.md", "1_spine/premortem.md")),
    ("decision", ("1_spine/decision.md", "2_meso/sector_map.md")),
)
_OPTIONALS = {
    "crossasset": "4_crossasset/credit.md",
    "meso": "6_meso_evidence/industry_cycle.md",
    "risk": "1_spine/debate.md",
}
QUALITY_CHECKS = (
    "artifact_contracts", "fact_references", "risk_coverage", "disagreement_retention",
    "allocation_rationale", "actual_measurement",
)


def grouped_products(optional_products=()) -> list[tuple[str, tuple[str, ...], list[str]]]:
    """Return the candidate's fixed outputs and complete upstream group edges."""
    optional = set(optional_products)
    if optional - set(_OPTIONALS.values()) or len(optional) != len(optional_products):
        raise ValueError("invalid macro optional products")
    groups = []
    for index, (name, relatives) in enumerate(_GROUPS):
        extra = _OPTIONALS.get(name)
        if extra in optional:
            relatives = (*relatives, extra)
        dependencies = [] if index < 3 else [f"macro.group.{prior}" for prior, _ in _GROUPS[:index]]
        groups.append((name, relatives, dependencies))
    return groups


def required_macro_products() -> set[str]:
    required = {assemble.DECISION_REL}
    for _, items in assemble.SPINE + assemble.MESO + assemble.APPENDIX:
        required.update(relative for _, relative, optional in items if not optional)
    return required



def compare_macro_candidate(
    *, engine: str, baseline_run_id: str | None, candidate_run_id: str,
    input_identity_equal: bool, config_identity_equal: bool,
    baseline_products: Mapping[str, str], candidate_products: Mapping[str, str],
    quality_checks: Mapping[str, dict], optional_products=(),
    deterministic_diffs: list[dict] | None = None,
) -> dict:
    """Keep original research text and require recorded coverage/quality/cost evidence.

    ``quality_checks`` is an explicit review input, never inferred from matching
    prose. Each of the six checks contains verdict PASS/FAIL/INCOMPLETE plus
    evidence_refs pointing to its review or real measurement artifact. PASS here
    only admits the comparison; it does not promote the candidate or claim savings.
    Config equality concerns common research settings, excluding the declared DAG
    treatment. Input equality includes raw data, intel and DecisionFrame hashes.
    """
    from autoresearch.contracts.session_comparison import build_comparison

    groups = grouped_products(optional_products)
    required = required_macro_products() | set(optional_products)
    known = {relative for _, relatives, _ in groups for relative in relatives}
    if known != required:
        raise ValueError("six-group outputs differ from assembler product contract")
    if set(quality_checks) - set(QUALITY_CHECKS):
        raise ValueError("unknown macro quality check")
    missing = []
    diffs = list(deterministic_diffs or [])
    for name, products in (("baseline", baseline_products), ("candidate", candidate_products)):
        for relative in sorted(required):
            if not isinstance(products.get(relative), str) or not products[relative].strip():
                missing.append(f"{name}.artifact:{relative}")
        if set(products) - required:
            diffs.append({"path": f"{name}.unexpected_products", "baseline": [],
                          "candidate": sorted(set(products) - required)})
    for name in QUALITY_CHECKS:
        check = quality_checks.get(name)
        if check is None:
            missing.append(f"quality:{name}")
            continue
        if not isinstance(check, dict) or set(check) != {"verdict", "evidence_refs"}:
            raise ValueError(f"invalid macro quality check: {name}")
        refs = check["evidence_refs"]
        if check["verdict"] not in {"PASS", "FAIL", "INCOMPLETE"} or not isinstance(refs, list):
            raise ValueError(f"invalid macro quality check: {name}")
        if any(not isinstance(ref, str) or not ref.strip() for ref in refs):
            raise ValueError(f"invalid macro quality evidence: {name}")
        if not refs or check["verdict"] == "INCOMPLETE":
            missing.append(f"quality:{name}")
        if check["verdict"] == "FAIL":
            diffs.append({"path": f"quality.{name}", "baseline": "PASS", "candidate": "FAIL"})
    # Deliberately do not normalize research values, phrasing, citations or ratings.
    research_diffs = [
        {"path": relative, "baseline": baseline_products.get(relative), "candidate": candidate_products.get(relative)}
        for relative in sorted(set(baseline_products) | set(candidate_products))
        if baseline_products.get(relative) != candidate_products.get(relative)
    ]
    comparison = build_comparison(
        engine=engine, workflow="macro-research", mode="FULL",
        baseline_run_id=baseline_run_id, candidate_run_id=candidate_run_id,
        input_identity_equal=input_identity_equal, config_identity_equal=config_identity_equal,
        deterministic_diffs=diffs, research_diffs=research_diffs, missing_evidence=missing,
    )
    return {
        "schema_version": 1, "baseline_profile": "serial21", "candidate_profile": "six_groups_v1",
        "required_products": sorted(required), "quality_checks": dict(quality_checks),
        "comparison": comparison,
    }
