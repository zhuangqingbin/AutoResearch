#!/usr/bin/env python3
"""闭环复盘 retro · 归因前一日 scan 报告 vs T+1 已实现涨跌(确定性,零 LLM)。

仅挂 scan-market。用当日已实现 `fwd_1_oo`(T+1 开到开,复用 factor_lab 口径:D 收盘信号→
D+1 开盘买、剔 D+1 一字板)检验 D 的报告,把每只股票分桶:抓到 / L2-L3 误判 / 漏在 L1 /
漏在 L0 / 误买。产出 attribution.csv + retro_input.md,喂给 scan-retro skill 做 Claude 诊断
(系统性病因 + 自动重标定 + 经验/建议)。归因数学纯函数、可离线自测;取数复用 factor_lab。

用法:
  uv run --no-sync python -m autoresearch.learning.retro --selftest
  uv run --no-sync python -m autoresearch.learning.retro attribute 2026-06-19      # 单日(需 fwd 已实现)
  uv run --no-sync python -m autoresearch.learning.retro pending                   # 列未复盘日
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

from autoresearch.agents.utils.rating import RATINGS_5_TIER, parse_rating

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


def attribute_frame(l1: pd.DataFrame, realized: pd.DataFrame, buylist: dict,
                    abs_thresh: float = 0.03, top_q: float = 0.9, bot_q: float = 0.1) -> pd.DataFrame:
    """全市场已实现收益 × L1 全打分面板 × 报告买单 → 每只一个 bucket。纯函数(无 IO)。

    赢家 = 可交易 universe 内 fwd_1_oo ≥ 九分位 ∧ ≥ abs_thresh。
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
    m["tradable"] = m["buyable"].fillna(True) & m["fwd_1_oo"].notna()

    trad = m[m["tradable"]]
    hi = trad["fwd_1_oo"].quantile(top_q) if len(trad) else float("nan")
    lo = trad["fwd_1_oo"].quantile(bot_q) if len(trad) else float("nan")
    m["winner"] = m["tradable"] & (m["fwd_1_oo"] >= hi) & (m["fwd_1_oo"] >= abs_thresh)

    def bucket(r) -> str:
        if r["winner"] and r["bought"]:
            return "caught"
        if r["winner"] and r["recalled_flag"] and not r["bought"]:
            return "recalled_cut"
        if r["winner"] and r["in_l1"] and not r["recalled_flag"]:
            return "missed_l1"
        if r["winner"] and not r["in_l1"]:
            return "missed_l0"
        if r["bought"] and r["tradable"] and r["fwd_1_oo"] <= lo:
            return "false_positive"
        return ""

    m["bucket"] = m.apply(bucket, axis=1)

    # T+5 swing 口径(spec 2026-07-02-scan-retro-depth-metrics §①):L3/L4 猎的是 swing,
    # 盲区审计与 T+1 并存;fwd_5 未成熟(NaN)→ winner_5 全 False(retro 补跑成熟日覆写)。
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
    """L3 错杀验尸(spec §②):L2-keep ∧ 非 finalist ∧ winner_5 → join L3 判分(当时的红队理由)。纯函数。"""
    cols = ["code", "name", "fwd_5_oc", "thesis", "risk", "triage_lean", "lane",
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
    w5 = a.get("winner_5", pd.Series(False, index=a.index)).fillna(False)
    miss = a[w5 & a["code"].isin(pool)]
    out = miss.merge(j, on="code", how="inner", suffixes=("", "_j"))
    out = out.sort_values("fwd_5_oc", ascending=False).head(top_n)
    return out[[c for c in cols if c in out.columns]].reset_index(drop=True)


def build_retro_pairs(attr: pd.DataFrame, max_pairs: int = 20) -> pd.DataFrame:
    """M1·同日配对蒸馏:构造 ExpeL 式 fail/success 对(控制变量=同日 → regime/地形/漏斗参数恒定)。

    fail 侧 = 评级最高档但 T+5 跌(有 bought=OW/Buy 则用之;**0 买日**退化到当日最高评级档的下跌者);
    success 侧 = 同日被门拦/漏召回(bucket_5 ∈ missed_l0/l1)但 T+5 涨(winner_5)。
    贪心配对:worst-fail 先,同 industry 最近邻优先(matched_on=industry),无则放宽全局(=global),success 各用一次。
    输出每对带因子差(fail − success),diff 只剩标的特征与判断 → 喂 Claude 蒸馏,走 M2 `adjudicate` 落库。
    fwd_5 未成熟 / 缺 fail 或 success 侧 → 返回空表(优雅,retro 未成熟日不产)。
    """
    empty = pd.DataFrame()
    if attr is None or attr.empty or "fwd_5_oc" not in attr.columns:
        return empty
    a = attr.copy()
    a["_fwd5"] = pd.to_numeric(a["fwd_5_oc"], errors="coerce")
    if a["_fwd5"].notna().sum() == 0:                        # fwd_5 未成熟
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
    fails = fail_pool[fail_pool["_fwd5"] < 0].sort_values("_fwd5")     # 跌得最狠先配

    # success 侧:同日被门拦/漏召回但 T+5 涨
    miss = a.get("bucket_5", "").isin(["missed_l0", "missed_l1"]) if "bucket_5" in a.columns else False
    win = a.get("winner_5", False).fillna(False).astype(bool) if "winner_5" in a.columns else False
    succ = a[miss & win].sort_values("_fwd5", ascending=False)
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
               "fail_fwd5": round(float(f["_fwd5"]), 4), "win_code": w["code"], "win_name": w.get("name"),
               "win_bucket5": w.get("bucket_5"), "win_fwd5": round(float(w["_fwd5"]), 4),
               "industry": f.get("industry"),
               "matched_on": "industry" if (not same.empty) else "global"}
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
    """R2·经验 MTM 机判:带 guard 的经验,其条件组当日 fwd_1 对市场的 excess → support/refute。

    guard 全是"拦买"型 → 满足组跑输市场(excess<0)= 拦得对 = support;跑赢 = refute。
    n<min_n → skip(样本不足不判)。apply=True → 判定即调 feedback_store.mtm_update
    (confidence 机械升降;达阈自动提名摘门/退休,人批)。
    """
    import autoresearch.learning.feedback_store as fs

    out: list[dict] = []
    mkt = pd.to_numeric(attr.get("fwd_1_oo"), errors="coerce")
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
    """R3·门审计:gate_fires.csv × 已实现 fwd → 被拦票后来怎么走(excess<0 = 拦对)。纯读。"""
    cols = ["code", "check", "severity", "fwd_1_oo", "ex1", "fwd_5_oc", "ex5"]
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
    m5 = pd.to_numeric(a.get("fwd_5_oc"), errors="coerce").mean() if "fwd_5_oc" in a.columns else float("nan")
    out = fires.merge(a[[c for c in ("code", "fwd_1_oo", "fwd_5_oc") if c in a.columns]],
                      on="code", how="left")
    out["ex1"] = pd.to_numeric(out.get("fwd_1_oo"), errors="coerce") - m1
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
        res["day_ic_composite"] = round(sub["composite"].rank().corr(sub["fwd_1_oo"].rank()), 4)
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


