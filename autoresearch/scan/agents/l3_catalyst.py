#!/usr/bin/env python3
"""scan-market · 确定性催化事件面 —— 增减持/回购/机构调研 近10日计数(零 LLM,advisory)。

spec: docs/specs/2026-07-05-evidence-catalyst-watchlist-card-wave-design.md §WS-B1/B2。
07-03 病灶:30/30 卡"无明确催化"——不是判断弱,是探测盲(只有公告情感+日历)。本模块把
三个有权限端点(07-05 实测)聚成每票事件计数,进 L3 表 `cat` 列与 L4 简报(存在性≠方向,
禁则见消费端图例);alpha 取证在 catalyst_ledger,IC 过硬前不入 composite、不设门。
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

# endpoint → (日期参数名, 计数规则见 catalyst_counts)
_ENDPOINTS = {"stk_holdertrade": "ann_date", "repurchase": "ann_date", "stk_surv": "trade_date"}
_COLS = ["code", "rep_impl", "rep_plan", "holder_in", "holder_de", "surv_n"]


def catalyst_counts(frames: dict[str, list[pd.DataFrame]], want: set[str]) -> pd.DataFrame:
    """{endpoint: [日帧,…]} → 每票事件计数帧(纯函数,可单测)。want=6位代码集合。"""
    from autoresearch.data.tushare_source import _code6
    acc = {c: dict.fromkeys(_COLS[1:], 0) for c in sorted(want)}

    def _rows(ep: str):
        for df in frames.get(ep, []):
            if df is None or not len(df) or "ts_code" not in df.columns:
                continue
            sub = df.assign(_c=_code6(df["ts_code"]))
            yield from sub[sub["_c"].isin(want)].to_dict("records")

    for r in _rows("stk_holdertrade"):
        key = "holder_in" if str(r.get("in_de", "")).upper() == "IN" else "holder_de"
        acc[r["_c"]][key] += 1
    for r in _rows("repurchase"):
        proc = str(r.get("proc", ""))
        key = "rep_impl" if ("实施" in proc and not any(w in proc for w in ("停止", "终止"))) else "rep_plan"
        acc[r["_c"]][key] += 1
    for r in _rows("stk_surv"):
        acc[r["_c"]]["surv_n"] += 1
    return pd.DataFrame([{"code": c, **v} for c, v in acc.items()], columns=_COLS)


def cat_label(row: dict) -> str:
    """事件计数 → 徽标(全零 → "")。顺序:回购(实施/预案)·增持·调研·减持。"""
    def _n(k: str) -> int:
        v = row.get(k, 0)
        try:
            return int(v) if v == v else 0        # NaN 安全
        except (TypeError, ValueError):
            return 0
    parts = []
    if _n("rep_impl"):
        parts.append(f"回购{_n('rep_impl')}(实施)")
    if _n("rep_plan"):
        parts.append(f"回购{_n('rep_plan')}(预案)")
    if _n("holder_in"):
        parts.append(f"增持{_n('holder_in')}")
    if _n("surv_n"):
        parts.append(f"调研{_n('surv_n')}")
    if _n("holder_de"):
        parts.append(f"减持{_n('holder_de')}")
    return "·".join(parts)


def harvest_catalyst(date: str, codes, root: Path | None = None, lookback_days: int = 10,
                     days: list[str] | None = None, fetch_fn=None) -> pd.DataFrame:
    """近 lookback_days 交易日三端点按日拉(湖优先)→ 计数 → 落 `<root>/<date>/L3_catalyst.csv`。

    best-effort:单日/单端点失败跳过(降级);days/fetch_fn 注入供离线测(fetch_fn 时绕湖直调)。
    """
    from autoresearch.data.cache import get_or_fetch
    from autoresearch.scan.agents.l3_news import _trade_days_for
    root = root or Path("context/scan")
    want = {str(c).zfill(6) for c in codes}
    days = days if days is not None else _trade_days_for(date, lookback_days)
    frames: dict[str, list[pd.DataFrame]] = {ep: [] for ep in _ENDPOINTS}
    for ep, dkey in _ENDPOINTS.items():
        for dd in days:
            try:
                df = (fetch_fn(ep, {dkey: dd}) if fetch_fn is not None
                      else get_or_fetch(ep, {dkey: dd}, today=date))
            except Exception:  # noqa: BLE001 — 无权限/限频 → 跳过该日(降级)
                continue
            frames[ep].append(df)
    out = catalyst_counts(frames, want)
    d = root / date
    d.mkdir(parents=True, exist_ok=True)
    out.to_csv(d / "L3_catalyst.csv", index=False)
    return out
