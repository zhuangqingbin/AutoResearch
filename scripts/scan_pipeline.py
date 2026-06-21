#!/usr/bin/env python3
"""scan-market · L3 精排 / L4 研究 的确定性 helper(L3 紧凑表 / 增量取数 / finalists 合并 / L4 级联选择器)。

零 LLM。AI 判断(L3 holistic 选股、L4 决策卡/红队)由 skill 编排 subagent(见 screening-playbook.md);
本模块只做喂料(把 ~200 只压成一张紧凑表)、增量真证据取数、finalists 格式化、L4 级联名单 + 评分卡,
产物 staging 到 context/scan/<date>/。

注:**L2 粗排已下沉到确定性层**——由 screen_market 的 GBDT 学习重排实现(见 factor_lab.train_gbdt /
predict_scores,模型 oos 未胜线性时自动回落 composite),本模块不再有 L2-AI keep/cut。

用法(被 skill 调用 / 自测):
  uv run --no-sync python scripts/scan_pipeline.py --selftest
"""
from __future__ import annotations

import sys
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


# ───────────────────────── L4:成本级联三层选择器(Tier-1 Sonnet / Tier-2 平反 / Tier-3 辩论) ─────────────────────────


def batch_finalists(df: pd.DataFrame, size: int = 3):
    """L4 Tier-1 分批:按 finalists 顺序每 size 只一个 subagent;yield (batch_idx, DataFrame)。

    宽筛阶段走 Sonnet,3 只/子代理摊薄重复子代理前导(~30 张卡 → ~10 个 subagent)。
    **这 ~10 个 subagent 由 skill 在一条消息里并发派发(并行启动),非顺序逐批**(见 screening-playbook.md)。
    """
    d = df.reset_index(drop=True)
    for i in range(0, len(d), size):
        yield i // size, d.iloc[i:i + size]


def parse_ratings_from_details(details_dir: Path | str) -> dict[str, str]:
    """读 details/*.md 决策卡,复用项目 `parse_rating` 提五档评级 → {code: rating}。

    code = 文件名 stem(6 位代码);读不到卡/无评级 → `parse_rating` 回退 'Hold'。
    """
    from tradingagents.agents.utils.rating import parse_rating  # 延迟导入,保持本模块轻量
    out: dict[str, str] = {}
    base = Path(details_dir)
    if not base.exists():
        return out
    for p in sorted(base.glob("*.md")):
        code = p.stem
        out[code.zfill(6) if code.isdigit() else code] = parse_rating(p.read_text(encoding="utf-8"))
    return out


def pick_buy_candidates(ratings: dict[str, str],
                        include: tuple[str, ...] = ("Buy", "Overweight")) -> list[str]:
    """L4 **Tier-3 多空辩论**名单:Tier-1(+Tier-2 平反)评级落在 include 的买点候选,直接进
    Tier-3 辩论(辩论既定级又证伪,吃掉旧 Tier-2 的单遍买点确认)。K2 默认 Buy/Overweight。"""
    keep = set(include)
    return [c for c, r in ratings.items() if r in keep]


def pick_buylist(ratings: dict[str, str], floor: str = "Overweight") -> list[str]:
    """评级 ≥ floor 的发布买单(floor=Overweight 时等价 pick_buy_candidates〔Buy/OW〕)。

    Tier-3 辩论输入用 `pick_buy_candidates`;本函数留作"最终买单"口径(Tier-3 折回后仍 ≥floor)。"""
    from tradingagents.agents.utils.rating import (
        RATINGS_5_TIER,  # Buy>Overweight>Hold>Underweight>Sell
    )
    order = {r: i for i, r in enumerate(RATINGS_5_TIER)}
    cap = order.get(floor, 1)
    return [c for c, r in ratings.items() if order.get(r, 99) <= cap]


