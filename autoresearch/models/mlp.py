#!/usr/bin/env python3
"""MLPRanker —— 前馈神经网横截面排序器(torch,core 特征)。

design: docs/specs/2026-06-22-autoresearch-arch-redesign-design.md §C(torch 表格 zoo).

与 GBDTRanker 同 `core` 特征(gbdt_features),但 NN 需 Standardizer 预处理(填补+标准化)。
走统一 Trainer + champion 门——薄面板上多半赢不过 linear、被门挡,框架照样支持。
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


class _MLPNet(nn.Module):
    def __init__(self, n_in: int, hidden=(256, 128, 64), dropout: float = 0.2) -> None:
        super().__init__()
        layers: list[nn.Module] = []
        d = n_in
        for h in hidden:
            layers += [nn.Linear(d, h), nn.BatchNorm1d(h), nn.ReLU(), nn.Dropout(dropout)]
            d = h
        layers += [nn.Linear(d, 1)]
        self.net = nn.Sequential(*layers)

    def forward(self, x):
        return self.net(x).squeeze(-1)


@register("mlp")
class MLPRanker(Model):
    """前馈 MLP 排序器(core 特征,标准化预处理,早停)。"""

    feature_set = "core"
    kind = "core"

    def __init__(self, hidden=(256, 128, 64), dropout: float = 0.2, epochs: int = 200,
                 lr: float = 1e-3, wd: float = 1e-4, patience: int = 20,
                 valid_fraction: float = 0.2, seed: int = 7, prefer_mps: bool = False):
        self.hidden = tuple(hidden)
        self.dropout = dropout
        self.epochs = epochs
        self.lr = lr
        self.wd = wd
        self.patience = patience
        self.valid_fraction = valid_fraction
        self.seed = seed
        self.prefer_mps = prefer_mps
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
        net = _MLPNet(xs.shape[1], self.hidden, self.dropout)
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
            raise RuntimeError("MLPRanker.predict called before fit/load")
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
            "arch": {"hidden": self.hidden, "dropout": self.dropout},
        }))

    @classmethod
    def load(cls, path: str | Path) -> MLPRanker:
        b = pickle.loads(Path(path).read_bytes())
        obj = cls(hidden=b["arch"]["hidden"], dropout=b["arch"]["dropout"])
        obj.features = b["features"]
        obj.scaler = Standardizer.from_state(b["scaler"]) if b["scaler"] else None
        if b["state_dict"] is not None:
            net = _MLPNet(b["n_in"], obj.hidden, obj.dropout)
            net.load_state_dict(b["state_dict"])
            obj.net = net.eval()
        return obj
