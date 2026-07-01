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
