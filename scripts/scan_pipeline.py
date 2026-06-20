#!/usr/bin/env python3
"""scan-market v2 · L2 粗排 / L3 精排 的确定性 helper(切片 / 紧凑表 / 配额合并 / L3 增量取数)。

零 LLM。AI 判断(keep/cut、论点/红队)由 skill 编排 subagent(见 screening-playbook.md);
本模块只做切片喂料、合并截断、增量真证据取数,产物 staging 到 context/scan/<date>/。

用法(被 skill 调用 / 自测):
  uv run --no-sync python scripts/scan_pipeline.py --selftest
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

# L2 subagent 要看的紧凑列(复合分 + 8 子分 + 关键原始因子)
_L2_COLS = ["code", "name", "industry", "composite",
            "score_momentum", "score_fund_main", "score_fund_retail", "score_chip",
            "score_north", "score_tech", "score_growth", "score_value",
            "pct_60d", "main_net_ratio", "retail_net_yi", "winner_rate",
            "chip_concentration", "price_to_cost", "hk_ratio", "rsi6", "pe", "pb",
            "dv_ratio", "np_yoy", "roe"]

# L2b 精简喂料列(12 列,降 token;双赛道判别只需核心因子)
_L2_COLS_LEAN = ["code", "name", "industry", "composite", "score_momentum",
                 "score_fund_main", "score_chip", "score_growth",
                 "pct_60d", "main_net_ratio", "winner_rate", "np_yoy"]


# ───────────────────────── L2:切片 + 紧凑表 + 配额合并 ─────────────────────────


def slice_recall(date: str, batch_size: int = 100, root: Path | None = None):
    """召回集按 composite 降序切片;yield (batch_idx, DataFrame)。"""
    root = root or Path("context/scan")
    df = pd.read_csv(root / date / "L1_recall_top1000.csv", dtype={"code": str})
    df = df.sort_values("composite", ascending=False).reset_index(drop=True)
    for i in range(0, len(df), batch_size):
        yield i // batch_size, df.iloc[i:i + batch_size]


def _fmt(v) -> str:
    if isinstance(v, float):
        return (f"{v:.2f}".rstrip("0").rstrip(".")) if v == v else "—"
    return str(v)


def compact_table(df: pd.DataFrame, lean: bool = False) -> str:
    """子集 → markdown 紧凑表,喂 L2 subagent;lean=True 用 12 列精简表(降 token)。"""
    cols = [c for c in (_L2_COLS_LEAN if lean else _L2_COLS) if c in df.columns]
    head = "| " + " | ".join(cols) + " |"
    sep = "|" + "|".join(["---"] * len(cols)) + "|"
    lines = [head, sep]
    for _, r in df[cols].iterrows():
        lines.append("| " + " | ".join(_fmt(r[c]) for c in cols) + " |")
    return "\n".join(lines)


def merge_l2_keeps(keep_frames: list[pd.DataFrame], recall: pd.DataFrame, target: int = 200) -> pd.DataFrame:
    """合并各批 subagent 的保留名单 → 取 target。排序键 = 归一(composite) × 归一(l2_score)。"""
    keeps = pd.concat([k for k in keep_frames if k is not None and len(k)], ignore_index=True)
    keeps["code"] = keeps["code"].astype(str).str.zfill(6)
    recall = recall.copy()
    recall["code"] = recall["code"].astype(str).str.zfill(6)
    m = keeps.merge(recall, on="code", how="left", suffixes=("", "_r"))
    for c in ("composite", "l2_score"):
        rng = m[c].max() - m[c].min()
        m[f"_n_{c}"] = (m[c] - m[c].min()) / rng if rng else 0.5
    m["_rank"] = m["_n_composite"] * m["_n_l2_score"]
    return m.sort_values("_rank", ascending=False).head(target).reset_index(drop=True)


# ───────────────────────── L2 v2:确定性分桶 + 双赛道 + 配额合并 ─────────────────────────


def l2_pre_bucket(recall: pd.DataFrame, min_reso_keep: int = 5) -> pd.DataFrame:
    """L2a 确定性分桶(零 LLM):逐只 classify_regime → 加
    resonance/regime/healthy_strong/exhausted/l2a_action/l2_lane 列。

    实测 6-18 召回 1000 切分 ≈ auto_keep 95 / auto_cut 535 / llm 370:
    - auto_keep:regime∈{趋势,回归} ∧ 共振≥min_reso_keep(默认5)∧ 非衰竭 → 免 LLM 直接留;
    - auto_cut:平庸(无共振无边际、与强势股无关)或 过热衰竭∧np<0(真破)→ 免 LLM 直接砍;
    - llm:其余争议带(趋势/回归共振不足、过热衰竭但业绩未破),按 regime 路由 lane。
    """
    from uzi_lenses import classify_regime

    m = recall.copy()
    m["code"] = m["code"].astype(str).str.zfill(6)
    recs = []
    for _, r in m.iterrows():
        cr = classify_regime(r.to_dict())
        regime, reso = cr["regime"], cr["resonance"]
        try:
            npy = float(r.get("np_yoy"))
            npy = None if npy != npy else npy
        except (TypeError, ValueError):
            npy = None
        if regime in ("趋势", "回归") and reso >= min_reso_keep and not cr["exhausted"]:
            action = "auto_keep"
            lane = "trend" if regime == "趋势" else "reversion"
        elif regime == "平庸" or (regime == "过热衰竭" and npy is not None and npy < 0):
            action, lane = "auto_cut", "—"
        else:
            action = "llm"
            lane = "trend" if regime in ("趋势", "过热衰竭") else "reversion"
        recs.append({"regime": regime, "resonance": reso,
                     "healthy_strong": cr["healthy_strong"], "exhausted": cr["exhausted"],
                     "l2a_action": action, "l2_lane": lane})
    return pd.concat([m.reset_index(drop=True), pd.DataFrame(recs)], axis=1)


def slice_l2_llm(bucketed: pd.DataFrame, lane: str, batch_size: int = 100):
    """筛 l2a_action=='llm' ∧ l2_lane==lane,按 composite 降序切片;yield (batch_idx, DataFrame)。"""
    df = bucketed[(bucketed["l2a_action"] == "llm") & (bucketed["l2_lane"] == lane)]
    df = df.sort_values("composite", ascending=False).reset_index(drop=True)
    for i in range(0, len(df), batch_size):
        yield i // batch_size, df.iloc[i:i + batch_size]


def merge_l2_keeps_v2(auto_keep: pd.DataFrame, trend_keeps, reversion_keeps,
                      recall: pd.DataFrame, target: int = 200, trend_quota: int = 50) -> pd.DataFrame:
    """合并 auto_keep + 两 lane 的 LLM keeps → target。

    先给 trend lane 命中者保底 trend_quota 席(强势科技不被回归票挤掉),再按
    `归一(composite)×归一(l2_score)` 填满;auto_keep 无 l2_score → 赋中性 70。
    """
    recall = recall.copy()
    recall["code"] = recall["code"].astype(str).str.zfill(6)

    def _norm_frame(frames, lane):
        parts = [f for f in frames if f is not None and len(f)]
        if not parts:
            return pd.DataFrame(columns=["code", "l2_score", "l2_reason", "_lane"])
        df = pd.concat(parts, ignore_index=True)
        df["code"] = df["code"].astype(str).str.zfill(6)
        df["_lane"] = lane
        return df[["code", "l2_score", "l2_reason", "_lane"]]

    if auto_keep is not None and len(auto_keep):
        ak = auto_keep.copy()
        ak["code"] = ak["code"].astype(str).str.zfill(6)
        ak["l2_score"] = 70
        ak["l2_reason"] = "L2a自动留(强共振无衰竭)"
        ak["_lane"] = ak["l2_lane"] if "l2_lane" in ak.columns else "trend"
        ak = ak[["code", "l2_score", "l2_reason", "_lane"]]
    else:
        ak = pd.DataFrame(columns=["code", "l2_score", "l2_reason", "_lane"])

    pool = pd.concat([ak, _norm_frame(trend_keeps, "trend"), _norm_frame(reversion_keeps, "reversion")],
                     ignore_index=True).drop_duplicates(subset="code", keep="first")
    m = pool.merge(recall, on="code", how="left", suffixes=("", "_r"))
    for c in ("composite", "l2_score"):
        m[c] = pd.to_numeric(m[c], errors="coerce")
        rng = m[c].max() - m[c].min()
        m[f"_n_{c}"] = (m[c] - m[c].min()) / rng if rng else 0.5
    m["_rank"] = m["_n_composite"].fillna(0.5) * m["_n_l2_score"].fillna(0.5)
    m = m.sort_values("_rank", ascending=False).reset_index(drop=True)

    # 先保底 trend 席位(按 rank 取 trend lane top-quota),再用全池填满
    trend_pool = m[m["_lane"] == "trend"]
    reserved = trend_pool.head(min(trend_quota, len(trend_pool)))
    rest = m[~m["code"].isin(set(reserved["code"]))]
    out = pd.concat([reserved, rest], ignore_index=True).drop_duplicates(subset="code", keep="first")
    return out.head(target).reset_index(drop=True)


# ───────────────────────── L3:增量真证据 + finalists 合并 ─────────────────────────


def _period(date: str) -> str:
    from screen_market import latest_reported_quarter
    return latest_reported_quarter(date)


def harvest_l3_evidence(date: str, codes: list[str], root: Path | None = None) -> dict:
    """对 L2 保留的 ~200 只补 L1 没有的真证据(龙虎榜/预告/快报)。bulk by date 一次拉、本地过滤;

    失败/无权限降级标注。产出 context/scan/<date>/L3_evidence/<code>.json,返回 {code: evidence}。
    """
    import json

    from tushare_source import _code6, _pro, _ts_call, resolve_momentum_dates
    root = root or Path("context/scan")
    out_dir = root / date / "L3_evidence"
    out_dir.mkdir(parents=True, exist_ok=True)
    pro = _pro()
    last = resolve_momentum_dates(pro, date)[0]
    want = {str(c).zfill(6) for c in codes}
    ev: dict[str, dict] = {c: {"code": c} for c in want}

    def _bulk(label, fn, key_field="ts_code"):
        try:
            df = _ts_call(fn)
            if df is None or df.empty:
                return
            df = df.assign(_c=_code6(df[key_field]))
            for c, g in df[df["_c"].isin(want)].groupby("_c"):
                ev[c].setdefault(label, []).extend(g.drop(columns=["_c"]).to_dict("records"))  # 累积(可多日)
        except Exception as e:  # noqa: BLE001
            ev.setdefault("_errors", {}).setdefault(label, str(e))   # 端点级错误记一次,不污染每只

    _bulk("longhu", lambda: pro.top_list(trade_date=last))           # 龙虎榜席位(游资/机构)
    # forecast/express 需 ann_date 或 ts_code(period 单参不够)→ 扫最近 ~10 个交易日的公告
    from datetime import datetime, timedelta

    from tushare_source import _trade_days
    start = (datetime.strptime(last, "%Y%m%d") - timedelta(days=30)).strftime("%Y%m%d")
    for dd in _trade_days(pro, start, last)[-10:]:
        _bulk("forecast", lambda dd=dd: pro.forecast(ann_date=dd))   # 业绩预告
        _bulk("express", lambda dd=dd: pro.express(ann_date=dd))     # 快报
    for c in want:
        (out_dir / f"{c}.json").write_text(json.dumps(ev[c], ensure_ascii=False, default=str), encoding="utf-8")
    return ev


def merge_l3_finalists(judged: pd.DataFrame, target: int = 30) -> pd.DataFrame:
    """按 确信度−脆弱度 取 target;输出 finalists.csv 列(兼容 L4/L5,+thesis/risk/catalyst)。"""
    m = judged.copy()
    m["code"] = m["code"].astype(str).str.zfill(6)
    m["net"] = m["conviction"].fillna(0) - m["fragility"].fillna(0)
    m = m.sort_values("net", ascending=False).head(target).reset_index(drop=True)
    m["ticker"] = m["code"]                       # harvester 自动补 .SS/.SZ/.BJ
    cols = ["ticker", "code", "name", "sector", "lenses", "conviction",
            "triage_lean", "triage_reason", "thesis", "risk", "catalyst"]
    return m[[c for c in cols if c in m.columns]]


def merge_l3_finalists_v2(judged: pd.DataFrame, target: int = 30, trend_quota: int = 10,
                          hybrid: bool = True) -> pd.DataFrame:
    """趋势 lane 感知的 finalists 合并:先给 trend lane(非回避)保底 trend_quota 席,再用
    `conviction−fragility` 填满。

    动机:趋势/强势票的高 fragility 多是 T+1/短期回撤概念,swing 视角不该一票否决——否则越强
    的票越被 net 挤出精排(实测:生益/亨通 conv 高但 frag 高 → net 低 → 进不了 top30)。
    - hybrid=True(默认):配额**一半按 conviction**(质量趋势:健康强势+主力在)+ **一半按 pct_60d**
      (动量龙头:最热的强势票)→ 兼得"健康强势"与"市场最热龙头"(实测:纯 conviction 捞不回生益/逸豪,
      纯 pct_60d 又丢质量;拆分两全)。需 `pct_60d` 列,缺则退化为纯 conviction 配额。
    - judged 需含 `lane` 列(无则退化为纯 net 排序 = 旧行为)。
    """
    m = judged.copy()
    m["code"] = m["code"].astype(str).str.zfill(6)
    for c in ("conviction", "fragility", "pct_60d"):
        if c in m.columns:
            m[c] = pd.to_numeric(m[c], errors="coerce")
    m["net"] = m["conviction"].fillna(0) - m["fragility"].fillna(0)

    is_trend = (m["lane"] == "trend") if "lane" in m.columns else pd.Series(False, index=m.index)
    not_avoid = (m["triage_lean"] != "回避") if "triage_lean" in m.columns else pd.Series(True, index=m.index)
    cand = m[is_trend & not_avoid]
    reserved_codes: list[str] = []
    if hybrid and "pct_60d" in m.columns and trend_quota > 0:
        n_conv = trend_quota // 2
        reserved_codes += list(cand.sort_values("conviction", ascending=False).head(n_conv)["code"])
        by_mom = cand[~cand["code"].isin(reserved_codes)].sort_values("pct_60d", ascending=False)
        reserved_codes += list(by_mom.head(trend_quota - n_conv)["code"])
    else:
        reserved_codes = list(cand.sort_values("conviction", ascending=False).head(max(0, trend_quota))["code"])

    reserved = m[m["code"].isin(reserved_codes)]
    rest = m[~m["code"].isin(set(reserved_codes))].sort_values("net", ascending=False)
    out = (pd.concat([reserved, rest], ignore_index=True)
           .drop_duplicates(subset="code", keep="first").head(target))
    out = out.sort_values("net", ascending=False).reset_index(drop=True)
    out["ticker"] = out["code"]
    cols = ["ticker", "code", "name", "sector", "lenses", "conviction",
            "triage_lean", "triage_reason", "thesis", "risk", "catalyst", "lane"]
    return out[[c for c in cols if c in out.columns]]


# ───────────────────────── 离线自测 ─────────────────────────


def _selftest() -> int:
    import tempfile
    fails: list[str] = []

    # 1) 切片 + 紧凑表
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "context/scan"
        d = root / "2026-06-20"
        d.mkdir(parents=True)
        rows = [{"code": f"{i:06d}", "name": f"s{i}", "industry": "电子", "composite": 100 - i * 0.1,
                 "score_momentum": 50, "score_fund_main": 40, "pct_60d": 10.0,
                 "main_net_ratio": 0.01, "winner_rate": 30.0} for i in range(250)]
        pd.DataFrame(rows).to_csv(d / "L1_recall_top1000.csv", index=False)
        batches = list(slice_recall("2026-06-20", batch_size=100, root=root))
        if [len(b[1]) for b in batches] != [100, 100, 50]:
            fails.append(f"切片大小错: {[len(b[1]) for b in batches]}")
        md = compact_table(batches[0][1])
        if "code" not in md or "000000" not in md:
            fails.append("紧凑表缺列/数据")

    # 2) L2 配额合并
    keeps = [pd.DataFrame({"code": ["000001", "000002"], "l2_score": [80, 60], "l2_reason": ["强", "中"]}),
             pd.DataFrame({"code": ["000003"], "l2_score": [90], "l2_reason": ["很强"]})]
    recall = pd.DataFrame({"code": ["000001", "000002", "000003"], "industry": ["A", "A", "B"],
                           "composite": [99, 70, 85]})
    out2 = merge_l2_keeps(keeps, recall, target=2)
    if len(out2) != 2 or "000003" not in set(out2["code"]):
        fails.append(f"L2 合并取 top2 错: {list(out2['code'])}")

    # 3) L3 finalists 合并(确信度−脆弱度)
    judged = pd.DataFrame({
        "code": ["000001", "000002", "000003"], "name": ["a", "b", "c"], "sector": ["电子"] * 3,
        "lenses": ["动量"] * 3, "conviction": [80, 50, 90], "fragility": [20, 40, 70],
        "thesis": ["t1", "t2", "t3"], "risk": ["r1", "r2", "r3"], "catalyst": ["c1", "c2", "c3"],
        "triage_lean": ["看多"] * 3, "triage_reason": ["x"] * 3})
    out3 = merge_l3_finalists(judged, target=2)
    if set(out3["code"]) != {"000001", "000003"}:
        fails.append(f"L3 net 排序错: {list(out3['code'])}")
    need = {"ticker", "code", "name", "sector", "lenses", "conviction",
            "triage_lean", "triage_reason", "thesis", "risk", "catalyst"}
    if not need <= set(out3.columns):
        fails.append(f"L3 缺列 {need - set(out3.columns)}")

    # 3b) L3 v2 hybrid:配额一半 conviction(高conv趋势)+ 一半 pct_60d(高动量趋势),都不被 net 挤掉
    judged2 = pd.DataFrame({
        "code": ["000010", "000011", "000012", "000013", "000014"],
        "name": ["趋高conv", "回1", "回2", "回3", "趋高动量"],
        "sector": ["元件", "银行", "银行", "银行", "元件"], "lenses": ["动量"] * 5,
        "conviction": [75, 60, 58, 55, 50], "fragility": [50, 20, 20, 20, 45],  # 趋 net 25/5 低,回 net 40·38·35
        "thesis": ["t"] * 5, "risk": ["r"] * 5, "catalyst": ["c"] * 5,
        "triage_lean": ["看多"] * 5, "triage_reason": ["x"] * 5,
        "lane": ["trend", "reversion", "reversion", "reversion", "trend"],
        "pct_60d": [60, 5, 5, 5, 300]})        # 000014 动量最高但 conviction 最低
    out3b = merge_l3_finalists_v2(judged2, target=3, trend_quota=2)   # quota 2 = 1 conv + 1 动量
    c3b = set(out3b["code"])
    if "000010" not in c3b:
        fails.append(f"L3 hybrid conviction 半未保住高conv趋势票: {list(out3b['code'])}")
    if "000014" not in c3b:
        fails.append(f"L3 hybrid 动量半未保住高pct_60d趋势票: {list(out3b['code'])}")
    if len(out3b) != 3:
        fails.append(f"L3 v2 数错: {list(out3b['code'])}")

    # 4) L2 v2:确定性分桶 + lane 切片 + 精简表 + 配额合并
    rec4 = pd.DataFrame([
        {"code": "100001", "name": "强", "industry": "电子", "composite": 90, "pct_60d": 120,
         "main_net_ratio": 0.02, "winner_rate": 80, "np_yoy": 90, "rsi6": 70, "score_momentum": 80,
         "score_fund_main": 65, "score_chip": 70, "score_growth": 75, "score_tech": 70,
         "score_north": 0, "score_value": 30},  # 健康强势高共振 → auto_keep trend
        {"code": "100002", "name": "衰", "industry": "电子", "composite": 60, "pct_60d": 300,
         "main_net_ratio": -0.05, "winner_rate": 77, "np_yoy": -100, "rsi6": 80, "score_momentum": 99,
         "score_fund_main": 10, "score_chip": 40, "score_growth": 10, "score_tech": 90,
         "score_north": 0, "score_value": 0},  # 衰竭 np<0 → auto_cut
        {"code": "100003", "name": "回", "industry": "医药", "composite": 70, "pct_60d": 12,
         "main_net_ratio": 0.03, "winner_rate": 30, "np_yoy": 20, "rsi6": 50, "score_momentum": 30,
         "score_fund_main": 60, "score_chip": 40, "score_growth": 65, "score_tech": 30,
         "score_north": 0, "score_value": 70},  # 低位回归共振3 → llm reversion
        {"code": "100004", "name": "庸", "industry": "钢铁", "composite": 50, "pct_60d": 5,
         "main_net_ratio": -0.005, "winner_rate": 50, "np_yoy": 3, "rsi6": 48, "score_momentum": 30,
         "score_fund_main": 30, "score_chip": 30, "score_growth": 30, "score_tech": 30,
         "score_north": 0, "score_value": 30},  # 平庸无共振 → auto_cut
        {"code": "100005", "name": "趋llm", "industry": "电子", "composite": 80, "pct_60d": 60,
         "main_net_ratio": 0.01, "winner_rate": 70, "np_yoy": 40, "rsi6": 65, "score_momentum": 75,
         "score_fund_main": 40, "score_chip": 40, "score_growth": 65, "score_tech": 65,
         "score_north": 0, "score_value": 30},  # 趋势但共振3 → llm trend
    ])
    b = l2_pre_bucket(rec4)
    by = dict(zip(b["code"], b["l2a_action"], strict=True))
    lanes = dict(zip(b["code"], b["l2_lane"], strict=True))
    if by.get("100001") != "auto_keep":
        fails.append(f"健康强势高共振应 auto_keep: {by}")
    if by.get("100002") != "auto_cut" or by.get("100004") != "auto_cut":
        fails.append(f"衰竭/平庸应 auto_cut: {by}")
    if lanes.get("100005") != "trend" or lanes.get("100003") != "reversion":
        fails.append(f"llm lane 路由错: {lanes}")
    tb = list(slice_l2_llm(b, "trend"))
    tcodes = set(pd.concat([x[1] for x in tb])["code"]) if tb else set()
    if "100005" not in tcodes or "100003" in tcodes:
        fails.append(f"slice_l2_llm trend 过滤错: {tcodes}")
    if compact_table(rec4, lean=True).count("|") >= compact_table(rec4, lean=False).count("|"):
        fails.append("lean 精简表未减少列")
    # 配额合并:弱趋势票(rank 低)被 trend_quota 救入,挤掉更强的回归票
    recall_m = pd.DataFrame({"code": ["100005", "100003", "100006"],
                             "industry": ["电子", "医药", "医药"], "composite": [60, 85, 88]})
    out4 = merge_l2_keeps_v2(
        pd.DataFrame(columns=["code", "l2_lane"]),
        [pd.DataFrame({"code": ["100005"], "l2_score": [55], "l2_reason": ["趋势弱"]})],
        [pd.DataFrame({"code": ["100003", "100006"], "l2_score": [92, 95], "l2_reason": ["回强", "回强"]})],
        recall_m, target=2, trend_quota=1)
    if len(out4) != 2:
        fails.append(f"v2 合并数错: {len(out4)}")
    if "100005" not in set(out4["code"]):
        fails.append(f"trend 保底席位未把弱趋势票救入: {list(out4['code'])}")

    if fails:
        print("SELFTEST ❌")
        for f in fails:
            print("  -", f)
        return 1
    print("SELFTEST ✅  L2 切片/紧凑表/配额合并 + L2v2(分桶/lane切片/精简表/保底席位)"
          "+ L3 finalists 合并(确信度−脆弱度)全过")
    return 0


if __name__ == "__main__":
    raise SystemExit(_selftest() if "--selftest" in sys.argv else 0)
