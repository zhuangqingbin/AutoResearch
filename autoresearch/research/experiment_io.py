#!/usr/bin/env python3
"""F1:实验方案的**排他**冻结 —— 写下去就不许改。

实施计划:`docs/superpowers/plans/2026-09-06-research-methods-and-stage-value.md` Task F1。

`freeze_spec` 用 `open("xb")`:目标已存在直接 `FileExistsError`,**没有 `--force`**。想改
假设就换一个 `experiment_id` 开新实验 —— 覆盖旧 spec 等于把「先写后看」这个动作抹掉,而它
是整套离线研究里唯一挡得住事后合理化的东西。

失败/中断的实验目录**保留**,不自动清理:一个半截目录是「这里试过、没跑完」的记录;删掉它,
下次同一个人会以为自己是第一次试。
"""
from __future__ import annotations

import json
from pathlib import Path

from autoresearch.common.atomic import canonical_json, sha256_bytes
from autoresearch.contracts.research_experiment import validate_spec

SPEC_NAME = "spec.json"


def create_experiment_dir(parent: Path | str, experiment_id: str) -> Path:
    """排他创建 `<parent>/<experiment_id>/`。目录已存在 → `FileExistsError`。"""
    import re

    if not re.fullmatch("[A-Za-z0-9_-]{1,80}", str(experiment_id)):
        raise ValueError("invalid experiment id")
    path = Path(parent) / str(experiment_id)
    path.mkdir(parents=True, exist_ok=False)
    return path


def freeze_spec(directory: Path | str, spec: dict) -> tuple[Path, str]:
    """把方案冻进 `<directory>/spec.json` → `(路径, sha256)`。已存在 → `FileExistsError`。

    落盘用规范化 JSON(排序键、紧凑分隔符),所以同一份方案的 hash 与字典书写顺序无关 ——
    否则「同一个方案」会因为键序不同拿到两个 hash,输入身份就失去了意义。
    """
    path = Path(directory) / SPEC_NAME
    payload = canonical_json(spec).encode("utf-8")
    with path.open("xb") as stream:
        stream.write(payload)
        stream.flush()
    return path, sha256_bytes(payload)


def freeze_validated_spec(directory: Path | str, spec: dict) -> tuple[Path, str]:
    """先过 `validate_spec` 再冻结 —— 生产入口用这个,别绕过校验直接冻一份坏方案。"""
    return freeze_spec(directory, validate_spec(spec))


def read_spec(directory: Path | str) -> dict:
    """读回已冻结的方案(不校验:历史 spec 可能是更早的 schema,读得出来才谈得上迁移)。"""
    return json.loads((Path(directory) / SPEC_NAME).read_text(encoding="utf-8"))


def spec_digest(directory: Path | str) -> str:
    """已冻结方案的 sha256 —— 按**文件原字节**算,不重新序列化。"""
    return sha256_bytes((Path(directory) / SPEC_NAME).read_bytes())
