"""What evidence one scan run *owes*, declared before anything is checked.

Completeness is only meaningful against an explicit expectation.  This module
holds that expectation for `scan-market`: which stages run, which agent roles
exist, which artifacts each mode owes, and which stages are deterministic
enough to replay.  A sentinel run legitimately has no L4 leg — that is
`NOT_EXPECTED`, not a missing file — and a run that failed at L3 never reached
L4 — that is `NOT_REACHED`, also not a missing file.

**Everything about "which names exist" now derives from
:mod:`autoresearch.contracts.stages`** (spec 2026-08-29 §2.2 K3 / §2.4 A1).  This module
used to redeclare the stage, role and mode vocabularies, which is exactly how the
denominator of "is this run complete" ended up with four different versions.  What stays
here is the *policy*: which of those names a given mode owes evidence for.
"""

from __future__ import annotations

from autoresearch.contracts.profiles import CURRENT_CARD_RULES

from autoresearch.contracts import stages as vocab

# `RunProfile` / `ArtifactRule` / `_BASE_RULES` used to be declared right here.  They
# moved to `contracts/profiles.py` (2026-08-31, D6.1) so `stock-research`
# (`autoresearch/analyze/`) can build its own `RunProfile` without importing `scan`
# (which the layering ratchet in `tests/contracts/test_layering.py` forbids — `analyze`
# sits *below* `scan`).  Re-exported here so every existing
# `from autoresearch.scan.run_profile import RunProfile` keeps working unchanged.
from autoresearch.contracts.profiles import _BASE_RULES, ArtifactRule, RunProfile  # noqa: F401

# Ordered pipeline stages.  Order is load-bearing: everything after the last
# reached stage of a failed run is NOT_REACHED, not missing.
SCAN_STAGES: tuple[str, ...] = vocab.PIPELINE_STAGES

# Business agent roles.  Deterministic relays (gp-shell / trace-control) are
# deliberately absent: their evidence is the captured command, not a transcript.
# Derived, not re-listed: a role is "unconditional" exactly when it is in the
# vocabulary's role table and not one of the conditional legs.
SCAN_AGENT_ROLES: tuple[str, ...] = tuple(
    role for role in vocab.ROLE_STAGES if role not in vocab.CONDITIONAL_ROLES
)

# Legs that only exist when something triggered them; their absence is a fact
# about the run, not a hole in the evidence.
CONDITIONAL_AGENT_ROLES: frozenset[str] = vocab.CONDITIONAL_ROLES

# Which stage each role belongs to.  A role whose stage was never reached is
# NOT_EXPECTED — a run that died at L3 does not owe L4 transcripts, and calling
# them "missing" would make every failed run look like an evidence failure too.
ROLE_STAGES: dict[str, str] = vocab.ROLE_STAGES

# A sentinel run *with no pinned holdings* never dispatches per-stock work.
# These two sets fire for `SENTINEL_EMPTY` **only** — see the note on MODES.
# The role set is derived from the stage set through the shared role table, so a
# new L4 role cannot be invented without the sentinel path learning about it.
SENTINEL_SKIPPED_STAGES: frozenset[str] = vocab.L4_STAGES
SENTINEL_SKIPPED_ROLES: frozenset[str] = vocab.roles_in_stages(vocab.L4_STAGES)

# One mode vocabulary, shared with `scan.run_mode.MODES` (spec 2026-08-29 §2.2 K3:
# this list used to hold three of the four, so `scan_profile(mode="SENTINEL_PINNED")`
# raised instead of describing a real run).
#
# `SENTINEL_PINNED` is a sentinel that **does** run L4: the market has nothing worth
# buying, but pinned holdings still owe a same-day decision card (`STAGES.md`
# 「哨兵 vs 持仓」—— 哨兵只问「今天有没有值得买的」,不问「持仓要不要动」).  The trigger for
# the skip sets is `vocab.skips_l4()`, never a `startswith("SENTINEL")` prefix match —
# a prefix match would swallow the pinned sentinel and silently deny holdings their card.
MODES: tuple[str, ...] = vocab.MODES
TERMINAL_STATUSES: tuple[str, ...] = (
    "ACTIVE",
    "SUCCEEDED",
    "FAILED",
    "INTERRUPTED",
)

