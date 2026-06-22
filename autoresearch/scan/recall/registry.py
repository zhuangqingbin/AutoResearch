#!/usr/bin/env python3
"""recall channel registry —— `@channel(name, quota, floor)` 注册 + `build(name)` 取函数。

design: docs/specs/2026-06-22-l1-multi-recall-design.md §架构(镜像 models/registry)。
加一路召回 = 写函数 + `@channel(...)`,不动 stage/merge。CHANNEL_DEFAULTS 存每路默认配额/保底。
"""
from __future__ import annotations

from dataclasses import dataclass

_REGISTRY: dict[str, object] = {}
_DEFAULTS: dict[str, ChannelSpec] = {}


@dataclass(frozen=True)
class ChannelSpec:
    """一路 channel 的默认元数据:配额 quota(取 top-k)+ 保底 floor(top-floor 无条件保留)+ 描述。"""

    name: str
    quota: int
    floor: int
    desc: str = ""


def channel(name: str, quota: int, floor: int, desc: str = ""):
    """函数装饰器:把一路 channel 函数登记进 registry(重名报错)+ 记默认配额/保底。"""

    def deco(fn):
        if name in _REGISTRY:
            raise KeyError(f"channel {name!r} already registered to {_REGISTRY[name]!r}")
        _REGISTRY[name] = fn
        _DEFAULTS[name] = ChannelSpec(name=name, quota=quota, floor=floor, desc=desc)
        return fn

    return deco


def build(name: str):
    """取一路 channel 函数(签名 fn(frame, date, k) -> DataFrame[code, channel_rank, channel_score])。"""
    try:
        return _REGISTRY[name]
    except KeyError:
        raise KeyError(f"unknown channel {name!r}: registered={sorted(_REGISTRY)}") from None


def registered_channels() -> list[str]:
    """已注册的全部 channel 名(导入 channels 模块后才齐)。"""
    return sorted(_REGISTRY)


CHANNEL_DEFAULTS = _DEFAULTS   # name -> ChannelSpec(随 @channel 注册增长;同一 dict 引用)
