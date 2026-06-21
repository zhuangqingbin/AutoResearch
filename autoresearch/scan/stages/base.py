#!/usr/bin/env python3
"""Stage 契约 —— 扫描漏斗每一段的最小接口(读 typed trace 输入 → 写 typed 输出)。

design: docs/specs/2026-06-22-autoresearch-arch-redesign-design.md §A("Stage 契约")。

每段声明 `name` / `inputs()` / `outputs()`(它读/写哪些 trace 产物名),`run(ctx)` 从 trace 读
上游段产物 + 用 ctx.handler 取数 → 把本段结果写回 trace。**段间只经 trace 产物通信**,不传大
DataFrame;`outputs()` + manifest 让 pipeline 能断点续跑(产物在且 status=done 即跳过)。
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from autoresearch.scan.context import RunContext


class Stage(ABC):
    """一段确定性扫描阶段。子类设 `name`,声明 inputs/outputs,实现 run。"""

    name: str = "stage"

    def inputs(self) -> list[str]:
        """本段读哪些 trace 产物名(上游段输出);无上游依赖 → []。"""
        return []

    @abstractmethod
    def outputs(self) -> list[str]:
        """本段写哪些 trace 产物名(供 pipeline 续跑判定 + schema 对照)。"""
        raise NotImplementedError

    @abstractmethod
    def run(self, ctx: RunContext) -> None:
        """读 typed 输入(ctx.trace / ctx.handler)→ 写 typed 输出到 ctx.trace。不返回大帧。"""
        raise NotImplementedError
