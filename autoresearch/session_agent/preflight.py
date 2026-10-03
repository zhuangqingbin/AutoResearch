"""Check the potential inference role universe before expensive deterministic work.

This is a dispatch support report, never evidence of REAL_SESSION acceptance. An
adapter must be registered or explicitly declare capabilities; unknown adapters
cannot silently become general-purpose agents.
"""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import tomllib

from autoresearch.session_agent.roles import (
    EXECUTOR_CAPABILITIES,
    configured_agents,
    get_role,
)


class RolePreflightError(RuntimeError):
    def __init__(self, report: dict):
        self.report = report
        detail = "; ".join(f"{row['role']}: {', '.join(row['reasons'])}"
                           for row in report["roles"] if row["reasons"])
        super().__init__(f"role preflight failed ({report['engine']}/{report['executor']}): {detail}")


def _definition(engine: str, config_role: str) -> tuple[dict, str]:
    physical = configured_agents()[config_role]
    if engine == "codex":
        path = Path(f".codex/agents/{config_role}.toml")
        doc = tomllib.loads(path.read_text(encoding="utf-8"))
        if doc.get("name") != physical["codex_agent"]:
            raise ValueError(f"Codex name mismatch: {path}")
        if not doc.get("developer_instructions") or not doc.get("model"):
            raise ValueError(f"incomplete Codex role definition: {path}")
        return {"model": doc["model"], "reasoning_effort": doc.get("model_reasoning_effort")}, str(path)
    path = Path(f".claude/agents/{physical['claude_agent']}.md")
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError(f"missing role frontmatter: {path}")
    fields = dict(line.split(":", 1) for line in text.split("---", 2)[1].splitlines() if ":" in line)
    fields = {key.strip(): value.strip() for key, value in fields.items()}
    if fields.get("name") != physical["claude_agent"] or not fields.get("model"):
        raise ValueError(f"incomplete Claude role definition: {path}")
    definition = {key: fields[key] for key in ("model", "effort") if key in fields}
    definition["tools"] = tuple(tool.strip() for tool in fields.get("tools", "").split(",")
                                if tool.strip())
    return definition, str(path)


def _executor_support(executor) -> tuple[str, dict | None]:
    if isinstance(executor, str):
        return executor, EXECUTOR_CAPABILITIES.get(executor)
    name = str(getattr(executor, "name", type(executor).__name__))
    declared = getattr(executor, "capabilities", None)
    return name, dict(declared) if isinstance(declared, Mapping) else EXECUTOR_CAPABILITIES.get(name)


