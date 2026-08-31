#!/usr/bin/env python3
"""全球隔夜 tape(yfinance)—— 22 个跨资产标的的收盘 + 1 日/5 日 % + 波动率派生。

design: docs/specs/2026-08-28-external-evidence-expansion-design.md §5.3 / §9(D-1)。

## 为什么要这张表

主尺 = T+1 15:00 CST 买 → T+2 09:30 CST 卖。美股常规时段(21:30–04:00 CST 夏令)、盘后财报、
FOMC(02:00 CST T+2)**整段落在持仓窗内且在 A 股 T+2 开盘前结束**(设计稿 §2)。所以这张表
对本项目不是"宏观背景",而是**隔夜窗内的已知事实**。

用途边界(设计稿 §0 / §5.1,不可越界):

- 只进 **research / 描述** 阶段(macro full 的 `global_tape.json`、L5 展示一行)。
- **不进** 筛选(L0–L3)、评级(L4)、regime、策略师 allowlist —— 后者是 B 类,受 A0 冻结,
  要开也是 `scan_config.jsonc` 一个默认关的开关(`external.strategist_global_tape`)。
- 所有字段是**数字地形**,不含方向指令。08-24 衍生品普查已证伪"波指温度计"一族,别重开旧案。

## 契约级别与降级(B 级)

全表 B 级:缺失永不阻断 run。**整体失败或部分缺失都返回可用部分,不抛**;每个失败/空的标的在
返回帧里留一行(status 列),并 `record_degradation("global_tape", ...)` 记账。

**「源成功但真空」与「请求/解析失败」必须可区分**(§9 湖纪律):两者都落空帧 = 把"今天没有
数据"和"我们没拿到数据"混成同一个下水道,而后者重跑能救、前者不能。故:

    status == "ok"      拿到可用日线
    status == "empty"   源正常应答但零行(标的退市/代码改名/该市场休市且无历史)
    status == "failed"  请求或解析失败(网络/限频/schema 变)—— 重跑可能救回来

**帧永远是"每个请求标的一行"**,不会因为失败就少一行(少一行 = 失败被静默吞掉)。

## 美股日 vs A 股日:分列不混

`000001.SS`(上证)与 `^HSI`(恒指)的最后一根日线不是美股交易日。把它们塞进同一个
`us_date` 列 = 制造一个"看起来对齐、其实错位"的时间锚(08-28 已在账本层逮到过同族事故:
13% 的 run 把时间锚记错对象)。故:

    bar_date   每个标的**自己交易所日历**下的最后一根日线日期(ISO)
    market     us / cn / hk / fx / commodity —— 决定收盘时刻与时区
    us_date    **只在 market == "us" 的行有值**,其余行为 None
    cn_date    **只在 market == "cn" 的行有值**,其余行为 None

整张表的美股参考日(= lake 分区键)另在 `global_tape_pack()["us_session_date"]` 给出。

时区一律用 IANA(`America/New_York` / `Asia/Shanghai` / `Asia/Hong_Kong`),**禁止写死
UTC±8 / ET 偏移**(§2 明令:必须覆盖 DST)。`fetched_at` 恒为 UTC ISO。
"""
from __future__ import annotations

import os
from datetime import date, datetime, time, timedelta, timezone
from typing import Callable, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

import pandas as pd

# ───────────────────────── 标的表 ─────────────────────────
#
# 设计稿 §9 写作「19 标的 + ZQ=F SR3=F」;§5.3 的隔夜 tape 行只点名 15 个,波动率行另点名
# `^VIX ^VIX3M ^SKEW ^MOVE`。下表是两处的并集(20)+ 利率路径 2 个 = 22 —— 稿里的「19」是
# 计数笔误,以**符号清单**为准(清单是可执行的,计数不是)。

TAPE_SYMBOLS: tuple[str, ...] = (
    "^GSPC", "^NDX", "^SOX", "^HSI", "000001.SS", "DX-Y.NYB", "^TNX",
    "^VIX", "^VIX3M", "^SKEW", "^MOVE",
    "CL=F", "GC=F", "HG=F", "USDCNH=X",
    "KWEB", "FXI", "ASHR", "SMH", "XLK",
)

