#!/usr/bin/env python3
"""序列排序器基类 + 循环网(LSTM / GRU / ALSTM)。

design: docs/specs/2026-06-22-autoresearch-arch-redesign-design.md §C(序列 zoo).

`_SeqRanker` 是**可插拔基类**:管 seq 列选取 → 标准化 → reshape [N, W*K] → [N, W, K] 训练 →
打分 → save/load;子类只实现 `_make_net()` 给出自己的 nn.Module。tcn.py / attn.py 复用本基类。
所有序列模型走统一 Trainer + champion 门(薄面板/弱信号下赢不过 linear 即被挡)。
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

K = len(SEQ_FEATURES)   # 每步特征数
W = SEQ_WINDOW           # 窗长


class _SeqRanker(Model):
    """序列模型可插拔基类。子类实现 `_make_net()`;其余(数据/标准化/训练/存取)在此。"""

    feature_set = "seq"
    kind = "seq"

    def __init__(self, hidden: int = 32, layers: int = 1, dropout: float = 0.1, epochs: int = 200,
                 lr: float = 1e-3, wd: float = 1e-4, patience: int = 20, valid_fraction: float = 0.2,
                 seed: int = 7, prefer_mps: bool = False, **net_kwargs):
        self.hidden, self.layers, self.dropout = hidden, layers, dropout
        self.epochs, self.lr, self.wd, self.patience = epochs, lr, wd, patience
        self.valid_fraction, self.seed, self.prefer_mps = valid_fraction, seed, prefer_mps
        self.net_kwargs = dict(net_kwargs)
        self.net: nn.Module | None = None
        self.scaler: Standardizer | None = None
        self.features: list[str] = feature_columns("seq")

    # 子类实现:返回吃 [N, W*K] 的 nn.Module(内部 reshape 成 [N, W, K])。
    def _make_net(self) -> nn.Module:
        raise NotImplementedError

    def _arch(self) -> dict:
        return {"hidden": self.hidden, "layers": self.layers, "dropout": self.dropout, **self.net_kwargs}

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
        return FitReport(n_rows=len(x), n_dates=len(udates), notes={"window": W, "n_feat": K})

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
    def load(cls, path: str | Path) -> _SeqRanker:
        b = pickle.loads(Path(path).read_bytes())
        obj = cls(**b["arch"])
        obj.features = b["features"]
        obj.scaler = Standardizer.from_state(b["scaler"]) if b["scaler"] else None
        if b["state_dict"] is not None:
            net = obj._make_net()
            net.load_state_dict(b["state_dict"])
            obj.net = net.eval()
        return obj


def _reshape(x):
    return x.view(x.shape[0], W, K)   # [N, W*K] → [N, W, K](时间主序)


class _RNNNet(nn.Module):
    def __init__(self, hidden: int, cell: str, layers: int, dropout: float) -> None:
        super().__init__()
        rnn = nn.LSTM if cell == "lstm" else nn.GRU
        self.rnn = rnn(K, hidden, num_layers=layers, batch_first=True,
                       dropout=dropout if layers > 1 else 0.0)
        self.head = nn.Sequential(nn.Dropout(dropout), nn.Linear(hidden, 1))

    def forward(self, x):
        out, _ = self.rnn(_reshape(x))
        return self.head(out[:, -1, :]).squeeze(-1)


class _ALSTMNet(nn.Module):
    """LSTM + 时间注意力(对各步隐状态加权求和,而非仅取末步)。"""

    def __init__(self, hidden: int, layers: int, dropout: float) -> None:
        super().__init__()
        self.rnn = nn.LSTM(K, hidden, num_layers=layers, batch_first=True,
                           dropout=dropout if layers > 1 else 0.0)
        self.att = nn.Linear(hidden, 1)
        self.head = nn.Sequential(nn.Dropout(dropout), nn.Linear(hidden, 1))

    def forward(self, x):
        out, _ = self.rnn(_reshape(x))                    # [N, W, H]
        w = torch.softmax(self.att(out).squeeze(-1), dim=1)   # [N, W]
        ctx = (out * w.unsqueeze(-1)).sum(dim=1)          # [N, H]
        return self.head(ctx).squeeze(-1)


@register("lstm")
class LSTMRanker(_SeqRanker):
    def _make_net(self) -> nn.Module:
        return _RNNNet(self.hidden, "lstm", self.layers, self.dropout)


@register("gru")
class GRURanker(_SeqRanker):
    def _make_net(self) -> nn.Module:
        return _RNNNet(self.hidden, "gru", self.layers, self.dropout)


@register("alstm")
class ALSTMRanker(_SeqRanker):
    def _make_net(self) -> nn.Module:
        return _ALSTMNet(self.hidden, self.layers, self.dropout)
