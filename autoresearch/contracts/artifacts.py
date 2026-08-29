#!/usr/bin/env python3
"""产物登记表 —— 「这条流水线会写出哪些文件」的**唯一真身**(声明,零 IO)。

design: `docs/specs/2026-08-29-full-coverage-research-system-brainstorm.md` §2.2 K1 / §2.4 A1。

## 病灶(2026-08-29 审计实测)

产物名今天是**跨三种语言的字面量**:`finalists.csv` 出现在 **40** 个生产文件里
(只有 `scan/artifacts.read_finalists` 做 zfill,其余 39 处各自 `read_csv`);
`L2_gbdt_top200.csv` 32 个、`L1_scored_full.csv` 19 个、`decision_records.json` 17 个、
`summary.md` 15 个、`_final_ratings.json` 14 个、`_l4_tasks.json` 13 个。
全仓只有 4 个名字有常量。`scan/artifacts.CRITICAL_ARTIFACTS` 把 21 个名字又写了一遍,
**而没有任何人 import 它的 path** —— 它只用来算 hash 与 coverage。

于是改一个产物名要改 40 个文件,JS 与 python 各拼各的路径,
`.claude/**` 的散文里还有第三份。

## 定位

本表**只声明**:名字、相对路径、属于哪个根、哪个阶段产的、谁产的、什么形态、
在不在场是有条件的、能不能重放。它不读盘、不写盘、不 glob。

上层从这里派生(A1 的落点,分批做):`scan/run_profile._BASE_RULES`、
`trace/completeness.build_expected`、`scan/health._ARTIFACTS`、`scan/brief` 白名单、
`scan/publisher` 的两张 trace 映射表、`trace/replay.default_stage_specs`、
以及 `.claude/workflows/_contracts.generated.js`。

## 根(`root`)的含义

- ``staging`` —— 本 run 的 staging(`ws.scan_dir(date)`;`AUTORESEARCH_RUN_ID` 在场时
  是 `scan_runs/<run_id>/staging/<date>/`,否则是历史根 `context_<engine>/scan/<date>/`)
- ``report``  —— 发布目录 `reports_<engine>/scan/<run_id>/`
- ``ledger``  —— 跨 run 账本 `reports_<engine>/scan/_ledger/`(**不在 run 目录内**:
  run 目录发布后不再变是 MANIFEST/ROOT 的不变量)
- ``capsule`` —— 法证现场 `reports_<engine>/scan/<run_id>/capsule/`

`presence`:``always`` = 该阶段跑到就必须有;``gated`` = 有前置条件才有(缺席是事实
不是洞);``conditional`` = 只有被触发才有(复核、修补)。
"""
from __future__ import annotations

from dataclasses import dataclass

ARTIFACT_REGISTRY_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class Artifact:
    """一个被登记的产物。

    `path` 相对于 `root`,允许 glob(`details/*.md`)—— glob 是产物**族**,
    它的「在场」判据是至少一个非空文件。
    """

    name: str
    path: str
    root: str
    stage: str
    producer: str
    kind: str          # csv | json | md | txt | dir
    presence: str      # always | gated | conditional
    required_when: str | None = None
    replayable: bool = False


ROOTS: tuple[str, ...] = ("staging", "report", "ledger", "capsule")
KINDS: tuple[str, ...] = ("csv", "json", "md", "txt", "dir")
PRESENCES: tuple[str, ...] = ("always", "gated", "conditional")