def _buylist(date: str, report_root: Path | None = None) -> dict[str, str]:
    """读数据日=date 的已发布报告 details/<名称>.md → {code: 五档评级}。

    目录名现在是运行时刻(数据日在 manifest),由 `_report_dir_for` 解析定位;发布层卡名是**名称**,
    code 从卡内标题 `# 决策卡 — <code> <名称>` 取(复用 parse_rating 提评级)。
    """
    rdir = _report_dir_for(date, report_root or Path("reports/scan"))
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
    """全市场 D 的已实现 fwd_1_oo/fwd_5_oc + buyable(复用 factor_lab;按需拉 D..D+fwd 的 daily)。

    fwd 未实现(D+2 交易日还没到)→ 返回空(供 pending 判定)。
    """
    import autoresearch.research.factor_lab as fl
    from autoresearch.data.tushare_source import _trade_days

    cols = ["code", "fwd_1_oo", "fwd_5_oc", "fwd_10_oc", "hi_10_oc", "buyable", "gap_d1"]   # fwd_10/hi_10 供买后管理(未成熟=NaN)
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


def pending_days(today: str | None = None, scan_root: Path | None = None,
                 report_root: Path | None = None) -> list[str]:
    """未复盘 scan 日:有 L1 面板 + 有报告 + 无 retro/done.json + D 的 fwd 已实现。"""
    import autoresearch.research.factor_lab as fl
    from autoresearch.data.tushare_source import _trade_days

    today = today or datetime.now().strftime("%Y-%m-%d")
    scan_root = scan_root or Path("context/scan")
    report_root = report_root or Path("reports/scan")
    if not scan_root.exists():
        return []
    pro = fl._pro()
    cal = _trade_days(pro, "20240101", today.replace("-", ""))   # 日历已截到 today
    pos = {d: i for i, d in enumerate(cal)}
    out = []
    for dd in sorted(p for p in scan_root.iterdir() if p.is_dir()):
        date = dd.name
        if not (dd / "L1_scored_full.csv").exists():
            continue
        if (dd / "retro" / "done.json").exists():
            continue
        if _report_dir_for(date, report_root) is None:           # 无已发布报告(目录名=运行日,按 manifest 定位)
            continue
        i = pos.get(date.replace("-", ""))
        if i is not None and i + 2 < len(cal):                   # D+2 交易日 ≤ today → fwd 已实现
            out.append(date)
    return out


