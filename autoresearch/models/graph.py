#!/usr/bin/env python3
"""图关系排序器:GATs / HIST / IGMTF(紧凑原生,graph feature_set)。

design: docs/specs/2026-06-22-autoresearch-arch-redesign-design.md §C(图 zoo).

graph 特征 = 自身 gbdt 特征 + 行业邻接上下文(handler 预计算 ctx_,1-hop 行业 GCN)→ 图模型是
**行独立**网络,走共享 train_loop + 统一 Trainer + champion 门(无需逐日图训练、无 N×N 邻接)。
三模型按各自图归纳偏置组合"自身 vs 行业上下文":GATs=注意力门混合、HIST=行业共享+个体分解、
IGMTF=自身+行业+全局上下文。**紧凑版**:学习式注意力降到逐维门控,够做图关系挑战者。
"""
from __future__ import annotations

import pickle
from pathlib import Path

import pandas as pd
import torch
from torch import nn

from autoresearch.data.features import GRAPH_SELF, feature_columns
from autoresearch.models._torch_util import Standardizer, pick_device, set_seed, train_loop
from autoresearch.models.base import Dataset, FitReport, Model
from autoresearch.models.registry import register

_HALF = len(GRAPH_SELF)   # 自身/上下文各 _HALF 列(graph 视图 = 2×_HALF)


class _GATNet(nn.Module):
    """注意力门:逐维学"信自身 vs 信行业上下文"。"""

    def __init__(self, hidden: int, dropout: float) -> None:
        super().__init__()
        self.gate = nn.Sequential(nn.Linear(_HALF, _HALF), nn.Sigmoid())
        self.proj = nn.Sequential(nn.Linear(_HALF, hidden), nn.ReLU(), nn.Dropout(dropout))
        self.head = nn.Linear(hidden, 1)

    def forward(self, x):
        s, c = x[:, :_HALF], x[:, _HALF:]
        return self.head(self.proj(s + self.gate(s) * c)).squeeze(-1)


class _HISTNet(nn.Module):
    """行业共享(concept)+ 个体(individual)分解后合成。"""

    def __init__(self, hidden: int, dropout: float) -> None:
        super().__init__()
        self.shared = nn.Sequential(nn.Linear(_HALF, hidden), nn.ReLU(), nn.Dropout(dropout))
        self.indiv = nn.Sequential(nn.Linear(_HALF, hidden), nn.ReLU(), nn.Dropout(dropout))
        self.head = nn.Linear(hidden, 1)

    def forward(self, x):
        s, c = x[:, :_HALF], x[:, _HALF:]
        return self.head(self.shared(c) + self.indiv(s)).squeeze(-1)


class _IGMTFNet(nn.Module):
    """自身 + 行业上下文 + 全局上下文(batch 均值)三级聚合。"""

    def __init__(self, hidden: int, dropout: float) -> None:
        super().__init__()
        self.proj = nn.Sequential(nn.Linear(3 * _HALF, hidden), nn.ReLU(), nn.Dropout(dropout))
        self.head = nn.Linear(hidden, 1)

    def forward(self, x):
        gmean = x[:, _HALF:].mean(dim=0, keepdim=True).expand(x.shape[0], -1)   # 全局行业上下文
        return self.head(self.proj(torch.cat([x, gmean], dim=1))).squeeze(-1)


class _GraphRanker(Model):
    """图模型可插拔基类(行独立,图关系已编码进特征)。子类实现 `_make_net()`。"""

    feature_set = "graph"
    kind = "graph"

    def __init__(self, hidden: int = 64, dropout: float = 0.1, epochs: int = 200, lr: float = 1e-3,
                 wd: float = 1e-4, patience: int = 20, valid_fraction: float = 0.2,
                 seed: int = 7, prefer_mps: bool = False, **net_kwargs):
        self.hidden, self.dropout = hidden, dropout
        self.epochs, self.lr, self.wd, self.patience = epochs, lr, wd, patience
        self.valid_fraction, self.seed, self.prefer_mps = valid_fraction, seed, prefer_mps
        self.net_kwargs = dict(net_kwargs)
        self.net: nn.Module | None = None
        self.scaler: Standardizer | None = None
        self.features: list[str] = feature_columns("graph")

    def _make_net(self) -> nn.Module:
        raise NotImplementedError

    def _arch(self) -> dict:
        return {"hidden": self.hidden, "dropout": self.dropout, **self.net_kwargs}

    def _x(self, df: pd.DataFrame) -> pd.DataFrame:
        return df.reindex(columns=self.features)

    def fit(self, ds: Dataset) -> FitReport:
        set_seed(self.seed)
        x = self._x(ds.X)
        self.scaler = Standardizer().fit(x)
        xs = self.scaler.transform(x)
        y = pd.to_numeric(ds.y, errors="coerce").fillna(0.5).to_numpy(dtype="float32")
        dates = pd.Series(ds.dates).to_numpy()
        udates = sorted(pd.unique(dates))
        n_val = max(1, min(int(len(udates) * self.valid_fraction), max(1, len(udates) - 1)))
        val = set(udates[-n_val:]) if len(udates) > 1 else set()
        is_val = pd.Series(dates).isin(val).to_numpy()
        net = self._make_net()
        dev = pick_device(self.prefer_mps)
        if is_val.any() and not is_val.all():
            net = train_loop(net, xs[~is_val], y[~is_val], xs[is_val], y[is_val],
                             epochs=self.epochs, lr=self.lr, wd=self.wd, patience=self.patience, device=dev)
        else:
            net = train_loop(net, xs, y, epochs=self.epochs, lr=self.lr, wd=self.wd,
                             patience=self.patience, device=dev)
        self.net = net
        return FitReport(n_rows=len(x), n_dates=len(udates), notes={"half": _HALF})

    def predict(self, feats: pd.DataFrame) -> pd.Series:
        if self.net is None or self.scaler is None:
            raise RuntimeError(f"{type(self).__name__}.predict called before fit/load")
        xs = self.scaler.transform(self._x(feats))
        self.net.eval()
        with torch.no_grad():
            s = self.net(torch.tensor(xs)).numpy()
        return pd.Series(s, index=feats.index)

    def save(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(pickle.dumps({
            "state_dict": None if self.net is None else self.net.state_dict(),
            "scaler": None if self.scaler is None else self.scaler.state(),
            "features": self.features, "arch": self._arch(),
        }))

    @classmethod
    def load(cls, path: str | Path) -> _GraphRanker:
        b = pickle.loads(Path(path).read_bytes())
        obj = cls(**b["arch"])
        obj.features = b["features"]
        obj.scaler = Standardizer.from_state(b["scaler"]) if b["scaler"] else None
        if b["state_dict"] is not None:
            net = obj._make_net()
            net.load_state_dict(b["state_dict"])
            obj.net = net.eval()
        return obj


@register("gats")
class GATRanker(_GraphRanker):
    def _make_net(self) -> nn.Module:
        return _GATNet(self.hidden, self.dropout)


@register("hist")
class HISTRanker(_GraphRanker):
    def _make_net(self) -> nn.Module:
        return _HISTNet(self.hidden, self.dropout)


@register("igmtf")
class IGMTFRanker(_GraphRanker):
    def _make_net(self) -> nn.Module:
        return _IGMTFNet(self.hidden, self.dropout)
