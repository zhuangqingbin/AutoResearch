#!/usr/bin/env python3
"""Run-evidence profile shapes shared by every research skill (declaration, zero IO).

design: `docs/specs/2026-08-29-full-coverage-research-system-brainstorm.md` §2.4 A1;
task: `.superpowers/sdd/2026-08-31-stock-research-p0-p1/task-1-brief.md`(D6.1).

## 为什么搬到这里

`RunProfile` / `ArtifactRule` 最早只在 `scan/run_profile.py` 声明,只服务
`scan-market`。`stock-research`(`autoresearch/analyze/`)需要同一副形状来描述它
自己的证据义务(`analyze_profile()`),但分层测试(`tests/contracts/test_layering.py`)
把 `analyze` 排在 `scan` **之下**——`analyze` import `scan` 会造一条新的向上边,
当场被 `test_no_new_upward_edges` 判红。

这两个 dataclass 本身不含任何 `scan-market` 专属逻辑(阶段/角色/规则全部由调用方
传入),所以它们属于契约层,而不是 `scan` 的私产。`scan/run_profile.py` 顶部
re-export 旧名,全仓既有 `from autoresearch.scan.run_profile import RunProfile`
一律不断。

内容与搬家前(`scan/run_profile.py`)逐字相同——搬家本身不是一次重构。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from autoresearch.contracts import stages as vocab

# `RunProfile.role_expected` 要用的两张表。两者都是 `contracts.stages` 的纯派生
# (不含 scan 专属信息),搬到这里后 `scan/run_profile.py` 仍各自保有同一份
# (同一个 `vocab.ROLE_STAGES` 对象;`is` 恒成立)。
ROLE_STAGES: dict[str, str] = vocab.ROLE_STAGES
SENTINEL_SKIPPED_ROLES: frozenset[str] = vocab.roles_in_stages(vocab.L4_STAGES)


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
        if vocab.skips_l4(self.mode) and role in SENTINEL_SKIPPED_ROLES:
            return False
        stage = ROLE_STAGES.get(role)
        if stage is not None and not self.stage_reached(stage):
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


__all__ = [
    "ROLE_STAGES",
    "SENTINEL_SKIPPED_ROLES",
    "ArtifactRule",
    "RunProfile",
]
