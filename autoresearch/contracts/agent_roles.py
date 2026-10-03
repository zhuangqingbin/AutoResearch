"""Pure physical-agent/config vocabulary shared by scan and session adapters."""
from __future__ import annotations

from types import MappingProxyType

# One registry owns physical definitions, config keys and logical task policy.
# The two relay roles have no inference tasks but share the config closed set.
PHYSICAL_AGENTS = {
    "strategist": ("macro-brief", "scan strategist", "critical", "high"),
    "sector_brief": ("sector-brief", "sector brief", "analytical", "high"),
    "l3_rank": ("l3-rank", "L3 rank", "critical", "max"),
    "l3_repair": ("l3-repair", "L3 repair", "repair", "medium"),
    "l4_intel": ("l4-intel", "L4 intel", "critical", "max"),
    "l4_card": ("l4-card", "L4 card", "critical", "xhigh"),
    "ens_review": ("l4-card", "ensemble review", "critical", "xhigh"),
    "dossier_init": ("dossier-init", "dossier init", "critical", "max"),
    "stock_full": ("stock-full", "stock full", "critical", "max"),
    "macro_full": ("macro-full", "macro full", "critical", "max"),
    "sector_full": ("sector-full", "sector full", "critical", "max"),
    "company_intel": ("company-intel", "company intel", "critical", "max"),
    "us_intel": ("us-intel", "US intel", "critical", "max"),
    "sector_intel": ("sector-intel", "sector intel", "critical", "max"),
    "global_intel": ("global-intel", "global intel", "critical", "max"),
    "gp_shell": (None, "deterministic command relay", "relay", "low"),
    "gp_shell_json": (None, "deterministic JSON relay", "relay", "low"),
}


def configured_agents() -> dict[str, dict]:
    return {key: {"claude_agent": value[0], "codex_agent": value[1],
                  "tier": value[2], "fallback_effort": value[3]}
            for key, value in PHYSICAL_AGENTS.items()}



def _role(role_id: str, config: str | None, output: str, stage: str, *,
          context: str = "SEQUENTIAL", tools: str = "READ_WRITE", contracts: tuple[str, ...] = ()) -> dict:
    ref = (f".claude/agents/{PHYSICAL_AGENTS[config][0]}.md" if config
           else ".claude/skills/scan-market/STAGES.md")
    return {"role_id": role_id, "instruction_refs": [ref],
            "instruction_section": role_id, "input_policy": f"{role_id}.inputs.v1",
            "output_contract": output, "accepted_output_contracts": list(contracts or (output,)),
            "context_policy": context,
            "tool_policy": tools, "config_role": config, "stage": stage}


