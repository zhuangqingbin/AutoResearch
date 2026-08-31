#!/usr/bin/env python3
"""SEC EDGAR 申报流(`data.sec.gov/submissions`)—— 美股 T1 原始证据源。

design: `docs/specs/2026-08-28-external-evidence-expansion-design.md` §9(「SEC EDGAR」行:
需 UA 头;CIK 由 `ticker→CIK` 官方表)、§7.2 第⑤面(内部人与大股东:Form 4 走确定性)。

## 官方要求(不是可选项)

SEC 明确要求所有自动访问声明 **User-Agent**(格式:`Sample Company Name AdminContact@example.com`)
并遵守速率(≤10 请求/秒)。不带 UA 会被 403 / 封 IP,而且**这是合规要求不是技术细节**。

本模块**不内置任何联系方式**:UA 从环境变量 `SEC_EDGAR_USER_AGENT` 读,缺就抛
`EdgarNotConfiguredError`(由 `*_safe` 包装成 B 级降级)。硬编码某个人的邮箱去请求第三方
服务是越权 —— 联系方式必须由使用者自己声明。

## 层级

EDGAR = **T1 原始 / 官方**(§3.1),可支持 material claim,但仍需时点与字段匹配:
`filingDate` 是**申报日**,不是事件发生日;`acceptanceDateTime` 才是「什么时候公开可见」。
本模块两个都带出来,消费侧要用哪个自己选 —— 拿申报日当「已知时刻」会在盘后申报上错一天。

lake 键 `edgar/<cik>@<as_of>`,B 级契约。
"""
from __future__ import annotations

import os
import time as _time
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import pandas as pd

ENDPOINT = "edgar"

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik10}.json"
COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
FILING_INDEX_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession_nodash}/{document}"

UA_ENV = "SEC_EDGAR_USER_AGENT"
REQUEST_TIMEOUT = 30

# SEC 公布的上限是 10 请求/秒。留一点余量:0.11s ≈ 9/s。
MIN_REQUEST_INTERVAL_S = 0.11
_last_request_monotonic = 0.0

DEFAULT_LOOKBACK_DAYS = 90          # §9:返回近 90 日 filings

_FILING_COLS = (
    "form", "filing_date", "report_date", "accession", "primary_document",
    "acceptance_datetime", "items", "url",
)


class EdgarNotConfiguredError(RuntimeError):
    """缺 `SEC_EDGAR_USER_AGENT` —— SEC 要求声明 UA(含联系方式),本仓不代填。"""


class EdgarError(RuntimeError):
    """EDGAR 请求 / 解析失败,或 ticker→CIK 未命中。"""


@dataclass(frozen=True)
class FetchOutcome:
    """「源成功但真空」与「请求/解析失败」必须可区分(§9),故不用裸 DataFrame 表达。"""

    rows: pd.DataFrame
    status: str            # "OK" | "EMPTY" | "FAILED" | "NO_CIK"
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.status in ("OK", "EMPTY")


def user_agent() -> str:
    """SEC 要求的 UA(从 `SEC_EDGAR_USER_AGENT` 读)。缺 → 抛,**不静默用默认值**。"""
    ua = str(os.environ.get(UA_ENV, "")).strip()
    if not ua:
        raise EdgarNotConfiguredError(
            f"{UA_ENV} 未设置。SEC 要求自动访问声明 User-Agent(含联系方式),格式如\n"
            f"  {UA_ENV}='YourName your.email@example.com'\n"
            f"本仓不代填联系方式 —— 拿别人的邮箱去请求第三方服务是越权。")
    return ua


def headers() -> dict[str, str]:
    """EDGAR 请求头:UA 必带;`Accept-Encoding` 按 SEC 建议压缩。"""
    return {
        "User-Agent": user_agent(),
        "Accept": "application/json",
        "Accept-Encoding": "gzip, deflate",
        "Host": "data.sec.gov",
    }


def _throttle() -> None:
    """速率闸(≤10 req/s)。测试 monkeypatch `_SLEEP` / `_MONOTONIC` 关掉真等待。"""
    global _last_request_monotonic
    now = _MONOTONIC()
    wait = MIN_REQUEST_INTERVAL_S - (now - _last_request_monotonic)
    if wait > 0:
        _SLEEP(wait)
    _last_request_monotonic = _MONOTONIC()


_SLEEP = _time.sleep
_MONOTONIC = _time.monotonic


