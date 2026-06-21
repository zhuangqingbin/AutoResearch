#!/usr/bin/env python3
"""GBDTRanker —— LightGBM 横截面排序模型(PORT 自 factor_lab.train_gbdt)。

design: docs/specs/2026-06-22-autoresearch-arch-redesign-design.md §C④。

把 factor_lab 的 GBDT 搬上 Model 接口:**同特征构造**(gbdt_features:g_* + GBDT_RAW + composite
锚定)、**同 lightgbm 原生 lgb.train**、**同强正则超参**(浅树 + 大叶 + 强 L2,压薄面板过拟合)。
区别仅在于 `fit` 吃 Dataset(Trainer 已物化 + rank-norm 标签),不再自己 build 面板 / 取数。
`predict(feats)` 对 core 面板提 gbdt_features → 打分(越高越看多)。
"""
from __future__ import annotations

import pickle
from pathlib import Path

import pandas as pd

from autoresearch.models._gbdt_features import gbdt_features
from autoresearch.models.base import Dataset, FitReport, Model
from autoresearch.models.registry import register

# 与 factor_lab.train_gbdt 同口径的薄面板强正则超参(浅树 + 大叶 + 强 L2/L1)。
_DEFAULT_PARAMS = {
    "objective": "regression", "metric": "l2", "learning_rate": 0.03,
    "num_leaves": 15, "max_depth": 5, "min_data_in_leaf": 200,
    "bagging_fraction": 0.8, "bagging_freq": 1, "feature_fraction": 0.8,
    "lambda_l2": 10.0, "lambda_l1": 1.0, "seed": 7, "num_threads": 0, "verbosity": -1,
}


@register("lgbm")
class GBDTRanker(Model):
    """lightgbm 原生 lgb.train 横截面排序器(挑战 linear champion)。"""

    feature_set = "core"
    kind = "core"

    def __init__(self, params: dict | None = None, num_boost_round: int = 800,
                 early_stopping: int = 60, valid_fraction: float = 0.2):
        self.params = {**_DEFAULT_PARAMS, **(params or {})}
        self.num_boost_round = num_boost_round
        self.early_stopping = early_stopping
        self.valid_fraction = valid_fraction
        self.model = None
        self.features: list[str] = []
        self.best_iter: int | None = None

    def fit(self, ds: Dataset) -> FitReport:
        """对 Dataset(core 面板 X + rank-norm 标签 y)拟合 lightgbm。

        在 ds 内部再切末尾若干日做早停 valid(无前视:按 ds.dates 末尾的日);ds 已是 Trainer
        的 train 段,这里的 valid 仅供早停,不参与 Trainer 的 oos 评估。
        """
        import lightgbm as lgb

        X = gbdt_features(ds.X)
        self.features = list(X.columns)
        y = pd.to_numeric(ds.y, errors="coerce").to_numpy()
        dates = pd.Series(ds.dates).to_numpy()
        udates = sorted(pd.unique(dates))
        n_val = max(1, min(int(len(udates) * self.valid_fraction), max(1, len(udates) - 1)))
        val = set(udates[-n_val:]) if len(udates) > 1 else set()
        is_val = pd.Series(dates).isin(val).to_numpy()
        if not is_val.any() or is_val.all():   # 单日 / 退化 → 不留早停 valid,跑满轮
            dtrain = lgb.Dataset(X, label=y)
            self.model = lgb.train(self.params, dtrain, num_boost_round=self.num_boost_round)
            self.best_iter = int(self.model.best_iteration or self.num_boost_round)
        else:
            dtrain = lgb.Dataset(X[~is_val], label=y[~is_val])
            dvalid = lgb.Dataset(X[is_val], label=y[is_val], reference=dtrain)
            self.model = lgb.train(
                self.params, dtrain, num_boost_round=self.num_boost_round,
                valid_sets=[dvalid],
                callbacks=[lgb.early_stopping(self.early_stopping, verbose=False)],
            )
            self.best_iter = int(self.model.best_iteration or self.num_boost_round)
        n_dates = len(udates)
        return FitReport(n_rows=len(X), n_dates=n_dates, best_iter=self.best_iter,
                         notes={"n_features": len(self.features)})

    def predict(self, feats: pd.DataFrame) -> pd.Series:
        if self.model is None:
            raise RuntimeError("GBDTRanker.predict called before fit/load")
        X = gbdt_features(feats).reindex(columns=self.features)
        return pd.Series(self.model.predict(X, num_iteration=self.best_iter), index=feats.index)

    def save(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(pickle.dumps(
            {"model": self.model, "features": self.features, "best_iter": self.best_iter,
             "params": self.params}))

    @classmethod
    def load(cls, path: str | Path) -> GBDTRanker:
        bundle = pickle.loads(Path(path).read_bytes())
        obj = cls(params=bundle.get("params"))
        obj.model = bundle["model"]
        obj.features = bundle["features"]
        obj.best_iter = bundle.get("best_iter")
        return obj
