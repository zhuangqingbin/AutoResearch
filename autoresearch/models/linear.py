#!/usr/bin/env python3
"""LinearComposite —— 行业条件化线性复合分(默认 champion)。

design: docs/specs/2026-06-22-autoresearch-arch-redesign-design.md §C④/⑤("linear 是默认 champion")。

`fit` 是 no-op(权重来自 factor_lab 校准的 weights.json,非从 Dataset 学);`predict(feats)` 直接
返回 `composite_score(feats, weights)["composite"]` —— 与 `scripts/screen_market`-era 的 L1 复合分
**逐值相等**(parity test 锁死)。它是"绝不部署比线性差的模型"里那条线性基线。
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from autoresearch.common.scoring import _load_weights, composite_score
from autoresearch.models.base import Dataset, FitReport, Model
from autoresearch.models.registry import register


@register("linear")
class LinearComposite(Model):
    """权重驱动的线性复合分模型(不训练;打分 = composite_score)。"""

    feature_set = "core"
    kind = "core"

    def __init__(self, weights_path: str = "context/factor_lab/weights.json"):
        self.weights_path = weights_path
        self.weights = _load_weights(weights_path)

    def fit(self, ds: Dataset) -> FitReport:
        """no-op:线性权重来自 weights.json(factor_lab 校准),不从 Dataset 学。"""
        n_dates = int(pd.Series(ds.dates).nunique()) if len(ds.dates) else 0
        return FitReport(n_rows=len(ds.X), n_dates=n_dates,
                         notes={"weights_path": self.weights_path, "fit": "no-op (weights.json)"})

    def predict(self, feats: pd.DataFrame) -> pd.Series:
        """= scripts/screen_market-era 的 L1 复合分(composite_score 的 composite 列)。"""
        return composite_score(feats, self.weights)["composite"]

    def save(self, path: str | Path) -> None:
        """存权重快照(自包含:load 不依赖 weights.json 仍在原处)。"""
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(
            json.dumps({"weights_path": self.weights_path, "weights": self.weights},
                       ensure_ascii=False),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: str | Path) -> LinearComposite:
        bundle = json.loads(Path(path).read_text(encoding="utf-8"))
        obj = cls.__new__(cls)
        obj.weights_path = bundle.get("weights_path", "context/factor_lab/weights.json")
        obj.weights = bundle["weights"]
        obj.feature_set = "core"
        obj.kind = "core"
        return obj
