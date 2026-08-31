#!/usr/bin/env python3
"""隔夜窗海外事件日历(D-2;确定性,零 LLM)—— **风险可见性,不是选股信号**。

design: docs/specs/2026-08-28-external-evidence-expansion-design.md §2 / §10 D-2

## 为什么这件事非做不可

主尺是 `gap_c1_o2`:T+1 15:00 买 → T+2 09:30 卖。美股常规时段 21:30–04:00 CST、盘后财报到
08:00 CST、FOMC 02:00 CST —— **T+1 的整个美股交易日都落在持仓窗内,且在 A 股 T+2 开盘前结束**。
08-26 那次真跑里,NVDA 08-26 16:00 ET 盘后财报(次日 +6.3%)对 21:20 发布的报告属于「入场前」
窗,而 summary 的 📅 日历里没有它、📌 持仓 300857(算力链)也没被提醒。

**它只回答「买之前/持仓期间有什么已知的外部事件」,不回答「该不该买」**:
不自动否决入场、不改仓位、不改评级、不推导方向(§0 边界表最后一行)。B 类的判断层接入
(L4 slim 事实行 / intel 第七面)受冻结,与本模块无关。

## 两窄窗(§2)

| 窗 | 区间(CST) | 用途 |
|---|---|---|
| `pre_entry` | (报告时刻, T+1 14:45] | 「入场否决」栏的**人审核对项** |
| `holding_overnight` | (T+1 15:00, T+2 09:30] | 隔夜风险可见性;📌 持仓 tripwire |

`date_risk` = 只有日期没有时刻(`DATE_ONLY`)的当日风险 —— **不许猜 AMC/BMO 补成精确窗**。

## PIT 与展示纪律

- 只有 `first_seen_ts <= decision_cutoff` 的事件可见(当时不可知的进不了任何窗);
- 同 `event_id` 只展示 cutoff 时点可见的**最新 revision**,改期保留旧条;
- 展示上限:summary ≤4 行、brief ≤1 句、每个持仓哨兵 ≤3 条,超出写 `+N`。

时区一律走 IANA(`Asia/Shanghai` / `America/New_York`),**禁止写死 UTC+8 / ET 偏移** ——
DST、美国休市、A 股休市三样都要过。

    uv run --no-sync python -m autoresearch.scan.overseas 2026-08-26
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from autoresearch.common import workspace as ws

CN_TZ = "Asia/Shanghai"
US_TZ = "America/New_York"

#: 运营入场截止 = T+1 **14:45**,不是交易所的 14:57 —— 人读完报告还要下单(§2 与
#: `scan/exec_anchor.py` 同一个数;两处不一致会让「来不来得及」这件事有两个答案)。
ENTRY_CUTOFF = time(14, 45)
T2_OPEN = time(9, 30)

OVERSEAS_CSV = "overseas_calendar.csv"
_COLS = ("event_id", "event_type", "subject", "window", "time_quality",
         "local_date", "scheduled_at_utc", "mapped_symbols", "source_url",
         "first_seen_ts", "revision", "status")

#: 展示上限(§2 末段)。超出不是丢弃,是折成 `+N`——「产物能证明跑过什么」。
MAX_SUMMARY_ROWS = 4
MAX_BRIEF_ROWS = 1
MAX_TRIPWIRE_ROWS = 3

_WINDOW_ZH = {"pre_entry": "入场前", "holding_overnight": "持仓隔夜", "date_risk": "当日风险"}


def _cn(dt_date, t: time) -> datetime:
    return datetime.combine(dt_date, t, tzinfo=ZoneInfo(CN_TZ))


def anchors(analysis_date: str, report_ts: datetime | None = None,
            trade_days=None) -> tuple[datetime, datetime, datetime]:
    """三个锚:(报告时刻, T+1 入场截止, T+2 开盘)。T/T+1/T+2 按 **A 股交易日历**。

    `trade_days` 缺省用 `autoresearch.common.trade_days`;取不到就退化成「下一个自然日」
    并由调用方自行标注 —— 退化是 B 级降级,不是静默正确。
    """
    day0 = datetime.strptime(analysis_date[:10], "%Y-%m-%d").date()
    nxt = None
    if trade_days is None:
        try:
            from autoresearch.scan import trade_days as _td
            trade_days = _td
        except Exception:  # noqa: BLE001
            trade_days = None
    if trade_days is not None:
        for fn in ("next_trade_day", "next_trading_day", "shift"):
            f = getattr(trade_days, fn, None)
            if callable(f):
                try:
                    nxt = f(day0.isoformat()) if fn != "shift" else f(day0.isoformat(), 1)
                    break
                except Exception:  # noqa: BLE001
                    nxt = None
    def _next_trade_day(d):
        """A 股下一交易日;日历不可用 → 退化成下一自然日(**退化会被 run() 记账**)。"""
        if trade_days is not None:
            for fn in ("next_trade_day", "next_trading_day"):
                f = getattr(trade_days, fn, None)
                if callable(f):
                    try:
                        got = f(d.isoformat())
                        if isinstance(got, str):
                            return datetime.strptime(got[:10], "%Y-%m-%d").date()
                    except Exception:  # noqa: BLE001
                        pass
        return d + timedelta(days=1)

    t1 = datetime.strptime(nxt[:10], "%Y-%m-%d").date() if isinstance(nxt, str) else _next_trade_day(day0)
    # 2026-08-29 D-0 普查逮到:T+2 原本写 `t1 + 1 自然日`,**只有 T+1 走了交易日历** ——
    # 周五的 run 会把「T+2 开盘」锚到周六 09:30,于是整个周末的美股事件被判成窗外。
    t2 = _next_trade_day(t1)
    rep = report_ts or _cn(day0, time(21, 0))
    return rep, _cn(t1, ENTRY_CUTOFF), _cn(t2, T2_OPEN)


def collect(analysis_date: str, *, report_ts: datetime | None = None,
            symbols=None, horizon_days: int = 14) -> list:
    """取 FRED release / FOMC / 映射票财报 → 分窗 → PIT 过滤 → 去重取最新 revision。

    **B 级**:任何一个源取不到就跳过它(记账由源模块自己做),**不抛**;整体失败返回 []。
    """
    from autoresearch.data.sources import official_event_calendar as oec
    rep, t1, t2 = anchors(analysis_date, report_ts)
    events: list = []
    day0 = datetime.strptime(analysis_date[:10], "%Y-%m-%d").date()
    end = (day0 + timedelta(days=horizon_days)).isoformat()
    # 名字**显式写死**,不取 `loader.__name__` —— 降级键是给人读的运维事实,不该随
    # 函数对象(包装/打桩/重命名)漂移。查表在调用点解析,便于测试替换单条腿。
    for name in ("fred", "fomc", "earnings"):
        loader = {"fred": _fred_events, "fomc": _fomc_events, "earnings": _earnings_events}[name]
        try:
            events.extend(loader(analysis_date, end, symbols) or [])
        except Exception as exc:  # noqa: BLE001 — B 级:少一个源不挡日历,**但必须留痕**
            # 2026-08-29:原来这里是裸 `continue`。于是 `fetch_releases` 因 limit 超上限
            # 必炸这件事被完整吞掉:日历里 FRED 恒为空,而汇总屏、degraded.json、run_health
            # 三处都看不出少了一个源。「系统有降级能力、没有『我降级了』的传达能力」正是
            # 数据契约设计稿要治的病 —— 少一个源可以,不说话不行。
            try:
                from autoresearch.data.contracts import record_degradation
                record_degradation(f"overseas:{name}", f"{type(exc).__name__}: {exc}")
            except Exception:  # noqa: BLE001
                pass
            continue
    if not events:
        return []
    try:
        events = oec.visible_at(events, rep)
        events = oec.latest_revisions(events)
        events = oec.assign_windows(events, rep, t1, t2)
    except Exception:  # noqa: BLE001
        return []
    keep = [e for e in events if getattr(e, "window", None) in _WINDOW_ZH]
    try:
        ordered, _ = oec.order_for_display(keep, watched_symbols=tuple(symbols or ()))
        return ordered
    except Exception:  # noqa: BLE001
        return keep


def _fred_events(start: str, end: str, symbols) -> list:
    """FRED release → `ExternalEvent` 列表(**全 `DATE_ONLY`**:FRED 只给哪天发,不给几点)。

    2026-08-29 D-0 普查逮到:这里原本直接返回 `fetch_releases` 的 **DataFrame**,
    到 `visible_at` 就炸,又被 `collect` 的 B 级 except 吞掉 —— 日历里 FRED 恒为空且不报警。
    现在走 `fetch_releases_safe`(区分「源成功但真空」与「请求失败」),失败由它自己记降级。
    """
    from autoresearch.data.sources import fred_calendar
    from autoresearch.data.sources import official_event_calendar as oec

    got = fred_calendar.fetch_releases_safe(start, end)
    df = getattr(got, "rows", None)          # FetchOutcome.rows / .status / .reason
    if df is None or not len(df):
        return []
    first_seen = datetime.now(ZoneInfo("UTC"))
    out: list = []
    for row in df.to_dict(orient="records"):
        day = str(row.get("date") or "")[:10]
        if not day:
            continue
        name = str(row.get("release_name") or "").strip() or "FRED release"
        fam = fred_calendar.classify_family(name)
        out.append(oec.ExternalEvent(
            event_id=f"fred:{row.get('release_id')}:{day}",
            event_type="fomc" if fam == "fomc" else "macro_release",
            subject=name,
            scheduled_at_utc=None,                 # DATE_ONLY:**不许猜时刻**
            local_date=datetime.strptime(day, "%Y-%m-%d").date(),
            timezone=US_TZ, time_quality="DATE_ONLY",
            source_url="https://fred.stlouisfed.org/releases",
            first_seen_ts=first_seen, revision="fred.v1", status="scheduled"))
    return out


def _fomc_events(start: str, end: str, symbols) -> list:
    from autoresearch.data.sources import fomc_calendar
    year = int(start[:4])
    out = list(fomc_calendar.load_fomc(year) or [])
    if end[:4] != start[:4]:
        out += list(fomc_calendar.load_fomc(int(end[:4])) or [])
    return [e for e in out if start[:10] <= e.local_date.isoformat() <= end[:10]]


def _earnings_events(start: str, end: str, symbols) -> list:
    """映射票的下次财报(§8 映射表给名单)。无映射 / 无 earnings 源 → []。"""
    if not symbols:
        return []
    from autoresearch.data.sources import official_event_calendar as oec
    fetch = None
    try:
        from autoresearch.data.sources.yf_options import next_earnings  # type: ignore
        fetch = next_earnings
    except Exception:  # noqa: BLE001
        return []
    out = []
    for sym in symbols:
        try:
            got = fetch(sym)
        except Exception:  # noqa: BLE001
            continue
        if isinstance(got, oec.ExternalEvent):
            out.append(got)
    return out


def write_csv(scan_dir: Path | str, events: list) -> Path:
    """落 `<scan_dir>/overseas_calendar.csv`(列固定;空事件也落表头 —— 空文件与没跑要能分开)。"""
    scan_dir = Path(scan_dir)
    scan_dir.mkdir(parents=True, exist_ok=True)
    out = scan_dir / OVERSEAS_CSV
    with out.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(_COLS)
        for e in events:
            w.writerow([
                e.event_id, e.event_type, e.subject, e.window or "", e.time_quality,
                e.local_date.isoformat(),
                e.scheduled_at_utc.isoformat() if e.scheduled_at_utc else "",
                "|".join(e.mapped_symbols or ()), e.source_url,
                e.first_seen_ts.isoformat(), e.revision, e.status])
    return out


def _read_csv(scan_dir: Path | str) -> list[dict]:
    p = Path(scan_dir) / OVERSEAS_CSV
    if not p.exists():
        return []
    try:
        with p.open(encoding="utf-8") as fh:
            return list(csv.DictReader(fh))
    except Exception:  # noqa: BLE001
        return []


def _fmt(row: dict) -> str:
    win = _WINDOW_ZH.get(row.get("window", ""), row.get("window", ""))
    when = row.get("scheduled_at_utc") or ""
    if when:
        try:
            local = datetime.fromisoformat(when).astimezone(ZoneInfo(CN_TZ))
            when = local.strftime("%m-%d %H:%M CST")
        except Exception:  # noqa: BLE001
            when = row.get("local_date", "")
    else:                      # DATE_ONLY:只报日期,**不猜时刻**
        when = f"{row.get('local_date', '')}(仅日期)"
    syms = row.get("mapped_symbols") or ""
    tail = f" · 映射 {syms.replace('|', '/')}" if syms else ""
    return f"- **{win}**:{row.get('subject', '')}({when}){tail}"


def summary_lines(scan_dir: Path | str, limit: int = MAX_SUMMARY_ROWS) -> list[str]:
    """summary 📅 节的海外事件行(presence-gated:无 csv / 无事件 → [])。

    只是**人工复核提示**,不自动产生否决 / 仓位 / 评级动作。
    """
    rows = _read_csv(scan_dir)
    if not rows:
        return []
    shown = [_fmt(r) for r in rows[:limit]]
    if len(rows) > limit:
        shown.append(f"- _另有 +{len(rows) - limit} 条,见 `{OVERSEAS_CSV}`_")
    return shown


def brief_line(scan_dir: Path | str) -> str:
    """brief ⑤ 风险哨一句(≤1 条 + 计数;无事件 → '')。"""
    rows = _read_csv(scan_dir)
    if not rows:
        return ""
    head = rows[0]
    win = _WINDOW_ZH.get(head.get("window", ""), "")
    more = f"(+{len(rows) - MAX_BRIEF_ROWS})" if len(rows) > MAX_BRIEF_ROWS else ""
    return f"海外窗:{win} {head.get('subject', '')}{more}"


def tripwire_rows(scan_dir: Path | str, code: str, symbols,
                  limit: int = MAX_TRIPWIRE_ROWS) -> list[str]:
    """📌 持仓哨兵:该票映射名单命中的事件(≤3 条)。无映射 / 无命中 → []。"""
    if not symbols:
        return []
    want = {str(s).upper() for s in symbols}
    hits = [r for r in _read_csv(scan_dir)
            if want & {s.upper() for s in (r.get("mapped_symbols") or "").split("|") if s}]
    return [_fmt(r) for r in hits[:limit]]


def run(analysis_date: str, scan_dir: Path | str | None = None, symbols=None) -> dict:
    """prelude 步入口:collect → write_csv。**永不抛**(B 级),返回计数供汇总屏。"""
    scan_dir = Path(scan_dir) if scan_dir else ws.context_root() / "scan" / analysis_date
    try:
        if symbols is None:
            symbols = _mapped_symbols(analysis_date)
        events = collect(analysis_date, symbols=symbols)
    except Exception as exc:  # noqa: BLE001
        try:
            from autoresearch.data.contracts import record_degradation
            record_degradation("overseas_calendar", f"{type(exc).__name__}: {exc}")
        except Exception:  # noqa: BLE001
            pass
        events = []
    path = write_csv(scan_dir, events)
    by_win: dict[str, int] = {}
    for e in events:
        by_win[e.window] = by_win.get(e.window, 0) + 1
    return {"n": len(events), "path": str(path), "by_window": by_win}


def _mapped_symbols(analysis_date: str) -> tuple[str, ...]:
    """映射表里所有有效美股名(§8);模块缺席 / 映射空 → ()。"""
    try:
        from autoresearch.data.readthrough import load_map
    except Exception:  # noqa: BLE001
        return ()
    try:
        m = load_map(analysis_date) or {}
    except Exception:  # noqa: BLE001
        return ()
    out: list[str] = []
    for bucket in ("codes", "industries"):
        for items in (m.get(bucket) or {}).values():
            for it in items or ():
                sym = (it or {}).get("symbol") if isinstance(it, dict) else None
                if sym and sym not in out:
                    out.append(sym)
    return tuple(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="隔夜窗海外事件日历(D-2)")
    ap.add_argument("date")
    ap.add_argument("--scan-dir", default=None)
    args = ap.parse_args(argv)
    got = run(args.date, args.scan_dir)
    print(f"[overseas] {got['n']} 条 → {got['path']}  {got['by_window']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
