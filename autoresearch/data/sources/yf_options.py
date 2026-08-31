#!/usr/bin/env python3
"""美股期权 v2(yfinance option_chain)—— 窄快照(30D ATM IV)+ 全快照(研究档)。

design: docs/specs/2026-08-28-external-evidence-expansion-design.md §5.2 / §9(D-1、D-3)。
取代 `analyze/harvest.py:options_iv_summary`(v1:只取"最近一个到期"、跨式用 `lastPrice`、
PCR 直接渲染成"偏防御/看空" —— 三处都是本模块要修的病)。

## 阶段裁定(§5.1,不可越界)

期权信息**只在「研究 / 描述」阶段进,永远不在「筛选 / 评级 / regime」阶段进**:

  A 股 ETF/指数期权   全阶段 ✗(08-24 普查三族零正证据,「别再提波指温度计 / PCR 进 regime」)
  美股个股期权        ✓ 仅 stock-research full(美股票);L5 只作日历行的「隐含波动 ±x%」量级注
  美股指数波动率      ✓ 仅 macro full(见 `yf_tape.py`)

**不得由期权推出评级。**隐含波动 = 预期**量级**,不是方向。

## 四条硬纪律(每条都对应一次真事故形态)

1. **UNMEASURED 不是 0**。链取数成功但报价质量不合格 → `status="UNMEASURED"` + `reason`,
   保留质量原因。写 0 会让下游把"没测到"读成"波动为零"。
2. **禁止退回 `lastPrice`**。财报隐含波动只用**有效 bid-ask midpoint** 跨式;任一腿
   crossed / stale / 缺报价 → 直接 UNMEASURED。`lastPrice` 可能是三周前一笔 1 张的成交,
   拿它当"市场定价"是把陈货冒充报价(v1 的原病)。
3. **PCR 只写数字与「对冲 / 持仓压力」语义,不写方向**(§5.1 普查裁定)。不得渲染成
   "偏防御 / 看空 / 看多 / bearish / bullish",不得跨品种加总。
4. **IV 分位只比较相同 `method_version` 的 30D tenor**,且要求 ≥40 个**完整交易时段**的
   有效观测;否则「样本不足(n)」。不同 tenor / 不同口径串成的"历史"不是历史。

## 明确不做(§5.2 末行)

max-pain、OI 集中价位磁吸、异动期权(unusual activity)网查 —— 前两者是普查裁定的噪声,
第三者没有可验证的确定性来源。**不要在本模块加回来。**

## 契约级别

B 级:无挂牌期权 / 任何取数异常 → `record_degradation("us_options", ...)` + 降级块,
**不抛异常**。湖键 `us_options/<sym>@<as_of>`(`snapshot: True` —— 期权链没有历史参数,
今天不采今天的报价就永远没有了;as_of 必须等于真实今天,否则 `SnapshotDateError`)。

## 注入点(测试用)

`client` 参数接受任意满足下列 duck-type 的对象(默认包一层 `yfinance.Ticker`):

    expirations() -> list[str]              # 'YYYY-MM-DD' 升序
    option_chain(expiry) -> (calls, puts)   # 两个 DataFrame
    spot() -> (price: float|None, as_of: str|None)
    daily_closes(lookback: int) -> pd.Series
    market_state() -> str                   # 'REGULAR' / 'CLOSED' / 'POST' / ''
"""
from __future__ import annotations

import math
import os
from datetime import date, datetime, timezone
from typing import Iterable, Mapping, Sequence

import pandas as pd

from autoresearch.data.sources.yf_tape import session_complete as _us_session_complete

# 口径版本:**任何影响 30D ATM IV 数值口径的改动都必须改它**(插值方式 / ATM 带宽 /
# 中值定义 / 质量门)。分位只在同版本内比较 —— 版本不变而口径变了 = 悄悄污染整条历史。
METHOD_VERSION = "options_v2.0"

UNMEASURED = "UNMEASURED"
STATUS_OK = "ok"

TARGET_DTE = 30                 # 30D tenor
ATM_MEDIAN_K = 3                # ATM IV = 距 spot 最近 K 个有效执行价的**中值**
ATM_BAND = 0.10                 # ATM 带宽:|K/S − 1| ≤ 10% 才算 near-the-money
MIN_VALID_CONTRACTS = 6         # 单个到期的有效合约数下限(call+put 合计)
MIN_OI_COVERAGE = 0.30          # OI > 0 的合约占比下限
MAX_QUOTE_AGE_SEC = 6 * 3600    # 报价 age 上限(单个交易时段内)
NARROW_FETCH_BUDGET = 4         # 窄快照最多抓几个到期的链
FULL_EXPIRY_COUNT = 4           # 全快照要的有效到期数
FULL_FETCH_BUDGET = 8           # 全快照最多抓几个到期的链
SKEW_PUT_MONEYNESS = 0.90
SKEW_CALL_MONEYNESS = 1.10
SKEW_TOLERANCE = 0.05           # |K/S − 目标| ≤ 5% 才算命中 10% 价外
REALIZED_VOL_WINDOW = 20
TRADING_DAYS = 252
IV_PCTILE_MIN_OBS = 40          # §5.2:≥40 个完整交易时段的有效观测才印分位

PCR_SEMANTICS = "PCR = OI put/call,只描述**对冲 / 持仓压力**的量级;不含方向,不跨品种加总"
SKEW_LABEL = "10% 价外偏度代理"
IV_UNIT = "annualized_pct"

