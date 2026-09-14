"""Evidence profiles for standalone macro research runs."""
from __future__ import annotations

from dataclasses import replace

from autoresearch.contracts import stages as vocab
from autoresearch.contracts.profiles import _BASE_RULES, ArtifactRule, RunProfile

TERMINAL = ("SUCCEEDED", "FAILED", "INTERRUPTED")
_RULES: tuple[ArtifactRule, ...] = tuple(
    replace(rule, evidence_level="L1") if rule.key == "agent_index" else rule
    for rule in _BASE_RULES
)


def macro_profile(
    *,
    mode: str = "FULL",
    business_status: str = "SUCCEEDED",
    last_stage: str | None = None,
    agent_roles: tuple[str, ...] | None = None,
    card_source: str = "legacy_md",
    role_stages: dict[str, str] | None = None,
) -> RunProfile:
    if mode not in vocab.MACRO_MODES:
        raise ValueError(f"unknown macro mode: {mode!r}")
    if business_status not in TERMINAL:
        raise ValueError(f"unknown business status: {business_status!r}")
    default_roles = ("macro.brief",) if mode == "LITE" else ("macro.research",)
    return RunProfile(
        kind="macro-research",
        expected_stages=(
            vocab.MACRO_LITE_STAGES if mode == "LITE" else vocab.MACRO_STAGES
        ),
        agent_roles=tuple(agent_roles) if agent_roles is not None else default_roles,
        artifact_rules=_RULES,
        # Session runs replace this legacy default with a projection of their frozen
        # ReplayPlan; a tuple here would otherwise advertise work that never ran.
        replayable_stages=(),
        mode=mode,
        business_status=business_status,
        last_stage=last_stage,
        captured_stages=(),
        card_source=card_source,
        role_stages=role_stages or dict(vocab.MACRO_ROLE_STAGES),
    )


__all__ = ["macro_profile"]
