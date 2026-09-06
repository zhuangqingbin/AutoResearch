#!/usr/bin/env python3
"""确定性运行器的**交接校验**(工作包 E2)。

Q-E 裁定(2026-09-07):runner 本身(E3 的 `execute_step` / 任务领取 / 恢复)**不立项**——
09-04 已裁扫描留 workflow.js 做减法。这里只有 E2 的 `verify_handoff`:宿主完成一段推理回来时,
校验它回的是**这个**引擎 / run / 任务 / 尝试 / 输入契约。它只做校验,不代替 `l4_tasks`
锁内的终态检查,更不派发任何东西。

`input_hash` 从既有 `stage_result.contract_hash_for(scan_dir)` 取,不自行 hash 某个字符串。
`available_artifacts` 必须是已验证路径 / hash / 所属引擎的产物 id 集合;`registered_contracts`
是 `(role, expected_output_contract)` 的注册表——宿主不许自造角色。
"""
from __future__ import annotations

from autoresearch.common import workspace as ws
from autoresearch.contracts.inference_task import validate_envelope


def verify_handoff(envelope: dict, *, engine: str, run_id: str, task: dict, input_hash: str,
                   available_artifacts, registered_contracts) -> dict:
    validate_envelope(envelope)
    ws.validate_run_id(envelope["run_id"])
    expected = (engine, run_id, task["code"], task["attempt"], input_hash)
    actual = tuple(envelope[k] for k in (
        "engine", "run_id", "task_id", "attempt", "input_contract_hash"))
    if actual != expected or task.get("status") != "RUNNING":
        raise ValueError("stale or foreign handoff")
    if not set(envelope["input_artifact_ids"]) <= set(available_artifacts):
        raise ValueError("missing registered input")
    key = (envelope["role"], envelope["expected_output_contract"])
    if key not in set(registered_contracts):
        raise ValueError("unknown role/output contract")
    return envelope
