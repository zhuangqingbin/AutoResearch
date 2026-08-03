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
    "事件": ("event",),
}
# 事件桶 floor=0(Wave4 Review Round 1 C-1):event 通道**默认不启用**(不在 scan_config 的
# funnel.recall_channels 里)⇒ 桶恒无成员,但 floor 只要 >0 就会**先把 `merit_need` 减掉**
# (`merit_need = l2_n − Σfloor`),那部分名额落回 floor 补位/回填循环 ⇒ 每天恰好 10 行被打上
# `l2_lane_reserved=True`(「配额救回,非有机进场」)。该标签有三个真消费者:`l3_select._row_lane`
# (渲染进喂 l3-rank 的表)、`l4_card.force_full_card`(满卡/早停)、`retro.floor_experiment`
# (闭环账本分组)⇒ 挂着「未启用」的牌子却天天改生产 LLM 输入,违背「全默认关→parity 不破」。
# 真数据实测:floor=0 vs 「无事件桶」在 29 个扫描日 code/l2_rank/l2_lane_reserved 三列逐值一致。
# **启用 event 路时,把这个 0 改成 10 即可**(桶与 STYLE_CHANNELS 的映射已就位)。
DEFAULT_FLOORS: dict[str, int] = {"趋势": 20, "健康": 15, "反转": 12, "价值": 12,
                                  "成长": 12, "吸筹": 12, "主力": 10, "事件": 0}

# selection_reason 词表 —— **与 L3 pass1 共用一套**(design 2026-08-03 §3.1/§4.4;
# 定义点见 `scan/l3/triage.py`)。两层各造一套词表 = 两边的「lane」不是同一件事,
# O1 的 SLO 切片与 §4.4 的反事实分层会各自量到不同的东西。
#
# 本层的产生规则:
#   merit     ② sector-neutral composite 核(过 cap)
#   lane      ③ 风格桶 floor 救回(detail 记桶名)
#   backfill  ④ 回填到 l2_n(过 cap)
#   sector    ④' cap 卡死后**松 cap** 才收进来的 —— 行业集中度约束被放开的那一批
#   pinned    `select_l2` 的保送行(全程直通,不占竞争名额)
#
# 与既有 `l2_lane_reserved` 的关系:后者 = merit 核之外的**全部**(floor∪回填∪松cap∪保送),
# 是个二值旗且有三个真消费者;`selection_reason` 把那一团拆成四种不同的进场方式。
# 两者并存、语义不同,**不得互相替代**。
L2_SELECTION_REASONS = ("merit", "lane", "sector", "pinned", "backfill")


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

    ⚠️ **`regime` / `regime_caps` 是「已建未接线」的半特性**(design 2026-08-03 §3.3 O3
    清算)。函数体确实按 regime 调 sector cap(见下面 `cap_frac` 一行),单测也覆盖了它 ——
    但 `scan/universe.py` 的**全部**生产调用点(`:301/313/328/346/458`)都不传这两个参数,
    所以生产路径上它恒为 `None`,cap 恒等于 `sector_cap_frac`。

    挂着参数没人喂,下一个读代码的人会以为 regime 化已生效 —— 这正是 §0.3-5 点名的形态
    (同一波里 `gpJson`/`bash` 两个 workflow helper 漏搬也是它)。**P0 只补文档 + 探针,
    不接线**:真把 `_regime`/caps 接进去会改变 L2 构成,属 B 类 challenger,必须先补
    variant contract 再走 registry/replay(候选 `O3_regime_caps`)。
    探针见 `tests/scan/test_l2_regime_wiring_probe.py` —— 生产调用点一旦开始传它、
    而 registry 无对应 ACTIVE 实验,测试立刻变红。
    risk_off 只有 11 日 → 该 regime 恒 `IMMATURE`,不得靠全样本调参后声称分 regime 稳定。
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
        out["selection_reason"] = "merit"      # 没有竞争 → 全体都是有机进场
        out["selection_detail"] = ""
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
    reasons: dict[int, tuple[str, str]] = {}

    def _ok(idx: int) -> bool:                       # sector cap 检查
        return sec_cnt.get(ind.iloc[idx], 0) < cap

    def _add(idx: int, reason: str, detail: str = "") -> None:
        sel.append(idx)
        sel_set.add(idx)
        reasons[idx] = (reason, detail)
        sec_cnt[ind.iloc[idx]] = sec_cnt.get(ind.iloc[idx], 0) + 1

    total_floor = sum(floors.values())
    merit_need = max(0, l2_n - total_floor)
    for idx in order:                                # ② merit 核(sn top,过 cap)
        if len(sel) >= merit_need:
            break
        if idx not in sel_set and _ok(idx):
            _add(idx, "merit")

    for st in sorted(floors, key=lambda s: -floors[s]):   # ③ floor 补(大 floor 先)
        m = masks[st]
        have = sum(1 for i in sel if m.iloc[i])
        need = floors[st] - have
        for idx in order:
            if need <= 0:
                break
            if idx not in sel_set and m.iloc[idx] and _ok(idx):
                _add(idx, "lane", st)
                need -= 1

    if len(sel) < l2_n:                              # ④ 回填到 l2_n(过 cap)
        for idx in order:
            if len(sel) >= l2_n:
                break
            if idx not in sel_set and _ok(idx):
                _add(idx, "backfill")
    if len(sel) < l2_n:                              # cap 卡死 → 松 cap 兜底凑满
        for idx in order:
            if len(sel) >= l2_n:
                break
            if idx not in sel_set:
                # 行业集中度约束被放开才收进来的一批 —— O1 的集中度守卫要单独看它们
                _add(idx, "sector", str(ind.iloc[idx]))

    reserved = set(sel[merit_need:])                 # merit 核之外 = floor/回填救回
    kept = sel[:l2_n]
    out = r.loc[kept].copy()
    out["l2_lane_reserved"] = out.index.isin(reserved)
    # 按**原始行索引**取理由,不按 reset 后的位置 —— `kept` 是选择序不是行序,两者不相等。
    out["selection_reason"] = [reasons[i][0] for i in kept]
    out["selection_detail"] = [reasons[i][1] for i in kept]
    return out.drop(columns=["_sn"], errors="ignore").reset_index(drop=True)


