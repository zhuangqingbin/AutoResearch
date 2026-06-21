#!/usr/bin/env python3
"""XGBRanker —— 原生 xgboost(xgb.train / DMatrix / Booster)横截面排序器(同 gbdt_features)。

design: docs/specs/2026-06-22-autoresearch-arch-redesign-design.md §C④/⑥(Qlib zoo 原生迁移)。

用 xgboost **原生** train API(非 sklearn 的 XGBRegressor —— 本环境无 scikit-learn,且原生路与
lgbm 的 lgb.train 对称、即 Qlib XGBModel 同路)。同一份特征矩阵(g_* + GBDT_RAW + composite 锚定)、
同薄面板强正则取向(浅树 + 强 L2 + 列/行采样);`fit` 吃 Dataset、`predict` → 横截面分(越高越看多)。
xgboost 原生支持 NaN(missing)。
"""
from __future__ import annotations

import pickle
from pathlib import Path

import numpy as np
import pandas as pd

from autoresearch.models._gbdt_features import gbdt_features
from autoresearch.models.base import Dataset, FitReport, Model
from autoresearch.models.registry import register

# 薄面板强正则(对齐 lgbm 取向:浅树 max_depth=5、强 lambda、子采样)。原生 train 的 booster 参数。
_DEFAULT_PARAMS = {
    "objective": "reg:squarederror", "eta": 0.03, "max_depth": 5,
    "subsample": 0.8, "colsample_bytree": 0.8,
    "lambda": 10.0, "alpha": 1.0, "seed": 7, "nthread": 0, "verbosity": 0,
}


@register("xgb")
class XGBRanker(Model):
    """xgboost 原生 Booster 横截面排序器(挑战 linear champion)。"""

    feature_set = "core"
    kind = "core"

    def __init__(self, params: dict | None = None, num_boost_round: int = 400):
        self.params = {**_DEFAULT_PARAMS, **(params or {})}
        self.num_boost_round = num_boost_round
        self.model = None
        self.features: list[str] = []

    def fit(self, ds: Dataset) -> FitReport:
        import xgboost as xgb

        X = gbdt_features(ds.X)
        self.features = list(X.columns)
        y = pd.to_numeric(ds.y, errors="coerce").to_numpy(dtype=float)
        dtrain = xgb.DMatrix(X.to_numpy(dtype=float), label=y,
                             feature_names=self.features, missing=np.nan)
        self.model = xgb.train(self.params, dtrain, num_boost_round=self.num_boost_round)
        n_dates = int(pd.Series(ds.dates).nunique()) if len(ds.dates) else 0
        return FitReport(n_rows=len(X), n_dates=n_dates,
                         notes={"n_features": len(self.features), "backend": "xgboost(native)"})

    def predict(self, feats: pd.DataFrame) -> pd.Series:
        import xgboost as xgb

        if self.model is None:
            raise RuntimeError("XGBRanker.predict called before fit/load")
        X = gbdt_features(feats).reindex(columns=self.features)
        dmat = xgb.DMatrix(X.to_numpy(dtype=float), feature_names=self.features, missing=np.nan)
        return pd.Series(self.model.predict(dmat), index=feats.index)

    def save(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(pickle.dumps(
            {"model": self.model, "features": self.features, "params": self.params,
             "num_boost_round": self.num_boost_round}))

    @classmethod
    def load(cls, path: str | Path) -> XGBRanker:
        bundle = pickle.loads(Path(path).read_bytes())
        obj = cls(params=bundle.get("params"), num_boost_round=bundle.get("num_boost_round", 400))
        obj.model = bundle["model"]
        obj.features = bundle["features"]
        return obj
