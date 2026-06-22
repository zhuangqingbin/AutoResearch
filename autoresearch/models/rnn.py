#!/usr/bin/env python3
"""LSTM / GRU 序列排序器(torch,seq feature_set)。

design: docs/specs/2026-06-22-autoresearch-arch-redesign-design.md §C(序列 zoo).

消费 seq 视图(每股 SEQ_WINDOW 日滚动窗 × SEQ_FEATURES 日级特征,展平 `{feat}_t{w}` 时间主序),
标准化后在 net 内 reshape [N, W*K] → [N, W, K] 喂 RNN,取末步隐状态 → 打分。走统一 Trainer +
champion 门(薄面板/弱信号下赢不过 linear 即被挡)。LSTM/GRU 仅 cell 不同,共用基类。
"""
from __future__ import annotations

import pickle
from pathlib import Path

import pandas as pd
import torch
from torch import nn

from autoresearch.data.features import SEQ_FEATURES, SEQ_WINDOW, feature_columns
from autoresearch.models._torch_util import Standardizer, pick_device, set_seed, train_loop
from autoresearch.models.base import Dataset, FitReport, Model
from autoresearch.models.registry import register

_K = len(SEQ_FEATURES)
_W = SEQ_WINDOW


class _RNNNet(nn.Module):
    def __init__(self, n_feat: int, window: int, hidden: int = 32, cell: str = "lstm",
                 layers: int = 1, dropout: float = 0.1) -> None:
        super().__init__()
        self.window, self.n_feat = window, n_feat
        rnn = nn.LSTM if cell == "lstm" else nn.GRU
        self.rnn = rnn(n_feat, hidden, num_layers=layers, batch_first=True,
                       dropout=dropout if layers > 1 else 0.0)
        self.head = nn.Sequential(nn.Dropout(dropout), nn.Linear(hidden, 1))

    def forward(self, x):
        n = x.shape[0]
        x = x.view(n, self.window, self.n_feat)   # [N, W*K] → [N, W, K](时间主序)
        out, _ = self.rnn(x)
        return self.head(out[:, -1, :]).squeeze(-1)   # 末步隐状态 → 分


class _SeqRanker(Model):
    """LSTM/GRU 共用基类:seq 列选取 → 标准化 → RNN。子类只设 `_cell`。"""

    feature_set = "seq"
    kind = "seq"
    _cell = "lstm"

    def __init__(self, hidden: int = 32, layers: int = 1, dropout: float = 0.1, epochs: int = 200,
                 lr: float = 1e-3, wd: float = 1e-4, patience: int = 20,
                 valid_fraction: float = 0.2, seed: int = 7, prefer_mps: bool = False):
        self.hidden, self.layers, self.dropout = hidden, layers, dropout
        self.epochs, self.lr, self.wd, self.patience = epochs, lr, wd, patience
        self.valid_fraction, self.seed, self.prefer_mps = valid_fraction, seed, prefer_mps
        self.net: nn.Module | None = None
        self.scaler: Standardizer | None = None
        self.features: list[str] = feature_columns("seq")

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
        net = _RNNNet(_K, _W, self.hidden, self._cell, self.layers, self.dropout)
        dev = pick_device(self.prefer_mps)
        if is_val.any() and not is_val.all():
            net = train_loop(net, xs[~is_val], y[~is_val], xs[is_val], y[is_val],
                             epochs=self.epochs, lr=self.lr, wd=self.wd, patience=self.patience, device=dev)
        else:
            net = train_loop(net, xs, y, epochs=self.epochs, lr=self.lr, wd=self.wd,
                             patience=self.patience, device=dev)
        self.net = net
        return FitReport(n_rows=len(x), n_dates=len(udates), notes={"window": _W, "n_feat": _K})

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
            "features": self.features, "cell": self._cell,
            "arch": {"hidden": self.hidden, "layers": self.layers, "dropout": self.dropout},
        }))

    @classmethod
    def load(cls, path: str | Path) -> _SeqRanker:
        b = pickle.loads(Path(path).read_bytes())
        obj = cls(hidden=b["arch"]["hidden"], layers=b["arch"]["layers"], dropout=b["arch"]["dropout"])
        obj.features = b["features"]
        obj.scaler = Standardizer.from_state(b["scaler"]) if b["scaler"] else None
        if b["state_dict"] is not None:
            net = _RNNNet(_K, _W, obj.hidden, b["cell"], obj.layers, obj.dropout)
            net.load_state_dict(b["state_dict"])
            obj.net = net.eval()
        return obj


@register("lstm")
class LSTMRanker(_SeqRanker):
    _cell = "lstm"


@register("gru")
class GRURanker(_SeqRanker):
    _cell = "gru"
