#!/usr/bin/env python3
"""CBOE `VIX_History.csv` —— VIX 日线全历史,作 **VIX 1 年分位的备源**。

design: docs/specs/2026-08-28-external-evidence-expansion-design.md §5.3 / §9(D-1)。
探针(2026-08-28 22:57 CST):9,261 行可达,1990-01-02 起。

## 为什么要备源

`yf_tape` 的 VIX 分位用"同批 yfinance 历史"自算。yfinance 的 `^VIX` 历史偶发缺段/回补,
而分位是**对整段历史敏感**的统计量:少一段 2020 或 2008,分位会静默偏移,而读数看起来
和正常时一模一样(「探针报警 ≠ 被检查者坏」的反面:**被检查者坏了却不报警**)。
CBOE 官方 csv 是同一指数的第一手发布,拿它对拍/兜底。

## 契约级别

B 级:拿不到就降级(`record_degradation("cboe_vix", ...)`),分位退回 yfinance 同批历史,
再不行就 `None + 样本不足(n)` —— **绝不用一个"差不多的"数字冒充分位**。

湖键 `cboe_vix/<date>`(key=date):csv 是全历史累积表,按**取数所属的美股日**分区留底。
"""
from __future__ import annotations

import io
import os
from datetime import datetime, timezone

import pandas as pd

from autoresearch.data.sources.yf_tape import (
    MIN_PCTILE_OBS,
    PCTILE_WINDOW,
    percentile_rank,
)

VIX_HISTORY_URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"

_TIMEOUT = 30          # 9k 行 csv;夜跑必须有超时(没有它,一次挂起的连接拖死整晚预热)
_MIN_ROWS = 2000       # 全历史 ≈9,261 行;低于此 = 拉了一半就断了,不是"VIX 只有这些天"

# 归一化后的列名(CBOE 原表是 DATE/OPEN/HIGH/LOW/CLOSE)。
COLUMNS: tuple[str, ...] = ("date", "open", "high", "low", "close")


class CboeFetchError(RuntimeError):
    """CBOE csv 取数/解析失败 —— **抛**,不返回空帧假装成功(空帧会被钉进湖里)。"""


def _offline() -> bool:
    return str(os.environ.get("AUTORESEARCH_OFFLINE", "")).strip() in {"1", "true", "TRUE", "yes"}


def parse_vix_csv(text: str) -> pd.DataFrame:
    """CBOE csv 文本 → 归一化帧(`COLUMNS`,按日期升序)。形态不对就抛。"""
    df = pd.read_csv(io.StringIO(text))
    cols = {str(c).strip().lower(): c for c in df.columns}
    date_col = cols.get("date")
    close_col = cols.get("close") or cols.get("vix close")
    if date_col is None or close_col is None:
        raise CboeFetchError(
            f"CBOE csv 形态异常(缺 DATE/CLOSE,拿到 {list(df.columns)[:8]})—— "
            f"多半是官方改版,不是「今天没有数据」")
    out = pd.DataFrame({
        "date": pd.to_datetime(df[date_col], errors="coerce").dt.date.astype("string"),
        "open": pd.to_numeric(df.get(cols.get("open", "")), errors="coerce"),
        "high": pd.to_numeric(df.get(cols.get("high", "")), errors="coerce"),
        "low": pd.to_numeric(df.get(cols.get("low", "")), errors="coerce"),
        "close": pd.to_numeric(df[close_col], errors="coerce"),
    })
    out = out.dropna(subset=["date", "close"]).sort_values("date").reset_index(drop=True)
    out["date"] = out["date"].astype(str)
    return out


def fetch_vix_history(*, timeout: int = _TIMEOUT, session=None, min_rows: int = _MIN_ROWS) -> pd.DataFrame:
    """拉 `VIX_History.csv` → 归一化帧。

    **形态不对/行数腰斩就抛**(`CboeFetchError`),不返回空帧:B 级"断采只损失当日"要求
    断采是**显式**的 —— 静默的空帧会被写进湖并把这天永久钉成空(「cache 空 pickle 永不
    重拉」家训的 parquet 同族)。降级由调用方(`load_vix_history`)记账。
    """
    if _offline():
        raise CboeFetchError("AUTORESEARCH_OFFLINE=1:离线模式不取网络")
    try:
        import requests
    except Exception as exc:  # noqa: BLE001
        raise CboeFetchError(f"requests 不可用:{exc!r}") from exc
    getter = session.get if session is not None else requests.get
    try:
        r = getter(VIX_HISTORY_URL, timeout=timeout)
        r.raise_for_status()
        text = r.text
    except CboeFetchError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise CboeFetchError(f"CBOE csv 请求失败:{type(exc).__name__}: {exc}") from exc
    df = parse_vix_csv(text)
    if min_rows and len(df) < min_rows:
        raise CboeFetchError(
            f"CBOE csv 行数腰斩({len(df)} < {min_rows})—— 取数半途而废,不是历史变短了")
    return df


def load_vix_history(us_date: str | None = None, *, today: str | None = None,
                     fetch=None, record: bool = True) -> pd.DataFrame:
    """B 级入口:走 lake(`cboe_vix/<date>`),失败 → 记账 + 空帧(`attrs['status']`)。

    **不抛**:VIX 分位是锦上添花,拿不到就退回 yfinance 同批历史或干脆不给分位。
    `attrs["status"]` ∈ {"ok","empty","failed"} —— 「源成功但真空」与「请求/解析失败」
    必须可区分(§9 湖纪律)。
    """
    from autoresearch.data import cache
    from autoresearch.data.contracts import record_degradation

    key = (us_date or datetime.now(timezone.utc).date().isoformat()).replace("-", "")
    try:
        df = cache.get_or_fetch(
            "cboe_vix", {"trade_date": key}, today=today,
            fetch=(fetch if fetch is not None else (lambda _ep, _p: fetch_vix_history())),
        )
    except BaseException as exc:  # noqa: BLE001 — B 级:降级不阻断
        if record:
            record_degradation("cboe_vix", f"{type(exc).__name__}: {exc}"[:200], key=key)
        out = pd.DataFrame(columns=list(COLUMNS))
        out.attrs["status"] = "failed"
        out.attrs["reason"] = f"{type(exc).__name__}: {exc}"[:200]
        return out
    df = df if df is not None else pd.DataFrame(columns=list(COLUMNS))
    df.attrs["status"] = "ok" if len(df) else "empty"
    if not len(df) and record:
        record_degradation("cboe_vix", "源应答成功但零行(empty,非 failed)", key=key)
    return df


def vix_percentile(history: pd.DataFrame, value: float | None, *,
                   window: int = PCTILE_WINDOW, min_obs: int = MIN_PCTILE_OBS) -> dict:
    """用 CBOE 历史算 VIX 分位(默认近 1 年)。口径与 `yf_tape.percentile_rank` 同一实现
    —— 两个源必须用同一把尺,否则"备源"会给出一个和主源不可比的数。"""
    if history is None or not len(history) or "close" not in history.columns:
        return {"pctile": None, "obs": 0, "note": "样本不足(0)", "source": "cboe"}
    closes = pd.to_numeric(history["close"], errors="coerce").dropna().tail(window)
    out = percentile_rank(closes, value, min_obs=min_obs)
    out["source"] = "cboe"
    return out