# ───────────────────────── 编排:attribute / retro_input / done ─────────────────────────

_KEEP = ["code", "name", "industry", "bucket", "winner", "news_pop", "fwd_1_oo", "fwd_5_oc",
         "fwd_10_oc", "hi_10_oc", "winner_5", "bucket_5",
         "gap_d1", "rank", "recalled_flag", "composite", "score_momentum", "score_fund_main",
         "score_chip", "pct_60d", "main_net_ratio", "winner_rate", "price_to_cost", "rsi6", "rating"]


def attribute(date: str, scan_root: Path | None = None, report_root: Path | None = None,
              abs_thresh: float = 0.03) -> pd.DataFrame:
    """单日归因 → 写 context/scan/<date>/retro/attribution.csv,返回全帧。"""
    scan_root = scan_root or Path("context/scan")
    sdir = scan_root / date
    l1 = pd.read_csv(sdir / "L1_scored_full.csv", dtype={"code": str})
    realized = realized_returns(date)
    if realized.empty:
        raise RuntimeError(f"{date} 的 fwd 未实现 / 无价格,暂不能复盘")
    attr = attribute_frame(l1, realized, _buylist(date, report_root), abs_thresh=abs_thresh)
    attr = flag_news_pop(attr)                       # 标隔夜跳空脉冲(诊断/重标定排除)
    outdir = sdir / "retro"
    outdir.mkdir(parents=True, exist_ok=True)
    attr[[c for c in _KEEP if c in attr.columns]].to_csv(outdir / "attribution.csv", index=False)
    pairs = build_retro_pairs(attr)                  # M1·同日 fail/success 对(成熟日才非空)
    if not pairs.empty:                              # presence-gated:未成熟日不落文件
        pairs.to_csv(outdir / "_retro_pairs.csv", index=False)
    return attr


