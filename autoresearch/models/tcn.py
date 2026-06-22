#!/usr/bin/env python3
"""TCN 序列排序器(膨胀因果卷积,seq feature_set)。

design: docs/specs/2026-06-22-autoresearch-arch-redesign-design.md §C(序列 zoo).
复用 rnn._SeqRanker 基类;net 用 levels 层膨胀因果卷积(右侧 chomp 保因果)→ 末步 → 打分。
"""
from __future__ import annotations

from torch import nn

from autoresearch.models.registry import register
from autoresearch.models.rnn import K, _reshape, _SeqRanker


class _CausalBlock(nn.Module):
    def __init__(self, cin: int, cout: int, kernel: int, dilation: int, dropout: float) -> None:
        super().__init__()
        self.pad = (kernel - 1) * dilation
        self.conv = nn.Conv1d(cin, cout, kernel, padding=self.pad, dilation=dilation)
        self.act = nn.ReLU()
        self.drop = nn.Dropout(dropout)
        self.down = nn.Conv1d(cin, cout, 1) if cin != cout else None

    def forward(self, x):
        y = self.conv(x)
        if self.pad:
            y = y[:, :, :-self.pad]            # chomp 右侧 → 因果、长度不变
        y = self.drop(self.act(y))
        return y + (x if self.down is None else self.down(x))


class _TCNNet(nn.Module):
    def __init__(self, hidden: int, levels: int, kernel: int, dropout: float) -> None:
        super().__init__()
        blocks = []
        cin = K
        for i in range(levels):
            blocks.append(_CausalBlock(cin, hidden, kernel, 2 ** i, dropout))
            cin = hidden
        self.tcn = nn.Sequential(*blocks)
        self.head = nn.Linear(hidden, 1)

    def forward(self, x):
        h = _reshape(x).transpose(1, 2)        # [N, K, W]
        h = self.tcn(h)                        # [N, hidden, W]
        return self.head(h[:, :, -1]).squeeze(-1)


@register("tcn")
class TCNRanker(_SeqRanker):
    def _make_net(self) -> nn.Module:
        return _TCNNet(self.hidden, self.net_kwargs.get("levels", 3),
                       self.net_kwargs.get("kernel", 3), self.dropout)
