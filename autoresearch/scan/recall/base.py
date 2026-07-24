#!/usr/bin/env python3
"""recall channel 共用原语 —— gate_rank(过门 → 降序 top-k → 标准三列)。

design: docs/specs/2026-06-22-l1-multi-recall-design.md §架构。
每路 channel 都把「过门 + 排序 + 截断」收敛到这里,保证返回列契约一致。
"""
from __future__ import annotations

import pandas as pd

_COLS = ["code", "channel_rank", "channel_score"]


def empty_result() -> pd.DataFrame:
    """标准空召回帧(仍带三列)——通道要**显式**返回空时直接调这个。

    m-1:此前 event 路写 `gate_rank(frame, None, "__no_event__", k)`,即把「我要返回空」
    编码成「我去查一个必然不存在的列名」,意图绕道、且依赖 `gate_rank` 的兜底分支不变
    (若哪天按本 repo「缺列不许静默」的方向把它改成抛 DataContractError,那三条降级分支
    会一起变成异常路径,而 `build(n)(...)` 在 `recall_select` 里不被 suppress 包裹 = 整次
    扫描崩)。同一张空帧,直说即可。
    """
    return pd.DataFrame(columns=_COLS)


def gate_rank(frame: pd.DataFrame, mask, score_col: str, k: int) -> pd.DataFrame:
    """过 mask(None=不过门)→ 按 score_col 降序 → top-k → DataFrame[code, channel_rank(1..), channel_score]。

    缺 score_col / 空帧 / 过门后为空 → 空帧(仍带三列)。stable 排序保证确定性。
    """
    if score_col not in frame.columns or not len(frame):
        return empty_result()
    sub = frame if mask is None else frame[mask.fillna(False)]
    sub = sub[sub[score_col].notna()]
    if not len(sub):
        return empty_result()
    sub = sub.sort_values(score_col, ascending=False, kind="stable").head(k)
    return pd.DataFrame({
        "code": sub["code"].astype(str).str.zfill(6).to_numpy(),
        "channel_rank": range(1, len(sub) + 1),
        "channel_score": sub[score_col].astype(float).to_numpy(),
    }).reset_index(drop=True)
