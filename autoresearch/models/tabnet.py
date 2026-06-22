#!/usr/bin/env python3
"""TabNetRanker —— 紧凑原生 TabNet 横截面排序器(torch,core 特征)。

design: docs/specs/2026-06-22-autoresearch-arch-redesign-design.md §C(torch 表格 zoo).

参考 Qlib/TabNet 结构、原生实现到我们 Model 接口(不引 pytorch-tabnet):**逐决策步的注意力特征
选择** —— 每步用 attentive transformer 出一张特征 mask(softmax,带 prior 松弛,近似 sparsemax),
masked 特征过 decision transformer 累加 → 输出分。紧凑版(非完整论文的 GLU/ghost-BN),够做
非线性挑战者;走统一 Trainer + champion 门(赢不过 linear 即被挡)。
"""
from __future__ import annotations

import pickle
from pathlib import Path

import pandas as pd
import torch
from torch import nn

from autoresearch.models._gbdt_features import gbdt_features
from autoresearch.models._torch_util import Standardizer, pick_device, set_seed, train_loop
from autoresearch.models.base import Dataset, FitReport, Model
from autoresearch.models.registry import register


class _TabNet(nn.Module):
    def __init__(self, n_in: int, n_d: int = 32, n_steps: int = 3, dropout: float = 0.1,
                 gamma: float = 1.5) -> None:
        super().__init__()
        self.n_steps = n_steps
        self.gamma = gamma
        self.bn = nn.BatchNorm1d(n_in)
        self.shared = nn.Sequential(nn.Linear(n_in, n_d), nn.ReLU())
        self.att = nn.ModuleList(nn.Linear(n_d, n_in) for _ in range(n_steps))
        self.dec = nn.ModuleList(
            nn.Sequential(nn.Linear(n_in, n_d), nn.ReLU(), nn.Dropout(dropout)) for _ in range(n_steps)
        )
        self.head = nn.Linear(n_d, 1)

    def forward(self, x):
        x = self.bn(x)
        a = self.shared(x)
        prior = torch.ones_like(x)
        agg = None
        for att, dec in zip(self.att, self.dec, strict=True):
            mask = torch.softmax(att(a) * prior, dim=-1)   # 注意力特征 mask(prior 松弛 ~ sparsemax 近似)
            d = dec(x * mask)
            agg = d if agg is None else agg + d
            a = d
            prior = prior * (self.gamma - mask)
        return self.head(agg).squeeze(-1)


@register("tabnet")
class TabNetRanker(Model):
    """紧凑原生 TabNet 排序器(core 特征,标准化预处理,早停)。"""

    feature_set = "core"
    kind = "core"

    def __init__(self, n_d: int = 32, n_steps: int = 3, dropout: float = 0.1, gamma: float = 1.5,
                 epochs: int = 200, lr: float = 1e-3, wd: float = 1e-4, patience: int = 20,
                 valid_fraction: float = 0.2, seed: int = 7, prefer_mps: bool = False):
        self.n_d, self.n_steps, self.dropout, self.gamma = n_d, n_steps, dropout, gamma
        self.epochs, self.lr, self.wd, self.patience = epochs, lr, wd, patience
        self.valid_fraction, self.seed, self.prefer_mps = valid_fraction, seed, prefer_mps
        self.net: nn.Module | None = None
        self.scaler: Standardizer | None = None
        self.features: list[str] = []

    def fit(self, ds: Dataset) -> FitReport:
        set_seed(self.seed)
        x = gbdt_features(ds.X)
        self.features = list(x.columns)
        self.scaler = Standardizer().fit(x)
        xs = self.scaler.transform(x)
        y = pd.to_numeric(ds.y, errors="coerce").fillna(0.5).to_numpy(dtype="float32")
        dates = pd.Series(ds.dates).to_numpy()
        udates = sorted(pd.unique(dates))
        n_val = max(1, min(int(len(udates) * self.valid_fraction), max(1, len(udates) - 1)))
        val = set(udates[-n_val:]) if len(udates) > 1 else set()
        is_val = pd.Series(dates).isin(val).to_numpy()
        net = _TabNet(xs.shape[1], self.n_d, self.n_steps, self.dropout, self.gamma)
        dev = pick_device(self.prefer_mps)
        if is_val.any() and not is_val.all():
            net = train_loop(net, xs[~is_val], y[~is_val], xs[is_val], y[is_val],
                             epochs=self.epochs, lr=self.lr, wd=self.wd, patience=self.patience, device=dev)
        else:
            net = train_loop(net, xs, y, epochs=self.epochs, lr=self.lr, wd=self.wd,
                             patience=self.patience, device=dev)
        self.net = net
        return FitReport(n_rows=len(x), n_dates=len(udates), notes={"n_features": len(self.features)})

    def predict(self, feats: pd.DataFrame) -> pd.Series:
        if self.net is None or self.scaler is None:
            raise RuntimeError("TabNetRanker.predict called before fit/load")
        xs = self.scaler.transform(gbdt_features(feats))
        self.net.eval()
        with torch.no_grad():
            s = self.net(torch.tensor(xs)).numpy()
        return pd.Series(s, index=feats.index)

    def save(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(pickle.dumps({
            "state_dict": None if self.net is None else self.net.state_dict(),
            "scaler": None if self.scaler is None else self.scaler.state(),
            "features": self.features, "n_in": len(self.features),
            "arch": {"n_d": self.n_d, "n_steps": self.n_steps, "dropout": self.dropout, "gamma": self.gamma},
        }))

    @classmethod
    def load(cls, path: str | Path) -> TabNetRanker:
        b = pickle.loads(Path(path).read_bytes())
        obj = cls(**b["arch"])
        obj.features = b["features"]
        obj.scaler = Standardizer.from_state(b["scaler"]) if b["scaler"] else None
        if b["state_dict"] is not None:
            net = _TabNet(b["n_in"], obj.n_d, obj.n_steps, obj.dropout, obj.gamma)
            net.load_state_dict(b["state_dict"])
            obj.net = net.eval()
        return obj
