"""autoresearch.models —— 统一、可插拔的粗排模型框架。

design: docs/specs/2026-06-22-autoresearch-arch-redesign-design.md §C。

导入本包即把 ported ranker(linear/lgbm/xgb/catboost/double_ensemble + 可选 torch 的 mlp/tabnet)
的 `@register` 副作用触发 → `registry.build(...)` 可直接按 kind 实例化。公共 API:Model/Dataset/
FitReport、ModelConfig/register/build、Trainer/TrainedModel + champion store、MODELS 目录。
"""
from __future__ import annotations

import contextlib

# ── 触发 @register 副作用:导入 ranker 模块即把 kind 登记进 registry ──
from autoresearch.models import (  # noqa: F401  (registration side-effects)
    cat,
    dbl,
    gbdt,
    linear,
    xgb,
)
from autoresearch.models.base import Dataset, FitReport, Model
from autoresearch.models.catalog import MODELS, by_status, ported
from autoresearch.models.registry import ModelConfig, build, register, registered_kinds
from autoresearch.models.trainer import (
    TrainedModel,
    Trainer,
    champion_ic,
    load_champion,
    save_champion,
)

# torch 表格 ranker(mlp/tabnet):**可选**——装了 torch 才注册;没装则跳过,树/线性 ranker
# 不受影响(torch 是 `[torch]` extra,不是核心依赖)。放在末尾保持上面 import 块连续可排序。
with contextlib.suppress(ImportError):
    from autoresearch.models import (  # noqa: F401  (optional registration side-effects)
        attn,
        mlp,
        rnn,
        tabnet,
        tcn,
    )

__all__ = [
    "Model", "Dataset", "FitReport",
    "ModelConfig", "register", "build", "registered_kinds",
    "Trainer", "TrainedModel", "save_champion", "load_champion", "champion_ic",
    "MODELS", "ported", "by_status",
]
