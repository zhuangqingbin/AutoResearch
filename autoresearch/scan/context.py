#!/usr/bin/env python3
"""RunContext —— 一次扫描运行的全部句柄(配置 + 数据层 + trace store + 日期）。

design: docs/specs/2026-06-22-autoresearch-arch-redesign-design.md §A("context.py")。

Stage.run(ctx) 只认这一个对象:从它拿 analysis_date / config 读取参数,拿 handler 取数(lake),
拿 trace 读上游段产物 + 写本段产物。run_id 是运行时刻(analysis_date 解耦,见 §D),`today` 给
取数层判"今天盘中是否未结算"(默认 = analysis_date)。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from autoresearch.data.handler import DataHandler
from autoresearch.scan.config import ScanConfig
from autoresearch.trace.store import TraceStore, new_run_id


@dataclass
class RunContext:
    """段间共享上下文:analysis_date · run_id · config · handler · trace · today。"""

    analysis_date: str
    config: ScanConfig = field(default_factory=ScanConfig)
    run_id: str = field(default_factory=new_run_id)
    handler: DataHandler = field(default_factory=DataHandler)
    trace: TraceStore = field(default_factory=TraceStore)
    today: str | None = None

    def __post_init__(self):
        # today 缺省 = analysis_date(取数层据此判该交易日是否已结算/可入湖)。
        if self.today is None:
            self.today = self.analysis_date