# Replay *units*, not pipeline stages — `l0`/`l1`/`l2` are funnel-level names that map
# onto the `frame` / `prelude` stages (`vocab.REPLAY_UNIT_STAGES` is the bridge, and
# `vocab.REPLAYABLE_STAGES` is the same information expressed in stage names).
REPLAYABLE_STAGES: tuple[str, ...] = vocab.REPLAY_UNITS


# `ArtifactRule`, `RunProfile` (and their `stage_reached` / `role_expected` methods)
# and `_BASE_RULES` used to be declared right here — see `contracts/profiles.py` for
# the (unchanged) definitions and the re-export import above.

#: Capsule-relative selector segments that name a stage / a role rather than a file.
#: `_BASE_RULES` themselves are evidence *selectors* (out of scope for the contracts
#: derivation), but the moment one of them names a stage or a role, that name has to come
#: from the shared vocabulary — otherwise a fifth registry starts growing right here.
_STAGE_SCOPED_PREFIXES = ("stages", "logs")
_ROLE_SCOPED_PREFIXES = ("agents",)


def check_rule_vocabulary(rules: tuple[ArtifactRule, ...]) -> None:
    """Raise when a rule selector names a stage or a role the contracts layer never heard of.

    Only the segment right after a scoped prefix is checked, and only when it looks like a
    name: `*` is a wildcard and a segment with a suffix (`agents/index.json`) is a file, not
    a role.  Today no `_BASE_RULES` row is stage- or role-scoped, so this guard is a tripwire
    for the next one that is — `tests/contracts/test_stage_vocabulary.py` proves it can fire.
    """
    for rule in rules:
        parts = rule.selector.split("/")
        if len(parts) < 2:
            continue
        head, name = parts[0], parts[1]
        if name == "*" or "." in name:
            continue
        if head in _STAGE_SCOPED_PREFIXES and name not in SCAN_STAGES:
            raise ValueError(
                f"rule {rule.key!r} names stage {name!r}, which is not in the contracts "
                f"vocabulary {SCAN_STAGES!r}"
            )
        if head in _ROLE_SCOPED_PREFIXES and name not in ROLE_STAGES:
            raise ValueError(
                f"rule {rule.key!r} names role {name!r}, which is not in the contracts "
                f"role table {sorted(ROLE_STAGES)!r}"
            )


check_rule_vocabulary(_BASE_RULES)


def scan_profile(
    *,
    mode: str = "FULL",
    business_status: str = "SUCCEEDED",
    last_stage: str | None = None,
    agent_roles: tuple[str, ...] | None = None,
    replayable_stages: tuple[str, ...] = REPLAYABLE_STAGES,
    card_source: str = "legacy_md",
    card_rules_version: str = CURRENT_CARD_RULES,
    role_stages: dict[str, str] | None = None,
) -> RunProfile:
    """Build the `scan-market` evidence profile for one run's mode and terminal state."""
    from autoresearch.contracts.agent_output import CARD_SOURCES

    if mode not in MODES:
        raise ValueError(f"unknown run mode: {mode!r}; expected one of {MODES!r}")
    if card_source not in CARD_SOURCES:
        raise ValueError(f"unknown card_source: {card_source!r}; expected one of {CARD_SOURCES!r}")
    if business_status not in TERMINAL_STATUSES:
        raise ValueError(f"unknown business status: {business_status!r}")
    skip_l4 = vocab.skips_l4(mode)
    stages = tuple(
        stage
        for stage in SCAN_STAGES
        if not (skip_l4 and stage in SENTINEL_SKIPPED_STAGES)
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
        card_source=card_source,
        card_rules_version=card_rules_version,
        role_stages=role_stages,
    )


__all__ = [
    "CONDITIONAL_AGENT_ROLES",
    "MODES",
    "REPLAYABLE_STAGES",
    "ROLE_STAGES",
    "SCAN_AGENT_ROLES",
    "SCAN_STAGES",
    "SENTINEL_SKIPPED_ROLES",
    "SENTINEL_SKIPPED_STAGES",
    "ArtifactRule",
    "RunProfile",
    "check_rule_vocabulary",
    "scan_profile",
]