def _default_request(url: str, hdrs: dict) -> dict:
    import requests

    _throttle()
    r = requests.get(url, headers=hdrs, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.json()


def to_cik10(cik) -> str:
    """任意 CIK 表示 → 10 位零填充串(`submissions` 端点要的形态)。"""
    s = str(cik).strip().upper()
    if s.startswith("CIK"):
        s = s[3:]
    if not s.isdigit():
        raise EdgarError(f"不是合法 CIK:{cik!r}")
    return s.zfill(10)


def fetch_company_tickers(*, request=None) -> dict[str, str]:
    """官方 `company_tickers.json` → `{TICKER: CIK10}`。

    **只走官方映射表**:自造 ticker→CIK 表会在改名 / 并购 / 一码多票时静默错到别家公司,
    而 EDGAR 的申报流看起来照样「有数据」—— 这类错误没有任何自然告警。
    """
    req = request or _default_request
    hdrs = dict(headers(), Host="www.sec.gov")
    payload = req(COMPANY_TICKERS_URL, hdrs)
    if not isinstance(payload, dict):
        raise EdgarError(f"company_tickers.json 返回体不是 dict,而是 {type(payload).__name__}")
    rows = payload.values() if not {"data", "fields"} <= set(payload) else (
        dict(zip(payload["fields"], r)) for r in payload["data"])
    out: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        tic = str(row.get("ticker") or "").strip().upper()
        cik = row.get("cik_str", row.get("cik"))
        if tic and cik not in (None, ""):
            out[tic] = to_cik10(cik)
    if not out:
        raise EdgarError("company_tickers.json 解析出 0 条映射 —— 多半是 schema 变了")
    return out


def resolve_cik(ticker_or_cik, *, mapping: dict[str, str] | None = None, request=None) -> str:
    """`NVDA` / `0001045810` / `CIK1045810` → CIK10。未命中 → `EdgarError`。"""
    s = str(ticker_or_cik).strip()
    if not s:
        raise EdgarError("空 ticker/CIK")
    explicit = s.upper().startswith("CIK")
    bare = s[3:] if explicit else s
    if bare.isdigit():
        # 2026-08-29:**纯数字 ≠ CIK**。A 股代码(600519 / 300857 / 映射表的 codes 键)
        # 全是 6 位纯数字,零填充后会变成 `CIK0000600519` —— 那是**另一家**美国注册人的
        # 申报流,或 404。这正是下面那句「猜错会静默返回另一家公司的申报流」要防的事,
        # 只是漏防了同族的一种。判据:**显式 `CIK` 前缀**才允许走数字路;否则 ≤6 位数字
        # 一律当 A 股代码拒掉(真 CIK 是 7–10 位,写全或加前缀都能过)。
        if explicit or len(bare.lstrip("0")) > 6:
            return to_cik10(bare)
        raise EdgarError(
            f"{s!r} 是 {len(bare)} 位纯数字 —— 看起来是 A 股代码而不是 CIK。"
            f"**不猜**:零填充成 CIK 会静默返回另一家美国公司的申报流。"
            f"确实要按 CIK 查就写全位数或加 `CIK` 前缀(如 `CIK{bare}`)。")
    table = mapping if mapping is not None else fetch_company_tickers(request=request)
    cik = table.get(s.upper())
    if not cik:
        raise EdgarError(
            f"ticker {s!r} 不在官方 company_tickers.json 里 —— 可能是非美股 / 已退市 / 拼写错。"
            f"**不猜**:猜错会静默返回另一家公司的申报流。")
    return to_cik10(cik)


def _filings_frame(payload: dict, *, cik10: str, since: date) -> pd.DataFrame:
    filings = (payload.get("filings") or {}).get("recent") or {}
    if not isinstance(filings, dict) or not filings:
        return pd.DataFrame(columns=list(_FILING_COLS))
    n = len(filings.get("accessionNumber") or [])

    def col(name: str) -> list:
        v = filings.get(name) or []
        return list(v) + [""] * (n - len(v))

    cik_int = str(int(cik10))
    rows = []
    for i in range(n):
        fdate = str(col("filingDate")[i] or "")
        if fdate and fdate < since.isoformat():
            continue
        acc = str(col("accessionNumber")[i] or "")
        doc = str(col("primaryDocument")[i] or "")
        acc_nodash = acc.replace("-", "")
        url = (FILING_INDEX_URL.format(cik=cik_int, accession_nodash=acc_nodash, document=doc)
               if acc_nodash and doc else "")
        rows.append({
            "form": str(col("form")[i] or ""),
            "filing_date": fdate,
            "report_date": str(col("reportDate")[i] or ""),
            "accession": acc,
            "primary_document": doc,
            "acceptance_datetime": str(col("acceptanceDateTime")[i] or ""),
            "items": str(col("items")[i] or ""),
            "url": url,
        })
    return pd.DataFrame(rows, columns=list(_FILING_COLS))


def fetch_submissions(
    ticker_or_cik,
    *,
    days: int = DEFAULT_LOOKBACK_DAYS,
    as_of: date | None = None,
    mapping: dict[str, str] | None = None,
    request=None,
) -> pd.DataFrame:
    """近 `days` 日的 filings(列 `form/filing_date/report_date/accession/primary_document/
    acceptance_datetime/items/url`)。

    `as_of`:窗口右端。**做 PIT 工作的调用方必须显式传**;None = 墙上时钟今天(只对「现在跑一次」
    的实时用途成立)。

    形态不对就抛(`EdgarError`),不返回空帧假装成功;**真的近 90 日没有申报** = 合法空,走
    正常路径返回 0 行 —— 两者形态不同,B 级记账才分得清(§9)。
    """
    cik10 = resolve_cik(ticker_or_cik, mapping=mapping, request=request)
    req = request or _default_request
    payload = req(SUBMISSIONS_URL.format(cik10=cik10), headers())
    if not isinstance(payload, dict):
        raise EdgarError(f"submissions 返回体不是 dict,而是 {type(payload).__name__}")
    if "filings" not in payload:
        raise EdgarError(f"submissions[CIK{cik10}] 缺 `filings` 键(keys={sorted(payload)[:8]})")
    end = as_of or date.today()
    return _filings_frame(payload, cik10=cik10, since=end - timedelta(days=int(days)))


def fetch_submissions_safe(ticker_or_cik, **kwargs) -> FetchOutcome:
    """B 级封装:UA 缺席 / CIK 未命中 / 请求炸 → 记账 + 返回空,**不抛**(§0 所有新源 B 级)。

    `status` 四态:`OK`(有行)/ `EMPTY`(源成功,近 90 日真无申报)/ `NO_CIK`(ticker 解析不到,
    多半非美股)/ `FAILED`(UA 缺席 / 网络 / schema)。`NO_CIK` 与 `FAILED` 分开是有用的:
    前者是「这只票本来就没有 EDGAR」,后者是「我们坏了」。
    """
    from autoresearch.data.contracts import record_degradation

    key = str(ticker_or_cik)
    try:
        df = fetch_submissions(ticker_or_cik, **kwargs)
    except EdgarError as exc:
        if "不在官方 company_tickers.json" in str(exc) or "不是合法 CIK" in str(exc):
            record_degradation(ENDPOINT, f"ticker→CIK 未命中 → EDGAR 块缺席({exc})", key=key)
            return FetchOutcome(pd.DataFrame(columns=list(_FILING_COLS)), "NO_CIK", str(exc))
        record_degradation(ENDPOINT, f"取数/解析失败:{exc}", key=key)
        return FetchOutcome(pd.DataFrame(columns=list(_FILING_COLS)), "FAILED", str(exc))
    except Exception as exc:                                       # noqa: BLE001
        reason = f"{type(exc).__name__}: {exc}"
        record_degradation(ENDPOINT, f"取数失败:{reason}", key=key)
        return FetchOutcome(pd.DataFrame(columns=list(_FILING_COLS)), "FAILED", reason)
    if not len(df):
        record_degradation(ENDPOINT, "近 90 日 0 条申报(源成功,真实空)", key=key,
                           kind="legit_empty")
        return FetchOutcome(df, "EMPTY")
    return FetchOutcome(df, "OK")


def acceptance_ts(row: dict) -> datetime | None:
    """`acceptanceDateTime` → aware UTC。EDGAR 给的是 ET 本地串(带偏移)时解析,否则 None。

    **不猜时区**:`filingDate` 只有日期,拿它当「公开可见时刻」会在盘后申报上错一天。
    """
    from autoresearch.data.sources.official_event_calendar import parse_ts

    raw = str(row.get("acceptance_datetime") or "").strip()
    if not raw:
        return None
    try:
        return parse_ts(raw)
    except Exception:                                              # noqa: BLE001
        return None


__all__ = [
    "ENDPOINT", "SUBMISSIONS_URL", "COMPANY_TICKERS_URL", "UA_ENV", "DEFAULT_LOOKBACK_DAYS",
    "EdgarNotConfiguredError", "EdgarError", "FetchOutcome",
    "user_agent", "headers", "to_cik10", "fetch_company_tickers", "resolve_cik",
    "fetch_submissions", "fetch_submissions_safe", "acceptance_ts",
]