# 渲染层禁词(测试断言用):PCR / 偏度 / IV 一律不得出现方向措辞。
FORBIDDEN_DIRECTION_WORDS: tuple[str, ...] = (
    "看空", "看多", "偏空", "偏多", "利空", "利好", "bearish", "bullish", "buy", "sell",
)


class OptionsFetchError(RuntimeError):
    """链取数/解析失败 —— 由 client 抛,`narrow_snapshot`/`full_chain` 捕获成 B 级降级。"""


# ───────────────────────── 默认 client(yfinance) ─────────────────────────


class _YFinanceClient:
    """`yfinance.Ticker` 的薄包装。第三方 import 延迟到方法内(本包最底层,不该硬依赖)。"""

    def __init__(self, symbol: str):
        self.symbol = symbol
        self._t = None

    def _ticker(self):
        if self._t is None:
            if _offline():
                raise OptionsFetchError("AUTORESEARCH_OFFLINE=1:离线模式不取网络")
            try:
                import yfinance as yf
            except Exception as exc:  # noqa: BLE001
                raise OptionsFetchError(f"yfinance 不可用:{exc!r}") from exc
            self._t = yf.Ticker(self.symbol)
        return self._t

    def expirations(self) -> list[str]:
        try:
            return [str(e) for e in (getattr(self._ticker(), "options", None) or [])]
        except OptionsFetchError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise OptionsFetchError(f"{self.symbol} 到期列表失败:{type(exc).__name__}: {exc}") from exc

    def option_chain(self, expiry: str):
        try:
            ch = self._ticker().option_chain(expiry)
            return ch.calls, ch.puts
        except OptionsFetchError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise OptionsFetchError(
                f"{self.symbol} {expiry} 链取数失败:{type(exc).__name__}: {exc}") from exc

    def spot(self) -> tuple[float | None, str | None]:
        t = self._ticker()
        for attr in ("fast_info", "info"):
            try:
                blob = getattr(t, attr, None)
                blob = blob() if callable(blob) else blob
                for key in ("last_price", "lastPrice", "regularMarketPrice", "previousClose"):
                    v = (blob or {}).get(key) if isinstance(blob, Mapping) else getattr(blob, key, None)
                    if v is not None and not pd.isna(v):
                        return float(v), _utc_now_iso()
            except Exception:  # noqa: BLE001 — spot 拿不到不该炸整条链
                continue
        try:
            h = t.history(period="5d")
            if h is not None and len(h):
                return float(h["Close"].dropna().iloc[-1]), _utc_now_iso()
        except Exception:  # noqa: BLE001
            pass
        return None, None

    def daily_closes(self, lookback: int = 60) -> pd.Series:
        try:
            h = self._ticker().history(period=f"{max(lookback * 2, 60)}d", auto_adjust=False)
        except Exception as exc:  # noqa: BLE001
            raise OptionsFetchError(f"{self.symbol} 日线失败:{type(exc).__name__}: {exc}") from exc
        if h is None or not len(h):
            return pd.Series(dtype="float64")
        return pd.to_numeric(h["Close"], errors="coerce").dropna().tail(lookback + 1)

    def market_state(self) -> str:
        try:
            info = getattr(self._ticker(), "info", None) or {}
            return str(info.get("marketState") or "")
        except Exception:  # noqa: BLE001
            return ""


