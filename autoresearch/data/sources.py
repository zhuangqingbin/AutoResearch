"""取数门面 —— 全项目唯一取数入口,按 policy.source 路由到后端。

design: docs/specs/2026-06-22-autoresearch-arch-redesign-design.md §B。

  fetch(endpoint, params) -> pd.DataFrame
    tushare  → autoresearch.data.tushare_source._pro() + getattr(pro, endpoint)(**params),
               套 _ts_call 限频重试(沿用现有防御层)。
    akshare  → import akshare + getattr(ak, endpoint)(**params)。
    fred     → 现有 FRED dataflow(get_macro_data),params 给 indicator/curr_date。
    yfinance → 现有 y_finance dataflow。

本层**薄**:只取原始帧,不做富化/打分(那是 features/stages 的事)。网络路径 best-effort,
不进单测——cache 的测试 monkeypatch 掉 fetch;此处只在真跑时点对点拉数。所有第三方/重 import
均**延迟到函数内**,使 autoresearch.data 包在无 akshare/tushare 的环境下也能 import。
"""
from __future__ import annotations

import pandas as pd

from autoresearch.data.endpoints import policy


def fetch(endpoint: str, params: dict) -> pd.DataFrame:
    """取一个端点的原始帧,按 policy.source 分派。未知 source → ValueError。"""
    src = policy(endpoint)["source"]
    if src == "tushare":
        return _fetch_tushare(endpoint, params)
    if src == "akshare":
        return _fetch_akshare(endpoint, params)
    if src == "fred":
        return _fetch_fred(endpoint, params)
    if src == "yfinance":
        return _fetch_yfinance(endpoint, params)
    raise ValueError(f"unknown source {src!r} for endpoint {endpoint!r}")


def _ensure_scripts_on_path() -> None:
    """临时桥:tushare_source 顶层 `from screen_market import ...`(6 个纯 helper)而
    screen_market 仍在 scripts/(Phase E5/E6 才进包)。把 scripts/ 挂上 sys.path,使
    autoresearch.data.tushare_source 可 import——**不改其 logic**。E5/E6 搬完即删此桥。
    """
    import sys
    from pathlib import Path

    scripts = Path(__file__).resolve().parent.parent.parent / "scripts"
    if scripts.is_dir() and str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))


def _fetch_tushare(endpoint: str, params: dict) -> pd.DataFrame:
    _ensure_scripts_on_path()
    from autoresearch.data.tushare_source import _pro, _ts_call

    pro = _pro()
    fn = getattr(pro, endpoint)
    df = _ts_call(lambda: fn(**params))
    return df if df is not None else pd.DataFrame()


def _fetch_akshare(endpoint: str, params: dict) -> pd.DataFrame:
    import akshare as ak

    fn = getattr(ak, endpoint)
    df = fn(**params)
    return df if df is not None else pd.DataFrame()


def _fetch_fred(endpoint: str, params: dict) -> pd.DataFrame:
    """FRED series → 单列('value')时序帧。params: indicator(别名/原始 ID)+ curr_date。

    复用项目 FRED dataflow 的请求/鉴权;这里只取原始观测,不做 markdown 渲染。
    """
    from autoresearch.dataflows.fred import _request, _resolve_series_id

    series_id = _resolve_series_id(params["indicator"])
    obs_params = {"series_id": series_id}
    if params.get("curr_date"):
        obs_params["observation_end"] = params["curr_date"]
    if params.get("observation_start"):
        obs_params["observation_start"] = params["observation_start"]
    payload = _request("series/observations", obs_params)
    rows = payload.get("observations") or []
    return pd.DataFrame(rows)


def _fetch_yfinance(endpoint: str, params: dict) -> pd.DataFrame:
    """yfinance 历史价(跨资产)。params: symbol + 可选 period/start/end。"""
    import yfinance as yf

    symbol = params["symbol"]
    kwargs = {k: v for k, v in params.items() if k != "symbol"}
    df = yf.Ticker(symbol).history(**kwargs)
    return df.reset_index() if df is not None else pd.DataFrame()