def shadow_compare(attr: pd.DataFrame, sdir: Path) -> list[dict]:
    """影子漏斗对照:各变体 L2 vs 主 L2 的 T+1/T+5 赢家捕获数(design: calendar-shadow §2)。

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


def write_retro_input(date: str, attr: pd.DataFrame, scan_root: Path | None = None) -> Path:
    """把 stage_stats + 漏判赢家 top(带因子行)+ 选中对照写成 retro_input.md(喂诊断)。"""
    scan_root = scan_root or Path("context/scan")
    st = stage_stats(attr)
    lines = [f"# retro 输入 — {date}\n", "## 漏斗命中(对赢家)",
             f"- 当日可交易 universe:{st['n_universe_realized']};**赢家(前10%∧≥3%):{st['n_winners']}**",
             f"- 赢家进入 L1 召回池:{st['winners_in_l1']}/{st['n_winners']} "
             f"(到召回 {st['winner_to_l1']});被买单抓到:{st['winners_bought']}/{st['n_winners']} "
             f"(到买单 {st['winner_to_buylist']})",
             f"- 买单 {st['buylist_n']} 只,命中赢家 {st['buylist_hit']}(命中率 {st['buylist_hitrate']}),"
             f"误买(跌入底10%){st['buylist_fp']}",
             f"- 分桶:{st['buckets']};当日 composite IC(vs fwd_1_oo):{st['day_ic_composite']}\n"]

    def _tbl(df: pd.DataFrame, cols: list[str]) -> list[str]:
        cols = [c for c in cols if c in df.columns]
        head = "| " + " | ".join(cols) + " |"
        sep = "|" + "|".join(["---"] * len(cols)) + "|"
        rows = ["| " + " | ".join(str(r[c]) for c in cols) + " |" for _, r in df.iterrows()]
        return [head, sep, *rows]

    fcols = ["code", "name", "industry", "fwd_1_oo", "rank", "composite", "score_momentum",
             "main_net_ratio", "winner_rate", "price_to_cost", "rsi6", "pct_60d"]
    for label, bk in [("漏在 L0(门槛误杀)", "missed_l0"), ("漏在 L1(权重压低)", "missed_l1"),
                      ("L2-L3 误判(召回了却 cut)", "recalled_cut")]:
        sub = attr[attr["bucket"] == bk].sort_values("fwd_1_oo", ascending=False).head(15)
        lines += [f"\n## {label} — {len(attr[attr['bucket'] == bk])} 只(top 15)"]
        lines += _tbl(sub, fcols) if len(sub) else ["_无_"]
    caught = attr[attr["bucket"] == "caught"].sort_values("fwd_1_oo", ascending=False).head(10)
    lines += ["\n## 对照:抓到的赢家(caught, top 10)"]
    lines += _tbl(caught, fcols) if len(caught) else ["_无_"]

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
            lines += ["\n## L3 错杀验尸(L2-keep ∧ 非 finalist ∧ T+5 赢家;risk=当时红队理由)"]
            lines += _tbl(au, list(au.columns)) if len(au) else ["_无错杀(或 fwd_5 未成熟)_"]
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
            lines += [f"- **{r['variant']}**(n={r['n']}):T+1 捕获 {r['cap1']} vs 主 {r['cap1_main']};"
                      f"T+5 捕获 {r['cap5']} vs 主 {r['cap5_main']}" for r in sc]
    except Exception:  # noqa: BLE001
        pass

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
    wp = Path("context/factor_lab/weights.json")
    before_raw = wp.read_bytes() if wp.exists() else b"{}"
    before_sha = fs.snapshot_weights() or _sha8(before_raw)   # 快照留底(Phase 3 回滚)
    fl.calibrate(cap_floor=cap_floor, k=k)                    # 重写 weights.json(多日面板,绝非单日)
    after_raw = wp.read_bytes()
    before, after = json.loads(before_raw), json.loads(after_raw)
    tc = top_weight_changes(before.get("weights", {}).get("__global__", {}),
                            after.get("weights", {}).get("__global__", {}))
    after_sha, n_dates = _sha8(after_raw), int(after.get("meta", {}).get("n_dates", 0))
    fs.log_change(retro_date, before_sha, after_sha, tc, n_dates)
    return {"before_sha": before_sha, "after_sha": after_sha, "top_changes": tc, "n_dates": n_dates}


def refresh_attributions(scan_root: Path | None = None, report_root: Path | None = None,
                         max_days: int = 20) -> list[str]:
    """对已复盘(done)但 fwd 未成熟即落账的老日重写 attribution(幂等,价格走 cache)。

    需要刷新 = attribution 缺 `fwd_10_oc`/`hi_10_oc` 列,或 fwd_5/fwd_10 全 NaN。治"买单
    ledger 永远 —"(attribution 原为 retro 时一次性落账)。design: run-reliability §3。
    """
    scan_root = scan_root or Path("context/scan")
    if not scan_root.exists():
        return []
    today = datetime.now().strftime("%Y-%m-%d")
    days = sorted(p.name for p in scan_root.iterdir()
                  if p.is_dir() and p.name[:2] == "20" and p.name < today
                  and (p / "retro" / "done.json").exists()
                  and (p / "retro" / "attribution.csv").exists())[-max_days:]
    out: list[str] = []
    for d in days:
        try:
            attr = pd.read_csv(scan_root / d / "retro" / "attribution.csv")
        except Exception:  # noqa: BLE001
            continue
        need = ("fwd_10_oc" not in attr.columns or "hi_10_oc" not in attr.columns
                or pd.to_numeric(attr.get("fwd_5_oc"), errors="coerce").isna().all()
                or pd.to_numeric(attr.get("fwd_10_oc"), errors="coerce").isna().all())
        if not need:
            continue
        try:
            attribute(d, scan_root=scan_root, report_root=report_root)
            out.append(d)
        except Exception as e:  # noqa: BLE001 — 单日失败不阻其余
            print(f"[refresh] {d} 跳过: {e}", file=sys.stderr)
    return out


def mark_done(date: str, summary: dict | None = None, scan_root: Path | None = None) -> None:
    scan_root = scan_root or Path("context/scan")
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
    rows = []
    rows += [{"code": c, "fwd_1_oo": 0.10, "fwd_5_oc": 0.12, "buyable": True}
             for c in ("000001", "000002", "000003", "000004")]
    rows += [{"code": "000005", "fwd_1_oo": -0.09, "fwd_5_oc": -0.10, "buyable": True}]
    rows += [{"code": f"0001{i:02d}", "fwd_1_oo": (i - 10) * 0.002, "fwd_5_oc": 0.0, "buyable": True}
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
    empty = attribute_frame(l1, pd.DataFrame(columns=["code", "fwd_1_oo", "fwd_5_oc", "buyable"]), {})
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
        print("\n".join(pending_days()) or "(无待复盘日)")
        return 0
    if args and args[0] == "refresh":
        done = refresh_attributions()
        print(f"[refresh] 刷新 {len(done)} 日:{'、'.join(done) or '(无需刷新)'}")
        return 0
    if len(args) >= 2 and args[0] == "attribute":
        attr = attribute(args[1])
        write_retro_input(args[1], attr)
        st = stage_stats(attr)
        print(f"[retro] {args[1]} 赢家 {st['n_winners']},买单命中 {st['buylist_hit']}/{st['buylist_n']},"
              f"漏 {st['buckets'].get('missed_l1', 0)+st['buckets'].get('missed_l0', 0)},"
              f"误判 {st['buckets'].get('recalled_cut', 0)} → context/scan/{args[1]}/retro/")
        return 0
    print(__doc__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
