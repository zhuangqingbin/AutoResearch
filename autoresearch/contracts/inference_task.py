#!/usr/bin/env python3
"""推理任务信封(工作包 E2)—— 纯形状校验,不导入上层。

实施计划:`docs/superpowers/plans/2026-09-06-orchestration-and-layering.md` Task E2。

信封是「确定性运行器停在推理边界、把活交给宿主会话」时那张交接单:它必须能唯一指认
**哪个引擎、哪个 run、哪个任务、第几次尝试、哪份输入契约**。少一项,迟到的回报就能冒充
当前的。九个字段全 required、多一个不认(与 ResearchCard 同一条律:字段缺席 ≠ 字段未知)。

`run_id` 的格式由上层 `workspace.validate_run_id` 管(contracts 不反向 import);`role` 与
`expected_output_contract` 是否登记由 `scan.deterministic_runner.verify_handoff` 对注册表查,
这里只校形状。**没有任何字段表示「会话模型 API」**——不变量 I12:普通 Python 进程不假设
自己能直接调用会话模型;信封只是交接单。
"""
from __future__ import annotations

import re

SCHEMA_VERSION = 1
FIELDS: frozenset[str] = frozenset({
    "schema_version", "engine", "run_id", "task_id", "role",
    "input_artifact_ids", "input_contract_hash", "expected_output_contract", "attempt",
})
_SHA256 = re.compile(r"[0-9a-f]{64}")


def validate_envelope(value: dict) -> dict:
    if not isinstance(value, dict) or set(value) != FIELDS:
        raise ValueError("missing or unknown inference envelope fields")
    if type(value["schema_version"]) is not int or value["schema_version"] != SCHEMA_VERSION:
        raise ValueError("invalid envelope version")
    if type(value["attempt"]) is not int or value["attempt"] < 1:
        raise ValueError("invalid attempt")
    if not isinstance(value["input_contract_hash"], str) \
            or not _SHA256.fullmatch(value["input_contract_hash"]):
        raise ValueError("invalid contract hash")
    ids = value["input_artifact_ids"]
    if not isinstance(ids, list) or not ids:
        raise ValueError("inputs required")
    if any(not isinstance(x, str) or not x for x in ids):
        raise ValueError("invalid artifact identity")
    for field in ("engine", "run_id", "task_id", "role", "expected_output_contract"):
        if not isinstance(value[field], str) or not value[field]:
            raise ValueError(f"{field} required")
    return value
