"""Evidence profile for first-coverage dossier runs."""
from __future__ import annotations

from dataclasses import replace

from autoresearch.contracts import stages as vocab
from autoresearch.contracts.profiles import _BASE_RULES, ArtifactRule, RunProfile

TERMINAL = ("SUCCEEDED", "FAILED", "INTERRUPTED")
_RULES: tuple[ArtifactRule, ...] = tuple(
    replace(rule, evidence_level="L1") if rule.key == "agent_index" else rule
    for rule in _BASE_RULES
)


def dossier_profile(
    *,
    mode: str = "INIT",
    business_status: str = "SUCCEEDED",
    last_stage: str | None = None,
    agent_roles: tuple[str, ...] | None = None,
    card_source: str = "legacy_md",
    role_stages: dict[str, str] | None = None,
) -> RunProfile:
    if mode not in vocab.DOSSIER_MODES:
        raise ValueError(f"unknown dossier mode: {mode!r}")
    if business_status not in TERMINAL:
        raise ValueError(f"unknown business status: {business_status!r}")
    return RunProfile(
        kind="dossier-init",
        expected_stages=vocab.DOSSIER_STAGES,
        agent_roles=("dossier.init",) if agent_roles is None else tuple(agent_roles),
        artifact_rules=_RULES,
        # Session runs project this from the exact deterministic operations that
        # reached their EvidencePlan; legacy runs must not advertise that proof.
        replayable_stages=(),
        mode=mode,
        business_status=business_status,
        last_stage=last_stage,
        captured_stages=(),
        card_source=card_source,
        role_stages=(
            dict(vocab.DOSSIER_ROLE_STAGES) if role_stages is None else role_stages
        ),
    )


__all__ = ["dossier_profile"]
