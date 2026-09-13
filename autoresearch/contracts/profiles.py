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

import importlib
from collections.abc import Callable
from dataclasses import dataclass, field

from autoresearch.contracts import stages as vocab

#: run kind → 它的 profile 工厂,**记成字符串**而不是 import 进来。
#:
#: 这不是懒:`trace/completeness.py` 与 `trace/capsule.finalize` 都要按 `run_kind` 拿到
#: 对应的 profile,而工厂真身分别住在 `scan/`(层 6)和 `analyze/`(层 5)—— `trace` 在
#: 层 3,静态 import 任何一个都是一条**向上的边**。`tests/contracts/test_layering.py` 的
#: 守卫按 AST 查静态 import,所以「字符串 + `importlib` 现取」既不新增边,也不需要往
#: `KNOWN_UPWARD` 里加豁免(那个棘轮只许减不许增)。
#:
#: 代价是错拼的名字要到调用时才炸,所以 `profile_factory()` 把错误说全(kind、目标串、
#: 原始异常),而不是让调用方拿到一个 `AttributeError: None`。
PROFILE_FACTORIES: dict[str, str] = {
    "scan-market": "autoresearch.scan.run_profile:scan_profile",
    "stock-research": "autoresearch.analyze.run_profile:analyze_profile",
    "macro-research": "autoresearch.macro.run_profile:macro_profile",
}

if tuple(PROFILE_FACTORIES) != vocab.RUN_KINDS:
    raise RuntimeError(
        "PROFILE_FACTORIES 与 contracts.stages.RUN_KINDS 不一致:"
        f"{tuple(PROFILE_FACTORIES)} vs {vocab.RUN_KINDS}"
    )


def profile_factory(kind: str) -> Callable[..., RunProfile]:
    """按 run kind 取 profile 工厂(动态 import;见 `PROFILE_FACTORIES` 的注释)。"""
    target = PROFILE_FACTORIES.get(str(kind))
    if target is None:
        raise ValueError(
            f"unknown run kind: {kind!r}; expected one of {sorted(PROFILE_FACTORIES)}"
        )
    module_name, _, attribute = target.partition(":")
    try:
        module = importlib.import_module(module_name)
        return getattr(module, attribute)
    except (ImportError, AttributeError) as exc:
        raise RuntimeError(
            f"profile factory for {kind!r} is not importable ({target!r}): {exc}"
        ) from exc


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

    ``evidence_level`` (D6.5) is a **purely additive** classification of *how much*
    of the underlying evidence a hit actually proves: ``L0`` = existence only (the
    default — a file is there, full stop), ``L1`` = a harness-written summary view
    (e.g. ``agents/index.json``: per-invocation model/effort/usage, not the raw
    transcript), ``L2`` = original full-text evidence (a fetch tool's captured page
    body — no producer exists for this yet; P2 reserves the slot).  It never
    participates in disposition: `build_expected`/`evaluate`'s REQUIRED/PRESENT/
    MISSING logic reads only `required_when` and file presence, exactly as before.
    This field only feeds `completeness.evaluate`'s new `levels` summary.
    """

    key: str
    selector: str
    source: str
    required_when: str
    evidence_level: str = "L0"


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
    #: 哪些阶段**欠一份被捕获的命令输出**(`logs/<stage>/*.stdout.log.gz`)。
    #: `None` = 「每个到达的阶段都欠」,也就是 `scan-market` 今天的行为(它的每条命令
    #: 都过 `exec_capture` 的 traced 壳)。`stock-research` 传 `()`:它按设计不套那层壳
    #: (每条命令 3 次 agent spawn 的成本病,设计稿 §1.4/Q2),留痕走**进程内 checkpoint**。
    #: 没有捕获壳却把日志记成 REQUIRED,会让每一趟单票研究都恒判「证据缺失」——
    #: 那是假警报,不是发现。
    captured_stages: tuple[str, ...] | None = None
    #: 决策卡读取权威(D4,2026-09-07):`legacy_md` = 现状(默认,生产不变);`candidate_json`
    #: = 双产物只比较不消费;`research_json_v1` = JSON 权威(D5,解冻后)。冻进 run 契约 hash,
    #: 缺字段的历史 run 按 legacy_md 读,未知值失败。
    card_source: str = "legacy_md"
    #: Session orchestration may use logical roles that are not part of the legacy
    #: global role table.  ``None`` preserves the historical lookup exactly.
    role_stages: dict[str, str] | None = None

    def owes_captured_logs(self, stage: str) -> bool:
        """这个阶段该不该有被捕获的 stdout/stderr。"""
        if self.captured_stages is None:
            return True
        return stage in self.captured_stages

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
        stage = (self.role_stages or ROLE_STAGES).get(role)
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
    "PROFILE_FACTORIES",
    "ROLE_STAGES",
    "SENTINEL_SKIPPED_ROLES",
    "ArtifactRule",
    "RunProfile",
    "profile_factory",
]
