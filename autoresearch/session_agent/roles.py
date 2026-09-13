"""Logical research roles backed by the existing skill and agent instructions."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

_FIELDS = frozenset({
    "role_id", "instruction_refs", "input_policy", "output_contract", "context_policy",
    "tool_policy", "config_role",
})


def _role(
    role_id: str,
    refs: list[str],
    output: str,
    *,
    context: str = "SEQUENTIAL",
    tools: str = "READ_WRITE",
    config_role: str | None = None,
) -> dict:
    return {
        "role_id": role_id,
        "instruction_refs": refs,
        "input_policy": f"{role_id}.inputs.v1",
        "output_contract": output,
        "context_policy": context,
        "tool_policy": tools,
        "config_role": config_role,
    }


_ROLES = {
    "stock.card": _role(
        "stock.card",
        [
            ".claude/skills/stock-research/lite-playbook.md",
            ".claude/agents/l4-card.md",
        ],
        "stock.lite.v1",
        config_role="l4-card",
    ),
    "stock.market": _role("stock.market", [".claude/skills/stock-research/engine-playbook.md"], "stock.section.v1"),
    "stock.news": _role("stock.news", [".claude/skills/stock-research/engine-playbook.md"], "stock.section.v1", tools="READ_WEB_WRITE"),
    "stock.fundamentals": _role("stock.fundamentals", [".claude/skills/stock-research/engine-playbook.md"], "stock.section.v1"),
    "stock.quality": _role("stock.quality", [".claude/skills/stock-research/engine-playbook.md"], "stock.section.v1"),
    "stock.valuation": _role("stock.valuation", [".claude/skills/stock-research/engine-playbook.md"], "stock.section.v1"),
    "stock.positioning": _role("stock.positioning", [".claude/skills/stock-research/engine-playbook.md"], "stock.section.v1"),
    "stock.peer": _role("stock.peer", [".claude/skills/stock-research/engine-playbook.md"], "stock.section.v1"),
    "stock.solvency": _role("stock.solvency", [".claude/skills/stock-research/engine-playbook.md"], "stock.section.v1"),
    "stock.reality_check": _role("stock.reality_check", [".claude/skills/stock-research/engine-playbook.md"], "stock.section.v1"),
    "stock.bull": _role("stock.bull", [".claude/skills/stock-research/engine-playbook.md"], "stock.section.v1"),
    "stock.bear": _role("stock.bear", [".claude/skills/stock-research/engine-playbook.md"], "stock.section.v1"),
    "stock.manager": _role("stock.manager", [".claude/skills/stock-research/engine-playbook.md"], "stock.section.v1"),
    "stock.premortem": _role("stock.premortem", [".claude/skills/stock-research/engine-playbook.md"], "stock.section.v1"),
    "stock.risk": _role("stock.risk", [".claude/skills/stock-research/engine-playbook.md"], "stock.section.v1"),
    "stock.pm": _role("stock.pm", [".claude/skills/stock-research/engine-playbook.md"], "stock.pm.v1"),
    "company.intel": _role("company.intel", [".claude/agents/company-intel.md"], "company.intel.v1", tools="WEB_WRITE", config_role="company-intel"),
    "us.intel": _role("us.intel", [".claude/agents/us-intel.md"], "company.intel.v1", tools="WEB_WRITE", config_role="us-intel"),
    "macro.brief": _role("macro.brief", [".claude/agents/macro-brief.md"], "macro.brief.v1", config_role="macro-brief"),
    "macro.research": _role("macro.research", [".claude/skills/macro-research/macro-playbook.md"], "macro.section.v1", tools="READ_WEB_WRITE"),
    "sector.brief": _role("sector.brief", [".claude/agents/sector-brief.md"], "sector.terrain.v1", config_role="sector-brief"),
    "sector.research": _role("sector.research", [".claude/skills/sector-research/sector-playbook.md"], "sector.full.v1", tools="READ_WEB_WRITE"),
    "sector.intel": _role("sector.intel", [".claude/agents/sector-intel.md"], "sector.intel.v1", tools="WEB_WRITE", config_role="sector-intel"),
    "dossier.init": _role("dossier.init", [".claude/agents/dossier-init.md"], "dossier.v1", tools="READ_WEB_WRITE", config_role="dossier-init"),
    "scan.l3": _role("scan.l3", [".claude/agents/l3-rank.md"], "scan.l3.v1", config_role="l3-rank"),
    "scan.l4.intel": _role("scan.l4.intel", [".claude/agents/l4-intel.md"], "scan.l4.intel.v1", tools="WEB_WRITE", config_role="l4-intel"),
    "scan.l4.card": _role("scan.l4.card", [".claude/agents/l4-card.md"], "stock.lite.v1", config_role="l4-card"),
    "scan.l4.review": _role("scan.l4.review", [".claude/agents/l4-card.md"], "stock.lite.v1", context="INDEPENDENT", config_role="l4-card"),
    "scan.l5": _role("scan.l5", [".claude/skills/scan-market/STAGES.md"], "scan.l5.v1"),
}

_ROLE_STAGES = {
    "stock.card": "card",
    "stock.market": "write",
    "stock.news": "write",
    "stock.fundamentals": "write",
    "stock.quality": "write",
    "stock.valuation": "write",
    "stock.positioning": "write",
    "stock.peer": "write",
    "stock.solvency": "write",
    "stock.reality_check": "write",
    "stock.bull": "write",
    "stock.bear": "write",
    "stock.manager": "write",
    "stock.premortem": "write",
    "stock.risk": "write",
    "stock.pm": "assemble",
    "company.intel": "intel",
    "us.intel": "intel",
    "macro.brief": "write",
    "macro.research": "write",
    "sector.brief": "write",
    "sector.research": "write",
    "sector.intel": "intel",
    "dossier.init": "research",
    "scan.l3": "l3",
    "scan.l4.intel": "l4",
    "scan.l4.card": "l4",
    "scan.l4.review": "l4",
    "scan.l5": "l5",
}

if set(_ROLE_STAGES) != set(_ROLES):
    raise RuntimeError("logical role stage registry is incomplete")


def _validate_role(value: dict) -> dict:
    if set(value) != _FIELDS:
        raise RuntimeError("invalid role registry entry")
    refs = value["instruction_refs"]
    if not isinstance(refs, list) or not refs:
        raise RuntimeError("role has no instruction references")
    missing = [ref for ref in refs if not Path(ref).is_file()]
    if missing:
        raise RuntimeError(f"role instruction is missing: {missing}")
    return value


def get_role(role_id: str) -> dict:
    try:
        return _validate_role(_ROLES[role_id])
    except KeyError as exc:
        raise KeyError(f"unknown session role: {role_id}") from exc


def roles_hash() -> str:
    payload = {}
    for role_id in sorted(_ROLES):
        role = get_role(role_id)
        payload[role_id] = {
            **role,
            "instruction_hashes": {
                ref: hashlib.sha256(Path(ref).read_bytes()).hexdigest()
                for ref in role["instruction_refs"]
            },
        }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def role_stage(role_id: str) -> str:
    get_role(role_id)
    return _ROLE_STAGES[role_id]


def role_manifest(role_ids: list[str] | tuple[str, ...]) -> dict:
    roles = {}
    for role_id in sorted(set(role_ids)):
        role = get_role(role_id)
        roles[role_id] = {
            "stage": role_stage(role_id),
            "instruction_refs": role["instruction_refs"],
            "instruction_hashes": {
                ref: hashlib.sha256(Path(ref).read_bytes()).hexdigest()
                for ref in role["instruction_refs"]
            },
        }
    return {"schema_version": 1, "roles_hash": roles_hash(), "roles": roles}


def all_roles() -> tuple[str, ...]:
    return tuple(sorted(_ROLES))


__all__ = [
    "all_roles", "get_role", "role_manifest", "role_stage", "roles_hash",
]
