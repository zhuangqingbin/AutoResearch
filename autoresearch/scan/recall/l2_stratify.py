#!/usr/bin/env python3
"""L2 确定性分层多样性采样器(ML-free)——取代 champion ML 排序 + 单风格 lane 配额。

设计:docs/specs/2026-06-25-l2-stratified-sampler-design.md。回测锚定(scratchpad/bt_*.py):
确定性 L2 无稳健 alpha(composite-top200 ≈ 0,regime 依赖),最优口径 = **sector-neutral composite**,
**分层免费**(strat ≈ top200)。故 L2 = 给 L3/L4 建均衡菜单的采样器,alpha 交 L3/L4。

桶 = recall_channels 的风格标签;桶内 + merit 核都按 sector-neutral composite 排;每风格固定 floor
(policy,非模型,保证不为 0);sector cap 控行业集中度。floors={} + cap=1.0 → 退化为 sn 单分 top-N。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# 风格桶 → recall channel(召回 provenance 已打标,零新增)。northbound/composite 不单列桶。
# 健康桶(2026-07-03):healthy 通道的票再由桶 floor 保底进 L2——通道进池、桶上菜,两级都补。
STYLE_CHANNELS: dict[str, tuple[str, ...]] = {
    "趋势": ("momentum", "heat"),
    "反转": ("reversal",),
    "价值": ("value",),
    "成长": ("growth",),
    "吸筹": ("accumulation",),
    "主力": ("main_fund",),
    "健康": ("healthy",),
}
DEFAULT_FLOORS: dict[str, int] = {"趋势": 20, "健康": 15, "反转": 12, "价值": 12,
                                  "成长": 12, "吸筹": 12, "主力": 10}


def sector_neutral(score: pd.Series, industry: pd.Series) -> pd.Series:
    """sector-neutral 分:composite − 申万一级组均值(去行业 beta;回测最优桶内口径)。"""
    s = pd.to_numeric(score, errors="coerce")
    if industry is None:
        return s
    sn = s - s.groupby(industry.values).transform("mean")
    return sn.fillna(s)            # 缺行业 → 退回原分


def _style_masks(channels: pd.Series) -> dict[str, pd.Series]:
    sets = channels.fillna("").map(lambda x: set(str(x).split("|")) - {""})
    return {st: sets.map(lambda cs, ch=set(ch): bool(cs & ch)) for st, ch in STYLE_CHANNELS.items()}


def stratified_l2(df: pd.DataFrame, l2_n: int = 200, floors: dict[str, int] | None = None,
                  sector_cap_frac: float = 0.20, score_col: str = "composite",
                  industry_col: str = "industry", regime: str | None = None,
                  regime_caps: dict | None = None) -> pd.DataFrame:
    """召回帧 → 分层采样的 l2_n 行(确定性)。返回选中行 + `l2_lane_reserved`(floor 补进来的=True)。

    算法:① sn = sector-neutral(score)② merit 核 = top(l2_n−Σfloor) by sn(过 sector cap)
    ③ 逐风格(floor 大的先)把不足 floor 的从线下按 sn 补 ④ 不足 l2_n → by sn 回填(必要时松 cap)。
    floors=None → DEFAULT_FLOORS;floors={} → 纯 sn top-N(无分层,parity 用)。
    """
    floors = DEFAULT_FLOORS if floors is None else floors
    r = df.reset_index(drop=True).copy()
    if "code" in r.columns:
        r["code"] = r["code"].astype(str).str.zfill(6)
    n = len(r)
    ind = r[industry_col] if industry_col in r.columns else pd.Series(["?"] * n, index=r.index)
    if "pct_60d" in r.columns:        # 行业动量(申万一级 median pct_60d):给 L3 补 sector-neutral 抹掉的行业 beta
        r["sector_mom"] = (pd.to_numeric(r["pct_60d"], errors="coerce")
                           .groupby(ind.values).transform("median").round(2))
    if n <= l2_n:
        out = r.copy()
        out["l2_lane_reserved"] = False
        return out
    r["_sn"] = sector_neutral(r[score_col], ind).fillna(-1e18).to_numpy()
    chan = r["recall_channels"] if "recall_channels" in r.columns else pd.Series([""] * n, index=r.index)
    masks = _style_masks(chan)
    order = list(r.sort_values("_sn", ascending=False, kind="stable").index)
    cap_frac = regime_caps[regime] if (regime and regime_caps and regime in regime_caps) else sector_cap_frac
    cap = l2_n + 1 if cap_frac >= 1.0 else int(np.floor(cap_frac * l2_n))

    sel: list[int] = []
    sel_set: set[int] = set()
    sec_cnt: dict = {}

    def _ok(idx: int) -> bool:                       # sector cap 检查
        return sec_cnt.get(ind.iloc[idx], 0) < cap

    def _add(idx: int) -> None:
        sel.append(idx)
        sel_set.add(idx)
        sec_cnt[ind.iloc[idx]] = sec_cnt.get(ind.iloc[idx], 0) + 1

    total_floor = sum(floors.values())
    merit_need = max(0, l2_n - total_floor)
    for idx in order:                                # ② merit 核(sn top,过 cap)
        if len(sel) >= merit_need:
            break
        if idx not in sel_set and _ok(idx):
            _add(idx)

    for st in sorted(floors, key=lambda s: -floors[s]):   # ③ floor 补(大 floor 先)
        m = masks[st]
        have = sum(1 for i in sel if m.iloc[i])
        need = floors[st] - have
        for idx in order:
            if need <= 0:
                break
            if idx not in sel_set and m.iloc[idx] and _ok(idx):
                _add(idx)
                need -= 1

    if len(sel) < l2_n:                              # ④ 回填到 l2_n(过 cap)
        for idx in order:
            if len(sel) >= l2_n:
                break
            if idx not in sel_set and _ok(idx):
                _add(idx)
    if len(sel) < l2_n:                              # cap 卡死 → 松 cap 兜底凑满
        for idx in order:
            if len(sel) >= l2_n:
                break
            if idx not in sel_set:
                _add(idx)

    reserved = set(sel[merit_need:])                 # merit 核之外 = floor/回填救回
    out = r.loc[sel[:l2_n]].copy()
    out["l2_lane_reserved"] = out.index.isin(reserved)
    return out.drop(columns=["_sn"], errors="ignore").reset_index(drop=True)


def select_l2(recall: pd.DataFrame, l2_n: int, floors: dict[str, int] | None = None,
              sector_cap_frac: float = 0.20, regime: str | None = None, regime_caps: dict | None = None):
    """L2 选股编排(`universe.run` 与 `L2Rank` stage **共用** → golden parity)。

    返回 (l2_df, engine):l2_df 带 `l2_rank`(分层选择序)+ `l2_lane_reserved` + `sector_mom`(行业动量)
    + `gbdt_score`/`l2_score`(=composite,显示用,向后兼容旧列名)+ 召回列。确定性、零 LLM、无模型。
    `regime`+`regime_caps` 给定 → 按 regime 调 sector cap(默认 None=固定 cap=parity)。
    """
    l2 = stratified_l2(recall, l2_n, floors=floors, sector_cap_frac=sector_cap_frac,
                       score_col="composite", regime=regime, regime_caps=regime_caps)
    l2.insert(0, "l2_rank", range(1, len(l2) + 1))
    if "composite" in l2.columns:                    # 显示分(两条管道列名各异,都填 composite)
        l2["gbdt_score"] = l2["composite"]
        l2["l2_score"] = l2["composite"]
    return l2, "stratified(sn_composite)"
