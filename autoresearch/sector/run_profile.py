"""Evidence profiles for standalone sector research runs."""
from __future__ import annotations

from dataclasses import replace

from autoresearch.contracts import stages as vocab
from autoresearch.contracts.profiles import _BASE_RULES, ArtifactRule, RunProfile

TERMINAL = ("SUCCEEDED", "FAILED", "INTERRUPTED")
_RULES: tuple[ArtifactRule, ...] = tuple(
    replace(rule, evidence_level="L1") if rule.key == "agent_index" else rule
    for rule in _BASE_RULES
)


def sector_profile(
    *,
    mode: str = "FULL",
    business_status: str = "SUCCEEDED",
    last_stage: str | None = None,
    agent_roles: tuple[str, ...] | None = None,
    card_source: str = "legacy_md",
    role_stages: dict[str, str] | None = None,
) -> RunProfile:
    if mode not in vocab.SECTOR_MODES:
        raise ValueError(f"unknown sector mode: {mode!r}")
    if business_status not in TERMINAL:
        raise ValueError(f"unknown business status: {business_status!r}")
    default_roles = (
        ("sector.brief",)
        if mode == "LITE"
        else ("sector.intel", "sector.research")
    )
    return RunProfile(
        kind="sector-research",
        expected_stages=(
            vocab.SECTOR_LITE_STAGES if mode == "LITE" else vocab.SECTOR_STAGES
        ),
        agent_roles=tuple(agent_roles) if agent_roles is not None else default_roles,
        artifact_rules=_RULES,
        replayable_stages=(),
        mode=mode,
        business_status=business_status,
        last_stage=last_stage,
        captured_stages=(),
        card_source=card_source,
        role_stages=(
            dict(vocab.SECTOR_ROLE_STAGES) if role_stages is None else role_stages
        ),
    )


__all__ = ["sector_profile"]
