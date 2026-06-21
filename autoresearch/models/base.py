#!/usr/bin/env python3
"""Model 接口 —— 可插拔粗排模型的最小契约(只管学和打分,不碰取数/特征)。

design: docs/specs/2026-06-22-autoresearch-arch-redesign-design.md §C①。

模型**消费 Dataset**(已物化的特征 + 每日横截面 rank-norm 标签 + 行日期),**绝不**触碰
DataHandler / lake —— 数据层与模型层解耦。Trainer 负责从 lake 物化 + 切分 + 喂 Dataset;
模型只实现 `fit(ds) / predict(feats) / save / load`。`predict` 吐**每只一个横截面分**(越高
越看多),所有模型同口径,Trainer 用统一 rank-IC 评估、champion 门统一晋升。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd


@dataclass
class Dataset:
    """喂给 Model.fit 的训练数据:特征矩阵 + 标签 + 行日期。

    X     : 特征帧(行=个股×日,列=特征),index 任意。
    y     : 标签(每个成型日横截面 rank(pct=True) 后的前瞻收益;学相对排序、免 regime 位移)。
    dates : 每行所属的成型日(与 X/y 行对齐)——切分/分组算 rank-IC 用。
    """

    X: pd.DataFrame
    y: pd.Series
    dates: pd.Series


@dataclass
class FitReport:
    """一次 fit 的轻量回执(供 trace/manifest 记录,不含模型本体)。"""

    n_rows: int
    n_dates: int
    best_iter: int | None = None
    notes: dict = field(default_factory=dict)


class Model(ABC):
    """可插拔粗排模型接口。子类声明 feature_set/kind,实现 fit/predict/save/load。"""

    feature_set: str = "core"   # 我们特征库的命名视图(core / seq60 / graph…)
    kind: str = "core"          # core(横截面表格)| seq | graph

    @abstractmethod
    def fit(self, ds: Dataset) -> FitReport:
        """在 Dataset 上拟合,返回 FitReport。无需训练的模型(如线性)可 no-op。"""
        raise NotImplementedError

    @abstractmethod
    def predict(self, feats: pd.DataFrame) -> pd.Series:
        """对特征帧打分:每行一个横截面分(越高越看多),index 对齐输入。"""
        raise NotImplementedError

    @abstractmethod
    def save(self, path: str | Path) -> None:
        """序列化到 path(目录由调用方保证存在或自建)。"""
        raise NotImplementedError

    @classmethod
    @abstractmethod
    def load(cls, path: str | Path) -> Model:
        """从 path 反序列化出一个就绪可预测的实例。"""
        raise NotImplementedError