# 利率路径:**只作合约月平均有效利率**,不得直接声称"下次会议隐含变动"(§5.3 明令,见
# `zq_front_month_avg_rate` 的 docstring)。
RATE_SYMBOLS: tuple[str, ...] = ("ZQ=F", "SR3=F")

ALL_SYMBOLS: tuple[str, ...] = TAPE_SYMBOLS + RATE_SYMBOLS

STATUS_OK = "ok"
STATUS_EMPTY = "empty"        # 源成功但真空
STATUS_FAILED = "failed"      # 请求/解析失败

TAPE_COLUMNS: tuple[str, ...] = (
    "symbol", "market", "close", "pct_1d", "pct_5d",
    "bar_date", "us_date", "cn_date", "session_complete",
    "fetched_at", "status", "reason", "obs",
)

# `^VIX` 1 年分位的最小观测数。不足 → 分位 None + `样本不足(n)`,**不是 0、不是 50**
# (「UNMEASURED 不是 0」与 §5.2 同一条纪律)。
MIN_PCTILE_OBS = 60
PCTILE_WINDOW = 252           # 1 年 ≈ 252 个交易日

# 取数窗口:1 年日线一次拉齐 —— pct_1d/pct_5d 用尾部,VIX 分位用"同批历史"自算(§任务书)。
_DEFAULT_PERIOD = "1y"

_MARKET_BY_SYMBOL: dict[str, str] = {
    "^GSPC": "us", "^NDX": "us", "^SOX": "us", "^TNX": "us",
    "^VIX": "us", "^VIX3M": "us", "^SKEW": "us", "^MOVE": "us",
    "KWEB": "us", "FXI": "us", "ASHR": "us", "SMH": "us", "XLK": "us",
    "^HSI": "hk",
    "000001.SS": "cn",
    "DX-Y.NYB": "fx", "USDCNH=X": "fx",
    "CL=F": "commodity", "GC=F": "commodity", "HG=F": "commodity",
    "ZQ=F": "commodity", "SR3=F": "commodity",
}

# market → (IANA 时区, 该市场当日"收盘落定"的本地时刻)。session_complete 的唯一判据。
# 期货/外汇按 CME/NY 17:00 结算日切;港股 16:00;A 股 15:00;美股常规时段 16:00。
_MARKET_META: dict[str, tuple[str, time]] = {
    "us": ("America/New_York", time(16, 0)),
    "cn": ("Asia/Shanghai", time(15, 0)),
    "hk": ("Asia/Hong_Kong", time(16, 0)),
    "fx": ("America/New_York", time(17, 0)),
    "commodity": ("America/New_York", time(17, 0)),
}

# macro_state 只写这 ≤8 个数(§5.3)。策略师是否读属 B-1,**本模块不负责接线**。
MACRO_STATE_KEYS: tuple[str, ...] = (
    "vix", "vix_term_ratio", "skew", "move",
    "ust10y", "dxy", "usdcnh", "zq_front_month_avg_rate",
)

# `100 − ZQ=F 价` 的字段名。**改名 = 破坏下游契约**,先读下面这条禁令再动。
ZQ_RATE_FIELD = "zq_front_month_avg_rate"

# §5.3 逐字禁令,渲染层必须原样带出去。
ZQ_RATE_DISCLAIMER = (
    "zq_front_month_avg_rate = 100 − ZQ=F 前月合约价,**只是该合约月的平均有效利率**;"
    "不得声称「下次会议隐含变动」—— 会议概率要另做基于当前 EFFR、会议日前后天数与相邻月"
    "合约的加权求解并独立验算,未实现前本字段不得被如此解读。"
)

VIX_TERM_NOTE = "vix_term_ratio = ^VIX / ^VIX3M;>1 = 期限结构倒挂(描述性地形,不含方向指令)"


class TapeFetchError(RuntimeError):
    """单个标的取数/解析失败 —— 由默认取数器抛,`fetch_global_tape` 捕获成 status=failed。"""


