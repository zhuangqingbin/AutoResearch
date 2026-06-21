#!/usr/bin/env python3
"""模型 registry —— `@register(key)` 注册 + `build(cfg)` 按 config 实例化。

design: docs/specs/2026-06-22-autoresearch-arch-redesign-design.md §C②。

换模型 = 改 config 的 `kind`(不动数据层 / Stage)。每个 Model 子类用 `@register("lgbm")`
注册到 `_REGISTRY`;`build(ModelConfig(kind="lgbm", params={...}))` → `_REGISTRY["lgbm"](**params)`,
并把 `feature_set` 写到实例上(Trainer 据此向 DataHandler 要哪份命名视图)。未知 kind → KeyError。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from autoresearch.models.base import Model

_REGISTRY: dict[str, type] = {}


def register(key: str):
    """类装饰器:把一个 Model 子类登记到 _REGISTRY[key](key 重复 → 报错,防覆盖)。"""

    def deco(cls: type) -> type:
        if key in _REGISTRY:
            raise KeyError(f"model kind {key!r} already registered to {_REGISTRY[key]!r}")
        _REGISTRY[key] = cls
        return cls

    return deco


@dataclass
class ModelConfig:
    """模型配置:kind(registry key)+ params(构造参数)+ feature_set(命名视图)。"""

    kind: str
    params: dict = field(default_factory=dict)
    feature_set: str = "core"


def build(cfg: ModelConfig) -> Model:
    """按 config 实例化模型:`_REGISTRY[cfg.kind](**cfg.params)`,并把 feature_set 写上。"""
    try:
        cls = _REGISTRY[cfg.kind]
    except KeyError:
        raise KeyError(
            f"unknown model kind {cfg.kind!r}: registered = {sorted(_REGISTRY)}"
        ) from None
    model = cls(**cfg.params)
    model.feature_set = cfg.feature_set
    return model


def registered_kinds() -> list[str]:
    """已注册的全部 kind(供 catalog/诊断对照)。"""
    return sorted(_REGISTRY)
