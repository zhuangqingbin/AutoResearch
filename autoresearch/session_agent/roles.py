"""Logical research roles backed by the existing skill and agent instructions."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import MappingProxyType

from autoresearch.contracts.agent_roles import (
    _ROLES,
    PHYSICAL_AGENTS as _PHYSICAL,
    configured_agents,
    dispatch_mapping,
)

# Capabilities describe the adapter boundary, not REAL_SESSION proof or filesystem
# isolation. Host receipts and output validation still have to demonstrate execution.
EXECUTOR_CAPABILITIES = MappingProxyType({
    "mailbox": {"engines": ("claude", "codex"), "independent_context": "HOST",
                "web": "HOST", "tool_policy": "HOST_ROLE_CONTRACT",
                "output_boundary": "REGISTERED_ARTIFACT_VALIDATION"},
    "headless": {"engines": ("claude", "codex"), "independent_context": True,
                 "web": "HOST", "tool_policy": "PROJECT_AGENT_TOOL_SUPERSET",
                 "output_boundary": "REGISTERED_ARTIFACT_VALIDATION"},
})

_FIELDS = frozenset({
    "role_id", "instruction_refs", "instruction_section", "input_policy", "output_contract",
    "context_policy", "tool_policy", "config_role", "stage", "accepted_output_contracts",
})





def codex_agent_names() -> dict[str, str]:
    return {role["config_role"]: _PHYSICAL[role["config_role"]][1]
            for role in _ROLES.values() if role["config_role"] is not None}


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
    payload["physical_agents"] = {
        key: {**spec, "codex_definition_sha256": hashlib.sha256(
            Path(f".codex/agents/{key}.toml").read_bytes()).hexdigest()}
        for key, spec in configured_agents().items()
    }
    payload["executor_capabilities"] = dict(EXECUTOR_CAPABILITIES)
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def role_stage(role_id: str) -> str:
    return get_role(role_id)["stage"]


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
    "all_roles", "dispatch_mapping", "get_role", "role_manifest", "role_stage", "roles_hash",
]
