#!/usr/bin/env python3
"""影子组合成绩单 —— 真实/影子/市场三条 NAV(确定性,零 LLM,零新端点)。

spec: 2026-07-05 wave §WS-A1。规则(零判断可复现):每笔买单信号日**次日开盘**建仓,固定占
当时 NAV 的 10% 槽;**持有 2 个交易日**后次日开盘平仓(无价顺延,超短主口径,2026-07-10 裁定);
另出 hold=10 副表做连续性对照。无持仓=现金。三条线:真实(≥OW 买单,buy_ledger 同源)/
影子(shadow_buys.csv)/ 市场(全市场等权日收益,与 zero_buy_ledger 口径同族)。
`真实 − 影子` = 门的价值。涨跌停可成交性不模拟(诚实局限)。

  uv run --no-sync python -m autoresearch.learning.paper_nav   # → reports/learning/paper_nav.md
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

_LAKE_DAILY = Path("context/lake/daily")
_START = "20260618"          # 首个 scan 日;之前的湖数据不进成绩单


def trade_days(start: str = _START, lake: Path | None = None) -> list[str]:
    lake = Path(lake or _LAKE_DAILY)
    if not lake.exists():
        return []
    return sorted(p.stem for p in lake.glob("*.parquet")
                  if len(p.stem) == 8 and p.stem.isdigit() and p.stem >= start)


def load_prices(codes: set[str], days: list[str], lake: Path | None = None) -> dict:
    """{(day, code6): (open, close)}——只读涉及票,NaN → None。"""
    from autoresearch.data.tushare_source import _code6
    lake = Path(lake or _LAKE_DAILY)
    want = {str(c).zfill(6) for c in codes}
    out: dict[tuple[str, str], tuple[float | None, float | None]] = {}
    if not want:
        return out
    for d in days:
        p = lake / f"{d}.parquet"
        if not p.exists():
            continue
        try:
            df = pd.read_parquet(p, columns=["ts_code", "open", "close"])
        except Exception:  # noqa: BLE001 — 坏分区跳过
            continue
        df = df.assign(_c=_code6(df["ts_code"]))
        for r in df[df["_c"].isin(want)].to_dict("records"):
            o = None if pd.isna(r["open"]) else float(r["open"])
            c = None if pd.isna(r["close"]) else float(r["close"])
            out[(d, r["_c"])] = (o, c)
    return out


def simulate(signals: list[dict], prices: dict, days: list[str],
             slot: float = 0.10, hold: int = 2) -> tuple[pd.Series, list[str]]:
    """事件组合模拟(纯函数)。signals=[{date, code}](date 兼容 YYYY-MM-DD / YYYYMMDD)。

    次日开盘建仓(slot×当时NAV,现金不足取剩余);exit=entry 后第 hold 个交易日开盘
    (无 open 顺延);持仓按最新可得 close 估值(停牌沿用)。信号日非交易日 → 跳过并记行。
    """
    idx = {d: i for i, d in enumerate(days)}
    entries: dict[int, list[str]] = {}
    skipped: list[str] = []
    for s in signals:
        d = str(s["date"]).replace("-", "")
        code = str(s["code"]).zfill(6)
        if d not in idx:
            skipped.append(f"{d} {code}(信号日非交易日,孤儿键跳过)")
            continue
        i = idx[d] + 1
        if i >= len(days):
            skipped.append(f"{d} {code}(次日未到,待成熟)")
            continue
        entries.setdefault(i, []).append(code)
    cash, nav = 1.0, 1.0
    pos: list[dict] = []
    navs: list[float] = []
    for i, d in enumerate(days):
        keep = []
        for p in pos:                                     # ① 到期平仓(无 open 顺延)
            o = prices.get((d, p["code"]), (None, None))[0]
            if p["exit_i"] <= i and o is not None:
                cash += p["shares"] * o
            else:
                keep.append(p)
        pos = keep
        for code in entries.get(i, ()):                   # ② 建仓
            o = prices.get((d, code), (None, None))[0]
            if o is None:
                skipped.append(f"{d} {code}(入场日无价,跳过)")
                continue
            cost = min(slot * nav, cash)
            if cost <= 1e-12:
                skipped.append(f"{d} {code}(现金槽满,跳过)")
                continue
            cash -= cost
            pos.append({"code": code, "shares": cost / o, "exit_i": i + hold, "last_close": o})
        mv = 0.0
        for p in pos:                                     # ③ 收盘估值(停牌沿用 last_close)
            c = prices.get((d, p["code"]), (None, None))[1]
            if c is not None:
                p["last_close"] = c
            mv += p["shares"] * p["last_close"]
        nav = cash + mv
        navs.append(round(nav, 6))
    return pd.Series(navs, index=list(days), name="nav"), skipped


def market_nav_from_returns(rets: list[float], days: list[str]) -> pd.Series:
    nav, navs = 1.0, []
    for r in rets:
        nav *= 1 + r
        navs.append(round(nav, 6))
    return pd.Series(navs, index=list(days), name="mkt")


def market_nav(days: list[str], lake: Path | None = None) -> pd.Series:
    """全市场等权日收益累乘(daily.pct_chg 均值;缺分区记 0)。"""
    lake = Path(lake or _LAKE_DAILY)
    rets = []
    for d in days:
        p = lake / f"{d}.parquet"
        r = 0.0
        if p.exists():
            try:
                s = pd.to_numeric(pd.read_parquet(p, columns=["pct_chg"])["pct_chg"],
                                  errors="coerce").dropna()
                r = float(s.mean()) / 100.0 if len(s) else 0.0
            except Exception:  # noqa: BLE001
                r = 0.0
        rets.append(r)
    return market_nav_from_returns(rets, days)


def real_signals(scan_root: Path | str | None = None) -> list[dict]:
    """≥OW 买单信号(verify 折回后,与 buy_ledger 同口径)。"""
    from autoresearch.scan.health import final_ratings
    scan_root = Path(scan_root or "context/scan")
    sig: list[dict] = []
    if not scan_root.exists():
        return sig
    for d in sorted(p for p in scan_root.iterdir() if p.is_dir() and p.name[:2] == "20"):
        sig += [{"date": d.name, "code": c} for c, r in final_ratings(d).items()
                if r in ("Buy", "Overweight")]
    return sig


def shadow_signals(path: Path | str = "context/learning/shadow_buys.csv") -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    df = pd.read_csv(p, dtype={"code": str})
    return [{"date": r["date"], "code": str(r["code"]).zfill(6)} for r in df.to_dict("records")]


def risk_metrics(nav: pd.Series, ann: int = 252) -> dict:
    """NAV 序列 → 风险调整指标(纯函数,X3)。total=总收益;mdd=最大回撤(峰→谷最深,≤0);

    sortino=年化下行风险调整(target 0,mean/下行波动×√ann);全程无下行→inf(标记);样本<2→NaN。
    短序列 sortino 噪声大(诚实局限,同文件『仅供研究』基调)——重在真实 vs 市场的**相对**排序。
    """
    v = pd.to_numeric(nav, errors="coerce").dropna()
    nan = float("nan")
    if len(v) < 2:
        return {"total": nan, "mdd": nan, "sortino": nan}
    total = float(v.iloc[-1] / v.iloc[0] - 1)
    mdd = float((v / v.cummax() - 1).min())
    ret = v.pct_change().dropna()
    downside = ret[ret < 0]
    dd = float((downside.pow(2).mean()) ** 0.5) if len(downside) else 0.0
    sortino = float("inf") if dd == 0 else float(ret.mean() / dd * (ann ** 0.5))
    return {"total": total, "mdd": mdd, "sortino": sortino}


def _fmt_sortino(s: float) -> str:
    if s != s:            # NaN
        return "—"
    return "∞" if s == float("inf") else f"{s:+.2f}"


def risk_block(real: pd.Series, shadow: pd.Series, mkt: pd.Series) -> list[str]:
    """风险调整对照块:三线 total/MDD/Sortino;市场等权 = buy&hold 基线(StockBench:多数跑不赢它)。"""
    rows = [("真实", real), ("影子", shadow), ("市场(买入持有 buy&hold)", mkt)]
    out = ["## 风险调整对照(X3)", "", "| 线 | 总收益 | 最大回撤 | Sortino |", "|---|---|---|---|"]
    ms = {}
    for label, nav in rows:
        m = risk_metrics(nav)
        ms[label] = m
        tot = "—" if m["total"] != m["total"] else f"{m['total']:+.2%}"
        mdd = "—" if m["mdd"] != m["mdd"] else f"{m['mdd']:+.2%}"
        out.append(f"| {label} | {tot} | {mdd} | {_fmt_sortino(m['sortino'])} |")
    rs, ks = ms["真实"]["sortino"], ms["市场(买入持有 buy&hold)"]["sortino"]
    if rs == rs and ks == ks:                     # 均非 NaN → 给一句 buy&hold 裁决
        verdict = "跑赢" if rs > ks else ("打平" if rs == ks else "**跑输**")
        out += ["", f"- 真实 vs buy&hold(市场等权)风险调整(Sortino):{verdict}"
                    f"({_fmt_sortino(rs)} vs {_fmt_sortino(ks)})。"]
    return out


def render(days: list[str], real: pd.Series, shadow: pd.Series, mkt: pd.Series,
           n_real: int, n_shadow: int, skipped: list[str], hold: int = 2) -> list[str]:
    out = [f"# 影子组合成绩单(paper NAV;10% 固定槽·持{hold}交易日·次日开盘进出)", "",
           "| 日期 | 真实线 | 影子线 | 市场等权 |", "|---|---|---|---|"]
    out += [f"| {d} | {real[d]:.4f} | {shadow[d]:.4f} | {mkt[d]:.4f} |" for d in days]
    if len(days):
        last = days[-1]
        out += ["", f"- **截至 {last}**:真实 {real[last] - 1:+.2%}({n_real} 笔)"
                    f" vs 影子 {shadow[last] - 1:+.2%}({n_shadow} 笔)"
                    f" vs 市场 {mkt[last] - 1:+.2%};`真实 − 影子` = 门的价值。"]
        out += [""] + risk_block(real, shadow, mkt)     # X3·风险调整对照(MDD/Sortino vs buy&hold)
    if skipped:
        out += ["", "## 未入组信号"] + [f"- {s}" for s in skipped]
    out += ["", "_涨跌停/停牌可成交性未模拟;仅供研究,非投资建议。_"]
    return out


def summary_line(days, real, shadow, mkt, n_real, n_shadow) -> str:
    if not len(days):
        return ""
    last = days[-1]
    return (f"**📈 影子组合成绩单**(起 {days[0]}):真实 {real[last] - 1:+.2%}({n_real}笔)"
            f" vs 影子(若门不拦最想买3只) {shadow[last] - 1:+.2%}({n_shadow}笔)"
            f" vs 市场等权 {mkt[last] - 1:+.2%}"
            f"——`真实−影子`=门的价值(明细 reports/learning/paper_nav.md)")


def main() -> int:
    days = trade_days()
    outp = Path("reports/learning/paper_nav.md")
    outp.parent.mkdir(parents=True, exist_ok=True)
    if not days:
        outp.write_text("# 影子组合成绩单\n\n_湖 daily 分区缺,无法结算_\n", encoding="utf-8")
        # 同步清空 summary 行(存在即覆盖)——不然 assemble 会把上一次(湖尚在时)的旧行当今日读数幽灵注入。
        Path("reports/learning/paper_nav_summary.txt").write_text("", encoding="utf-8")
        print("[paper_nav] 湖 daily 缺 → 空稿")
        return 0
    rs, ss = real_signals(), shadow_signals()
    codes = {s["code"] for s in rs} | {s["code"] for s in ss}
    prices = load_prices(codes, days)
    mkt = market_nav(days)
    # 主表:hold=2(超短主口径,2026-07-10 裁定);副表:hold=10(旧口径连续性对照)。
    real2, sk1_2 = simulate(rs, prices, days, hold=2)
    shadow2, sk2_2 = simulate(ss, prices, days, hold=2)
    real10, sk1_10 = simulate(rs, prices, days, hold=10)
    shadow10, sk2_10 = simulate(ss, prices, days, hold=10)
    primary = render(days, real2, shadow2, mkt, len(rs), len(ss), sk1_2 + sk2_2, hold=2)
    secondary = render(days, real10, shadow10, mkt, len(rs), len(ss), sk1_10 + sk2_10, hold=10)
    full = primary + ["", "## 副表:hold=10(旧口径连续性对照)", ""] + secondary[2:]
    outp.write_text("\n".join(full) + "\n", encoding="utf-8")
    line = summary_line(days, real2, shadow2, mkt, len(rs), len(ss))
    Path("reports/learning/paper_nav_summary.txt").write_text(line + "\n", encoding="utf-8")
    print(f"[paper_nav] {len(days)} 日 × (真实{len(rs)}/影子{len(ss)}) hold=2 主表 → {outp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