# ───────────────────────── 小工具 ─────────────────────────


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _iso_date(value) -> str | None:
    """任意日期形态 → 'YYYY-MM-DD';不可解析 → None。"""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        s = s[:10] if len(s) >= 10 and s[4] == "-" else s
        if len(s) == 8 and s.isdigit():
            return f"{s[:4]}-{s[4:6]}-{s[6:]}"
        return s
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    ts = pd.Timestamp(value)
    if pd.isna(ts):
        return None
    return ts.date().isoformat()


def market_of(symbol: str) -> str:
    """标的 → 市场桶(决定时区与收盘时刻)。未登记标的按 `us` 处理并在 reason 里留痕。"""
    return _MARKET_BY_SYMBOL.get(symbol, "us")


def session_complete(market: str, bar_date: str | None, *, now: datetime | None = None) -> bool:
    """该市场的 `bar_date` 这根日线是否已是**收盘落定**的完整时段。

    判据(用 IANA 时区现算,禁止写死偏移):bar_date < 当地今天 → True;
    bar_date == 当地今天 且 当地时刻 >= 该市场收盘时刻 → True;其余 False。
    """
    if not bar_date:
        return False
    tz_name, close_at = _MARKET_META.get(market, _MARKET_META["us"])
    tz = ZoneInfo(tz_name)
    local = (now or datetime.now(timezone.utc)).astimezone(tz)
    try:
        bar = date.fromisoformat(bar_date)
    except ValueError:
        return False
    if bar < local.date():
        return True
    if bar > local.date():
        return False
    return local.time() >= close_at


def percentile_rank(series: Sequence[float] | pd.Series, value: float | None,
                    *, min_obs: int = MIN_PCTILE_OBS) -> dict:
    """`value` 在 `series` 里的分位(0–100)。观测不足 → `pctile=None` + `样本不足(n)`。

    返回 {"pctile": float|None, "obs": int, "note": str}。**不足样本不给数**是硬纪律:
    给一个基于 12 个观测的"分位"比不给更坏 —— 它看起来和 250 个观测的分位一模一样。
    """
    s = pd.Series(list(series), dtype="float64").dropna()
    n = int(len(s))
    if value is None or pd.isna(value):
        return {"pctile": None, "obs": n, "note": "无当前值"}
    if n < min_obs:
        return {"pctile": None, "obs": n, "note": f"样本不足({n})"}
    pct = float((s <= float(value)).mean() * 100.0)
    return {"pctile": round(pct, 1), "obs": n, "note": ""}


def _pct_change(closes: pd.Series, lag: int) -> float | None:
    if len(closes) < lag + 1:
        return None
    prev = float(closes.iloc[-1 - lag])
    last = float(closes.iloc[-1])
    if prev == 0 or pd.isna(prev) or pd.isna(last):
        return None
    return round((last / prev - 1.0) * 100.0, 3)


def _normalize_history(raw) -> pd.DataFrame:
    """任意 yfinance 风格历史帧 → 两列 ['bar_date','close'](升序,去 NaN)。"""
    if raw is None:
        return pd.DataFrame(columns=["bar_date", "close"])
    df = pd.DataFrame(raw).copy()
    if df.empty:
        return pd.DataFrame(columns=["bar_date", "close"])
    cols = {str(c).lower(): c for c in df.columns}
    close_col = cols.get("close") or cols.get("adj close") or cols.get("close_price")
    if close_col is None:
        raise TapeFetchError(f"历史帧缺 close 列(拿到 {list(df.columns)[:8]})")
    date_col = cols.get("date") or cols.get("datetime") or cols.get("index")
    if date_col is not None:
        dates = df[date_col]
    else:
        dates = df.index.to_series()
    out = pd.DataFrame({
        "bar_date": [_iso_date(v) for v in dates],
        "close": pd.to_numeric(df[close_col], errors="coerce"),
    })
    out = out.dropna(subset=["bar_date", "close"]).sort_values("bar_date")
    return out.reset_index(drop=True)


def _offline() -> bool:
    return str(os.environ.get("AUTORESEARCH_OFFLINE", "")).strip() in {"1", "true", "TRUE", "yes"}