_STOCK_FULL = (
    "market", "news", "fundamentals", "quality", "valuation", "positioning", "peer",
    "solvency", "reality_check", "bull", "bear", "manager", "premortem", "risk", "pm",
)
_ROLES = {
    "stock.card": _role("stock.card", "l4_card", "stock.lite.v1", "card", contracts=("stock.lite.v1", "research.card.initial.v1", "research.card.decision.v1")),
    **{f"stock.{name}": _role(f"stock.{name}", "stock_full",
        "stock.pm.v1" if name == "pm" else "stock.section.v1",
        "assemble" if name == "pm" else "write", context="INDEPENDENT",
        tools="READ_WEB_WRITE" if name == "news" else "READ_WRITE") for name in _STOCK_FULL},
    "company.intel": _role("company.intel", "company_intel", "company.intel.v1", "intel", context="INDEPENDENT", tools="WEB_WRITE"),
    "us.intel": _role("us.intel", "us_intel", "company.intel.v1", "intel", context="INDEPENDENT", tools="WEB_WRITE"),
    "global.intel": _role("global.intel", "global_intel", "global.intel.v1", "intel", context="INDEPENDENT", tools="WEB_WRITE"),
    "macro.brief": _role("macro.brief", "strategist", "macro.brief.v1", "write"),
    "macro.research": _role("macro.research", "macro_full", "macro.section.v1", "write", context="INDEPENDENT", tools="READ_WEB_WRITE", contracts=("macro.section.v1", "macro.allocation.v1")),
    "sector.brief": _role("sector.brief", "sector_brief", "sector.terrain.v1", "write",
                          tools="READ_WEB_WRITE", contracts=("sector.terrain.v1", "sector.events.v1")),
    "sector.research": _role("sector.research", "sector_full", "sector.full.v1", "write", context="INDEPENDENT", tools="READ_WEB_WRITE"),
    "sector.intel": _role("sector.intel", "sector_intel", "sector.intel.v1", "intel", context="INDEPENDENT", tools="WEB_WRITE"),
    "dossier.init": _role("dossier.init", "dossier_init", "dossier.v1", "research", tools="READ_WEB_WRITE"),
    "scan.l3": _role("scan.l3", "l3_rank", "scan.l3.v2", "l3", contracts=("scan.l3.v1", "scan.l3.v2")),
    "scan.l3.repair": _role("scan.l3.repair", "l3_repair", "scan.l3.repair.v1", "l3"),
    "scan.l4.intel": _role("scan.l4.intel", "l4_intel", "scan.l4.intel.v1", "l4", tools="WEB_WRITE"),
    "scan.l4.card": _role("scan.l4.card", "l4_card", "stock.lite.v1", "l4", contracts=("stock.lite.v1", "research.card.initial.v1", "research.card.decision.v1")),
    "scan.l4.review": _role("scan.l4.review", "ens_review", "stock.lite.v1", "l4", context="INDEPENDENT"),
    "scan.l5": _role("scan.l5", None, "scan.l5.v1", "l5"),
}


def dispatch_mapping() -> dict[str, tuple[str, str]]:
    return {key: (PHYSICAL_AGENTS[role["config_role"]][0], role["config_role"])
            for key, role in _ROLES.items() if role["config_role"] is not None}


# ── 轮数上限(2026-10-03 B8 从 session_agent.executors.headless_claude 下沉)───────────────
# headless 拼 `--max-turns`;mailbox 派发经 agent 定义 frontmatter `maxTurns` 镜像
# (`scan.agent_frontmatter`)。两条路径同一个数,所以真身放在两层共用的契约层。
#: ``--max-turns`` per role.  Longest real subagent runs over the 30 days before
#: 2026-09-26 (unique assistant messages): macro-brief 25, sector-brief 14, l3-rank 32,
#: l4-intel 34, l4-card 35.  Caps sit ~2x above them: the wall clock is the real guard,
#: the turn cap only stops a runaway loop (``--max-budget-usd`` is unverified under a
#: subscription, probe doc "待办").
MAX_TURNS = MappingProxyType({
    "macro.brief": 50,
    "sector.brief": 30,
    "scan.l3": 64,
    "scan.l3.repair": 30,
    "scan.l4.intel": 64,
    "scan.l4.card": 80,
    "scan.l4.review": 80,
})
#: Fallback for roles outside ``MAX_TURNS``: scan_config ``agents.<role>.tier``.
TIER_MAX_TURNS = MappingProxyType({
    "critical": 80, "analytical": 40, "repair": 30, "relay": 20,
})
DEFAULT_MAX_TURNS = 60


def role_max_turns(role: str, tier: str | None, *, overrides=None, tier_overrides=None,
                   default: int = DEFAULT_MAX_TURNS) -> int:
    """`overrides`(配置 `session.max_turns`,已与 `MAX_TURNS` 合并或单独给)→ 档位 → `default`。"""
    table = {**MAX_TURNS, **dict(overrides or {})}
    if role in table:
        return int(table[role])
    return int({**TIER_MAX_TURNS, **dict(tier_overrides or {})}.get(tier or "", default))
