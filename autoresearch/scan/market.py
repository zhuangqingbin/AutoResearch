#!/usr/bin/env python3
"""scan-market · 市场级确定性聚合 —— 首席策略师数据包 + L3/L4 注入地形块 + L5 尾注/回退。

design: docs/specs/2026-07-01-scan-market-strategist-view-design.md

零 LLM。market_pack 从 L1_scored_full + sectors.csv 聚合"今日市场"事实(regime/宽度/估值分散/
资金/板块红黑榜);market_context_block 派生**描述性**地形块喂 L3/L4(防锚定:只描述不指令);
render_funnel_readout 给 L5 确定性漏斗读数尾注;render_fallback_pulse 给 market_view 缺失时回退。
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from autoresearch.common.regime import classify_regime

_REGIME_ZH = {"trend": "趋势", "range": "震荡", "risk_off": "避险"}


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
    sec = scan_dir / "sectors.csv"
    if sec.exists():
        pack["sectors"] = _sectors(pd.read_csv(sec))
    return pack


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


# ───────────────────────── L5 渲染:回退脉搏 + 漏斗读数尾注 ─────────────────────────


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


def render_funnel_readout(scan_dir: Path | str) -> str:
    """L5 确定性漏斗读数尾注:今日买单(≥OW,含 verify 折回)/ 观察单(skeptic 降级)。

    无决策卡 → 空串。verify 折回口径复用 assemble(降级=降一档、否决=至少 Hold)。
    """
    from autoresearch.scan.agents.l4_card import parse_ratings_from_details   # lazy:避免 import cycle
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
        lines.append(f"- **0 买**:{len(final)} 只 finalist 深核后无一过 ≥OW 三门 —— "
                     f"{zh}regime 下的纪律空仓观望,非漏斗故障。")
    downgraded = [c for c, v in vmap.items() if v["verdict"] == "降级"]
    if downgraded:
        lines.append(f"- **观察单**:{_names(scan_dir, downgraded)}(skeptic 降级,待触发复核)")
    return "\n".join(lines) + "\n"
