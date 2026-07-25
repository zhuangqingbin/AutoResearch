#!/usr/bin/env python3
"""scan-market · 市场级确定性聚合 —— 首席策略师数据包 + L3/L4 注入地形块 + L5 尾注/回退。

design: docs/specs/2026-07-01-scan-market-strategist-view-design.md

零 LLM。market_pack 从 L1_scored_full + sectors.csv 聚合"今日市场"事实(regime/宽度/估值分散/
资金/板块红黑榜);market_context_block 派生**描述性**地形块喂 L3/L4(防锚定:只描述不指令);
render_funnel_readout 给 L5 确定性漏斗读数尾注;render_fallback_pulse 给 market_view 缺失时回退。
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from autoresearch.common.regime import classify_regime

_REGIME_ZH = {"trend": "趋势", "range": "震荡", "risk_off": "避险"}

# ── P7:确定性看多行业 top3 带参(spec 2026-07-12-scan-speed-perimeter §P7)──
_TOP3_MIN_N = 8            # 资格门①:成分数(剔 n=1 噪声行业)
_TOP3_KNIFE = -20.0        # 资格门③:非落刀(60日中位)
_TOP3_MOM_CENTER = 10.0    # 倒U 动量带中心
_TOP3_MOM_HALF = 15.0      # 倒U 半宽


def _num(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series([], dtype=float)
    return pd.to_numeric(df[col], errors="coerce")


def _frac_of(s: pd.Series, cond) -> float | None:
    """s 丢 NaN 后满足 cond 的占比(先 dropna 值再比较,对齐 classify_regime);空 → None。"""
    s = s.dropna()
    return round(float(cond(s).mean()), 4) if len(s) else None


def _med(s: pd.Series) -> float | None:
    s = s.dropna()
    return round(float(s.median()), 2) if len(s) else None


def _quantile(s: pd.Series, q: float) -> float | None:
    s = s.dropna()
    return round(float(s.quantile(q)), 2) if len(s) else None


def _round(v, nd: int = 2):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else round(f, nd)   # NaN → None


def _breadth(df: pd.DataFrame) -> dict:
    p60 = _num(df, "pct_60d")
    return {
        "above_ma60": _frac_of(_num(df, "above_ma60"), lambda x: x > 0),
        "ma_bull": _frac_of(_num(df, "ma_bull"), lambda x: x > 0),
        "med_pct_60d": _med(p60),
        "med_pct_ytd": _med(_num(df, "pct_ytd")),
        "falling_knife": _frac_of(p60, lambda x: x < -20),
        "up_60d": _frac_of(p60, lambda x: x > 0),
    }


def _valuation(df: pd.DataFrame) -> dict:
    pe = _num(df, "pe")
    pe_pos = pe[pe > 0]
    return {
        "med_pe": _med(pe_pos),
        "med_pb": _med(_num(df, "pb")),
        "pe_top_decile": _quantile(pe_pos, 0.90),
        "pe_gt_60": _frac_of(pe_pos, lambda x: x > 60),
    }


def _money(df: pd.DataFrame) -> dict:
    mnr = _num(df, "main_net_ratio")
    return {
        "main_pos": _frac_of(mnr, lambda x: x > 0),
        "med_main_ratio": _med(mnr),
        "cmf_pos": _frac_of(_num(df, "cmf_20"), lambda x: x > 0),
    }


def _sectors(sec: pd.DataFrame, n: int = 5) -> dict | None:
    if "median_pct_60d" not in sec.columns or not len(sec):
        return None
    s = sec.copy()
    s["_m"] = pd.to_numeric(s["median_pct_60d"], errors="coerce")
    s = s.dropna(subset=["_m"]).sort_values("_m", ascending=False)
    if not len(s):
        return None

    def _row(r) -> dict:
        return {"industry": r.get("industry"),
                "n_recall": int(r["n_recall"]) if pd.notna(r.get("n_recall")) else None,
                "median_composite": _round(r.get("median_composite")),
                "median_pct_60d": _round(r.get("median_pct_60d")),
                "median_main_net_ratio": _round(r.get("median_main_net_ratio"), 4)}

    red = [_row(r) for _, r in s.head(n).iterrows()]
    black = [_row(r) for _, r in s.tail(n).iloc[::-1].iterrows()]
    return {"red": red, "black": black}


def market_pack(scan_dir: Path | str) -> dict:
    """从 L1_scored_full.csv(全市场真宽度)+ sectors.csv 聚合今日市场事实。零 LLM。

    只读 L1_scored_full(**不回退** L1_recall_top1000:composite 偏置子集会扭曲 breadth)。
    缺文件/缺列 → 对应字段 None,不抛。
    """
    scan_dir = Path(scan_dir)
    pack: dict = {"regime": None, "breadth": None, "valuation": None, "money": None, "sectors": None}
    src = scan_dir / "L1_scored_full.csv"
    if src.exists():
        df = pd.read_csv(src)
        if len(df):
            pack["regime"] = classify_regime(df).to_dict()
            pack["breadth"] = _breadth(df)
            pack["valuation"] = _valuation(df)
            pack["money"] = _money(df)
            pack["sector_healthy_top3"] = sector_healthy_top3(df)
    sec = scan_dir / "sectors.csv"
    if sec.exists():
        pack["sectors"] = _sectors(pd.read_csv(sec))
    # S1 温度计(独立数据源,presence-gated:与 regime/sectors 是否存在无关,自成一门缺→不加键)。
    # 目录名即分析日('context/scan/<date>' 是唯一真实调用约定;tmp_path 测试目录名非日期天然不命中)。
    blk = _temperature_block(scan_dir.name)
    if blk:
        pack["temperature"] = blk
    # Wave5 ③A:资金面/指数估值同款 presence-gated(root 取 scan_dir 的父目录,与帧入口同源)
    _attach_macro_cn(pack, scan_dir.name, root=scan_dir.parent)
    return pack


def _sectors_from_frame(df: pd.DataFrame, n: int = 5) -> dict | None:
    """帧 groupby(industry)→ 与 sectors.csv 兼容的行(n_recall=全市场计数、无打分列)→ 红黑榜。

    盘前帧无 composite → `median_composite` 缺省 None(描述性字段可缺,_row 已容)。
    """
    if "industry" not in df.columns or "pct_60d" not in df.columns or not len(df):
        return None
    gd = df[["industry"]].copy()
    gd["pct_60d"] = _num(df, "pct_60d")
    if "main_net_ratio" in df.columns:
        gd["main_net_ratio"] = _num(df, "main_net_ratio")
    g = gd.groupby("industry")
    sec = pd.DataFrame({"industry": list(g.size().index), "n_recall": g.size().to_numpy(),
                        "median_pct_60d": g["pct_60d"].median().to_numpy()})
    if "main_net_ratio" in gd.columns:
        sec["median_main_net_ratio"] = g["main_net_ratio"].median().to_numpy()
    return _sectors(sec, n=n)


# ───────────────────────── P7:确定性看多行业 top3(healthy 分 · 零 LLM) ─────────────────────────


def sector_healthy_table(df: pd.DataFrame) -> pd.DataFrame | None:
    """P7 行业级 healthy 组件表(spec 2026-07-12-scan-speed-perimeter §P7,零 LLM)。

    资格门(先过门再排序):n≥_TOP3_MIN_N ∧ 资金门(主力净比中位>0 或 为正占比≥50%)∧
    非落刀(60日中位>_TOP3_KNIFE)。过门行业按四组件 rank-sum 等权(score 越小越好):
    资金(净比中位+为正占比)/ 健康占比(healthy_riser_mask 单一事实源)/ 估值(中位PE低+
    PE>60 占比低)/ 动量(倒U:|中位60日−center|/half 越小越好——不追拥挤链不接刀)。
    缺列/空帧 → None(presence-gated)。
    """
    need = ("industry", "pct_60d", "main_net_ratio", "cmf_20", "pe")
    if df is None or not len(df) or not all(c in df.columns for c in need):
        return None
    from autoresearch.common.scoring import healthy_riser_mask
    d = df.copy()
    hm = healthy_riser_mask(d)
    d["_healthy"] = hm.astype(float) if hm is not None else 0.0
    d["_pe"], d["_mnr"], d["_p60"] = _num(d, "pe"), _num(d, "main_net_ratio"), _num(d, "pct_60d")
    g = d.groupby(d["industry"].astype(str))

    def _agg(fn):
        return g.apply(fn).to_numpy()

    t = pd.DataFrame({
        "industry": list(g.size().index),
        "n": g.size().to_numpy(),
        "med_main_ratio": _agg(lambda s: float(s["_mnr"].median()) if s["_mnr"].notna().any() else float("nan")),
        "main_pos": _agg(lambda s: float((s["_mnr"].dropna() > 0).mean()) if s["_mnr"].notna().any() else float("nan")),
        "healthy_share": _agg(lambda s: float(s["_healthy"].mean())),
        "med_pct_60d": _agg(lambda s: float(s["_p60"].median()) if s["_p60"].notna().any() else float("nan")),
        "med_pe": _agg(lambda s: float(s.loc[s["_pe"] > 0, "_pe"].median()) if (s["_pe"] > 0).any() else float("nan")),
        "pe_gt_60": _agg(lambda s: float((s.loc[s["_pe"] > 0, "_pe"] > 60).mean()) if (s["_pe"] > 0).any() else float("nan")),
    })
    t["qualified"] = ((t["n"] >= _TOP3_MIN_N)
                      & ((t["med_main_ratio"] > 0) | (t["main_pos"] >= 0.5))
                      & (t["med_pct_60d"] > _TOP3_KNIFE))
    t["score"] = float("nan")
    q = t[t["qualified"]]
    if len(q):
        comp = pd.DataFrame({
            "fund": (q["med_main_ratio"].rank(ascending=False) + q["main_pos"].rank(ascending=False)) / 2,
            "health": q["healthy_share"].rank(ascending=False),
            "valuation": (q["med_pe"].rank(ascending=True) + q["pe_gt_60"].rank(ascending=True)) / 2,
            "momentum": ((q["med_pct_60d"] - _TOP3_MOM_CENTER).abs() / _TOP3_MOM_HALF).rank(ascending=True),
        })
        t.loc[q.index, "score"] = comp.mean(axis=1)
    return t.sort_values("score", na_position="last").reset_index(drop=True)


def sector_healthy_top3(df: pd.DataFrame, k: int = 3) -> list[dict]:
    """过门行业按 score 取前 k(不足 k 出几个是几个,宁缺毋滥);缺列 → []。"""
    t = sector_healthy_table(df)
    if t is None:
        return []
    out = []
    for _, r in t[t["qualified"] & t["score"].notna()].head(k).iterrows():
        out.append({"industry": r["industry"], "n": int(r["n"]),
                    "med_main_ratio": _round(r["med_main_ratio"], 4),
                    "main_pos": _round(r["main_pos"], 2),
                    "healthy_share": _round(r["healthy_share"], 2),
                    "med_pct_60d": _round(r["med_pct_60d"]),
                    "med_pe": _round(r["med_pe"], 1),
                    "pe_gt_60": _round(r["pe_gt_60"], 2)})
    return out


def render_sector_top3(pack: dict) -> str:
    """L5 小节「🎯 看多行业 top3」(仅 L5 + sector_ledger;防锚定:不喂 L3/L4)。空 → ''。"""
    rows = pack.get("sector_healthy_top3") or []
    if not rows:
        return ""
    lines = ["## 🎯 看多行业 top3(确定性 healthy 分 · 零 LLM)",
             "_资格门:n≥8 ∧ 资金门(主力净比中位>0 或 为正占比≥50%)∧ 非落刀(60日中位>−20%);"
             "四组件 rank-sum 等权(资金/健康占比/估值/倒U动量,带参见 market.py 常量)。"
             "无论点;证伪点 = 分数构成反转(资金转负/健康占比塌/进入拥挤带)。"
             "只进 L5 与 sector_ledger(source=deterministic_top3),不喂 L3/L4。_", "",
             "| # | 行业 | n | 主力净比中位 | 主力+占比 | 健康占比 | 60日中位% | 中位PE | PE>60占比 |",
             "|---|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(rows, 1):
        lines.append(f"| {i} | {r['industry']} | {r['n']} | {r['med_main_ratio']} | {r['main_pos']} | "
                     f"{r['healthy_share']} | {r['med_pct_60d']} | {r['med_pe']} | {r['pe_gt_60']} |")
    return "\n".join(lines) + "\n"


def market_pack_from_frame(frame: pd.DataFrame | None, date: str | None = None) -> dict:
    """帧入口(盘前 cron / Stage 0 宏观 lite,scan staging 尚不存在时)。零 LLM。

    与 `market_pack(scan_dir)` 同字段形状、regime/breadth/valuation/money 四段同函数同口径;
    sectors 由帧 groupby 生成(n_recall=全市场计数、median_composite=None——帧无打分)。
    design: docs/specs/2026-07-03-research-skills-altitude-refactor-design.md §5.1。

    `date`(可选,'YYYY-MM-DD'):S1 温度计块锚点。帧本身不带日期语义,库函数不碰 wall-clock
    (`date.today()` 只许留在 CLI `main()`)——缺省 → 不注入 temperature(与老调用点/老测试
    逐字 parity);调用方(如 `frame.py` CLI)有 `analysis_date` 时显式传入。
    """
    pack: dict = {"regime": None, "breadth": None, "valuation": None, "money": None, "sectors": None}
    if date:                                # 温度=独立数据源,与帧空否正交(镜像 market_pack 语义)
        blk = _temperature_block(date)
        if blk:
            pack["temperature"] = blk
        _attach_macro_cn(pack, date)        # Wave5 ③A:资金面/指数估值(presence-gated 同款)
    if frame is None or not len(frame):
        return pack
    pack["regime"] = classify_regime(frame).to_dict()
    pack["breadth"] = _breadth(frame)
    pack["valuation"] = _valuation(frame)
    pack["money"] = _money(frame)
    pack["sectors"] = _sectors_from_frame(frame)
    pack["sector_healthy_top3"] = sector_healthy_top3(frame)
    return pack


# ───────────────────────── S1 温度计消费(presence-gated;展示先行,不接菜单/预算) ─────────────────────────


def _temperature_block(date: str) -> dict | None:
    """`temperature.csv`(Task 4)当日读数 + 近 ≤5 个交易日走势 → 两个 pack 函数共用的 helper。

    presence-gated:csv 缺 / 当日无行 / 当日 score 缺(fetch 成功但核心序列不全,`score()`
    降级 None,只 phase 靠滞回延续)→ 统一 None,不抛,与 pack 其余字段"缺→不炸"同款契约。
    `temperature.CSV_PATH` 现读(经 `autoresearch.scan.temperature` 模块引用,不在本模块顶部
    `from ... import CSV_PATH` 绑值——否则测试 `monkeypatch.setattr(...CSV_PATH, ...)` 不生效,
    与 temperature.py 顶部同款告诫一致)。
    """
    from autoresearch.scan import temperature as T
    path = Path(T.CSV_PATH)
    if not path.exists():
        return None
    try:
        df = pd.read_csv(path, dtype={"date": str})
    except Exception:  # noqa: BLE001 — 损坏 csv 当无数据,不阻塞 pack 组装
        return None
    if "date" not in df.columns or date not in set(df["date"]):
        return None
    df = df[df["date"] <= date].sort_values("date", kind="stable")   # 防未来行渗入(防御性)
    sc_all = _num(df, "score")
    if not len(sc_all):                     # score 列整体缺失(csv 被外部改坏)→ 与缺值同路降级
        return None
    sc = sc_all.iloc[-1]
    if pd.isna(sc):
        return None
    phase = df["phase"].iloc[-1] if "phase" in df.columns else None
    trend5 = [float(v) for v in sc_all.tail(5).dropna()]
    return {"score": float(sc), "phase": phase, "trend5": trend5}


# ───────────────────────── L3/L4 注入:描述性地形块(防锚定) ─────────────────────────


def _pct(x) -> str:
    return f"{x:.0%}" if isinstance(x, (int, float)) and not isinstance(x, bool) else "—"


def _sign(x) -> str:
    return f"{x:+.1f}%" if isinstance(x, (int, float)) and not isinstance(x, bool) else "—"


def _sector_rank(industry: str, secs: dict) -> str:
    for r in secs.get("red", []):
        if r.get("industry") == industry:
            return f"{industry} 属**强势**端(中位60日动量 {_sign(r.get('median_pct_60d'))})"
    for r in secs.get("black", []):
        if r.get("industry") == industry:
            return f"{industry} 属**弱势**端(中位60日动量 {_sign(r.get('median_pct_60d'))})"
    return f"{industry}(非红黑榜极端)"


def market_context_block(pack: dict, industry: str | None = None) -> str:
    """L3/L4 注入的**描述性市场地形**块(防锚定:只陈述结构事实,无操作/个股方向指令)。"""
    reg = pack.get("regime") or {}
    br = pack.get("breadth") or {}
    val = pack.get("valuation") or {}
    mon = pack.get("money") or {}
    secs = pack.get("sectors") or {}
    zh = _REGIME_ZH.get(reg.get("label"), reg.get("label") or "—")
    lines = ["## 市场地形(背景校准 · 非选股指令)",
             f"- **regime**:{zh}(breadth {_pct(br.get('above_ma60'))}·中位60日动量 "
             f"{_sign(br.get('med_pct_60d'))}·落刀面 {_pct(br.get('falling_knife'))})",
             f"- **估值分散**:中位 PE {val.get('med_pe')}·上十分位 PE {val.get('pe_top_decile')}"
             f"(贵端 PE>60 占比 {_pct(val.get('pe_gt_60'))})",
             f"- **资金**:主力净流入为正占比 {_pct(mon.get('main_pos'))}·CMF>0 占比 {_pct(mon.get('cmf_pos'))}"]
    if secs.get("red"):
        lines.append("- **强势板块**:" + "、".join(
            f"{r['industry']}({_sign(r.get('median_pct_60d'))})" for r in secs["red"][:3]))
    if secs.get("black"):
        lines.append("- **弱势板块**:" + "、".join(
            f"{r['industry']}({_sign(r.get('median_pct_60d'))})" for r in secs["black"][:3]))
    if industry and secs:
        lines.append(f"- **本股所在板块**:{_sector_rank(industry, secs)}")
    lines.append("- 用途:据此校准估值/资金门严格度;**个股评级只由本股 rubric 三门决定,"
                 "大盘看空不压个股、看多不松门**。")
    return "\n".join(lines) + "\n"


# ───────────────────────── L5 渲染:回退脉搏 + 温度一行 + 漏斗读数尾注 ─────────────────────────


def _attach_macro_cn(pack: dict, date: str, root: Path | str | None = None) -> None:
    """把 `_macro_cn_block` 的三样东西挂进 pack(缺 → 什么都不挂,老 pack 形状不变)。

    两个 pack 入口(帧 / staging)共用,防"一个接了另一个没接"的半接线。
    """
    blk = _macro_cn_block(date, root)
    if not blk:
        return
    if blk.get("cross_money"):
        pack["cross_money"] = blk["cross_money"]
    if blk.get("index_val"):
        pack["index_val"] = blk["index_val"]
    if blk.get("_degraded"):
        pack["macro_cn_degraded"] = blk["_degraded"]


def _macro_cn_block(date: str, root: Path | str | None = None) -> dict | None:
    """`_macro_cn.json`(Wave5 ③A 取数产物)→ pack 的 cross_money / index_val 两块。

    presence-gated,与 `_temperature_block` 同款契约:文件缺/坏 → None(pack 里就没这两个键,
    下游 presence-gated 消费,老路不破)。**降级不静默**:文件里的 `_degraded` 原样带出,
    market_view 与 prelude 汇总屏据此显示"哪块没取到"。

    此前 market_pack 只有 24 个标量、且全部来自全 A 个股快照的横截面自聚合 —— 零真宏观
    变量(无利率/汇率/北向/两融/指数估值分位)。这里补上资金面与指数估值两块。
    """
    p = Path(root or "context/scan") / date / "_macro_cn.json"
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — 坏文件当无数据,不阻 pack 组装
        return None
    nb, mg, sf = data.get("northbound"), data.get("margin"), data.get("sector_flow")
    cross_money = None
    if nb or mg or sf:
        cross_money = {
            "as_of": data.get("as_of"),
            "north_latest_yi": (nb or {}).get("latest_yi"),
            "north_cum5_yi": (nb or {}).get("cum5_yi"),
            "margin_rzye_yi": (mg or {}).get("rzye_yi"),
            "margin_d5_yi": (mg or {}).get("d5_yi"),
            "sector_flow_top": [(r["industry"], r["net_yi"]) for r in (sf or {}).get("top", [])[:5]],
            "sector_flow_bottom": [(r["industry"], r["net_yi"])
                                   for r in (sf or {}).get("bottom", [])[:5]],
        }
    return {"cross_money": cross_money, "index_val": data.get("index_val"),
            "_degraded": data.get("_degraded") or []}


def render_temperature_line(date: str) -> str:
    """L5 一行情绪温度读数(assemble regime 行旁;presence-gated:无当日读数 → ''):

        🌡 情绪温度 41(发酵)·近5日 28→41

    只描述数值+分段+近况走向,不含操作建议(与 market_context_block 同一"防锚定"纪律;
    本波不接菜单/预算联动)。
    """
    blk = _temperature_block(date)
    if not blk:
        return ""
    sc, ph, trend = blk["score"], blk["phase"], blk["trend5"]
    arrow = f"{trend[0]:.0f}→{trend[-1]:.0f}" if len(trend) >= 2 else f"{sc:.0f}"
    return f"🌡 情绪温度 {sc:.0f}({ph})·近{len(trend)}日 {arrow}"


def render_fallback_pulse(pack: dict) -> str:
    """market_view.md 缺失时的确定性市场脉搏(2–3 行);无 regime → 空串。"""
    reg = pack.get("regime") or {}
    if not reg:
        return ""
    br = pack.get("breadth") or {}
    secs = pack.get("sectors") or {}
    zh = _REGIME_ZH.get(reg.get("label"), reg.get("label") or "—")
    lines = [f"**市场脉搏(确定性回退)**:{zh} regime — breadth {_pct(br.get('above_ma60'))}·"
             f"中位60日动量 {_sign(br.get('med_pct_60d'))}·落刀面 {_pct(br.get('falling_knife'))}。"]
    if secs.get("red") and secs.get("black"):
        red = "、".join(r["industry"] for r in secs["red"][:3])
        black = "、".join(r["industry"] for r in secs["black"][:3])
        lines.append(f"强势:{red};弱势:{black}。")
    lines.append("_(未生成首席策略师研判 market_view.md → 回退确定性脉搏)_")
    return "\n".join(lines) + "\n"


def _names(scan_dir: Path, codes) -> str:
    """code → 名称(读 finalists.csv);缺 → code。"""
    f = Path(scan_dir) / "finalists.csv"
    m: dict = {}
    if f.exists():
        fdf = pd.read_csv(f, dtype={"code": str})
        for _, r in fdf.iterrows():
            m[str(r["code"]).zfill(6)] = r.get("name")
    return "、".join(f"{m.get(str(c).zfill(6)) or c}({str(c).zfill(6)})" for c in codes)


def _zero_buy_mechanism(scan_dir: Path | str, n_cards: int) -> str:
    """0 买的真机制分桶(Wave5 ②D)。

    旧判词写死「无一过 ≥OW 三门」——07-21 实测 12 卡里 6 张是早停卡(按定义压 ≤Hold 且
    从不写三门段)、仅 2 张能被 gate_status 解析。报告不能继续讲一个自己数据不支持的
    故事:这里按 `_early_stop.json` 把 0 买拆成「早停 / 满卡未达 OW」两桶并列出停因。
    """
    import json as _json
    p = Path(scan_dir) / "_early_stop.json"
    if not p.is_file():
        return "口径未知(`_early_stop.json` 未落——本日卡片早于早停记账上线,或 assemble 未跑完)"
    try:
        stops = _json.loads(p.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return "口径未知(`_early_stop.json` 解析失败)"
    n_early = len(stops)
    buckets: dict[str, int] = {}
    for meta in stops.values():
        r = (meta or {}).get("reason", "其他")
        buckets[r] = buckets.get(r, 0) + 1
    detail = "、".join(f"{k} {v}" for k, v in sorted(buckets.items(), key=lambda kv: -kv[1]))
    return (f"早停 {n_early} 张({detail or '—'})· 满卡未达 OW {max(n_cards - n_early, 0)} 张"
            f"——早停卡按定义压 ≤Hold 且不写 OW三门段,不计入门柱直方图")


def render_funnel_readout(scan_dir: Path | str) -> str:
    """L5 确定性漏斗读数尾注:今日买单(≥OW,含 verify 折回)/ 观察单(skeptic 降级)。

    无决策卡 → 空串。verify 折回口径复用 assemble(降级=降一档、否决=至少 Hold)。
    """
    from autoresearch.scan.agents.l4_card import (
        parse_ratings_from_details,  # lazy:避免 import cycle
    )
    from autoresearch.scan.assemble import _apply_verify_downgrade, _load_verify

    scan_dir = Path(scan_dir)
    ratings = parse_ratings_from_details(scan_dir / "details")
    if not ratings:
        return ""
    vmap = _load_verify(scan_dir)
    final: dict = {}
    for code, r in ratings.items():
        v = vmap.get(str(code).zfill(6))
        final[code] = (_apply_verify_downgrade(r, v["verdict"])
                       if v and v["verdict"] in ("降级", "否决") else r)
    buys = [c for c, r in final.items() if r in ("Buy", "Overweight")]
    lines = ["", "### 📉 今日漏斗读数"]
    if buys:
        lines.append(f"- **{len(buys)} 买**(≥OW):{_names(scan_dir, buys)}")
    else:
        reg = (market_pack(scan_dir).get("regime") or {}).get("label")
        zh = _REGIME_ZH.get(reg, reg or "")
        lines.append(f"- **0 买**:{len(final)} 只 finalist 深核后无一进买单 —— "
                     f"{zh} regime 下的纪律空仓观望,非漏斗故障。")
        lines.append(f"  - 机制拆分:{_zero_buy_mechanism(scan_dir, len(final))}")
    downgraded = [c for c, v in vmap.items() if v["verdict"] == "降级"]
    if downgraded:
        lines.append(f"- **观察单**:{_names(scan_dir, downgraded)}(skeptic 降级,待触发复核)")
    return "\n".join(lines) + "\n"
