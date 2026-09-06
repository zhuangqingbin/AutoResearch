#!/usr/bin/env python3
"""D-0 · 海外事件窗普查 —— 「持仓隔夜窗里有海外事件」的那些 A 股交易日,主尺读数不一样吗?

预注册口径逐字来自 `docs/specs/2026-08-28-external-evidence-expansion-design.md` §10 的 D-0 段
(§2 两窄窗 + `time_quality`,§11 D-0 验收行)。读数落 `docs/research/2026-08-29-overseas-event-
window-census.md`,**不进生产**:本模块只读 `lake/` 与 `reports_<engine>/scan/_ledger/`,
不写 run 目录 / staging / lake(本仓有「回放仪器禁写 run 目录/staging」的疤)。

## 它回答什么、不回答什么

只回答一句:**某个事件族值不值得进入影子判断实验**。不直接启用 B-2 / B-3,不改主尺、不改 E6、
不加硬门。即便一族都没有 alpha,L5 日历 + 持仓哨兵仍作为**风险可见性**保留(Q7)—— 日历的
存在理由从来不是「它能赚钱」,而是「漏报一个已知事件是错误」。

## 预注册(§10 D-0,跑前锁死,看完读数不许回头改)

1. **主事件族只有四个**:`FOMC / CPI / NFP / mapped earnings`。其余(PCE / GDP / 未核实映射名单)
   只作**探索性**展示,另报 FDR,**不得混入主门**。
2. **先把股票行聚合成日期级等权组合**,再比较 `gap_c1_o2` 的均值与 `|gap|` 离散度 —— 股票行
   不是独立样本(同一天几千只票共享当天的市场冲击)。`oc_t1`(T+1 开→收,日内腿)仅作对照:
   若效应真来自「隔夜的海外事件」,它应当出现在 gap 上而不是日内腿上。
3. **主检验 = 5 个交易日 moving-block bootstrap**(固定 seed,10,000 次);NW lag 5 只作敏感性。
   每族报 `n_dates` / 标准化 effect size / 95% CI / 原始 p / **四个主族的 Holm 校正 p**
   (`m` 恒取预注册的主族数 4,哪怕只有两族算得出 p —— 少算一族不该让剩下的更容易过)。
   预设**最小事件日数 20**;做 2022–23 / 2024–26 **子期稳定性**。
4. **全市场可买截面 与 finalist / 📌 分表**报告。后者有选择偏差(它们是被漏斗挑出来的),
   **不得外推**到全市场。两张表各算各的日期集合与基准,不共享任何中间量。
5. 保留 `time_quality`:**`DATE_ONLY` 不得伪装成精确窗**。契约层的窗口归属一律走
   `official_event_calendar.classify_window`(DATE_ONLY 只会得到 `date_risk`,永远进不了
   `pre_entry` / `holding_overnight`)。
6. 窗口期 **2022-03 → 2026-08**。

### 普查旗的定义(第 5 条的直接后果,先写后看)

现有源**没有一条 TIMED**(FRED `releases/dates` 只给日期;`fomc_calendar.yaml` 是
`verified_by: pending` 故恒 DATE_ONLY;yfinance 财报时刻是 T3 聚合源,不配把 DATE_ONLY 升
TIMED)。如果普查只认两窄窗,那么**每一族的事件日数都是 0**,整个 D-0 无话可说。所以普查旗
另立一个**日级**口径,它是一句可验证的真陈述,不是猜时刻:

    holding 旗 := 事件的 ET 本地日 [00:00, 次日 00:00) 与持仓窗 (T+1 15:00 CST, T+2 09:30 CST]
                  **相交**

为什么这是真陈述:CST = UTC+8,ET = UTC−4/−5,故 ET 日 T+1 在 CST 下 = [T+1 12:00/13:00,
T+2 12:00/13:00),**恒包含**持仓窗 (T+1 15:00, T+2 09:30]。所以「事件在 ET 日 T+1」⇒
「它落在持仓窗内」对任何 03:00–21:30 ET 的发布时刻都成立 —— 美国宏观发布(08:30 / 10:00 ET)
与盘后财报(16:00 ET)全在此区间。跨周末 / 长假时窗更长,ET 日 Fri/Sat/Sun 都会相交,旗照打。

代价必须说清:ET 日 T+1 在 CST 下从 12:00 起,**早于** 14:45 入场截止,所以这个旗**分不开**
「入场前已知」与「持仓期间发生」。这是 DATE_ONLY 的固有代价,不是实现偷懒。契约层的
`classify_window` 结果(`pre_entry` / `holding_overnight` / `date_risk` / `outside`)逐事件一起
落表,供对账;`date_risk`(相交 `(报告时刻, T+2 开盘]`,更粗)作为敏感性同报。

### PIT 的诚实声明

历史真实发生日**只能用于事后事件研究,不能倒推生产时点一定可知**(§2)。本仪器给每条事件的
`first_seen_ts` 一个显式假设:`ET 本地日 00:00 − ASSUMED_LEAD_DAYS(30 天)`。这是**事件研究的
假设**(宏观日历提前数月公布、财报日提前 2–4 周公布),**不是** PIT 主张。因此
`visible_at` / `classify_window` 的 PIT 闸在本普查里按构造恒通过 —— 闸本身仍实现并有测试锁
(`first_seen_ts > cutoff → outside`),但它在这里没有拦住任何东西,读数不得被当成「当时可知」
的证据。

## 用法

    uv run --no-sync python -m autoresearch.research.overseas_event_census            # 全量(需网络)
    uv run --no-sync python -m autoresearch.research.overseas_event_census --offline  # 只用缓存
    uv run --no-sync python -m autoresearch.research.overseas_event_census --no-earnings

→ `reports_<engine>/research/overseas_event_census.md` + `_overseas_event_census.json`。
事件与日面板缓存落 `docs/research/.cache/overseas_event_census/`(`.cache` 已 gitignore)。

## 上游缺陷备案(本模块绕开,不改别人的文件)

* `fred_calendar.PAGE_LIMIT = 10000` 超出 FRED `releases/dates` 的上限 1000 → 默认参数下
  `fetch_releases` **必炸**(`Variable limit is not between 1 and 1000`)。本模块显式传
  `limit=FRED_PAGE_LIMIT(1000)`。
* `fred_calendar.classify_family` 把 `H.4.1 Factors Affecting Reserve Balances`(每周四的
  美联储资产负债表)判成 `fomc` —— 那是**每周**发布,不是议息会议。本模块的 FOMC 族只认
  `fomc_calendar.load_range`,CPI/NFP 只认**精确**发布名(`Research Consumer Price Index`
  是另一个发布,不算 CPI)。
* `scan/overseas.anchors` 的 T+2 = T+1 **自然日** +1(只有 T+1 走了交易日历),周五的 run 会把
  T+2 锚到周六 09:30。本模块自带 `census_anchors`,T+1/T+2 都取湖里的下一个交易日,并复用
  `overseas` 的 `ENTRY_CUTOFF` / `T2_OPEN` / `CN_TZ` 常量(时刻不另造第二份)。
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from autoresearch.common import ruler as _ruler, stats as _stats, workspace as ws
from autoresearch.data.sources import official_event_calendar as oec
from autoresearch.scan.overseas import CN_TZ, ENTRY_CUTOFF, T2_OPEN, US_TZ

# ───────────────────────── §0 预注册常量(跑前锁死) ─────────────────────────

WINDOW_START = "2022-03-01"
WINDOW_END = "2026-08-31"

MAIN = _ruler.MAIN_RULER                 # gap_c1_o2
MIN_CROSS_SECTION = 50                   # 截面不足 → 该日不进面板(同 edge_census)
MIN_EVENT_DATES = 20                     # §10 预设最小事件日数
BLOCK = 5                                # 5 个交易日 moving block
N_BOOT = 10_000
ALPHA = 0.05
SEED = 20260829
NW_LAG = 5
EFFECT_FLOOR = 0.20                      # Q7 门:|标准化 effect| ≥ 0.2
ASSUMED_LEAD_DAYS = 30                   # first_seen_ts 的显式假设(不是 PIT 主张)
REPORT_HOUR = time(21, 0)                # 报告时刻默认锚(同 scan/overseas.anchors)

#: 主事件族(§10 第 1 条)。顺序即展示序;Holm 的 m 恒取 len(MAIN_FAMILIES)。
MAIN_FAMILIES: tuple[str, ...] = ("FOMC", "CPI", "NFP", "EARNINGS")
FAMILY_LABEL = {
    "FOMC": "FOMC 议息",
    "CPI": "CPI(Consumer Price Index)",
    "NFP": "NFP(Employment Situation)",
    "EARNINGS": "mapped earnings(映射票财报)",
    "PCE": "PCE(Personal Income and Outlays)· 探索",
    "GDP": "GDP(Gross Domestic Product)· 探索",
    "EARNINGS_RAW": "mapped earnings · 未核实映射名单 · 探索",
}
EXPLORATORY_FAMILIES: tuple[str, ...] = ("PCE", "GDP", "EARNINGS_RAW")

#: FRED 发布名 → 族。**精确**匹配(strip + casefold),不用 `classify_family` 的子串规则:
#: 子串会把 `Research Consumer Price Index` 也算成 CPI,把每周的 H.4.1 算成 FOMC。
FRED_EXACT_NAME: dict[str, str] = {
    "consumer price index": "CPI",
    "employment situation": "NFP",
    "personal income and outlays": "PCE",
    "gross domestic product": "GDP",
}
FRED_PAGE_LIMIT = 1000                   # FRED `releases/dates` 的真实上限(源模块写了 10000)

#: 子期切分(§10 第 3 条)。左闭右开。
SUBPERIODS: tuple[tuple[str, str, str], ...] = (
    ("2022-23", "2022-03-01", "2024-01-01"),
    ("2024-26", "2024-01-01", "2026-09-01"),
)

#: **后验(非预注册)**稳健性:持仓窗跨越 A 股长假时,T+1 收盘 → T+2 开盘可以隔着 3~11 个自然日。
#: 长窗天然(a)覆盖更多 ET 日 ⇒ 命中更多海外事件,(b)累积更多消息 ⇒ gap 方差大一个量级。
#: 两件事一起发生 = 「海外事件日」这个旗与「长假窗」共线,任何正读数都可能只是长假的影子。
#: `span_days` = T+1 → T+2 的**自然日**跨度;`SPAN_SHORT_MAX` 以内算普通隔夜窗。
#: 这条检查是跑完主表、看见 NFP/GDP 的组内 sd 比全样本高 4 倍之后加的,**不是**预注册项,
#: 因此只用来**证伪**探索族的正读数(方向保守),不用来把任何族抬进主门。
SPAN_SHORT_MAX = 3

#: 人口(§10 第 4 条)。**分表**,各算各的;`finalist` / `pinned` 有选择偏差,不得外推。
POP_MARKET = "market"
POP_FINALIST = "finalist"
POP_PINNED = "pinned"
POPULATIONS: tuple[str, ...] = (POP_MARKET, POP_FINALIST, POP_PINNED)

#: 判读四态(与 `edge_census.verdict` / `overnight_census.judge` 同一套词,便于跨读数对表)。
POS = "值得影子实验"
NEG = "显著负"
UNPROVEN = "未证"
THIN = "样本不足"

CACHE_DIR = Path(__file__).resolve().parents[2] / "docs" / "research" / ".cache" / "overseas_event_census"


# ───────────────────────── 纯统计层(零网络、零磁盘) ─────────────────────────


def holm(pvalues, *, m_total: int | None = None) -> list[float]:
    """Holm–Bonferroni 逐步向下校正 → 与入参**同序**的校正 p。

    `m_total`:检验族的规模。缺省 = `len(pvalues)`;本普查恒传 `len(MAIN_FAMILIES)` = 4 ——
    某一族因为样本不足算不出 p,不该让剩下的族更容易过门(把 m 缩到 2 就是事后放宽)。
    `m_total < len(pvalues)` 会抛:那是把族规模缩小,方向正好反了。

    单调性由「running max」强制:排序后第 i 个的校正值不得小于前一个(Holm 的定义要求)。
    """
    ps = [float(p) for p in pvalues]
    k = len(ps)
    if not k:
        return []
    m = int(m_total) if m_total is not None else k
    if m < k:
        raise ValueError(f"m_total={m} 小于实际检验数 {k} —— 族规模只能变大不能缩小")
    order = sorted(range(k), key=lambda i: ps[i])
    out = [0.0] * k
    running = 0.0
    for rank, i in enumerate(order):                      # rank 从 0 起
        adj = (m - rank) * ps[i]
        running = max(running, adj)
        out[i] = float(min(1.0, running))
    return out


# BootResult / _diff / moving_block_diff 于 2026-09-06(F5)搬进 `common/stats.py` —— 同仓
# 两套 block bootstrap 就是同一个估计量两处实现。这里是**同对象**转发,抽块与统计口径不变;
# §0 的 BLOCK/N_BOOT/SEED 仍是本普查的预注册记录,与共享默认同值由测试钉死。
from autoresearch.common.stats import (  # noqa: E402
    BootResult,  # noqa: F401
    _diff,
    moving_block_diff,
)


def newey_west_diff(values, flags, *, lag: int = NW_LAG) -> tuple[float | None, float | None, float | None]:
    """OLS `value ~ 1 + flag` 的斜率 + Newey-West(Bartlett,lag=5)HAC t 与双侧 p。**只作敏感性**。

    → `(beta, t, p)`;任一侧为空 / 方差为 0 → `(None, None, None)`(不给一个假 t)。
    """
    v = np.asarray(values, dtype=float)
    f = np.asarray(flags, dtype=bool).astype(float)
    n = len(v)
    if n < 3 or f.sum() == 0 or f.sum() == n:
        return None, None, None
    X = np.column_stack([np.ones(n), f])
    XtX = X.T @ X
    try:
        XtX_inv = np.linalg.inv(XtX)
    except np.linalg.LinAlgError:                          # pragma: no cover - 前面的守卫已排除
        return None, None, None
    beta = XtX_inv @ (X.T @ v)
    resid = v - X @ beta
    u = X * resid[:, None]
    S = u.T @ u
    for L in range(1, min(int(lag), n - 1) + 1):
        w = 1.0 - L / (int(lag) + 1.0)                     # Bartlett 核
        G = u[L:].T @ u[:-L]
        S += w * (G + G.T)
    cov = XtX_inv @ S @ XtX_inv
    var = float(cov[1, 1])
    if not np.isfinite(var) or var <= 0:
        return float(beta[1]), None, None
    t = float(beta[1] / math.sqrt(var))
    p = float(2.0 * (1.0 - _stats.norm_cdf(abs(t))))
    return float(beta[1]), t, p


def standardized_effect(values, flags) -> float | None:
    """标准化 effect size = (事件日均值 − 非事件日均值) / 全序列 sd(日级)。

    分母用**全序列**的 sd 而不是组内合并 sd:两组样本量悬殊(事件日往往只有几十天)时,
    组内合并 sd 会被大组主导得几乎等于全序列 sd,但小组 sd 的估计噪声会把 effect 抖出花来。
    sd=0 或任一侧为空 → None。
    """
    v = np.asarray(values, dtype=float)
    f = np.asarray(flags, dtype=bool)
    d = _diff(v, f)
    if d is None or len(v) < 2:
        return None
    sd = float(v.std(ddof=1))
    if not np.isfinite(sd) or sd <= 1e-15:
        return None
    return float(d / sd)


# ───────────────────────── 时间锚 / 窗口归属 ─────────────────────────


def _cn(day: date, t: time) -> datetime:
    return datetime.combine(day, t, tzinfo=ZoneInfo(CN_TZ))


def _as_date(compact: str) -> date:
    s = str(compact).strip()
    if len(s) == 8 and s.isdigit():
        return date(int(s[:4]), int(s[4:6]), int(s[6:]))
    return date.fromisoformat(s[:10])


def census_anchors(P: list[str], i: int, *, report_hour: time = REPORT_HOUR
                   ) -> tuple[datetime, datetime, datetime] | None:
    """(报告时刻, T+1 入场截止, T+2 开盘)—— T+1/T+2**都**取 `P` 里的下一个交易日。

    `P` = 湖里的交易日序列(紧凑 `YYYYMMDD` 升序);`i` = 分析日 D 在 `P` 里的位置。
    `i+2` 越界 → None(那一天的主尺本来就算不出来)。

    时刻常量(14:45 / 09:30 / Asia-Shanghai)从 `scan.overseas` import,不另造第二份 ——
    「来不来得及」这件事不能有两个答案。**不复用** `overseas.anchors` 是因为它的 T+2 是
    T+1 + 1 **自然日**,周五的 run 会锚到周六 09:30,而主尺 `gap_c1_o2` 用的是下一个交易日。
    """
    if i + 2 >= len(P):
        return None
    d0, d1, d2 = _as_date(P[i]), _as_date(P[i + 1]), _as_date(P[i + 2])
    return _cn(d0, report_hour), _cn(d1, ENTRY_CUTOFF), _cn(d2, T2_OPEN)


def day_intersects(local_date: date, tz: str, lo: datetime, hi: datetime) -> bool:
    """事件本地日 `[00:00, 次日 00:00)` 是否与半开区间 `(lo, hi]` 相交。

    `local_day_bounds` 走 IANA(DST 由 `ZoneInfo` 负责);写死 ET 偏移会在换季周错一小时,
    而那一小时正是「盘后财报落不落在持仓窗内」的分界。
    """
    start, end = oec.local_day_bounds(local_date, tz)
    return start < hi and end > lo


def event_flags(event: oec.ExternalEvent, report_ts: datetime, t1_close: datetime,
                t2_open: datetime) -> dict:
    """一条事件 × 一个分析日 → `{"window": 契约窗, "holding": bool, "pre_entry": bool}`。

    `window` 是**契约层**结论,一律走 `official_event_calendar.classify_window`:
    DATE_ONLY 在那里只可能得到 `date_risk` / `outside`,永远进不了两个窄窗(§10 第 5 条)。

    `holding` / `pre_entry` 是**普查日级旗**(模块 docstring「普查旗的定义」节):
    TIMED 用精确时刻落窗;DATE_ONLY 用「ET 本地日与该窗相交」。两者都不猜时刻。
    取消(`cancelled`)与 PIT 不可见(`first_seen_ts > report_ts`)的事件:两个旗一律 False
    —— 与契约层的 `outside` 同一判定序,不许旗比窗宽。
    """
    window = oec.classify_window(event, report_ts, t1_close, t2_open)
    if window == oec.WINDOW_OUTSIDE and (
            event.status == oec.STATUS_CANCELLED or event.first_seen_ts > oec.to_utc(report_ts)):
        return {"window": window, "holding": False, "pre_entry": False}
    if event.time_quality == oec.TIME_QUALITY_TIMED:
        sched = event.scheduled_at_utc
        return {"window": window,
                "holding": bool(t1_close < sched <= t2_open),
                "pre_entry": bool(report_ts < sched <= t1_close)}
    return {"window": window,
            "holding": day_intersects(event.local_date, event.timezone, t1_close, t2_open),
            "pre_entry": day_intersects(event.local_date, event.timezone, report_ts, t1_close)}


def flag_frame(P: list[str], events_by_family: dict[str, list], *,
               report_hour: time = REPORT_HOUR) -> pd.DataFrame:
    """交易日 × 族 → 旗表。index = 分析日(紧凑 `YYYYMMDD`),列 `<族>_holding` / `<族>_pre_entry`
    / `<族>_date_risk` / `<族>_n_timed` / `<族>_n_date_only`。

    只对 `i+2` 存在的分析日建行(主尺算不出来的日子不该有旗)。事件按本地日建索引,逐日只
    扫可能相交的那几天(±3 自然日),不做 N×M 全对比 —— 1090 日 × 数千事件会跑成分钟级。
    """
    rows: list[dict] = []
    by_day: dict[str, dict[date, list]] = {}
    for fam, evs in events_by_family.items():
        idx: dict[date, list] = {}
        for e in evs:
            idx.setdefault(e.local_date, []).append(e)
        by_day[fam] = idx
    for i, D in enumerate(P):
        anch = census_anchors(P, i, report_hour=report_hour)
        if anch is None:
            continue
        report_ts, t1_close, t2_open = anch
        row: dict = {"date": D}
        lo_day = _as_date(D) - timedelta(days=3)
        span_days = (t2_open.date() - lo_day).days + 3
        probe = [lo_day + timedelta(days=k) for k in range(span_days)]
        for fam, idx in by_day.items():
            holding = pre = risk = False
            n_timed = n_dateonly = 0
            for d in probe:
                for e in idx.get(d, ()):
                    got = event_flags(e, report_ts, t1_close, t2_open)
                    if got["holding"]:
                        holding = True
                        if e.time_quality == oec.TIME_QUALITY_TIMED:
                            n_timed += 1
                        else:
                            n_dateonly += 1
                    pre = pre or got["pre_entry"]
                    risk = risk or got["window"] == oec.WINDOW_DATE_RISK
            row[f"{fam}_holding"] = holding
            row[f"{fam}_pre_entry"] = pre
            row[f"{fam}_date_risk"] = risk
            row[f"{fam}_n_timed"] = n_timed
            row[f"{fam}_n_date_only"] = n_dateonly
        rows.append(row)
    return pd.DataFrame(rows).set_index("date") if rows else pd.DataFrame()


# ───────────────────────── 事件源(需网络;逐段缓存) ─────────────────────────


def _assumed_first_seen(local_date: date, tz: str = US_TZ) -> datetime:
    """`first_seen_ts` 的显式假设:本地日 00:00 往前 `ASSUMED_LEAD_DAYS` 天。**不是** PIT 主张。"""
    start, _ = oec.local_day_bounds(local_date, tz)
    return start - timedelta(days=ASSUMED_LEAD_DAYS)


def _months(start: str, end: str) -> list[tuple[str, str]]:
    """[(月首, 月末)] —— FRED 一次拉一个月(单月 ≈800 行 < 1000 的分页上限,一页拿完)。"""
    a, b = date.fromisoformat(start[:10]), date.fromisoformat(end[:10])
    out: list[tuple[str, str]] = []
    cur = date(a.year, a.month, 1)
    while cur <= b:
        nxt = date(cur.year + (cur.month == 12), (cur.month % 12) + 1, 1)
        out.append((max(cur, a).isoformat(), min(nxt - timedelta(days=1), b).isoformat()))
        cur = nxt
    return out


def fetch_fred_frame(start: str, end: str, *, cache_dir: Path | None = None,
                     offline: bool = False, fetch=None) -> pd.DataFrame:
    """FRED `releases/dates` 原始帧(逐月缓存 csv)。`offline=True` → 只读缓存,缺月就缺。

    `limit=FRED_PAGE_LIMIT` 是显式的:源模块的 `PAGE_LIMIT=10000` 超出 FRED 上限 1000,
    用默认值必炸(见模块 docstring 的上游缺陷备案)。
    """
    cache = Path(cache_dir) if cache_dir else CACHE_DIR
    cache.mkdir(parents=True, exist_ok=True)
    if fetch is None:
        from autoresearch.data.sources.fred_calendar import fetch_releases as fetch
    frames: list[pd.DataFrame] = []
    for lo, hi in _months(start, end):
        fp = cache / f"fred_{lo[:7]}.csv"
        if fp.exists():
            frames.append(pd.read_csv(fp, dtype=str))
            continue
        if offline:
            continue
        df = fetch(lo, hi, limit=FRED_PAGE_LIMIT)
        df = df.astype(str) if len(df) else pd.DataFrame(columns=["release_id", "release_name", "date"])
        df.to_csv(fp, index=False)
        frames.append(df)
    if not frames:
        return pd.DataFrame(columns=["release_id", "release_name", "date"])
    return pd.concat(frames, ignore_index=True)


def fred_events(frame: pd.DataFrame, *, families: dict[str, str] | None = None) -> dict[str, list]:
    """FRED 原始帧 → `{族: [ExternalEvent]}`,恒 `DATE_ONLY`(FRED 只给日期,不猜时刻)。

    **精确**发布名匹配(见 `FRED_EXACT_NAME`)—— 子串规则会把 `Research Consumer Price Index`
    算成 CPI、把每周的 `H.4.1` 算成 FOMC,两个都是真会发生的误判。
    """
    want = dict(families or FRED_EXACT_NAME)
    out: dict[str, list] = {f: [] for f in set(want.values())}
    if frame is None or not len(frame):
        return out
    seen: set[tuple[str, str]] = set()
    for row in frame.to_dict("records"):
        name = str(row.get("release_name") or "").strip().casefold()
        fam = want.get(name)
        if fam is None:
            continue
        raw = row.get("date")
        if raw in (None, "", "nan"):
            continue
        d = oec.parse_date(raw)
        key = (fam, d.isoformat())
        if key in seen:
            continue
        seen.add(key)
        rid = str(row.get("release_id") or "").strip() or "0"
        out[fam].append(oec.ExternalEvent(
            event_id=f"fred:{rid}:{d.isoformat()}", event_type="macro_release",
            subject=str(row.get("release_name") or fam), scheduled_at_utc=None,
            local_date=d, timezone=US_TZ, time_quality=oec.TIME_QUALITY_DATE_ONLY,
            source_url=f"https://fred.stlouisfed.org/release?rid={rid}",
            first_seen_ts=_assumed_first_seen(d), revision="fred-releases",
            status=oec.STATUS_SCHEDULED, mapped_symbols=()))
    return out


def fomc_events(start: str, end: str, *, path: Path | str | None = None) -> list:
    """`fomc_calendar.load_range` —— **只**它。绝不拿 FRED 的 `H.4.1` 冒充 FOMC。

    yaml 是 `verified_by: pending` → 恒 `DATE_ONLY`,故普查旗走日级口径,契约窗恒 `date_risk`。
    yaml 未登记的年份由 `load_range` 记 B 级降级并跳过,**不抛**:缺年是缺年,不是 0 个会议。
    """
    from autoresearch.data.sources import fomc_calendar
    evs = fomc_calendar.load_range(date.fromisoformat(start[:10]), date.fromisoformat(end[:10]),
                                  path=path)
    return [oec.ExternalEvent(
        event_id=e.event_id, event_type=e.event_type, subject=e.subject,
        scheduled_at_utc=e.scheduled_at_utc, local_date=e.local_date, timezone=e.timezone,
        time_quality=e.time_quality, source_url=e.source_url,
        first_seen_ts=(_assumed_first_seen(e.local_date, e.timezone)
                       if e.time_quality != oec.TIME_QUALITY_TIMED else e.first_seen_ts),
        revision=e.revision, status=e.status, mapped_symbols=e.mapped_symbols) for e in evs]


def mapped_symbols(as_of: str, *, roster: str = "load_map") -> tuple[list[str], str]:
    """映射票名单 → `(symbols, 说明)`。

    `roster="load_map"`(**主族口径**):走 `readthrough.load_map(as_of)`,只含
    「有效期内 + 证据齐 + 枚举合法 + 可交易」的条目。初版映射表全部 `pending_evidence`,
    所以它**返回空** —— 这不是 bug,是「没有真凭证的关系永远进不了任何报告」那条纪律在生效。
    `roster="raw"`(**探索口径**):读 yaml 里 `kind: company` 的全部 symbol,含未核实的。
    只能进探索表(FDR),**不得进主门**。
    """
    from autoresearch.data import readthrough as rt
    if roster == "load_map":
        m = rt.load_map(as_of)
        syms: list[str] = []
        for layer in ("codes", "industries"):
            for items in (m.get(layer) or {}).values():
                for it in items or ():
                    s = (it or {}).get("symbol")
                    if s and s not in syms and (it or {}).get("kind") == "company":
                        syms.append(s)
        return syms, "readthrough.load_map(可消费条目)"
    doc = rt.load_raw()
    syms = []
    for layer in ("industries", "codes"):
        for items in (doc.get(layer) or {}).values():
            for it in items or ():
                ok = isinstance(it, dict) and it.get("kind") == "company" and it.get("symbol")
                if ok and it["symbol"] not in syms:
                    syms.append(it["symbol"])
    return syms, "readthrough_map.yaml 原始 kind=company 名单(含 pending_evidence)"


def earnings_events(symbols, start: str, end: str, *, cache_dir: Path | None = None,
                    offline: bool = False, fetch=None) -> list:
    """映射票历史财报日 → `ExternalEvent`(恒 `DATE_ONLY`)。

    源是 yfinance `get_earnings_dates`,按 §3.1 是 **T3 聚合源** —— 它给的时刻(16:00 ET 之类)
    **不足以**把 DATE_ONLY 升成 TIMED(只有 T1 官方 / 发行人 IR 才可以)。所以只取它的 ET
    本地日,时刻记进 `subject` 备查,不写进 `scheduled_at_utc`。

    每只票缓存一份 csv;`offline=True` 只读缓存。取不到的票**跳过并留痕**(返回值不含它),
    不伪造空 —— 「这只票没有财报」和「这只票拉不到」不是一回事。
    """
    cache = Path(cache_dir) if cache_dir else CACHE_DIR
    cache.mkdir(parents=True, exist_ok=True)
    lo, hi = date.fromisoformat(start[:10]), date.fromisoformat(end[:10])
    out: list = []
    for sym in symbols or ():
        fp = cache / f"earnings_{str(sym).replace('/', '_')}.csv"
        df: pd.DataFrame | None = None
        if fp.exists():
            df = pd.read_csv(fp, dtype=str)
        elif not offline:
            try:
                df = (fetch or _yf_earnings)(sym)
            except Exception:                              # noqa: BLE001 - 单票取不到 = 跳过
                df = None
            if df is not None:
                df.to_csv(fp, index=False)
        if df is None or not len(df):
            continue
        for row in df.to_dict("records"):
            raw = str(row.get("ts") or "").strip()
            if not raw:
                continue
            try:
                ts = datetime.fromisoformat(raw)
            except ValueError:
                continue
            d = oec.local_date_of(oec.to_utc(ts), US_TZ) if ts.tzinfo else ts.date()
            if not lo <= d <= hi:
                continue
            out.append(oec.ExternalEvent(
                event_id=f"earn:{sym}:{d.isoformat()}", event_type="earnings",
                subject=f"{sym} 财报(yfinance 报时 {raw};T3 源,不升 TIMED)",
                scheduled_at_utc=None, local_date=d, timezone=US_TZ,
                time_quality=oec.TIME_QUALITY_DATE_ONLY,
                source_url=f"https://finance.yahoo.com/quote/{sym}/",
                first_seen_ts=_assumed_first_seen(d), revision="yfinance-earnings-dates",
                status=oec.STATUS_SCHEDULED, mapped_symbols=(str(sym),)))
    return out


def _yf_earnings(symbol: str) -> pd.DataFrame | None:                # pragma: no cover - 真网络
    import yfinance as yf
    df = yf.Ticker(symbol).get_earnings_dates(limit=100)
    if df is None or not len(df):
        return None
    return pd.DataFrame({"ts": [ts.isoformat() for ts in df.index]})


# ───────────────────────── 日级面板(只读 lake / _ledger) ─────────────────────────


def market_panel(P: list[str], *, lake_daily: Path | None = None) -> pd.DataFrame:
    """全市场可买截面 → **日期级等权组合**面板。

    每个分析日 D 一行:`mean_pp`(等权组合的 `gap_c1_o2`,pp)、`absmean_pp`(截面 `|gap|` 均值,
    离散度代理)、`oc_t1_pp`(T+1 开→收日内腿,**对照**)、`n`(截面票数)。

    口径与 `edge_census` 逐字同源:前向收益走 `factor_lab.forward_returns`、可交易折叠走
    `ruler.entry_tradable`、`|gap| > GAP_CLIP` 视作数据错置 NaN;截面 < `MIN_CROSS_SECTION`
    → 该日不进面板(不是记 0)。
    """
    from autoresearch.research import edge_census as ec
    piv = ec.load_lake_pivots(P, lake_daily)
    if not piv:
        return pd.DataFrame()
    o, c = piv.get("open"), piv.get("close")
    rows: list[dict] = []
    for i, D in enumerate(P):
        fr = ec.forward_frame(piv, P, D)
        if fr is None or i + 2 >= len(P):
            continue
        ok = _ruler.entry_tradable(fr, ruler_name=MAIN)
        g = pd.to_numeric(fr[MAIN], errors="coerce")[ok].dropna()
        if len(g) < MIN_CROSS_SECTION:
            continue
        d1 = P[i + 1]
        oc = np.nan
        if o is not None and c is not None and d1 in o.columns and d1 in c.columns:
            s = (c[d1] / o[d1] - 1.0).reindex(g.index).dropna()
            oc = float(s.mean() * 100) if len(s) else np.nan
        rows.append({"date": D, "n": int(len(g)), "mean_pp": float(g.mean() * 100),
                     "absmean_pp": float(g.abs().mean() * 100), "oc_t1_pp": oc,
                     "span_days": (_as_date(P[i + 2]) - _as_date(d1)).days})
    return pd.DataFrame(rows).set_index("date") if rows else pd.DataFrame()


def ledger_panel(role: str, *, ledger_csv: Path | None = None) -> pd.DataFrame:
    """结果账本 → finalist / 📌 的**日期级等权组合**面板(§10 第 4 条的第二张表)。

    读 `reports_<engine>/scan/_ledger/recommendations.csv`(`outcome.LEDGER_COLUMNS`),
    每个 `analysis_date` 内对该 role 的推荐票等权平均 —— 同日多票只算**一票**(一个日期
    = 一个观测),这正是 §10 第 2 条要的东西。

    `mode` / `src` 两列按账本自己的纪律**原样保留计数**(`n_shared` / `n_shadow`),不在这里
    做筛选:选择偏差已经够大了,再加一层静默过滤只会让读数更难解释。
    """
    from autoresearch.scan import outcome as oc_mod
    p = (Path(ledger_csv) if ledger_csv
         else ws.reports_root() / "scan" / oc_mod.LEDGER_DIRNAME / oc_mod.LEDGER_CSV)
    if not p.exists():
        return pd.DataFrame()
    df = pd.read_csv(p, dtype={"code": str}, low_memory=False)
    if "role" not in df.columns or MAIN not in df.columns:
        return pd.DataFrame()
    sub = df[df["role"].astype(str).str.strip() == role].copy()
    sub["g"] = pd.to_numeric(sub[MAIN], errors="coerce")
    sub = sub[sub["g"].notna() & (sub["g"].abs() <= _ruler.GAP_CLIP)]
    if not len(sub):
        return pd.DataFrame()
    rows: list[dict] = []
    for day, grp in sub.groupby(sub["analysis_date"].astype(str).str[:10]):
        rows.append({
            "date": day.replace("-", ""), "n": int(len(grp)),
            "mean_pp": float(grp["g"].mean() * 100),
            "absmean_pp": float(grp["g"].abs().mean() * 100),
            "oc_t1_pp": np.nan,
            "n_shared": int((grp.get("src", pd.Series(dtype=str)).astype(str) == "shared").sum()),
            "n_shadow": int((grp.get("mode", pd.Series(dtype=str)).astype(str) == "shadow").sum()),
        })
    return pd.DataFrame(rows).set_index("date").sort_index()


# ───────────────────────── 一族的读数 + 判读 ─────────────────────────


def _subperiod_diff(panel: pd.DataFrame, flag: pd.Series, value_col: str) -> dict:
    """子期(2022–23 / 2024–26)的原始差值 + 两侧日数。样本任一侧为空 → 该子期 None。"""
    out: dict = {}
    for label, lo, hi in SUBPERIODS:
        lo_c, hi_c = lo.replace("-", ""), hi.replace("-", "")
        sel = (panel.index >= lo_c) & (panel.index < hi_c)
        v = pd.to_numeric(panel.loc[sel, value_col], errors="coerce")
        f = flag[sel].astype(bool)
        keep = v.notna()
        d = _diff(v[keep].to_numpy(dtype=float), f[keep].to_numpy(dtype=bool)) if keep.any() else None
        out[label] = {"diff_pp": d, "n_event": int((f & keep).sum()),
                      "n_non": int((~f & keep).sum())}
    return out


def family_readout(panel: pd.DataFrame, flag: pd.Series, *, value_col: str = "mean_pp",
                   n_boot: int = N_BOOT, seed: int = SEED,
                   min_dates: int = MIN_EVENT_DATES) -> dict:
    """一族 × 一个人口 × 一个统计量 → 全部读数。

    **样本门先判**(与 `overnight_census.judge` 同一纪律):事件日数 < `min_dates` →
    `status="THIN"`,`p / ci / effect` **一律 None** —— 不足 20 日的族只报「样本不足」,
    不报 p 值(§11 D-0 验收行)。这不是「先算了再藏起来」,是压根不算。
    """
    v_all = pd.to_numeric(panel[value_col], errors="coerce")
    f_all = flag.reindex(panel.index).fillna(False).astype(bool)
    keep = v_all.notna()
    v = v_all[keep].to_numpy(dtype=float)
    f = f_all[keep].to_numpy(dtype=bool)
    n_event, n_non = int(f.sum()), int((~f).sum())
    base = {"value_col": value_col, "n_dates": int(len(v)), "n_event_dates": n_event,
            "n_non_dates": n_non,
            "mean_event_pp": float(v[f].mean()) if n_event else None,
            "mean_non_pp": float(v[~f].mean()) if n_non else None}
    if n_event < min_dates or n_non < min_dates:
        return {**base, "status": THIN, "diff_pp": None, "effect": None, "ci_lo": None,
                "ci_hi": None, "p_raw": None, "nw_t": None, "nw_p": None, "subperiods": None,
                "same_sign": None, "boot": None}
    boot = moving_block_diff(v, f, n_boot=n_boot, seed=seed)
    _b, nw_t, nw_p = newey_west_diff(v, f)
    subs = _subperiod_diff(panel[keep], f_all[keep], value_col)
    signs = [s["diff_pp"] for s in subs.values()]
    same_sign = (all(x is not None for x in signs)
                 and (all(x > 0 for x in signs) or all(x < 0 for x in signs)))
    return {**base, "status": "OK", "diff_pp": boot.point,
            "effect": standardized_effect(v, f), "ci_lo": boot.lo, "ci_hi": boot.hi,
            "p_raw": boot.p, "nw_t": nw_t, "nw_p": nw_p, "subperiods": subs,
            "same_sign": bool(same_sign), "boot": boot.as_dict()}


def judge_shadow(readout: dict, *, holm_p: float | None = None,
                 effect_floor: float = EFFECT_FLOOR) -> str:
    """Q7 门 → 四态。**只有全部四条同时成立**才叫「值得影子实验」:

        n_event_dates ≥ 20  ∧  Holm 校正 p < 0.05  ∧  |effect| ≥ effect_floor  ∧  两子期同号

    「显著负」= Holm p < 0.05 ∧ 差值 < 0(它同样是可用信息:风险可见性的理由)。
    其余「未证」—— **不是**「已证不存在」:区间跨 0 只说明这批样本没量出来。
    """
    if readout.get("status") != "OK":
        return THIN
    d, eff = readout.get("diff_pp"), readout.get("effect")
    if holm_p is None or d is None or eff is None:
        return UNPROVEN
    if holm_p < ALPHA and d > 0 and abs(eff) >= effect_floor and readout.get("same_sign"):
        return POS
    if holm_p < ALPHA and d < 0:
        return NEG
    return UNPROVEN


def apply_holm(readouts: dict[str, dict], *, m_total: int = len(MAIN_FAMILIES)) -> dict[str, float]:
    """主族 Holm 校正 → `{族: 校正 p}`。样本不足的族不参与(它没有 p),但 `m_total` 不缩水。"""
    named = [(k, r["p_raw"]) for k, r in readouts.items()
             if r.get("status") == "OK" and r.get("p_raw") is not None]
    if not named:
        return {}
    adj = holm([p for _k, p in named], m_total=m_total)
    return {k: a for (k, _p), a in zip(named, adj, strict=True)}


def apply_fdr(readouts: dict[str, dict]) -> dict[str, float]:
    """探索族 BH-FDR → `{族: q}`。走 `common.stats.bh_fdr`,**与主门分开**(§10 第 3 条)。"""
    named = [(k, r["p_raw"]) for k, r in readouts.items()
             if r.get("status") == "OK" and r.get("p_raw") is not None]
    if not named:
        return {}
    got = _stats.bh_fdr([p for _k, p in named])
    return {named[row["i"]][0]: row["q"] for row in got}


# ───────────────────────── 主流程 ─────────────────────────


def build_events(start: str, end: str, *, cache_dir: Path | None = None, offline: bool = False,
                 with_earnings: bool = True, as_of: str | None = None) -> tuple[dict[str, list], dict]:
    """→ `({族: [ExternalEvent]}, meta)`。缺源**记在 meta 里**,不静默当成 0 个事件。"""
    meta: dict = {"sources": {}, "assumed_lead_days": ASSUMED_LEAD_DAYS}
    fam: dict[str, list] = {}
    frame = fetch_fred_frame(start, end, cache_dir=cache_dir, offline=offline)
    meta["sources"]["fred"] = {"rows": int(len(frame)),
                              "months_cached": len(_months(start, end)) if len(frame) else 0}
    for k, v in fred_events(frame).items():
        fam[k] = v
    fam["FOMC"] = fomc_events(start, end)
    meta["sources"]["fomc"] = {"events": len(fam["FOMC"]),
                              "note": "fomc_calendar.yaml;未登记的年份由 load_range 记 B 级降级并跳过"}
    fam["EARNINGS"], fam["EARNINGS_RAW"] = [], []
    as_of = as_of or end
    syms_main, note_main = mapped_symbols(as_of, roster="load_map")
    syms_raw, note_raw = mapped_symbols(as_of, roster="raw")
    meta["sources"]["earnings"] = {"roster_main": syms_main, "roster_main_note": note_main,
                                  "roster_raw": syms_raw, "roster_raw_note": note_raw,
                                  "fetched": bool(with_earnings)}
    if with_earnings:
        fam["EARNINGS"] = earnings_events(syms_main, start, end, cache_dir=cache_dir, offline=offline)
        fam["EARNINGS_RAW"] = earnings_events(syms_raw, start, end, cache_dir=cache_dir,
                                              offline=offline)
    meta["event_counts"] = {k: len(v) for k, v in fam.items()}
    meta["time_quality"] = {k: dict(pd.Series([e.time_quality for e in v]).value_counts())
                            if v else {} for k, v in fam.items()}
    return fam, meta


def run_census(*, start: str = WINDOW_START, end: str = WINDOW_END, cache_dir: Path | None = None,
               offline: bool = False, with_earnings: bool = True, n_boot: int = N_BOOT,
               seed: int = SEED, lake_daily: Path | None = None,
               ledger_csv: Path | None = None) -> dict:
    """全流程 → 一个 JSON 安全的 dict。**只读**;任何分支都不写 run 目录 / staging / lake。"""
    from autoresearch.research import edge_census as ec
    cache = Path(cache_dir) if cache_dir else CACHE_DIR
    P_all = ec.lake_trade_days(lake_daily)
    lo, hi = start.replace("-", ""), end.replace("-", "")
    P = [d for d in P_all if lo <= d <= hi]
    events, meta = build_events(start, end, cache_dir=cache, offline=offline,
                                with_earnings=with_earnings)
    flags = flag_frame(P, events)
    panels = {POP_MARKET: market_panel(P, lake_daily=lake_daily),
              POP_FINALIST: ledger_panel("finalist", ledger_csv=ledger_csv),
              POP_PINNED: ledger_panel("pinned", ledger_csv=ledger_csv)}
    meta.update({"start": start, "end": end, "trade_days": len(P), "main_ruler": MAIN,
                 "block": BLOCK, "n_boot": n_boot, "seed": seed, "alpha": ALPHA,
                 "min_event_dates": MIN_EVENT_DATES, "effect_floor": EFFECT_FLOOR,
                 "panel_days": {k: int(len(v)) for k, v in panels.items()},
                 "flag_days": int(len(flags))})

    out: dict = {"meta": meta, "populations": {}}
    for pop, panel in panels.items():
        if panel is None or not len(panel) or not len(flags):
            out["populations"][pop] = {"status": "NO_PANEL", "n_dates": 0}
            continue
        idx = [d for d in panel.index if d in flags.index]
        pan = panel.loc[idx]
        blk = {"status": "OK", "n_dates": int(len(pan)),
               "date_range": [pan.index.min(), pan.index.max()] if len(pan) else None,
               "families": {}, "dispersion": {}}
        main_r: dict[str, dict] = {}
        expl_r: dict[str, dict] = {}
        disp_r: dict[str, dict] = {}
        for famset, bucket in ((MAIN_FAMILIES, main_r), (EXPLORATORY_FAMILIES, expl_r)):
            for f in famset:
                col = f"{f}_holding"
                if col not in flags.columns:
                    bucket[f] = {"status": THIN, "n_event_dates": 0,
                                 "note": "该族无事件源产出(见 meta.sources)"}
                    continue
                fl = flags.loc[idx, col]
                bucket[f] = family_readout(pan, fl, value_col="mean_pp", n_boot=n_boot, seed=seed)
                if f in MAIN_FAMILIES:
                    disp_r[f] = family_readout(pan, fl, value_col="absmean_pp",
                                               n_boot=n_boot, seed=seed)
                if "oc_t1_pp" in pan.columns and pan["oc_t1_pp"].notna().any():
                    bucket[f]["control_oc_t1"] = family_readout(
                        pan, fl, value_col="oc_t1_pp", n_boot=n_boot, seed=seed)
        if "span_days" in pan.columns:
            short = pan[pd.to_numeric(pan["span_days"], errors="coerce") <= SPAN_SHORT_MAX]
            blk["span_profile"] = {
                "short_days": int(len(short)), "long_days": int(len(pan) - len(short)),
                "sd_short_pp": float(short["mean_pp"].std(ddof=1)) if len(short) > 1 else None,
                "sd_long_pp": (float(pan.drop(index=short.index)["mean_pp"].std(ddof=1))
                               if len(pan) - len(short) > 1 else None)}
            sens: dict[str, dict] = {}
            for f in (*MAIN_FAMILIES, *EXPLORATORY_FAMILIES):
                col = f"{f}_holding"
                if col not in flags.columns or not len(short):
                    continue
                sens[f] = family_readout(short, flags.loc[short.index, col],
                                         value_col="mean_pp", n_boot=n_boot, seed=seed)
                sens[f]["n_long_event_dates"] = int(
                    flags.loc[[d for d in pan.index if d not in short.index], col].sum())
            blk["sensitivity_short_window"] = sens
        holm_main = apply_holm(main_r)
        holm_disp = apply_holm(disp_r)
        q_expl = apply_fdr(expl_r)
        for f, r in main_r.items():
            r["holm_p"] = holm_main.get(f)
            r["verdict"] = judge_shadow(r, holm_p=holm_main.get(f))
        for f, r in disp_r.items():
            r["holm_p"] = holm_disp.get(f)
            r["verdict"] = judge_shadow(r, holm_p=holm_disp.get(f))
        for f, r in expl_r.items():
            r["fdr_q"] = q_expl.get(f)
            r["verdict"] = judge_shadow(r, holm_p=q_expl.get(f))
        blk["families"] = main_r
        blk["exploratory"] = expl_r
        blk["dispersion"] = disp_r
        blk["holm_m"] = len(MAIN_FAMILIES)
        out["populations"][pop] = blk
    return out


# ───────────────────────── 渲染 ─────────────────────────

_FOOT = ("_口径预注册见模块 docstring 与设计稿 §10 D-0。**不显著 ≠ 无 alpha**;"
         "finalist / 📌 两表有选择偏差,**不得外推全市场**。DATE_ONLY 事件的契约窗恒 `date_risk`,"
         "普查旗是日级「ET 本地日与持仓窗相交」,不是精确窗。_")


def _fmt(x, nd: int = 3, suffix: str = "") -> str:
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "—"
    return f"{x:+.{nd}f}{suffix}" if nd else f"{x}{suffix}"


def _row(f: str, r: dict, *, pkey: str = "holm_p") -> str:
    if r.get("status") != "OK":
        return (f"| {FAMILY_LABEL.get(f, f)} | {r.get('n_event_dates', 0)} | — | — | — | — | "
                f"{THIN}({r.get('note') or f'事件日 < {MIN_EVENT_DATES}'}) |")
    subs = r.get("subperiods") or {}
    sig = " / ".join(_fmt((subs.get(lab) or {}).get("diff_pp"), 3) for lab, _a, _b in SUBPERIODS)
    ci = f"[{_fmt(r.get('ci_lo'))}, {_fmt(r.get('ci_hi'))}]"
    return (f"| {FAMILY_LABEL.get(f, f)} | {r['n_event_dates']}/{r['n_non_dates']} | "
            f"{_fmt(r.get('diff_pp'))} | {_fmt(r.get('effect'), 2)} | {ci} | "
            f"{_fmt(r.get(pkey), 4, '')}(raw {_fmt(r.get('p_raw'), 4)}) | "
            f"{r.get('verdict')}·子期 {sig} |")


def render(doc: dict) -> str:
    meta = doc.get("meta", {})
    lines = ["# D-0 · 海外事件窗普查读数(机器产出)", "",
             f"- 窗口 {meta.get('start')} → {meta.get('end')} · 交易日 {meta.get('trade_days')} · "
             f"主尺 `{meta.get('main_ruler')}` · block {meta.get('block')} · "
             f"bootstrap {meta.get('n_boot')}(seed {meta.get('seed')})· "
             f"最小事件日 {meta.get('min_event_dates')} · Holm m={len(MAIN_FAMILIES)}",
             f"- 事件计数 {meta.get('event_counts')}",
             f"- time_quality {meta.get('time_quality')}", ""]
    for pop in POPULATIONS:
        blk = (doc.get("populations") or {}).get(pop) or {}
        title = {POP_MARKET: "全市场可买截面", POP_FINALIST: "finalist(选择偏差)",
                 POP_PINNED: "📌 持仓(选择偏差)"}[pop]
        lines += [f"## {title}", ""]
        if blk.get("status") != "OK":
            lines += [f"_无面板({blk.get('status')})。_", ""]
            continue
        lines += [f"- 可算日 {blk['n_dates']} · 区间 {blk.get('date_range')}", "",
                  "### 主族(Holm)", "",
                  "| 族 | 事件日/非事件日 | 差值 pp | effect | 95% CI | Holm p | 判读 |",
                  "|---|---:|---:|---:|---|---|---|"]
        for f in MAIN_FAMILIES:
            lines.append(_row(f, (blk.get("families") or {}).get(f, {"status": THIN})))
        lines += ["", "### `|gap|` 离散度(同族,另一统计量)", "",
                  "| 族 | 事件日/非事件日 | 差值 pp | effect | 95% CI | Holm p | 判读 |",
                  "|---|---:|---:|---:|---|---|---|"]
        for f in MAIN_FAMILIES:
            lines.append(_row(f, (blk.get("dispersion") or {}).get(f, {"status": THIN})))
        lines += ["", "### 探索族(FDR,**不进主门**)", "",
                  "| 族 | 事件日/非事件日 | 差值 pp | effect | 95% CI | FDR q | 判读 |",
                  "|---|---:|---:|---:|---|---|---|"]
        for f in EXPLORATORY_FAMILIES:
            lines.append(_row(f, (blk.get("exploratory") or {}).get(f, {"status": THIN}),
                              pkey="fdr_q"))
        lines.append("")
        sens = blk.get("sensitivity_short_window")
        if sens:
            sp = blk.get("span_profile") or {}
            lines += [f"### 后验稳健性(**非预注册**):剔除跨假长窗(span > {SPAN_SHORT_MAX} 自然日)", "",
                      f"- 长窗 {sp.get('long_days')} 日 / 短窗 {sp.get('short_days')} 日 · "
                      f"日均值 sd 长 {_fmt(sp.get('sd_long_pp'), 3)}pp vs 短 "
                      f"{_fmt(sp.get('sd_short_pp'), 3)}pp",
                      "", "| 族 | 短窗事件日/非事件日 | 差值 pp | effect | 95% CI | 原始 p | 其中长窗事件日 |",
                      "|---|---:|---:|---:|---|---|---:|"]
            for f in (*MAIN_FAMILIES, *EXPLORATORY_FAMILIES):
                r = sens.get(f)
                if not r:
                    continue
                if r.get("status") != "OK":
                    lines.append(f"| {FAMILY_LABEL.get(f, f)} | {r.get('n_event_dates', 0)} | — | — "
                                 f"| — | — | {r.get('n_long_event_dates', 0)} |")
                    continue
                lines.append(
                    f"| {FAMILY_LABEL.get(f, f)} | {r['n_event_dates']}/{r['n_non_dates']} | "
                    f"{_fmt(r.get('diff_pp'))} | {_fmt(r.get('effect'), 2)} | "
                    f"[{_fmt(r.get('ci_lo'))}, {_fmt(r.get('ci_hi'))}] | "
                    f"{_fmt(r.get('p_raw'), 4)} | {r.get('n_long_event_dates', 0)} |")
            lines.append("")
    lines += [_FOOT, ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="D-0 海外事件窗普查(只读;不写生产)")
    ap.add_argument("--start", default=WINDOW_START)
    ap.add_argument("--end", default=WINDOW_END)
    ap.add_argument("--offline", action="store_true", help="只用本地缓存,零网络")
    ap.add_argument("--no-earnings", action="store_true", help="跳过 yfinance 财报日取数")
    ap.add_argument("--boot", type=int, default=N_BOOT)
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--cache", default=None)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    doc = run_census(start=a.start, end=a.end, offline=a.offline,
                     with_earnings=not a.no_earnings, n_boot=a.boot, seed=a.seed,
                     cache_dir=Path(a.cache) if a.cache else None)
    out = Path(a.out) if a.out else ws.reports_root() / "research" / "overseas_event_census.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(doc), encoding="utf-8")
    (out.parent / "_overseas_event_census.json").write_text(
        json.dumps(doc, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(f"[done] → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