def preflight_roles(handle, role_ids, host_profile: dict, *, executor="mailbox", tasks=()) -> dict:
    from autoresearch.session_agent.dispatch import resolve_agent_spec
    from autoresearch.session_agent.task_access import capability
    from autoresearch.session_agent.boundary_proof import native_root_context
    engine = handle.engine
    boundary = capability(engine, root_context=native_root_context(engine, host_profile))
    executor_name, adapter = _executor_support(executor)
    adapter_web = (adapter or {}).get("web")
    web_capabilities = {capability: host_profile.get(capability) if adapter_web == "HOST" else adapter_web
                        for capability in ("web_search", "web_fetch")}
    task_roles = {task["role"] for task in tasks if task.get("kind") == "INFERENCE"}
    requested_independent = {task["role"] for task in tasks
                             if task.get("kind") == "INFERENCE" and task.get("independent_context")}
    from autoresearch.session_agent.config import orchestration_config

    config = orchestration_config(handle)
    rows = []
    for role_id in sorted(set(role_ids)):
        row = {"role": role_id, "engine": engine, "executor": executor_name,
               "agent_type": None, "config_role": None, "config_source": None,
               "independent_context": False, **web_capabilities, "tool_policy": None,
               "tool_policy_enforcement": (adapter or {}).get("tool_policy", "UNSUPPORTED"),
               "output_contract": None, "read_evidence_capability": "UNVERIFIED",
               "output_boundary": (adapter or {}).get("output_boundary", "UNSUPPORTED"),
               "research_access": boundary, "reasons": []}
        reasons = row["reasons"]
        if not boundary["runnable"]:
            reasons.append(boundary["reason"])
        if adapter is None:
            reasons.append(f"unknown executor {executor_name}; explicit capabilities required")
        elif engine not in adapter.get("engines", ()):
            reasons.append(f"executor does not support engine {engine}")
        if engine != host_profile.get("engine"):
            reasons.append("host engine differs from run engine")
        if host_profile.get("inference_handoff") is not True:
            reasons.append("inference_handoff unavailable or unknown")
        try:
            role = get_role(role_id)
            key = role["config_role"]
            if key is None:
                raise ValueError("role is deterministic-only; no inference mapping")
            for task in tasks:
                if task.get("role") == role_id and task.get("expected_output_contract") not in role["accepted_output_contracts"]:
                    reasons.append(f"unsupported output contract {task.get('expected_output_contract')}")
            physical = configured_agents()[key]
            row.update(config_role=key, agent_type=physical[f"{engine}_agent"],
                       tool_policy=role["tool_policy"], output_contract=role["output_contract"])
            definition, definition_path = _definition(engine, key)
            policy = role["tool_policy"].split("_")
            if engine == "claude":
                required_tools = {"READ": ("Read",), "WRITE": ("Write",),
                                  "WEB": ("WebSearch", "WebFetch")}
                for capability in policy:
                    for tool in required_tools.get(capability, ()):
                        if tool not in definition["tools"]:
                            reasons.append(f"Claude role tools missing {tool}")
            spec, tier, source = resolve_agent_spec(handle, key)
            # Old run contracts may predate model materialization. A concrete project
            # definition remains explicit and reviewable; never a general-purpose fallback.
            if source.startswith("UNRESOLVED"):
                if key in (config.get("agents") or {}):
                    reasons.append(source)
                spec, source = definition, "PROJECT_AGENT_DEFINITION"
            row.update(config_source=source, definition=definition_path, model=spec.get("model") or definition.get("model"), tier=tier)
            if executor_name == "mailbox":
                # 2026-10-03 钉版:mailbox 宿主派发时生效的是项目 agent 定义(Claude frontmatter /
                # Codex toml),不是请求里的解析值。两边不一致 = 这一场跑的不是冻结配置钉的模型,
                # 在任何取数之前拦下;headless 用 --model/--effort 显式透传,不受此约束。
                effort_key = "effort" if engine == "claude" else "reasoning_effort"
                for key in ("model", effort_key):
                    want, have = spec.get(key), definition.get(key)
                    if want is not None and have != want:
                        reasons.append(
                            f"agent definition {key}={have!r} ≠ frozen config {want!r} "
                            "(mailbox host loads the definition; sync: "
                            "uv run --no-sync python -m autoresearch.scan.agent_frontmatter --write; "
                            "roles sharing one definition with different settings need "
                            "begin/run --executor headless)")
            independent = (role_id in requested_independent if role_id in task_roles
                           else role["context_policy"] == "INDEPENDENT")
            available = (adapter or {}).get("independent_context")
            available = host_profile.get("independent_context") if available == "HOST" else available
            row["independent_context"] = available
            if independent and available is not True:
                reasons.append("independent_context unavailable or unknown")
            if "WEB" in policy:
                # Only HOST delegates capability discovery; a host cannot repair
                # an executor that explicitly denies or does not declare web.
                if adapter_web == "HOST":
                    for capability, available in web_capabilities.items():
                        if available is not True:
                            reasons.append(f"{capability} unavailable or unknown")
                elif adapter_web is not True:
                    reasons.append("executor web unavailable or unknown")
                if spec.get("web_search") == "disabled":
                    reasons.append("role model config disables live web_search")
        except (KeyError, ValueError, RuntimeError, OSError, tomllib.TOMLDecodeError) as exc:
            reasons.append(str(exc))
        rows.append(row)
    return {"schema_version": 1, "engine": engine, "executor": executor_name,
            "ok": all(not row["reasons"] for row in rows), "roles": rows,
            "research_access": boundary, "boundary_acceptance_satisfied": False,
            "acceptance": "SUPPORT_CHECK_ONLY"}


def preflight_plan(handle, plan: dict, host_profile: dict, *, executor="mailbox", tasks=None) -> dict:
    tasks = plan.get("tasks", []) if tasks is None else tasks
    roles = [task["role"] for task in tasks if task.get("kind") == "INFERENCE"]
    roles.extend(role for template in plan.get("task_templates", []) for role in template["allowed_roles"])
    report = preflight_roles(handle, roles, host_profile, executor=executor, tasks=tasks)
    if not report["ok"]:
        raise RolePreflightError(report)
    return report