def pick_downgrade_reviews(ratings: dict[str, str], finalists: pd.DataFrame,
                           conv_floor: float = 75, top_k: int = 5, max_rating: str = "Hold") -> list[str]:
    """L4 **Tier-2**(瘦,唯一职责=防假阴性平反,**条件触发**):Sonnet 把**高 conviction 的趋势 finalist**
    判到 ≤max_rating 的,才送 Opus 单遍复核平反——买点候选已直接进 Tier-3 辩论,Tier-2 只救误杀的边界假阴;
    名单空(无高 conviction 趋势被压)则 **Tier-2 完全不触发、零 Opus**。按 conviction 取 top_k。"""
    from tradingagents.agents.utils.rating import RATINGS_5_TIER
    order = {r: i for i, r in enumerate(RATINGS_5_TIER)}
    floor_idx = order.get(max_rating, 2)
    df = finalists.copy()
    df["code"] = df["code"].astype(str).str.zfill(6)
    conv = pd.to_numeric(df.get("conviction"), errors="coerce").fillna(0)
    lane = df["lane"] if "lane" in df.columns else pd.Series("", index=df.index)
    picks: list[tuple[str, float]] = []
    for i, c in enumerate(df["code"]):
        rt = ratings.get(c, "Hold")
        if lane.iloc[i] == "trend" and conv.iloc[i] >= conv_floor and order.get(rt, 9) >= floor_idx:
            picks.append((c, float(conv.iloc[i])))
    picks.sort(key=lambda x: -x[1])
    return [c for c, _ in picks[:top_k]]


# ───────────────────────── L4 · C:评级评分卡(LLM-as-judge rubric,确定性锚) ─────────────────────────

_RUBRIC_DIMS = ("基本面", "估值", "技术资金", "盈利质量", "偿付", "催化")
_DIM_SCORE = {"强": 1, "中": 0, "弱": -1}
_OW_GATES = ("主力真在", "业绩真兑现", "估值不透支")


def _norm_dim(k: str) -> str:
    """维度名归一:技术·资金→技术资金、偿付(爆雷)→偿付,去修饰/空白对齐锚键。"""
    s = str(k)
    for ch in "·()（）爆雷 　":
        s = s.replace(ch, "")
    return s


def rubric_rating(dims: dict, gates: dict) -> tuple[str, str]:
    """C·LLM-as-judge 评分卡:6 维(强+1/中0/弱−1)净分定档 + 3 道 OW 硬门 → 确定性建议评级 + 约束因。

    动机:Sonnet 凭 gestalt 过度多报(实测 6-18:10 OW vs Opus 3 OW),撑大 Tier-2 复核量。把评级
    **派生**自评分卡——净分映射档位,但**任一 OW 门未过则 ≥Overweight 一律压到 Hold**(对齐 Tier-1
    『三条全中才 OW』)。卡片据此自检:`**Rating**` 必须 = 建议,否则显式写 `**偏离**:<硬理由>`。

    dims: {维度: 强|中|弱}(缺/不识别按 中=0;键名容错 技术·资金 / 偿付(爆雷));
    gates: {主力真在|业绩真兑现|估值不透支: bool}(缺按 False 保守)。
    返回 (建议评级, 约束因)。
    """
    from tradingagents.agents.utils.rating import RATINGS_5_TIER  # Buy>OW>Hold>UW>Sell
    nd = {_norm_dim(k): v for k, v in (dims or {}).items()}
    net = sum(_DIM_SCORE.get(str(nd.get(d, "中")).strip(), 0) for d in _RUBRIC_DIMS)
    if net >= 4:
        base = "Buy"
    elif net >= 2:
        base = "Overweight"
    elif net >= -1:
        base = "Hold"
    elif net >= -3:
        base = "Underweight"
    else:
        base = "Sell"
    order = {r: i for i, r in enumerate(RATINGS_5_TIER)}
    failed = [g for g in _OW_GATES if not (gates or {}).get(g, False)]
    if order[base] < order["Hold"] and failed:        # 想给 ≥OW 但有门没过 → 压 Hold(防过度多报)
        return "Hold", f"净分{net:+d}→{base},OW门未过({'、'.join(failed)})→压Hold"
    suffix = "(OW门3/3)" if order[base] < order["Hold"] else ""
    return base, f"净分{net:+d}→{base}{suffix}"


# ───────────────────────── 离线自测 ─────────────────────────


