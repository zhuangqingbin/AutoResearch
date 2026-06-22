"""autoresearch.scan.recall —— L1 多路策略召回(channel registry + quota union merge)。

design: docs/specs/2026-06-22-l1-multi-recall-design.md。
导入本包即触发 channels 的 @channel 注册副作用(Task 2 起);公共 API 见 __all__。
"""
from __future__ import annotations

from autoresearch.scan.recall.base import gate_rank
from autoresearch.scan.recall.registry import (
    CHANNEL_DEFAULTS,
    ChannelSpec,
    build,
    channel,
    registered_channels,
)
from autoresearch.scan.recall import channels  # noqa: F401  (registration side-effects)

__all__ = ["gate_rank", "channel", "build", "registered_channels",
           "CHANNEL_DEFAULTS", "ChannelSpec"]