def select_l2(recall: pd.DataFrame, l2_n: int, floors: dict[str, int] | None = None,
              sector_cap_frac: float = 0.20, regime: str | None = None, regime_caps: dict | None = None):
    """L2 选股编排(`universe.run` 与 `L2Rank` stage **共用** → golden parity)。

    返回 (l2_df, engine):l2_df 带 `l2_rank`(分层选择序)+ `l2_lane_reserved` + `sector_mom`(行业动量)
    + `gbdt_score`/`l2_score`(=composite,显示用,向后兼容旧列名)+ 召回列。确定性、零 LLM、无模型。
    `regime`+`regime_caps` 给定 → 按 regime 调 sector cap(默认 None=固定 cap=parity)。

    pinned 强留(design 2026-07-11 §4.1;plan Task 3):`recall` 若带 `pinned`(bool)列且有
    True 行 → 这些行**先抽出、完全不进分层采样的竞争池**(不占 l2_n、不因它们恰好达标与否
    改变对待——"全程直通"语义:即便某行凭 merit 本就能挤进 l2_n,也只走保送这一条路,不占
    竞争名额,故其余票的入选结果零影响),分层采样只在剩余票上跑,选完后**无条件**拼回末尾
    (l2_rank 接续编号、`l2_lane_reserved=True`,与 floor 救回同一"保底进场"语义)。无
    `pinned` 列 / 全 False → 原逻辑不变(presence-gated parity)。
    """
    has_pinned = "pinned" in recall.columns and bool(
        recall["pinned"].fillna(False).astype(bool).any())
    if has_pinned:
        mask = recall["pinned"].fillna(False).astype(bool)
        pinned_rows = recall[mask].copy()
        rest = recall[~mask].copy()
    else:
        rest = recall

    l2 = stratified_l2(rest, l2_n, floors=floors, sector_cap_frac=sector_cap_frac,
                       score_col="composite", regime=regime, regime_caps=regime_caps)
    l2.insert(0, "l2_rank", range(1, len(l2) + 1))

    if has_pinned:
        pinned_rows.insert(0, "l2_rank", range(len(l2) + 1, len(l2) + 1 + len(pinned_rows)))
        pinned_rows["l2_lane_reserved"] = True
        pinned_rows["selection_reason"] = "pinned"
        pinned_rows["selection_detail"] = ""
        l2 = pd.concat([l2, pinned_rows], ignore_index=True, sort=False)

    if "composite" in l2.columns:                    # 显示分(两条管道列名各异,都填 composite)
        l2["gbdt_score"] = l2["composite"]
        l2["l2_score"] = l2["composite"]
    return l2, "stratified(sn_composite)"