def _offline() -> bool:
    return str(os.environ.get("AUTORESEARCH_OFFLINE", "")).strip() in {"1", "true", "TRUE", "yes"}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _iso_date(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        if len(s) == 8 and s.isdigit():
            return f"{s[:4]}-{s[4:6]}-{s[6:]}"
        return s[:10]
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    ts = pd.Timestamp(value)
    return None if pd.isna(ts) else ts.date().isoformat()


def _today_et(now: datetime | None = None) -> str:
    from zoneinfo import ZoneInfo

    return (now or datetime.now(timezone.utc)).astimezone(ZoneInfo("America/New_York")).date().isoformat()


def _dte(expiry: str, as_of: str) -> int | None:
    try:
        return (date.fromisoformat(expiry) - date.fromisoformat(as_of)).days
    except Exception:  # noqa: BLE001
        return None


# ───────────────────────── 报价质量 ─────────────────────────


def _num_col(df: pd.DataFrame, col: str) -> pd.Series:
    """取一列数值;**列不存在 → 全 NaN 同长度 Series**(缺列是数据形态问题,不该炸成异常)。"""
    if df is None or col not in getattr(df, "columns", []):
        idx = getattr(df, "index", None)
        return pd.Series([float("nan")] * (0 if df is None else len(df)),
                         index=idx, dtype="float64")
    return pd.to_numeric(df[col], errors="coerce")


def _quote_ages(df: pd.DataFrame, now: datetime) -> pd.Series:
    """每行报价 age(秒);无 `lastTradeDate` 列 → 全 NaN(= age 不可判,**不当作新鲜**)。"""
    if "lastTradeDate" not in df.columns:
        return pd.Series([float("nan")] * len(df), index=df.index, dtype="float64")
    ts = pd.to_datetime(df["lastTradeDate"], errors="coerce", utc=True)
    ref = pd.Timestamp(now.astimezone(timezone.utc))
    return (ref - ts).dt.total_seconds()


def leg_quality(row: Mapping, *, now: datetime, max_age_sec: int = MAX_QUOTE_AGE_SEC,
                age_sec: float | None = None) -> dict:
    """单腿报价的质量判定 —— 财报跨式的守门人。

    返回 {"ok": bool, "reasons": [...], "mid": float|None, "age_sec": float|None}。
    `mid` 只由 **bid/ask 中点**给出;**任何情况下都不读 `lastPrice`**(纪律 2)。
    """
    reasons: list[str] = []
    bid = row.get("bid")
    ask = row.get("ask")
    bid = None if bid is None or pd.isna(bid) else float(bid)
    ask = None if ask is None or pd.isna(ask) else float(ask)
    if bid is None or ask is None:
        reasons.append("缺报价(bid/ask 为空)")
    else:
        if bid < 0 or ask < 0:
            reasons.append(f"报价为负(bid={bid}, ask={ask})")
        if ask <= 0:
            reasons.append("ask ≤ 0(无卖价)")
        if bid > ask:
            reasons.append(f"crossed(bid={bid} > ask={ask})")
    if age_sec is None or pd.isna(age_sec):
        reasons.append("quote age 不可判(缺 lastTradeDate)")
    elif age_sec > max_age_sec:
        reasons.append(f"stale(quote age {int(age_sec)}s > {max_age_sec}s)")
    mid = None
    if not reasons and bid is not None and ask is not None:
        mid = round((bid + ask) / 2.0, 6)
    return {"ok": not reasons, "reasons": reasons, "mid": mid,
            "age_sec": None if age_sec is None or pd.isna(age_sec) else float(age_sec)}


def _valid_frame(df: pd.DataFrame, *, now: datetime, max_age_sec: int) -> pd.DataFrame:
    """给链帧补 `_age_sec` / `_valid` / `_mid` / `_iv_pct` 列(不改原帧)。"""
    out = pd.DataFrame(df).copy()
    if out.empty:
        for c in ("_age_sec", "_valid", "_mid", "_iv_pct", "strike", "openInterest"):
            if c not in out.columns:
                out[c] = pd.Series(dtype="float64")
        out["_valid"] = out.get("_valid", pd.Series(dtype="bool"))
        return out
    ages = _quote_ages(out, now)
    out["_age_sec"] = ages
    checks = [leg_quality(r, now=now, max_age_sec=max_age_sec, age_sec=a)
              for r, a in zip(out.to_dict(orient="records"), ages)]
    out["_iv_pct"] = _num_col(out, "impliedVolatility") * 100.0
    out["strike"] = _num_col(out, "strike")
    out["openInterest"] = _num_col(out, "openInterest")
    out["_mid"] = [c["mid"] for c in checks]
    out["_valid"] = [
        bool(c["ok"]) and v is not None and not pd.isna(v) and float(v) > 0
        for c, v in zip(checks, out["_iv_pct"])
    ]
    out["_reasons"] = ["; ".join(c["reasons"]) for c in checks]
    return out


def expiry_quality(calls: pd.DataFrame, puts: pd.DataFrame) -> dict:
    """单个到期的数据质量块(§5.2「数据质量」行):bid/ask 非负且未 crossed、quote age、
    OI 覆盖率、有效合约数。`ok=False` 的到期**不参与插值**。"""
    total = int(len(calls) + len(puts))
    both = pd.concat([calls, puts], ignore_index=True) if total else pd.DataFrame()
    valid = int(both["_valid"].sum()) if total and "_valid" in both else 0
    oi = _num_col(both, "openInterest")
    oi_cov = float((oi.fillna(0) > 0).mean()) if total else 0.0
    ages = _num_col(both, "_age_sec")
    age_p50 = None if not total or ages.dropna().empty else float(ages.dropna().median())
    reasons: list[str] = []
    if total == 0:
        reasons.append("空链(0 合约)")
    if valid < MIN_VALID_CONTRACTS:
        reasons.append(f"有效合约不足({valid} < {MIN_VALID_CONTRACTS})")
    if oi_cov < MIN_OI_COVERAGE:
        reasons.append(f"OI 覆盖率不足({oi_cov:.0%} < {MIN_OI_COVERAGE:.0%})")
    if age_p50 is None and total:
        reasons.append("quote age 不可判(链缺 lastTradeDate)")
    return {
        "ok": not reasons,
        "total_contracts": total,
        "valid_contracts": valid,
        "oi_coverage": round(oi_cov, 4),
        "quote_age_p50_sec": None if age_p50 is None else round(age_p50, 1),
        "max_quote_age_sec": MAX_QUOTE_AGE_SEC,
        "reasons": reasons,
    }


def _atm_iv(df: pd.DataFrame, spot: float, *, k: int = ATM_MEDIAN_K) -> float | None:
    """距 spot 最近 k 个**有效**执行价的 IV **中值**(年化 %);不足 → None。"""
    if df is None or df.empty or "_valid" not in df or spot in (None, 0):
        return None
    sub = df[df["_valid"]].copy()
    if sub.empty:
        return None
    strikes = _num_col(sub, "strike")
    sub = sub.assign(_d=(strikes - float(spot)).abs(), _mny=(strikes / float(spot) - 1.0).abs())
    sub = sub[sub["_mny"] <= ATM_BAND]
    if sub.empty:
        return None
    near = sub.nsmallest(min(k, len(sub)), "_d")
    val = float(pd.to_numeric(near["_iv_pct"], errors="coerce").median())
    return None if pd.isna(val) else round(val, 4)


def _pcr(calls: pd.DataFrame, puts: pd.DataFrame) -> dict:
    call_oi = float(_num_col(calls, "openInterest").fillna(0).sum())
    put_oi = float(_num_col(puts, "openInterest").fillna(0).sum())
    ratio = round(put_oi / call_oi, 4) if call_oi else None
    return {"call_oi": call_oi, "put_oi": put_oi, "pcr_oi": ratio}


def _chain_point(client, expiry: str, *, as_of: str, spot: float | None,
                 now: datetime, max_age_sec: int) -> dict:
    calls_raw, puts_raw = client.option_chain(expiry)
    calls = _valid_frame(calls_raw, now=now, max_age_sec=max_age_sec)
    puts = _valid_frame(puts_raw, now=now, max_age_sec=max_age_sec)
    q = expiry_quality(calls, puts)
    atm_c = _atm_iv(calls, spot) if spot else None
    atm_p = _atm_iv(puts, spot) if spot else None
    both = [v for v in (atm_c, atm_p) if v is not None]
    atm = round(sum(both) / len(both), 4) if both else None
    return {
        "expiry": expiry,
        "dte": _dte(expiry, as_of),
        "atm_iv_call": atm_c,
        "atm_iv_put": atm_p,
        "atm_iv": atm,
        "iv_unit": IV_UNIT,
        "quality": q,
        **_pcr(calls, puts),
        "_calls": calls,
        "_puts": puts,
    }


def _public(point: dict) -> dict:
    """剥掉 `_calls`/`_puts` 两个内部帧(它们不进 JSON)。"""
    return {k: v for k, v in point.items() if not k.startswith("_")}


# ───────────────────────── 30D 插值 ─────────────────────────


def interpolate_30d(points: Sequence[Mapping]) -> dict:
    """由**包围 30D 的两个有效到期**线性插值出一致口径的 30D ATM IV。

    不足 / 质量不够 → `{"status": "UNMEASURED", "reason": ...}`,**不是 0**(纪律 1)。
    插值在 DTE 上线性(口径写死在 `METHOD_VERSION` 里:换插值方式必须改版本号,
    否则新旧数会被 `iv_percentile` 当成同一条历史比较)。
    """
    usable = [p for p in points
              if p.get("dte") is not None and p.get("atm_iv") is not None
              and (p.get("quality") or {}).get("ok")]
    base = {"tenor_days": TARGET_DTE, "method_version": METHOD_VERSION, "iv_unit": IV_UNIT}
    if not usable:
        why = "; ".join(
            f"{p.get('expiry')}: {'/'.join((p.get('quality') or {}).get('reasons') or ['无 ATM IV'])}"
            for p in points) or "无到期数据"
        return {**base, "status": UNMEASURED, "atm_iv_30d": None,
                "reason": f"无质量合格的到期({why})"}
    exact = [p for p in usable if p["dte"] == TARGET_DTE]
    if exact:
        p = exact[0]
        return {**base, "status": STATUS_OK, "atm_iv_30d": round(float(p["atm_iv"]), 4),
                "method": "exact", "legs": [p["expiry"]], "reason": ""}
    near = [p for p in usable if p["dte"] < TARGET_DTE]
    far = [p for p in usable if p["dte"] > TARGET_DTE]
    if not near or not far:
        have = sorted(p["dte"] for p in usable)
        return {**base, "status": UNMEASURED, "atm_iv_30d": None,
                "reason": f"无包围 30D 的有效到期(现有 DTE {have})—— 不外推、不拿别的 tenor 冒充"}
    lo = max(near, key=lambda p: p["dte"])
    hi = min(far, key=lambda p: p["dte"])
    span = hi["dte"] - lo["dte"]
    w = (TARGET_DTE - lo["dte"]) / span if span else 0.0
    iv = float(lo["atm_iv"]) + w * (float(hi["atm_iv"]) - float(lo["atm_iv"]))
    return {**base, "status": STATUS_OK, "atm_iv_30d": round(iv, 4), "method": "linear_dte",
            "legs": [lo["expiry"], hi["expiry"]],
            "leg_dte": [lo["dte"], hi["dte"]], "weight": round(w, 4), "reason": ""}


# ───────────────────────── 窄快照 ─────────────────────────


def narrow_snapshot(symbol: str, as_of: str | None = None, *, client=None,
                    now: datetime | None = None, max_age_sec: int = MAX_QUOTE_AGE_SEC,
                    record: bool = True) -> dict:
    """日更窄快照:**只出一致口径的 30D ATM IV**(持仓 / 当日研究票 / 映射票)。

    不为了一个日历量级把映射池每天全链抓爆 —— 最多抓 `NARROW_FETCH_BUDGET` 个到期
    (按 |DTE − 30| 排序),找到包围 30D 的合格一对就停。

    返回:
      {"symbol", "as_of", "spot", "spot_as_of", "chain_fetched_at", "market_state",
       "session_complete", "method_version", "status": ok|UNMEASURED,
       "atm_iv_30d": float|None, "iv_unit": "annualized_pct", "reason": str,
       "interpolation": {...}, "expiries": [每到期的 quality/ATM/PCR], "degraded": [...]}
    """
    now = now or datetime.now(timezone.utc)
    as_of_d = _iso_date(as_of) or _today_et(now)
    cli = client or _YFinanceClient(symbol)
    out = _base_result(symbol, as_of_d, now)
    try:
        expiries = [e for e in (cli.expirations() or []) if _iso_date(e)]
    except BaseException as exc:  # noqa: BLE001
        return _degrade(out, f"到期列表失败:{type(exc).__name__}: {exc}", record=record, symbol=symbol)
    if not expiries:
        return _degrade(out, "该标的在 yfinance 无挂牌期权(A股/港股等常见)→ 期权/IV 不可用",
                        record=record, symbol=symbol, kind="legit_empty")
    try:
        spot, spot_as_of = cli.spot()
        out["spot"], out["spot_as_of"] = spot, spot_as_of
        out["market_state"] = cli.market_state()
    except BaseException as exc:  # noqa: BLE001
        return _degrade(out, f"spot 取数失败:{type(exc).__name__}: {exc}", record=record, symbol=symbol)
    if not spot:
        return _degrade(out, "spot 不可得 → ATM 无法定位", record=record, symbol=symbol)

    ordered = sorted(
        [e for e in expiries if (_dte(_iso_date(e), as_of_d) or -1) >= 0],
        key=lambda e: abs((_dte(_iso_date(e), as_of_d) or 10**6) - TARGET_DTE))
    points, errs = [], []
    for exp in ordered[:NARROW_FETCH_BUDGET]:
        try:
            points.append(_chain_point(cli, _iso_date(exp), as_of=as_of_d, spot=spot,
                                       now=now, max_age_sec=max_age_sec))
        except BaseException as exc:  # noqa: BLE001
            errs.append(f"{exp}: {type(exc).__name__}: {exc}")
    out["expiries"] = [_public(p) for p in points]
    interp = interpolate_30d(points)
    out["interpolation"] = interp
    out["atm_iv_30d"] = interp.get("atm_iv_30d")
    out["status"] = interp["status"]
    out["reason"] = interp.get("reason", "")
    if errs:
        out.setdefault("degraded", []).extend(errs)
        if record:
            for e in errs:
                _record(f"{symbol} 链取数失败 → {e}", key=f"{symbol}@{as_of_d}")
    if out["status"] != STATUS_OK and record:
        _record(f"{symbol} 30D ATM IV UNMEASURED:{out['reason']}", key=f"{symbol}@{as_of_d}")
    return out


# ───────────────────────── 全快照 ─────────────────────────


def full_chain(symbol: str, as_of: str | None = None, earnings_dt=None, *, client=None,
               now: datetime | None = None, max_age_sec: int = MAX_QUOTE_AGE_SEC,
               record: bool = True) -> dict:
    """研究档全快照:最近 `FULL_EXPIRY_COUNT` 个**有效**到期 + 财报后首个到期。

    `earnings_dt` 接受 `date` / `datetime` / 'YYYY-MM-DD' / `{"date":…, "session": "AMC"|"BMO"}`。
    时段(AMC/BMO)只影响 `time_quality` 标注;隐含波动的硬条件恒为**到期日严格晚于财报日**
    —— 这一条对 AMC / BMO 都安全,故时段未确认时不猜、只标 `DATE_ONLY`。

    返回(§5.2 字段表):
      基本:symbol / spot / as_of / spot_as_of / chain_fetched_at / market_state /
            session_complete / method_version / status
      expiries[]:每到期的 quality(bid-ask 非负未 crossed、quote age、OI 覆盖率、有效合约数)
                  + atm_iv_call / atm_iv_put(中值)/ atm_iv / dte / call_oi / put_oi / pcr_oi
      term_structure:points + `slope = IV_near − IV_far`
      atm_iv_30d:`interpolate_30d` 的一致口径块
      earnings_implied_move:**只用有效 bid-ask midpoint 跨式**;任一腿 crossed/stale/缺报价
                            → UNMEASURED(**不退回 lastPrice**)
      vol_premium:`ATM IV(30D) − 20 日已实现年化波动`
      skew_proxy:`IV(put,K≈0.9S) − IV(call,K≈1.1S)`,标「10% 价外偏度代理」
      pcr:每到期 + 合计,`semantics` 恒为对冲/持仓压力,**不写方向**
    """
    now = now or datetime.now(timezone.utc)
    as_of_d = _iso_date(as_of) or _today_et(now)
    cli = client or _YFinanceClient(symbol)
    out = _base_result(symbol, as_of_d, now)
    out.update({"term_structure": None, "earnings_implied_move": None, "vol_premium": None,
                "skew_proxy": None, "pcr": None,
                "not_computed": ["max_pain", "oi_magnet", "unusual_activity"]})
    try:
        expiries = [_iso_date(e) for e in (cli.expirations() or []) if _iso_date(e)]
    except BaseException as exc:  # noqa: BLE001
        return _degrade(out, f"到期列表失败:{type(exc).__name__}: {exc}", record=record, symbol=symbol)
    if not expiries:
        return _degrade(out, "该标的在 yfinance 无挂牌期权(A股/港股等常见)→ 期权/IV 不可用",
                        record=record, symbol=symbol, kind="legit_empty")
    try:
        spot, spot_as_of = cli.spot()
        out["spot"], out["spot_as_of"] = spot, spot_as_of
        out["market_state"] = cli.market_state()
    except BaseException as exc:  # noqa: BLE001
        return _degrade(out, f"spot 取数失败:{type(exc).__name__}: {exc}", record=record, symbol=symbol)
    if not spot:
        return _degrade(out, "spot 不可得 → ATM 无法定位", record=record, symbol=symbol)

    future = sorted(e for e in expiries if (_dte(e, as_of_d) or -1) >= 0)
    earn = _earnings_spec(earnings_dt)
    wanted: list[str] = []
    points: dict[str, dict] = {}
    errs: list[str] = []
    for exp in future[:FULL_FETCH_BUDGET]:
        try:
            p = _chain_point(cli, exp, as_of=as_of_d, spot=spot, now=now, max_age_sec=max_age_sec)
        except BaseException as exc:  # noqa: BLE001
            errs.append(f"{exp}: {type(exc).__name__}: {exc}")
            continue
        points[exp] = p
        if p["quality"]["ok"]:
            wanted.append(exp)
        if len([e for e in wanted]) >= FULL_EXPIRY_COUNT and (
                earn["date"] is None or any(e > earn["date"] for e in points)):
            break
    earn_exp = None
    if earn["date"]:
        after = [e for e in future if e > earn["date"]]
        if after:
            earn_exp = after[0]
            if earn_exp not in points and len(points) < FULL_FETCH_BUDGET + 2:
                try:
                    points[earn_exp] = _chain_point(cli, earn_exp, as_of=as_of_d, spot=spot,
                                                    now=now, max_age_sec=max_age_sec)
                except BaseException as exc:  # noqa: BLE001
                    errs.append(f"{earn_exp}: {type(exc).__name__}: {exc}")

    ordered = [points[e] for e in sorted(points)]
    valid_pts = [p for p in ordered if p["quality"]["ok"]][:FULL_EXPIRY_COUNT]
    out["expiries"] = [_public(p) for p in ordered]

    # 期限结构:全档最近 N 个**有效**到期的 ATM IV + DTE;slope = IV_near − IV_far
    ts_points = [{"expiry": p["expiry"], "dte": p["dte"], "atm_iv": p["atm_iv"],
                  "atm_iv_call": p["atm_iv_call"], "atm_iv_put": p["atm_iv_put"]}
                 for p in valid_pts if p["atm_iv"] is not None]
    slope = None
    if len(ts_points) >= 2:
        slope = round(float(ts_points[0]["atm_iv"]) - float(ts_points[-1]["atm_iv"]), 4)
    out["term_structure"] = {
        "points": ts_points, "slope": slope, "iv_unit": IV_UNIT,
        "slope_def": "slope = IV_near − IV_far(正 = 近端更贵 / 倒挂)",
        "status": STATUS_OK if slope is not None else UNMEASURED,
        "reason": "" if slope is not None else f"有效到期不足 2 个(现有 {len(ts_points)})",
    }

    interp = interpolate_30d(ordered)
    out["interpolation"] = interp
    out["atm_iv_30d"] = interp.get("atm_iv_30d")
    out["status"] = interp["status"]
    out["reason"] = interp.get("reason", "")

    out["earnings_implied_move"] = _earnings_implied_move(
        points.get(earn_exp) if earn_exp else None, spot=spot, earn=earn, expiry=earn_exp)
    out["vol_premium"] = _vol_premium(cli, interp, errs)
    out["skew_proxy"] = _skew_proxy(ordered, spot)
    out["pcr"] = _pcr_block(ordered)

    if errs:
        out.setdefault("degraded", []).extend(errs)
    if record:
        for e in errs:
            _record(f"{symbol} 链取数失败 → {e}", key=f"{symbol}@{as_of_d}")
        if out["status"] != STATUS_OK:
            _record(f"{symbol} 30D ATM IV UNMEASURED:{out['reason']}", key=f"{symbol}@{as_of_d}")
    return out


# ───────────────────────── 派生块 ─────────────────────────


def _earnings_spec(earnings_dt) -> dict:
    """归一化财报时点:{"date": 'YYYY-MM-DD'|None, "session": 'AMC'|'BMO'|None,
    "time_quality": 'TIMED'|'DATE_ONLY'|'UNKNOWN'}。**不猜时段**(§2 事件契约)。"""
    if earnings_dt is None:
        return {"date": None, "session": None, "time_quality": "UNKNOWN"}
    if isinstance(earnings_dt, Mapping):
        d = _iso_date(earnings_dt.get("date") or earnings_dt.get("dt"))
        s = str(earnings_dt.get("session") or "").upper() or None
    else:
        d, s = _iso_date(earnings_dt), None
    if s not in (None, "AMC", "BMO"):
        s = None
    return {"date": d, "session": s,
            "time_quality": ("TIMED" if (d and s) else "DATE_ONLY" if d else "UNKNOWN")}


def _earnings_implied_move(point: Mapping | None, *, spot: float, earn: Mapping,
                           expiry: str | None) -> dict:
    """财报隐含波动 = ATM **有效 bid-ask midpoint** 跨式 / spot。

    **纪律 2**:任一腿 crossed / stale / 缺报价 → `UNMEASURED` + 原因;**禁止退回
    `lastPrice`** —— 本函数从头到尾不读该列。
    """
    base = {"status": UNMEASURED, "implied_move_pct": None, "straddle": None,
            "expiry": expiry, "earnings_date": earn.get("date"),
            "earnings_session": earn.get("session"), "time_quality": earn.get("time_quality"),
            "method": "ATM bid-ask midpoint straddle / spot(禁用 lastPrice)",
            "requires": "到期日严格晚于财报日", "reason": ""}
    if not earn.get("date"):
        return {**base, "reason": "无确认的下次财报日 → 不测(不猜时段、不用日历外推)"}
    if point is None or not expiry:
        return {**base, "reason": "无严格晚于财报日的可用到期"}
    if not (expiry > earn["date"]):
        return {**base, "reason": f"到期 {expiry} 未严格晚于财报日 {earn['date']}"}
    calls, puts = point.get("_calls"), point.get("_puts")
    if calls is None or puts is None or calls.empty or puts.empty:
        return {**base, "reason": "链为空"}
    legs = {}
    for name, df in (("call", calls), ("put", puts)):
        dist = (_num_col(df, "strike") - float(spot)).abs().dropna()
        if dist.empty:
            return {**base, "reason": f"{name} 腿无可解析执行价"}
        row = df.loc[dist.idxmin()]
        legs[name] = {"strike": float(row["strike"]),
                      "mid": None if pd.isna(row.get("_mid")) else row.get("_mid"),
                      "reasons": str(row.get("_reasons") or "")}
    bad = [f"{k} K={v['strike']}: {v['reasons'] or '无有效中点'}"
           for k, v in legs.items() if v["mid"] is None]
    if bad:
        return {**base, "legs": legs,
                "reason": "腿报价不合格 → UNMEASURED(不退回 lastPrice):" + "; ".join(bad)}
    straddle = float(legs["call"]["mid"]) + float(legs["put"]["mid"])
    return {**base, "status": STATUS_OK, "legs": legs,
            "straddle": round(straddle, 6),
            "implied_move_pct": round(straddle / float(spot) * 100.0, 3),
            "note": "隐含波动 = 预期**量级**,不是方向;与历史实际波动对照才有解读价值"}


def realized_vol(closes: Sequence[float] | pd.Series, *, window: int = REALIZED_VOL_WINDOW) -> float | None:
    """`window` 日已实现波动(对数收益标准差年化,%);观测不足 → None(不是 0)。"""
    s = pd.Series(list(closes), dtype="float64").dropna()
    if len(s) < window + 1:
        return None
    rets = (s / s.shift(1)).dropna().apply(lambda x: math.log(x) if x > 0 else float("nan")).dropna()
    rets = rets.tail(window)
    if len(rets) < window:
        return None
    sd = float(rets.std(ddof=1))
    return None if pd.isna(sd) else round(sd * math.sqrt(TRADING_DAYS) * 100.0, 4)


def _vol_premium(client, interp: Mapping, errs: list[str]) -> dict:
    base = {"status": UNMEASURED, "atm_iv_30d": interp.get("atm_iv_30d"),
            "realized_vol_20d": None, "premium_pp": None, "iv_unit": IV_UNIT,
            "def": "vol_premium = ATM IV(30D) − 20 日已实现年化波动(百分点)", "reason": ""}
    if interp.get("status") != STATUS_OK:
        return {**base, "reason": f"30D ATM IV 不可测:{interp.get('reason')}"}
    try:
        closes = client.daily_closes(REALIZED_VOL_WINDOW + 5)
    except BaseException as exc:  # noqa: BLE001
        errs.append(f"日线失败:{type(exc).__name__}: {exc}")
        return {**base, "reason": f"日线不可得:{type(exc).__name__}"}
    rv = realized_vol(closes)
    if rv is None:
        return {**base, "reason": f"20 日已实现波动样本不足({len(pd.Series(closes).dropna())})"}
    return {**base, "status": STATUS_OK, "realized_vol_20d": rv,
            "premium_pp": round(float(interp["atm_iv_30d"]) - rv, 4)}


def _pick_moneyness(df: pd.DataFrame, spot: float, target: float) -> dict | None:
    if df is None or df.empty or "_valid" not in df:
        return None
    sub = df[df["_valid"]].copy()
    if sub.empty:
        return None
    strikes = _num_col(sub, "strike")
    sub = sub.assign(_mny=strikes / float(spot), _d=(strikes / float(spot) - target).abs())
    sub = sub[sub["_d"] <= SKEW_TOLERANCE]
    if sub.empty:
        return None
    hit = sub.nsmallest(1, "_d").iloc[0]
    return {"strike": float(hit["strike"]), "moneyness": round(float(hit["_mny"]), 4),
            "iv_pct": round(float(hit["_iv_pct"]), 4)}


def _skew_proxy(points: Sequence[Mapping], spot: float) -> dict:
    """`IV(put, K≈0.9·spot) − IV(call, K≈1.1·spot)` —— 无 Greeks,**不能算 25Δ**,
    故一律标注为「10% 价外偏度代理」。只描述结构,不含方向。"""
    base = {"status": UNMEASURED, "label": SKEW_LABEL, "skew_pp": None,
            "put_leg": None, "call_leg": None, "expiry": None, "iv_unit": IV_UNIT,
            "note": "无 Greeks → 不是 25Δ 偏度;只描述**仓位结构**,不含方向", "reason": ""}
    usable = [p for p in points if p["quality"]["ok"] and p.get("dte") is not None]
    if not usable:
        return {**base, "reason": "无质量合格的到期"}
    ref = min(usable, key=lambda p: abs(p["dte"] - TARGET_DTE))
    put_leg = _pick_moneyness(ref.get("_puts"), spot, SKEW_PUT_MONEYNESS)
    call_leg = _pick_moneyness(ref.get("_calls"), spot, SKEW_CALL_MONEYNESS)
    if put_leg is None or call_leg is None:
        miss = ", ".join(n for n, v in (("put K≈0.9S", put_leg), ("call K≈1.1S", call_leg)) if v is None)
        return {**base, "expiry": ref["expiry"], "reason": f"缺有效腿({miss},容差 ±{SKEW_TOLERANCE:.0%})"}
    return {**base, "status": STATUS_OK, "expiry": ref["expiry"], "put_leg": put_leg,
            "call_leg": call_leg,
            "skew_pp": round(put_leg["iv_pct"] - call_leg["iv_pct"], 4)}


def _pcr_block(points: Sequence[Mapping]) -> dict:
    """每到期 OI put/call + 合计。**只写数字与「对冲 / 持仓压力」语义,不写方向**(纪律 3)。"""
    by_expiry = [{"expiry": p["expiry"], "dte": p["dte"], "call_oi": p["call_oi"],
                  "put_oi": p["put_oi"], "pcr_oi": p["pcr_oi"]} for p in points]
    call_total = sum(float(p["call_oi"] or 0) for p in points)
    put_total = sum(float(p["put_oi"] or 0) for p in points)
    total = round(put_total / call_total, 4) if call_total else None
    return {
        "by_expiry": by_expiry,
        "total": {"call_oi": call_total, "put_oi": put_total, "pcr_oi": total},
        "semantics": PCR_SEMANTICS,
        "status": STATUS_OK if total is not None else UNMEASURED,
        "reason": "" if total is not None else "call OI 合计为 0 → 比值无定义",
    }


# ───────────────────────── IV 分位 ─────────────────────────


def iv_percentile(observations: Iterable[Mapping], value: float | None = None, *,
                  method_version: str = METHOD_VERSION, tenor_days: int = TARGET_DTE,
                  min_obs: int = IV_PCTILE_MIN_OBS) -> dict:
    """30D ATM IV 的历史分位 —— **只比较相同 `method_version` 的同一 tenor**(纪律 4)。

    `observations`:`lake/us_options/<sym>@<as_of>` 读出的历史观测,每条至少含
    `atm_iv_30d` / `method_version` / `tenor_days`(可缺,缺则按默认)/ `session_complete`。

    过滤三道:① `method_version` 必须相同(口径变了就不是同一条历史)② `tenor_days`
    必须相同(不同 tenor 串起来不是历史)③ `session_complete` 必须为真(半截时段的
    报价不是"一个交易时段的观测")。剩余 < `min_obs` → `样本不足(n)`,**不给数**。
    """
    rows = list(observations or [])
    kept, dropped = [], {"method_version": 0, "tenor": 0, "incomplete_session": 0, "no_value": 0}
    for r in rows:
        if str(r.get("method_version") or "") != method_version:
            dropped["method_version"] += 1
            continue
        if int(r.get("tenor_days") or tenor_days) != tenor_days:
            dropped["tenor"] += 1
            continue
        if not bool(r.get("session_complete", False)):
            dropped["incomplete_session"] += 1
            continue
        v = r.get("atm_iv_30d")
        if v is None or pd.isna(v):
            dropped["no_value"] += 1
            continue
        kept.append(float(v))
    n = len(kept)
    cur = value if value is not None else (kept[-1] if kept else None)
    base = {"method_version": method_version, "tenor_days": tenor_days, "obs": n,
            "dropped": dropped, "min_obs": min_obs, "value": cur, "iv_unit": IV_UNIT}
    if n < min_obs:
        return {**base, "status": UNMEASURED, "pctile": None, "note": f"样本不足({n})"}
    if cur is None:
        return {**base, "status": UNMEASURED, "pctile": None, "note": "无当前值"}
    s = pd.Series(kept, dtype="float64")
    return {**base, "status": STATUS_OK,
            "pctile": round(float((s <= float(cur)).mean() * 100.0), 1), "note": ""}


# ───────────────────────── 结果骨架 / 降级 ─────────────────────────


def _base_result(symbol: str, as_of: str, now: datetime) -> dict:
    fetched = _utc_now_iso()
    return {
        "symbol": symbol,
        "as_of": as_of,
        "spot": None,
        "spot_as_of": None,
        "chain_fetched_at": fetched,
        "market_state": "",
        "session_complete": _us_session_complete("us", as_of, now=now),
        "method_version": METHOD_VERSION,
        "status": UNMEASURED,
        "atm_iv_30d": None,
        "iv_unit": IV_UNIT,
        "reason": "",
        "interpolation": None,
        "expiries": [],
        "degraded": [],
    }


def _record(reason: str, *, key: str = "", kind: str = "degraded") -> None:
    from autoresearch.data.contracts import record_degradation

    record_degradation("us_options", reason, key=key, kind=kind)


def _degrade(out: dict, reason: str, *, record: bool, symbol: str, kind: str = "degraded") -> dict:
    out["status"] = UNMEASURED
    out["reason"] = reason
    out.setdefault("degraded", []).append(reason)
    if record:
        _record(f"{symbol}: {reason}", key=f"{symbol}@{out.get('as_of', '')}", kind=kind)
    return out
