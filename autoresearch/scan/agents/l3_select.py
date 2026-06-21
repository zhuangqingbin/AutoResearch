#!/usr/bin/env python3
"""scan-market · L3 精排的确定性 helper(紧凑表喂料 / 增量真证据 / finalists 合并)。

design: docs/specs/2026-06-22-autoresearch-arch-redesign-design.md §A/§D;Plan 4.1。

零 LLM。L3 holistic 选股(1 agent 通看 ~200 比较着选 30)由 skill 编排 subagent(见
screening-playbook.md);本模块只做**确定性喂料 + 取数 + 格式化**:把 ~200 只压成一张紧凑表、
对保留集补 L1 没有的真证据(龙虎榜/预告/快报)、把 holistic 入选排成 finalists(带趋势配额安全网)。
产物 staging 到 context/scan/<date>/。selftest 已迁 pytest(tests/scan/test_agents.py)。
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

# L3 holistic 选股 subagent 要看的紧凑列(GBDT 复合分/重排分 + 9 子分〔含 volprice 多日量价〕+ 关键原始
# 因子;量价位置/多日资金流/筹码/估值都在,够它一次通看 ~200 只比较着选 30)。
_L3_COLS = ["code", "name", "industry", "composite", "gbdt_score",
            "score_momentum", "score_fund_main", "score_fund_retail", "score_chip",
            "score_north", "score_tech", "score_growth", "score_value", "score_volprice",
            "pct_60d", "vol_ratio", "cmf_20", "obv_mom_20", "main_net_ratio", "retail_net_yi", "winner_rate",
            "chip_concentration", "price_to_cost", "hk_ratio", "rsi6", "pe", "pb",
            "dv_ratio", "np_yoy", "roe"]


# ───────────────────────── L3:紧凑表 + 增量真证据 + finalists 合并 ─────────────────────────


def _fmt(v) -> str:
    if isinstance(v, float):
        return (f"{v:.2f}".rstrip("0").rstrip(".")) if v == v else "—"
    return str(v)


def compact_table(df: pd.DataFrame, cols: list[str] | None = None) -> str:
    """子集 → markdown 紧凑表(喂 L3 holistic 选股 subagent;一行一只,~200 只一次通看、比较着选)。"""
    cols = [c for c in (cols or _L3_COLS) if c in df.columns]
    head = "| " + " | ".join(cols) + " |"
    sep = "|" + "|".join(["---"] * len(cols)) + "|"
    lines = [head, sep]
    for _, r in df[cols].iterrows():
        lines.append("| " + " | ".join(_fmt(r[c]) for c in cols) + " |")
    return "\n".join(lines)


def load_l3_input(date: str, root: Path | None = None) -> pd.DataFrame:
    """读 L2 粗排产物(L2_gbdt_top200.csv)+ 合并已 harvest 的 L3 增量证据摘要 → L3 选股输入帧。

    证据摘要列(表内一眼可见,不必逐 json 翻):lhb_n(龙虎榜上榜条数)、has_forecast/has_express
    (预告/快报有无)。证据未 harvest → 三列缺省 0/False。
    """
    import json
    root = root or Path("context/scan")
    df = pd.read_csv(root / date / "L2_gbdt_top200.csv", dtype={"code": str})
    df["code"] = df["code"].astype(str).str.zfill(6)
    ev_dir = root / date / "L3_evidence"
    if ev_dir.exists():
        rows = []
        for c in df["code"]:
            fp = ev_dir / f"{c}.json"
            if fp.exists():
                ev = json.loads(fp.read_text(encoding="utf-8"))
                rows.append({"code": c, "lhb_n": len(ev.get("longhu", [])),
                             "has_forecast": bool(ev.get("forecast")), "has_express": bool(ev.get("express"))})
            else:
                rows.append({"code": c, "lhb_n": 0, "has_forecast": False, "has_express": False})
        df = df.merge(pd.DataFrame(rows), on="code", how="left")
    return df


def l3_table_md(date: str, root: Path | None = None) -> str:
    """L3 holistic 选股 subagent 的完整输入表(~200 行紧凑表 + 证据摘要列)。"""
    df = load_l3_input(date, root=root)
    cols = [*_L3_COLS] + [c for c in ("lhb_n", "has_forecast", "has_express") if c in df.columns]
    return compact_table(df, cols=cols)


def _period(date: str) -> str:
    from autoresearch.common.scoring import latest_reported_quarter
    return latest_reported_quarter(date)


def harvest_l3_evidence(date: str, codes: list[str], root: Path | None = None) -> dict:
    """对 L2 保留的 ~200 只补 L1 没有的真证据(龙虎榜/预告/快报)。bulk by date 一次拉、本地过滤;

    失败/无权限降级标注。产出 context/scan/<date>/L3_evidence/<code>.json,返回 {code: evidence}。
    """
    import json

    from autoresearch.data.tushare_source import _code6, _pro, _ts_call, resolve_momentum_dates
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

    from autoresearch.data.tushare_source import _trade_days
    start = (datetime.strptime(last, "%Y%m%d") - timedelta(days=30)).strftime("%Y%m%d")
    for dd in _trade_days(pro, start, last)[-10:]:
        _bulk("forecast", lambda dd=dd: pro.forecast(ann_date=dd))   # 业绩预告
        _bulk("express", lambda dd=dd: pro.express(ann_date=dd))     # 快报
    for c in want:
        (out_dir / f"{c}.json").write_text(json.dumps(ev[c], ensure_ascii=False, default=str), encoding="utf-8")
    return ev


def merge_l3_finalists_v2(judged: pd.DataFrame, target: int = 30, trend_quota: int = 10,
                          hybrid: bool = True) -> pd.DataFrame:
    """格式化 L3 holistic 选股 agent 的入选 → finalists.csv(L4/L5 读),并做趋势配额安全网。

    holistic 单 agent 通看 ~200 只、比较着选 ~30(各只带 conviction/fragility/thesis/risk/catalyst/lane)。
    本函数把它的入选排成 finalists:先给 trend lane(非回避)保底 trend_quota 席(强势票的高 fragility 多是
    T+1/短期回撤概念,swing 视角不该被 `conviction−fragility` 一票挤出),再按 net 填满。
    - hybrid=True(默认):配额**一半按 conviction**(质量趋势:健康强势+主力在)+ **一半按 pct_60d**
      (动量龙头:最热的强势票)→ 兼得"健康强势"与"市场最热龙头"。需 `pct_60d` 列,缺则退化为纯 conviction 配额。
    - judged 需含 `lane` 列(无则退化为纯 net 排序)。
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