def _selftest() -> int:
    import tempfile
    fails: list[str] = []

    # 1) L3 紧凑表 + load_l3_input(读 L2_gbdt_top200 + 证据摘要)
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "context/scan"
        d = root / "2026-06-20"
        (d / "L3_evidence").mkdir(parents=True)
        rows = [{"code": f"{i:06d}", "name": f"s{i}", "industry": "电子", "composite": 100 - i * 0.1,
                 "gbdt_score": 0.5, "score_momentum": 50, "score_fund_main": 40, "pct_60d": 10.0,
                 "main_net_ratio": 0.01, "winner_rate": 30.0, "np_yoy": 50.0} for i in range(200)]
        pd.DataFrame(rows).to_csv(d / "L2_gbdt_top200.csv", index=False)
        import json as _json
        (d / "L3_evidence" / "000000.json").write_text(
            _json.dumps({"code": "000000", "longhu": [{"x": 1}, {"x": 2}], "forecast": [{"y": 1}]}),
            encoding="utf-8")
        l3in = load_l3_input("2026-06-20", root=root)
        if len(l3in) != 200 or "lhb_n" not in l3in.columns:
            fails.append("load_l3_input 行数/证据列错")
        row0 = l3in[l3in["code"] == "000000"].iloc[0]
        if int(row0["lhb_n"]) != 2 or not bool(row0["has_forecast"]) or bool(row0["has_express"]):
            fails.append(f"L3 证据摘要错: lhb_n={row0['lhb_n']} fc={row0['has_forecast']} ex={row0['has_express']}")
        md = l3_table_md("2026-06-20", root=root)
        if "code" not in md or "000000" not in md or "lhb_n" not in md:
            fails.append("l3_table_md 缺列/数据")

    # 2) L3 finalists 合并(holistic 入选 → 趋势配额安全网):一半 conviction + 一半 pct_60d
    judged = pd.DataFrame({
        "code": ["000010", "000011", "000012", "000013", "000014"],
        "name": ["趋高conv", "回1", "回2", "回3", "趋高动量"],
        "sector": ["元件", "银行", "银行", "银行", "元件"], "lenses": ["动量"] * 5,
        "conviction": [75, 60, 58, 55, 50], "fragility": [50, 20, 20, 20, 45],  # 趋 net 低,回 net 高
        "thesis": ["t"] * 5, "risk": ["r"] * 5, "catalyst": ["c"] * 5,
        "triage_lean": ["看多"] * 5, "triage_reason": ["x"] * 5,
        "lane": ["trend", "reversion", "reversion", "reversion", "trend"],
        "pct_60d": [60, 5, 5, 5, 300]})        # 000014 动量最高但 conviction 最低
    out3 = merge_l3_finalists_v2(judged, target=3, trend_quota=2)   # quota 2 = 1 conv + 1 动量
    c3 = set(out3["code"])
    if "000010" not in c3:
        fails.append(f"L3 hybrid conviction 半未保住高conv趋势票: {list(out3['code'])}")
    if "000014" not in c3:
        fails.append(f"L3 hybrid 动量半未保住高pct_60d趋势票: {list(out3['code'])}")
    if len(out3) != 3:
        fails.append(f"L3 finalists 数错: {list(out3['code'])}")
    need = {"ticker", "code", "name", "sector", "conviction", "thesis", "risk", "catalyst", "lane"}
    if not need <= set(out3.columns):
        fails.append(f"L3 finalists 缺列 {need - set(out3.columns)}")

    # 3) L4 成本级联选择器:Tier-1 批分(10 批并发派发)/ 评级解析 / Tier-3 买点候选 / Tier-2 条件平反
    batched = [len(x[1]) for x in batch_finalists(pd.DataFrame({"code": [f"{i:06d}" for i in range(30)]}), size=3)]
    if batched != [3] * 10:
        fails.append(f"batch_finalists 30→10 批错: {batched}")
    with tempfile.TemporaryDirectory() as td:
        dd = Path(td) / "details"
        dd.mkdir(parents=True)
        cards = {"000001": "Buy", "000002": "Overweight", "000003": "Hold",
                 "000004": "Underweight", "000005": "Sell"}
        for code, rt in cards.items():
            (dd / f"{code}.md").write_text(
                f"# 决策卡\n**Rating**: {rt}\nFINAL TRANSACTION PROPOSAL: **HOLD**\n", encoding="utf-8")
        got = parse_ratings_from_details(dd)
        if got != cards:
            fails.append(f"parse_ratings_from_details 评级错: {got}")
        if set(pick_buy_candidates(got)) != {"000001", "000002"}:
            fails.append(f"pick_buy_candidates(Buy/OW) 错: {pick_buy_candidates(got)}")
        if set(pick_buylist(got, floor="Overweight")) != {"000001", "000002"}:
            fails.append(f"pick_buylist(≥OW) 错: {pick_buylist(got, floor='Overweight')}")
        if set(pick_buylist(got, floor="Buy")) != {"000001"}:
            fails.append(f"pick_buylist(≥Buy) 错: {pick_buylist(got, floor='Buy')}")
    # 3b) pick_downgrade_reviews(条件触发):高 conviction 趋势被 Sonnet 压 ≤Hold → 送 Tier-2;否则空(零 Opus)
    fdf = pd.DataFrame({"code": ["000021", "000022", "000023", "000024"],
                        "lane": ["trend", "trend", "reversion", "trend"],
                        "conviction": [90, 60, 90, 95]})
    rev = pick_downgrade_reviews({"000021": "Hold", "000022": "Hold", "000023": "Hold", "000024": "Overweight"},
                                 fdf, conv_floor=75, top_k=5)
    if set(rev) != {"000021"}:  # 趋势+高conv+被压Hold 才入;低conv/回归/已OW 都排除
        fails.append(f"pick_downgrade_reviews(高conv趋势被压Hold)错: {rev}")
    if pick_downgrade_reviews({"000021": "Overweight"}, fdf.head(1), conv_floor=75):
        fails.append("pick_downgrade_reviews 无假阴时应空(Tier-2 不触发)")

    # 4) C·rubric_rating:评分卡净分定档 + OW 门压 Hold(防 gestalt 过度多报);键名容错
    allgates = {"主力真在": True, "业绩真兑现": True, "估值不透支": True}
    strong = {"基本面": "强", "估值": "强", "技术·资金": "强", "盈利质量": "强", "偿付(爆雷)": "强", "催化": "强"}
    if rubric_rating(strong, allgates)[0] != "Buy":      # net+6 + 门全过
        fails.append(f"6维全强+门全过应 Buy: {rubric_rating(strong, allgates)}")
    ow = {"基本面": "强", "估值": "中", "技术·资金": "强", "盈利质量": "中", "偿付(爆雷)": "中", "催化": "中"}  # net+2
    if rubric_rating(ow, allgates)[0] != "Overweight":
        fails.append(f"净分+2+门全过应 OW: {rubric_rating(ow, allgates)}")
    r_gate = rubric_rating(ow, {"主力真在": False, "业绩真兑现": True, "估值不透支": True})   # 一门未过
    if r_gate[0] != "Hold" or "压Hold" not in r_gate[1]:
        fails.append(f"净分+2但OW门缺一应压 Hold: {r_gate}")
    flat = dict.fromkeys(("基本面", "估值", "技术·资金", "盈利质量", "偿付(爆雷)", "催化"), "中")     # net0
    if rubric_rating(flat, {})[0] != "Hold":
        fails.append(f"净分0应 Hold: {rubric_rating(flat, {})}")
    weak = dict.fromkeys(("基本面", "估值", "技术·资金", "盈利质量", "偿付(爆雷)", "催化"), "弱")     # net-6
    if rubric_rating(weak, allgates)[0] != "Sell":       # 门只压上行,不救下行
        fails.append(f"6维全弱应 Sell(门不救下行): {rubric_rating(weak, allgates)}")

    if fails:
        print("SELFTEST ❌")
        for f in fails:
            print("  -", f)
        return 1
    print("SELFTEST ✅  L3 紧凑表/load_l3_input(证据摘要)/l3_table_md + L3 finalists 合并(holistic→趋势配额)"
          "+ L4 三层选择器(批分并发/评级解析/Tier-3买点/Tier-2条件平反)+ C·rubric_rating(净分定档+OW门压Hold)全过")
    return 0


if __name__ == "__main__":
    raise SystemExit(_selftest() if "--selftest" in sys.argv else 0)