def _default_history(symbol: str, *, period: str = _DEFAULT_PERIOD) -> pd.DataFrame:
    """默认取数器:yfinance 日线。**形态不对就抛**(不返回空帧假装成功)。

    离线开关 `AUTORESEARCH_OFFLINE=1` → 直接抛,让上层走 B 级降级(D-1 验收行)。
    """
    if _offline():
        raise TapeFetchError("AUTORESEARCH_OFFLINE=1:离线模式不取网络")
    try:
        import yfinance as yf
    except Exception as exc:  # noqa: BLE001 — 依赖缺失同样是"拿不到",归 failed
        raise TapeFetchError(f"yfinance 不可用:{exc!r}") from exc
    try:
        raw = yf.Ticker(symbol).history(period=period, auto_adjust=False)
    except Exception as exc:  # noqa: BLE001
        raise TapeFetchError(f"{symbol} history 失败:{type(exc).__name__}: {exc}") from exc
    return _normalize_history(raw.reset_index() if raw is not None else None)


# ───────────────────────── 主函数 ─────────────────────────


def fetch_global_tape(
    as_of: str | None = None,
    *,
    symbols: Iterable[str] | None = None,
    history: Callable[[str], pd.DataFrame] | None = None,
    now: datetime | None = None,
    record: bool = True,
) -> pd.DataFrame:
    """22 个跨资产标的的隔夜 tape —— **每个请求标的恰一行**,失败也留行。

    参数
      as_of   PIT 日期截断(ISO 或紧凑串);给了就只用 `bar_date <= as_of` 的日线。
              它是**日期截断**,不是市场 —— 各标的按各自交易所日历取该日期之前的最后一根。
      symbols 覆盖标的表(默认 `ALL_SYMBOLS`)。
      history 注入取数器 `f(symbol) -> DataFrame`(测试用;默认走 yfinance)。
      now     注入"此刻"(带 tz),用于 `session_complete` 的确定性测试。
      record  是否 `record_degradation` 记账(默认 True)。

    返回:列 = `TAPE_COLUMNS` 的帧。`df.attrs` 另带:
      `tape_status`  {"ok": n, "empty": n, "failed": n, "requested": n}
      `derived`      `tape_derived()` 的派生块(vix 期限比 / VIX 1 年分位 / ZQ 平均利率)
      `histories`    {symbol: 归一化后的历史帧} —— 分位是"同批历史自算",留给调用方复核
      `fetched_at`   UTC ISO

    **不抛异常**:整体失败 → 22 行全 status=failed(可用部分为空但结构完好)。
    """
    syms = tuple(symbols) if symbols is not None else ALL_SYMBOLS
    fetch = history or _default_history
    fetched_at = _utc_now_iso()
    cutoff = _iso_date(as_of)

    rows: list[dict] = []
    histories: dict[str, pd.DataFrame] = {}
    counts = {STATUS_OK: 0, STATUS_EMPTY: 0, STATUS_FAILED: 0}

    for sym in syms:
        mkt = market_of(sym)
        row = {
            "symbol": sym, "market": mkt, "close": None, "pct_1d": None, "pct_5d": None,
            "bar_date": None, "us_date": None, "cn_date": None, "session_complete": False,
            "fetched_at": fetched_at, "status": STATUS_FAILED, "reason": "", "obs": 0,
        }
        try:
            hist = _normalize_history(fetch(sym))
        except BaseException as exc:  # noqa: BLE001 — 任何取数/解析异常都归 failed,不外泄
            row["reason"] = f"failed: {type(exc).__name__}: {exc}"[:200]
            counts[STATUS_FAILED] += 1
            rows.append(row)
            continue
        if cutoff:
            hist = hist[hist["bar_date"] <= cutoff]
        histories[sym] = hist
        if hist.empty:
            row["status"] = STATUS_EMPTY
            row["reason"] = "empty: 源应答成功但零行(标的退市/改名,或该窗口无交易日)"
            counts[STATUS_EMPTY] += 1
            rows.append(row)
            continue
        closes = hist["close"].reset_index(drop=True)
        bar_date = str(hist["bar_date"].iloc[-1])
        row.update({
            "status": STATUS_OK,
            "close": round(float(closes.iloc[-1]), 4),
            "pct_1d": _pct_change(closes, 1),
            "pct_5d": _pct_change(closes, 5),
            "bar_date": bar_date,
            "us_date": bar_date if mkt == "us" else None,
            "cn_date": bar_date if mkt == "cn" else None,
            "session_complete": session_complete(mkt, bar_date, now=now),
            "obs": int(len(closes)),
        })
        counts[STATUS_OK] += 1
        rows.append(row)

    df = pd.DataFrame(rows, columns=list(TAPE_COLUMNS))
    df.attrs["tape_status"] = {**counts, "requested": len(syms)}
    df.attrs["histories"] = histories
    df.attrs["fetched_at"] = fetched_at
    df.attrs["derived"] = tape_derived(df, histories=histories)
    if record:
        _record_tape_degradations(df, key=str(cutoff or ""))
    return df


