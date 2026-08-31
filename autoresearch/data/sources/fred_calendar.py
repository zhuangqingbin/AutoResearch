#!/usr/bin/env python3
"""FRED 发布日历(`fred/releases/dates`)—— 海外宏观事件的**日期**源。

design: `docs/specs/2026-08-28-external-evidence-expansion-design.md` §5.3 / §9 / §10(D-0)。
契约见 `official_event_calendar.ExternalEvent`。

## 三件必须先说清的事

1. **不是 keyless**:走既有 `FRED_API_KEY`(`.env` 由 `autoresearch/__init__.py` 的
   `load_dotenv` 装载)。缺 key → `FredNotConfiguredError`(沿用 `dataflows.fred.get_api_key`),
   由 `*_safe` 包装成 B 级降级,**不阻断**。
2. **只有日期,没有时刻** → 一律 `time_quality=DATE_ONLY`。FRED 的 `release_dates` 是「哪天发」,
   不是「几点发」。CPI 08:30 ET 是**常识**不是**本源数据** —— 把常识写成 `scheduled_at_utc`
   就是伪造精确度,而这个精确度会直接决定事件进不进持仓隔夜窄窗(§2 铁律)。真时刻要走
   `official_event_calendar.merge_time_quality`(T1 官方源确认后才升 TIMED)。
3. **支持历史**:D-0 普查(2022-03 → 2026-08 每个 A 股交易日打旗)要的正是历史发布日。
   FRED 用 `realtime_start` / `realtime_end` 框定区间,所以 `fetch_releases(start, end)` 的
   两个参数直接映射过去。但**历史真实发生日只能用于事后事件研究**,不能倒推「当时可知」
   —— `first_seen_ts` 由调用方显式给,本模块绝不拿 `now()` 冒充。

## 湖纪律(§9)

lake 键 `fred_calendar/<date>`,B 级契约。写湖一律走 `cache.get_or_fetch`(剥 `fields`),本模块
只负责取原始帧:**形态不对就抛**,不返回空帧假装成功(空帧一旦入湖,这一天永远是空)。
「源成功但真空」与「请求/解析失败」由 `fetch_releases`(抛)与 `fetch_releases_safe`(记账 +
返回空 + `status` 区分)分开表达 —— 两者绝不都落成空数组。
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime

import pandas as pd

from autoresearch.data.sources.official_event_calendar import (
    STATUS_SCHEDULED,
    TIME_QUALITY_DATE_ONLY,
    TZ_NEW_YORK,
    ExternalEvent,
    parse_date,
    to_utc,
)

ENDPOINT = "fred_calendar"
FRED_API_BASE = "https://api.stlouisfed.org/fred"
RELEASES_DATES_PATH = "releases/dates"

REQUEST_TIMEOUT = 30
#: FRED `releases/dates` 的 **limit 上限是 1000**(超了直接 400
#: `Variable limit is not between 1 and 1000`)。2026-08-29 D-0 普查逮到:这里原本写 10000,
#: 于是**默认参数下 `fetch_releases` 必炸**,而调用方 `scan/overseas.collect` 是 B 级
#: try/except —— 生产日历里 FRED 一条都没有,且**不报警**。「降级不留痕才是真病」的原样复刻。
PAGE_LIMIT = 1000
MAX_PAGES = 50              # 分页护栏:防接口改语义时无限翻页

# 发布名 → D-0 预注册主事件族(§10 统计口径:主族只有四个,其余只作探索性展示,不进主门)。
# 匹配是**子串**,大小写不敏感;FRED 的 release_name 是稳定的英文长名。
_FAMILY_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("cpi", ("consumer price index",)),
    ("employment", ("employment situation",)),
    ("pce", ("personal income and outlays", "personal consumption expenditures")),
    ("gdp", ("gross domestic product",)),
    # ⚠️ **不要**把 `h.4.1`(Factors Affecting Reserve Balances)放进来:它是**每周四**的
    # 准备金余额报表,不是议息会议。2026-08-29 D-0 普查算过:按它建族会凭空造出约 230 个
    # 「FOMC 日」,把一个一年 8 次的事件稀释成每周事件 —— 族一旦被污染,后面所有统计都白做。
    # FOMC 的真名录走 `fomc_calendar.yaml`(官方年度页物化),不靠发布名猜。
    ("fomc", ("federal open market committee",)),
)


class FredCalendarError(RuntimeError):
    """FRED 日历取数 / 解析失败(**不是**「那几天没有发布」)。"""


@dataclass(frozen=True)
class FetchOutcome:
    """B 级降级要能区分「源成功但真空」与「请求 / 解析失败」(§9),故不用裸 DataFrame 表达。

    `status`:`"OK"`(有行)| `"EMPTY"`(源成功,区间内确实没有发布)| `"FAILED"`(请求 / 解析炸)。
    `reason`:失败原因(仅 FAILED)。二者绝不都落成空数组 —— 那是「降级不留痕」的原形。
    """

    rows: pd.DataFrame
    status: str
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.status != "FAILED"


def classify_family(release_name: str) -> str:
    """发布名 → D-0 预注册族(`cpi|employment|pce|gdp|fomc|other`)。纯函数,无网络。"""
    s = str(release_name or "").strip().lower()
    for family, needles in _FAMILY_RULES:
        if any(n in s for n in needles):
            return family
    return "other"


def _compact(d) -> str:
    """date / `YYYY-MM-DD` / `YYYYMMDD` → FRED 要的 `YYYY-MM-DD`。"""
    if isinstance(d, date) and not isinstance(d, datetime):
        return d.isoformat()
    s = str(d).strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return date.fromisoformat(s).isoformat()


def _default_request(path: str, params: dict) -> dict:
    """走既有 FRED dataflow 的请求 / 鉴权(唯一事实源,别再写第二份 api_key 读取)。"""
    from autoresearch.dataflows.fred import _request

    return _request(path, params)


def fetch_releases(
    start,
    end,
    *,
    include_no_data: bool = True,
    request=None,
    limit: int = PAGE_LIMIT,
) -> pd.DataFrame:
    """`fred/releases/dates` 的原始帧,列 `release_id / release_name / date`。

    `start` / `end` → `realtime_start` / `realtime_end`(FRED 用 realtime 区间框定返回的发布日,
    这正是历史查询的入口 —— D-0 要 2022-03 → 2026-08 就直接传那两个端点)。

    `include_no_data=True`(默认):**未来**的待发布日只有开这个开关才回得来(默认 false 只给
    已有数据的发布日),FRED 也只在开它时返回 `release_name`。关它 = 日历里永远没有明天。

    `request`:可注入的 `request(path, params) -> dict`,单测用(**测试禁止真网络**)。

    形态不对就抛 `FredCalendarError`,不返回空帧假装成功 —— 空帧入湖会把这一天永久钉成空
    (「cache 空 pickle 永不重拉」家训的 parquet 同族)。**区间内确实没有发布** = 合法空,
    由返回 0 行的正常路径表达,与「炸了」形态不同。
    """
    req = request or _default_request
    rs, re_ = _compact(start), _compact(end)
    if rs > re_:
        raise FredCalendarError(f"start {rs} 晚于 end {re_}")

    rows: list[dict] = []
    offset = 0
    for _ in range(MAX_PAGES):
        params = {
            "realtime_start": rs,
            "realtime_end": re_,
            "include_release_dates_with_no_data": "true" if include_no_data else "false",
            "order_by": "release_date",
            "sort_order": "asc",
            "limit": int(limit),
            "offset": offset,
        }
        payload = req(RELEASES_DATES_PATH, params)
        if not isinstance(payload, dict):
            raise FredCalendarError(
                f"FRED {RELEASES_DATES_PATH} 返回体不是 dict,而是 {type(payload).__name__}"
                f" —— 多半是接口改版 / 限流,不是「这段时间没有发布」")
        page = payload.get("release_dates")
        if page is None:
            raise FredCalendarError(
                f"FRED {RELEASES_DATES_PATH} 返回体缺 `release_dates` 键(keys={sorted(payload)[:8]})")
        if not isinstance(page, list):
            raise FredCalendarError(f"`release_dates` 不是 list,而是 {type(page).__name__}")
        rows.extend(page)
        if len(page) < int(limit):
            break
        offset += len(page)
    else:
        raise FredCalendarError(f"分页超过 {MAX_PAGES} 页仍未结束 —— 接口语义可能变了,拒绝继续翻")

    df = pd.DataFrame(rows)
    if len(df) and not {"release_id", "date"} <= set(df.columns):
        raise FredCalendarError(f"返回行缺必需列(实际列 {sorted(df.columns)})")
    return df


def fetch_releases_safe(start, end, **kwargs) -> FetchOutcome:
    """B 级封装:炸了 → `record_degradation` 记账 + `status="FAILED"`,**不抛**(§0 全部 B 级)。"""
    from autoresearch.data.contracts import record_degradation

    try:
        df = fetch_releases(start, end, **kwargs)
    except Exception as exc:                                       # noqa: BLE001
        reason = f"{type(exc).__name__}: {exc}"
        record_degradation(ENDPOINT, f"取数失败 → 海外日历缺席({reason})",
                           key=f"{_compact(start)}~{_compact(end)}")
        return FetchOutcome(pd.DataFrame(), "FAILED", reason)
    if not len(df):
        record_degradation(ENDPOINT, "区间内 0 条发布(源成功,真实空)",
                           key=f"{_compact(start)}~{_compact(end)}", kind="legit_empty")
        return FetchOutcome(df, "EMPTY")
    return FetchOutcome(df, "OK")


def releases_to_events(
    df: pd.DataFrame,
    *,
    first_seen_ts: datetime,
    revision: str = "fred-releases",
    families: Iterable[str] | None = None,
) -> list[ExternalEvent]:
    """原始帧 → `ExternalEvent` 列表,**恒 `DATE_ONLY`**(FRED 不给时刻)。

    `first_seen_ts`:这批日历**被我们看到**的时刻,由调用方显式给(夜间预热的墙上时钟 /
    回放的冻结时刻)。本模块绝不 `now()` —— PIT 时刻靠猜就等于没有 PIT。

    `families`:只保留这些 D-0 主族(`classify_family` 口径);None = 全留。

    `timezone` 恒 `America/New_York`:美国宏观发布的本地日按 ET 算,拿 UTC 日或北京日去对
    会在跨日边界系统性错一天。
    """
    seen = to_utc(first_seen_ts)
    want = {str(f).lower() for f in families} if families is not None else None
    out: list[ExternalEvent] = []
    for row in df.to_dict("records"):
        rid = str(row.get("release_id", "")).strip()
        raw_date = row.get("date")
        if not rid or raw_date in (None, ""):
            continue
        name = str(row.get("release_name") or "").strip() or f"FRED release {rid}"
        family = classify_family(name)
        if want is not None and family not in want:
            continue
        local_date = parse_date(raw_date)
        out.append(ExternalEvent(
            event_id=f"fred:{rid}:{local_date.isoformat()}",
            event_type="macro_release",
            subject=name,
            scheduled_at_utc=None,                     # ← 恒 None:只有日期,不猜时刻
            local_date=local_date,
            timezone=TZ_NEW_YORK,
            time_quality=TIME_QUALITY_DATE_ONLY,
            source_url=f"https://fred.stlouisfed.org/release?rid={rid}",
            first_seen_ts=seen,
            revision=revision,
            status=STATUS_SCHEDULED,
            mapped_symbols=(),
        ))
    return out


def fetch_events(
    start,
    end,
    *,
    first_seen_ts: datetime,
    families: Iterable[str] | None = None,
    **kwargs,
) -> tuple[list[ExternalEvent], FetchOutcome]:
    """取数 + 转契约的一步式入口 → `(events, outcome)`。失败时 events 为空、`outcome.status="FAILED"`。"""
    outcome = fetch_releases_safe(start, end, **kwargs)
    if outcome.status != "OK":
        return [], outcome
    return releases_to_events(outcome.rows, first_seen_ts=first_seen_ts, families=families), outcome


__all__ = [
    "ENDPOINT", "FRED_API_BASE", "RELEASES_DATES_PATH", "FredCalendarError", "FetchOutcome",
    "classify_family", "fetch_releases", "fetch_releases_safe", "releases_to_events", "fetch_events",
]
