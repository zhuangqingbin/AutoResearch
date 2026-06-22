#!/usr/bin/env python3
"""L2 champion 重排 —— 加载 l2_model champion,在召回帧上派生模型特征后 predict。

design: docs/specs/2026-06-22-l2-zoo-champion-design.md §L2 接线。

universe.run(live staging)与 L2Rank(typed trace)**共用**本 helper → 两条管道 L2 口径一致
(golden parity)。无 champion / predict 失败(如 seq/graph 视图在召回帧不可得)→ (None, 原因),
调用方回落(GBDT / composite,绝不比线性差)。champion 用 `_derive_model_features` 把召回帧补成
训练态特征(g_* + composite)再 predict,避免训练-推理特征错位。
"""
from __future__ import annotations

import sys


def champion_scores(frame, l2_model: str):
    """(scores: pd.Series|None, engine: str)。

    无 champion → (None, "no-champion(<name>)");predict 失败 → (None, "champion-failed(<name>)");
    成功 → (scores 与 frame 行对齐, "champion:<name>(<kind>)")。
    """
    from autoresearch.models import load_champion_any
    champ = load_champion_any(l2_model)
    if champ is None:
        return None, f"no-champion({l2_model})"
    try:
        from autoresearch.data.handler import _derive_model_features
        feats = _derive_model_features(frame)
        scores = champ.predict(feats)
        return scores, f"champion:{l2_model}({getattr(champ, 'kind', '?')})"
    except Exception as e:  # noqa: BLE001 — champion 不适用 / 反序列化坏 → 回落
        print(f"[L2] champion {l2_model} predict 失败({e!r})→ 回落 GBDT/线性", file=sys.stderr)
        return None, f"champion-failed({l2_model})"
