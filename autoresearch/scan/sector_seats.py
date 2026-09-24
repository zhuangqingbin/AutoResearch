#!/usr/bin/env python3
"""行业席位(2026-09-24 可买性对齐 §2.3;裁定③ Claude 决定:接上涨侧、席位制、不做排序驱动)。

当日 `market.sector_healthy_top3`(资格门:n≥8 ∧ 资金门 ∧ 60 日中位 > −20 ∧ 倒 U 动量)入围的
行业里,取 `healthy_riser_mask ∧ ¬falling_knife_mask` 的成员,按当日 composite(偏好档)降序每行业
≤`per_sector` 只。剔 📌 / ST / 当日涨幅 ≥ CHASE_1D_PCT / 调用方 `exclude`。确定性、零 LLM。
它只保证这几只**被 L3 看见**(L1 强注、L2 保留行、pass1 强留),L3 的 B 条照常适用、不抬评级。
"""
from __future__ import annotations

import pandas as pd

from autoresearch.common.scoring import falling_knife_mask, healthy_riser_mask
from autoresearch.scan.l3.merge import CHASE_1D_PCT, _is_st
from autoresearch.scan.market import sector_healthy_top3

SECTOR_SEATS_FILENAME = "_sector_seats.json"
SECTOR_SEAT_GUARD = "sector_seat"


def pick_sector_seats(frame: pd.DataFrame | None, *, per_sector: int = 2, max_sectors: int = 3,
                      exclude: set[str] | None = None) -> list[dict]:
    need = {"code", "industry", "composite"}
    if frame is None or not len(frame) or not need.issubset(frame.columns):
        return []
    healthy, knife = healthy_riser_mask(frame), falling_knife_mask(frame)
    if healthy is None or knife is None:
        return []
    top = sector_healthy_top3(frame, k=int(max_sectors))
    if not top:
        return []
    d = frame.copy()
    d["code"] = d["code"].astype(str).str.zfill(6)
    keep = healthy.fillna(False) & ~knife.fillna(False)
    if "name" in d.columns:
        keep &= ~d["name"].map(_is_st)
    if "pct_1d" in d.columns:
        keep &= ~(pd.to_numeric(d["pct_1d"], errors="coerce") >= CHASE_1D_PCT)
    if "pinned" in d.columns:
        keep &= ~d["pinned"].map(lambda v: bool(v) if pd.notna(v) else False)
    if exclude:
        keep &= ~d["code"].isin({str(c).zfill(6) for c in exclude})
    d = d.loc[keep]
    out: list[dict] = []
    for row in top:
        ind = str(row["industry"])
        members = d[d["industry"].astype(str) == ind].copy()
        members["_c"] = pd.to_numeric(members["composite"], errors="coerce")
        members = (members.dropna(subset=["_c"])
                   .sort_values(["_c", "code"], ascending=[False, True]).head(int(per_sector)))
        for k, (_, m) in enumerate(members.iterrows(), start=1):
            out.append({"code": m["code"], "name": str(m.get("name", "") or ""), "industry": ind,
                        "rank_in_sector": k, "composite": float(m["_c"])})
    return out
