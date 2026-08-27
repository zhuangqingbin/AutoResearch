"""来源 adapter 注册表 —— `parse(path, source_kind)` 分发到各家解析器,产出 `schema.RAW_COLUMNS` 字符串帧。

design: docs/specs/2026-08-27-broker-trades-ingest-design.md §9

只有探针证实过格式的来源才有 adapter;没有的 → ValueError 明说(不静默跳过、不猜格式)。
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pandas as pd

from autoresearch.broker.schema import SOURCE_KINDS

REGISTRY: dict[str, Callable[..., pd.DataFrame]] = {}


def detect_source_kind(path: Path) -> str | None:
    """按父目录名识别来源(inbox/<src>/文件);不在 `SOURCE_KINDS` → None。"""
    name = Path(path).parent.name
    return name if name in SOURCE_KINDS else None


def parse(path: Path, source_kind: str, *, account: str | None = None) -> pd.DataFrame:
    fn = REGISTRY.get(source_kind)
    if fn is None:
        raise ValueError(f"source_kind={source_kind!r} 尚无 adapter(已有 {sorted(REGISTRY)};"
                         "chinaclear/gtht/tpy 等探针证实格式后再建)")
    return fn(Path(path), account=account)