ARTIFACTS: tuple[Artifact, ...] = (
    # ---- frame(L0 帧 + 三个 pack)---------------------------------------
    Artifact("run_contract", "run_contract.json", "staging", "frame", "frame", "json", "always", replayable=True),
    Artifact("market_pack", "market_pack.json", "staging", "frame", "frame", "json", "always", replayable=True),
    Artifact("strategist_pack", "strategist_pack.json", "staging", "frame", "strategist_pack", "json", "always"),
    Artifact("user_config_echo", "user_config_echo.json", "staging", "frame", "frame", "json", "always"),
    # ---- prelude ---------------------------------------------------------
    Artifact("prelude_summary", "_prelude_summary.md", "staging", "prelude", "prelude", "md", "always"),
    Artifact("l4_rejection_readout", "_l4_rejection_readout.json", "staging", "prelude", "prelude", "json", "gated",
             required_when="有已发布 run 可读(滚动 40 日)"),
    Artifact("prewarm", "_prewarm.json", "staging", "prelude", "prewarm", "json", "gated",
             required_when="夜间预热跑过"),
    Artifact("market_view", "market_view.md", "staging", "prelude", "strategist", "md", "always"),
    Artifact("prewarm_failed", "_prewarm_failed.json", "staging", "prelude", "prewarm", "json",
             "conditional", required_when="预热在解析交易日前就失败(拿不到日期,落无日期记录)"),
    Artifact("overseas_calendar", "overseas_calendar.csv", "staging", "prelude", "overseas", "csv", "gated",
             required_when="外源日历源可达"),
    # ---- L0/L1/L2(universe 一条命令产全部)------------------------------
    Artifact("l0_meta", "L0_universe_meta.json", "staging", "prelude", "universe", "json", "always", replayable=True),
    Artifact("l1_full", "L1_scored_full.csv", "staging", "prelude", "universe", "csv", "always", replayable=True),
    Artifact("l1_recall", "L1_recall_top1000.csv", "staging", "prelude", "universe", "csv", "always", replayable=True),
    Artifact("l1_channels", "L1_channels.csv", "staging", "prelude", "universe", "csv", "always", replayable=True),
    Artifact("l2", "L2_gbdt_top200.csv", "staging", "prelude", "universe", "csv", "always", replayable=True),
    Artifact("sectors", "sectors.csv", "staging", "prelude", "universe", "csv", "always", replayable=True),
    Artifact("funnel_meta", "meta.json", "staging", "prelude", "universe", "json", "always", replayable=True),
    Artifact("weights_used", "weights_used.json", "staging", "prelude", "universe", "json", "always"),
    Artifact("degraded", "degraded.json", "staging", "prelude", "universe", "json", "always"),
    Artifact("calendar", "calendar.csv", "staging", "prelude", "calendar", "csv", "always"),
    # ---- 控制面 ----------------------------------------------------------
    Artifact("stage_results", "stage_results/*.json", "staging", "prelude", "control_plane", "json", "always"),
    Artifact("run_mode", "run_mode.json", "staging", "gate1", "run_mode", "json", "always"),
    # ---- 行业旁路 --------------------------------------------------------
    Artifact("sector_briefs", "sector_briefs/*.md", "staging", "sector", "sector-brief", "md", "gated",
             required_when="当日选出了行业"),
    # ---- L3 --------------------------------------------------------------
    Artifact("l3_catalyst", "L3_catalyst.csv", "staging", "l3", "l3_catalyst", "csv", "gated",
             required_when="当日有催化事件"),
    Artifact("l3_table", "_l3_table.md", "staging", "l3", "l3_prompt", "md", "always"),
    Artifact("l3_pass1_kept", "_l3_pass1_kept.csv", "staging", "l3", "l3_triage", "csv", "always"),
    Artifact("l3_pass1_cut", "_l3_pass1_cut.csv", "staging", "l3", "l3_triage", "csv", "always"),
    Artifact("l3_pass1_meta", "_l3_pass1_meta.json", "staging", "l3", "l3_triage", "json", "always"),
    Artifact("l3_judged_raw", "_l3_judged.json", "staging", "l3", "l3-rank", "json", "always"),
    Artifact("l3_judged", "L3_judged_full.csv", "staging", "l3", "l3_merge", "csv", "always"),
    Artifact("l3_bench", "_l3_bench.csv", "staging", "l3", "l3_merge", "csv", "always"),
    Artifact("finalists", "finalists.csv", "staging", "l3", "l3_merge", "csv", "always"),
    Artifact("l3_repair_pack", "_l3_repair_pack.json", "staging", "l3", "l3_validation", "json", "conditional",
             required_when="lint 有失败行"),
    Artifact("l3_repair_prompt", "_l3_repair_prompt.md", "staging", "l3", "l3_validation", "md", "conditional",
             required_when="lint 有失败行"),
    Artifact("l3_repair_patch", "_l3_repair_patch.json", "staging", "l3", "l3-repair", "json", "conditional",
             required_when="派了 repair agent"),
    # ---- L4 派发前生产者 --------------------------------------------------
    Artifact("l4_shared_instructions", "_l4_shared_instructions.md", "staging", "l4_prep", "l4_prompts", "md", "always"),
    Artifact("l4_prompts", "_l4_prompt_*.md", "staging", "l4_prep", "l4_prompts", "md", "always"),
    Artifact("harvest_list", "_harvest_list.txt", "staging", "l4_prep", "l4_prompts", "txt", "always"),
    Artifact("dossier_present", "_dossier_present.json", "staging", "l4_prep", "l4_prompts", "json", "always"),
    Artifact("pledge", "pledge.csv", "staging", "l4_prep", "l4_producers", "csv", "gated",
             required_when="有 finalist 且质押源可达"),
    Artifact("seats", "seats.csv", "staging", "l4_prep", "l4_producers", "csv", "gated",
             required_when="有龙虎榜席位数据"),
    Artifact("consensus", "consensus.csv", "staging", "l4_prep", "l4_producers", "csv", "gated",
             required_when="consensus 缓存 ≥10 日"),
    Artifact("fund_hold", "fund_hold.csv", "staging", "l4_prep", "l4_producers", "csv", "gated",
             required_when="基金重仓源可达"),
    # ---- L4 ---------------------------------------------------------------
    Artifact("l4_task_book", "_l4_tasks.json", "staging", "l4", "l4_tasks", "json", "always"),
    Artifact("l4_intel", "_l4_intel_*.md", "staging", "l4", "l4-intel", "md", "gated",
             required_when="l4_intel.enabled"),
    Artifact("l4_intel_status", "_l4_intel_status_*.json", "staging", "l4", "intel_status", "json", "gated",
             required_when="l4_intel.enabled"),
    Artifact("l4_cards", "details/*.md", "staging", "l4", "l4-card", "md", "always"),
    Artifact("ensemble", "_ensemble_*.json", "staging", "l4", "l4-ensemble", "json", "conditional",
             required_when="有 ≥OW 卡或 📌 SELL 提案"),
    # ---- L5 ---------------------------------------------------------------
    Artifact("early_stop", "_early_stop.json", "staging", "l5", "assemble", "json", "always"),
    Artifact("final_ratings", "_final_ratings.json", "staging", "l5", "assemble", "json", "always"),
    Artifact("decision_records", "decision_records.json", "staging", "l5", "decision_finalize", "json", "always"),
    Artifact("dissent_records", "dissent_records.json", "staging", "l5", "decision_finalize", "json", "gated",
             required_when="有复核分歧"),
    Artifact("candidate_passport", "_candidate_passport.json", "staging", "l5", "passport", "json", "always"),
    Artifact("brief_sources", "_brief_sources.json", "staging", "l5", "brief", "json", "always"),
    Artifact("report_budget", "_report_budget.json", "staging", "l5", "health", "json", "always"),
    Artifact("gate_fires", "gate_fires.csv", "staging", "l5", "self_review", "csv", "always"),
    # ---- 发布目录 ----------------------------------------------------------
    Artifact("brief", "brief.md", "report", "l5", "brief", "md", "always", replayable=False),
    Artifact("summary", "summary.md", "report", "l5", "assemble", "md", "always", replayable=True),
    Artifact("appendix", "appendix.md", "report", "l5", "assemble", "md", "gated",
             required_when="run_contract 记了 appendix 的 schema 版本", replayable=True),
    Artifact("manifest", "manifest.json", "report", "l5", "publisher", "json", "always"),
    Artifact("funnel", "funnel.md", "report", "l5", "publisher", "md", "always"),
    Artifact("index", "index.md", "report", "l5", "publisher", "md", "always"),
    Artifact("run_health", "run_health.json", "report", "l5", "health", "json", "always"),
    Artifact("artifact_index", "artifact_index.json", "report", "l5", "publisher", "json", "always"),
    Artifact("published_cards", "details/*.md", "report", "l5", "publisher", "md", "always"),
    Artifact("token_usage", "token_usage.md", "report", "observe", "usage_harvest", "md", "gated",
             required_when="CP7 跑过 usage_harvest"),
    # ---- observe ------------------------------------------------------------
    Artifact("token_usage_json", "_token_usage.json", "staging", "observe", "usage_harvest", "json", "gated",
             required_when="CP7 跑过 usage_harvest"),
    Artifact("usage_reconcile", "_usage_reconcile.json", "staging", "observe", "usage_reconcile", "json", "gated",
             required_when="CP7 跑过 usage_reconcile"),
    Artifact("budget_observation", "_budget_observation.json", "staging", "observe", "post_run", "json", "always"),
    Artifact("stage_timing", "_stage_timing.json", "staging", "observe", "post_run", "json", "gated",
             required_when="post_run observe 跑过"),
    Artifact("relative_buy_decision", "_relative_buy_decision.json", "staging", "observe", "relative_buy", "json", "always"),
    Artifact("outbox_events", "outbox/events.json", "staging", "observe", "post_run", "json", "always"),
    Artifact("consumer_state", "outbox/consumer_state.json", "staging", "observe", "post_run", "json", "always"),
    # ---- 账本(跨 run,run 目录之外)------------------------------------------
    Artifact("recommendations", "recommendations.csv", "ledger", "observe", "outcome", "csv", "gated",
             required_when="有已发布 run 且 D+2 成熟"),
    Artifact("ledger_runs", "views/runs.csv", "ledger", "observe", "ledger_views", "csv", "gated",
             required_when="outcome/ledger_views 跑过"),
    Artifact("ledger_sessions", "views/session_calendar.csv", "ledger", "observe", "ledger_views", "csv", "gated",
             required_when="outcome/ledger_views 跑过"),
    Artifact("ledger_market", "views/market.csv", "ledger", "observe", "ledger_views", "csv", "gated",
             required_when="outcome/ledger_views 跑过"),
    Artifact("ledger_stage_rulers", "views/stage_rulers.csv", "ledger", "observe", "populations", "csv", "gated",
             required_when="populations rulers 跑过"),
    # ---- capsule(法证现场;由 trace 层产,登记在此以便派生 expected 清单)-------
    Artifact("capsule_json", "capsule.json", "capsule", "finalize", "capsule", "json", "always"),
    Artifact("capsule_root", "ROOT.json", "capsule", "finalize", "capsule", "json", "always"),
    Artifact("capsule_expected", "verification/expected.json", "capsule", "finalize", "completeness", "json", "always"),
    Artifact("capsule_completeness", "verification/completeness.json", "capsule", "finalize", "completeness", "json", "always"),
    Artifact("capsule_replay", "verification/replay.json", "capsule", "finalize", "replay", "json", "always"),
    Artifact("capsule_web_budget", "usage/web_budget.json", "capsule", "finalize", "web_budget", "json", "gated",
             required_when="有 external_tools 留痕"),
    Artifact("capsule_evidence_index", "lineage/external_evidence_index.json", "capsule", "finalize",
             "evidence_index", "json", "gated", required_when="有外源证据"),
)

