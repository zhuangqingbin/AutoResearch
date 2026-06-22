#!/usr/bin/env python3
"""注意力序列排序器:Transformer / Localformer / TFT / TRA(紧凑原生,seq feature_set)。

design: docs/specs/2026-06-22-autoresearch-arch-redesign-design.md §C(序列 zoo).
复用 rnn._SeqRanker 基类。TFT/TRA 为紧凑版(捕获核心结构:TFT=变量选择门+LSTM+自注意力;
TRA=LSTM 编码+多路预测+路由混合),非完整论文,够做非线性挑战者、走统一 Trainer + champion 门。
hidden 需能被 heads 整除(默认 heads=4)。
"""
from __future__ import annotations

import torch
from torch import nn

from autoresearch.models.registry import register
from autoresearch.models.rnn import K, W, _reshape, _SeqRanker


class _TransformerNet(nn.Module):
    def __init__(self, hidden: int, heads: int, layers: int, dropout: float) -> None:
        super().__init__()
        self.proj = nn.Linear(K, hidden)
        self.pos = nn.Parameter(torch.randn(1, W, hidden) * 0.02)
        enc = nn.TransformerEncoderLayer(hidden, heads, hidden * 2, dropout, batch_first=True)
        self.enc = nn.TransformerEncoder(enc, layers)
        self.head = nn.Linear(hidden, 1)

    def forward(self, x):
        h = self.enc(self.proj(_reshape(x)) + self.pos)
        return self.head(h[:, -1, :]).squeeze(-1)


class _LocalformerNet(nn.Module):
    """Transformer + 局部卷积残差(Localformer 思路)。"""

    def __init__(self, hidden: int, heads: int, layers: int, kernel: int, dropout: float) -> None:
        super().__init__()
        self.proj = nn.Linear(K, hidden)
        self.local = nn.Conv1d(hidden, hidden, kernel, padding=kernel // 2)
        self.pos = nn.Parameter(torch.randn(1, W, hidden) * 0.02)
        enc = nn.TransformerEncoderLayer(hidden, heads, hidden * 2, dropout, batch_first=True)
        self.enc = nn.TransformerEncoder(enc, layers)
        self.head = nn.Linear(hidden, 1)

    def forward(self, x):
        h = self.proj(_reshape(x))
        h = h + self.local(h.transpose(1, 2)).transpose(1, 2)
        return self.head(self.enc(h + self.pos)[:, -1, :]).squeeze(-1)


class _TFTNet(nn.Module):
    """紧凑 TFT:变量选择门 + LSTM + 自注意力。"""

    def __init__(self, hidden: int, heads: int, dropout: float) -> None:
        super().__init__()
        self.gate = nn.Sequential(nn.Linear(K, K), nn.Sigmoid())
        self.proj = nn.Linear(K, hidden)
        self.lstm = nn.LSTM(hidden, hidden, batch_first=True)
        self.attn = nn.MultiheadAttention(hidden, heads, dropout=dropout, batch_first=True)
        self.norm = nn.LayerNorm(hidden)
        self.head = nn.Linear(hidden, 1)

    def forward(self, x):
        s = _reshape(x)
        h = self.proj(s * self.gate(s))
        h, _ = self.lstm(h)
        a, _ = self.attn(h, h, h)
        return self.head(self.norm(h + a)[:, -1, :]).squeeze(-1)


class _TRANet(nn.Module):
    """紧凑 TRA:LSTM 编码 + 多路预测 + 路由 softmax 混合。"""

    def __init__(self, hidden: int, routes: int, dropout: float) -> None:
        super().__init__()
        self.lstm = nn.LSTM(K, hidden, batch_first=True)
        self.drop = nn.Dropout(dropout)
        self.preds = nn.Linear(hidden, routes)
        self.router = nn.Linear(hidden, routes)

    def forward(self, x):
        out, _ = self.lstm(_reshape(x))
        last = self.drop(out[:, -1, :])
        w = torch.softmax(self.router(last), dim=-1)
        return (self.preds(last) * w).sum(-1)


@register("transformer")
class TransformerRanker(_SeqRanker):
    def _make_net(self) -> nn.Module:
        return _TransformerNet(self.hidden, self.net_kwargs.get("heads", 4), self.layers, self.dropout)


@register("localformer")
class LocalformerRanker(_SeqRanker):
    def _make_net(self) -> nn.Module:
        return _LocalformerNet(self.hidden, self.net_kwargs.get("heads", 4), self.layers,
                               self.net_kwargs.get("kernel", 3), self.dropout)


@register("tft")
class TFTRanker(_SeqRanker):
    def _make_net(self) -> nn.Module:
        return _TFTNet(self.hidden, self.net_kwargs.get("heads", 4), self.dropout)


@register("tra")
class TRARanker(_SeqRanker):
    def _make_net(self) -> nn.Module:
        return _TRANet(self.hidden, self.net_kwargs.get("routes", 3), self.dropout)
