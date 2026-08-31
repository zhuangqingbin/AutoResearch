"""harvest full 档:外源扩面 D-3 块(期权 v2 / EDGAR / 分析师行动 / gnews / readthrough)。

design: docs/superpowers/plans/2026-08-31-stock-research-p0-p1.md task-10(D1.1 拆分)。

**纯搬家**(task-10 红线):本文件内容逐字节照搬自 `autoresearch/analyze/harvest.py`
拆分前的「外源扩面 D-3」整段(原设计稿 2026-08-28 §5.2 / §7.2 / §7.3 / §8),函数体
未改一字节,只有 import 行按新文件位置调整。`_is_ashare`/`_REALTIME_DISCLAIMER`
**不回引 harvest.py**(见 `blocks_statements.py` 模块 docstring 的 DAG 说明——
harvest.py 严格在顶端,`python -m` 直跑时任何回引都会触发"模块执行两遍"的
ImportError),分别回引它们的新家:`blocks_statements.py`/`blocks_us.py`。

取代 v1 `options_iv_summary` 的三处病:只取"最近一个到期" / 跨式用 `lastPrice` / PCR 被直接
渲染成"偏防御"。数值口径的唯一事实源是 `data/sources/yf_options.py`;本文件只负责**渲染**
与**消费纪律**:

  1. 隐含波动 = 预期**量级**,不是方向 —— 必须与「财报历史实际波动」对照写一句
     「市场定价 ±x% vs 过去 8 次中位 ±y%」。只给一半 = 给了一个无法解读的数。
  2. `UNMEASURED` 原样展示 + 原因,**永远不显示 0**(0 会被读成"波动为零")。
  3. 偏度 / PCR 只描述**仓位结构**;**不得由期权推出评级**(§5.1 阶段裁定)。
  4. 财报历史实际波动按时段对齐:AMC 用 D close→D+1 open/close;BMO 用 D−1 close→D
     open/close;**时段未知不纳入**(猜 AMC/BMO = 量错对象)。

slim / LITE 档整档关闭 —— 两道门:`external_sections(..., slim=True)` 返回空 + main() 里
原有的 `if not slim:`(§5.2「slim 关」;微观 lite 属 B 类,受判断层冻结)。
"""
import sys
from datetime import datetime, timedelta

import pandas as pd
import yfinance as yf

from autoresearch.analyze.blocks_statements import _is_ashare
from autoresearch.analyze.blocks_us import _REALTIME_DISCLAIMER
from autoresearch.dataflows.symbol_utils import normalize_symbol

_TITLE_OPTIONS = "期权与仓位地形 (options v2)"
_TITLE_EDGAR = "外源事件账 · EDGAR 近90日 (T1官方)"
_TITLE_ANALYST_ACTIONS = "外源事件账 · 分析师行动 近30日"
_TITLE_GNEWS_EN = "外源事件账 · Google News en 14日候选 (T4发现源·UNVERIFIED)"
_TITLE_GNEWS_ZH = "外源事件账 · Google News zh 14日候选 (T4发现源·UNVERIFIED)"
_TITLE_READTHROUGH = "海外映射 (readthrough·≤4名·事实行)"

#: v1 的「无挂牌期权」降级文案 —— **原样保留**(下游 playbook / 分析师按这句话认降级)。
NO_OPTIONS_DEGRADE = (
    "_该标的在 yfinance 无挂牌期权（A股/港股等常见）→ 期权/IV/定位信号不可用，"
    "催化剂&定位分析师需注明降级。_"
)

_EARNINGS_HIST_N = 8            # §5.2:财报历史实际波动对照取最近 8 次
_GNEWS_DAYS = 14
_GNEWS_MAX_ROWS = 12
_EDGAR_DAYS = 90
_EDGAR_MAX_ROWS = 15
_ANALYST_ACTION_DAYS = 30

_RT_MAX = 4                     # §8:单层 ≤4 项(load_map 已截,这里再截一次 = 纵深防御)
_RT_KINDS = ("company", "etf", "index")
_RT_RELATIONS = ("customer", "supplier", "peer", "theme")
_RT_DIRECTIONS = ("downstream", "upstream", "peer")
_RT_UNTRADABLE = ("delisted", "suspended", "halted", "unlisted", "expired")

_T4_NOTE = (
    "> **T4 发现源 · UNVERIFIED**(§3.1):聚合标题 / 摘要**不可单独支持任何断言**,"
    "只是**候选,须跟到原文** —— WebFetch canonical 原文、按 T1/T2/T3 重新标级后才可引用。"
)


def _degrade_note(endpoint: str, reason: str, key: str = "") -> None:
    """B 级降级记账(懒导入;记账本身失败也不许影响 harvest)。"""
    try:
        from autoresearch.data.contracts import record_degradation
        record_degradation(endpoint, reason, key=key)
    except Exception:  # noqa: BLE001
        print(f"[harvest] {endpoint}:{reason}(记账失败)", file=sys.stderr)


