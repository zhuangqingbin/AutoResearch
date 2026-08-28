"""What evidence one scan run *owes*, declared before anything is checked.

Completeness is only meaningful against an explicit expectation.  This module
holds that expectation for `scan-market`: which stages run, which agent roles
exist, which artifacts each mode owes, and which stages are deterministic
enough to replay.  A sentinel run legitimately has no L4 leg — that is
`NOT_EXPECTED`, not a missing file — and a run that failed at L3 never reached
L4 — that is `NOT_REACHED`, also not a missing file.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Ordered pipeline stages.  Order is load-bearing: everything after the last
# reached stage of a failed run is NOT_REACHED, not missing.
SCAN_STAGES: tuple[str, ...] = (
    "frame",
    "prelude",
    "gate1",
    "l3",
    "gate2",
    "l4",
    "l5",
    "observe",
    "gate4",
)

# Business agent roles.  Deterministic relays (gp-shell / trace-control) are
# deliberately absent: their evidence is the captured command, not a transcript.
SCAN_AGENT_ROLES: tuple[str, ...] = (
    "strategist",
    "sector-brief",
    "l3-rank",
    "l4-card",
    "l4-intel",
)

# Legs that only exist when something triggered them; their absence is a fact
# about the run, not a hole in the evidence.
CONDITIONAL_AGENT_ROLES: frozenset[str] = frozenset({"l3-repair", "l4-ensemble"})

# A sentinel (no-finalist) run never dispatches per-stock work.
SENTINEL_SKIPPED_STAGES: frozenset[str] = frozenset({"l4"})
SENTINEL_SKIPPED_ROLES: frozenset[str] = frozenset(
    {"l4-card", "l4-intel", "l4-ensemble"}
)

MODES: tuple[str, ...] = ("FULL", "FORCED_FULL", "SENTINEL_EMPTY")
TERMINAL_STATUSES: tuple[str, ...] = (
    "ACTIVE",
    "SUCCEEDED",
    "FAILED",
    "INTERRUPTED",
)

REPLAYABLE_STAGES: tuple[str, ...] = ("l0", "l1", "l2", "l5")


@dataclass(frozen=True)
class ArtifactRule:
    """One declarative evidence rule.

    ``selector`` is capsule-relative and is also the rule's stable lookup key.
    ``source`` says who answers it: the capsule filesystem, the agent index, the
    read lineage, or the replay probe.  ``required_when`` names the condition
    under which the artifact is owed at all.
    """

    key: str
    selector: str
    source: str
    required_when: str


@dataclass(frozen=True)
class RunProfile:
    kind: str
    expected_stages: tuple[str, ...]
    agent_roles: tuple[str, ...]
    artifact_rules: tuple[ArtifactRule, ...]
    replayable_stages: tuple[str, ...]
    mode: str = "FULL"
    business_status: str = "SUCCEEDED"
    last_stage: str | None = None
    conditional_roles: frozenset[str] = field(default_factory=frozenset)

    def stage_reached(self, stage: str) -> bool:
        """True when *stage* is at or before the last stage this run reached."""
        if stage not in self.expected_stages:
            return False
        if self.business_status in {"SUCCEEDED", "ACTIVE"} or self.last_stage is None:
            return True
        if self.last_stage not in self.expected_stages:
            return True
        return self.expected_stages.index(stage) <= self.expected_stages.index(
            self.last_stage
        )

    def role_expected(self, role: str) -> bool:
        if self.mode == "SENTINEL_EMPTY" and role in SENTINEL_SKIPPED_ROLES:
            return False
        return role in self.agent_roles


# Rules that do not depend on stage or role expansion.  ``required_when``:
#   always      —— every run, whatever its terminal state
#   llm_run     —— any run that dispatched at least one business agent
#   failure     —— FAILED / INTERRUPTED runs only
#   replayable  —— runs with at least one deterministically replayable stage
_BASE_RULES: tuple[ArtifactRule, ...] = (
    ArtifactRule("run_contract", "identity/run_contract.json", "capsule", "always"),
    ArtifactRule("environment", "identity/environment.json", "capsule", "always"),
    ArtifactRule("dependencies", "identity/dependencies.txt", "capsule", "always"),
    ArtifactRule(
        "source_manifest", "identity/source_manifest.json", "capsule", "always"
    ),
    ArtifactRule("prompts", "identity/prompts/*", "capsule", "llm_run"),
    ArtifactRule("events", "events/events.jsonl", "capsule", "always"),
    ArtifactRule("agent_index", "agents/index.json", "capsule", "llm_run"),
    ArtifactRule("reads", "lineage/reads.jsonl", "capsule", "always"),
    ArtifactRule("source_coverage", "lineage/coverage.json", "capsule", "always"),
    ArtifactRule("usage_ledger", "usage/_token_usage.json", "capsule", "llm_run"),
    ArtifactRule("products", "products/staging/*", "capsule", "always"),
    ArtifactRule("capsule_manifest", "capsule.json", "capsule", "always"),
    ArtifactRule("failure", "failure.json", "capsule", "failure"),
    ArtifactRule("replay", "verification/replay.json", "capsule", "replayable"),
)


def scan_profile(
    *,
    mode: str = "FULL",
    business_status: str = "SUCCEEDED",
    last_stage: str | None = None,
    agent_roles: tuple[str, ...] | None = None,
    replayable_stages: tuple[str, ...] = REPLAYABLE_STAGES,
) -> RunProfile:
    """Build the `scan-market` evidence profile for one run's mode and terminal state."""
    if mode not in MODES:
        raise ValueError(f"unknown run mode: {mode!r}; expected one of {MODES!r}")
    if business_status not in TERMINAL_STATUSES:
        raise ValueError(f"unknown business status: {business_status!r}")
    stages = tuple(
        stage
        for stage in SCAN_STAGES
        if not (mode == "SENTINEL_EMPTY" and stage in SENTINEL_SKIPPED_STAGES)
    )
    roles = tuple(agent_roles) if agent_roles is not None else SCAN_AGENT_ROLES
    return RunProfile(
        kind="scan-market",
        expected_stages=stages,
        agent_roles=roles,
        artifact_rules=_BASE_RULES,
        replayable_stages=tuple(replayable_stages),
        mode=mode,
        business_status=business_status,
        last_stage=last_stage,
        conditional_roles=CONDITIONAL_AGENT_ROLES,
    )


__all__ = [
    "CONDITIONAL_AGENT_ROLES",
    "MODES",
    "REPLAYABLE_STAGES",
    "SCAN_AGENT_ROLES",
    "SCAN_STAGES",
    "SENTINEL_SKIPPED_ROLES",
    "SENTINEL_SKIPPED_STAGES",
    "ArtifactRule",
    "RunProfile",
    "scan_profile",
]
