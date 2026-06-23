#!/usr/bin/env python3
"""免 token 直连数据源层 —— tushare 的补充/兜底(不替换)。

参考 simonlin1212/a-stock-data 的直连栈 + 本环境实测可达性(2026-06-23)。所有直连走
`_keyless_get`(串行限流 + UA + Session 复用,防封);只在 L4 ~30 只深挖用,不进全市场热路径。

源①(本期):同花顺一致预期 EPS —— 补 tushare 完全没有的卖方前瞻 EPS,供 L4 算真 fwd-PE。
解析是纯函数(可离线测);lake 缓存在整合层(analyze)按需包一层 get_or_fetch。

设计:docs/specs/2026-06-23-keyless-data-sources-design.md。
"""
from __future__ import annotations

import json
import re

import pandas as pd

_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
_MIN_INTERVAL = 1.0          # 串行限流下限(秒);批量再调大
_COLS = ["year", "eps", "np_yi", "kind"]   # kind: SJ=实际 / YC=预测
_LAST = [0.0]
_SESSION: list = [None]


def _keyless_get(url: str, *, params=None, headers=None, timeout: int = 8, encoding=None) -> str:
    """串行限流(_MIN_INTERVAL + 抖动)+ UA + Session 复用的 GET → text。requests 延迟导入。"""
    import random
    import time

    import requests

    wait = _MIN_INTERVAL - (time.time() - _LAST[0])
    if wait > 0:
        time.sleep(wait + random.uniform(0.0, 0.4))
    _LAST[0] = time.time()
    if _SESSION[0] is None:
        _SESSION[0] = requests.Session()
    h = {"User-Agent": _UA, **(headers or {})}
    r = _SESSION[0].get(url, params=params, headers=h, timeout=timeout)
    r.raise_for_status()
    if encoding:
        r.encoding = encoding
    return r.text


def _empty() -> pd.DataFrame:
    return pd.DataFrame(columns=_COLS)


# ───────────────────────── 源①:同花顺一致预期 EPS ─────────────────────────


def parse_consensus_eps(html: str) -> pd.DataFrame:
    """worth.html 内嵌 `yjycData` JSON blob → DataFrame[year, eps, np_yi, kind]。

    blob 形如 `[["2026","68.82","861.83","YC"], ...]`,每行 [年, EPS, 净利润亿, 类型]
    (SJ=实际 / YC=预测)。抽不到 / 解析失败 → 空帧(列在)。纯函数,无网络。
    """
    if not html:
        return _empty()
    m = re.search(r'id="yjycData"[^>]*>(\[.*?\])\s*</div>', html, re.S)
    if not m:
        return _empty()
    try:
        rows = json.loads(m.group(1))
    except (ValueError, TypeError):
        return _empty()
    if not rows:
        return _empty()
    df = pd.DataFrame([r[:4] for r in rows if len(r) >= 4], columns=_COLS)
    if df.empty:
        return _empty()
    df["year"] = df["year"].astype(str)
    df["eps"] = pd.to_numeric(df["eps"], errors="coerce")
    df["np_yi"] = pd.to_numeric(df["np_yi"], errors="coerce")
    df["kind"] = df["kind"].astype(str)
    return df[_COLS]


def fwd_eps(df: pd.DataFrame, year) -> float | None:
    """取某年的预测 EPS(kind=='YC');无该年预测 / 空帧 → None。"""
    if df is None or df.empty:
        return None
    sub = df[(df["year"].astype(str) == str(year)) & (df["kind"] == "YC")]
    if not len(sub):
        return None
    v = pd.to_numeric(sub["eps"], errors="coerce").iloc[0]
    return None if v != v else float(v)


def fetch_consensus_eps(code: str, *, get=_keyless_get) -> pd.DataFrame:
    """GET 同花顺 worth.html(gbk)→ parse_consensus_eps;出错 → 空帧(降级隔离)。

    lake 缓存在整合层(analyze)按需包 get_or_fetch;本函数是原始取数单元(可注入 get= 离线测)。
    """
    url = f"https://basic.10jqka.com.cn/new/{code}/worth.html"
    try:
        html = get(url, headers={"Referer": "https://basic.10jqka.com.cn/"}, encoding="gbk")
        return parse_consensus_eps(html)
    except Exception:
        return _empty()


def consensus_eps_block(ticker: str, price: float | None = None, *, fetch=fetch_consensus_eps) -> str:
    """A股卖方一致预期 EPS(同花顺 keyless)→ 预测年 EPS/净利 (+ fwd-PE 若给价)。

    补 yfinance 对 A 股 `forwardPE` 多缺失的缺口。降级 / 无前瞻 → 文字提示。可注入 fetch= 离线测。
    """
    code = str(ticker).split(".")[0]
    try:
        df = fetch(code)
    except Exception:
        df = _empty()
    if df is None or df.empty:
        return "_同花顺一致预期 EPS 不可用(降级);fwd-PE 由分析师用上方 snapshot 价自算_"
    yc = df[df["kind"] == "YC"].sort_values("year")
    if yc.empty:
        return "_同花顺仅有实际值、无前瞻一致预期;fwd-PE 由分析师自算_"
    has_pe = price is not None and float(price) == float(price) and float(price) > 0
    lines = ["| 年度 | 预测EPS(元) | 预测净利(亿) |" + (" fwd-PE |" if has_pe else ""),
             "|---|---:|---:|" + ("---:|" if has_pe else "")]
    for r in yc.itertuples(index=False):
        eps = float(r.eps) if r.eps == r.eps else None
        npy = float(r.np_yi) if r.np_yi == r.np_yi else None
        row = f"| {r.year} | {eps if eps is not None else '—'} | {npy if npy is not None else '—'} |"
        if has_pe:
            row += f" {price / eps:.1f}x |" if eps else " — |"
        lines.append(row)
    nxt_year, nxt_eps = yc["year"].iloc[0], float(yc["eps"].iloc[0])
    headline = (f"**下一财年({nxt_year})fwd-PE ≈ {price / nxt_eps:.1f}x**"
                f"(卖方一致预期 EPS {nxt_eps})\n\n") if (has_pe and nxt_eps) else ""
    note = ("\n\n_来源:同花顺 worth.html 一致预期(keyless·免 token);YC=预测/SJ=实际。"
            "补 yfinance 对 A 股 forwardPE 的缺口。_")
    return headline + "\n".join(lines) + note
