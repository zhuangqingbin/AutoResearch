#!/usr/bin/env python3
"""影子组合成绩单 —— 真实/影子/市场三条 NAV(确定性,零 LLM,零新端点)。

spec: 2026-07-05 wave §WS-A1。规则(零判断可复现):每笔买单信号日**次日开盘**建仓,固定占
当时 NAV 的 10% 槽;**持有 2 个交易日**后次日开盘平仓(无价顺延,超短主口径,2026-07-10 裁定);
另出 hold=10 副表做连续性对照。无持仓=现金。三条线:真实(≥OW 买单,buy_ledger 同源)/
影子(shadow_buys.csv)/ 市场(全市场等权日收益,与 zero_buy_ledger 口径同族)。
`真实 − 影子` = 门的价值。涨跌停可成交性不模拟(诚实局限)。

W1(2026-07-12,S3 纸面 sizer):影子线之外并列一条"影子(sized)"——同一批 shadow_buys 信号,
仓位改用 `autoresearch.learning.sizer` 的分数 Kelly×波动率目标×流动性 cap 公式,而非固定
10% 槽;presence-gated(无波动数据的票在该轨回退等权)。见 sizer.py 模块 docstring。

Wave11批A9(2026-08-06,响应 2026-08-05 用户裁定隔夜主尺 gap_c1_o2):`simulate()`/`market_nav()`
增 `mode="oc"|"gap"`。gap = signal+1 收盘买 → signal+2 开盘卖(区间外持币,exit 固定
entry_i+1,与 `hold` 参数无关);market_nav 的 gap 变体 = 全市场 (open/pre_close−1) 均值
(隔夜等权基线,与 gap 线口径同步,湖分区自带 pre_close 列)。**主表**改跑 mode="gap";旧主口径
hold=2(2026-07-10 裁定)降为副表,与 hold=10 并列(两副表仍走 mode="oc",数值与改动前逐字
一致,parity)。

  uv run --no-sync python -m autoresearch.learning.paper_nav   # → reports/learning/paper_nav.md
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

_LAKE_DAILY = Path("context/lake/daily")
_START = "20260618"          # 首个 scan 日;之前的湖数据不进成绩单
_SLOT = 0.10                  # 等权轨固定槽(= simulate() 默认 slot;sized 轨回退目标同此值)


def trade_days(start: str = _START, lake: Path | None = None) -> list[str]:
    lake = Path(lake or _LAKE_DAILY)
    if not lake.exists():
        return []
    return sorted(p.stem for p in lake.glob("*.parquet")
                  if len(p.stem) == 8 and p.stem.isdigit() and p.stem >= start)


def load_prices(codes: set[str], days: list[str], lake: Path | None = None) -> dict:
    """{(day, code6): (open, close, low)}——只读涉及票,NaN → None。

    `low`(Wave12-T9 新增第三元):`simulate(mode="gap")` 判定 EXIT_FLAG(T+2 一字跌停开=
    卖不出)要用;向后兼容——旧调用方/旧测试 fixture 只喂/只解包 `(open, close)` 二元组仍
    照常工作(`simulate` 对缺第三元的 value 按 `low=None` 处理,不判定为卖不出)。
    """
    from autoresearch.data.tushare_source import _code6
    lake = Path(lake or _LAKE_DAILY)
    want = {str(c).zfill(6) for c in codes}
    out: dict[tuple[str, str], tuple[float | None, float | None, float | None]] = {}
    if not want:
        return out
    for d in days:
        p = lake / f"{d}.parquet"
        if not p.exists():
            continue
        try:
            df = pd.read_parquet(p, columns=["ts_code", "open", "close", "low"])
        except Exception:  # noqa: BLE001 — 坏分区跳过
            continue
        df = df.assign(_c=_code6(df["ts_code"]))
        for r in df[df["_c"].isin(want)].to_dict("records"):
            o = None if pd.isna(r["open"]) else float(r["open"])
            c = None if pd.isna(r["close"]) else float(r["close"])
            lo = None if pd.isna(r["low"]) else float(r["low"])
            out[(d, r["_c"])] = (o, c, lo)
    return out


def _unsellable_open(open_px: float | None, low_px: float | None,
                     ref_close: float | None, code: str) -> bool:
    """gap 模式卖腿是否撞上一字跌停开(EXIT_FLAG 口径的操作性镜像)。

    公式对齐 `factor_lab.forward_returns` 生产 `unsellable_o2` 的判定(open≈全日最低 ∧
    open 较前一交易日收盘≈跌停):`open_px<=low_px`(一字板:开盘就是当日最低)且
    `open_px<=ref_close*(1-板幅*0.98/100)`。`ref_close` 传位置持仓当前的 `last_close`——
    在被检查的这一天的估值步骤③运行**之前**读取,天然等于"上一个交易日的收盘价"(不论是
    刚建仓当天还是顺延过 N 天后的某天,同一个字段递推着满足这个语义,不需要额外状态)。

    缺任一价(open/low/ref_close)→ False(不确定不判定为卖不出——与既有"缺 open 才顺延"
    路径同一保守方向:数据不全时照旧结算,不能让 low 数据缺失把整条 gap 主表都卡住;真正
    的"未知"三态语义属于生产 `unsellable_o2` 那一层,这里只是操作决策,两层职责不同)。
    """
    if open_px is None or low_px is None or ref_close is None or ref_close <= 0:
        return False
    from autoresearch.research.factor_lab import _board_limit
    lim = _board_limit(code)
    return open_px <= low_px + 1e-6 and open_px <= ref_close * (1 - lim * 0.98 / 100.0)


def simulate(signals: list[dict], prices: dict, days: list[str],
             slot: float = _SLOT, hold: int = 2, mode: str = "oc"
             ) -> tuple[pd.Series, list[str]]:
    """事件组合模拟(纯函数)。signals=[{date, code}](date 兼容 YYYY-MM-DD / YYYYMMDD)。

    次日(signal+1)入场。mode="oc"(默认,parity——与改动前逐字一致):**开盘**建仓
    (slot×当时NAV,现金不足取剩余),exit=entry 后第 hold 个交易日**开盘**(无 open 顺延)。
    mode="gap"(隔夜尺,2026-08-05 裁定):**收盘**建仓,exit 固定 entry 后 1 个交易日**开盘**
    (区间外持币;`hold` 参数不生效,忽略传入值)。两模式 exit 腿都读开盘价(唯一实现价,与
    mode 无关);持仓按最新可得 close 估值(停牌沿用)。信号日非交易日 → 跳过并记行。
    signal 可选带 "weight" 键(0-1,NAV 占比,S3 sized 轨用)覆盖 slot;缺省仍用 slot
    (等权轨)。

    Wave12-T9(EXIT_FLAG `unsellable_o2` 消费②,实现口径就此锁定):mode="gap" 的到期平仓若
    撞上**一字跌停开**(`_unsellable_open`:开=当日最低 且 较前收跌停)——**不**在跌停价强制
    卖飞(M2 修复,final-review 2026-08-08:不主张这会让账本"更好看"或"更难看"——账本读数
    可能因顺延后价格反弹/进一步下跌而更好或更差,方向不是重点;不假装能在一个成交不了的
    价位上成交、更贴近真实持仓体验才是重点,与用户"标旗不剔"裁定同一精神:卖不出=真实
    亏损延续),而是**顺延到下一个可卖开盘价**结算,顺延期间持仓仍按当日可得收盘价逐日估值
    (镜像既有"入场日无价→顺延"的兜底路径,同一套"继续持有、下一天再试"机制)。mode="oc"
    不受影响(parity,该口径只锁 gap 腿——见 brief Step1)。
    """
    if mode not in ("oc", "gap"):
        raise ValueError(f"simulate: 未知 mode={mode!r}(仅接受 'oc'/'gap')")
    idx = {d: i for i, d in enumerate(days)}
    entries: dict[int, list[tuple[str, float]]] = {}
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
        w = s.get("weight", slot)
        entries.setdefault(i, []).append((code, w))
    cash, nav = 1.0, 1.0
    pos: list[dict] = []
    navs: list[float] = []
    for i, d in enumerate(days):
        keep = []
        for p in pos:                                     # ① 到期平仓(无价/一字跌停开 → 顺延)
            v = prices.get((d, p["code"]), (None, None, None))
            o = v[0]
            low = v[2] if len(v) > 2 else None
            blocked = mode == "gap" and _unsellable_open(o, low, p["last_close"], p["code"])
            if p["exit_i"] <= i and o is not None and not blocked:
                cash += p["shares"] * o
            else:
                keep.append(p)
        pos = keep
        for code, w in entries.get(i, ()):                 # ② 建仓(w=该信号权重,缺省 slot)
            px = prices.get((d, code), (None, None))
            entry_px = px[1] if mode == "gap" else px[0]   # gap:收盘入场;oc:开盘入场(现行)
            if entry_px is None:
                skipped.append(f"{d} {code}(入场日无价,跳过)")
                continue
            cost = min(w * nav, cash)
            if cost <= 1e-12:
                skipped.append(f"{d} {code}(现金槽满,跳过)")
                continue
            cash -= cost
            exit_i = i + (1 if mode == "gap" else hold)    # gap:固定隔夜 1 日,hold 不生效
            pos.append({"code": code, "shares": cost / entry_px,
                        "exit_i": exit_i, "last_close": entry_px})
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


def market_nav(days: list[str], lake: Path | None = None, mode: str = "oc") -> pd.Series:
    """全市场等权收益累乘;缺分区/缺列记 0。

    mode="oc"(默认,parity):daily.pct_chg 均值(收盘到收盘,与改动前逐字一致)。
    mode="gap"(隔夜尺,2026-08-05 裁定,与 `simulate(mode="gap")` 口径同步):daily
    (open/pre_close − 1) 均值(隔夜缺口,湖分区自带 pre_close 列,不必跨日拼接)。
    """
    if mode not in ("oc", "gap"):
        raise ValueError(f"market_nav: 未知 mode={mode!r}(仅接受 'oc'/'gap')")
    lake = Path(lake or _LAKE_DAILY)
    rets = []
    for d in days:
        p = lake / f"{d}.parquet"
        r = 0.0
        if p.exists():
            try:
                if mode == "gap":
                    df = pd.read_parquet(p, columns=["open", "pre_close"])
                    o = pd.to_numeric(df["open"], errors="coerce")
                    pc = pd.to_numeric(df["pre_close"], errors="coerce")
                    valid = o.notna() & pc.notna() & (pc != 0)
                    g = o[valid] / pc[valid] - 1.0
                    r = float(g.mean()) if len(g) else 0.0
                else:
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
    """conviction 随信号带出(S3 sizer 的输入;`simulate()` 本身忽略多余键,老调用方不受影响)。"""
    p = Path(path)
    if not p.exists():
        return []
    df = pd.read_csv(p, dtype={"code": str})
    return [{"date": r["date"], "code": str(r["code"]).zfill(6), "conviction": r.get("conviction")}
            for r in df.to_dict("records")]


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


def risk_block(real: pd.Series, shadow: pd.Series, mkt: pd.Series, mode: str = "oc") -> list[str]:
    """风险调整对照块:三线 total/MDD/Sortino。市场线 mode="oc"(默认,parity)= buy&hold 基线
    (StockBench:多数跑不赢它);mode="gap" = 隔夜等权基线(与 gap 主表口径同步,标签同步换)。
    """
    mkt_label = "市场(隔夜等权 gap_c1_o2)" if mode == "gap" else "市场(买入持有 buy&hold)"
    verdict_label = "隔夜等权(市场)" if mode == "gap" else "buy&hold(市场等权)"  # oc:与改动前逐字一致
    rows = [("真实", real), ("影子", shadow), (mkt_label, mkt)]
    out = ["## 风险调整对照(X3)", "", "| 线 | 总收益 | 最大回撤 | Sortino |", "|---|---|---|---|"]
    ms = {}
    for label, nav in rows:
        m = risk_metrics(nav)
        ms[label] = m
        tot = "—" if m["total"] != m["total"] else f"{m['total']:+.2%}"
        mdd = "—" if m["mdd"] != m["mdd"] else f"{m['mdd']:+.2%}"
        out.append(f"| {label} | {tot} | {mdd} | {_fmt_sortino(m['sortino'])} |")
    rs, ks = ms["真实"]["sortino"], ms[mkt_label]["sortino"]
    if rs == rs and ks == ks:                     # 均非 NaN → 给一句基线裁决
        verdict = "跑赢" if rs > ks else ("打平" if rs == ks else "**跑输**")
        out += ["", f"- 真实 vs {verdict_label}风险调整(Sortino):{verdict}"
                    f"({_fmt_sortino(rs)} vs {_fmt_sortino(ks)})。"]
    return out


def render(days: list[str], real: pd.Series, shadow: pd.Series, mkt: pd.Series,
           n_real: int, n_shadow: int, skipped: list[str], hold: int = 2,
           sized: pd.Series | None = None, mode: str = "oc") -> list[str]:
    """sized(S3 纸面 sizer 的影子轨,presence-gated)不传、mode 不传(默认"oc")→ 输出与改动前
    逐字一致(parity)。mode="gap"(主表,2026-08-05 裁定隔夜尺):标题走隔夜措辞,`hold` 不参与
    显示(gap 交易结构固定 signal+1 收→signal+2 开,与 hold 无关)。
    """
    if mode == "gap":
        out = ["# 影子组合成绩单(paper NAV;主表=隔夜尺 gap_c1_o2·signal+1 收盘买→"
               "signal+2 开盘卖·区间外持币·2026-08-05 裁定)", ""]
    else:
        out = [f"# 影子组合成绩单(paper NAV;10% 固定槽·持{hold}交易日·次日开盘进出)", ""]
    if sized is not None:
        out += ["| 日期 | 真实线 | 影子线 | 影子(sized) | 市场等权 |", "|---|---|---|---|---|"]
        out += [f"| {d} | {real[d]:.4f} | {shadow[d]:.4f} | {sized[d]:.4f} | {mkt[d]:.4f} |"
                for d in days]
    else:
        out += ["| 日期 | 真实线 | 影子线 | 市场等权 |", "|---|---|---|---|"]
        out += [f"| {d} | {real[d]:.4f} | {shadow[d]:.4f} | {mkt[d]:.4f} |" for d in days]
    if len(days):
        last = days[-1]
        line = (f"- **截至 {last}**:真实 {real[last] - 1:+.2%}({n_real} 笔)"
                f" vs 影子 {shadow[last] - 1:+.2%}({n_shadow} 笔)")
        if sized is not None:
            line += f" vs 影子(sized) {sized[last] - 1:+.2%}"
        line += f" vs 市场 {mkt[last] - 1:+.2%};`真实 − 影子` = 门的价值。"
        out += ["", line]
        out += [""] + risk_block(real, shadow, mkt, mode=mode)  # X3·风险调整对照(MDD/Sortino)
    if skipped:
        out += ["", "## 未入组信号"] + [f"- {s}" for s in skipped]
    out += ["", "_涨跌停/停牌可成交性未模拟;仅供研究,非投资建议。_"]
    if mode == "gap":
        # M1 修复(final-review 2026-08-08):两条脚注中间补空行分段(markdown 不会被渲染成
        # 同一段),且措辞与上一条"未模拟"不矛盾——gap 卖腿的一字跌停开这一种情形**确实**
        # 被模拟了(顺延),上一条泛化免责声明说的是其余未处理的涨跌停/停牌情形(如入场腿/
        # 非到期日停牌)仍未模拟。
        out += ["", "_上条免责声明的例外:gap 卖腿撞上一字跌停开(EXIT_FLAG `unsellable_o2`)"
                    "这一种情形**确实**被模拟——不强制按跌停价卖飞,顺延到下一个可卖开盘价"
                    "结算(标旗不剔——卖不出=真实亏损延续,持仓期间仍按当日可得收盘价估值)。_"]
    if sized is not None:
        out += ["", "_影子(sized) = S3 纸面仓位 sizer(分数 Kelly×波动率目标×流动性 cap;公式见 "
                    "`autoresearch/learning/sizer.py` docstring);presence-gated:无波动数据的"
                    "票在该轨回退为等权固定槽。_"]
    return out


def summary_line(days, real, shadow, mkt, n_real, n_shadow, sized=None) -> str:
    """sized(presence-gated)不传 → 输出与改动前逐字一致(parity)。"""
    if not len(days):
        return ""
    last = days[-1]
    line = (f"**📈 影子组合成绩单**(起 {days[0]}):真实 {real[last] - 1:+.2%}({n_real}笔)"
            f" vs 影子(若门不拦最想买3只) {shadow[last] - 1:+.2%}({n_shadow}笔)")
    if sized is not None:
        line += f" vs 影子(sized) {sized[last] - 1:+.2%}"
    line += (f" vs 市场等权 {mkt[last] - 1:+.2%}"
             f"——`真实−影子`=门的价值(明细 reports/learning/paper_nav.md)")
    return line


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
    mkt_oc = market_nav(days)                 # 副表(旧口径):收盘到收盘,parity
    mkt_gap = market_nav(days, mode="gap")     # 主表(隔夜尺,2026-08-05 裁定)
    # W1·S3 sizer:同一批影子信号,仓位改按 sizer.size_weights 公式(presence-gated 回退等权)。
    from autoresearch.learning.sizer import size_shadow_signals
    ss_sized = size_shadow_signals(ss, equal_slot=_SLOT)
    # 主表:隔夜尺 gap(2026-08-05 裁定;signal+1 收买→signal+2 开卖,与 hold 无关)。
    real_g, sk1_g = simulate(rs, prices, days, mode="gap")
    shadow_g, sk2_g = simulate(ss, prices, days, mode="gap")
    sized_g, sk3_g = simulate(ss_sized, prices, days, mode="gap")
    # 副表:hold=2(旧主尺口径,2026-07-10 裁定,现降为对照)。
    real2, sk1_2 = simulate(rs, prices, days, hold=2)
    shadow2, sk2_2 = simulate(ss, prices, days, hold=2)
    sized2, sk3_2 = simulate(ss_sized, prices, days, hold=2)
    # 副表:hold=10(旧口径连续性对照)。
    real10, sk1_10 = simulate(rs, prices, days, hold=10)
    shadow10, sk2_10 = simulate(ss, prices, days, hold=10)
    sized10, sk3_10 = simulate(ss_sized, prices, days, hold=10)
    primary = render(days, real_g, shadow_g, mkt_gap, len(rs), len(ss),
                      sk1_g + sk2_g + sk3_g, sized=sized_g, mode="gap")
    sec2 = render(days, real2, shadow2, mkt_oc, len(rs), len(ss), sk1_2 + sk2_2 + sk3_2,
                  hold=2, sized=sized2)
    sec10 = render(days, real10, shadow10, mkt_oc, len(rs), len(ss), sk1_10 + sk2_10 + sk3_10,
                   hold=10, sized=sized10)
    full = (primary
            + ["", "## 副表:hold=2(旧主尺对照,2026-07-10 裁定)", ""] + sec2[2:]
            + ["", "## 副表:hold=10(旧口径连续性对照)", ""] + sec10[2:])
    outp.write_text("\n".join(full) + "\n", encoding="utf-8")
    line = summary_line(days, real_g, shadow_g, mkt_gap, len(rs), len(ss), sized=sized_g)
    Path("reports/learning/paper_nav_summary.txt").write_text(line + "\n", encoding="utf-8")
    print(f"[paper_nav] {len(days)} 日 × (真实{len(rs)}/影子{len(ss)}) "
          f"主表=隔夜尺(gap,08-05 裁定) → {outp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
