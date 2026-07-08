#!/usr/bin/env python3
"""scan-market · L3 精排的确定性 helper(紧凑表喂料 / 增量真证据 / finalists 合并)。

design: docs/specs/2026-06-22-autoresearch-arch-redesign-design.md §A/§D;Plan 4.1。

零 LLM。L3 holistic 选股(1 agent 通看 ~200 比较着选 30)由 skill 编排 subagent(见
screening-playbook.md);本模块只做**确定性喂料 + 取数 + 格式化**:把 ~200 只压成一张紧凑表、
对保留集补 L1 没有的真证据(龙虎榜/预告/快报)、把 holistic 入选排成 finalists(带趋势配额安全网)。
产物 staging 到 context/scan/<date>/。selftest 已迁 pytest(tests/scan/test_agents.py)。
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

# L3 holistic 选股 subagent 要看的紧凑列(GBDT 复合分/重排分 + 9 子分〔含 volprice 多日量价〕+ 关键原始
# 因子;量价位置/多日资金流/筹码/估值都在,够它一次通看 ~200 只比较着选 30)。
# 2026-07-06 瘦身:L3 max-effort 需更小输入才净提速 → 删 9 个 score_* 子分(composite 已含、与原始因子冗余)
# + retail_net_yi/chip_concentration/price_to_cost/hk_ratio(常 NaN)/dv_ratio/l2_lane_reserved/news_n·tags/med_n·tags·head。
# 保留 = 5 维 rubric 真正读的原始因子 + composite/gbdt + 情感 sent/head(42→22 列)。旧宽表见 git 历史。
_L3_COLS = ["code", "name", "industry", "composite", "gbdt_score",
            "pct_60d", "sector_mom", "vol_ratio", "cmf_20", "obv_mom_20", "main_net_ratio", "winner_rate",
            "rsi6", "pe", "pb", "np_yoy", "roe",
            "n_channels", "recall_channels",                   # 召回 provenance(channel 共振)
            "news_sent", "news_head", "med_sent"]              # 情感(净分 + 头条;counts/tags 已删)


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
    # Phase 3:并入公告情感 digest(L3_news/<code>.json,缺则缺省 0/""/—)。
    from autoresearch.scan.agents.l3_news import news_digest
    news_dir = root / date / "L3_news"
    drows = []
    for c in df["code"]:
        fp = news_dir / f"{c}.json"
        anns = json.loads(fp.read_text(encoding="utf-8")) if fp.exists() else []
        drows.append({"code": c, **news_digest(anns)})
    df = df.merge(pd.DataFrame(drows), on="code", how="left")
    # 媒体新闻情感 digest(L3_webnews/<code>.json,akshare stock_news_em;缺则缺省 0/""/—)。
    web_dir = root / date / "L3_webnews"
    mrows = []
    for c in df["code"]:
        fp = web_dir / f"{c}.json"
        web = json.loads(fp.read_text(encoding="utf-8")) if fp.exists() else []
        mrows.append({"code": c, **news_digest(web, prefix="med")})
    df = df.merge(pd.DataFrame(mrows), on="code", how="left")
    return df


def _prev_l3_day(date: str, root: Path | None = None) -> Path | None:
    """最近一个有 L3 现场(L3_judged_full + L2)的更早 scan 日;无 → None。"""
    root = root or Path("context/scan")
    if not root.exists():
        return None
    cands = sorted((p for p in root.iterdir()
                    if p.is_dir() and p.name[:2] == "20" and p.name < date
                    and (p / "L3_judged_full.csv").exists()
                    and (p / "L2_gbdt_top200.csv").exists()), reverse=True)
    return cands[0] if cands else None


def _delta_filter(df: pd.DataFrame, prev_dir: Path,
                  comp_tol: float = 2.0, mom_tol: float = 2.0) -> tuple[pd.DataFrame, list[str]]:
    """Δ模式过滤:略去「昨判弃 ∧ 今无变化」的行,保留行加 prev_l3 标记(选/弃)。

    变化 = |Δcomposite|>comp_tol ∨ |Δpct_60d|>mom_tol ∨ 今日有 lhb/预告/快报证据;
    prev 缺值 = 视为变(保守保留)。返回 (过滤后帧, 被略去 codes)。
    """
    jd = pd.read_csv(prev_dir / "L3_judged_full.csv", dtype={"code": str})
    judged = set(jd["code"].astype(str).str.zfill(6)) if "code" in jd.columns else set()
    fp = prev_dir / "finalists.csv"
    fin: set[str] = set()
    if fp.exists():
        fd = pd.read_csv(fp, dtype={"code": str})
        if "code" in fd.columns:
            fin = set(fd["code"].astype(str).str.zfill(6))
    rejected = judged - fin
    prev_l2 = pd.read_csv(prev_dir / "L2_gbdt_top200.csv", dtype={"code": str})
    prev_l2["code"] = prev_l2["code"].astype(str).str.zfill(6)
    keep_prev = [c for c in ("code", "composite", "pct_60d") if c in prev_l2.columns]
    m = df.merge(prev_l2[keep_prev], on="code", how="left", suffixes=("", "_prev"))
    dcomp = (pd.to_numeric(m.get("composite"), errors="coerce")
             - pd.to_numeric(m.get("composite_prev"), errors="coerce")).abs()
    dmom = (pd.to_numeric(m.get("pct_60d"), errors="coerce")
            - pd.to_numeric(m.get("pct_60d_prev"), errors="coerce")).abs()
    changed = dcomp.isna() | (dcomp > comp_tol) | dmom.isna() | (dmom > mom_tol)
    for c, is_bool in (("lhb_n", False), ("has_forecast", True), ("has_express", True)):
        if c in m.columns:
            v = m[c].fillna(False).astype(bool) if is_bool else pd.to_numeric(m[c], errors="coerce").fillna(0) > 0
            changed |= v
    is_rejected = m["code"].isin(rejected)
    drop = is_rejected & ~changed
    dropped = m.loc[drop, "code"].tolist()
    kept = df[~df["code"].isin(set(dropped))].copy()
    kept["prev_l3"] = kept["code"].map(
        lambda c: "选" if c in fin else ("弃" if c in rejected else ""))
    return kept, dropped


def l3_table_md(date: str, root: Path | None = None, delta: bool = False,
                shuffle_seed: int | None = None, sector_terrain: bool = False,
                dist_flag: bool = False, reg_flag: bool = False, cat_flag: bool = False,
                misread_flag: bool = False) -> str:
    """L3 holistic 选股 subagent 的完整输入表(~200 行紧凑表 + 证据摘要列)。

    delta=True:略去「昨判弃 ∧ 今无变化」行 + prev_l3 标记(design: l4-economy §3;
    默认 False = 逐字 parity;无前日 L3 现场 → 自动回退全量)。
    shuffle_seed:确定性乱序行序(稳定性抽检用;同 seed 同输出)。
    sector_terrain=True:前置**全行业确定性地形段**(申万一级对称覆盖,防"有 brief 行业被高看";
    design: 2026-07-03 海拔重构 §5.5;默认 False = 逐字 parity,缺 staging 自动跳过)。
    dist_flag=True:加 `main_inflow_yi`(绝对净额,亿)+ `main_dist`(反号/微量,谓词
    = `scoring.main_net_distortion_label` 单一事实源)两列 + 图例禁则——07-03 病灶:L3 把
    失真占比读成"真主力"打 conv 60-82,L4 再花 ~15 卡逐一辟谣(默认 False = 逐字 parity)。
    reg_flag=True:加 `news_reg` 列(近 10 日公告标题命中监管事项词,`l3_news.reg_hits`
    独立检测器——**不动 _EVENT_TAGS/news_digest,情感列口径不变**)+ 图例禁则
    (默认 False = 逐字 parity)。spec 2026-07-05 §5.3。
    cat_flag=True:加 `cat` 列(近 10 日 回购/增持/调研/减持 事件计数徽标,staging
    `L3_catalyst.csv` 在才生效)+ 图例禁则——事件存在性≠方向确认(默认 False = 逐字 parity)。
    spec 2026-07-05 wave §B2。
    misread_flag=True:加 misread 预警列(低基/背离/套牢,谓词=scoring.l3_misread_flags
    单一事实源)+图例禁则;默认 False = 逐字 parity。
    """
    df = load_l3_input(date, root=root)
    cols = [*_L3_COLS] + [c for c in ("lhb_n", "has_forecast", "has_express") if c in df.columns]
    header: list[str] = []
    if dist_flag and {"main_net_ratio", "main_inflow_yi"}.issubset(df.columns):
        from autoresearch.common.scoring import main_net_distortion_label
        df["main_dist"] = [main_net_distortion_label(r, a) for r, a in
                          zip(df["main_net_ratio"], df["main_inflow_yi"], strict=True)]
        cols = [*cols, "main_inflow_yi", "main_dist"]
        header += ["_⚠主力失真列(main_dist):**反号**=占比正而绝对净额(main_inflow_yi,亿)为负;"
                   "**微量**=占比≥2%而|绝对|<0.5亿(微盘/窗口放大)。两型下「主力净流入」"
                   "**不得单独作核心多头论点**,须绝对净额+cmf/obv 同向共振才算确认"
                   "(07-03 实证:该型 finalist 深核全数翻案)。_", ""]
    if reg_flag and "code" in df.columns:
        from autoresearch.scan.agents.l3_news import reg_hits_for_code
        day_dir = (root or Path("context/scan")) / date
        df["news_reg"] = [reg_hits_for_code(day_dir, c) for c in df["code"]]
        cols = [*cols, "news_reg"]
        header += ["_⚠监管旗(news_reg):近 10 日公告命中 立案/问询/关注函/处罚/违规/诉讼/"
                   "监管/证监会/交易所。旗票论点**必须显式回应监管事项**,不得无视;独立检测器,"
                   "情感列口径不变(非利空词表变更)。_", ""]
    if cat_flag and "code" in df.columns:
        catp = (root or Path("context/scan")) / date / "L3_catalyst.csv"
        if catp.exists():
            from autoresearch.scan.agents.l3_catalyst import cat_label
            try:
                cf = pd.read_csv(catp, dtype={"code": str})
                cf["code"] = cf["code"].astype(str).str.zfill(6)
                lab = {r["code"]: cat_label(r) for r in cf.to_dict("records")}
            except Exception:  # noqa: BLE001 — 坏 staging 降级不加列
                lab = None
            if lab is not None:
                df["cat"] = [lab.get(str(c).zfill(6), "") for c in df["code"]]
                cols = [*cols, "cat"]
                header += ["_📣催化列(cat):近 10 日 回购/增持/机构调研/减持 事件计数(存在性"
                           "≠方向确认)。催化须与资金/基本面共振才可作论点支柱;**减持≥2 的票"
                           "论点必须显式回应**。_", ""]
    if delta:
        prev = _prev_l3_day(date, root=root)
        if prev is None:
            header += ["_Δ模式:无前日 L3 现场 → 回退全量表_", ""]
        else:
            df, dropped = _delta_filter(df, prev)
            cols = [*cols, "prev_l3"]
            header += [f"_Δ模式(vs {prev.name}):略去 **{len(dropped)}** 只「昨判弃且无变化」票"
                       f"(重新入场条件:Δcomposite>2 或 Δpct60>2 或新 lhb/预告/快报);"
                       f"**今日仍须对下表独立重新比较,prev_l3 列仅供参考、严禁沿用昨日结论**;"
                       f"全量表每 ≤5 个 scan 日至少 1 次_", ""]
    if misread_flag:
        from autoresearch.common.scoring import l3_misread_flags
        df["misread"] = df.apply(l3_misread_flags, axis=1)
        cols = [*cols, "misread"]
        header.append(
            "misread 预警:低基=净利暴增但 ROE 极低(低基数幻觉,勿当真成长);背离=cmf/obv 正但当日主力净流出"
            "(拉高派发嫌疑);套牢=低获利盘·非多头排列·60日已涨(反弹撞套牢盘≠上行空间)。**旗亮仍以对应论点入选者,thesis 必须一句自证非陷阱**。")
    if shuffle_seed is not None:
        df = df.sample(frac=1, random_state=int(shuffle_seed)).reset_index(drop=True)
    table = compact_table(df, cols=cols)
    body = "\n".join([*header, table]) if header else table
    if sector_terrain:                      # Phase 3:全行业地形段前置(默认关 = 逐字 parity)
        try:
            from autoresearch.sector.pack import sector_terrain_md
            terr = sector_terrain_md((root or Path("context/scan")) / date, top200_only=True)
            if terr:
                body = terr + "\n\n" + body
        except Exception:  # noqa: BLE001 — 地形可选,缺 staging 不挡表
            pass
    return body


def stability_overlap(codes_a, codes_b) -> dict:
    """两次 L3 选择的重叠度(稳定性抽检:同表乱序重跑,<70% = rubric 太松/噪声大)。"""
    a = {str(c).zfill(6) for c in codes_a}
    b = {str(c).zfill(6) for c in codes_b}
    inter = a & b
    denom = min(len(a), len(b))
    return {"n_a": len(a), "n_b": len(b), "n_common": len(inter),
            "overlap": round(len(inter) / denom, 3) if denom else 0.0}


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
            "triage_lean", "triage_reason", "thesis", "risk", "catalyst", "lane", "sentiment"]
    return out[[c for c in cols if c in out.columns]]


def write_finalists(date: str, budget: int = 30, root: Path | None = None) -> dict:
    """确定性写 finalists.csv + L3_judged_full.csv(workflow L3 后确定性入口,取代手工 glue)。

    读 l3-rank agent 落的 _l3_judged.json → 从 L2 回填 pct_60d(供 merge 混合配额)
    → merge_l3_finalists_v2 → 写盘。**全程 6 位零填**,修 000062→62 的 CSV 往返坑。
    """
    base = Path(root) if root else Path("context/scan")
    scan_dir = base / date
    picks = json.loads((scan_dir / "_l3_judged.json").read_text(encoding="utf-8"))
    jd = pd.DataFrame(picks)
    if jd.empty or "code" not in jd.columns:
        raise ValueError(f"_l3_judged.json 空或缺 code 列:{scan_dir / '_l3_judged.json'}")
    jd["code"] = jd["code"].astype(str).str.zfill(6)
    l2p = scan_dir / "L2_gbdt_top200.csv"
    if l2p.exists() and "pct_60d" not in jd.columns:
        l2 = pd.read_csv(l2p, dtype={"code": str})
        l2["code"] = l2["code"].astype(str).str.zfill(6)
        if "pct_60d" in l2.columns:
            jd = jd.merge(l2[["code", "pct_60d"]], on="code", how="left")
    jd.to_csv(scan_dir / "L3_judged_full.csv", index=False)       # 全量判断(retro/assemble/trace)
    fin = merge_l3_finalists_v2(jd, target=budget)                # 内部 zfill code + ticker=code
    fin.to_csv(scan_dir / "finalists.csv", index=False)
    return {"judged_n": int(len(jd)), "finalists_n": int(len(fin))}


def prepare_l3_table(date: str, root: Path | None = None, delta: bool = True,
                     do_harvest: bool = True) -> dict:
    """L3 精排前的确定性件:harvest 证据/公告情感 + 构建紧凑表 → 写 _l3_table.md(l3-rank agent 读)。"""
    base = Path(root) if root else Path("context/scan")
    scan_dir = base / date
    l2 = pd.read_csv(scan_dir / "L2_gbdt_top200.csv", dtype={"code": str})
    codes = l2["code"].astype(str).str.zfill(6).tolist()
    if do_harvest:
        harvest_l3_evidence(date, codes, root=base)
        from autoresearch.scan.agents.l3_news import harvest_l3_news
        harvest_l3_news(date, codes, root=base)
    md = l3_table_md(date, root=base, delta=delta, dist_flag=True, reg_flag=True,
                     cat_flag=True, sector_terrain=True, misread_flag=True)
    (scan_dir / "_l3_table.md").write_text(md, encoding="utf-8")
    return {"codes": len(codes), "table_bytes": len(md)}


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="l3_select")
    ap.add_argument("cmd", choices=["finalists", "prepare"])
    ap.add_argument("date")
    ap.add_argument("--budget", type=int, default=30)
    ap.add_argument("--root", default=None)
    a = ap.parse_args(argv)
    if a.cmd == "finalists":
        res = write_finalists(a.date, budget=a.budget, root=a.root)
        print(f"[l3_select finalists] judged {res['judged_n']} → finalists {res['finalists_n']}")
    else:
        res = prepare_l3_table(a.date, root=a.root)
        print(f"[l3_select prepare] codes {res['codes']} → _l3_table.md {res['table_bytes']}B")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