def _num(v, nd: int = 4):
    """标量 → round 后的 float;None / NaN / 不可解析 → None(**不是 0**)。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(f) else round(f, nd)


def _cell(v, nd: int = 2, suffix: str = "") -> str:
    n = _num(v, nd)
    return "—" if n is None else f"{n:.{nd}f}{suffix}"


def _signed(v, nd: int = 2, suffix: str = "%") -> str:
    n = _num(v, nd)
    return "—" if n is None else f"{n:+.{nd}f}{suffix}"


def _abs_pct(v, nd: int = 2) -> str:
    n = _num(v, nd)
    return "—" if n is None else f"±{abs(n):.{nd}f}%"


def _median(values: list[float]) -> float | None:
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    mid = len(vals) // 2
    return vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2


# ───────────────────── 财报时点:时段(AMC/BMO)与历史实际波动 ─────────────────────


def _et_session(ts) -> str | None:
    """财报时刻 → `AMC` / `BMO`;**不猜**:00:00 占位与盘中时刻一律 None(§2 事件契约)。

    判据是交易所本地时刻:≥16:00 = 盘后(AMC),<09:30 = 盘前(BMO)。yfinance 在只知道
    日期时给 00:00 占位 —— 那是 `DATE_ONLY`,拿它当 BMO 就是**量错对象**(锚会整体错一天)。
    """
    try:
        t = pd.Timestamp(ts)
    except Exception:  # noqa: BLE001
        return None
    if getattr(t, "tzinfo", None) is not None:
        try:
            t = t.tz_convert("America/New_York")
        except Exception:  # noqa: BLE001
            return None
    try:
        h, m = int(t.hour), int(t.minute)
    except Exception:  # noqa: BLE001
        return None
    if (h, m) == (0, 0):
        return None
    if h >= 16:
        return "AMC"
    if (h, m) < (9, 30):
        return "BMO"
    return None


def earnings_events(symbol: str, curr_date: str, *, frame=None) -> list[dict]:
    """`earnings_dates` → `[{date, session, time_quality, ts}]`(日期降序);取数失败 → []。

    `session` 为 None 时 `time_quality = DATE_ONLY` —— 下游据此把它排除在锚对齐之外。
    """
    df = frame
    if df is None:
        try:
            df = yf.Ticker(normalize_symbol(symbol)).earnings_dates
        except Exception as e:  # noqa: BLE001
            _degrade_note("us_earnings_dates", f"取数失败({type(e).__name__}: {e})", key=symbol)
            df = None
    if df is None or not len(df):
        return []
    out: list[dict] = []
    for ts in list(getattr(df, "index", [])):
        try:
            d = pd.Timestamp(ts).strftime("%Y-%m-%d")
        except Exception:  # noqa: BLE001
            continue
        sess = _et_session(ts)
        out.append({"date": d, "session": sess,
                    "time_quality": "TIMED" if sess else "DATE_ONLY", "ts": str(ts)})
    out.sort(key=lambda r: r["date"], reverse=True)
    return out


def next_earnings_spec(symbol: str, curr_date: str, *, events=None) -> dict:
    """下次财报 `{date, session, time_quality}`(严格晚于分析日);无 → date=None。"""
    evs = events if events is not None else earnings_events(symbol, curr_date)
    future = sorted((e for e in evs if str(e.get("date") or "") > curr_date),
                    key=lambda e: e["date"])
    if not future:
        return {"date": None, "session": None, "time_quality": "UNKNOWN"}
    e = future[0]
    return {"date": e["date"], "session": e.get("session"),
            "time_quality": e.get("time_quality") or "DATE_ONLY"}


def _bar_days(bars) -> tuple[list[str], dict]:
    """日线帧 → (升序日期表, {日期: (open, close)});空 / 缺列 → ([], {})。"""
    if bars is None or not len(bars):
        return [], {}
    cols = {str(c).lower(): c for c in list(getattr(bars, "columns", []))}
    o_col, c_col = cols.get("open"), cols.get("close")
    if o_col is None or c_col is None:
        return [], {}
    by_day: dict[str, tuple] = {}
    for idx, row in bars.iterrows():
        try:
            d = pd.Timestamp(idx).strftime("%Y-%m-%d")
        except Exception:  # noqa: BLE001
            continue
        by_day[d] = (_num(row[o_col], 6), _num(row[c_col], 6))
    return sorted(by_day), by_day


def earnings_realized_moves(events, bars, *, limit: int = _EARNINGS_HIST_N) -> dict:
    """按**时段对齐**的财报历史实际波动(§5.2 字段表逐行)。

        AMC(D 盘后披露): D close → D+1 open / D+1 close
        BMO(D 盘前披露): D−1 close → D open / D close
        时段未知        : **不纳入**(不猜 AMC/BMO —— 猜错 = 整条对照句量错对象)

    返回 `{"rows", "n", "median_abs_cc_pct", "median_abs_co_pct", "skipped_unknown_session",
    "skipped_no_bars", "status", "reason"}`;一条都对不齐 → `status="UNMEASURED"`(**不是 0**)。
    """
    days, by_day = _bar_days(bars)
    pos = {d: i for i, d in enumerate(days)}
    rows: list[dict] = []
    skipped_unknown = skipped_bars = 0
    for ev in sorted(events or [], key=lambda e: str(e.get("date") or ""), reverse=True):
        if len(rows) >= int(limit):
            break
        d = str(ev.get("date") or "")
        sess = ev.get("session")
        if sess not in ("AMC", "BMO"):
            skipped_unknown += 1
            continue
        i = pos.get(d)
        j = (i + 1) if (sess == "AMC" and i is not None) else i
        base_i = i if sess == "AMC" else (i - 1 if i is not None else None)
        if i is None or j is None or base_i is None or base_i < 0 or j >= len(days):
            skipped_bars += 1
            continue
        base = by_day[days[base_i]][1]
        o, c = by_day[days[j]]
        if not base or o is None or c is None:
            skipped_bars += 1
            continue
        rows.append({
            "date": d,
            "session": sess,
            "anchor": ("D close→D+1 open/close" if sess == "AMC"
                       else "D−1 close→D open/close"),
            "base_date": days[base_i],
            "move_date": days[j],
            "close_to_open_pct": _num((o / base - 1.0) * 100.0, 3),
            "close_to_close_pct": _num((c / base - 1.0) * 100.0, 3),
        })
    cc = [abs(r["close_to_close_pct"]) for r in rows if r["close_to_close_pct"] is not None]
    co = [abs(r["close_to_open_pct"]) for r in rows if r["close_to_open_pct"] is not None]
    out = {
        "rows": rows, "n": len(rows),
        "median_abs_cc_pct": _num(_median(cc), 3),
        "median_abs_co_pct": _num(_median(co), 3),
        "skipped_unknown_session": skipped_unknown,
        "skipped_no_bars": skipped_bars,
        "status": "ok" if rows else "UNMEASURED",
        "reason": "",
    }
    if not rows:
        out["reason"] = (f"无可对齐的历史财报(时段未知 {skipped_unknown} 次 / "
                         f"缺日线 {skipped_bars} 次)——**UNMEASURED 不是 0**")
    return out


# ───────────────────────── 期权与仓位地形(options v2)─────────────────────────


def _iv_pct(v) -> str:
    """yfinance 的 impliedVolatility 已被源模块折算成年化 %(`iv_unit`)。"""
    n = _num(v, 2)
    return "—" if n is None else f"{n:.2f}%"


def _um(block, field: str, fmt=_iv_pct) -> str:
    """`ok` → 数字;否则 `UNMEASURED(原因)`。**任何情况下都不写 0**(纪律 1)。"""
    if not isinstance(block, dict):
        return "UNMEASURED(无数据块)"
    if str(block.get("status")) != "ok":
        return f"UNMEASURED({block.get('reason') or block.get('note') or '未给原因'})"
    return fmt(block.get(field))


def options_block(symbol: str, curr_date: str, *, chain=None, earn=None,
                  realized=None, iv_history=None) -> str:
    """报告块「期权与仓位地形」(美股 full;A 股/港股 → 原降级文案)。

    数值全部来自 `data/sources/yf_options.full_chain`(口径 = `METHOD_VERSION`);本函数只渲染。

    铁律(测试逐字锁):隐含波动 = **预期量级**,不是方向 → 必须与财报历史实际波动对照写出
    「市场定价 ±x% vs 过去 N 次中位 ±y%」;`UNMEASURED` 原样展示带原因、**不显示 0**;
    偏度 / PCR 只描述仓位结构,**不得由期权推出评级**。

    注入点(测试用,**禁真网络**):`chain` / `earn` / `realized` / `iv_history`。
    """
    sym = normalize_symbol(symbol)
    if earn is None:
        earn = next_earnings_spec(sym, curr_date)
    if chain is None:
        try:
            from autoresearch.data.sources import yf_options as _yfo
            chain = _yfo.full_chain(sym, curr_date,
                                    earnings_dt=(earn if earn.get("date") else None))
        except Exception as e:  # noqa: BLE001 — B 级:取数炸了也不许阻断 harvest
            _degrade_note("us_options", f"full_chain 失败({type(e).__name__}: {e})", key=sym)
            return f"_期权取数失败(B 级降级,不阻断):{type(e).__name__}: {e}_"
    if not isinstance(chain, dict):
        return f"_期权数据块形态异常({type(chain).__name__})→ 降级注明。_"

    reason = str(chain.get("reason") or "")
    if "无挂牌期权" in reason or "无挂牌期权" in " ".join(map(str, chain.get("degraded") or [])):
        return NO_OPTIONS_DEGRADE
    if chain.get("spot") is None and str(chain.get("status")) != "ok":
        return f"_期权块降级(B 级,不阻断):{reason or '未给原因'}_"

    if realized is None:
        realized = earnings_realized_moves(
            [e for e in earnings_events(sym, curr_date) if str(e.get("date") or "") <= curr_date],
            _earnings_bars(sym, curr_date))

    interp = chain.get("interpolation") or {}
    ts_block = chain.get("term_structure") or {}
    eim = chain.get("earnings_implied_move") or {}
    volp = chain.get("vol_premium") or {}
    skew = chain.get("skew_proxy") or {}
    pcr = chain.get("pcr") or {}

    out: list[str] = []
    out.append(
        f"**口径**:method_version=`{chain.get('method_version')}` ｜ spot "
        f"{_cell(chain.get('spot'))}(as-of {chain.get('spot_as_of') or '—'})｜ "
        f"chain_fetched_at {chain.get('chain_fetched_at') or '—'} ｜ market_state "
        f"{chain.get('market_state') or '—'} ｜ session_complete "
        f"{bool(chain.get('session_complete'))} ｜ as_of {chain.get('as_of')}")
    out.append(
        "> **消费纪律**:隐含波动 = 市场对**波动量级**的定价,**不是方向**;偏度 / PCR 只描述"
        "**仓位结构**;**不得由期权推出评级**(评级只由 rubric 三门决定)。`UNMEASURED` 是"
        "「没测到」,**不等于 0**。")

    out.append("\n### 30D ATM IV(一致口径,可跨日比较)")
    out.append(f"- 30D ATM IV: {_um(interp, 'atm_iv_30d')} ｜ 插值 "
               f"{interp.get('method') or '—'} ｜ 腿 {'/'.join(interp.get('legs') or []) or '—'}")
    pct = _iv_percentile_row(chain, iv_history)
    out.append(f"- 1 年分位(同 method_version 同 tenor 才比): {pct}")

    out.append("\n### 期限结构(最近有效到期)")
    pts = list(ts_block.get("points") or [])
    if pts:
        out.append("| 到期 | DTE | ATM IV(call/put 中值) |")
        out.append("|---|---:|---:|")
        for p in pts:
            out.append(f"| {p.get('expiry')} | {p.get('dte')} | {_iv_pct(p.get('atm_iv'))} |")
        out.append(f"- slope = IV_near − IV_far: "
                   f"{_signed(ts_block.get('slope'), 2, 'pp') if ts_block.get('slope') is not None else 'UNMEASURED(' + str(ts_block.get('reason') or '') + ')'}"
                   f" ｜ {ts_block.get('slope_def') or ''}")
    else:
        out.append(f"- UNMEASURED({ts_block.get('reason') or '无有效到期'})")

    out.append("\n### 财报隐含波动 vs 历史实际波动(**必读对照**)")
    out.append(f"- 下次财报: {earn.get('date') or '未确认'}"
               f"({earn.get('session') or '时段未知'} ｜ time_quality="
               f"{earn.get('time_quality') or 'UNKNOWN'})")
    implied = (f"{_abs_pct(eim.get('implied_move_pct'))}" if str(eim.get("status")) == "ok"
               else f"UNMEASURED({eim.get('reason') or '未给原因'})")
    hist_n = int((realized or {}).get("n") or 0)
    hist = (_abs_pct((realized or {}).get("median_abs_cc_pct"))
            if hist_n else f"UNMEASURED({(realized or {}).get('reason') or '无可对齐样本'})")
    out.append(f"- **市场定价 {implied} vs 过去 {hist_n} 次中位 {hist}**"
               f"(实际口径 = close→close;close→open 中位 "
               f"{_abs_pct((realized or {}).get('median_abs_co_pct'))})")
    out.append("- 口径:隐含 = ATM **有效 bid-ask midpoint** 跨式 / spot(**禁用 lastPrice**;"
               "任一腿 crossed / stale / 缺报价 → UNMEASURED);实际 = AMC 用 D close→D+1 "
               "open/close、BMO 用 D−1 close→D open/close,**时段未知不纳入**。")
    rows = list((realized or {}).get("rows") or [])
    if rows:
        out.append("")
        out.append("| 财报日 | 时段 | 锚 | close→open % | close→close % |")
        out.append("|---|---|---|---:|---:|")
        for r in rows:
            out.append(f"| {r['date']} | {r['session']} | {r['anchor']} | "
                       f"{_signed(r['close_to_open_pct'])} | {_signed(r['close_to_close_pct'])} |")
    out.append(f"- 未纳入:时段未知 {(realized or {}).get('skipped_unknown_session', 0)} 次 ｜ "
               f"缺日线 {(realized or {}).get('skipped_no_bars', 0)} 次")

    out.append("\n### 波动率溢价")
    out.append(f"- {volp.get('def') or 'vol_premium = ATM IV(30D) − 20 日已实现年化波动'}: "
               f"{_um(volp, 'premium_pp', lambda v: _signed(v, 2, 'pp'))} "
               f"(20 日已实现 {_iv_pct(volp.get('realized_vol_20d'))})")

    out.append(f"\n### 偏度代理({(skew.get('label') or '10% 价外偏度代理')})")
    out.append(f"- IV(put K≈0.9·S) − IV(call K≈1.1·S) = "
               f"{_um(skew, 'skew_pp', lambda v: _signed(v, 2, 'pp'))} ｜ 参考到期 "
               f"{skew.get('expiry') or '—'}")
    out.append(f"- {skew.get('note') or '无 Greeks → 不是 25Δ 偏度;只描述**仓位结构**,不含方向'}")

    out.append("\n### PCR(未平仓 OI)")
    by_exp = list(pcr.get("by_expiry") or [])
    if by_exp:
        out.append("| 到期 | DTE | call OI | put OI | PCR |")
        out.append("|---|---:|---:|---:|---:|")
        for p in by_exp:
            out.append(f"| {p.get('expiry')} | {p.get('dte')} | {_cell(p.get('call_oi'), 0)} | "
                       f"{_cell(p.get('put_oi'), 0)} | {_cell(p.get('pcr_oi'))} |")
    total = (pcr.get("total") or {}).get("pcr_oi")
    out.append(f"- 合计 PCR(OI): {_cell(total) if total is not None else 'UNMEASURED(' + str(pcr.get('reason') or '') + ')'}")
    out.append(f"- {pcr.get('semantics') or 'PCR 只描述**对冲 / 持仓压力**的量级;不含方向,不跨品种加总'}")

    out.append("\n### 明确不做(§5.2 末行)")
    out.append("- " + " / ".join(chain.get("not_computed") or
                                 ["max_pain", "oi_magnet", "unusual_activity"])
               + " —— 普查裁定的噪声源,不要加回来。")
    degraded = list(chain.get("degraded") or [])
    if degraded:
        out.append(f"\n> ⚠️ B 级降级留痕({len(degraded)} 条):" + " ｜ ".join(map(str, degraded[:4])))
    return "\n".join(out)


def _iv_percentile_row(chain: dict, iv_history) -> str:
    """30D ATM IV 的 1 年分位 —— 观测为空(湖里还没历史)→ 「样本不足(0)」,不编。"""
    try:
        from autoresearch.data.sources.yf_options import iv_percentile
    except Exception:  # noqa: BLE001
        return "UNMEASURED(分位口径模块不可用)"
    obs = list(iv_history or [])
    try:
        r = iv_percentile(obs, chain.get("atm_iv_30d"),
                          method_version=str(chain.get("method_version") or ""))
    except Exception as e:  # noqa: BLE001
        return f"UNMEASURED({type(e).__name__}: {e})"
    if str(r.get("status")) != "ok":
        drop = ", ".join(f"{k}={v}" for k, v in (r.get("dropped") or {}).items() if v)
        return (f"UNMEASURED({r.get('note') or '样本不足'}"
                + (f";已丢弃 {drop}" if drop else "")
                + " —— 口径不同的观测不进同一条历史)")
    return f"{r.get('pctile')}%(n={r.get('obs')})"


def _earnings_bars(symbol: str, curr_date: str, *, lookback_days: int = 900):
    """财报锚要用的日线(含 D+1,故右端取分析日次日);取数失败 → None(B 级)。"""
    try:
        d = datetime.strptime(curr_date, "%Y-%m-%d")
        d_end = (d + timedelta(days=2)).strftime("%Y-%m-%d")
        d_start = (d - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
        return yf.Ticker(normalize_symbol(symbol)).history(start=d_start, end=d_end)
    except Exception as e:  # noqa: BLE001
        _degrade_note("us_options", f"财报历史日线取数失败({type(e).__name__}: {e})", key=symbol)
        return None


# ───────────────────────── 美股确定层:EDGAR / 分析师行动 / gnews ─────────────────────────


def edgar_block(ticker: str, curr_date: str, *, outcome=None, days: int = _EDGAR_DAYS) -> str | None:
    """EDGAR 近 90 日申报(T1 官方)。presence-gated:非美股(NO_CIK)→ 整块省略(返回 None)。

    「源成功但真空」与「请求 / 解析失败」分开渲染 —— 二者都落空表就分不清是"没有申报"还是
    "我们坏了"(§9 逐字)。任何情况都不抛。
    """
    res = outcome
    if res is None:
        try:
            from autoresearch.data.sources.edgar import fetch_submissions_safe
            res = fetch_submissions_safe(ticker, days=days,
                                         as_of=datetime.strptime(curr_date, "%Y-%m-%d").date())
        except Exception as e:  # noqa: BLE001
            _degrade_note("edgar", f"调用失败({type(e).__name__}: {e})", key=ticker)
            return f"_EDGAR 取数失败(B 级降级,不阻断):{type(e).__name__}: {e}_"
    status = str(getattr(res, "status", "") or "")
    df = getattr(res, "rows", None)       # `FetchOutcome.rows`(不是 frame/df)
    if status == "NO_CIK":
        return None                       # 非美股本来就没有 EDGAR → 不写空节
    if status == "FAILED":
        return (f"_EDGAR 取数失败(B 级降级,不阻断):{getattr(res, 'reason', '') or '未给原因'}_")
    if status == "EMPTY" or df is None or not len(df):
        return (f"_源成功、近 {days} 日 **0 条申报**(合法空,不是取数失败)。_")
    out = [f"近 {days} 日 filings(T1 原始 / 官方;可支持 material claim,仍需时点与字段匹配):",
           "", "| form | 申报日 | 报告期 | 受理时刻 | 链接 |", "|---|---|---|---|---|"]
    for _, r in df.head(_EDGAR_MAX_ROWS).iterrows():
        out.append(f"| {r.get('form')} | {r.get('filing_date')} | {r.get('report_date') or '—'} | "
                   f"{r.get('acceptance_datetime') or '—'} | {r.get('url') or '—'} |")
    if len(df) > _EDGAR_MAX_ROWS:
        out.append(f"\n> 另有 {len(df) - _EDGAR_MAX_ROWS} 条未列(按申报日降序截断)。")
    return "\n".join(out)


def analyst_actions_block(ticker: str, curr_date: str, *, actions=None, targets=None,
                          prev_targets=None, days: int = _ANALYST_ACTION_DAYS) -> str | None:
    """分析师行动近 30 日:评级变动窄表 + 目标价区间(变化需历史快照,缺 → UNMEASURED)。

    presence-gated:既无评级动作又无目标价字段(非美标的常见)→ 整块省略。
    """
    sym = normalize_symbol(ticker)
    if actions is None:
        try:
            actions = yf.Ticker(sym).upgrades_downgrades
        except Exception as e:  # noqa: BLE001
            _degrade_note("us_ticker", f"upgrades_downgrades 失败({type(e).__name__}: {e})", key=sym)
            actions = None
    if targets is None:
        try:
            info = yf.Ticker(sym).info or {}
        except Exception as e:  # noqa: BLE001
            _degrade_note("us_ticker", f"info 失败({type(e).__name__}: {e})", key=sym)
            info = {}
        targets = {k: info.get(k) for k in
                   ("targetLowPrice", "targetMeanPrice", "targetMedianPrice",
                    "targetHighPrice", "numberOfAnalystOpinions")}
    rows = _recent_actions(actions, curr_date, days)
    has_targets = any(v is not None for v in (targets or {}).values())
    if not rows and not has_targets:
        return None
    out: list[str] = []
    if rows:
        out += [f"评级变动(近 {days} 日,as-of ≤ {curr_date};窄表 = 只留窗内动作):", "",
                "| 日期 | 机构 | 动作 | From → To |", "|---|---|---|---|"]
        out += [f"| {r['date']} | {r['firm']} | {r['action']} | {r['from']} → {r['to']} |"
                for r in rows]
    else:
        out.append(f"_近 {days} 日**无评级变动**(源成功,窗内真空)。_")
    lo, hi = (targets or {}).get("targetLowPrice"), (targets or {}).get("targetHighPrice")
    out.append("")
    out.append(f"目标价区间: {_cell(lo)} – {_cell(hi)} ｜ 均值 "
               f"{_cell((targets or {}).get('targetMeanPrice'))} ｜ 中位 "
               f"{_cell((targets or {}).get('targetMedianPrice'))} ｜ 覆盖分析师 "
               f"{(targets or {}).get('numberOfAnalystOpinions') or '—'}")
    if prev_targets:
        out.append(f"区间变化 vs 上一快照({prev_targets.get('as_of') or '—'}): 低 "
                   f"{_signed(_delta(lo, prev_targets.get('targetLowPrice')), 2, '')} ｜ 高 "
                   f"{_signed(_delta(hi, prev_targets.get('targetHighPrice')), 2, '')} ｜ 均值 "
                   f"{_signed(_delta((targets or {}).get('targetMeanPrice'), prev_targets.get('targetMeanPrice')), 2, '')}")
    else:
        out.append("区间变化: **UNMEASURED**(无上一快照可比 —— 变化要历史快照才算得出;"
                   "**UNMEASURED 不是 0、不是「没变」**)")
    out.append("> 消费纪律:目标价是**卖方口径的预期**,不是事实;动作背后的论点要靠 "
               "`us-intel` 第②面追原文。")
    return _REALTIME_DISCLAIMER + "\n\n" + "\n".join(out)


def _delta(now_v, prev_v):
    a, b = _num(now_v), _num(prev_v)
    return None if (a is None or b is None) else round(a - b, 4)


def _recent_actions(actions, curr_date: str, days: int) -> list[dict]:
    """`upgrades_downgrades` → 窗内行(as-of ≤ 分析日,禁前视);形态不对 → []。"""
    if actions is None or not len(actions):
        return []
    try:
        start = (datetime.strptime(curr_date, "%Y-%m-%d") - timedelta(days=int(days))).date()
        end = datetime.strptime(curr_date, "%Y-%m-%d").date()
    except Exception:  # noqa: BLE001
        return []
    recs = []
    it = actions.iterrows() if hasattr(actions, "iterrows") else enumerate(actions)
    for idx, row in it:
        raw = row.get("GradeDate") if hasattr(row, "get") else None
        try:
            d = pd.Timestamp(raw if raw is not None else idx).date()
        except Exception:  # noqa: BLE001
            continue
        if not (start <= d <= end):
            continue
        get = row.get if hasattr(row, "get") else (lambda k, _r=row: None)
        recs.append({"date": d.isoformat(), "firm": str(get("Firm") or "—"),
                     "action": str(get("Action") or "—"),
                     "from": str(get("FromGrade") or "—"), "to": str(get("ToGrade") or "—")})
    recs.sort(key=lambda r: r["date"], reverse=True)
    return recs


def gnews_block(query: str, curr_date: str, *, lang: str = "en", items=None,
                days: int = _GNEWS_DAYS) -> str | None:
    """Google News RSS 近 14 日**候选**(T4 发现源)。presence-gated:零条 → 整块省略。

    §3.1 / §12 Q6:聚合标题只能证明「搜到过」,**不能**令 `content_supports=true`;每条都标
    `T4 / UNVERIFIED`,必须跟到 canonical 原文才可作证据。取数失败由源模块 B 级记账,不抛。
    """
    rows = items
    if rows is None:
        try:
            from autoresearch.data.sources.gnews_rss import search
            rows = search(str(query), lang=lang, limit=60)
        except Exception as e:  # noqa: BLE001
            _degrade_note("gnews_rss", f"发现源调用失败({type(e).__name__}: {e})", key=str(query))
            return None
    rows = _news_window(rows or [], curr_date, days)
    if not rows:
        return None
    out = [_T4_NOTE, "",
           f"查询 `{query}`(lang={lang})｜ 窗 {days} 日 ｜ as-of ≤ {curr_date} ｜ 共 {len(rows)} 条候选",
           "", "| 发布时间 | 标题 | 来源站 | canonical 待核 URL |", "|---|---|---|---|"]
    for r in rows[:_GNEWS_MAX_ROWS]:
        title = str(r.get("title") or "").replace("|", "｜")
        out.append(f"| {str(r.get('published_ts') or '日期不明')} | {title} | "
                   f"{r.get('source_name') or '—'} | {r.get('url') or '—'} |")
    if len(rows) > _GNEWS_MAX_ROWS:
        out.append(f"\n> 另有 {len(rows) - _GNEWS_MAX_ROWS} 条候选未列(按发布时间降序截断)。")
    out.append("\n> 每条状态恒为 `T4 / UNVERIFIED` —— **候选,须跟到原文**;未追到 canonical "
               "原文之前不得进入正文判断(只能进证据附录 / 待核)。")
    return "\n".join(out)


def _news_window(rows, curr_date: str, days: int) -> list[dict]:
    """窗内过滤 + 降序;`published_ts` 缺失的条目保留(标「日期不明」)但排末尾。"""
    try:
        end = datetime.strptime(curr_date, "%Y-%m-%d").date()
        start = end - timedelta(days=int(days))
    except Exception:  # noqa: BLE001
        return list(rows)
    dated, undated = [], []
    for r in rows:
        raw = r.get("published_ts")
        if not raw:
            undated.append(r)
            continue
        try:
            d = pd.Timestamp(str(raw)).tz_localize(None).date()
        except Exception:  # noqa: BLE001
            try:
                d = pd.Timestamp(str(raw)).date()
            except Exception:  # noqa: BLE001
                undated.append(r)
                continue
        if start <= d <= end:                       # 晚于分析日 = 前视,丢弃
            dated.append((d, r))
    dated.sort(key=lambda t: t[0], reverse=True)
    return [r for _, r in dated] + undated


# ───────────────────────── A 股:海外映射(readthrough)─────────────────────────


def _rt_date_ok(v) -> str | None:
    s = str(v or "").strip()[:10]
    return s if len(s) == 10 and s[4] == "-" and s[7] == "-" else None


def _rt_valid(item, as_of: str) -> bool:
    """消费者侧独立准入(**不信任 load_map 已经筛过**):枚举 / 证据 URL / 有效期 / 可交易。"""
    if not isinstance(item, dict) or not str(item.get("symbol") or "").strip():
        return False
    if str(item.get("kind") or "") not in _RT_KINDS:
        return False
    if str(item.get("relation") or "") not in _RT_RELATIONS:
        return False
    if str(item.get("direction") or "") not in _RT_DIRECTIONS:
        return False
    url = str(item.get("evidence_url") or "").strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        return False
    if item.get("tradable") is False:
        return False
    if str(item.get("status") or "").strip().lower() in _RT_UNTRADABLE:
        return False
    eff_from, eff_to, today = (_rt_date_ok(item.get("effective_from")),
                               _rt_date_ok(item.get("effective_to")), _rt_date_ok(as_of))
    if eff_from is None:
        return False
    if today is None:
        return True
    if eff_from > today:
        return False
    return not (eff_to is not None and eff_to < today)


def _readthrough_items(ticker: str, as_of: str, industry: str | None = None) -> list[dict]:
    """人工映射表 → 本票名单(**个股映射优先于行业**,§8);模块未接线 / 无映射 → []。"""
    try:
        from autoresearch.data.readthrough import load_map
    except Exception:  # noqa: BLE001 — D-1 source soak 前该模块可以整个不存在
        return []
    try:
        m = load_map(as_of) or {}
    except Exception as e:  # noqa: BLE001
        _degrade_note("readthrough_map", f"load_map 失败({type(e).__name__}: {e})", key=str(as_of))
        return []
    if not isinstance(m, dict):
        return []
    sym = normalize_symbol(ticker)
    codes = m.get("codes") if isinstance(m.get("codes"), dict) else {}
    for key in (sym.split(".")[0], sym):
        got = codes.get(key) if isinstance(codes, dict) else None
        if isinstance(got, (list, tuple)) and got:
            return list(got)
    inds = m.get("industries") if isinstance(m.get("industries"), dict) else {}
    got = inds.get(str(industry)) if (industry and isinstance(inds, dict)) else None
    return list(got) if isinstance(got, (list, tuple)) else []


def _readthrough_tape(symbols: list[str], as_of: str) -> tuple[dict, str | None]:
    """映射名的隔夜 tape → {SYMBOL: 行};失败 / 空 → ({}, stale_reason) 且记账,不抛。"""
    if not symbols:
        return {}, None
    try:
        from autoresearch.data.sources.yf_tape import fetch_global_tape
    except Exception as e:  # noqa: BLE001
        reason = f"tape 源未接线({type(e).__name__})"
        _degrade_note("global_tape", reason, key=str(as_of))
        return {}, reason
    try:
        df = fetch_global_tape(as_of=as_of, symbols=list(symbols))
    except Exception as e:  # noqa: BLE001
        reason = f"tape 取数失败({type(e).__name__}: {e})"
        _degrade_note("global_tape", reason, key=str(as_of))
        return {}, reason
    if df is None or not len(df) or "symbol" not in list(getattr(df, "columns", [])):
        reason = "tape 空返回(无 symbol 行)"
        _degrade_note("global_tape", reason, key=str(as_of))
        return {}, reason
    rows: dict[str, dict] = {}
    for _, r in df.iterrows():
        s = str(r.get("symbol") or "").strip().upper()
        if s:
            rows.setdefault(s, dict(r))
    return rows, None


def _readthrough_iv(symbol: str, as_of: str) -> dict:
    """映射名的**窄快照**(30D ATM IV;§9:映射名单走窄快照,不为一个量级把全链抓爆)。"""
    try:
        from autoresearch.data.sources.yf_options import narrow_snapshot
        return narrow_snapshot(symbol, as_of) or {}
    except Exception as e:  # noqa: BLE001
        _degrade_note("us_options", f"{symbol} 窄快照失败({type(e).__name__}: {e})", key=symbol)
        return {"status": "UNMEASURED", "reason": f"{type(e).__name__}"}


def readthrough_block(ticker: str, as_of: str, *, items=None, industry: str | None = None,
                      tape=None, iv=None, earnings=None) -> str | None:
    """A 股 full 的「海外映射」块:≤4 个美股名,每名一行**事实**。

    **渲染禁忌(§8,测试逐字探针)**:映射只表示「值得观察的关系」,不表示因果、不表示涨跌
    方向、不表示评级方向 —— 本块**不产生任何推论句**(禁词「所以 / 传导 / 带动」)。
    无有效映射 → 返回 None,**整块省略**(不写空表)。

    注入点(测试用,禁真网络):`items` 名单 / `tape` {SYMBOL: 行} / `iv` {SYMBOL: 窄快照}
    / `earnings` {SYMBOL: {"date","session"}}。
    """
    raw = items if items is not None else _readthrough_items(ticker, as_of, industry)
    valid = [it for it in (raw or []) if _rt_valid(it, as_of)][:_RT_MAX]
    if not valid:
        return None
    syms = [str(it["symbol"]).strip() for it in valid]
    if tape is None:
        tape, stale = _readthrough_tape(syms, as_of)
    else:
        tape, stale = {str(k).upper(): v for k, v in dict(tape).items()}, None

    out = [
        "> 名单来自人工维护的 `readthrough_map.yaml`(有效期内 + 证据 URL 齐,单层 ≤4 项)。",
        "> 映射只表示「值得观察的关系」:**不表示因果、不表示涨跌方向、不表示评级方向**。",
        "> 下面每一行都是**事实行**(行情由确定性 tape 供给;隐含波动是**预期量级**,不是方向),"
        "不是推论链 —— 要不要把它写进论点,由分析师自己举证。",
        "",
        "| 美股名 | 类型 | relation | direction | 上一完整交易日% | 5日% | 下次财报日 | 隐含波动量级(30D ATM IV) | 数据状态 |",
        "|---|---|---|---|---:|---:|---|---|---|",
    ]
    notes: list[str] = []
    for it in valid:
        sym = str(it["symbol"]).strip()
        kind = str(it["kind"])
        row = tape.get(sym.upper())
        reason = stale
        if reason is None and row is None:
            reason = f"tape 无 {sym} 行"
        elif reason is None and _rt_incomplete(row):
            reason = "美股时段未收(session_complete=False)"
        if kind == "index":
            iv_cell = "不适用(指数非可挂牌主体)"        # 不为它去抓一条注定为空的链
        else:
            snap = (iv or {}).get(sym) if iv is not None else _readthrough_iv(sym, as_of)
            iv_cell = (f"{_iv_pct((snap or {}).get('atm_iv_30d'))}(年化)"
                       if str((snap or {}).get("status")) == "ok"
                       else f"UNMEASURED({(snap or {}).get('reason') or '未给原因'})")
        if kind == "company":
            e = ((earnings or {}).get(sym) if earnings is not None
                 else next_earnings_spec(sym, as_of))
            earn_cell = (f"{e.get('date')}({e.get('session') or '时段未知'})"
                         if (e or {}).get("date") else "未确认")
        else:
            # §8:ETF / 指数不得伪装成公司或财报主体 —— 硬门,不是提示。
            earn_cell = "不适用(非公司主体)"
        out.append(f"| **{sym}** | {kind} | {it['relation']} | {it['direction']} | "
                   f"{_signed(_rt_cell(row, 'pct_1d'))} | {_signed(_rt_cell(row, 'pct_5d'))} | "
                   f"{earn_cell} | {iv_cell} | {reason or '—'} |")
        notes.append(f"- **{sym}** ｜ relation={it['relation']} ｜ direction={it['direction']} "
                     f"｜ rationale(原样): {str(it.get('rationale') or '—')} ｜ 证据: "
                     f"{str(it['evidence_url'])}")
    out.append("")
    out += notes
    out.append("")
    out.append("> `relation` / `direction` / `rationale` 一律**原样搬运**人工映射表,harvest "
               "不改写、不加解释。缺行 / 未收盘的一律标在「数据状态」列,**不用 0 填充**。")
    return "\n".join(out)


def _rt_cell(row, col: str):
    if not row or col not in row:
        return None
    return _num(row[col], 2)


def _rt_incomplete(row) -> bool:
    """`session_complete` 三态判活 —— pandas 取出来是 `numpy.bool_`,`is False` 判不出。"""
    if not row or "session_complete" not in row:
        return False
    v = row["session_complete"]
    try:
        if v is None or pd.isna(v):
            return False
    except (TypeError, ValueError):
        return False
    if isinstance(v, str):
        return v.strip().lower() in ("false", "0", "no")
    return not bool(v)


def _company_query_name(explicit_name: str | None, identity: dict) -> str | None:
    """gnews 查询词的公司名来源(D1.6 #3):显式 `--name`(A股中文简称,Claude 在 session 内
    已知,同 `analyze.assemble --name` 的约定)优先 —— yfinance `longName`/`shortName`
    对 A 股常是英文/拼音,Google News zh 用它检索基本查不到东西;缺省时 fallback 回
    `identity.get("company_name")`(英文 longName,美股这就是对的查询词)。"""
    name = (explicit_name or "").strip()
    return name or identity.get("company_name")


# ───────────────────────── 派发清单(单一事实源)─────────────────────────


def external_sections(ticker: str, curr_date: str, *, slim: bool,
                      company_name: str | None = None) -> list[tuple]:
    """外源扩面块的派发清单 —— **单一事实源**(main() 照它派发,测试照它断言)。

    `slim=True`(scan L4 / stock-research lite)→ **返回空列表**:§5.2「slim 关」,且微观 lite
    的外源事实属 B 类(受 08-26 判断层冻结,要开关灰度)。这是第二道门,main() 里原有的
    `if not slim:` 是第一道。

    美股票:期权 v2 + EDGAR + 分析师行动 + Google News en 候选。
    A 股票:期权 v2(→ 无挂牌期权降级文案)+ 海外映射 + Google News zh 候选。
    """
    if slim:
        return []
    q = (company_name or "").strip() or ticker
    out: list[tuple] = [(_TITLE_OPTIONS, options_block, (ticker, curr_date), {})]
    if _is_ashare(ticker):
        out.append((_TITLE_READTHROUGH, readthrough_block, (ticker, curr_date), {}))
        out.append((_TITLE_GNEWS_ZH, gnews_block, (q, curr_date), {"lang": "zh"}))
    else:
        out.append((_TITLE_EDGAR, edgar_block, (ticker, curr_date), {}))
        out.append((_TITLE_ANALYST_ACTIONS, analyst_actions_block, (ticker, curr_date), {}))
        out.append((_TITLE_GNEWS_EN, gnews_block, (q, curr_date), {"lang": "en"}))
    return out


def _opt_section(title: str, fn, *args, **kwargs) -> str:
    """presence-gated 版 `_section`:块函数返回 None → **整节省略**(不写空表)。"""
    print(f"  - {title} ...", flush=True)
    try:
        out = fn(*args, **kwargs)
    except Exception as e:  # noqa: BLE001 — 外源块一律 B 级,炸了也不许阻断 harvest
        _degrade_note("external_block", f"{title} 渲染失败({type(e).__name__}: {e})")
        return f"\n## {title}\n\n_ERROR fetching this section: {e}_\n"
    if out is None:
        return ""
    body = str(out).strip() or "_(empty)_"
    return f"\n## {title}\n\n{body}\n"