_BY_NAME: dict[str, Artifact] = {a.name: a for a in ARTIFACTS}


def by_name(name: str) -> Artifact:
    """按登记名取产物;未登记 → `KeyError`(这正是 drift 守卫想要的响声)。"""
    return _BY_NAME[name]


def for_stage(stage: str) -> tuple[Artifact, ...]:
    """某阶段产出的全部产物(按登记序)。"""
    return tuple(a for a in ARTIFACTS if a.stage == stage)


def for_root(root: str) -> tuple[Artifact, ...]:
    """某个根下的全部产物。"""
    return tuple(a for a in ARTIFACTS if a.root == root)


def replayable() -> tuple[Artifact, ...]:
    """能按冻结输入重放出同样字节的产物。"""
    return tuple(a for a in ARTIFACTS if a.replayable)


def paths() -> frozenset[str]:
    """全部已登记的相对路径(drift 守卫用)。"""
    return frozenset(a.path for a in ARTIFACTS)


#: **不是产物**的文件名字面量白名单(drift 守卫用)。
#:
#: 这里的每一项都是审计时逐条确认过「它不是流水线产物」的:capsule 内部结构、
#: 文档路径、agent def、glob 片段、第三方/历史文件。**只许减不许增** ——
#: 新增一个名字之前先问「它是不是一个产物」,是就进 `ARTIFACTS`。
NON_ARTIFACT_LITERALS: frozenset[str] = frozenset({
    # glob / 路径片段
    "*/_budget_observation.json", "*/attempt-*/result.json",
    "gate*.json", "_ensemble*.json", "_ensemble.json", ".meta.json",
    "reasoning/l4/_l4_tasks.json",
    # 文档与 agent def(散文,不是产物)
    ".claude/agents/dossier-init.md", ".claude/agents/l3-rank.md",
    ".claude/agents/l4-card.md", ".claude/agents/l4-intel.md",
    ".claude/agents/macro-brief.md", ".claude/agents/sector-brief.md",
    ".claude/skills/scan-market/SKILL.md", ".claude/skills/scan-market/STAGES.md",
    ".claude/skills/stock-research/lite-playbook.md",
    "AGENTS.md", "CLAUDE.md", "docs/PANORAMA.md",
    # capsule 内部结构(trace 层自有,不经阶段产出)
    "agents/index.json", "assemble.json", "bootstrap_failure.json",
    "failure.json", "identity/environment.json",
    "identity/identity_event_failure.json", "identity/run_contract.json",
    "identity/source_manifest.json", "index.json", "_index.json", "inputs.json",
    "invocations.json", "lineage/coverage.json", "outputs.json", "result.json",
    "snapshot_cleanup_warning.json", "snapshot_inventory.json", "snapshot_result.json",
    "snapshot_transaction.json", "source_links.json", "source_manifest.json",
    "state.json", "submodules.json", "verification/ROOT.json",
    "dependencies.txt", "identity/dependencies.txt",
    "verification/profile.json", "usage/_token_usage.json", "usage/token_usage.md",
    "lake_manifest.json", "_t0.json", "environment.json",
    # 引擎根下的非 run 产物 / 历史遗留
    "learning/l2_knife_audit.md", "learning/structural_audit.md",
    "learning/temperature.csv", "temperature.csv", "research/temperature_calib.md",
    "_macro_cn.json", "weights.json", "L1_weights.json", "_claim_ledger.csv",
    "_dossier_snapshot.json", "_slim.md", "_slim_deep.md",
    "_price_claim_status.json", "price_claim_subjects.json",
    "_relative_buy_decision.mismatch.json", "_resolved_agent_config.json",
    "_tripwire_conflicts.json", "l4_watch_cursor.json", "_health.json",
    "temperature_row.json",
    # 已退役但仍被读的(消费者无生产者;见 spec §1.3)
    "verify.csv", "L3_fine_finalists.csv",
    # 报告目录内的旧名 / 兼容
    "gate4.json",
})
