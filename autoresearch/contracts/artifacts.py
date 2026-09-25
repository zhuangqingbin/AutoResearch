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
- ``analyze_ctx`` —— stock-research 的取数落盘根,就是 `ws.context_root()` 本身
  (`context_<engine>/`)。full 报告的整份 context 与 `harvest --slim` 产的决策卡半成品
  **都直接落在这里**,不进分段目录——这正是 D6.1 的更正:草稿里写的 `../<ticker>_<date>.md`
  假设了它相对 `analyze_staging`,但真实写法是相对 `analyze_ctx` 自身,无需 `..`。
- ``analyze_staging`` —— 单票分析的分段草稿目录 `$CTX/analyze/<TICKER>_<YYYYMMDD>/`
  (`1_analysts/`、`2_research/`、`3_risk/`、`4_portfolio/` 四个子目录 + 两份情报 md)
- ``analyze_report`` —— stock-research 发布目录 `$RPT/analyze/<YYYYMMDD_HHMM>/`
  (`autoresearch.analyze.assemble` 产出的最终报告 + `manifest.json`;目录名 = 组装时刻,
  与 scan 的 `report` 根同一约定)
- ``analyze_ledger`` —— stock-research 跨 run 账本(镜像 scan 的 `ledger` 根形状;
  D6.1 落笔时尚无生产者,presence=gated 记的是「这一步还没接线」而不是「这一趟没触发」)
- ``metering`` —— 跨 run 的本机计量读数 `$RPT/_metering/`(**不在任何 run 目录内**;
  JSON 是机器真相,Markdown 只由 JSON 渲染)
- ``acceptance`` —— 本引擎可导入/导出的 portable 验收 proof
  `$RPT/_acceptance/proofs/`；不读取另一引擎原始 context/reports。

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


