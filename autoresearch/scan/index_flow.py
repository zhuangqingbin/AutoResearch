#!/usr/bin/env python3
"""ETF 被动规模 → 调样票「被动买入 ≈ x 天 ADV」描述字段(design 2026-09-25 §2.2 `flow_adv_days`;批 B3)。

只做描述:不进任何门、不进排序、不改评级。三源(fund_basic / fund_share / fund_nav)任一缺 → None,整张
事件表的该字段留空(空 = 未计算,不是 0);第四源 `index_weight` 某一指数的成分快照真空(B 级合法空,
非取数失败)→ 只跳过**那一个指数**的贡献,不用"只有本次涉及票"的假分母去凭空算出一个偏大几十到
几百倍的数字(比留空更危险——它长得像一个算出来的数字)。口径:
  AUM_index(亿) = Σ 非增强/非联接 ETF 的 fd_share(万份)× unit_nav / 1e4
  weight_proxy(code, index) = circ_mv(code) / Σ circ_mv(该指数最新月末成分 ∪ 本次调样涉及票)   —— 自由流通市值近似,
                              **不是**指数公司发布的真实权重(该数只用于估算,读者必须能一眼看出这是近似值)
  flow(亿) = Σ_index ± AUM_index × weight_proxy(调入 +,调出 −)                             —— 跨指数净额
  flow_adv_days(code) = flow / ADV20(亿;湖 daily.amount 千元 → 亿 = ×1e3/1e8),写在该票每一行上

denom 的并集包含**调入与调出两侧**的票(不只是调入侧):调出票通常应已在 `_members` 快照里,但快照
可能滞后(月末快照 vs 本次事件),显式并入本次事件涉及的每一只票,让权重代理在调入/调出两侧对称,
不因快照滞后而把调出票的权重错算成"用其它成分票的分母,漏了它自己"。
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from autoresearch.contracts.index_whitelist import INDEX_WHITELIST

BENCHMARK_PATTERNS: dict[str, str] = {
    "000300": r"沪深300",
    "000905": r"中证500(?!成长|价值|信息|医药|红利|低波|行业中性|ESG)",
    "000852": r"中证1000(?!成长|价值)",
    "000510": r"中证A500",
    "000688": r"科创50|上证科创板50",
    "399006": r"创业板指数|创业板指(?!成长|50|动量|中盘)",
}
_EXCLUDE_NAME = re.compile(r"增强|ESG|联接|LOF")


def _default_fetch():
    return None


def _index_of(benchmark: str) -> str | None:
    for idx, pat in BENCHMARK_PATTERNS.items():
        if re.search(pat, str(benchmark)):
            return idx
    return None


def etf_aum_by_index(as_of: str, *, fetch=None) -> dict[str, float] | None:
    """六指数各自的跟踪 ETF 总规模(亿元;剔增强/ESG/联接/LOF)。三源任一不可达 → None。"""
    from autoresearch.data import cache
    from autoresearch.data.contracts import record_degradation
    fetch = fetch if fetch is not None else _default_fetch()
    try:
        basic = cache.get_or_fetch("fund_basic", {"market": "E", "status": "L"}, today=as_of, fetch=fetch)
        share = cache.get_or_fetch("fund_share", {"trade_date": as_of}, today=as_of, fetch=fetch)
        nav = cache.get_or_fetch("fund_nav", {"nav_date": as_of, "market": "E"}, today=as_of, fetch=fetch)
    except Exception as e:  # noqa: BLE001 — 三源任一不可达:presence-gated,不阻断
        record_degradation("fund_share", f"ETF 规模三源之一不可达({type(e).__name__}: {e})→ flow_adv_days 留空",
                           key=as_of)
        return None
    if basic is None or share is None or nav is None or basic.empty or share.empty or nav.empty:
        return None
    b = basic[~basic["name"].astype(str).str.contains(_EXCLUDE_NAME)].copy()
    b["idx"] = b["benchmark"].map(_index_of)
    b = b[b["idx"].notna()]
    m = (b.merge(share[["ts_code", "fd_share"]], on="ts_code", how="inner")
          .merge(nav[["ts_code", "unit_nav"]].drop_duplicates("ts_code"), on="ts_code", how="inner"))
    m["aum_yi"] = m["fd_share"].astype(float) * m["unit_nav"].astype(float) / 1e4
    out = m.groupby("idx")["aum_yi"].sum().to_dict()
    return {k: float(v) for k, v in out.items()}


def _members(index_code6: str, as_of: str, fetch) -> set[str]:
    """该指数最新已发布月末(= `as_of` 所在月的上月末)成分股六位代码集合。取不到 → 空集。"""
    import calendar as _cal

    from autoresearch.data import cache
    y, m = int(as_of[:4]), int(as_of[4:6])
    py, pm = (y, m - 1) if m > 1 else (y - 1, 12)                      # 最新已发布月末 = 上月末
    end = f"{py}{pm:02d}{_cal.monthrange(py, pm)[1]:02d}"
    suffix = ".SZ" if index_code6.startswith("399") else ".SH"
    df = cache.get_or_fetch("index_weight", {"index_code": index_code6 + suffix, "start_date": f"{py}{pm:02d}01",
                                             "end_date": end}, today=end, fetch=fetch)
    if df is None or df.empty:
        return set()
    last = df["trade_date"].astype(str).max()
    return set(df[df["trade_date"].astype(str) == last]["con_code"].astype(str).str[:6])


def _adv20_yi(codes: set[str], as_of: str, lake_daily: Path | None) -> dict[str, float]:
    """codes 各自的 20 日日均成交额(亿元;湖 `daily.amount` 单位千元)。只读湖,零网络。

    默认根 = **`cache.LAKE`**(测试的 monkeypatch 点),不是 `workspace.lake_root()`——后者每次
    调用都重新构造一个不受 monkeypatch 影响的 `Path("lake")`,会在测试里悄悄读到真实仓库的湖
    (2026-09-25 review:worktree 的真实湖有 1111 天历史,「不受 monkeypatch 影响」不会报错,
    只会读到不该读的数字,比抛异常更危险)。
    """
    from autoresearch.data import cache
    d = Path(lake_daily) if lake_daily else cache.LAKE / "daily"
    days = sorted(p.stem[:8] for p in d.glob("*.parquet") if p.stem[:8].isdigit() and p.stem[:8] <= as_of)[-20:]
    frames = [pd.read_parquet(d / f"{day}.parquet", columns=["ts_code", "amount"]) for day in days]
    if not frames:
        return {}
    px = pd.concat(frames, ignore_index=True)
    px["code"] = px["ts_code"].astype(str).str[:6]
    px = px[px["code"].isin(codes)]
    return (px.groupby("code")["amount"].mean() * 1e3 / 1e8).to_dict()


def _circ_mv(as_of: str, lake_daily_basic: Path | None) -> dict[str, float]:
    """最新一份(≤ as_of)`daily_basic` 快照里,各票的自由流通市值(万元)。只读湖,零网络。

    默认根同 `_adv20_yi`——`cache.LAKE`,不是 `workspace.lake_root()`。
    """
    from autoresearch.data import cache
    d = Path(lake_daily_basic) if lake_daily_basic else cache.LAKE / "daily_basic"
    days = sorted(p.stem[:8] for p in d.glob("*.parquet") if p.stem[:8].isdigit() and p.stem[:8] <= as_of)
    if not days:
        return {}
    df = pd.read_parquet(d / f"{days[-1]}.parquet", columns=["ts_code", "circ_mv"])
    return dict(zip(df["ts_code"].astype(str).str[:6], df["circ_mv"].astype(float), strict=True))


def flow_adv_days(events: pd.DataFrame, as_of: str, *, lake_daily: Path | None = None,
                  lake_daily_basic: Path | None = None, fetch=None) -> pd.Series | None:
    """`events`(`index_events.EVENT_COLS` 形状)逐行的跨指数净被动流 / ADV20(天)。算不出 → None。

    净额跨指数按 `code` 汇总(同一票同日一边调出一边调入 → 两条腿相抵);单行的值就是该票的净额,
    ADV20 用同一票同一份,故同 code 的多行取值相同(净额是这只票的,不是这一行事件的)。
    """
    if events is None or events.empty:
        return None
    fetch = fetch if fetch is not None else _default_fetch()
    aum = etf_aum_by_index(as_of, fetch=fetch)
    if aum is None:
        return None
    circ = _circ_mv(as_of, lake_daily_basic)
    adv = _adv20_yi(set(events["code"].astype(str)), as_of, lake_daily)
    flow_by_code: dict[str, float] = {}
    for idx in sorted(set(events["index_code"].astype(str))):
        if idx not in INDEX_WHITELIST or idx not in aum:
            continue
        rows = events[events["index_code"].astype(str) == idx]
        # denom 并集含本次事件里这个指数涉及的**所有**票(调入 + 调出),不只是调入侧:调出侧的票
        # 概念上应已在 `_members` 的月末快照里,但快照可能滞后于事件,显式并入让权重代理两侧对称
        # (review 2026-09-25:只并入 add 侧会让调出票的权重分母少算它自己那一份,把净额算偏)。
        touched = set(rows["code"].astype(str))
        members = _members(idx, as_of, fetch)
        # 2026-09-25 review:成分快照真空(B 级取数取到但 0 行——tushare 尚未发布/限流)时,
        # `members` 是空集,不能悄悄退化成「分母只有 touched 这几只」——一个真实指数总有几十到
        # 几百个成分,空集从来不是"这个指数真的没有成分",只可能是"这次没能读出来"。绝不能把
        # 「没问出答案」和「问出的答案是只有这几只」编码成同一个空集(缺席 ≠ 否,本仓反复踩过的坑):
        # 分母会因此只剩这一两只调样票自己,权重代理算成接近 100%,把 flow_adv_days 撑大几十到
        # 几百倍——比留空更危险的错误,因为它长得像一个算出来的数字。这个指数本次直接跳过
        # (denom 用不着算,连 touched 的份额都不猜),不让它污染 flow_by_code。
        if not members:
            continue
        denom = sum(circ.get(c, 0.0) for c in (members | touched))
        if denom <= 0:
            continue
        for r in rows.itertuples(index=False):
            w = circ.get(str(r.code), 0.0) / denom
            sign = 1.0 if r.side == "add" else -1.0
            flow_by_code[str(r.code)] = flow_by_code.get(str(r.code), 0.0) + sign * aum[idx] * w
    vals = []
    for r in events.itertuples(index=False):
        a = adv.get(str(r.code))
        f = flow_by_code.get(str(r.code))
        vals.append(None if not a or f is None else round(f / a, 2))
    return pd.Series(vals, index=events.index, dtype="float64")
