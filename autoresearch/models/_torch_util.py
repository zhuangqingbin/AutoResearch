#!/usr/bin/env python3
"""torch 表格模型共享件:设备 / 标准化(中位数填补 NaN + 标准化)/ 训练环。

design: docs/specs/2026-06-22-autoresearch-arch-redesign-design.md §C(torch 表格模型).

NN 不能吃 NaN、对尺度敏感 → core 特征(0-1 组分 + 不同量级原始因子 + NaN)先 `Standardizer`
中位数填补 + 标准化;scaler 随模型持久化(predict 用同口径)。全批梯度下降 + 末尾日早停。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import torch
from torch import nn


def set_seed(seed: int = 7) -> None:
    torch.manual_seed(seed)
    np.random.seed(seed)


def pick_device(prefer_mps: bool = False) -> str:
    if prefer_mps and torch.backends.mps.is_available():
        return "mps"
    return "cpu"   # 测试/复现默认 CPU


class Standardizer:
    """列中位数填补 NaN + 标准化(均值0/方差1);fit 存 median/mean/std,可持久化。"""

    def __init__(self) -> None:
        self.median = self.mean = self.std = None
        self.cols: list[str] = []

    def fit(self, X: pd.DataFrame) -> Standardizer:
        self.cols = list(X.columns)
        xn = X.apply(pd.to_numeric, errors="coerce")
        self.median = xn.median(numeric_only=True)
        xi = xn.fillna(self.median).fillna(0.0)
        self.mean = xi.mean()
        self.std = xi.std(ddof=0).replace(0, 1.0)
        return self

    def transform(self, X: pd.DataFrame) -> np.ndarray:
        xn = X.reindex(columns=self.cols).apply(pd.to_numeric, errors="coerce")
        xi = xn.fillna(self.median).fillna(0.0)
        return ((xi - self.mean) / self.std).to_numpy(dtype="float32")

    def state(self) -> dict:
        return {"median": self.median, "mean": self.mean, "std": self.std, "cols": self.cols}

    @classmethod
    def from_state(cls, s: dict) -> Standardizer:
        o = cls()
        o.median, o.mean, o.std, o.cols = s["median"], s["mean"], s["std"], s["cols"]
        return o


def train_loop(net: nn.Module, x_tr, y_tr, x_va=None, y_va=None, *,
               epochs: int = 200, lr: float = 1e-3, wd: float = 1e-4,
               patience: int = 20, device: str = "cpu") -> nn.Module:
    """全批 Adam + MSE;有 valid 则早停取最优。返回(最优权重已载入的)net。"""
    net = net.to(device)
    opt = torch.optim.Adam(net.parameters(), lr=lr, weight_decay=wd)
    loss_fn = nn.MSELoss()
    xt = torch.tensor(x_tr, device=device)
    yt = torch.tensor(y_tr, dtype=torch.float32, device=device)
    has_val = x_va is not None and len(x_va) > 1
    if has_val:
        xv = torch.tensor(x_va, device=device)
        yv = torch.tensor(y_va, dtype=torch.float32, device=device)
    best, best_state, bad = float("inf"), None, 0
    for _ in range(epochs):
        net.train()
        opt.zero_grad()
        loss = loss_fn(net(xt), yt)
        loss.backward()
        opt.step()
        if has_val:
            net.eval()
            with torch.no_grad():
                v = loss_fn(net(xv), yv).item()
            if v < best - 1e-7:
                best, bad = v, 0
                best_state = {k: val.detach().clone() for k, val in net.state_dict().items()}
            else:
                bad += 1
                if bad >= patience:
                    break
    if best_state is not None:
        net.load_state_dict(best_state)
    return net.to("cpu").eval()
