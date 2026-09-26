"""Render one claimed inference attempt into a :class:`DispatchRequest` (runner side).

This is the single place where a session task becomes "what to launch": the project
agent (``executors.base.ROLE_DISPATCH``), model/effort (the run's frozen
``scan.user_config.resolve_agent_bundle`` interpretation), prompt text and every
input/output path (the run's artifact registry).  Executors only transport the result.
"""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from autoresearch.session_agent import artifacts
from autoresearch.session_agent.executors.base import (
    DEFAULT_TIMEOUTS,
    FALLBACK_TIMEOUT,
    ROLE_DISPATCH,
    DispatchRequest,
)
from autoresearch.session_agent.roles import get_role


def display_path(path: Path | str) -> str:
    """Repo-relative spelling when the path lives under the working tree (legacy wording)."""
    value = Path(path)
    if value.is_absolute():
        try:
            return value.relative_to(Path.cwd()).as_posix()
        except ValueError:
            return str(value)
    return value.as_posix()


def resolve_agent_spec(handle, config_role: str | None) -> tuple[dict, str | None, str]:
    """Return ``(spec, tier, resolution)`` for one scan_config agent role.

    Order = what the legacy workflows consume: the run contract's frozen
    ``resolved_agent_bundle`` (made by ``resolve_agent_bundle`` at begin), then the
    materialized ``_resolved_agent_config.json``, then a fresh ``resolve_agent_bundle``
    over the frozen config.  Nothing found → ``{}`` and an explicit ``UNRESOLVED`` label
    (the agent definition's frontmatter decides; the degradation is visible in the request).
    """
    if config_role is None:
        return {}, None, "AGENT_DEFINITION"
    config = getattr(getattr(handle, "contract", None), "user_config", None) or {}
    tier = ((config.get("agents") or {}).get(config_role) or {}).get("tier")
    frozen = (config.get("resolved_agent_bundle") or {}).get("roles") or config.get(
        "resolved_agents"
    )
    if isinstance(frozen, Mapping) and isinstance(frozen.get(config_role), Mapping):
        return dict(frozen[config_role]), tier, "FROZEN_RUN_CONTRACT"
    from autoresearch.scan import user_config

    materialized = user_config.load_resolved_agent_bundle(handle.staging).get("roles") or {}
    if isinstance(materialized.get(config_role), Mapping):
        return dict(materialized[config_role]), tier, "MATERIALIZED"
    try:
        resolved = user_config.resolve_agent_bundle(
            config, engine=handle.engine, require_all=False
        )["roles"]
    except (ValueError, KeyError, TypeError) as exc:
        return {}, tier, f"UNRESOLVED: {exc}"[:300]
    if isinstance(resolved.get(config_role), Mapping):
        return dict(resolved[config_role]), tier, "RESOLVED"
    return {}, tier, f"UNRESOLVED: role {config_role} absent from scan_config agents"


def _generic_prompt(task: dict, attempt: int, role: dict, inputs: dict, outputs: dict) -> str:
    refs = "、".join(role["instruction_refs"])
    read = "、".join(display_path(path) for path in inputs.values()) or "(无)"
    write = "、".join(display_path(path) for path in outputs.values())
    return (
        f"session 任务 {task['task_id']}(角色 {task['role']},attempt {attempt})。"
        f"按 {refs} 的人设与契约工作。输入:{read}。把产出写到 {write};只写这些输出文件。"
    )


def render_prompt(handle, task: dict, attempt: int, *, inputs: dict, outputs: dict) -> str:
    role = get_role(task["role"])
    return _generic_prompt(task, attempt, role, inputs, outputs)


def build_request(
    handle,
    task: dict,
    attempt: int,
    *,
    host_profile: dict,
    timeouts: Mapping[str, float] | None = None,
    timeout_multiplier: float = 1.0,
) -> DispatchRequest:
    role_id = task["role"]
    if role_id not in ROLE_DISPATCH:
        raise KeyError(f"no project agent is mapped for session role {role_id}")
    role = get_role(role_id)
    agent_type, config_role = ROLE_DISPATCH[role_id]
    spec, tier, resolution = resolve_agent_spec(handle, config_role)
    inputs = {
        artifact_id: str(artifacts.artifact_path(handle, artifact_id))
        for artifact_id in task["input_artifact_ids"]
    }
    outputs = {
        artifact_id: str(artifacts.artifact_path(handle, artifact_id))
        for artifact_id in task["output_artifact_ids"]
    }
    table = {**DEFAULT_TIMEOUTS, **dict(timeouts or {})}
    return DispatchRequest(
        run_id=handle.run_id,
        engine=handle.engine,
        task_id=task["task_id"],
        attempt=attempt,
        role=role_id,
        agent_type=agent_type,
        config_role=config_role,
        model=spec.get("model"),
        effort=spec.get("effort") or spec.get("reasoning_effort"),
        agent_spec=spec,
        tier=tier,
        max_turns=None,
        prompt=render_prompt(handle, task, attempt, inputs=inputs, outputs=outputs),
        instruction_refs=tuple(role["instruction_refs"]),
        input_paths=inputs,
        output_paths=outputs,
        subject=task.get("subject"),
        independent_context=bool(task["independent_context"]),
        tool_policy=role["tool_policy"],
        timeout_seconds=float(table.get(role_id, FALLBACK_TIMEOUT)) * float(timeout_multiplier),
        host_session_ref=host_profile["session_ref"],
        resolution=resolution,
    )


__all__ = ["build_request", "display_path", "render_prompt", "resolve_agent_spec"]
