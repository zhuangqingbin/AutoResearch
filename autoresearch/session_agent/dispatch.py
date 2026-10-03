"""Render one claimed inference attempt into a :class:`DispatchRequest` (runner side).

This is the single place where a session task becomes "what to launch": the project
agent (``executors.base.ROLE_DISPATCH``), model/effort (the run's frozen
``scan.user_config.resolve_agent_bundle`` interpretation), prompt text and every
input/output path (the run's artifact registry).  Executors only transport the result.
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path

from autoresearch.session_agent import artifacts
from autoresearch.session_agent.executors.base import (
    DEFAULT_TIMEOUTS,
    ROLE_DISPATCH,
    DispatchRequest,
    agent_type_for,
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
    from autoresearch.session_agent.config import orchestration_config

    config = orchestration_config(handle)
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
    section = role["instruction_section"]
    if task["role"] == "macro.research":
        groups = {"3_regional": "regional", "4_crossasset": "crossasset",
                  "5_sinous": "sinous", "2_meso": "meso", "6_meso_evidence": "meso", "1_spine": "spine"}
        selected = {name for group, name in groups.items()
                    if any(f".{group}." in artifact_id for artifact_id in outputs)}
        if any(artifact_id.endswith((".decision", ".sector_map")) for artifact_id in outputs):
            selected.add("allocation_contract")
        section = ",".join(["common", *sorted(selected)])
    return (
        f"session 任务 {task['task_id']}(角色 {task['role']},attempt {attempt})。"
        f"instruction_section={section};input_contract={role['input_policy']};"
        f"output_contract={task.get('expected_output_contract', role['output_contract'])}。"
        f"按 {refs} 的人设与契约工作。输入:{read}。把产出写到 {write};只写这些输出文件。"
    )


def _json_artifact(handle, artifact_id: str) -> dict:
    with artifacts.open_artifact(handle, artifact_id) as stream:
        value = json.loads(stream.read().decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {artifact_id}")
    return value


def sector_brief_web_searches(config: dict | None) -> int:
    """`sector.brief_web_searches`:行业 brief 写手可发的有界网查条数(0 = 零新取数;缺省 2,与 scan-market.js 同一份)。"""
    return int(((config or {}).get("sector") or {}).get("brief_web_searches", 2))


def _pass1_target(handle) -> int:
    """L3 prompt 文案里的「~N 表」= 冻结 config 的 `l3.pass1_target`(与 scan-market.js 同一份缺省 60)。"""
    config = getattr(getattr(handle, "contract", None), "user_config", None) or {}
    return int((config.get("l3") or {}).get("pass1_target", 60))


def l3_bounds_from_gate1(gate1: dict) -> tuple[int, int]:
    """``(l3lo, l3cap)`` from a GATE1 result, exactly as ``scan-market.js`` reads it.

    GATE1 echoes ``l3cap`` and ``l3min`` (``scan.l4.card_count.effective_caps``, the latter
    from ``l3.finalist_min``); a GATE1 frozen before those echoes existed falls back to
    ``min(10, l4_budget)`` and the built-in lower bound 7.  Never a literal 10.
    """
    l3cap = gate1.get("l3cap")
    if type(l3cap) is not int or l3cap < 1:
        budget = gate1.get("l4_budget")
        if type(budget) is not int or budget < 1:
            raise ValueError("frozen GATE1 result has neither l3cap nor l4_budget")
        l3cap = min(10, budget)
    l3min = gate1.get("l3min")
    if type(l3min) is not int or l3min < 1:
        l3min = 7
    return min(l3min, l3cap), l3cap


def l3_bounds(handle) -> tuple[int, int]:
    return l3_bounds_from_gate1(_json_artifact(handle, "scan.gate1.result"))


def _pick(paths: dict, suffix: str) -> str:
    matches = [path for artifact_id, path in paths.items() if artifact_id.endswith(suffix)]
    if len(matches) != 1:
        raise KeyError(f"expected one artifact ending with {suffix}")
    return display_path(matches[0])


def _only(paths: dict) -> str:
    if len(paths) != 1:
        raise KeyError("expected exactly one artifact")
    return display_path(next(iter(paths.values())))


def intel_prompt_cap(plan: dict | None) -> int:
    """intel prompt 的「≤N 条」:计划里冻结的 ``intel_max_queries``,缺 → 注册表缺省(单源)。"""
    from autoresearch.contracts.scan_config import DEFAULT_INTEL_MAX_QUERIES

    value = (plan or {}).get("intel_max_queries")
    return DEFAULT_INTEL_MAX_QUERIES if value is None else int(value)


def _intel_prompt(handle, code: str, inputs: dict, outputs: dict) -> str:
    plan = _json_artifact(handle, "scan.l4.plan")
    meta = (plan.get("meta") or {}).get(code) or {}
    name = meta.get("name") or ""
    sector = meta.get("sector") or "行业未知"
    max_queries = intel_prompt_cap(plan)
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
    from autoresearch.contracts.execution import validate_decision_frame

    if "stock.evidence_bundle" in inputs:
        from autoresearch.session_agent.evidence_bundle import validate_bundle

        validate_bundle(handle, task["input_artifact_ids"])
    domain_inputs = dict(inputs)
    prefix = ""
    if "research.frame" in domain_inputs:
        domain_inputs.pop("research.frame")
        frame = validate_decision_frame(_json_artifact(handle, "research.frame"))
        prefix = (
            "## 冻结决策时间窗（FULL/LITE 仅表示研究深度）\n"
            + json.dumps(frame, ensure_ascii=False, sort_keys=True)
            + "\n主尺 gap_c1_o2：D1 收盘入场、D2 开盘退出；收益分母是声明的入场价（ENTRY_PRICE），"
            "不是分析现价。未知入场价不报精确 EV；日历 UNKNOWN 不生成可执行承诺。"
            "仅使用 knowledge_cutoff 前可得证据；持仓保留真实成本，无法按窗退出须记偏离。\n\n"
        )
    if (task.get("role") in {"stock.card", "stock.pm", "scan.l4.card", "scan.l4.review"}
            and "research.frame" in inputs
            and task.get("expected_output_contract") in {"stock.lite.v1", "stock.pm.v1", "research.card.decision.v1"}):
        from autoresearch.contracts.profiles import CURRENT_CARD_RULES
        from autoresearch.trace.completeness import card_rules_from_capsule
        if (getattr(handle, "capsule", None) is not None
                and card_rules_from_capsule(handle.capsule) == CURRENT_CARD_RULES):
            from autoresearch.common.card_decision import decision_instruction
            subject = str(task.get("subject") or "")
            if not subject:
                subject = json.loads((Path(handle.workspace) / "session/request.json").read_text())["subject"]
            venue = frame["venue"]
            if task["role"].startswith("scan."):
                from autoresearch.dataflows.symbol_utils import normalize_symbol
                venue = {"SS": "XSHG", "SZ": "XSHE", "BJ": "XBSE"}[normalize_symbol(subject).rsplit(".", 1)[-1]]
            from autoresearch.trace.completeness import card_rating_bands_from_capsule
            prefix += decision_instruction(subject=subject, venue=venue)
            from autoresearch.news.card_claims import (
                bound_claim_context,
                claim_population,
                claim_usage_instruction,
            )
            claim_context = bound_claim_context(handle, task=task)
            population, _ = claim_population(frame=frame, capsule=claim_context["capsule"],
                identity=claim_context["identity"], accepted_attempts=claim_context["accepted_attempts"],
                subject=subject, task_subjects=claim_context["task_subjects"])
            prefix += claim_usage_instruction()
            prefix += "已知断言人口（不得删除未核项）：" + json.dumps([{key: row[key] for key in ("claim_id", "statement_sha256", "verdict")} for row in population.values()], ensure_ascii=False) + "\n"
            prefix += "冻结评分档位（不得读当前配置替换）：" + json.dumps(card_rating_bands_from_capsule(handle.capsule), ensure_ascii=False) + "\n"
    if task.get("expected_output_contract") in {"research.card.initial.v1", "research.card.decision.v1"}:
        from autoresearch.session_agent.card_facts import bound_manifest
        fact_id, manifest = bound_manifest(handle, task)
        binding = {"subject": task["subject"], "frame_hash": manifest["frame_hash"],
                   "fact_manifest_hash": artifacts.snapshot_artifact(handle, fact_id)["sha256"],
                   "evidence_refs": [row["evidence_id"] for row in manifest["sources"]]}
        initial = task["expected_output_contract"] == "research.card.initial.v1"
        if initial:
            instruction = (
                "two-stage-v1 initial · research.card.initial.v1。按角色文件的两阶段协议，"
                "只读取本次显式列出的事实输入，写专用初评JSON（不是完整卡）。"
                "不要打开内容里的外部路径、历史任务包或任何未列文件；不执行P0读先验。"
                "持仓事实要求先读声明deep；其他初评保留未核与缺口。")
        else:
            initial_id = next(key for key in inputs if key.endswith(".initial"))
            binding["initial_hash"] = artifacts.snapshot_artifact(handle, initial_id)["sha256"]
            instruction = (
                "two-stage-v1 decision · research.card.decision.v1。先读冻结初评和事实，"
                "再读本次声明的L3先验任务包与情报（如有），按原三门/渐进DD规则写最终MD卡，"
                "并写changes JSON。进入P4或持仓必须实际读取声明deep；初评不能替代本次Read。"
                "changes逐项精确比较最终卡的rating、六维dimensions与三门gates，改判说明可为纠正既有事实，"
                "不强求每次变化都有新来源。只引用声明的真实证据ID，禁止改写初评。"
                "旧任务包中的路径以本次显式输入为准，不扩展读取路径。")
        return (prefix + instruction + "\n绑定值：" + json.dumps(binding, ensure_ascii=False, sort_keys=True)
                + "\n输入：" + json.dumps(inputs, ensure_ascii=False, sort_keys=True)
                + "\n输出：" + json.dumps(outputs, ensure_ascii=False, sort_keys=True))
    if task.get("role") in {"scan.l4.card", "scan.l4.review"} and any(key.endswith(".deep") for key in inputs):
        import csv
        import io

        usage = "scan"
        if "scan.finalists" in inputs:
            with artifacts.open_artifact(handle, "scan.finalists") as stream:
                rows = csv.DictReader(io.StringIO(stream.read().decode("utf-8")))
                if any(row.get("code", "").zfill(6) == str(task.get("subject"))
                       and row.get("lane") == "pinned" for row in rows):
                    usage = "holding_review"
        depth_inputs = {key: value for key, value in inputs.items()
                        if key.endswith((".slim", ".deep", ".intel_status", ".intel_doc"))}
        prefix += (
            f"usage={usage} · depth=LITE。以下是本次 attempt 的授权证据路径，"
            "优先于历史任务包中的同类路径。普通候选早停不读 deep；进入 P4 或持仓复核必须实际读取 deep。"
            "仅绑定宿主 transcript 的成功完整 Read/read_file 观测证明已读，缺失记未核。\n"
            + json.dumps(depth_inputs, ensure_ascii=False, sort_keys=True) + "\n"
        )
    if getattr(handle, 'workspace', None) is not None and artifacts.layout_version(handle) >= 2:
        prefix += artifact_scope(get_role(task['role'])['tool_policy'], inputs, outputs,
                                 engine=getattr(handle, 'engine', 'claude'))
    return prefix + _render_domain_prompt(handle, task, attempt, inputs=domain_inputs, outputs=outputs)


def artifact_scope(tool_policy: str, inputs: dict, outputs: dict, *, engine: str = 'claude') -> str:
    """layout v2 派发前缀:能读什么、写什么、能用哪些工具。

    没有 READ 的盲搜角色(l4-intel 等)登记的输入只作溯源 —— 不把路径摆给它,也不叫它去读
    (A11,2026-10-03:10-02 首跑里 l4-intel 被叫去读卡任务包,没有 Read 工具,读不到也不该读)。
    """
    from autoresearch.session_agent.task_access import tool_allowance

    if 'READ' in str(tool_policy).split('_'):
        head = ('本次只读取以下 artifact 输入；任务包中的旧路径以此映射为准。'
                '只写本次列出的私有输出路径，任务包中的旧输出路径不可写。\n')
        body = {'inputs': inputs, 'outputs': outputs}
    else:
        head = '本任务不读任何文件(所需信息已写在下文,登记的输入只作溯源);只写本次列出的私有输出路径。\n'
        body = {'outputs': outputs}
    return (head + json.dumps(body, ensure_ascii=False, sort_keys=True) + '\n'
            + tool_allowance(tool_policy, engine) + '\n')


def _render_domain_prompt(handle, task: dict, attempt: int, *, inputs: dict, outputs: dict) -> str:
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
    if role.startswith("stock.") and role != "stock.card":
        path = Path(__file__).resolve().parents[2] / ".claude/agents/stock-full.md"
        sections = re.split(r"(?m)^## ", path.read_text(encoding="utf-8"))[1:]
        selected = {part.split("\n", 1)[0]: "## " + part for part in sections}
        if role not in selected or "common" not in selected:
            raise ValueError(f"missing stock FULL instruction section: {role}")
        return (
            f"session 任务 {task['task_id']} · full_role={role} · usage=standalone · depth=FULL · attempt={attempt}\n"
            + selected["common"] + "\n" + selected[role]
            + "\n## 声明的输入（逐项读取，禁止扩展路径）\n"
            + json.dumps(inputs, ensure_ascii=False, sort_keys=True)
            + "\n## 声明的输出\n" + json.dumps(outputs, ensure_ascii=False, sort_keys=True)
        )
    if role == "stock.card":
        return (
            "usage=standalone · depth=LITE；按 .claude/agents/l4-card.md 的渐进 DD 工作。"
            "先读 stock.slim；早停则不打开 stock.deep。进入 P4 必须实际读取声明的 stock.deep，"
            "并在卡中引用对应证据。只有宿主绑定 transcript 中成功的结构化 Read/read_file"
            "观测可证明已读；缺失或仅 shell 命令推断的证据记未核，不允许据此宣称深 DD 已完成。\n"
            + "输入：" + json.dumps(inputs, ensure_ascii=False, sort_keys=True)
            + "\n输出：" + json.dumps(outputs, ensure_ascii=False, sort_keys=True)
        )
    if role in {"company.intel", "us.intel"}:
        if set(inputs) != {"stock.context"}:
            raise KeyError(f"{role} requires the registered stock.context input")
        return (
            f"盲搜实体:{subject}；分析日 {handle.analysis_date}。按你的人设与既有查询预算采集事实。"
            f"只写 {_only(outputs)}；本任务不提供上游观点，不读取 stock.context。"
            "未给海外映射名单则该面写不适用，不自行扩展实体。"
        )
    if role == "sector.intel":
        if set(inputs) != {"sector.pack"}:
            raise KeyError("sector.intel requires exactly sector.pack")
        pack = _json_artifact(handle, "sector.pack")
        entities = [{key: row[key] for key in ("symbol", "kind") if key in row}
                    for row in pack.get("readthrough", []) if isinstance(row, dict)]
        return (
            f"行业盲搜:{subject}；分析日 {handle.analysis_date}。合法海外映射名单:"
            + json.dumps(entities, ensure_ascii=False, sort_keys=True)
            + f"。按角色既有预算四面查事实，只写 {_only(outputs)}；不读 pack 或研究观点。"
        )
    if role == "global.intel":
        if set(inputs) != {"macro.intel.request"}:
            raise KeyError("global.intel requires exactly macro.intel.request")
        request = _json_artifact(handle, "macro.intel.request")
        return (
            "全球盲搜只按以下冻结中性请求；as-of 不越 knowledge_cutoff，"
            "日历 UNAVAILABLE 不是无事件。Search 与 Fetch 合计不超 source_budget。\n"
            + json.dumps(request, ensure_ascii=False, sort_keys=True)
            + f"\n按 global-intel 机器契约只写 {_only(outputs)}；不得读取研究结论。"
        )
    if role == "macro.brief":
        return (
            f"读 {_only(inputs)} 的 pack 段,按你的人设写 {_only(outputs)}"
            "(六小节;前3描述性地形、后2仅 L5)。数字只出自该文件,不编;个股不评级、不锚定卡片。"
        )
    if role == "sector.brief":
        if task.get("expected_output_contract") == "sector.events.v1":
            request_ids = [key for key in inputs if key.endswith(".events.request")]
            if len(request_ids) != 1:
                raise KeyError("sector event task requires one frozen event request")
            request = _json_artifact(handle, request_ids[0])
            return (
                "只核实以下冻结请求的事件理由；不写Markdown、不修改pack数字。"
                "只输出sector.events.v1 JSON到 " + _only(outputs) + "。"
                "events每行字段reason_id,claim,source_url,published_at,available_at,"
                "source_observation_id,source_text_sha256,quote。顶层字段schema_version=1,"
                "pack_sha256,events,unresolved_reason_ids；每个reason必须有已绑定事实或未核记录。"
                "来源须落宿主原文/观测及B2断言证据；未形成绑定就列unresolved_reason_ids，"
                "不得自报PASS。不得添加行业方向，不能重算行情。查询数不超过max_queries。\n"
                + json.dumps(request, ensure_ascii=False, sort_keys=True)
            )
        if task["task_id"].startswith("scan."):
            sector_inputs = _only(inputs)
        else:
            if set(inputs) != {"sector.pack", "sector.reuse"}:
                raise KeyError("sector.brief requires exactly sector.pack and sector.reuse")
            sector_inputs = (f"{display_path(inputs['sector.pack'])} 与 "
                             f"{display_path(inputs['sector.reuse'])}(复用状态与已核地形)")
        return (
            f"你是行业分析师。读 {sector_inputs} 写 {_only(outputs)},"
            "单段机器契约(## 地形段 喂 L3/L4;纯事实性,不含方向判断)。"
            f"可发 ≤{sector_brief_web_searches(getattr(getattr(handle, 'contract', None), 'user_config', None) or {})} 条有界 WebSearch(0 = 零新取数)。"
        )
    if role == "scan.l3":
        l3lo, l3cap = l3_bounds(handle)
        compatibility = (
            "历史冻结任务 scan.l3.v1：输出原 v1 数组，不添加 schema_version 或 veto_reasons；"
            "角色文档的 v2 输出字段仅适用于新任务。\n"
        ) if task.get("expected_output_contract") == "scan.l3.v1" else ""
        if getattr(handle, 'workspace', None) is not None and artifacts.layout_version(handle) >= 2:
            return compatibility + (
                f'L3 精排 · 日期 {handle.analysis_date} · finalist tier 按质 {l3lo}~{l3cap} 只'
                '(judged 每元素带 finalist:true/false)+其余为 bench;宁缺毋滥。'
                f'按声明输入逐项读取 pass1(~{_pass1_target(handle)} 表,pass1 已分诊)、'
                '市场(§1-3 地形)与行业地形。按你的人设(6 维 rubric + 硬约束 A-E)比较式精排,'
                f'写 {_only(outputs)}。'
            )
        return compatibility + (
            f"L3 精排 · 日期 {handle.analysis_date} · finalist tier 按质 {l3lo}~{l3cap} 只"
            "(judged 每元素带 finalist:true/false)+其余为 bench;宁缺毋滥。"
            f"文件在 {sd}/:_l3_table.md(~{_pass1_target(handle)} 表,pass1 已分诊)、market_view.md(§1-3 地形)、"
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
        from autoresearch.common.research_prompts import render_research_prompt
        return render_research_prompt(role, prompt=_pick(inputs, '.prompt'), output=_only(outputs))
    if role == "scan.l4.review":
        run_index = 3 if task["task_id"].endswith(".review3") else 2
        from autoresearch.common.research_prompts import render_research_prompt
        return render_research_prompt(role, prompt=_pick(inputs, '.prompt'), output=_only(outputs), run_index=run_index)
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
    frozen_path = Path(handle.workspace) / 'session/dispatch' / f"{task['task_id']}-a{attempt}.json"
    if artifacts.layout_version(handle) >= 2 and frozen_path.is_file():
        return DispatchRequest.from_json(json.loads(frozen_path.read_text()))
    role_id = task["role"]
    if role_id not in ROLE_DISPATCH:
        raise KeyError(f"no project agent is mapped for session role {role_id}")
    role = get_role(role_id)
    _, config_role = ROLE_DISPATCH[role_id]
    agent_type = agent_type_for(role_id, handle.engine)     # M4: the host engine's agent
    spec, tier, resolution = resolve_agent_spec(handle, config_role)
    inputs = {
        artifact_id: str(artifacts.artifact_path(handle, artifact_id))
        for artifact_id in task["input_artifact_ids"]
    }
    outputs = artifacts.output_paths(handle, task, attempt)
    for path in outputs.values():
        # Attempt-scoped outputs (session_attempts/<code>/a<n>/…) live in sub-directories
        # the agent should not have to create before its first write.
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    from autoresearch.session_agent.config import orchestration_config, session_cfg

    _sc = session_cfg(orchestration_config(handle))["timeouts"]
    table = {**DEFAULT_TIMEOUTS, **_sc["mailbox"], **dict(timeouts or {})}
    instruction_refs = tuple(role["instruction_refs"])
    if artifacts.layout_version(handle) >= 2:
        from autoresearch.session_agent.task_access import freeze_instructions
        instruction_refs = freeze_instructions(role_id, instruction_refs,
            Path(handle.workspace) / 'session/instructions' / f"{task['task_id']}-a{attempt}", outputs)
    prompt = render_prompt(handle, task, attempt, inputs=inputs, outputs=outputs)
    host_prompt = None
    if artifacts.layout_version(handle) >= 2:
        prompt += ("\nC4 输入边界：契约仅从冻结 instruction_refs 读取；文中的旧路径与链接不授权扩展。"
                   "使用宿主结构化文件工具或根会话绑定后给出的规范 task_file_broker 命令；禁止通用 shell。"
                   "本阶段显式登记的 deep 已由任务图授权，按原渐进DD规则条件读取；未登记文件不可追加。\n"
                   + json.dumps({'instruction_refs': instruction_refs}, ensure_ascii=False))
        # B8(2026-10-03):按引用派发。全文冻结成文件并进授权读取;宿主只传一行指针,省掉主会话
        # 逐字复述(10-02 首跑 23.3 万字符)。没有 READ 的盲搜角色读不了文件,照旧传全文。
        by_reference = session_cfg(orchestration_config(handle))["mailbox"]["by_reference"]
        if by_reference and "READ" in role["tool_policy"].split("_"):
            prompt_file = frozen_path.with_name(f"{task['task_id']}-a{attempt}.prompt.md")
            prompt_file.parent.mkdir(parents=True, exist_ok=True)
            prompt_file.write_text(prompt, encoding="utf-8")
            instruction_refs = (*instruction_refs, str(prompt_file))
            host_prompt = (f"执行冻结任务 {task['task_id']}(attempt {attempt})。第一步:用 Read 完整读取 "
                           f"{prompt_file},它就是本任务的全部指令;严格照做,不读其中未列出的文件。")
    request = DispatchRequest(
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
        prompt=prompt,
        instruction_refs=instruction_refs,
        input_paths=inputs,
        output_paths=outputs,
        subject=task.get("subject"),
        independent_context=bool(task["independent_context"]),
        tool_policy=role["tool_policy"],
        timeout_seconds=float(table.get(role_id, _sc["fallback_s"])) * float(timeout_multiplier),
        host_session_ref=host_profile["session_ref"],
        resolution=resolution,
        access_manifest_path=str(frozen_path.with_suffix('.access.json')) if artifacts.layout_version(handle) >= 2 else None,
        host_prompt=host_prompt,
    )

    if artifacts.layout_version(handle) >= 2:
        from autoresearch.session_agent.executors.mailbox import abandoned_path
        from autoresearch.session_agent.task_access import freeze_access
        freeze_access(request, frozen_path, abandonment_path=abandoned_path(handle.staging, task['task_id'], attempt))
    return request


__all__ = ["build_request", "display_path", "l3_bounds", "l3_bounds_from_gate1", "render_prompt", "resolve_agent_spec"]
