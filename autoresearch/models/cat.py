#!/usr/bin/env python3
"""CatBoostRanker —— 原生 catboost 横截面排序器(同 gbdt_features)。

design: docs/specs/2026-06-22-autoresearch-arch-redesign-design.md §C④/⑥(Qlib zoo 原生迁移)。

与 GBDTRanker/XGBRanker 同一份特征矩阵(g_* + GBDT_RAW + composite 锚定),换 catboost 后端;
`fit` 吃 Dataset、`predict` → 横截面分(越高越看多)。catboost 原生支持 NaN。薄面板 → 浅树 +
强 l2_leaf_reg + 关日志。
"""
from __future__ import annotations

import pickle
from pathlib import Path

import pandas as pd

from autoresearch.models._gbdt_features import gbdt_features
from autoresearch.models.base import Dataset, FitReport, Model
from autoresearch.models.registry import register

# 薄面板强正则(对齐取向:浅 depth、强 l2、子采样、静默)。
_DEFAULT_PARAMS = {
    "iterations": 400, "learning_rate": 0.03, "depth": 5,
    "l2_leaf_reg": 10.0, "subsample": 0.8,
    "loss_function": "RMSE", "random_seed": 7, "allow_writing_files": False,
    "verbose": False,
}


@register("catboost")
class CatBoostRanker(Model):
    """catboost.CatBoostRegressor 横截面排序器(挑战 linear champion)。"""

    feature_set = "core"
    kind = "core"

    def __init__(self, params: dict | None = None):
        self.params = {**_DEFAULT_PARAMS, **(params or {})}
        self.model = None
        self.features: list[str] = []

    def fit(self, ds: Dataset) -> FitReport:
        from catboost import CatBoostRegressor

        X = gbdt_features(ds.X)
        self.features = list(X.columns)
        y = pd.to_numeric(ds.y, errors="coerce")
        self.model = CatBoostRegressor(**self.params)
        self.model.fit(X, y)
        n_dates = int(pd.Series(ds.dates).nunique()) if len(ds.dates) else 0
        return FitReport(n_rows=len(X), n_dates=n_dates,
                         notes={"n_features": len(self.features), "backend": "catboost"})

    def predict(self, feats: pd.DataFrame) -> pd.Series:
        if self.model is None:
            raise RuntimeError("CatBoostRanker.predict called before fit/load")
        X = gbdt_features(feats).reindex(columns=self.features)
        return pd.Series(self.model.predict(X), index=feats.index)

    def save(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(pickle.dumps(
            {"model": self.model, "features": self.features, "params": self.params}))

    @classmethod
    def load(cls, path: str | Path) -> CatBoostRanker:
        bundle = pickle.loads(Path(path).read_bytes())
        obj = cls(params=bundle.get("params"))
        obj.model = bundle["model"]
        obj.features = bundle["features"]
        return obj
