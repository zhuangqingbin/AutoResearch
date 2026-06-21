#!/usr/bin/env python3
"""DoubleEnsembleRanker —— 原生 DoubleEnsemble(lightgbm 子模型 bagging + 样本重加权)。

design: docs/specs/2026-06-22-autoresearch-arch-redesign-design.md §C④/⑥;参考 Qlib DEnsembleModel。

DoubleEnsemble 的核心两件事(这里保持简单但真实):
  1. **样本重加权(SR, sample reweighting)**:每加一个子模型后,按当前集成在每个样本上的
     "h-value"(损失曲线特征:近期损失高且不稳定的样本)抬高其权重,让后续子模型更关注难样本。
  2. **特征选择(FS)**:Qlib 还做基于扰动的特征 bagging;此处简化为每个子模型对特征做随机
     子采样(feature_fraction),达到同样的"特征扰动 + 去相关"效果。

每个子模型 = 一棵 lightgbm(同 GBDTRanker 的强正则取向);最终分 = 全部子模型预测均值。
特征矩阵复用 gbdt_features(与其它树模型同口径)。
"""
from __future__ import annotations

import pickle
from pathlib import Path

import numpy as np
import pandas as pd

from autoresearch.models._gbdt_features import gbdt_features
from autoresearch.models.base import Dataset, FitReport, Model
from autoresearch.models.registry import register

_SUB_PARAMS = {
    "objective": "regression", "metric": "l2", "learning_rate": 0.05,
    "num_leaves": 15, "max_depth": 5, "min_data_in_leaf": 100,
    "lambda_l2": 10.0, "lambda_l1": 1.0, "verbosity": -1,
}


@register("double_ensemble")
class DoubleEnsembleRanker(Model):
    """lightgbm 子模型 bagging + 损失曲线样本重加权(Qlib DoubleEnsemble 的精简原生实现)。"""

    feature_set = "core"
    kind = "core"

    def __init__(self, num_models: int = 6, sub_params: dict | None = None,
                 num_boost_round: int = 150, feature_fraction: float = 0.8,
                 alpha: float = 1.0, seed: int = 7):
        self.num_models = num_models
        self.sub_params = {**_SUB_PARAMS, **(sub_params or {})}
        self.num_boost_round = num_boost_round
        self.feature_fraction = feature_fraction
        self.alpha = alpha           # 重加权强度(h-value → 权重的放大系数)
        self.seed = seed
        self.models: list = []
        self.sub_feature_cols: list[list[str]] = []
        self.features: list[str] = []

    def fit(self, ds: Dataset) -> FitReport:
        import lightgbm as lgb

        X = gbdt_features(ds.X)
        self.features = list(X.columns)
        y = pd.to_numeric(ds.y, errors="coerce").to_numpy(dtype=float)
        n = len(X)
        rng = np.random.default_rng(self.seed)
        weights = np.ones(n, dtype=float)            # 样本权重(SR 逐轮更新)
        loss_curve = np.zeros((n, self.num_models))  # 每样本 × 每轮的损失(算 h-value)
        self.models, self.sub_feature_cols = [], []

        for k in range(self.num_models):
            # 特征扰动(FS 简化):每个子模型随机取一部分特征列。
            n_feat = max(1, int(round(len(self.features) * self.feature_fraction)))
            cols = list(rng.choice(self.features, size=n_feat, replace=False))
            self.sub_feature_cols.append(cols)
            dtrain = lgb.Dataset(X[cols], label=y, weight=weights)
            booster = lgb.train(self.sub_params, dtrain, num_boost_round=self.num_boost_round)
            self.models.append(booster)
            # 当前集成预测 → 每样本损失 → 写损失曲线 → 重加权(难样本权重抬高)。
            ens = self._ensemble_predict(X)
            loss_curve[:, k] = (ens - y) ** 2
            weights = self._reweight(loss_curve[:, : k + 1])

        n_dates = int(pd.Series(ds.dates).nunique()) if len(ds.dates) else 0
        return FitReport(n_rows=n, n_dates=n_dates,
                         notes={"n_features": len(self.features), "num_models": len(self.models),
                                "backend": "double_ensemble(lgbm)"})

    def _reweight(self, loss_curve: np.ndarray) -> np.ndarray:
        """h-value 重加权:近期损失 *大* 且 *跨子模型不稳定* 的样本 → 高权重(Qlib SR 思路)。

        h = mean(recent loss) + alpha * std(loss across sub-models);min-max 归一到 [eps, 1],
        再线性放大成权重(均值约 1,稳定不致权重崩塌)。
        """
        recent = loss_curve.mean(axis=1)
        instab = loss_curve.std(axis=1)
        h = recent + self.alpha * instab
        rng = h.max() - h.min()
        if rng < 1e-12:
            return np.ones_like(h)
        norm = (h - h.min()) / rng              # [0,1]
        w = 0.5 + norm                          # [0.5, 1.5],均值 ~1
        return w * (len(w) / w.sum())           # 归一到均值 1

    def _ensemble_predict(self, X: pd.DataFrame) -> np.ndarray:
        preds = [m.predict(X[cols]) for m, cols in zip(self.models, self.sub_feature_cols, strict=True)]
        return np.mean(preds, axis=0)

    def predict(self, feats: pd.DataFrame) -> pd.Series:
        if not self.models:
            raise RuntimeError("DoubleEnsembleRanker.predict called before fit/load")
        X = gbdt_features(feats).reindex(columns=self.features)
        return pd.Series(self._ensemble_predict(X), index=feats.index)

    def save(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(pickle.dumps(
            {"models": self.models, "sub_feature_cols": self.sub_feature_cols,
             "features": self.features, "sub_params": self.sub_params,
             "num_models": self.num_models}))

    @classmethod
    def load(cls, path: str | Path) -> DoubleEnsembleRanker:
        bundle = pickle.loads(Path(path).read_bytes())
        obj = cls(num_models=bundle.get("num_models", 6), sub_params=bundle.get("sub_params"))
        obj.models = bundle["models"]
        obj.sub_feature_cols = bundle["sub_feature_cols"]
        obj.features = bundle["features"]
        return obj