ROOTS: tuple[str, ...] = (
    "staging", "report", "ledger", "capsule",
    "analyze_ctx", "analyze_staging", "analyze_report", "analyze_ledger",
    "metering",
    "acceptance",
    # 2026-09-07(Q-R 裁定①):研究仪器与券商取数层的产物根,此前从未登记、守卫也不扫。
    # research_report = $RPT/research/(读数、离线实验目录);research_ctx = $CTX/research/
    # (研究账本);factor_lab = ws.factor_lab_root();broker = ws.broker_root()。
    "research_report", "research_ctx", "factor_lab", "broker",
)
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
    Artifact("sector_seats", "_sector_seats.json", "staging", "prelude", "universe", "json", "gated",
             required_when="l2.sector_seats.enabled"),
    Artifact("sectors", "sectors.csv", "staging", "prelude", "universe", "csv", "always", replayable=True),
    Artifact("funnel_meta", "meta.json", "staging", "prelude", "universe", "json", "always", replayable=True),
    Artifact("weights_used", "weights_used.json", "staging", "prelude", "universe", "json", "always"),
    Artifact("degraded", "degraded.json", "staging", "prelude", "universe", "json", "always"),
    Artifact("calendar", "calendar.csv", "staging", "prelude", "calendar", "csv", "always"),
    Artifact("index_events", "index_events.csv", "staging", "prelude", "calendar", "csv", "gated",
             required_when="calendar.index_rebalance 开且中证公告源可达(design 2026-09-25 §2.2)"),
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
    # D3 候选双产物(2026-09-07 Q-D ②):对拍期由解析桥从 md 派生,md 仍是权威;比较结果不供决策消费。
    Artifact("l4_research_card", "details/*.research.json", "staging", "l4", "card_io", "json", "conditional"),
    Artifact("l4_card_compare", "card_compare.json", "staging", "l4", "card_io", "json", "conditional"),
    # B4 影子(2026-09-07 Q-B ③):本票回购/增持/减持/中标行的 ClaimEvidence v2 抽取 + 绑定结论;
    # 稿里一行事件都没有时不写。没有任何门读它。
    Artifact("l4_claim_events", "_l4_claims_*.json", "staging", "l4", "intel_guard", "json", "conditional"),
    Artifact("l4_cards", "details/*.md", "staging", "l4", "l4-card", "md", "always"),
    Artifact("ensemble", "_ensemble_*.json", "staging", "l4", "l4-ensemble", "json", "conditional",
             required_when="有 ≥OW 卡或 📌 SELL 提案"),
    # ---- L5 ---------------------------------------------------------------
    Artifact("early_stop", "_early_stop.json", "staging", "l5", "assemble", "json", "always"),
    Artifact("final_ratings", "_final_ratings.json", "staging", "l5", "assemble", "json", "always"),
    Artifact("blind_cards", "_blind_cards.json", "staging", "l5", "assemble", "json", "gated",
             required_when="任务簿有 status≠SUCCEEDED 或 slim≠PRESENT 的票"),
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
    Artifact("buyability", "_buyability.json", "staging", "observe", "buyability", "json", "always"),
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
    Artifact("execution_origin", "identity/execution_origin.json", "capsule", "finalize",
             "session_agent.origin", "json", "gated", required_when="session_v1 或显式 legacy"),
    Artifact("session_plan", "identity/session/plan.json", "capsule", "finalize",
             "session_agent.plan", "json", "gated", required_when="session_v1"),
    Artifact("session_request", "identity/session/request.json", "capsule", "finalize",
             "session_agent.request", "json", "gated", required_when="session_v1"),
    Artifact("session_host_profile", "identity/session/host_profile.json", "capsule", "finalize",
             "session_agent.origin", "json", "gated", required_when="session_v1"),
    Artifact("host_evidence_registration", "identity/session/host_evidence.json", "capsule", "finalize",
             "session_agent.host_evidence", "json", "gated", required_when="session_v1"),
    Artifact("evidence_plan", "evidence/evidence_plan.json", "capsule", "finalize",
             "session_agent.evidence", "json", "gated", required_when="session_v1"),
    Artifact("task_evidence", "evidence/tasks/*/a*/evidence.json", "capsule", "finalize",
             "session_agent.evidence", "json", "gated", required_when="session_v1"),
    Artifact("operation_request", "evidence/attempt_records/*/a*/operation_request.json",
             "capsule", "finalize", "session_agent.evidence", "json", "gated",
             required_when="session_v1 deterministic task was launched"),
    Artifact("main_host_evidence", "evidence/main_host.json", "capsule", "finalize",
             "session_agent.host_evidence", "json", "gated", required_when="session_v1"),
    Artifact("evidence_closure", "verification/evidence_closure.json", "capsule", "finalize",
             "session_agent.evidence", "json", "gated", required_when="session_v1"),
    Artifact("source_receipts", "lineage/source_receipts.jsonl", "capsule", "finalize",
             "trace.source_receipts", "json", "gated",
             required_when="有供应商或外部工具响应"),
    # session_v1 commit 后给兼容交付路径写的身份 sidecar。目录型交付使用
    # delivery.json，文件型交付使用 <name>.delivery.json；均只引用 canonical
    # publication，不把可变兼容路径本身当作封存真身。
    Artifact("publication_delivery_identity_dir", "delivery.json", "report", "finalize",
             "session_agent.publication", "json", "conditional",
             required_when="session_v1 发布器交付目录型兼容视图"),
    Artifact("publication_delivery_identity_file", "*.delivery.json", "report", "finalize",
             "session_agent.publication", "json", "conditional",
             required_when="session_v1 发布器交付文件型兼容视图"),
    Artifact("session_acceptance_proof", "*/*/*/*.json", "acceptance", "finalize",
             "session_agent.evaluation", "json", "conditional",
             required_when="某宿主真实场景生成或显式导入 portable proof"),
    Artifact("calculation_evidence", "evidence/calculations/*.json", "capsule", "finalize",
             "research.calculations", "json", "conditional",
             required_when="推理任务调用了登记补算"),
    Artifact("material_claim_evidence", "evidence/material_claims/*.json", "capsule", "finalize",
             "news.material_claims", "json", "conditional",
             required_when="报告重大断言声明了来源/引用/补算连接"),
    Artifact("capsule_web_budget", "usage/web_budget.json", "capsule", "finalize", "web_budget", "json", "gated",
             required_when="有 external_tools 留痕"),
    Artifact("capsule_evidence_index", "lineage/external_evidence_index.json", "capsule", "finalize",
             "evidence_index", "json", "gated", required_when="有外源证据"),
    # ── stock-research(D6.1;root=analyze_ctx 指 `ws.context_root()` 本身,
    #    analyze_staging 指 $CTX/analyze/<T>_<D>/,analyze_report 指 $RPT/analyze/<YYYYMMDD_HHMM>/)──
    # analyze_full_context 与 analyze_slim/analyze_slim_deep 的更正(见 ROOTS 含义表):
    # 草稿写的 `../<ticker>_<date>.md` 假设它们相对 analyze_staging,但真实落点是
    # analyze_ctx 根自身,`..` 不合法,故单独给它们一个根。
    Artifact("analyze_full_context", "*_????-??-??.md", "analyze_ctx", "harvest",
             "analyze.harvest", "md", "gated", required_when="full"),
    # analyze_slim / analyze_slim_deep 的 path 刻意写成不带通配符的裸后缀
    # `_slim.md` / `_slim_deep.md`,而不是「与既有行风格一致」建议的 `*_slim.md`:
    # `scan/retention.py:182` 早就把这两个后缀原样写成 `for suffix in ("_slim.md",
    # "_slim_deep.md")`——drift 守卫是纯字符串集合比对,不做真 glob 匹配,`*_slim.md`
    # 不会等于这条已有字面量。要把它们从白名单挪成正式登记,path 必须逐字等于
    # retention.py 里已经在用的那个后缀常量。
    Artifact("analyze_slim", "_slim.md", "analyze_ctx", "harvest",
             "analyze.harvest", "md", "gated", required_when="lite_or_scan"),
    Artifact("analyze_slim_deep", "_slim_deep.md", "analyze_ctx", "harvest",
             "analyze.harvest", "md", "gated", required_when="lite_or_scan"),
    # analyze_indicators(task-11/D1.5,Q7 瘦身):full 档的 12 个 `## <ind> values`
    # 30 天序列块搬进这份 deep 附件(主文件只留「末值+5日前值+方向」汇总表),按需
    # `Read` 才拉。同 analyze_slim 的裸后缀理由:path 逐字等于生产侧真实写盘用的后缀
    # 常量,不写成 `*_indicators.md`(drift 守卫是字符串集合比对,不做 glob)。
    #
    # ⚠️ 2026-09-01(修复轮1,reviewer Important-2 实测坐实):**这条登记对
    # `test_no_unregistered_artifact_literals` 是空转,不受守卫保护。** 根因:生产代码
    # 里 `_indicators.md` 只以 f-string 插值出现(`harvest.py`
    # `_blk_technical_indicators_compact` 的
    # `f"{ctx['ticker']}_{ctx['trade_date']}_indicators.md"`),从未有裸字符串字面量
    # `"_indicators.md"` ——守卫的 `_LITERAL_RE` 只抓完整的裸引号字面量,f-string 里的
    # `{...}` 插值天然不落在该正则的字符类里,grep 根本抓不到这个名字。reviewer 用
    # 与 `test_no_unregistered_artifact_literals` 完全相同的正则/扫描根实测:把这一条
    # 从 `ARTIFACTS` 里整条删掉重跑,unknown-literals 输出前后完全相同(均为空)——
    # 即它注不注册,守卫都不会红。`analyze_slim`/`analyze_slim_deep` 之所以真受保护,
    # 是因为 `scan/retention.py:182` 有一处裸字面量消费点
    # (`for suffix in ("_slim.md", "_slim_deep.md")`);`_indicators.md` 没有对应的
    # 裸字面量消费者。
    #
    # 用户裁定(二选一取 b):不为了激活守卫去发明一个本不需要的裸字面量消费点
    # (本末倒置)——这条登记保留为**文档性登记**(记录"这是个真实产物",供人类与
    # `for_stage`/`for_root` 等派生查询使用),但如实标注它当前**不受** drift 守卫
    # 保护。结论:「先登记再写码 → 守卫会红」这条纪律**只对以裸字面量出现的产物
    # 成立**;f-string 拼出来的产物名不在守卫覆盖范围内,登记了也不代表有安全网。
    # (独立复核脚本见 batch-D-report.md「修复轮 1」节:移除/保留本条注册,
    # `test_no_unregistered_artifact_literals` 的 unknown-literals 输出确认完全相同。)
    Artifact("analyze_indicators", "_indicators.md", "analyze_ctx", "harvest",
             "analyze.harvest", "md", "gated", required_when="full"),
    Artifact("analyze_sections", "1_analysts/*.md", "analyze_staging", "write",
             "stock-writer", "md", "always"),
    Artifact("analyze_research", "2_research/*.md", "analyze_staging", "write",
             "stock-writer", "md", "always"),
    Artifact("analyze_risk", "3_risk/*.md", "analyze_staging", "write",
             "stock-writer", "md", "always"),
    Artifact("analyze_portfolio", "4_portfolio/*.md", "analyze_staging", "write",
             "stock-writer", "md", "always"),
    Artifact("company_intel", "_company_intel.md", "analyze_staging", "intel",
             "company-intel", "md", "gated", required_when="full_ashare"),
    Artifact("us_intel", "_us_intel.md", "analyze_staging", "intel",
             "us-intel", "md", "gated", required_when="full_us"),
    Artifact("analyze_report_md", "*.md", "analyze_report", "assemble",
             "analyze.assemble", "md", "always"),
    Artifact("analyze_manifest", "manifest.json", "analyze_report", "assemble",
             "analyze.assemble", "json", "always"),
    Artifact("analyze_lite_card", "*_lite.md", "analyze_report", "card",
             "stock-writer", "md", "gated", required_when="lite"),
    Artifact("analyze_ledger_cards", "cards.csv", "analyze_ledger", "publish",
             "analyze.ledger", "csv", "gated", required_when="ledger"),
    # ── analyze_staging 分段草稿的 18 个具名文件(D6.1 Step4:扩根后 drift 守卫在
    #    autoresearch/analyze/assemble.py 的 SPINE/APPENDIX/DECISION_REL 里逮到的字面量,
    #    每个都是「产物」——engine-playbook.md §输出文件映射 逐字同源)。
    #    必需 11 个(assemble.py 的 opt=False,缺则 [MISSING] 硬挡):
    Artifact("analyze_decision", "4_portfolio/decision.md", "analyze_staging", "write",
             "stock-writer", "md", "always"),
    Artifact("analyze_variant", "2_research/variant.md", "analyze_staging", "write",
             "stock-writer", "md", "always"),
    Artifact("analyze_faceoff", "2_research/faceoff.md", "analyze_staging", "write",
             "stock-writer", "md", "always"),
    Artifact("analyze_calendar", "4_portfolio/calendar.md", "analyze_staging", "write",
             "stock-writer", "md", "always"),
    Artifact("analyze_premortem", "3_risk/premortem.md", "analyze_staging", "write",
             "stock-writer", "md", "always"),
    Artifact("analyze_market", "1_analysts/market.md", "analyze_staging", "write",
             "stock-writer", "md", "always"),
    Artifact("analyze_news", "1_analysts/news.md", "analyze_staging", "write",
             "stock-writer", "md", "always"),
    Artifact("analyze_fundamentals", "1_analysts/fundamentals.md", "analyze_staging", "write",
             "stock-writer", "md", "always"),
    Artifact("analyze_bull", "2_research/bull.md", "analyze_staging", "write",
             "stock-writer", "md", "always"),
    Artifact("analyze_bear", "2_research/bear.md", "analyze_staging", "write",
             "stock-writer", "md", "always"),
    Artifact("analyze_manager", "2_research/manager.md", "analyze_staging", "write",
             "stock-writer", "md", "always"),
    #    可选 7 个(engine-playbook.md §输出文件映射「optional lens」名单,写手视深度取舍):
    Artifact("analyze_debate", "3_risk/debate.md", "analyze_staging", "write",
             "stock-writer", "md", "gated", required_when="optional_lens"),
    Artifact("analyze_quality", "1_analysts/quality.md", "analyze_staging", "write",
             "stock-writer", "md", "gated", required_when="optional_lens"),
    Artifact("analyze_valuation", "1_analysts/valuation.md", "analyze_staging", "write",
             "stock-writer", "md", "gated", required_when="optional_lens"),
    Artifact("analyze_positioning", "1_analysts/positioning.md", "analyze_staging", "write",
             "stock-writer", "md", "gated", required_when="optional_lens"),
    Artifact("analyze_peer", "1_analysts/peer.md", "analyze_staging", "write",
             "stock-writer", "md", "gated", required_when="optional_lens"),
    Artifact("analyze_solvency", "1_analysts/solvency.md", "analyze_staging", "write",
             "stock-writer", "md", "gated", required_when="optional_lens"),
    Artifact("analyze_reality_check", "2_research/reality_check.md", "analyze_staging", "write",
             "stock-writer", "md", "gated", required_when="optional_lens"),
    # ── 跨 run 计量读模型(不污染任何单次 run 的 manifest/现场)─────────────
    Artifact("panorama_json", "panorama_*.json", "metering", "observe",
             "usage_panorama", "json", "gated", required_when="usage_panorama --write"),
    Artifact("panorama_md", "panorama_*.md", "metering", "observe",
             "usage_panorama", "md", "gated", required_when="usage_panorama --write"),
    # ── 研究仪器读数(2026-09-07 Q-R;零 LLM、只读、不进任何 run 现场)────────
    Artifact("edge_census_md", "edge_census.md", "research_report", "observe",
             "edge_census", "md", "gated", required_when="edge_census 跑过"),
    Artifact("edge_census_json", "_edge_census.json", "research_report", "observe",
             "edge_census", "json", "gated", required_when="edge_census 跑过"),
    Artifact("overseas_census_md", "overseas_event_census.md", "research_report", "observe",
             "overseas_event_census", "md", "gated", required_when="overseas_event_census 跑过"),
    Artifact("overseas_census_json", "_overseas_event_census.json", "research_report", "observe",
             "overseas_event_census", "json", "gated", required_when="overseas_event_census 跑过"),
    Artifact("derivatives_census_md", "derivatives_census.md", "research_report", "observe",
             "derivatives_census", "md", "gated", required_when="derivatives_census 跑过"),
    Artifact("derivatives_census_json", "_derivatives_census.json", "research_report", "observe",
             "derivatives_census", "json", "gated", required_when="derivatives_census 跑过"),
    Artifact("overnight_census_json", "overnight_census/_overnight_census.json", "research_report",
             "observe", "overnight_census", "json", "gated",
             required_when="overnight_census --run(md 默认落 docs/,见 A 包 --out/--out-json)"),
    Artifact("lowturn_precheck_md", "lowturn_precheck.md", "research_report", "observe",
             "lowturn_precheck", "md", "gated", required_when="lowturn_precheck 跑过"),
    Artifact("feature_gate_md", "feature_gate.md", "research_report", "observe",
             "feature_gate", "md", "gated", required_when="feature_gate 跑过"),
    Artifact("feature_gate_ledger", "feature_gate.json", "research_ctx", "observe",
             "feature_gate", "json", "gated", required_when="feature_gate 跑过"),
    Artifact("nested_probe_ledger", "nested_probe.json", "research_ctx", "observe",
             "nested_probe", "json", "gated", required_when="nested_probe 跑过"),
    Artifact("ic_by_regime_md", "ic_by_regime.md", "research_report", "observe",
             "factor_lab", "md", "gated", required_when="factor_lab run_ic_by_regime"),
    Artifact("sector_top3_backtest_csv", "sector_top3_backtest.csv", "research_report", "observe",
             "sector_top3_backtest", "csv", "gated", required_when="sector_top3_backtest 跑过"),
    # 指数调样事件 · 隔夜尺普查(design 2026-09-25 附录 A 的可复现版;O8)
    Artifact("index_rebalance_census_md", "index_rebalance_census.md", "research_report", "observe",
             "index_rebalance_census", "md", "gated", required_when="index_rebalance_census 跑过"),
    Artifact("index_rebalance_census_json", "_index_rebalance_census.json", "research_report", "observe",
             "index_rebalance_census", "json", "gated", required_when="index_rebalance_census 跑过"),
    # factor_lab 自己的落盘根(ws.factor_lab_root())
    Artifact("factor_lab_ic_table", "ic_table.csv", "factor_lab", "observe",
             "factor_lab", "csv", "gated", required_when="factor_lab eval"),
    Artifact("factor_lab_decile_table", "decile_table.csv", "factor_lab", "observe",
             "factor_lab", "csv", "gated", required_when="factor_lab eval"),
    Artifact("factor_lab_ic_by_regime", "ic_by_regime.csv", "factor_lab", "observe",
             "factor_lab", "csv", "gated", required_when="factor_lab run_ic_by_regime"),
    Artifact("factor_lab_panel_meta", "panel_meta.json", "factor_lab", "observe",
             "factor_lab", "json", "gated", required_when="factor_lab harvest"),
    Artifact("factor_lab_youzi_seats", "youzi_seats.json", "factor_lab", "observe",
             "factor_lab", "json", "gated", required_when="factor_lab harvest(席位缓存)"),
    # ── 离线实验目录(F1 冻结 / F8 阶段价值 / C5 执行评价;experiment_id 一目录,排他创建)──
    Artifact("experiment_spec", "*/spec.json", "research_report", "observe",
             "experiment_io", "json", "conditional"),
    Artifact("experiment_input_manifest", "*/input_manifest.json", "research_report", "observe",
             "experiment_io", "json", "conditional"),
    Artifact("experiment_manifest", "*/manifest.json", "research_report", "observe",
             "experiment_io", "json", "conditional"),
    Artifact("experiment_readout", "*/readout.md", "research_report", "observe",
             "experiment_io", "md", "conditional"),
    Artifact("stage_value_daily_delta", "stage_value/*/daily_delta.csv", "research_report",
             "observe", "stage_value", "csv", "conditional"),
    Artifact("stage_value_coverage", "stage_value/*/coverage.json", "research_report",
             "observe", "stage_value", "json", "conditional"),
    Artifact("stage_value_statistics", "stage_value/*/statistics.json", "research_report",
             "observe", "stage_value", "json", "conditional"),
    Artifact("execution_assessments", "execution/*/assessments.csv", "research_report",
             "observe", "execution_audit", "csv", "conditional"),
    Artifact("execution_daily_metrics", "execution/*/daily_metrics.csv", "research_report",
             "observe", "execution_audit", "csv", "conditional"),
    Artifact("execution_coverage", "execution/*/coverage.json", "research_report",
             "observe", "execution_audit", "json", "conditional"),
    # F6 W3 三格普查(2026-09-07 登记;冻结方案见 docs/research/2026-09-07-w3-three-grids-family.spec.json)
    Artifact("w3_grids_cells", "w3_grids/*/cells.csv", "research_report", "observe",
             "w3_grids", "csv", "conditional"),
    Artifact("w3_grids_statistics", "w3_grids/*/statistics.json", "research_report", "observe",
             "w3_grids", "json", "conditional"),
    Artifact("w3_grids_signals", "w3_grids/*/signal_coverage.json", "research_report", "observe",
             "w3_grids", "json", "conditional"),
    # ── 券商成交取数层(08-27 设计稿 §5;不进 lake/,只记不学)──────────────────
    Artifact("broker_trades", "trades.csv", "broker", "observe",
             "broker.ingest", "csv", "gated", required_when="broker ingest 跑过"),
    Artifact("broker_raw", "raw/*.csv", "broker", "observe",
             "broker.store", "csv", "gated", required_when="broker ingest 跑过"),
    # ── 场景重建:证据归属产物(2026-09-12 设计稿 §9;Task 1 只登记,生产者留 Task
    #    3/4/8/9 —— `scan/transcript_binder.py`、`scan/salvage.py` 均尚未创建。这是
    #    「先登记再写码」的正向用例:drift 守卫扫的是代码里出现的未登记字面量,不
    #    要求登记表反向证明生产者已存在。`capsule/agents/{index.json,bindings.jsonl,
    #    raw/,normalized/}` 本身**不在这里新增**——spec §9 那一行写的是「沿用 capsule
    #    契约」,即维持它们的既有处置,只是 schema 版本号往上走;不重复挂号。
    #    2026-09-13(fix round 1,Important 1 更正):这四个名字受到的保护**并不一致**,
    #    不能一概说成「都在 NON_ARTIFACT_LITERALS 里」——grep 实测只有
    #    `"agents/index.json"` 真的在那张白名单里;`bindings.jsonl`、`raw/`、
    #    `normalized/` 根本不在白名单,而是**从未进入过守卫的扫描范围**:
    #    `test_registry_parity.py` 的 `_LITERAL_RE` 只匹配以 `.csv`/`.json`/`.md`/
    #    `.txt` 收尾的带引号字面量,`.jsonl` 后缀与两个无后缀目录名天然落在这个
    #    正则的字符类之外。也就是说这三个名字今天**无人守护**:把它们改名或删掉,
    #    `test_no_unregistered_artifact_literals` 不会红。是否要把 `_LITERAL_RE`
    #    扩到覆盖 `.jsonl`/无后缀目录名是另一件事(会改变守卫在全仓的扫描范围),
    #    留给控制者记录的整分支收尾评审裁决,本任务不在此处顺手改。────────────
    Artifact("transcript_bindings_report", "_transcript_bindings.json", "staging",
             "observe", "transcript_binder", "json", "always"),
    Artifact("transcript_ledger_index", "agents_index/*.json", "ledger", "observe",
             "transcript_binder", "json", "conditional",
             required_when="transcript_binder --offline 跑过该 report_run_id"),
    Artifact("transcript_ledger_revision", "agents_index/*/*", "ledger", "observe",
             "transcript_binder", "dir", "conditional",
             required_when="transcript_binder --offline 生成了新的重建版本(revision_id)"),
    Artifact("salvage_provenance", "salvage/*/provenance.json", "ledger", "observe",
             "salvage", "json", "conditional", required_when="salvage 跑过该 report_run_id"),
    # Task 9(2026-09-12,fix round 1 finding 1):抢救到的文件字节本身——按内容摘要命名
    # 保存(spec §9「文件快照按摘要命名保存」),与 `provenance.json` 分开登记,因为它是
    # dir-kind 的内容寻址存储而非单一 JSON 文件。**复用**既有 `trace.blobs.put_bytes`/
    # `blob_path`(`relative_buy.py` 归档卡快照走的同一实现,不是本任务另起的第二份),
    # 所以布局是该模块自己的两级扇出 `blobs/sha256/<digest 前两位>/<digest>`——不是一层
    # `blobs/<digest>`。与 `transcript_ledger_revision`/`outcome_migration` 同一处置,
    # 内部按 sha256 命名的具体文件不逐一登记。只在 `salvage.py` 真的读到了字节时才写入
    # (VERIFIED_RUN/TIME_WINDOW_ONLY/OVERWRITTEN_BY_LATER_RUN,以及少数"读到字节但判不出
    # 窗口"的 UNKNOWN);连字节都没读到的 ABSENT,或未能定位到 transcript 候选的
    # UNKNOWN,不产生 blob。
    Artifact("salvage_blob", "salvage/*/blobs/sha256/*/*", "ledger", "observe",
             "salvage", "dir", "conditional",
             required_when="salvage 抢救到至少一份可归档的文件字节(逐文件按内容摘要命名)"),
    Artifact("scene_reconstruction_acceptance", "acceptance/scene-reconstruction-*.md",
             "ledger", "observe", "acceptance", "md", "conditional",
             required_when="真实验收发生后由本引擎执行人记录,不由文档修订生成"),
    # ── 可审阅回填与恢复(2026-09-12 outcome-trading-calendar-integrity §6 Task C3)──
    #    `_ledger/outcome_migrations/<migration_id>/` 是一次「把受旧日历口径影响的
    #    历史账本重算一遍」的完整审阅现场:before/(应用前逐字节原样拷贝)、after/
    #    (拟写入的候选文档)、diff.json(人读的逐行审阅表 + 人口统计)、
    #    migration_state.json(迁移自身的进度,供中断后判断能否继续/恢复)——与
    #    `transcript_ledger_revision` 同一处置:整个目录用一条 dir-kind glob 登记
    #    为一个产物族,内部固定文件名不逐一登记(见下方 NON_ARTIFACT_LITERALS 的
    #    对应说明)。`migration_id` 由 `--run-id`/`--rebuild` 输入派生,不含挂钟时间。
    Artifact("outcome_migration", "outcome_migrations/*", "ledger", "observe",
             "outcome_migrate", "dir", "conditional",
             required_when=("outcome fill --dry-run 或 --run-id 规划/应用过一次迁移"
                            "(§6 Task C3;目录内含 before/、after/、diff.json、"
                            "migration_state.json)")),
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
    "source_tree.tar.zst", "source_tree_manifest.json", "runtime_manifest.json",
    "portable_runtime.tar.zst", "state.json", "submodules.json", "verification/ROOT.json",
    # session_v1 发布事务自己的 journal/seal 元数据；业务文件仍逐项登记并绑定。
    "publication/journal.json", "publication_bundle.json", "sealed_manifest.json",
    "_publication/state_index.json",
    "dependencies.txt", "identity/dependencies.txt",
    "verification/profile.json", "usage/_token_usage.json", "usage/token_usage.md",
    "lake_manifest.json", "_t0.json", "environment.json",
    # 引擎根下的非 run 产物 / 历史遗留
    "learning/l2_knife_audit.md", "learning/structural_audit.md",
    "learning/temperature.csv", "temperature.csv", "research/temperature_calib.md",
    "_macro_cn.json", "weights.json", "L1_weights.json", "_claim_ledger.csv",
    # 2026-09-25(终审 M4):`L1_weight_profile.json` 与紧邻的 `L1_weights.json` 同类 ——
    # 都是 `session_agent.domain_ops._freeze_scan_runtime_inputs` 写进 run 冻结输入目录的
    # **身份快照镜像**(前者对应偏好档、后者对应旧校准档),不是漏斗自己产出的业务产物。
    # 与其兄弟同样处理:进白名单而非登记表。`publisher.py` 对两者各有一条完整性断言。
    "L1_weight_profile.json",
    "_dossier_snapshot.json",
    # 2026-09-07(Q-R):守卫扩到 autoresearch/research 与 autoresearch/broker 后冒出的非产物
    "docs/research/2026-08-28-overnight-concentrated-census-readout.md",   # 已提交的读数文档(census 的 md 默认落点)
    "docs/research/2026-09-07-w3-three-grids-family.spec.json",           # 已提交的**预注册方案**(W3 三格),不是 run 产物
    # broker inbox 的 `*.csv` 通配不进白名单:已登记的 `raw/*.csv` 基名就是它,白名单再写会与登记表重叠
    # 2026-08-31(D6.1):`_slim.md`/`_slim_deep.md` 挪出白名单,改为正式登记
    # `analyze_slim`/`analyze_slim_deep`(见 ARTIFACTS 尾部 stock-research 节)。
    "_price_claim_status.json", "price_claim_subjects.json",
    "_relative_buy_decision.mismatch.json", "_resolved_agent_config.json",
    "_tripwire_conflicts.json", "l4_watch_cursor.json", "_health.json",
    "temperature_row.json",
    # 已退役但仍被读的(消费者无生产者;见 spec §1.3)
    "verify.csv", "L3_fine_finalists.csv",
    # 报告目录内的旧名 / 兼容
    "gate4.json",
    # outcome_migration(ledger/outcome_migrations/*,dir-kind,2026-09-12 Task C3)的
    # 内部固定文件名——与 capsule 内部结构、`transcript_ledger_revision` 同一处置:
    # 整个迁移目录已用上面那条 dir-kind 登记覆盖,`diff.json`/`migration_state.json`
    # 在每个 <migration_id>/ 下逐字同名重复出现,不逐一登记成带完整路径的 Artifact。
    "diff.json", "migration_state.json",
    # 2026-09-13:漏斗对照实验(`research/funnel_variants.py`)的研究 bundle —— 落
    # `reports_<engine>/research/funnel_variants/<experiment_id>/`,不属于任何 run 的
    # 期望证据:它由人手动对**已冻结**的历史日跑,不接 prelude、不写 run 目录,缺席也
    # 不该让任何一次扫描的完整性判定变脸。同族处置见上面 W3 预注册方案那条。
    # ⚠️ 同 bundle 的 `daily_metrics.csv` / `manifest.json` 之所以不在这里,只是因为
    # 这两个名字与**别的**已登记产物重名、被守卫按基名放行了 —— 不是它们另有归属。
    # 守卫按基名匹配,所以重名即免检:这是它今天的一个盲点,记在此处备查。
    "membership.csv", "paired_summary.json",
    # 2026-09-24(Task 9,可买性对齐批 1):`research/menu_replay.py` 离线重算工具的输出——
    # 同 `funnel_variants.py` 一族但更彻底:落点是调用方在 CLI 传的 `--out`(本任务实跑落在
    # session scratchpad),不是仓内任何固定路径,连"reports_<engine>/research/..."这样的
    # 可预测前缀都没有——工具本身对生产 staging 只读、只写 `--out`,不进 context_*/、
    # reports_*/、lake/,天然不可能是任何一次扫描的期望证据。
    "menu_replay.csv", "menu_replay.md", "weights_doc.json",
    # Codex harness 自己的模型能力缓存(`~/.codex/models_cache.json`)——**我们只读不产**。
    # runtime capability 那一层事实的来源,不在本仓任何产物根下。读不到 = UNKNOWN,
    # 不是缺产物(`user_config.load_codex_capabilities` 返回 None,不编造支持度)。
    "models_cache.json",
})
