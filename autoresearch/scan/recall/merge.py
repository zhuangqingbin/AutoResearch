#!/usr/bin/env python3
"""quota_union —— 多路 channel 名单的 pure quota union 合并(非 RRF)。

design: docs/specs/2026-06-22-l1-multi-recall-design.md §merge。
并集去重 → 每路 top-floor 无条件保留(多样性保证)→ 裁到恰 recall_n
(优先级 = n_channels desc, composite desc;非加权融合,只是 trim tiebreak)→
不足则从 base 按 composite backfill。provenance 列:recall_channels/n_channels/best_rank。
"""
from __future__ import annotations

import pandas as pd

_BIG = 10**9


def quota_union(channel_frames, defaults, recall_n, base_frame):
    """见模块 docstring。channel_frames: {name: [code,channel_rank,channel_score]}。"""
    base = base_frame.copy()
    base["code"] = base["code"].astype(str).str.zfill(6)

    chan_of: dict[str, set] = {}
    best_rank: dict[str, int] = {}
    protected: set[str] = set()
    long_rows = []
    for name, cf in channel_frames.items():
        if cf is None or not len(cf):
            continue
        floor = defaults[name].floor if name in defaults else 0
        for i, r in enumerate(cf.itertuples(index=False)):
            code = str(r.code).zfill(6)
            chan_of.setdefault(code, set()).add(name)
            best_rank[code] = min(best_rank.get(code, _BIG), int(r.channel_rank))
            long_rows.append({"channel": name, "code": code,
                              "channel_rank": int(r.channel_rank), "channel_score": float(r.channel_score)})
            if i < floor:
                protected.add(code)
    per_channel_long = pd.DataFrame(long_rows, columns=["channel", "code", "channel_rank", "channel_score"])

    union_codes = set(chan_of) & set(base["code"])
    protected &= union_codes
    prov = pd.DataFrame({"code": sorted(union_codes)})
    prov["recall_channels"] = prov["code"].map(lambda c: "|".join(sorted(chan_of[c])))
    prov["n_channels"] = prov["code"].map(lambda c: len(chan_of[c]))
    prov["best_rank"] = prov["code"].map(lambda c: best_rank[c])
    merged = base.merge(prov, on="code", how="inner")

    def _ranked(df):
        return df.sort_values(["n_channels", "composite"], ascending=[False, False], kind="stable")

    is_prot = merged["code"].isin(protected)
    prot_df, rest_df = _ranked(merged[is_prot]), _ranked(merged[~is_prot])
    chosen = pd.concat([prot_df, rest_df], ignore_index=True)

    if len(chosen) > recall_n:
        if len(prot_df) >= recall_n:
            chosen = prot_df.head(recall_n)
        else:
            chosen = pd.concat([prot_df, rest_df.head(recall_n - len(prot_df))], ignore_index=True)
    elif len(chosen) < recall_n:
        have = set(chosen["code"])
        extra = (base[~base["code"].isin(have)]
                 .sort_values("composite", ascending=False, kind="stable").head(recall_n - len(chosen)))
        extra = extra.assign(recall_channels="(backfill)", n_channels=0, best_rank=_BIG)
        chosen = pd.concat([chosen, extra], ignore_index=True)

    out = _ranked(chosen).reset_index(drop=True)
    return out, per_channel_long
