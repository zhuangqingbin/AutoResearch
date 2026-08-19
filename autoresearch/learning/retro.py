#!/usr/bin/env python3
"""闭环复盘 retro · 归因前一日 scan 报告 vs T+2 已实现涨跌(确定性,零 LLM)。

仅挂 scan-market。用当日已实现的**主尺** `ruler.MAIN_RULER`(现 `gap_c1_o2` = open[D+2]/close[D+1]−1,
超短隔夜:D 收盘信号 → D+1 **收盘**买、D+2 **开盘**卖、剔 D+1 收盘封板 `buyable_c1`;沿革:2026-08-05
换尺前是 `fwd_2_oc`(D+1 开盘买/D+2 收盘卖,旗 `buyable`),现与 `fwd_1_oo` 同为参考尺)检验 D 的报告,
把每只股票分桶:抓到 / L2-L3 误判 /
漏在 L1 / 漏在 L0 / 误买。产出 attribution.csv + retro_input.md,喂给 scan-retro skill 做 Claude 诊断
(系统性病因 + 自动重标定 + 经验/建议)。归因数学纯函数、可离线自测;取数复用 factor_lab。

用法:
  uv run --no-sync python -m autoresearch.learning.retro --selftest
  uv run --no-sync python -m autoresearch.learning.retro attribute 2026-06-19      # 单日(需 fwd 已实现)
  uv run --no-sync python -m autoresearch.learning.retro pending                   # 归因欠账+诊断欠账两段
  uv run --no-sync python -m autoresearch.learning.retro refresh                   # 补跑已成熟未归因日
  uv run --no-sync python -m autoresearch.learning.retro backfill-bought           # 历史 attribution 补 bought 列(幂等)
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

from autoresearch.agents.utils.rating import RATINGS_5_TIER, parse_rating
from autoresearch.common import workspace as ws
from autoresearch.common.ruler import (
    EXIT_FLAG,
    MAIN_RULER,
    REL_GAP_RULER,
    REL_MARKET,
    REL_SECTOR,
    entry_tradable,
)

# 保送/观察单直通/菜单滞回——不是 L3 当日选的票,不进「L3 选股成绩」头条(pr_20260716_002,
# 与 t1_review 同一裁定同一集合;后两种 lane 已退役但历史 scan 目录仍有存量行)。
from autoresearch.learning.t1_review import _NON_GENUINE_LANES

_BUY = ("Overweight", "Buy")
_RATING_RANK = {r: i for i, r in enumerate(RATINGS_5_TIER)}   # Buy0<OW1<Hold2<UW3<Sell4(小=看多)
_PAIR_DIFF_COLS = [("d_composite", "composite"), ("d_momentum", "score_momentum"),
                   ("d_main_net", "main_net_ratio"), ("d_winner_rate", "winner_rate"),
                   ("d_pct60", "pct_60d")]


# ───────────────────────── 纯函数:分桶 + 阶段统计(可离线自测) ─────────────────────────


def _as_bool(s: pd.Series) -> pd.Series:
    def one(v):
        if isinstance(v, str):
            return v.strip().lower() in ("true", "1", "yes", "是")
        try:
            return bool(v) and v == v  # 非 NaN
        except (TypeError, ValueError):
            return False
    return s.map(one)


def _rel_gap_cols(frame: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """rel_gap_market / rel_gap_sector(纯函数;`attribute_frame` 与历史回填共用同一算法,
    不许两处各写一份 —— 漂移只会在某一天被人发现)。

    T22(Wave12 E6-0,用户 2026-08-08 追加裁定的地基):系统对外只有一种 BUY——"今日可交易
    全集里相对最值得买"(不承诺绝对上涨)。相对基准 = 全市场可交易等权为主、行业中性超额
    为辅。

    I-4(final-review 2026-08-08/09):口径钉死在字面量 `ruler.REL_GAP_RULER`("gap_c1_o2"),
    **不**读动态 `MAIN_RULER`——批A 回滚杆把 `MAIN_RULER` 改回 `fwd_2_oc` 后这两列必须仍是
    gap 口径,不能新旧行静默混尺(详细动机见 `ruler.py` REL_GAP_RULER 旁的长注释)。

    基准分母 = 当日通过入场旗的可交易票(`ruler.entry_tradable(frame, ruler_name=
    REL_GAP_RULER)`,C1 同款单点选旗,但选旗口径同样钉死不随 MAIN_RULER 走)∧
    `REL_GAP_RULER` 有数。

    I-1(final-review 2026-08-08/09,人口裁定并留痕):分母是**全市场**可交易票(不区分是否
    过 L0/L1/L2 门,只要当日真能买 ∧ 主尺有数就入分母),不是仅 L0 过门的子集——任务书
    Interfaces 一度写「L0 可交易全集」,与用户裁定「全市场可交易等权」字面冲突,以用户裁定
    为准(裁定与理由见 `ruler.py` REL_MARKET/REL_SECTOR 旁的 I-1 长注释)。这不是"全市场
    所有行"(含停牌/涨停封死买不进的票仍会被 `entry_tradable` 剔出分母,C1 同款坑此列已经
    绕开)。行业均值在同一分母内再按 `industry` 分组;票本身缺行业 / 该行业当日无可交易
    成员 → `rel_gap_sector` = NaN,不猜(标签宁可留白,不像 `l2_stratify.sector_neutral`
    那样为了打分连续性回退全局值)。

    注意:分子(每行自己的 `REL_GAP_RULER`)不要求该行自己可交易 —— 一只票哪怕当天买不进,
    "它相对可执行市场基准表现如何"依然是有意义的读数(零买复盘/账本审计要看这个)。

    I-2:与 `l3_marginal.day_frame` 的 `excess_2`(L3 内部反事实读数的市场基准,中位、跟随
    MAIN_RULER)是两个刻意不同的市场基准,互指说明见 `ruler.py` 与 `l3_marginal.py` 两处。
    """
    gap = pd.to_numeric(frame[REL_GAP_RULER], errors="coerce")
    pool_mask = entry_tradable(frame, ruler_name=REL_GAP_RULER) & gap.notna()
    pool = gap[pool_mask]
    market_mean = float(pool.mean()) if len(pool) else float("nan")
    rel_market = gap - market_mean

    industry = frame["industry"] if "industry" in frame.columns \
        else pd.Series(pd.NA, index=frame.index, dtype=object)
    sector_mean = pool.groupby(industry[pool_mask].astype(str)).mean()
    rel_sector = (gap - industry.astype(str).map(sector_mean)).mask(industry.isna())
    return rel_market, rel_sector


def attribute_frame(l1: pd.DataFrame, realized: pd.DataFrame, buylist: dict,
                    abs_thresh: float = 0.03, top_q: float = 0.9, bot_q: float = 0.1) -> pd.DataFrame:
    """全市场已实现收益 × L1 全打分面板 × 报告买单 → 每只一个 bucket。纯函数(无 IO)。

    赢家 = 可交易 universe 内主尺(`ruler.MAIN_RULER`,现 `gap_c1_o2`)≥ 九分位 ∧ ≥ abs_thresh。
    """
    l1 = l1.copy()
    l1["code"] = l1["code"].astype(str).str.zfill(6)
    realized = realized.copy()
    realized["code"] = realized["code"].astype(str).str.zfill(6)
    bl = {str(k).zfill(6): v for k, v in buylist.items()}

    m = realized.merge(l1, on="code", how="left")               # base = 全市场(含漏在 L0 的)
    m["in_l1"] = m["composite"].notna() if "composite" in m.columns else m.get("rank").notna()
    m["recalled_flag"] = _as_bool(m["recalled"]) if "recalled" in m.columns else False
    m["rating"] = m["code"].map(bl)
    m["bought"] = m["rating"].isin(_BUY)
    # C1 修复(final-review 2026-08-08):入场旗必须跟随 MAIN_RULER 选腿 —— 旧 `buyable`
    # 只测 D+1 开盘一字板(fwd_2_oc 的入场腿是 D+1 开盘);换尺到 gap_c1_o2 后入场腿是 D+1
    # **收盘**,对应旗是 `buyable_c1`(entry_flag_for 单点选旗,不在此处写字面量)。
    m["tradable"] = entry_tradable(m) & m[MAIN_RULER].notna()
    # T22(E6-0):相对标签两列 —— 市场等权超额(主)+ 行业中性超额(辅)。纯函数,回填共用。
    m[REL_MARKET], m[REL_SECTOR] = _rel_gap_cols(m)

    trad = m[m["tradable"]]
    hi = trad[MAIN_RULER].quantile(top_q) if len(trad) else float("nan")
    lo = trad[MAIN_RULER].quantile(bot_q) if len(trad) else float("nan")
    m["winner"] = m["tradable"] & (m[MAIN_RULER] >= hi) & (m[MAIN_RULER] >= abs_thresh)
    # ruler tag(Wave11 头等大事):winner/bucket 用 MAIN_RULER 分类,写入那一刻的真值随行入账 ——
    # 不然 T16 flip 后旧行(fwd_2_oc 尺分类)与新行(gap_c1_o2 尺分类)的 winner/bucket 语义
    # 无法区分。历史行(本列上线前写的)没有这列,读侧按 `row.get("ruler", "fwd_2_oc")` 兜底。
    m["ruler"] = MAIN_RULER

    def bucket(r) -> str:
        if r["winner"] and r["bought"]:
            return "caught"
        if r["winner"] and r["recalled_flag"] and not r["bought"]:
            return "recalled_cut"
        if r["winner"] and r["in_l1"] and not r["recalled_flag"]:
            return "missed_l1"
        if r["winner"] and not r["in_l1"]:
            return "missed_l0"
        if r["bought"] and r["tradable"] and r[MAIN_RULER] <= lo:
            return "false_positive"
        return ""

    m["bucket"] = m.apply(bucket, axis=1)

    # T+5 参考口径(降级保留;spec 2026-07-02-scan-retro-depth-metrics §①):L3/L4 猎的是 swing,
    # 盲区审计与主尺(T+2)并存;fwd_5 未成熟(NaN)→ winner_5 全 False(retro 补跑成熟日覆写)。
    abs_thresh_5 = 0.05
    if "fwd_5_oc" in m.columns:
        t5 = m["buyable"].fillna(True) & m["fwd_5_oc"].notna()
        trad5 = m[t5]
        hi5 = trad5["fwd_5_oc"].quantile(top_q) if len(trad5) else float("nan")
        m["winner_5"] = t5 & (m["fwd_5_oc"] >= hi5) & (m["fwd_5_oc"] >= abs_thresh_5)
    else:
        m["winner_5"] = False

    def bucket5(r) -> str:
        if not r["winner_5"]:
            return ""
        if r["bought"]:
            return "caught"
        if r["recalled_flag"]:
            return "recalled_cut"
        return "missed_l1" if r["in_l1"] else "missed_l0"

    m["bucket_5"] = m.apply(bucket5, axis=1)
    return m


def floor_experiment(l2df: pd.DataFrame, attr: pd.DataFrame) -> dict:
    """L2 风格 floor 自然实验(spec §③):floor 救回 vs merit 入选 vs 被挤掉 的 fwd 对照。纯函数。

    组:floor=`l2_lane_reserved>0`;merit=L2 内其余;cut=召回(top1000)但没进 L2。
    返回 {组: {n, fwd1, fwd5}};缺列/空 → 组 n=0。
    """
    a = attr.copy()
    a["code"] = a["code"].astype(str).str.zfill(6)
    l2codes: set[str] = set()
    floor_codes: set[str] = set()
    if l2df is not None and len(l2df) and "code" in l2df.columns:
        l2 = l2df.copy()
        l2["code"] = l2["code"].astype(str).str.zfill(6)
        l2codes = set(l2["code"])
        if "l2_lane_reserved" in l2.columns:
            rsv = pd.to_numeric(l2["l2_lane_reserved"], errors="coerce").fillna(0)
            floor_codes = set(l2.loc[rsv > 0, "code"])
    recalled = a["recalled_flag"].fillna(False) if "recalled_flag" in a.columns \
        else pd.Series(False, index=a.index)
    groups = {"floor": a["code"].isin(floor_codes),
              "merit": a["code"].isin(l2codes - floor_codes),
              "cut": recalled & ~a["code"].isin(l2codes)}

    def _agg(mask) -> dict:
        sub = a[mask]
        f1 = pd.to_numeric(sub.get("fwd_1_oo"), errors="coerce") if len(sub) else pd.Series(dtype=float)
        f5 = pd.to_numeric(sub.get("fwd_5_oc"), errors="coerce") if len(sub) else pd.Series(dtype=float)
        return {"n": int(len(sub)),
                "fwd1": round(float(f1.mean()), 6) if f1.notna().any() else None,
                "fwd5": round(float(f5.mean()), 6) if f5.notna().any() else None}

    return {k: _agg(v) for k, v in groups.items()}


def l3_miss_autopsy(attr: pd.DataFrame, l2df: pd.DataFrame, finalists: pd.DataFrame,
                    judged: pd.DataFrame, top_n: int = 10) -> pd.DataFrame:
    """L3 错杀验尸(spec §②):L2-keep ∧ 非 finalist ∧ winner(T+2 主尺) → join L3 判分(当时的红队理由)。纯函数。"""
    cols = ["code", "name", MAIN_RULER, "thesis", "risk", "triage_lean", "lane",
            "conviction", "fragility"]
    if judged is None or not len(judged) or attr is None or not len(attr):
        return pd.DataFrame(columns=cols)

    def _codes(df) -> set[str]:
        return set(df["code"].astype(str).str.zfill(6)) if df is not None and len(df) and "code" in df.columns else set()

    a = attr.copy()
    a["code"] = a["code"].astype(str).str.zfill(6)
    j = judged.copy()
    j["code"] = j["code"].astype(str).str.zfill(6)
    pool = _codes(l2df) - _codes(finalists)
    w2 = a.get("winner", pd.Series(False, index=a.index)).fillna(False)
    miss = a[w2 & a["code"].isin(pool)]
    out = miss.merge(j, on="code", how="inner", suffixes=("", "_j"))
    out = out.sort_values(MAIN_RULER, ascending=False).head(top_n)
    return out[[c for c in cols if c in out.columns]].reset_index(drop=True)


def build_retro_pairs(attr: pd.DataFrame, max_pairs: int = 20) -> pd.DataFrame:
    """M1·同日配对蒸馏:构造 ExpeL 式 fail/success 对(控制变量=同日 → regime/地形/漏斗参数恒定)。

    fail 侧 = 评级最高档但 T+2 跌(有 bought=OW/Buy 则用之;**0 买日**退化到当日最高评级档的下跌者);
    success 侧 = 同日被门拦/漏召回(bucket ∈ missed_l0/l1)但 T+2 涨(winner)。
    贪心配对:worst-fail 先,同 industry 最近邻优先(matched_on=industry),无则放宽全局(=global),success 各用一次。
    输出每对带因子差(fail − success),diff 只剩标的特征与判断 → 喂 Claude 蒸馏,走 M2 `adjudicate` 落库。
    fwd_2 与主归因同尺、D+2 即成熟 → 配对当日可产,不再等 T+5(缺 fail 或 success 侧仍返回空表,优雅)。
    """
    empty = pd.DataFrame()
    if attr is None or attr.empty or MAIN_RULER not in attr.columns:
        return empty
    a = attr.copy()
    a["_fwd2"] = pd.to_numeric(a[MAIN_RULER], errors="coerce")
    if a["_fwd2"].notna().sum() == 0:                        # fwd_2 未成熟
        return empty
    # fail 侧只在**真被 L4 评级过**的票里选(rating ∈ 五档);未评级 universe 票即便暴跌也非判断失败
    rated = a[a.get("rating", pd.Series(dtype=str)).astype(str).isin(set(RATINGS_5_TIER))] \
        if "rating" in a.columns else a.iloc[0:0]
    rated = rated.copy()
    if not rated.empty:
        rated["_rank"] = rated["rating"].map(_RATING_RANK)
    # bought(OW/Buy)优先;0 买日退化到当日最高评级档(_rank 最小)present
    bought = rated[rated["rating"].isin(_BUY)] if not rated.empty else rated
    fail_pool = bought if not bought.empty else (
        rated[rated["_rank"] == rated["_rank"].min()] if not rated.empty else rated)
    fails = fail_pool[fail_pool["_fwd2"] < 0].sort_values("_fwd2")     # 跌得最狠先配

    # success 侧:同日被门拦/漏召回但 T+2 涨
    miss = a.get("bucket", "").isin(["missed_l0", "missed_l1"]) if "bucket" in a.columns else False
    win = a.get("winner", False).fillna(False).astype(bool) if "winner" in a.columns else False
    succ = a[miss & win].sort_values("_fwd2", ascending=False)
    if fails.empty or succ.empty:
        return empty

    used: set[str] = set()
    rows: list[dict] = []
    for _, f in fails.iterrows():
        if len(rows) >= max_pairs:
            break
        pool = succ[~succ["code"].isin(used)]
        if pool.empty:
            break
        same = pool[pool.get("industry") == f.get("industry")]
        w = same.iloc[0] if not same.empty else pool.iloc[0]
        used.add(w["code"])
        rec = {"fail_code": f["code"], "fail_name": f.get("name"), "fail_rating": f.get("rating"),
               "fail_fwd2": round(float(f["_fwd2"]), 4), "win_code": w["code"], "win_name": w.get("name"),
               "win_bucket": w.get("bucket"), "win_fwd2": round(float(w["_fwd2"]), 4),
               "industry": f.get("industry"),
               "matched_on": "industry" if (not same.empty) else "global",
               "ruler": MAIN_RULER}      # fail_fwd2/win_fwd2 固定列名但取值来自 MAIN_RULER,同族陷阱,一并打标
        for dcol, src in _PAIR_DIFF_COLS:                    # 因子差 = fail − success(控制变量对比)
            if src in a.columns:
                fv, wv = pd.to_numeric(pd.Series([f.get(src), w.get(src)]), errors="coerce")
                rec[dcol] = round(float(fv - wv), 4) if pd.notna(fv) and pd.notna(wv) else None
        rows.append(rec)
    return pd.DataFrame(rows)


_GUARD_OPS = {">": lambda s, t: s > t, ">=": lambda s, t: s >= t, "<": lambda s, t: s < t,
              "<=": lambda s, t: s <= t, "==": lambda s, t: s == t}


def mtm_check_guards(attr: pd.DataFrame, lessons: list[dict], day: str,
                     min_n: int = 5, apply: bool = True) -> list[dict]:
    """R2·经验 MTM 机判:带 guard 的经验,其条件组当日 fwd_2(超短主尺)对市场的 excess → support/refute。

    guard 全是"拦买"型 → 满足组跑输市场(excess<0)= 拦得对 = support;跑赢 = refute。
    n<min_n → skip(样本不足不判)。apply=True → 判定即调 feedback_store.mtm_update
    (confidence 机械升降;达阈自动提名摘门/退休,人批)。
    """
    import autoresearch.learning.feedback_store as fs

    out: list[dict] = []
    # T16 硬化(同 gate_attribution._day_facts 的修法):缺 MAIN_RULER 列时 `attr.get(...)`
    # 返回 None,`pd.to_numeric(None)` 退化成标量 NaN 而非 Series,下游 `.notna()` 会崩。
    mkt = pd.to_numeric(attr[MAIN_RULER], errors="coerce") if MAIN_RULER in attr.columns \
        else pd.Series(float("nan"), index=attr.index)
    for lsn in lessons:
        gd = lsn.get("guard")
        if not isinstance(gd, dict) or gd.get("field") not in attr.columns:
            continue
        op = _GUARD_OPS.get(gd.get("op"))
        thr = pd.to_numeric(pd.Series([gd.get("value")]), errors="coerce").iloc[0]
        if op is None or pd.isna(thr):
            continue
        vals = pd.to_numeric(attr[gd["field"]], errors="coerce")
        sub = mkt[op(vals, float(thr)).fillna(False) & mkt.notna()]
        n = int(len(sub))
        if n < min_n or not mkt.notna().any():
            out.append({"id": lsn.get("id"), "n": n, "excess": None, "verdict": "skip"})
            continue
        excess = float(sub.mean() - mkt.mean())
        verdict = "support" if excess < 0 else "refute"
        out.append({"id": lsn.get("id"), "n": n, "excess": round(excess, 6), "verdict": verdict})
        if apply:
            fs.mtm_update(lsn.get("id", ""), verdict, day=day,
                          note=f"机判 {day}: n={n} excess={excess:+.4f}")
    return out


def gate_audit(attr: pd.DataFrame, scan_dir: Path | str) -> pd.DataFrame:
    """R3·门审计:gate_fires.csv × 已实现 fwd → 被拦票后来怎么走(excess<0 = 拦对)。纯读。

    T+1/T+2/T+5 三档并列展示(ex1/ex2/ex5),不像 attribute_frame 那样单挑主尺——
    此函数下方 fwd_2_oc/fwd_5_oc 字面量固定,勿随主尺漂移(与「主尺」无关的多档对照)。
    """
    cols = ["code", "check", "severity", "fwd_1_oo", "ex1", "fwd_2_oc", "ex2", "fwd_5_oc", "ex5"]
    p = Path(scan_dir) / "gate_fires.csv"
    if not p.exists():
        return pd.DataFrame(columns=cols)
    fires = pd.read_csv(p, dtype={"code": str})
    fires = fires[fires["code"].astype(str).str.len() > 0].copy()
    if not len(fires):
        return pd.DataFrame(columns=cols)
    fires["code"] = fires["code"].astype(str).str.zfill(6)
    a = attr.copy()
    a["code"] = a["code"].astype(str).str.zfill(6)
    m1 = pd.to_numeric(a["fwd_1_oo"], errors="coerce").mean()
    m2 = pd.to_numeric(a.get("fwd_2_oc"), errors="coerce").mean() if "fwd_2_oc" in a.columns else float("nan")
    m5 = pd.to_numeric(a.get("fwd_5_oc"), errors="coerce").mean() if "fwd_5_oc" in a.columns else float("nan")
    out = fires.merge(a[[c for c in ("code", "fwd_1_oo", "fwd_2_oc", "fwd_5_oc") if c in a.columns]],
                      on="code", how="left")
    out["ex1"] = pd.to_numeric(out.get("fwd_1_oo"), errors="coerce") - m1
    out["ex2"] = (pd.to_numeric(out.get("fwd_2_oc"), errors="coerce") - m2) if "fwd_2_oc" in out.columns else None
    out["ex5"] = (pd.to_numeric(out.get("fwd_5_oc"), errors="coerce") - m5) if "fwd_5_oc" in out.columns else None
    return out[[c for c in cols if c in out.columns]]


def flag_news_pop(attr: pd.DataFrame, gap_thresh: float = 0.07) -> pd.DataFrame:
    """赢家里"隔夜大跳空"(gap_d1 ≥ 阈值)= 多为消息/事件脉冲,不可预测 →

    标 news_pop,诊断与重标定**排除**之(别拿不可预测脉冲惩罚打分)。纯函数。
    """
    attr = attr.copy()
    if "gap_d1" in attr.columns:
        attr["news_pop"] = attr["winner"] & (pd.to_numeric(attr["gap_d1"], errors="coerce") >= gap_thresh)
    else:
        attr["news_pop"] = False
    return attr


def stage_stats(attr: pd.DataFrame) -> dict:
    """漏斗各段对赢家的存活率 + 买单命中率 + 当日 composite IC。纯函数。"""
    winners = attr[attr["winner"]]
    nW = len(winners)
    bought = attr[attr["bought"]]
    nB = len(bought)
    res = {
        "n_universe_realized": int(attr["tradable"].sum()),
        "n_winners": int(nW),
        "winners_in_l1": int(winners["in_l1"].sum()),
        "winners_recalled": int(winners["recalled_flag"].sum()),
        "winners_bought": int(winners["bought"].sum()),
        "buylist_n": int(nB),
        "buylist_hit": int(bought["winner"].sum()),
        "buylist_fp": int((attr["bucket"] == "false_positive").sum()),
        "buckets": {k: int(v) for k, v in attr["bucket"].value_counts().items() if k},
        "n_news_pop": int(attr.get("news_pop", pd.Series([], dtype=bool)).fillna(False).sum()),
    }
    res["n_winners_systematic"] = res["n_winners"] - res["n_news_pop"]   # 剔消息脉冲后的"可归因漏判"基数
    res["winner_to_l1"] = round(res["winners_in_l1"] / nW, 3) if nW else None
    res["winner_to_buylist"] = round(res["winners_bought"] / nW, 3) if nW else None
    res["buylist_hitrate"] = round(res["buylist_hit"] / nB, 3) if nB else None
    sub = attr[attr["tradable"] & attr.get("composite", pd.Series(dtype=float)).notna()]
    if len(sub) >= 30:
        res["day_ic_composite"] = round(sub["composite"].rank().corr(sub[MAIN_RULER].rank()), 4)
    else:
        res["day_ic_composite"] = None
    return res


# ───────────────────────── IO:买单 / 已实现收益 / 待复盘日 ─────────────────────────


def _report_dir_for(date: str, report_root: Path) -> Path | None:
    """定位数据日 analysis_date=date 的已发布报告目录(最新一轮)。

    新布局目录名 = **运行时刻**(与数据日解耦),数据日记在 `manifest.json` → 按 `analysis_date` 匹配;
    老布局目录名 = 数据日(无 manifest)→ glob `<date>_*` 兜底。都取目录名最大(= 最近运行)。
    """
    compact = date.replace("-", "")
    cands: set[Path] = set()
    for mf in report_root.glob("*/manifest.json"):
        try:
            if json.loads(mf.read_text(encoding="utf-8")).get("analysis_date") == date:
                cands.add(mf.parent)
        except (json.JSONDecodeError, OSError):
            continue
    cands |= set(report_root.glob(f"{compact}_*"))                 # 老布局:目录名即数据日
    dirs = sorted((p for p in cands if (p / "details").is_dir()), key=lambda p: p.name)
    return dirs[-1] if dirs else None


def _buylist(date: str, report_root: Path | None = None,
             scan_dir: Path | None = None) -> dict[str, str]:
    """评级 buylist:{code: 五档评级}。

    P0-2(坏账③修复):优先读 `<scan_dir>/_final_ratings.json`(assemble 发布时落的
    ensemble/verify **折回后终评级**)——此前直接解析已发布报告卡面文本,Tier-3 红队降级/
    否决 + 买单 ensemble 折回都只改了 assemble 内存里的评级,从未写回卡片文件,导致被折回的
    OW 仍以卡面 OW 进 attribution,污染 `bought`/评级基率(06-30 胜宏实证;STAGES.md 开放
    线头 #6)。presence-gated:缺 `scan_dir` 入参/缺文件/坏 json/空表 → 回退**卡面解析**
    (读已发布报告 `details/*.md` 的 `parse_rating`,现行为不变)。

    目录名现在是运行时刻(数据日在 manifest),由 `_report_dir_for` 解析定位;发布层卡名是**名称**,
    code 从卡内标题 `# 决策卡 — <code> <名称>` 取(复用 parse_rating 提评级)。
    """
    if scan_dir is not None:
        from autoresearch.scan.decision_read_model import read_final_ratings

        ratings = read_final_ratings(scan_dir)
        if ratings:
            return ratings
    rdir = _report_dir_for(date, report_root or ws.reports_root() / "scan")
    if rdir is None:
        return {}
    out: dict[str, str] = {}
    for md in (rdir / "details").glob("*.md"):
        text = md.read_text(encoding="utf-8")
        m = re.search(r"决策卡\s*[—\-]\s*(\d{6})", text)
        if m:
            out[m.group(1).zfill(6)] = parse_rating(text)
    return out


def realized_returns(date: str, fwd: int = 10) -> pd.DataFrame:
    """全市场 D 的已实现 fwd_1_oo/fwd_2_oc/fwd_5_oc + buyable(复用 factor_lab;按需拉 D..D+fwd 的 daily)。

    fwd 未实现(D+2 交易日还没到)→ 返回空(供 pending 判定)。

    Wave11-A3:`gap_c1_o2`/`buyable_c1`/`unsellable_o2`(隔夜尺三列,T11 已让
    `factor_lab.forward_returns` 产出)此前被下面的白名单过滤掉,归因/回填两侧都读不到——
    现在透传,是 `refresh_attributions` 历史回填 gap 列的唯一数据来源。
    """
    import autoresearch.research.factor_lab as fl
    from autoresearch.data.tushare_source import _trade_days

    cols = ["code", "fwd_1_cc", "fwd_1_oo", "fwd_2_oc", "fwd_5_oc", "fwd_10_oc", "hi_2_oc", "hi_10_oc",
            "buyable", "gap_d1", "gap_c1_o2", "buyable_c1", "unsellable_o2"]
    # ↑ 全部历史/参考尺字面量列表(FWDS 同族),固定列名,勿随主尺漂移;fwd_10/hi_10 供买后管理(未成熟=NaN)
    # gap_c1_o2/buyable_c1/unsellable_o2:隔夜尺三列(Wave11 批A);后两者是 pandas 可空
    # boolean(<NA>=未知,见 factor_lab.forward_returns 2026-08-07 review fix),本函数只透传
    # 原样返回,不在此处做任何布尔判读 —— 消费方若要用真值需自行绕开 `_bool_series` 族陷阱
    # (T11 修的病:naive `.astype(str)` 把 `<NA>` 静默读成 "False")。
    # fwd_1_cc(C2 修复,final-review 2026-08-08):v4 触价 rebase 基——buy_ledger.target_hit_for
    # 的 gap_c1_o2 分支要把目标幅(close[D] 基)rebase 到 c1=close[D+1] 基,用的正是这一列
    # (=close[D+1]/close[D]−1);此前不在白名单里,该 bug 没法修就是因为这一列压根没落盘过。
    pro = fl._pro()
    d0 = date.replace("-", "")
    today = datetime.now().strftime("%Y%m%d")
    fdays = _trade_days(pro, d0, today)
    if not fdays or fdays[0] != d0 or len(fdays) < 3:     # D 非交易日 / fwd 未实现
        return pd.DataFrame(columns=cols)
    P = fdays[:fwd + 2]
    for d in P:
        fl._cache("daily", d, fl._fetch(pro, "daily", d))
    piv = fl.load_price_pivots(P)
    fr = fl.forward_returns(piv, P, d0, fwd).reset_index()
    fr = fr.rename(columns={fr.columns[0]: "code"})
    op, cl = piv["open"], piv["close"]
    gap = (op[P[1]] / cl[P[0]] - 1.0).reset_index()       # D+1 开盘相对 D 收盘的隔夜跳空
    gap.columns = ["code", "gap_d1"]
    fr = fr.merge(gap, on="code", how="left")
    hs = piv.get("high")                                   # 触价口径:D+1..D+10 最高 / D+1 开盘(同 fwd_10_oc 基)
    win = [d for d in P[1:11] if hs is not None and d in hs.columns]
    if win and P[1] in op.columns:
        hi = (hs[win].max(axis=1) / op[P[1]] - 1.0).reset_index()
        hi.columns = ["code", "hi_10_oc"]
        fr = fr.merge(hi, on="code", how="left")
    fr["code"] = fr["code"].astype(str).str.zfill(6)
    return fr[[c for c in cols if c in fr.columns]]


def _ready_scan_days(today: str, scan_root: Path, report_root: Path) -> list[str]:
    """候选 scan 日:有 L1 面板 + 有报告 + D 的 fwd 已实现(不问 attribution/done 状态)。

    `attribution_pending()`(欠归因)与 `pending_days()`(欠诊断)共用的候选集合基座——
    Wave11-A7 拆账前两者被 `pending_days()` 一个函数混着算,现在只是同一份候选集合上
    叠两层不同的"具体欠什么"过滤,判据本身(L1 面板/报告/fwd 成熟)不重复写第二遍。
    """
    if not scan_root.exists():
        return []
    import autoresearch.research.factor_lab as fl
    from autoresearch.data.tushare_source import _trade_days

    pro = fl._pro()
    cal = _trade_days(pro, "20240101", today.replace("-", ""))   # 日历已截到 today
    pos = {d: i for i, d in enumerate(cal)}
    out = []
    for dd in sorted(p for p in scan_root.iterdir() if p.is_dir()):
        date = dd.name
        if not (dd / "L1_scored_full.csv").exists():
            continue
        if _report_dir_for(date, report_root) is None:           # 无已发布报告(目录名=运行日,按 manifest 定位)
            continue
        i = pos.get(date.replace("-", ""))
        if i is not None and i + 2 < len(cal):                   # D+2 交易日 ≤ today → fwd 已实现
            out.append(date)
    return out


def attribution_pending(today: str | None = None, scan_root: Path | None = None,
                        report_root: Path | None = None) -> list[str]:
    """真欠归因的 scan 日:候选日(有 L1 面板 + 有报告 + fwd 已实现)∧ 无 `retro/attribution.csv`。

    这批日子需要跑一次 `attribute()`(确定性计算,零 LLM;`nightly_close._retro_refresh`
    每晚会对候选集合幂等重跑,漏跑才会在这里露出来)。跟 `pending_days()`(诊断欠账——
    归因已备料,只差人/LLM 看一眼)是两笔不同的账:2026-08-05 实测 07-27..08-03 六日的
    确定性归因已全补齐,`retro pending` 却仍报欠 6 天,正是这两笔账被混成一份列表的代价
    (task-19)。正常情况下(夜间批已跑过)这份列表应为空;非空说明夜间批漏跑、或刚发布
    还没到夜间窗口。
    """
    today = today or datetime.now().strftime("%Y-%m-%d")
    scan_root = scan_root or ws.scan_root()
    report_root = report_root or ws.reports_root() / "scan"
    days = _ready_scan_days(today, scan_root, report_root)
    return [d for d in days if not (scan_root / d / "retro" / "attribution.csv").exists()]


def pending_days(today: str | None = None, scan_root: Path | None = None,
                 report_root: Path | None = None) -> list[str]:
    """未复盘 scan 日 = 诊断欠账:候选日(有 L1 面板 + 有报告 + fwd 已实现)∧ 无 `retro/done.json`。

    **名字与判据自 Wave7 起未变**——nightly_close/prelude 等既有调用点行为不受本次改动
    影响,这里只是把语义写实(Wave11-A7):本函数从来只回答"有没有 done.json",不回答
    "有没有 attribution.csv"(真正的归因欠账见 `attribution_pending()`)。之所以过去能被
    当"诊断欠账"用而不出岔子,是因为 `nightly_close._retro_refresh` 每晚都会把候选日的
    归因幂等补满——人早上看到这份列表时归因通常已经跑完,列表里剩的确实只是"还没人看"
    (scan-retro 诊断 + `mark_done`)。若夜间批漏跑,这份列表会暂时把"欠归因"的日子也
    带进来,此时应先查 `attribution_pending()`。
    """
    today = today or datetime.now().strftime("%Y-%m-%d")
    scan_root = scan_root or ws.scan_root()
    report_root = report_root or ws.reports_root() / "scan"
    days = _ready_scan_days(today, scan_root, report_root)
    return [d for d in days if not (scan_root / d / "retro" / "done.json").exists()]


# ───────────────────────── 编排:attribute / retro_input / done ─────────────────────────

_KEEP = ["code", "name", "industry", "bucket", "winner", "news_pop",
         "buyable", "tradable", "fwd_1_cc", "fwd_1_oo", "fwd_2_oc", "hi_2_oc",
         "fwd_5_oc", "fwd_10_oc", "hi_10_oc", "winner_5", "bucket_5",
         "gap_d1", "gap_c1_o2", "buyable_c1", "unsellable_o2",
         REL_MARKET, REL_SECTOR,
         "rank", "recalled_flag", "composite", "score_momentum", "score_fund_main",
         "score_chip", "pct_60d", "main_net_ratio", "winner_rate", "price_to_cost", "rsi6", "rating", "bought",
         "process_score", "ruler"]   # P0-4:逐卡过程分(presence-gated join,见 _join_process_score)
# ↑ attribution.csv 的落盘 schema(FWDS 同族,fwd_2_oc 等列名固定,勿随主尺漂移):这是跨多日
# 拼接读取的持久化 CSV 表头,若随 MAIN_RULER 改名,旧日期的历史行会与新日期错列、读出全 NaN
# (T16 换尺需要新增列而非在此重命名,由该次改动自行处理落盘 schema 演进)。gap_c1_o2/
# buyable_c1/unsellable_o2:隔夜尺三列(Wave11 批A,历史回填见 refresh_attributions);
# fwd_1_cc:C2 修复(final-review 2026-08-08)v4 触价 rebase 基,新收编 —— 本次修复前写的
# v4 日期历史行没有这一列;`target_hit_for` 缺列时 `gap` 读 NaN,退化成 `t_entry=tr`(不
# rebase,直接拿 close 基目标幅比 c1 基触价列),不是拿 v3 分支的 `gap_d1` 冒充,也不会诚实
# 标 None——这是历史行的已知过渡期 degradation(hi 本身早在 T11 就已收编,不缺;缺的只是
# rebase 基),不追溯重算旧行,新写的行起就是对的。
# ruler:本行 winner/bucket 是在哪个 MAIN_RULER 下分类的(写入那一刻的真值),历史行缺此列
# → 读侧 `row.get("ruler", "fwd_2_oc")` 兜底(旧行诚实标旧尺,不假装未知)。
# rel_gap_market/rel_gap_sector:T22(Wave12 E6-0)相对标签两列,`_rel_gap_cols` 现算,
# 与 T16(selection_reason/detail)同一类洞——算出来不落白名单等于没算,历史回填见
# `_backfill_rel_gap_columns`/`refresh_attributions`。


def refine_l3_bucket(attr: pd.DataFrame, sdir: Path) -> pd.DataFrame:
    """细分 `recalled_cut` 桶(design: plan 2026-07-12-l3-merge-plan.md Task 5):L3 两遍法产两个
    影子账本——pass1 分诊即切的 `_l3_pass1_cut.csv`、l3-rank judged 但未晋级 finalist 的
    `_l3_bench.csv`。原先笼统落 `recalled_cut` 的赢家(召回了却没被买)现按命中哪个影子文件
    细分成 `l3_bench`/`pass1_cut`——归因更贴近"漏在哪一步"(分诊就切 vs 判过没入选)。

    命中优先序:bench 先于 pass1_cut(两文件结构上不重叠——一票只会出现在其一,顺序只为
    防御性兜底)。任一文件缺失 → 该文件对应的码集合为空,不参与重打标(不要求"两个都在
    才生效");**两文件都不存在/都空**(旧日期、two_pass 关闭、或 Task2 bench 落地前)→
    原样返回,`recalled_cut` 现行为不变,presence-gated、零多算。只读,不写文件;只改
    `recalled_cut` 行,其余 bucket(`caught`/`missed_l0`/`missed_l1`/`false_positive`/空)不动。
    """
    out = attr.copy()
    if "bucket" not in out.columns or "code" not in out.columns:
        return out

    def _codes(fn: str) -> set[str]:
        p = Path(sdir) / fn
        if not p.exists():
            return set()
        try:
            df = pd.read_csv(p, dtype={"code": str})
        except Exception:  # noqa: BLE001
            return set()
        if not len(df) or "code" not in df.columns:
            return set()
        return set(df["code"].astype(str).str.zfill(6))

    bench_codes = _codes("_l3_bench.csv")
    cut_codes = _codes("_l3_pass1_cut.csv")
    if not bench_codes and not cut_codes:
        return out                                  # 两文件缺失/皆空 → 现行为不变

    codes = out["code"].astype(str).str.zfill(6)
    is_rc = out["bucket"] == "recalled_cut"
    out.loc[is_rc & codes.isin(bench_codes), "bucket"] = "l3_bench"
    still_rc = out["bucket"] == "recalled_cut"
    out.loc[still_rc & codes.isin(cut_codes), "bucket"] = "pass1_cut"
    return out


def _join_process_score(attr: pd.DataFrame, sdir: Path) -> pd.DataFrame:
    """P0-4:`<sdir>/process_scores.csv`(逐卡过程分 checklist)→ attribution 加
    `process_score` 列(presence-gated join)。

    缺文件/空表/缺列 → 原样返回,不加列(老路不破;调用方按 `"process_score" in attr.columns`
    判断该日是否有过程分读数)。"""
    p = Path(sdir) / "process_scores.csv"
    if not p.exists():
        return attr
    try:
        ps = pd.read_csv(p, dtype={"code": str})
    except Exception:  # noqa: BLE001
        return attr
    if not len(ps) or "code" not in ps.columns or "process_score" not in ps.columns:
        return attr
    ps = ps[["code", "process_score"]].copy()
    ps["code"] = ps["code"].astype(str).str.zfill(6)
    out = attr.copy()
    out["code"] = out["code"].astype(str).str.zfill(6)
    return out.merge(ps, on="code", how="left")


def attribute(date: str, scan_root: Path | None = None, report_root: Path | None = None,
              abs_thresh: float = 0.03) -> pd.DataFrame:
    """单日归因 → 写 context/scan/<date>/retro/attribution.csv,返回全帧。"""
    scan_root = scan_root or ws.scan_root()
    sdir = scan_root / date
    l1 = pd.read_csv(sdir / "L1_scored_full.csv", dtype={"code": str})
    realized = realized_returns(date)
    if realized.empty:
        raise RuntimeError(f"{date} 的 fwd 未实现 / 无价格,暂不能复盘")
    # 主尺**有没有数**,不只是帧有没有行(Wave7 实锤):`pending_days` 的成熟判据是按**日期**
    # 算的(D+2 交易日 ≤ today),盘后跑对、盘中跑就错 —— 2026-07-28 上午跑 07-24,D+2 正是
    # 当天、收盘未发布,于是 realized **有行但 fwd_2_oc 全 NaN**,原判据("帧非空")放行,
    # attribute_frame 一路算出 universe=0 / 赢家=0 的空归因,nightly_close 还报「归因 4/4 日 ✓」。
    # 隔壁 t1_review._fetch_prices 早就立了正确规矩(「T+1 daily 未结算 → ValueError,**绝不
    # 静默返回空帧**」),这里补齐同款:降级不留痕才是真病。
    n_ruler = int(pd.to_numeric(realized.get(MAIN_RULER), errors="coerce").notna().sum()) \
        if MAIN_RULER in realized.columns else 0
    if n_ruler < 100:
        raise RuntimeError(
            f"{date} 主尺 {MAIN_RULER} 仅 {n_ruler} 只有数(<100)——D+2 收盘多半未发布"
            "(通常 17:00 后可用),稍后再跑;**不产出空归因**")
    attr = attribute_frame(l1, realized, _buylist(date, report_root, scan_dir=sdir), abs_thresh=abs_thresh)
    attr = flag_news_pop(attr)                       # 标隔夜跳空脉冲(诊断/重标定排除)
    attr = refine_l3_bucket(attr, sdir)              # 细分 recalled_cut → l3_bench/pass1_cut(presence-gated)
    attr = _join_process_score(attr, sdir)           # P0-4:process_scores.csv → process_score 列(presence-gated)
    outdir = sdir / "retro"
    outdir.mkdir(parents=True, exist_ok=True)
    attr[[c for c in _KEEP if c in attr.columns]].to_csv(outdir / "attribution.csv", index=False)
    from autoresearch.learning.rejection_attribution import (
        build_rejection_attribution,
        write_rejection_attribution,
    )
    write_rejection_attribution(sdir, attr)
    from autoresearch.learning.abstention_ledger import (
        write_abstention_verdict,
    )
    health = None
    health_path = sdir / "run_health.json"
    if health_path.exists():
        health = json.loads(health_path.read_text(encoding="utf-8"))
    write_abstention_verdict(
        sdir,
        build_rejection_attribution(sdir, attr),
        health=health,
    )
    if (sdir / "shadow" / "l3_audit_candidates.csv").exists():
        from autoresearch.learning.l3_audit_ledger import write_day_ledger

        write_day_ledger(sdir, attr)
    pairs = build_retro_pairs(attr)                  # M1·同日 fail/success 对(成熟日才非空)
    if not pairs.empty:                              # presence-gated:未成熟日不落文件
        pairs.to_csv(outdir / "_retro_pairs.csv", index=False)
    from autoresearch.scan.outbox import safe_emit_retro_finalized_event
    from autoresearch.scan.post_run import (
        initialize_consumer_state,
        safe_run_consumers,
    )

    if safe_emit_retro_finalized_event(sdir) is not None:
        initialize_consumer_state(sdir)
        is_real = sdir.resolve() == (
            ws.scan_root() / date
        ).resolve()
        if is_real:
            safe_run_consumers(sdir)
    _publish_retro_control_state(
        sdir,
        report_root=report_root or ws.reports_root() / "scan",
    )
    return attr


def _publish_retro_control_state(
    scan_dir: Path,
    *,
    report_root: Path,
) -> None:
    """Refresh health/index and mirror finite retro facts into report trace."""
    import contextlib

    scan = Path(scan_dir)
    report = _report_dir_for(scan.name, Path(report_root))
    with contextlib.suppress(Exception):
        from autoresearch.scan.health import write_run_health

        write_run_health(scan)
    with contextlib.suppress(Exception):
        from autoresearch.scan.artifacts import write_artifact_index

        index = write_artifact_index(scan, report_dir=report)
        if report is None:
            return
        trace = report / "trace"
        trace.mkdir(parents=True, exist_ok=True)
        shutil.copy2(index, trace / "artifact_index.json")
        health = scan / "run_health.json"
        if health.exists():
            shutil.copy2(health, trace / "run_health.json")

        for directory in ("retro", "outbox"):
            source = scan / directory
            if not source.is_dir():
                continue
            target = trace / directory
            target.mkdir(parents=True, exist_ok=True)
            for path in sorted(source.glob("*")):
                if path.is_file():
                    shutil.copy2(path, target / path.name)

        shadow = scan / "shadow"
        if shadow.is_dir():
            target = trace / "shadow"
            target.mkdir(parents=True, exist_ok=True)
            for path in sorted(shadow.rglob("*")):
                if not path.is_file():
                    continue
                relative = path.relative_to(shadow)
                destination = target / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, destination)


def backfill_bought(scan_root: Path | str | None = None) -> int:
    """历史 attribution.csv 补 `bought` 列(rating∈OW/Buy);已有列跳过 → 幂等。返回补写文件数。

    修复:`_KEEP` 曾漏 `bought`(attribute_frame 算好但落盘白名单未收),导致老 attribution.csv
    无此列 → zero_buy_ledger.roll() 容错读成全 False → 真实买单日被记成 0 买(台账污染)。
    """
    root = Path(scan_root) if scan_root else ws.scan_root()
    n = 0
    for p in sorted(root.glob("*/retro/attribution.csv")):
        df = pd.read_csv(p, dtype={"code": str})
        if "bought" in df.columns:
            continue
        df["bought"] = df.get("rating", pd.Series("", index=df.index)).astype(str).isin(_BUY)
        df.to_csv(p, index=False)
        n += 1
    return n


def shadow_compare(attr: pd.DataFrame, sdir: Path) -> list[dict]:
    """影子漏斗对照:各变体 L2 vs 主 L2 的 T+2(主尺)/T+5 赢家捕获数(design: calendar-shadow §2)。

    读 <scan_dir>/shadow/L2_*.csv;无影子/无赢家列 → []。单日读数薄,≥10 日累计再下结论。
    """
    sh = sdir / "shadow"
    if not sh.is_dir():
        return []
    mp = sdir / "L2_gbdt_top200.csv"
    if not mp.exists():
        return []
    main = set(pd.read_csv(mp, dtype={"code": str})["code"].astype(str).str.zfill(6))
    a = attr.copy()
    a["code"] = a["code"].astype(str).str.zfill(6)
    w1 = set(a.loc[a.get("winner", pd.Series(dtype=bool)).fillna(False), "code"]) \
        if "winner" in a.columns else set()
    w5 = set(a.loc[a.get("winner_5", pd.Series(dtype=bool)).fillna(False), "code"]) \
        if "winner_5" in a.columns else set()
    out = []
    for f in sorted(sh.glob("L2_*.csv")):
        try:
            codes = set(pd.read_csv(f, dtype={"code": str})["code"].astype(str).str.zfill(6))
        except Exception:  # noqa: BLE001
            continue
        out.append({"variant": f.stem[3:], "n": len(codes),
                    "cap1": len(w1 & codes), "cap1_main": len(w1 & main),
                    "cap5": len(w5 & codes), "cap5_main": len(w5 & main)})
    return out


def l3_bench_shadow(attr: pd.DataFrame, sdir: Path, top_n: int = 5) -> dict | None:
    """L3 bench 防漏体检(design: plan 2026-07-12-l3-merge-plan.md Task 5):bench(l3-rank judged
    但未晋级 finalist 的候选,`_l3_bench.csv`)按 conviction 取 top-N 的已实现主尺
    (`ruler.MAIN_RULER`,现 `gap_c1_o2`)均值,对照 finalists(`finalists.csv`)的同尺均值
    ——「收窄没吃好票」的日常法庭:bench 头部
    若跑赢/持平 finalists,说明 finalist tier 收窄可能漏掉了够格票。

    无 `_l3_bench.csv`(旧日期/two_pass 关闭/Task2 bench 落地前)→ `None`(presence-gated,
    不进 retro_input,姿势同 `shadow_compare`)。缺 `conviction` 列 → 退化为文件前 N 行
    (不排序,不报错)。`finalists.csv` 缺失 → finalists 侧 mean 报 `None`(纵深防御,不因此
    连 bench 侧读数也不渲染)。主尺未成熟/两侧样本皆空 → mean 为 `None`(不臆造数字)。
    (输出键名 `*_mean_fwd2` 沿自 `fwd_2_oc` 年代,**取值随 `MAIN_RULER` 现算**,改键会破读者。)

    **真选口径**(pr_20260716_002):finalists 里 lane∈{pinned, watchlist_trigger, carryover}
    的行是保送/直通,L3 对它们没有选择权 → 头条 `finalists_mean_fwd2`/`n_finalists_realized`
    只算真选;保送行单独回显 `n_escorted`/`n_escorted_realized`/`escorted_mean_fwd2`
    (n 照实,不藏账)。finalists 无 `lane` 列(老数据)→ 全量当真选,行为不变。
    """
    p = Path(sdir) / "_l3_bench.csv"
    if not p.exists():
        return None
    try:
        bench = pd.read_csv(p, dtype={"code": str})
    except Exception:  # noqa: BLE001
        return None
    if not len(bench) or "code" not in bench.columns:
        return None
    bench = bench.copy()
    bench["code"] = bench["code"].astype(str).str.zfill(6)
    if "conviction" in bench.columns:
        bench["conviction"] = pd.to_numeric(bench["conviction"], errors="coerce")
        top = bench.sort_values("conviction", ascending=False).head(top_n)
    else:
        top = bench.head(top_n)

    a = attr.copy()
    a["code"] = a["code"].astype(str).str.zfill(6)
    fwd = (pd.to_numeric(a.set_index("code")[MAIN_RULER], errors="coerce")
          if MAIN_RULER in a.columns else pd.Series(dtype=float))

    top_fwd = fwd.reindex(top["code"]).dropna()
    fin_fwd = pd.Series(dtype=float)
    esc_fwd = pd.Series(dtype=float)
    n_escorted = 0
    finp = Path(sdir) / "finalists.csv"
    if finp.exists():
        try:
            fin = pd.read_csv(finp, dtype={"code": str})
        except Exception:  # noqa: BLE001
            fin = None
        if fin is not None and "code" in fin.columns:
            fin = fin.copy()
            fin["code"] = fin["code"].astype(str).str.zfill(6)
            if "lane" in fin.columns:
                esc_mask = fin["lane"].fillna("").astype(str).str.strip().isin(_NON_GENUINE_LANES)
            else:                                     # 老数据无 lane 列 → 全量当真选(行为不变)
                esc_mask = pd.Series(False, index=fin.index)
            n_escorted = int(esc_mask.sum())
            fin_fwd = fwd.reindex(fin.loc[~esc_mask, "code"]).dropna()
            esc_fwd = fwd.reindex(fin.loc[esc_mask, "code"]).dropna()

    return {
        "n_bench": int(len(bench)),
        "n_bench_top": int(len(top)),
        "n_bench_top_realized": int(len(top_fwd)),
        "bench_top_mean_fwd2": round(float(top_fwd.mean()), 5) if len(top_fwd) else None,
        "n_finalists_realized": int(len(fin_fwd)),
        "finalists_mean_fwd2": round(float(fin_fwd.mean()), 5) if len(fin_fwd) else None,
        "n_escorted": n_escorted,
        "n_escorted_realized": int(len(esc_fwd)),
        "escorted_mean_fwd2": round(float(esc_fwd.mean()), 5) if len(esc_fwd) else None,
    }


def pass1_cut_winners(attr: pd.DataFrame, sdir: Path) -> dict | None:
    """pass1 分诊即切(`_l3_pass1_cut.csv`)中的 T+2 赢家数(design: plan 2026-07-12-l3-merge-plan.md
    Task 5):沿用本模块 `attribute_frame` 已算好的 `winner` 定义(主尺 `ruler.MAIN_RULER`
    (现 `gap_c1_o2`)前10%分位 ∧ ≥abs_thresh,
    该模块现成赢家定义,不重算),数分诊环节切掉的票里事后有几只是赢家——「分诊有没有漏赢家」
    的日常法庭读数。

    无 `_l3_pass1_cut.csv`(旧日期/two_pass 关闭)→ `None`(presence-gated,不进 retro_input)。
    """
    p = Path(sdir) / "_l3_pass1_cut.csv"
    if not p.exists():
        return None
    try:
        cut = pd.read_csv(p, dtype={"code": str})
    except Exception:  # noqa: BLE001
        return None
    if not len(cut) or "code" not in cut.columns:
        return None
    cut_codes = set(cut["code"].astype(str).str.zfill(6))

    a = attr.copy()
    a["code"] = a["code"].astype(str).str.zfill(6)
    if "winner" in a.columns:
        win_codes = set(a.loc[a["winner"].fillna(False).astype(bool), "code"])
    else:
        win_codes = set()

    return {"n_cut": len(cut_codes), "n_winners": len(cut_codes & win_codes)}


def _health_section(sdir: Path) -> list[str]:
    """run_health.json → retro_input 运行健康节(降级字段/缺产物提示)。缺文件/无恙 → []。

    目的:勿把数据病当因子病——降级字段多的日子,IC/归因读数打折扣,重标定留意。
    """
    p = sdir / "run_health.json"
    if not p.exists():
        return []
    try:
        h = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    deg = h.get("degraded_fields") or []
    core_miss = h.get("core_missing") or []
    if not deg and not core_miss:
        return []
    out = ["\n## 运行健康(run_health)"]
    if deg:
        out.append(f"- 降级字段(NaN>30%):{'、'.join(deg)} —— 该日与这些因子相关的 IC/归因读数打折扣,"
                   "**勿把数据病当因子病**,重标定时留意。")
    if core_miss:
        out.append(f"- 核心产物缺失:{'、'.join(core_miss)} —— 该日漏斗不完整,分桶归因可能失真。")
    return out


def unsellable_section(attr: pd.DataFrame) -> list[str]:
    """`EXIT_FLAG`(`unsellable_o2`,T+2 一字跌停开=卖不出)消费点①:retro 渲染层。

    Wave12-T9:该字段此前生产/落盘齐全但零读侧消费(`ruler.py` 自己的注释都写着「A 建的
    字段 B 没消费」)。用户裁定「标旗不剔」——卖不出=真实亏损延续,把这些行从归因里剔除
    反而会美化账本;所以这里只做**可见性**:单独点名"买了、且卖不出"的行,不过滤/不改
    任何既有分桶(`caught`/`missed_l0`/… 等桶的成员资格完全不受影响)。

    只标**买单**里的旗:`unsellable_o2` 对任意票(买或没买)都可能为真(跌停开是市场普遍
    现象),但"标旗不剔=真实亏损延续"这句话字面意义上的对象只有已建仓的票——没买的旗票
    不是"你的仓位卖不出",不进这一节。presence-gated:旧 attribution 缺 `bought`/
    `EXIT_FLAG` 列 → 空列表,不新增章节(不炸旧调用方/旧产物)。
    """
    if "bought" not in attr.columns or EXIT_FLAG not in attr.columns:
        return []
    bought = _as_bool(attr["bought"])
    flagged = _as_bool(attr[EXIT_FLAG])
    hit = attr[(bought & flagged).to_numpy()]
    if not len(hit):
        return []
    cols = [c for c in ("code", "name", MAIN_RULER, "rating") if c in hit.columns]
    head = "| " + " | ".join(cols) + " |"
    sep = "|" + "|".join(["---"] * len(cols)) + "|"
    rows = ["| " + " | ".join(str(r[c]) for c in cols) + " |" for _, r in hit.iterrows()]
    return [f"\n## ⚠️卖不出预警(`{EXIT_FLAG}`,T+2 一字跌停开;标旗不剔——卖不出=真实亏损延续,"
            "不是过滤剔除)", head, sep, *rows]


def write_retro_input(date: str, attr: pd.DataFrame, scan_root: Path | None = None) -> Path:
    """把 stage_stats + 漏判赢家 top(带因子行)+ 选中对照写成 retro_input.md(喂诊断)。"""
    scan_root = scan_root or ws.scan_root()
    st = stage_stats(attr)
    lines = [f"# retro 输入 — {date}\n", "## 漏斗命中(对赢家)",
             f"- 当日可交易 universe:{st['n_universe_realized']};**赢家(T+2 前10%∧≥3%):{st['n_winners']}**",
             f"- 赢家进入 L1 召回池:{st['winners_in_l1']}/{st['n_winners']} "
             f"(到召回 {st['winner_to_l1']});被买单抓到:{st['winners_bought']}/{st['n_winners']} "
             f"(到买单 {st['winner_to_buylist']})",
             f"- 买单 {st['buylist_n']} 只,命中赢家 {st['buylist_hit']}(命中率 {st['buylist_hitrate']}),"
             f"误买(跌入底10%){st['buylist_fp']}",
             f"- 分桶:{st['buckets']};当日 composite IC(vs {MAIN_RULER}):{st['day_ic_composite']}\n"]

    def _tbl(df: pd.DataFrame, cols: list[str]) -> list[str]:
        cols = [c for c in cols if c in df.columns]
        head = "| " + " | ".join(cols) + " |"
        sep = "|" + "|".join(["---"] * len(cols)) + "|"
        rows = ["| " + " | ".join(str(r[c]) for c in cols) + " |" for _, r in df.iterrows()]
        return [head, sep, *rows]

    fcols = ["code", "name", "industry", MAIN_RULER, "rank", "composite", "score_momentum",
             "main_net_ratio", "winner_rate", "price_to_cost", "rsi6", "pct_60d"]
    for label, bk in [("漏在 L0(门槛误杀)", "missed_l0"), ("漏在 L1(权重压低)", "missed_l1"),
                      ("L2-L3 误判(召回了却 cut)", "recalled_cut"),
                      # 终审 I-3:refine_l3_bucket 细分两桶也要逐票因子行(明细层与聚合层同权),
                      # 否则 bench/pass1 漏检赢家从所有逐票表消失,诊断能力比细分前退步;
                      # 旧日期无影子文件 → 桶恒空 → 渲染 "_无_"(presence 天然)。
                      ("L3 bench 漏检(judged 未晋级)", "l3_bench"),
                      ("pass1 分诊漏检", "pass1_cut")]:
        sub = attr[attr["bucket"] == bk].sort_values(MAIN_RULER, ascending=False).head(15)
        lines += [f"\n## {label} — {len(attr[attr['bucket'] == bk])} 只(top 15)"]
        lines += _tbl(sub, fcols) if len(sub) else ["_无_"]
    caught = attr[attr["bucket"] == "caught"].sort_values(MAIN_RULER, ascending=False).head(10)
    lines += ["\n## 对照:抓到的赢家(caught, top 10)"]
    lines += _tbl(caught, fcols) if len(caught) else ["_无_"]

    lines += unsellable_section(attr)          # Wave12-T9:EXIT_FLAG 消费①(标旗不剔,见函数 docstring)

    # ── T+5 盲区(swing 口径;spec 2026-07-02-scan-retro-depth-metrics)──
    if "winner_5" in attr.columns and attr["winner_5"].fillna(False).any():
        w5 = attr[attr["winner_5"].fillna(False)]
        b5 = {k: int(v) for k, v in w5["bucket_5"].value_counts().items() if k}
        lines += [f"\n## T+5 盲区(swing 口径):赢家(前10%∧≥5%){len(w5)};分桶:{b5}"]
        f5cols = ["code", "name", "industry", "fwd_5_oc", "rank", "composite", "score_momentum",
                  "main_net_ratio", "winner_rate", "pct_60d"]
        sub5 = attr[attr["bucket_5"] == "missed_l1"].sort_values("fwd_5_oc", ascending=False).head(10)
        lines += _tbl(sub5, f5cols) if len(sub5) else ["_missed_l1(T+5)无_"]
    else:
        lines += ["\n## T+5 盲区(swing 口径)\n_fwd_5 未成熟或无数据(retro 补跑成熟日自动补)_"]

    # ── L3 错杀验尸 + L2 floor 自然实验(读 staging,presence-gated)──
    sdir = scan_root / date
    try:
        def _rd(fn):
            p = sdir / fn
            return pd.read_csv(p, dtype={"code": str}) if p.exists() else None

        l2df, fin, jud = _rd("L2_gbdt_top200.csv"), _rd("finalists.csv"), _rd("L3_judged_full.csv")
        if jud is not None and l2df is not None:
            au = l3_miss_autopsy(attr, l2df, fin if fin is not None else pd.DataFrame(), jud)
            lines += ["\n## L3 错杀验尸(L2-keep ∧ 非 finalist ∧ T+2 赢家;risk=当时红队理由)"]
            lines += _tbl(au, list(au.columns)) if len(au) else ["_无错杀(或 fwd_2 未成熟)_"]
        if l2df is not None:
            fx = floor_experiment(l2df, attr)
            lines += ["\n## L2 floor 自然实验(fwd 均值;救回≈merit → floor 免费,持续弱于被挤掉 → 复审)",
                      f"- floor 救回 n={fx['floor']['n']}:fwd1 {fx['floor']['fwd1']} / fwd5 {fx['floor']['fwd5']};"
                      f"merit n={fx['merit']['n']}:fwd1 {fx['merit']['fwd1']} / fwd5 {fx['merit']['fwd5']};"
                      f"被挤掉 n={fx['cut']['n']}:fwd1 {fx['cut']['fwd1']} / fwd5 {fx['cut']['fwd5']}"]
    except Exception as e:  # noqa: BLE001
        lines += [f"\n_L3 错杀/floor 实验跳过:{e}_"]

    try:                                   # R2/R3/R4 · 经验 MTM(机判自动记账)+ 门审计 + proposals 看板
        import autoresearch.learning.feedback_store as fs
        active = fs.lessons_for([("global", "*")])
        mtm = mtm_check_guards(attr, active, day=date, apply=True)
        if mtm:
            lines += ["\n## 经验 mark-to-market(带 guard 的机判已自动记账;无 guard 的逐条 support/refute 由你判)"]
            lines += [f"- `{m['id']}`:n={m['n']} excess={m['excess']} → **{m['verdict']}**" for m in mtm]
        no_guard = [r for r in active if not r.get("guard")]
        if no_guard:
            lines += ["- 待人判(无 guard):" + "、".join(f"`{r['id']}`" for r in no_guard)
                      + " —— 用 `fs.mtm_update(id, 'support'|'refute', day)` 记账"]
        ga = gate_audit(attr, sdir)
        if len(ga):
            lines += ["\n## 门审计(self_review 拦的票后来怎么走;ex<0 = 拦对)"]
            lines += _tbl(ga.head(15), list(ga.columns))
        props = fs.open_proposals(date)
        if props:
            lines += ["\n## 待裁决 proposals(看板;>14 天 ⚠)"]
            lines += [f"- {'⚠️ ' if p['age_days'] > 14 else ''}`{p['id']}`({p['age_days']}d,{p['kind']}):"
                      f"{p['summary']}" for p in props]
    except Exception as e:  # noqa: BLE001
        lines += [f"\n_MTM/门审计/看板跳过:{e}_"]

    lines += _health_section(sdir)         # 运行健康:降级字段/缺产物 → 勿把数据病当因子病

    try:                                   # 影子漏斗对照(变体 L2 的赢家捕获 vs 主;免费 A/B)
        sc = shadow_compare(attr, sdir)
        if sc:
            lines += ["\n## 影子漏斗对照(赢家捕获数;单日勿下结论,≥10 日累计再提 proposal)"]
            lines += [f"- **{r['variant']}**(n={r['n']}):T+2 捕获 {r['cap1']} vs 主 {r['cap1_main']};"
                      f"T+5 捕获 {r['cap5']} vs 主 {r['cap5_main']}" for r in sc]
    except Exception:  # noqa: BLE001
        pass

    try:                                   # L3 收窄防漏体检(bench 头部 vs finalists + pass1_cut 赢家数;
                                            # plan 2026-07-12-l3-merge-plan.md Task 5;逐行 presence-gated)
        sect: list[str] = []
        bs = l3_bench_shadow(attr, sdir)
        if bs:
            # 真选口径(pr_20260716_002):保送/直通行已剔出 finalists 头条,有则单独回显。
            esc_txt = (f";📌保送/直通 {bs['n_escorted']} 只(已实现 {bs['n_escorted_realized']}):"
                       f"mean_fwd2 {bs['escorted_mean_fwd2']},不计入头条"
                       if bs.get("n_escorted") else "")
            sect.append(f"- L3 bench top-{bs['n_bench_top']}(按 conviction,bench 共 {bs['n_bench']} 只,"
                        f"已实现 {bs['n_bench_top_realized']}):mean_fwd2 {bs['bench_top_mean_fwd2']} "
                        f"vs finalists 真选(已实现 {bs['n_finalists_realized']}):"
                        f"mean_fwd2 {bs['finalists_mean_fwd2']}{esc_txt}")
        pc = pass1_cut_winners(attr, sdir)
        if pc:
            sect.append(f"- pass1_cut 中 T+2 赢家数:{pc['n_winners']}/{pc['n_cut']}"
                        "(该模块 winner 定义:前10%∧≥3%)")
        if sect:
            lines += ["\n## L3 收窄防漏体检(「收窄没吃好票」的日常法庭;presence-gated,缺文件的行不渲染)",
                      *sect]
    except Exception as e:  # noqa: BLE001
        lines += [f"\n_L3 收窄防漏体检跳过:{e}_"]

    try:                                   # F · 逐阶段 agent edge(staging 缺 / fwd 未实现则跳过)
        import autoresearch.learning.stage_eval as stage_eval
        lines += stage_eval.render_stage_eval(stage_eval.evaluate(date, scan_root=scan_root))
    except Exception as e:  # noqa: BLE001
        lines += [f"\n## 各阶段 agent edge\n_stage_eval 跳过:{e}_"]

    try:                                   # E2 · 够格升『程序性硬门』的经验(给它写 guard → self_review 拦)
        import autoresearch.learning.feedback_store as fs
        cands = fs.promotion_candidates()
        if cands:
            lines += ["\n## 够格升硬门的经验(E2:反复强化、还没 guard → 给它写 {field,op,value} 升 self_review 硬门)"]
            lines += [f"- `{c['id']}` ×{c.get('reinforce_count')} conf {c.get('confidence')}:{c.get('rule')}"
                      for c in cands]
    except Exception:  # noqa: BLE001
        pass

    p = scan_root / date / "retro" / "retro_input.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


def _sha8(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()[:8]


def top_weight_changes(before: dict, after: dict, n: int = 8) -> list[dict]:
    """__global__ 组权重的最大绝对变化(before/after 为 {group: weight});纯函数。"""
    rows = [{"group": k, "before": round(float(before.get(k, 0.0)), 5),
             "after": round(float(after.get(k, 0.0)), 5),
             "delta": round(float(after.get(k, 0.0)) - float(before.get(k, 0.0)), 5)}
            for k in (set(before) | set(after))]
    return sorted(rows, key=lambda r: abs(r["delta"]), reverse=True)[:n]


def recalibrate_and_log(retro_date: str, cap_floor: float = 30.0, k: float = 200.0) -> dict:
    """半自动闭环的"自动落地":factor_lab.calibrate(多日滚动+收缩)重写 weights.json + 审计 changelog。

    快照旧权重(weights.<sha>.json,供 Phase 3 回滚)→ calibrate → log_change(前后 sha + top 变化)。
    """
    import autoresearch.learning.feedback_store as fs
    import autoresearch.research.factor_lab as fl
    # 先增量续面板(修 pr_20260716_001:calibrate 只消费冻结 plan.pkl → 连续 NO-OP)。
    # 失败必须响(打 stderr),但不阻断:冻结面板校准仍好过不校准——心跳探针
    # (changelog_ledger.heartbeat,prelude 每日打)会把持续冻结顶到汇总屏。
    try:
        ext = fl.extend_plan()
        if ext["added_f"] or ext["added_p"] or ext["healed"]:
            print(f"[recalibrate] 面板增量续:+F {ext['added_f']} / +P {ext['added_p']}"
                  f" / 回补洞 {ext['healed']}(F 尾 {ext['f_last']},共 {ext['n_f']} 日)")
    except Exception as e:  # noqa: BLE001
        print(f"🚨 [recalibrate] extend_plan 失败({e})→ 本次退化为冻结面板校准"
              f"(= 旧 NO-OP 行为);若心跳探针连日报警即是它", file=sys.stderr)
    wp = ws.factor_lab_root() / "weights.json"
    before_raw = wp.read_bytes() if wp.exists() else b"{}"
    before_sha = fs.snapshot_weights() or _sha8(before_raw)   # 快照留底(Phase 3 回滚)
    fl.calibrate(cap_floor=cap_floor, k=k, label_col=MAIN_RULER)  # 重写 weights.json(多日面板,绝非单日)
    after_raw = wp.read_bytes()
    before, after = json.loads(before_raw), json.loads(after_raw)
    tc = top_weight_changes(before.get("weights", {}).get("__global__", {}),
                            after.get("weights", {}).get("__global__", {}))
    after_sha, n_dates = _sha8(after_raw), int(after.get("meta", {}).get("n_dates", 0))
    fs.log_change(retro_date, before_sha, after_sha, tc, n_dates, label_col=MAIN_RULER)
    return {"before_sha": before_sha, "after_sha": after_sha, "top_changes": tc, "n_dates": n_dates}


_GAP_COLS = ("gap_c1_o2", "buyable_c1", "unsellable_o2")


def _backfill_gap_columns(path: Path, attr: pd.DataFrame, date: str) -> bool:
    """只追加隔夜尺三列(merge-on-code),旧列旧值/行数原样不动 —— 不走全量 `attribute()`
    (那会重跑 attribute_frame + 一整串下游 consumer/事件,不是"只加列"的最小改动)。

    `realized_returns(date)` 若连 `gap_c1_o2` 都没有(数据源太老/未算过)→ 无从回填,
    返回 False(该日下次 refresh 仍会重试,不假装已处理)。成功追加 → 写盘 + True,
    同时打 `ruler` tag(写入那一刻的 MAIN_RULER 真值;历史行本就没有这列,不必回改)。
    """
    realized = realized_returns(date)
    if realized is None or not len(realized) or "code" not in realized.columns \
            or "gap_c1_o2" not in realized.columns:
        return False
    realized = realized.copy()
    realized["code"] = realized["code"].astype(str).str.zfill(6)
    attr = attr.copy()
    attr["code"] = attr["code"].astype(str).str.zfill(6)
    add_cols = [c for c in _GAP_COLS if c in realized.columns and c not in attr.columns]
    if not add_cols:
        return False
    merged = attr.merge(realized[["code", *add_cols]], on="code", how="left")
    merged["ruler"] = MAIN_RULER
    temp = path.with_name(f"{path.name}.tmp")
    merged.to_csv(temp, index=False)
    temp.replace(path)
    return True


def _backfill_rel_gap_columns(path: Path, attr: pd.DataFrame) -> bool:
    """T22(Wave12 E6-0):只追加 rel_gap_market/rel_gap_sector 两列,旧列旧值/行数原样不动。

    与 `_backfill_gap_columns` 不同的是**不必重取 `realized_returns()`**:算这两列所需的
    全部原料(`MAIN_RULER`/`industry`/`buyable_c1`)早在 Wave11 批A 就已经进了 `_KEEP`
    白名单,历史 `attribution.csv` 自身就带着——纯粹从已有列现算现追加(`_rel_gap_cols`,
    与 `attribute_frame` 共用同一算法,不得两处各写一份)。

    源列缺失(pre-Wave11-A3 的老文件,连 `gap_c1_o2`/`industry` 都没有)→ 无从回填,诚实
    返回 False,不碰文件。两列已存在 → 幂等跳过,返回 False。

    I-4 修复(final-review 2026-08-08/09):存在性判据用字面量 `REL_GAP_RULER`,不用
    `MAIN_RULER`——`_rel_gap_cols` 内部已钉死读 `REL_GAP_RULER`,若这里仍按动态 `MAIN_RULER`
    判存在性,批A 回滚杆改回 `fwd_2_oc` 后,一份只有 `fwd_2_oc`、没有 `gap_c1_o2` 的老文件
    会被误判"源列齐全"放行,实际调用 `_rel_gap_cols` 时 `frame[REL_GAP_RULER]` 直接 KeyError。
    """
    if REL_GAP_RULER not in attr.columns or "industry" not in attr.columns:
        return False
    if REL_MARKET in attr.columns and REL_SECTOR in attr.columns:
        return False
    out = attr.copy()
    out["code"] = out["code"].astype(str).str.zfill(6)
    out[REL_MARKET], out[REL_SECTOR] = _rel_gap_cols(out)
    temp = path.with_name(f"{path.name}.tmp")
    out.to_csv(temp, index=False)
    temp.replace(path)
    return True


def refresh_attributions(scan_root: Path | None = None, report_root: Path | None = None,
                         max_days: int = 20) -> list[str]:
    """对已复盘(done)但 fwd 未成熟即落账的老日重写 attribution(幂等,价格走 cache)。

    三条互不重叠的「需要刷新」判据(顺序执行,同一天可以连中第二、三条):
    - 缺 `fwd_10_oc`/`hi_10_oc` 列,或 fwd_5/fwd_10 全 NaN → 老路不变:全量 `attribute()`
      重算(治"买单 ledger 永远 —",attribution 原为 retro 时一次性落账;design:
      run-reliability §3)。该路自然带出 gap/rel_gap 列(`_KEEP` 已收编),此日无需再走
      下面两条。
    - 否则,若缺 `gap_c1_o2`(隔夜尺,Wave11 批A)→ 只追加新列(merge-on-code),不重跑
      attribute_frame、不碰任何旧列的旧值、不改行数(见 `_backfill_gap_columns`);成功后
      就地从磁盘重读一次,让下一条判据看到刚落的 `gap_c1_o2`(同一遍 refresh 就能把 rel_gap
      也补上,不必等下一次夜间批)。
    - 否则,若缺 `rel_gap_market`/`rel_gap_sector`(T22)→ 只追加两列(自包含现算,见
      `_backfill_rel_gap_columns`),同样不碰旧列/行数。
    """
    scan_root = scan_root or ws.scan_root()
    if not scan_root.exists():
        return []
    today = datetime.now().strftime("%Y-%m-%d")
    days = sorted(p.name for p in scan_root.iterdir()
                  if p.is_dir() and p.name[:2] == "20" and p.name < today
                  and (p / "retro" / "done.json").exists()
                  and (p / "retro" / "attribution.csv").exists())[-max_days:]
    out: list[str] = []
    for d in days:
        path = scan_root / d / "retro" / "attribution.csv"
        try:
            attr = pd.read_csv(path, dtype={"code": str})
        except Exception:  # noqa: BLE001
            continue
        cols = set(attr.columns)
        need_full = ("fwd_10_oc" not in cols or "hi_10_oc" not in cols
                     or pd.to_numeric(attr.get("fwd_5_oc"), errors="coerce").isna().all()
                     or pd.to_numeric(attr.get("fwd_10_oc"), errors="coerce").isna().all())
        if need_full:
            try:
                attribute(d, scan_root=scan_root, report_root=report_root)
                out.append(d)
            except Exception as e:  # noqa: BLE001 — 单日失败不阻其余
                print(f"[refresh] {d} 跳过: {e}", file=sys.stderr)
            continue
        changed = False
        if "gap_c1_o2" not in cols:
            try:
                if _backfill_gap_columns(path, attr, d):
                    attr = pd.read_csv(path, dtype={"code": str})   # 就地刷新:给下面 rel_gap 判据看到新列
                    cols = set(attr.columns)
                    changed = True
            except Exception as e:  # noqa: BLE001 — 单日失败不阻其余
                print(f"[refresh] {d} gap 回填跳过: {e}", file=sys.stderr)
        if REL_MARKET not in cols or REL_SECTOR not in cols:
            try:
                if _backfill_rel_gap_columns(path, attr):
                    changed = True
            except Exception as e:  # noqa: BLE001 — 单日失败不阻其余
                print(f"[refresh] {d} rel_gap 回填跳过: {e}", file=sys.stderr)
        if changed:
            out.append(d)
    return out


def mark_done(date: str, summary: dict | None = None, scan_root: Path | None = None) -> None:
    scan_root = scan_root or ws.scan_root()
    p = scan_root / date / "retro" / "done.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"date": date, "ts": datetime.now().isoformat(timespec="seconds"),
                             "summary": summary or {}}, ensure_ascii=False), encoding="utf-8")
    try:                                    # R2·decay 接入节奏:每完成一次复盘做一次记忆防腐(幂等/日)
        import autoresearch.learning.feedback_store as fs
        decayed = fs.decay_lessons()
        if decayed:
            print(f"[mark_done] decay_lessons: {decayed}")
    except Exception:  # noqa: BLE001
        pass


# ───────────────────────── 离线自测(分桶 + 阶段统计) ─────────────────────────


def _selftest() -> int:
    fails: list[str] = []
    # 构造全市场已实现:4 赢家(0.10)、1 误买(-0.09)、20 噪声(~0)
    # 主尺列用 MAIN_RULER 真值(T16 flip 现为 gap_c1_o2)—— attribute_frame 按 m[MAIN_RULER]
    # 动态读源,喂错列名会让主尺整体缺失,不是"退回旧尺"。
    rows = []
    rows += [{"code": c, "fwd_1_oo": 0.10, MAIN_RULER: 0.10, "fwd_5_oc": 0.12, "buyable": True}
             for c in ("000001", "000002", "000003", "000004")]
    rows += [{"code": "000005", "fwd_1_oo": -0.09, MAIN_RULER: -0.09, "fwd_5_oc": -0.10, "buyable": True}]
    rows += [{"code": f"0001{i:02d}", "fwd_1_oo": (i - 10) * 0.002, MAIN_RULER: (i - 10) * 0.002,
             "fwd_5_oc": 0.0, "buyable": True}
             for i in range(20)]
    realized = pd.DataFrame(rows)
    # L1 面板:000001-3 在 universe(2 recalled),000005 在 universe 且被买,000004 不在(漏 L0)
    l1 = pd.DataFrame([
        {"code": "000001", "name": "抓到", "industry": "电子", "rank": 5, "recalled": True, "composite": 80},
        {"code": "000002", "name": "误判", "industry": "电子", "rank": 8, "recalled": True, "composite": 75},
        {"code": "000003", "name": "漏L1", "industry": "医药", "rank": 1500, "recalled": False, "composite": 40},
        {"code": "000005", "name": "误买", "industry": "电子", "rank": 12, "recalled": True, "composite": 70},
    ])
    buylist = {"000001": "Overweight", "000005": "Overweight", "000002": "Hold"}
    attr = attribute_frame(l1, realized, buylist)

    def bk(code):
        return attr.loc[attr["code"] == code, "bucket"].iloc[0]

    expect = {"000001": "caught", "000002": "recalled_cut", "000003": "missed_l1",
              "000004": "missed_l0", "000005": "false_positive"}
    for code, want in expect.items():
        got = bk(code)
        if got != want:
            fails.append(f"{code} 桶错: 期望 {want} 得 {got}")

    st = stage_stats(attr)
    checks = {"n_winners": 4, "winners_in_l1": 3, "winners_recalled": 2, "winners_bought": 1,
              "buylist_n": 2, "buylist_hit": 1, "buylist_fp": 1}
    for k, v in checks.items():
        if st[k] != v:
            fails.append(f"stage_stats[{k}] 期望 {v} 得 {st[k]}")
    if st["buckets"].get("caught") != 1 or st["buckets"].get("missed_l0") != 1:
        fails.append(f"buckets 计数错: {st['buckets']}")

    # 边界:realized 为空 → attribute_frame 不崩(无赢家)
    empty = attribute_frame(l1, pd.DataFrame(columns=["code", "fwd_1_oo", MAIN_RULER, "fwd_5_oc", "buyable"]), {})
    if len(empty[empty["winner"]]) != 0:
        fails.append("空 realized 不应有赢家")

    # 重标定簿记:权重变化排序 + sha
    tc = top_weight_changes({"momentum": 0.026, "value": -0.010, "tech": 0.026},
                            {"momentum": 0.031, "value": -0.010, "tech": 0.020})
    if tc[0]["group"] not in ("momentum", "tech") or abs(tc[0]["delta"]) < 0.005:
        fails.append(f"top_weight_changes 排序错: {tc[:2]}")
    if next(r for r in tc if r["group"] == "value")["delta"] != 0.0:
        fails.append("未变的组 delta 应为 0")
    if _sha8(b"abc") != hashlib.sha1(b"abc").hexdigest()[:8]:
        fails.append("_sha8 错")

    # 消息脉冲:隔夜大跳空赢家被标 news_pop,普通赢家 / 非赢家不标
    npf = flag_news_pop(pd.DataFrame({"winner": [True, True, False], "gap_d1": [0.09, 0.01, 0.09]}))
    if list(npf["news_pop"]) != [True, False, False]:
        fails.append(f"flag_news_pop 错: {list(npf['news_pop'])}")

    if fails:
        print("SELFTEST ❌")
        for f in fails:
            print("  -", f)
        return 1
    print("SELFTEST ✅  分桶(caught/recalled_cut/missed_l1/missed_l0/false_positive)+ 阶段统计 全过")
    return 0


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--selftest" in sys.argv:
        return _selftest()
    if args and args[0] == "pending":
        # 拆两段(Wave11-A7):归因欠账(nightly_close 该补没补上)vs 诊断欠账(已备料,
        # 只差人/LLM 看一眼)——此前混在一份列表里,已归因的日子会被误读成"什么都没做"。
        attr_days = attribution_pending()
        diag_days = pending_days()
        print(f"归因欠账:{'、'.join(attr_days) if attr_days else '(无)'}")
        print(f"诊断欠账(已备料):{'、'.join(diag_days) if diag_days else '(无)'}")
        return 0
    if args and args[0] == "refresh":
        done = refresh_attributions()
        print(f"[refresh] 刷新 {len(done)} 日:{'、'.join(done) or '(无需刷新)'}")
        return 0
    if args and args[0] == "backfill-bought":
        n = backfill_bought()
        print(f"[backfill-bought] 补写 {n} 个文件")
        return 0
    if len(args) >= 2 and args[0] == "attribute":
        attr = attribute(args[1])
        write_retro_input(args[1], attr)
        st = stage_stats(attr)
        # 误判 = recalled_cut + 其细分标签(l3_bench/pass1_cut,见 refine_l3_bucket)之和,
        # 避免两遍法影子接入后 CLI 摘要静默漏计已被细分走的行。
        misjudged = sum(st["buckets"].get(k, 0) for k in ("recalled_cut", "l3_bench", "pass1_cut"))
        print(f"[retro] {args[1]} 赢家 {st['n_winners']},买单命中 {st['buylist_hit']}/{st['buylist_n']},"
              f"漏 {st['buckets'].get('missed_l1', 0)+st['buckets'].get('missed_l0', 0)},"
              f"误判 {misjudged} → context/scan/{args[1]}/retro/")
        return 0
    print(__doc__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
