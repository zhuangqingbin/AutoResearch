"""Render one claimed inference attempt into a :class:`DispatchRequest` (runner side).

This is the single place where a session task becomes "what to launch": the project
agent (``executors.base.ROLE_DISPATCH``), model/effort (the run's frozen
``scan.user_config.resolve_agent_bundle`` interpretation), prompt text and every
input/output path (the run's artifact registry).  Executors only transport the result.
"""
from __future__ import annotations

import json
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


def _json_artifact(handle, artifact_id: str) -> dict:
    with artifacts.open_artifact(handle, artifact_id) as stream:
        value = json.loads(stream.read().decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {artifact_id}")
    return value


def l3_bounds(handle) -> tuple[int, int]:
    """``(l3lo, l3cap)`` from the frozen GATE1 result, exactly as ``scan-market.js`` does.

    GATE1 echoes ``l3cap`` (``scan.l4.card_count.effective_caps``); a GATE1 frozen before
    that echo existed falls back to ``min(10, l4_budget)``.  Never a literal 10.
    """
    gate1 = _json_artifact(handle, "scan.gate1.result")
    l3cap = gate1.get("l3cap")
    if type(l3cap) is not int or l3cap < 1:
        budget = gate1.get("l4_budget")
        if type(budget) is not int or budget < 1:
            raise ValueError("frozen GATE1 result has neither l3cap nor l4_budget")
        l3cap = min(10, budget)
    return min(7, l3cap), l3cap


def _pick(paths: dict, suffix: str) -> str:
    matches = [path for artifact_id, path in paths.items() if artifact_id.endswith(suffix)]
    if len(matches) != 1:
        raise KeyError(f"expected one artifact ending with {suffix}")
    return display_path(matches[0])


def _only(paths: dict) -> str:
    if len(paths) != 1:
        raise KeyError("expected exactly one artifact")
    return display_path(next(iter(paths.values())))


def _intel_prompt(handle, code: str, inputs: dict, outputs: dict) -> str:
    plan = _json_artifact(handle, "scan.l4.plan")
    meta = (plan.get("meta") or {}).get(code) or {}
    name = meta.get("name") or ""
    sector = meta.get("sector") or "行业未知"
    max_queries = plan.get("intel_max_queries")
    max_queries = 15 if max_queries is None else max_queries
    dossier = str(meta.get("dossier_summary") or "").strip()
    known_base = (
        f"\n\n## 已知底(覆盖档案摘要·仅用于去重,**不是**查询方向指令)\n{dossier}\n\n"
        "已在上面出现的事实不必复查,查询额度全花在增量与新事件上。"
    ) if dossier else ""
    return (
        f"活体情报采集:{code} {name}({sector})· 分析日 {handle.analysis_date}。"
        f"按你的人设六面全查(≤{max_queries} 条),写 {_only(outputs)};"
        f"返回 code 与事件行数 events。{known_base}"
    )


def render_prompt(handle, task: dict, attempt: int, *, inputs: dict, outputs: dict) -> str:
    """Dispatch prompt; scan roles use the legacy workflow wording verbatim.

    Sources (2026-09-26): ``scan-market.js`` 377 (macro.brief), 485 (sector.brief),
    494 (scan.l3), 521 (scan.l3.repair); ``l4-stock.js`` 373 (scan.l4.intel), 469
    (scan.l4.card), 503 (scan.l4.review).  Paths come from the run's artifact registry,
    so an attempt ≥2 names its own retry files; ``tests/session_agent/test_mailbox.py``
    re-reads the JS lines and fails when the wording drifts.
    """
    role = task["role"]
    subject = str(task.get("subject") or "")
    sd = display_path(handle.staging)
    if role == "macro.brief":
        return (
            f"读 {_only(inputs)} 的 pack 段,按你的人设写 {_only(outputs)}"
            "(六小节;前3描述性地形、后2仅 L5)。数字只出自该文件,不编;个股不评级、不锚定卡片。"
        )
    if role == "sector.brief":
        return (
            f"你是行业分析师。读 {_only(inputs)} 写 {_only(outputs)},"
            "单段机器契约(## 地形段 喂 L3/L4;纯事实性,不含方向判断)。零新取数。"
        )
    if role == "scan.l3":
        l3lo, l3cap = l3_bounds(handle)
        return (
            f"L3 精排 · 日期 {handle.analysis_date} · finalist tier 按质 {l3lo}~{l3cap} 只"
            "(judged 每元素带 finalist:true/false)+其余为 bench;宁缺毋滥。"
            f"文件在 {sd}/:_l3_table.md(~40 表,pass1 已分诊)、market_view.md(§1-3 地形)、"
            "sector_briefs/(地形段)。按你的人设(6 维 rubric + 硬约束 A-E)比较式精排,"
            f"写 {_only(outputs)}。"
        )
    if role == "scan.l3.repair":
        return (
            f"Read {_only(inputs)}，只处理其中列出的失败票；按文件内 schema 用 Write 写 "
            f"{_only(outputs)}。不要读取任何全量 L3 输入或输出文件。"
        )
    if role == "scan.l4.intel":
        return _intel_prompt(handle, subject, inputs, outputs)
    if role == "scan.l4.card":
        return (
            f"执行 {_pick(inputs, '.prompt')}:先读整个任务包,再按其指令做渐进深度 DD + 早停,"
            f"写决策卡到 {_only(outputs)}。最后返回该卡最终五档评级与 FINAL 行"
            '(code / rating / conviction / proposal=FINAL TRANSACTION PROPOSAL 的值,如 "SELL")。'
        )
    if role == "scan.l4.review":
        run_index = 3 if task["task_id"].endswith(".review3") else 2
        return (
            f"独立复核 run{run_index}(不知道其它 run 结论):执行 {_pick(inputs, '.prompt')} 的任务包,"
            f"按人设走渐进深度 DD,决策卡写到 {_only(outputs)}(先自行创建 ensemble/ 目录),"
            "返回 code/rating/conviction/proposal。"
        )
    return _generic_prompt(task, attempt, get_role(role), inputs, outputs)


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


__all__ = ["build_request", "display_path", "l3_bounds", "render_prompt", "resolve_agent_spec"]