def _record_tape_degradations(df: pd.DataFrame, *, key: str = "") -> None:
    """B 级记账:每个非 ok 标的一笔;整体失败额外一笔汇总(降级必须**可见**)。"""
    from autoresearch.data.contracts import record_degradation

    bad = df[df["status"] != STATUS_OK] if len(df) else df
    if len(bad) == 0:
        return
    if len(bad) == len(df):
        record_degradation(
            "global_tape",
            f"整表取数失败/真空({len(bad)}/{len(df)} 标的)→ 隔夜 tape 本次不可用,"
            f"消费端应整块省略而不是沿用旧值",
            key=key)
    for _, r in bad.iterrows():
        record_degradation("global_tape", f"{r['symbol']}: {r['reason'] or r['status']}", key=key)


def _val(df: pd.DataFrame, symbol: str, col: str = "close"):
    if df is None or len(df) == 0 or col not in df.columns:
        return None
    hit = df[(df["symbol"] == symbol) & (df["status"] == STATUS_OK)]
    if len(hit) == 0:
        return None
    v = hit.iloc[0][col]
    return None if v is None or pd.isna(v) else float(v)


def tape_derived(df: pd.DataFrame, *, histories: Mapping[str, pd.DataFrame] | None = None) -> dict:
    """tape 帧 → 派生块(**只有数字与口径注,零方向指令**)。

    - `vix_term_ratio = ^VIX / ^VIX3M`,>1 = 倒挂(描述,见 `VIX_TERM_NOTE`)。
    - `vix_1y_pctile`:用**同批历史**(`histories["^VIX"]` 的近 252 根)自算;不足
      `MIN_PCTILE_OBS` → None + `样本不足(n)`。
    - `zq_front_month_avg_rate = 100 − ZQ=F 价` —— 见 `ZQ_RATE_DISCLAIMER`:**它只是
      合约月的平均有效利率,不得声称「下次会议隐含变动」**。
    """
    hists = dict(histories or (df.attrs.get("histories") if hasattr(df, "attrs") else {}) or {})
    vix = _val(df, "^VIX")
    vix3m = _val(df, "^VIX3M")
    zq = _val(df, "ZQ=F")
    sr3 = _val(df, "SR3=F")

    vix_hist = hists.get("^VIX")
    closes = (vix_hist["close"].tail(PCTILE_WINDOW) if vix_hist is not None
              and not getattr(vix_hist, "empty", True) else pd.Series(dtype="float64"))
    pct = percentile_rank(closes, vix, min_obs=MIN_PCTILE_OBS)

    us_rows = df[(df["market"] == "us") & (df["status"] == STATUS_OK)] if len(df) else df
    us_session_date = None
    if len(us_rows):
        top = us_rows["us_date"].dropna().max()
        us_session_date = str(top) if top is not None and not pd.isna(top) else None

    ratio = None
    if vix is not None and vix3m not in (None, 0):
        ratio = round(vix / vix3m, 4)

    return {
        "us_session_date": us_session_date,
        "fetched_at": df.attrs.get("fetched_at") if hasattr(df, "attrs") else None,
        "vix": vix,
        "vix3m": vix3m,
        "vix_term_ratio": ratio,
        "vix_term_note": VIX_TERM_NOTE,
        "vix_1y_pctile": pct["pctile"],
        "vix_1y_pctile_obs": pct["obs"],
        "vix_1y_pctile_note": pct["note"],
        "skew": _val(df, "^SKEW"),
        "move": _val(df, "^MOVE"),
        "ust10y": _val(df, "^TNX"),
        "dxy": _val(df, "DX-Y.NYB"),
        "usdcnh": _val(df, "USDCNH=X"),
        ZQ_RATE_FIELD: (round(100.0 - zq, 4) if zq is not None else None),
        "zq_front_month_price": zq,
        "zq_disclaimer": ZQ_RATE_DISCLAIMER,
        "sr3_front_price": sr3,
    }


