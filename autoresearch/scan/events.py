"""全市场事件计数(Wave4 Task 2;确定性,零 LLM)。

plan: docs/plans/2026-07-24-wave4-event-recall-plan.md。

与 `agents/l3_catalyst.py` 的关系:**分类逻辑 100% 复用它的 `catalyst_counts` 纯函数**
(增减持 in_de / 回购 proc / 调研计数,已确定性、已单测),本模块只改两件事——
①覆盖面:L2-200 → **全市场**(不传 want 过滤);②时机:L3 阶段 → **L1 之前**
(要能把没进 L2 的票也捞出来,否则事件召回路无意义)。

三端点均已入湖(`ann_date`/`trade_date` 键,eod 不可变)→ `get_or_fetch` 湖命中零网络。
降级:单端点单日失败跳过;**三腿全失败 → 空帧 + stderr 告警**(降级必须留痕)。
"""
from __future__ import annotations

import sys

import pandas as pd

from autoresearch.scan.agents.l3_catalyst import _ENDPOINTS, catalyst_counts

EVENT_COLS: tuple[str, ...] = ("ev_rep_impl", "ev_rep_plan", "ev_holder_in",
                               "ev_holder_de", "ev_surv_n", "ev_pos")
# 正催化口径与 learning/catalyst_ledger._POS 对齐(减持不算正)——同一口径两处消费,勿分叉。
_POS_SRC = ("rep_impl", "rep_plan", "holder_in", "surv_n")
_RENAME = {"rep_impl": "ev_rep_impl", "rep_plan": "ev_rep_plan",
           "holder_in": "ev_holder_in", "holder_de": "ev_holder_de", "surv_n": "ev_surv_n"}


def _empty() -> pd.DataFrame:
    return pd.DataFrame({"code": pd.Series(dtype=str),
                         **{c: pd.Series(dtype=float) for c in EVENT_COLS}})


def _fetch_day(endpoint: str, day: str, date: str) -> pd.DataFrame:
    """单端点单日取数(湖优先);module-attr 派发便于测试注入。"""
    from autoresearch.data.cache import get_or_fetch
    return get_or_fetch(endpoint, {_ENDPOINTS[endpoint]: day}, today=date)


def market_event_counts(date: str, *, lookback_days: int = 10, fetch_fn=None) -> pd.DataFrame:
    """近 lookback_days 交易日三端点全市场事件计数;列 = code + EVENT_COLS。"""
    from autoresearch.scan.agents.l3_news import _trade_days_for
    fetch = fetch_fn or _fetch_day
    frames: dict[str, list[pd.DataFrame]] = {ep: [] for ep in _ENDPOINTS}
    ok = 0
    for day in _trade_days_for(date, lookback_days):
        for ep in _ENDPOINTS:
            try:
                df = fetch(ep, day, date)
            except Exception:  # noqa: BLE001 — 单端点单日失败跳过,余下照拉
                continue
            if df is not None and len(df):
                frames[ep].append(df)
                ok += 1
    if not ok:
        print(f"[events] ⚠️ 事件取数三腿全失败({date},近 {lookback_days} 交易日)"
              "→ 事件列全 0,事件召回路本日等同停用。", file=sys.stderr)
        return _empty()

    want = {str(c).split(".")[0].zfill(6)
            for ep in frames for df in frames[ep] for c in df.get("ts_code", [])}
    if not want:
        return _empty()
    cnt = catalyst_counts(frames, want)
    if cnt is None or not len(cnt):
        return _empty()
    out = cnt.rename(columns=_RENAME)
    for c in EVENT_COLS:
        if c not in out.columns:
            out[c] = 0.0
    out["ev_pos"] = sum(out[_RENAME[k]].fillna(0.0) for k in _POS_SRC)
    return out[["code", *EVENT_COLS]]


def attach_event_cols(scored: pd.DataFrame, ev: pd.DataFrame) -> pd.DataFrame:
    """事件列左连接进 scored(不就地改入参);缺 → 0.0。

    挂在 `composite_score` **之后**——不进 `build_market_frame`,避开 A 级规模契约。
    """
    out = scored.copy()
    out["code"] = out["code"].astype(str).str.zfill(6)
    if ev is None or not len(ev):
        for c in EVENT_COLS:
            out[c] = 0.0
        return out
    e = ev.copy()
    e["code"] = e["code"].astype(str).str.zfill(6)
    out = out.merge(e[["code", *[c for c in EVENT_COLS if c in e.columns]]],
                    on="code", how="left")
    for c in EVENT_COLS:
        out[c] = out[c].fillna(0.0) if c in out.columns else 0.0
    return out
