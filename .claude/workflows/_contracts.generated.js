// 自动生成,请勿手编 —— 真身是 `autoresearch/contracts/`。
// 重新生成:`uv run --no-sync python -m autoresearch.contracts.emit --write`
//
// 为什么有这个文件:JS 与 python 此前各持一份评级序(方向还相反)、任务动作枚举、
// 阶段串与 staging 路径。两份真身 = 两个可以各自漂移的地方,而 JS 侧没有测试
// (`node --check` 对 ESM 顶层 return 零鉴别力)。现在 python 是唯一真身。

export const CONTRACTS_HASH = '5df6cb47aeb2418e'

export const CONTRACTS = {
  "artifacts": {
    "appendix": {
      "path": "appendix.md",
      "presence": "gated",
      "root": "report",
      "stage": "l5"
    },
    "artifact_index": {
      "path": "artifact_index.json",
      "presence": "always",
      "root": "report",
      "stage": "l5"
    },
    "brief": {
      "path": "brief.md",
      "presence": "always",
      "root": "report",
      "stage": "l5"
    },
    "brief_sources": {
      "path": "_brief_sources.json",
      "presence": "always",
      "root": "staging",
      "stage": "l5"
    },
    "budget_observation": {
      "path": "_budget_observation.json",
      "presence": "always",
      "root": "staging",
      "stage": "observe"
    },
    "calendar": {
      "path": "calendar.csv",
      "presence": "always",
      "root": "staging",
      "stage": "prelude"
    },
    "candidate_passport": {
      "path": "_candidate_passport.json",
      "presence": "always",
      "root": "staging",
      "stage": "l5"
    },
    "capsule_completeness": {
      "path": "verification/completeness.json",
      "presence": "always",
      "root": "capsule",
      "stage": "finalize"
    },
    "capsule_evidence_index": {
      "path": "lineage/external_evidence_index.json",
      "presence": "gated",
      "root": "capsule",
      "stage": "finalize"
    },
    "capsule_expected": {
      "path": "verification/expected.json",
      "presence": "always",
      "root": "capsule",
      "stage": "finalize"
    },
    "capsule_json": {
      "path": "capsule.json",
      "presence": "always",
      "root": "capsule",
      "stage": "finalize"
    },
    "capsule_replay": {
      "path": "verification/replay.json",
      "presence": "always",
      "root": "capsule",
      "stage": "finalize"
    },
    "capsule_root": {
      "path": "ROOT.json",
      "presence": "always",
      "root": "capsule",
      "stage": "finalize"
    },
    "capsule_web_budget": {
      "path": "usage/web_budget.json",
      "presence": "gated",
      "root": "capsule",
      "stage": "finalize"
    },
    "consensus": {
      "path": "consensus.csv",
      "presence": "gated",
      "root": "staging",
      "stage": "l4_prep"
    },
    "consumer_state": {
      "path": "outbox/consumer_state.json",
      "presence": "always",
      "root": "staging",
      "stage": "observe"
    },
    "decision_records": {
      "path": "decision_records.json",
      "presence": "always",
      "root": "staging",
      "stage": "l5"
    },
    "degraded": {
      "path": "degraded.json",
      "presence": "always",
      "root": "staging",
      "stage": "prelude"
    },
    "dissent_records": {
      "path": "dissent_records.json",
      "presence": "gated",
      "root": "staging",
      "stage": "l5"
    },
    "dossier_present": {
      "path": "_dossier_present.json",
      "presence": "always",
      "root": "staging",
      "stage": "l4_prep"
    },
    "early_stop": {
      "path": "_early_stop.json",
      "presence": "always",
      "root": "staging",
      "stage": "l5"
    },
    "ensemble": {
      "path": "_ensemble_*.json",
      "presence": "conditional",
      "root": "staging",
      "stage": "l4"
    },
    "final_ratings": {
      "path": "_final_ratings.json",
      "presence": "always",
      "root": "staging",
      "stage": "l5"
    },
    "finalists": {
      "path": "finalists.csv",
      "presence": "always",
      "root": "staging",
      "stage": "l3"
    },
    "fund_hold": {
      "path": "fund_hold.csv",
      "presence": "gated",
      "root": "staging",
      "stage": "l4_prep"
    },
    "funnel": {
      "path": "funnel.md",
      "presence": "always",
      "root": "report",
      "stage": "l5"
    },
    "funnel_meta": {
      "path": "meta.json",
      "presence": "always",
      "root": "staging",
      "stage": "prelude"
    },
    "gate_fires": {
      "path": "gate_fires.csv",
      "presence": "always",
      "root": "staging",
      "stage": "l5"
    },
    "harvest_list": {
      "path": "_harvest_list.txt",
      "presence": "always",
      "root": "staging",
      "stage": "l4_prep"
    },
    "index": {
      "path": "index.md",
      "presence": "always",
      "root": "report",
      "stage": "l5"
    },
    "l0_meta": {
      "path": "L0_universe_meta.json",
      "presence": "always",
      "root": "staging",
      "stage": "prelude"
    },
    "l1_channels": {
      "path": "L1_channels.csv",
      "presence": "always",
      "root": "staging",
      "stage": "prelude"
    },
    "l1_full": {
      "path": "L1_scored_full.csv",
      "presence": "always",
      "root": "staging",
      "stage": "prelude"
    },
    "l1_recall": {
      "path": "L1_recall_top1000.csv",
      "presence": "always",
      "root": "staging",
      "stage": "prelude"
    },
    "l2": {
      "path": "L2_gbdt_top200.csv",
      "presence": "always",
      "root": "staging",
      "stage": "prelude"
    },
    "l3_bench": {
      "path": "_l3_bench.csv",
      "presence": "always",
      "root": "staging",
      "stage": "l3"
    },
    "l3_catalyst": {
      "path": "L3_catalyst.csv",
      "presence": "gated",
      "root": "staging",
      "stage": "l3"
    },
    "l3_judged": {
      "path": "L3_judged_full.csv",
      "presence": "always",
      "root": "staging",
      "stage": "l3"
    },
    "l3_judged_raw": {
      "path": "_l3_judged.json",
      "presence": "always",
      "root": "staging",
      "stage": "l3"
    },
    "l3_pass1_cut": {
      "path": "_l3_pass1_cut.csv",
      "presence": "always",
      "root": "staging",
      "stage": "l3"
    },
    "l3_pass1_kept": {
      "path": "_l3_pass1_kept.csv",
      "presence": "always",
      "root": "staging",
      "stage": "l3"
    },
    "l3_pass1_meta": {
      "path": "_l3_pass1_meta.json",
      "presence": "always",
      "root": "staging",
      "stage": "l3"
    },
    "l3_repair_pack": {
      "path": "_l3_repair_pack.json",
      "presence": "conditional",
      "root": "staging",
      "stage": "l3"
    },
    "l3_repair_patch": {
      "path": "_l3_repair_patch.json",
      "presence": "conditional",
      "root": "staging",
      "stage": "l3"
    },
    "l3_repair_prompt": {
      "path": "_l3_repair_prompt.md",
      "presence": "conditional",
      "root": "staging",
      "stage": "l3"
    },
    "l3_table": {
      "path": "_l3_table.md",
      "presence": "always",
      "root": "staging",
      "stage": "l3"
    },
    "l4_cards": {
      "path": "details/*.md",
      "presence": "always",
      "root": "staging",
      "stage": "l4"
    },
    "l4_intel": {
      "path": "_l4_intel_*.md",
      "presence": "gated",
      "root": "staging",
      "stage": "l4"
    },
    "l4_intel_status": {
      "path": "_l4_intel_status_*.json",
      "presence": "gated",
      "root": "staging",
      "stage": "l4"
    },
    "l4_prompts": {
      "path": "_l4_prompt_*.md",
      "presence": "always",
      "root": "staging",
      "stage": "l4_prep"
    },
    "l4_rejection_readout": {
      "path": "_l4_rejection_readout.json",
      "presence": "gated",
      "root": "staging",
      "stage": "prelude"
    },
    "l4_shared_instructions": {
      "path": "_l4_shared_instructions.md",
      "presence": "always",
      "root": "staging",
      "stage": "l4_prep"
    },
    "l4_task_book": {
      "path": "_l4_tasks.json",
      "presence": "always",
      "root": "staging",
      "stage": "l4"
    },
    "ledger_market": {
      "path": "views/market.csv",
      "presence": "gated",
      "root": "ledger",
      "stage": "observe"
    },
    "ledger_runs": {
      "path": "views/runs.csv",
      "presence": "gated",
      "root": "ledger",
      "stage": "observe"
    },
    "ledger_sessions": {
      "path": "views/session_calendar.csv",
      "presence": "gated",
      "root": "ledger",
      "stage": "observe"
    },
    "ledger_stage_rulers": {
      "path": "views/stage_rulers.csv",
      "presence": "gated",
      "root": "ledger",
      "stage": "observe"
    },
    "manifest": {
      "path": "manifest.json",
      "presence": "always",
      "root": "report",
      "stage": "l5"
    },
    "market_pack": {
      "path": "market_pack.json",
      "presence": "always",
      "root": "staging",
      "stage": "frame"
    },
    "market_view": {
      "path": "market_view.md",
      "presence": "always",
      "root": "staging",
      "stage": "prelude"
    },
    "outbox_events": {
      "path": "outbox/events.json",
      "presence": "always",
      "root": "staging",
      "stage": "observe"
    },
    "overseas_calendar": {
      "path": "overseas_calendar.csv",
      "presence": "gated",
      "root": "staging",
      "stage": "prelude"
    },
    "pledge": {
      "path": "pledge.csv",
      "presence": "gated",
      "root": "staging",
      "stage": "l4_prep"
    },
    "prelude_summary": {
      "path": "_prelude_summary.md",
      "presence": "always",
      "root": "staging",
      "stage": "prelude"
    },
    "prewarm": {
      "path": "_prewarm.json",
      "presence": "gated",
      "root": "staging",
      "stage": "prelude"
    },
    "prewarm_failed": {
      "path": "_prewarm_failed.json",
      "presence": "conditional",
      "root": "staging",
      "stage": "prelude"
    },
    "published_cards": {
      "path": "details/*.md",
      "presence": "always",
      "root": "report",
      "stage": "l5"
    },
    "recommendations": {
      "path": "recommendations.csv",
      "presence": "gated",
      "root": "ledger",
      "stage": "observe"
    },
    "relative_buy_decision": {
      "path": "_relative_buy_decision.json",
      "presence": "always",
      "root": "staging",
      "stage": "observe"
    },
    "report_budget": {
      "path": "_report_budget.json",
      "presence": "always",
      "root": "staging",
      "stage": "l5"
    },
    "run_contract": {
      "path": "run_contract.json",
      "presence": "always",
      "root": "staging",
      "stage": "frame"
    },
    "run_health": {
      "path": "run_health.json",
      "presence": "always",
      "root": "report",
      "stage": "l5"
    },
    "run_mode": {
      "path": "run_mode.json",
      "presence": "always",
      "root": "staging",
      "stage": "gate1"
    },
    "seats": {
      "path": "seats.csv",
      "presence": "gated",
      "root": "staging",
      "stage": "l4_prep"
    },
    "sector_briefs": {
      "path": "sector_briefs/*.md",
      "presence": "gated",
      "root": "staging",
      "stage": "sector"
    },
    "sectors": {
      "path": "sectors.csv",
      "presence": "always",
      "root": "staging",
      "stage": "prelude"
    },
    "stage_results": {
      "path": "stage_results/*.json",
      "presence": "always",
      "root": "staging",
      "stage": "prelude"
    },
    "stage_timing": {
      "path": "_stage_timing.json",
      "presence": "gated",
      "root": "staging",
      "stage": "observe"
    },
    "strategist_pack": {
      "path": "strategist_pack.json",
      "presence": "always",
      "root": "staging",
      "stage": "frame"
    },
    "summary": {
      "path": "summary.md",
      "presence": "always",
      "root": "report",
      "stage": "l5"
    },
    "token_usage": {
      "path": "token_usage.md",
      "presence": "gated",
      "root": "report",
      "stage": "observe"
    },
    "token_usage_json": {
      "path": "_token_usage.json",
      "presence": "gated",
      "root": "staging",
      "stage": "observe"
    },
    "usage_reconcile": {
      "path": "_usage_reconcile.json",
      "presence": "gated",
      "root": "staging",
      "stage": "observe"
    },
    "user_config_echo": {
      "path": "user_config_echo.json",
      "presence": "always",
      "root": "staging",
      "stage": "frame"
    },
    "weights_used": {
      "path": "weights_used.json",
      "presence": "always",
      "root": "staging",
      "stage": "prelude"
    }
  },
  "conditional_roles": [
    "l3-repair",
    "l4-ensemble"
  ],
  "js_stage_aliases": {
    "finalize": "finalize",
    "frame": "frame",
    "gate1": "gate1",
    "gate2": "gate2",
    "gate4": "gate4",
    "l3": "l3",
    "l4": "l4",
    "l4-prep": "l4_prep",
    "l5": "l5",
    "observe": "observe",
    "prelude": "prelude",
    "sector": "sector"
  },
  "l4_skipping_modes": [
    "SENTINEL_EMPTY"
  ],
  "modes": [
    "FULL",
    "FORCED_FULL",
    "SENTINEL_EMPTY",
    "SENTINEL_PINNED"
  ],
  "ow_gates": [
    "主力真在",
    "业绩真兑现",
    "估值不透支"
  ],
  "proposals": [
    "BUY",
    "HOLD",
    "SELL"
  ],
  "rating_order": [
    "Buy",
    "Overweight",
    "Hold",
    "Underweight",
    "Sell"
  ],
  "rating_rank_js": {
    "buy": 4,
    "hold": 2,
    "overweight": 3,
    "sell": 0,
    "underweight": 1
  },
  "role_stages": {
    "l3-rank": "l3",
    "l3-repair": "l3",
    "l4-card": "l4",
    "l4-ensemble": "l4",
    "l4-intel": "l4",
    "sector-brief": "sector",
    "strategist": "prelude"
  },
  "schema_version": 1,
  "stages": [
    "frame",
    "prelude",
    "gate1",
    "sector",
    "l3",
    "gate2",
    "l4_prep",
    "l4",
    "l5",
    "observe",
    "gate4",
    "finalize"
  ],
  "stop_reasons": [
    "数据不足",
    "涨停追高",
    "题材透支",
    "资金流出",
    "估值透支",
    "基本面恶化",
    "其他"
  ]
}

export const STAGES = CONTRACTS.stages
export const MODES = CONTRACTS.modes
export const RATING_RANK = CONTRACTS.rating_rank_js
export const ARTIFACTS = CONTRACTS.artifacts

// 阶段串折叠:JS 用连字符,python 用下划线。
export const normalizeStage = (name) =>
  CONTRACTS.js_stage_aliases[name] ?? name

// 产物相对路径:JS 侧唯一该拼路径的地方。
export const artifactPath = (name) => {
  const spec = CONTRACTS.artifacts[name]
  if (!spec) throw new Error(`未登记的产物:${name}`)
  return spec.path
}