def global_tape_pack(
    as_of: str | None = None,
    *,
    symbols: Iterable[str] | None = None,
    history: Callable[[str], pd.DataFrame] | None = None,
    now: datetime | None = None,
) -> dict:
    """机读 pack(落 `global_tape.json`;macro full / L5 展示消费)。

    {"as_of", "us_session_date", "fetched_at", "status", "rows": [...],
     "derived": {...}, "macro_state": {8 个数}, "degraded": [...]}
    """
    df = fetch_global_tape(as_of, symbols=symbols, history=history, now=now)
    derived = df.attrs["derived"]
    status = df.attrs["tape_status"]
    rows = df.drop(columns=[]).to_dict(orient="records")
    macro_state = {k: derived.get(k) for k in MACRO_STATE_KEYS}
    return {
        "as_of": _iso_date(as_of),
        "us_session_date": derived.get("us_session_date"),
        "fetched_at": df.attrs.get("fetched_at"),
        "status": status,
        "usable": status[STATUS_OK] > 0,
        "rows": rows,
        "derived": derived,
        "macro_state": macro_state,
        "notes": [VIX_TERM_NOTE, ZQ_RATE_DISCLAIMER],
        "degraded": [r for r in rows if r["status"] != STATUS_OK],
    }


def load_global_tape(
    us_date: str | None = None,
    *,
    as_of: str | None = None,
    today: str | None = None,
    history: Callable[[str], pd.DataFrame] | None = None,
    now: datetime | None = None,
) -> pd.DataFrame:
    """走 lake 的入口:`lake/global_tape/<us_date>.parquet`(key=date,B 级契约)。

    分区键 = **美股参考日**,不是 A 股日 —— 两者分列不混(见模块 docstring)。

    - 给了 `us_date`(夜间预热知道自己在采哪个美股日)→ 直接查湖,**命中即零网络**;
    - 没给 → 先探一次 tape 拿 `derived.us_session_date` 作键(这一探本身就是取数,
      随后交给 `get_or_fetch` 落盘)。

    注:本端点的 `sources.fetch` 路由需要按 symbol 取数,与本模块"一次取全表"的形态不同,
    故这里**总是注入 fetch=**;不要指望 `sources.fetch("global_tape", ...)` 能跑通。
    """
    from autoresearch.data import cache

    if us_date:
        key = str(us_date).replace("-", "")

        def _fetch(_ep, _params):
            df = fetch_global_tape(as_of, history=history, now=now)
            return df.copy()

        return cache.get_or_fetch("global_tape", {"trade_date": key}, today=today, fetch=_fetch)

    probe = fetch_global_tape(as_of, history=history, now=now)
    resolved = probe.attrs["derived"].get("us_session_date")
    if not resolved:
        # 没有任何美股行可用 → 无法定分区键。已记账,原样返回(不入湖,重跑还能救)。
        return probe
    out = cache.get_or_fetch(
        "global_tape", {"trade_date": resolved.replace("-", "")}, today=today,
        fetch=lambda _ep, _params: probe.copy(),
    )
    out.attrs.setdefault("derived", probe.attrs["derived"])
    out.attrs.setdefault("tape_status", probe.attrs["tape_status"])
    return out
