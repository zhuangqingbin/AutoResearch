#!/usr/bin/env python3
"""统一外源事件契约 + 官方时刻补齐(框架)—— 所有日历源(FRED / FOMC / 官方 IR)共用。

design: `docs/specs/2026-08-28-external-evidence-expansion-design.md` §2(统一事件契约 yaml)、
§3.1(来源分级)、§9(数据源规格 + 湖纪律)。本模块是 **D-1 的契约本体**:`fred_calendar.py`、
`fomc_calendar.py` 以及 scan 侧的 `overseas_calendar` 消费口都 import 这里的 `ExternalEvent`。

## 为什么需要一个契约(而不是各源各自的 dict)

主尺是 `gap_c1_o2`(T+1 15:00 CST 买 → T+2 09:30 CST 卖),这个持仓窗**横跨一整个美股交易日**
(§2)。要回答「这一夜有没有已知的海外事件」,必须同时回答三件事,少一件就会造出假精确:

1. **发生在什么绝对时刻**(`scheduled_at_utc`)—— 只有 IANA 时区换算能过 DST,写死 UTC+8 / ET
   偏移在 3 月 / 11 月的换季周必错一小时,而那一小时恰好是「盘后财报落不落在持仓窗内」的分界。
2. **这个时刻有多可靠**(`time_quality`)—— FRED `releases/dates` 只给**日期**。把它当作
   「盘后 AMC」或「盘前 BMO」是**猜**;猜出来的窄窗会让一条本该是「当日风险」的提示伪装成
   「持仓隔夜窗内的定时事件」。故 `DATE_ONLY` 永远只能是 `date_risk`,不得进两个窄窗。
3. **当时知不知道**(`first_seen_ts` + `revision` + `status`)—— 历史真实发生日只能用于**事后**
   事件研究(D-0),不能倒推「生产时点一定可知」。改期(rescheduled)保留旧 revision,展示层
   只出 cutoff 时点可见的最新版本。

## 铁律(§2 逐字)

- 只有 `TIMED` 且 `first_seen_ts <= decision_cutoff` 的事件可进入两个窄窗。
- `DATE_ONLY` 只能显示为「当日风险」,**不得猜 AMC / BMO**,不得触发精确窗口 tripwire。
- 任何一行都只是**人工复核提示**,不自动产生否决 / 仓位 / 评级动作(§0 事件链用途 = 风险可见性)。
- 时区一律 IANA(`Asia/Shanghai` / `America/New_York`),必须覆盖 DST、美国休市、A 股休市。

## 本模块**不**做的事

- 不判断窗口归属的日历锚:`classify_window` 收三个**外部传入**的时间锚(报告时刻 / T+1 收盘 /
  T+2 开盘),A 股交易日历由 scan 侧按 `trade_days` 给。日历所有权留在 scan,契约层不复制一份。
- 不抓 Fed / BLS / BEA 官方时刻:`fetch_official_times` 是 `NotImplementedError`(§9 该行标的
  就是「待探针」)。**宁可保持 DATE_ONLY,也不编造未验证的抓取逻辑** —— 假 TIMED 比没有更坏。
"""
from __future__ import annotations

import dataclasses
import ipaddress
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

UTC = timezone.utc

# ───────────────────────── 枚举(契约 yaml 逐字) ─────────────────────────

EVENT_TYPES: tuple[str, ...] = ("macro_release", "fomc", "earnings", "regulator", "other")

TIME_QUALITY_TIMED = "TIMED"
TIME_QUALITY_DATE_ONLY = "DATE_ONLY"
TIME_QUALITY_UNKNOWN = "UNKNOWN"
TIME_QUALITIES: tuple[str, ...] = (TIME_QUALITY_TIMED, TIME_QUALITY_DATE_ONLY, TIME_QUALITY_UNKNOWN)

STATUS_SCHEDULED = "scheduled"
STATUS_RESCHEDULED = "rescheduled"
STATUS_CANCELLED = "cancelled"
STATUS_RELEASED = "released"
STATUSES: tuple[str, ...] = (STATUS_SCHEDULED, STATUS_RESCHEDULED, STATUS_CANCELLED, STATUS_RELEASED)

WINDOW_PRE_ENTRY = "pre_entry"
WINDOW_HOLDING_OVERNIGHT = "holding_overnight"
WINDOW_DATE_RISK = "date_risk"
WINDOW_OUTSIDE = "outside"
WINDOWS: tuple[str, ...] = (WINDOW_PRE_ENTRY, WINDOW_HOLDING_OVERNIGHT, WINDOW_DATE_RISK, WINDOW_OUTSIDE)

TZ_SHANGHAI = "Asia/Shanghai"
TZ_NEW_YORK = "America/New_York"

# 美股常规时段(ET 本地钟面;DST 由 ZoneInfo 负责,**禁止**写死 UTC 偏移)。
US_REGULAR_OPEN = time(9, 30)
US_REGULAR_CLOSE = time(16, 0)


class EventContractError(ValueError):
    """事件契约违约 —— 字段缺失 / 枚举非法 / 时刻与 time_quality 自相矛盾。"""


# ───────────────────────── 共享 URL 原语(§3.1 入账前 canonicalize) ─────────────────────────
#
# 放在契约层而不是某个具体源里:事件的 `source_url`、RSS 发现项的 URL、映射表的 `evidence_url`
# 三处用的是**同一把尺**。`gnews_rss` / `readthrough` 从这里 re-export,不各写一份
#(「发现一处必 grep 全部消费者」的反面教训:同族规则散成三份,修一处永远漏两处)。

_ALLOWED_SCHEMES = ("http", "https")

# 追踪参数:去掉后语义不变,但留着会让同一篇原文产生 N 个「不同」URL(去重失效 + 隐私外泄)。
_TRACKING_PARAM_PREFIXES = ("utm_", "at_", "pk_", "mtm_", "hsa_", "_hs")
_TRACKING_PARAMS = frozenset({
    "gclid", "gclsrc", "dclid", "fbclid", "msclkid", "yclid", "igshid", "twclid",
    "ocid", "oc", "cmpid", "campaign_id", "mc_cid", "mc_eid", "_hsenc", "_hsmi",
    "ref", "ref_src", "referrer", "spm", "share_token", "s_cid", "src", "source",
    "sourceid", "trk", "trkCampaign", "cid", "ncid", "xtor", "vero_id", "vero_conv",
})

# 私网 / 环回 / 保留域名后缀:接口不该指向本机或内网,指向了就是 SSRF 面(或配置事故)。
_PRIVATE_HOST_SUFFIXES = (".localhost", ".local", ".internal", ".intranet", ".home.arpa")
_PRIVATE_HOSTS = frozenset({"localhost", "ip6-localhost", "ip6-loopback"})


class UnsafeURLError(ValueError):
    """URL 不可入账:非公网 http(s) / 环回 / 私网 / `file:` / 带 userinfo。"""


def _strip_tracking(query: str) -> str:
    """去掉追踪参数,保留其余参数并按 key 稳定排序(同一原文 → 同一 canonical URL)。"""
    kept = [
        (k, v) for k, v in parse_qsl(query, keep_blank_values=True)
        if k.lower() not in _TRACKING_PARAMS
        and not any(k.lower().startswith(p) for p in _TRACKING_PARAM_PREFIXES)
    ]
    kept.sort(key=lambda kv: (kv[0], kv[1]))
    return urlencode(kept, doseq=False)


def _reject_private_host(host: str) -> None:
    h = (host or "").strip().lower().rstrip(".")
    if not h:
        raise UnsafeURLError("URL 无 host")
    if h in _PRIVATE_HOSTS or any(h.endswith(sfx) for sfx in _PRIVATE_HOST_SUFFIXES):
        raise UnsafeURLError(f"URL host 指向本机 / 内网域:{host!r}")
    literal = h[1:-1] if h.startswith("[") and h.endswith("]") else h
    try:
        ip = ipaddress.ip_address(literal)
    except ValueError:
        return                                     # 普通域名 —— DNS 解析后的私网由抓取层再判
    if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
            or ip.is_multicast or ip.is_unspecified):
        raise UnsafeURLError(f"URL host 是私网 / 保留地址:{ip}")


def canonicalize_url(url: str, *, resolve=None, allowed_hosts: Sequence[str] | None = None) -> str:
    """URL 入账前的统一规整 —— **拒绝**非公网 http(s),去追踪参数 / fragment(§3.1)。

    `resolve`:可选的重定向解析器 `resolve(url) -> str`(聚合页 → canonical 原文)。**默认 None
    = 不联网**:本函数是纯字符串规整,单测零网络。跟随重定向是调用方的显式选择,且跟随后的
    结果会再走一遍本函数(允许域校验 + 私网拒绝),不能因为「是重定向来的」就放行。

    `allowed_hosts`:给定时,最终 host 必须命中其一(或为其子域)。用于「只跟随允许域重定向」。

    拒绝(抛 `UnsafeURLError`):`file:` / `ftp:` / `data:` / `javascript:` 等非 http(s);
    `localhost` / `*.local` / `*.internal`;私网 / 环回 / 链路本地 / 保留 IP;带 `user:pass@`
    的 netloc(凭证走私)。
    """
    raw = str(url or "").strip()
    if not raw:
        raise UnsafeURLError("空 URL")
    parts = urlsplit(raw)
    scheme = (parts.scheme or "").lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise UnsafeURLError(f"只接受公网 http(s),收到 scheme={scheme or '(空)'}:{raw[:80]!r}")
    if "@" in parts.netloc:
        raise UnsafeURLError("URL netloc 含 userinfo(凭证走私)")
    _reject_private_host(parts.hostname or "")

    if resolve is not None:
        resolved = resolve(raw)
        if resolved and str(resolved).strip() != raw:
            # 跟随一跳后**重走全套校验**(含 allowed_hosts),不给「重定向来的」任何豁免。
            return canonicalize_url(resolved, resolve=None, allowed_hosts=allowed_hosts)

    host = (parts.hostname or "").lower().rstrip(".")
    if allowed_hosts is not None:
        allow = tuple(h.lower().lstrip(".") for h in allowed_hosts)
        if not any(host == a or host.endswith("." + a) for a in allow):
            raise UnsafeURLError(f"host {host!r} 不在允许域 {list(allow)} 内")
    port = parts.port
    default_port = 80 if scheme == "http" else 443
    netloc = host if (port is None or port == default_port) else f"{host}:{port}"
    path = parts.path or "/"
    return urlunsplit((scheme, netloc, path, _strip_tracking(parts.query), ""))    # fragment 恒丢


def is_public_http_url(url: str) -> bool:
    """canonicalize 能通过 → True。用于 lint 的布尔判据(不抛)。"""
    try:
        canonicalize_url(url)
    except UnsafeURLError:
        return False
    return True


# ───────────────────────── 时间原语(全部走 IANA,禁止写死偏移) ─────────────────────────


def require_aware(dt: datetime, what: str) -> datetime:
    """时间戳必须带时区 —— naive datetime 是 DST 事故的唯一入口,一律拒收。"""
    if not isinstance(dt, datetime):
        raise EventContractError(f"{what} 不是 datetime:{dt!r}")
    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        raise EventContractError(
            f"{what} 是 naive datetime({dt!r}):必须带 IANA 时区。"
            f"写死 UTC+8 / ET 偏移在换季周必错一小时,而那一小时正是窗口分界。")
    return dt


def to_utc(dt: datetime) -> datetime:
    """任意 aware datetime → UTC。"""
    return require_aware(dt, "时间戳").astimezone(UTC)


def local_date_of(dt: datetime, tz: str) -> date:
    """绝对时刻在给定 IANA 时区的**本地日**(DST 正确)。"""
    return require_aware(dt, "时间戳").astimezone(ZoneInfo(tz)).date()


def local_day_bounds(day: date, tz: str) -> tuple[datetime, datetime]:
    """本地日 `day` 在时区 `tz` 的 [起, 止) 绝对区间(UTC)。DST 日自然是 23h / 25h。"""
    zone = ZoneInfo(tz)
    start = datetime.combine(day, time.min, tzinfo=zone)
    end = datetime.combine(day.fromordinal(day.toordinal() + 1), time.min, tzinfo=zone)
    return start.astimezone(UTC), end.astimezone(UTC)


def us_session_window(day: date, *, is_trading_day) -> tuple[datetime, datetime] | None:
    """美股常规时段 09:30–16:00 ET 的绝对区间(UTC);非交易日 → None。

    **本模块不拥有美股休市日历**:`is_trading_day(day) -> bool` 由调用方注入(交易所日历 /
    数据源的实际有无)。自带一张手抄假日表 = 又一个没人复核的事实源,而它错了会静默地
    把「感恩节没有常规时段」算成「有」。注入式让休市判据与真实数据同源、且可单测。
    """
    if not is_trading_day(day):
        return None
    zone = ZoneInfo(TZ_NEW_YORK)
    open_dt = datetime.combine(day, US_REGULAR_OPEN, tzinfo=zone)
    close_dt = datetime.combine(day, US_REGULAR_CLOSE, tzinfo=zone)
    return open_dt.astimezone(UTC), close_dt.astimezone(UTC)


# ───────────────────────── 统一事件契约 ─────────────────────────


@dataclass(frozen=True)
class ExternalEvent:
    """一条外源事件(§2 契约 yaml 逐字;字段一个不少)。

    不可变(frozen):事件一旦观测就是历史事实,改期要**新建一条**(新 `revision` + 新
    `first_seen_ts`),旧条保留 —— 「产物能证明跑过什么、不能证明没跑过什么」的同族纪律。

    - `scheduled_at_utc`:绝对时刻,仅 `TIMED` 有值;`DATE_ONLY` / `UNKNOWN` **必须为 None**
      (契约层强制)。留个「大概 8:30」在里面,下游迟早会拿它算窄窗。
    - `local_date`:事件在 `timezone` 下的本地日。`TIMED` 时必须与 `scheduled_at_utc` 自洽
      (换算后同日),否则抛 —— 这条恰好能逮住 DST 换算写错的那一小时。
    - `window`:`classify_window` 的产物,默认 None = 未分类。契约本身不猜窗口。
    """

    event_id: str
    event_type: str
    subject: str
    scheduled_at_utc: datetime | None
    local_date: date
    timezone: str
    time_quality: str
    source_url: str
    first_seen_ts: datetime
    revision: str
    status: str
    mapped_symbols: tuple[str, ...] = ()
    window: str | None = None

    def __post_init__(self) -> None:
        if not str(self.event_id).strip():
            raise EventContractError("event_id 不得为空")
        if self.event_type not in EVENT_TYPES:
            raise EventContractError(f"event_type={self.event_type!r} 不在 {EVENT_TYPES}")
        if self.time_quality not in TIME_QUALITIES:
            raise EventContractError(f"time_quality={self.time_quality!r} 不在 {TIME_QUALITIES}")
        if self.status not in STATUSES:
            raise EventContractError(f"status={self.status!r} 不在 {STATUSES}")
        if self.window is not None and self.window not in WINDOWS:
            raise EventContractError(f"window={self.window!r} 不在 {WINDOWS}")
        try:
            ZoneInfo(self.timezone)
        except Exception as exc:                       # noqa: BLE001 - 任何 tz 解析失败都是违约
            raise EventContractError(f"timezone={self.timezone!r} 不是合法 IANA 时区:{exc}") from exc
        if not isinstance(self.local_date, date) or isinstance(self.local_date, datetime):
            raise EventContractError(f"local_date 必须是 date(收到 {self.local_date!r})")
        object.__setattr__(self, "first_seen_ts", to_utc(self.first_seen_ts))
        object.__setattr__(self, "mapped_symbols", tuple(self.mapped_symbols or ()))
        if not str(self.revision).strip():
            raise EventContractError("revision 不得为空(改期要留得住旧版本)")
        if self.source_url and not is_public_http_url(self.source_url):
            raise EventContractError(f"source_url 不是公网 http(s):{self.source_url!r}")

        if self.time_quality == TIME_QUALITY_TIMED:
            if self.scheduled_at_utc is None:
                raise EventContractError(f"{self.event_id}:TIMED 却没有 scheduled_at_utc")
            sched = to_utc(self.scheduled_at_utc)
            object.__setattr__(self, "scheduled_at_utc", sched)
            actual = local_date_of(sched, self.timezone)
            if actual != self.local_date:
                raise EventContractError(
                    f"{self.event_id}:local_date={self.local_date} 与 scheduled_at_utc "
                    f"在 {self.timezone} 下的本地日 {actual} 不符 —— 多半是时区/DST 换算写错了")
        elif self.scheduled_at_utc is not None:
            raise EventContractError(
                f"{self.event_id}:time_quality={self.time_quality} 却带着 scheduled_at_utc="
                f"{self.scheduled_at_utc!r}。只有日期就只能是 DATE_ONLY —— 猜 AMC/BMO 会把"
                f"「当日风险」伪装成「窄窗内定时事件」(§2 铁律)。")

    # ── 序列化(scan 侧落 overseas_calendar.csv / json 用) ──

    def to_dict(self) -> dict:
        """JSON 安全的 dict(datetime → ISO8601 Z;date → ISO;tuple → list)。"""
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "subject": self.subject,
            "scheduled_at_utc": iso_z(self.scheduled_at_utc),
            "local_date": self.local_date.isoformat(),
            "timezone": self.timezone,
            "time_quality": self.time_quality,
            "source_url": self.source_url,
            "first_seen_ts": iso_z(self.first_seen_ts),
            "revision": self.revision,
            "status": self.status,
            "mapped_symbols": list(self.mapped_symbols),
            "window": self.window,
        }

    @classmethod
    def from_dict(cls, row: dict) -> ExternalEvent:
        return cls(
            event_id=str(row["event_id"]),
            event_type=str(row["event_type"]),
            subject=str(row.get("subject", "")),
            scheduled_at_utc=parse_ts(row.get("scheduled_at_utc")),
            local_date=parse_date(row["local_date"]),
            timezone=str(row["timezone"]),
            time_quality=str(row["time_quality"]),
            source_url=str(row.get("source_url", "")),
            first_seen_ts=parse_ts(row["first_seen_ts"]),
            revision=str(row["revision"]),
            status=str(row["status"]),
            mapped_symbols=tuple(row.get("mapped_symbols") or ()),
            window=row.get("window") or None,
        )

    def with_window(self, window: str) -> ExternalEvent:
        return dataclasses.replace(self, window=window)


def iso_z(dt: datetime | None) -> str | None:
    """aware datetime → `2026-08-28T12:34:56Z`(UTC);None 透传。"""
    if dt is None:
        return None
    return to_utc(dt).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(value) -> datetime | None:
    """ISO8601(含 `Z`)→ aware UTC datetime;None/空 → None。naive 串一律拒收。"""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return to_utc(value)
    s = str(value).strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    return to_utc(dt)


def parse_date(value) -> date:
    if isinstance(value, datetime):
        raise EventContractError("local_date 收到 datetime —— 本地日必须显式,别靠隐式截断")
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value).strip())


# ───────────────────────── 窗口判定(纯函数;日历锚由 scan 传入) ─────────────────────────


def classify_window(
    event: ExternalEvent,
    report_ts: datetime,
    t1_close_ts: datetime,
    t2_open_ts: datetime,
    *,
    decision_cutoff: datetime | None = None,
) -> str:
    """事件落在哪个窗(§2 两窄窗 + 当日风险 + 窗外)。**纯函数,零副作用,零网络**。

    三个锚(全部 aware,由 scan 按 A 股交易日历 `trade_days` 给,本模块不复制一份日历):

    - `report_ts`     报告发布时刻(= 默认 decision cutoff)
    - `t1_close_ts`   **入场截止**时刻。窄窗表写的是 T+1 14:45(下单决定),不是 15:00 收盘:
                      传 14:45 时,14:45–15:00 之间的事件会落进 `holding_overnight` —— 这是
                      对的,那时单已经下了,它只能是持仓风险。
    - `t2_open_ts`    T+2 开盘(卖出时刻)

    判定序(**先 PIT,后时刻**):

    1. `first_seen_ts > cutoff` → `outside`。当时不可知的事件进不了任何窗;历史真实发生日只用于
       事后事件研究(D-0),不能倒推生产时点可知。
    2. `status == "cancelled"` → `outside`。取消的会议不是隔夜风险,展示它只会制造假警报。
    3. `TIMED`:`(report_ts, t1_close_ts]` → `pre_entry`;`(t1_close_ts, t2_open_ts]` →
       `holding_overnight`;其余 → `outside`。
    4. `DATE_ONLY` / `UNKNOWN`:该**本地日**在其 `timezone` 下的 [00:00, 次日 00:00) 若与
       `(report_ts, t2_open_ts]` 相交 → `date_risk`,否则 `outside`。绝不进两个窄窗。

    A 股长假(如国庆)自然被覆盖:锚是 T+1 收盘与 T+2 开盘,长假只是让这两个锚相隔一周多,
    落在中间的任何美股事件照样判进 `holding_overnight` / `date_risk`。
    """
    report_ts = require_aware(report_ts, "report_ts")
    t1_close_ts = require_aware(t1_close_ts, "t1_close_ts")
    t2_open_ts = require_aware(t2_open_ts, "t2_open_ts")
    if not (report_ts <= t1_close_ts <= t2_open_ts):
        raise EventContractError(
            f"时间锚次序错乱:report_ts={iso_z(report_ts)} ≤ t1_close_ts={iso_z(t1_close_ts)} "
            f"≤ t2_open_ts={iso_z(t2_open_ts)} 不成立")
    cutoff = to_utc(decision_cutoff) if decision_cutoff is not None else to_utc(report_ts)

    if event.first_seen_ts > cutoff:
        return WINDOW_OUTSIDE
    if event.status == STATUS_CANCELLED:
        return WINDOW_OUTSIDE

    if event.time_quality == TIME_QUALITY_TIMED:
        sched = event.scheduled_at_utc
        if report_ts < sched <= t1_close_ts:
            return WINDOW_PRE_ENTRY
        if t1_close_ts < sched <= t2_open_ts:
            return WINDOW_HOLDING_OVERNIGHT
        return WINDOW_OUTSIDE

    day_start, day_end = local_day_bounds(event.local_date, event.timezone)
    if day_start < t2_open_ts and day_end > report_ts:
        return WINDOW_DATE_RISK
    return WINDOW_OUTSIDE


def assign_windows(
    events: Iterable[ExternalEvent],
    report_ts: datetime,
    t1_close_ts: datetime,
    t2_open_ts: datetime,
    *,
    decision_cutoff: datetime | None = None,
) -> list[ExternalEvent]:
    """批量打窗(返回带 `window` 的新对象;原对象不变)。"""
    return [
        e.with_window(classify_window(e, report_ts, t1_close_ts, t2_open_ts,
                                      decision_cutoff=decision_cutoff))
        for e in events
    ]


# ───────────────────────── PIT 可见性 / revision 去重 ─────────────────────────


def visible_at(events: Iterable[ExternalEvent], cutoff: datetime) -> list[ExternalEvent]:
    """cutoff 时点**可见**的事件(`first_seen_ts <= cutoff`)。晚于 cutoff 才知道的一律不可见。"""
    at = to_utc(cutoff)
    return [e for e in events if e.first_seen_ts <= at]


def latest_revisions(
    events: Iterable[ExternalEvent], *, cutoff: datetime | None = None
) -> list[ExternalEvent]:
    """同一 `event_id` 只留 cutoff 时点可见的**最新** revision(§2:改期保留旧 revision)。

    旧 revision 不被删除 —— 它仍在入参里、仍在账本里;这里只决定**展示哪一条**。排序键是
    `(first_seen_ts, revision)`,确定性(同 first_seen 的两条按 revision 串比大小)。
    """
    pool = visible_at(events, cutoff) if cutoff is not None else list(events)
    best: dict[str, ExternalEvent] = {}
    for e in pool:
        cur = best.get(e.event_id)
        if cur is None or (e.first_seen_ts, e.revision) > (cur.first_seen_ts, cur.revision):
            best[e.event_id] = e
    return sorted(best.values(), key=lambda e: (e.local_date, e.event_id))


# ───────────────────────── 展示排序(§2 展示纪律) ─────────────────────────

_WINDOW_RANK = {WINDOW_PRE_ENTRY: 0, WINDOW_HOLDING_OVERNIGHT: 0, WINDOW_DATE_RISK: 1,
                WINDOW_OUTSIDE: 2, None: 2}
_TYPE_RANK = {"fomc": 0, "macro_release": 0, "earnings": 1, "regulator": 1, "other": 2}


def display_sort_key(event: ExternalEvent, *, watched_symbols: Sequence[str] = ()) -> tuple:
    """§2 排序:映射到 📌/候选的官方定时事件 > 官方宏观定时 > 行业/主题 > DATE_ONLY。"""
    watched = {str(s).upper() for s in watched_symbols}
    mapped = bool(watched & {str(s).upper() for s in event.mapped_symbols})
    timed = event.time_quality == TIME_QUALITY_TIMED
    return (
        _WINDOW_RANK.get(event.window, 2),
        0 if timed else 1,
        0 if mapped else 1,
        _TYPE_RANK.get(event.event_type, 2),
        event.scheduled_at_utc or datetime.combine(event.local_date, time.min, tzinfo=UTC),
        event.event_id,
    )


def order_for_display(
    events: Iterable[ExternalEvent], *, watched_symbols: Sequence[str] = (), limit: int | None = None
) -> tuple[list[ExternalEvent], int]:
    """→ (要显示的行, 溢出条数)。`limit` 用于 summary ≤4 / brief ≤1 / 哨兵 ≤3(§2)。

    调用方把溢出写成 `+N`,**不是**悄悄截断:一行都不显示的日历和显示 4 行的日历,人审时
    看起来一样,这正是「漏报」的形态。
    """
    ordered = sorted(events, key=lambda e: display_sort_key(e, watched_symbols=watched_symbols))
    if limit is None or len(ordered) <= limit:
        return ordered, 0
    return ordered[:limit], len(ordered) - limit


# ───────────────────────── 时刻补齐(能确认才升 TIMED) ─────────────────────────

# 只有 T1(官方 / 原始)来源可以把 DATE_ONLY 升成 TIMED(§3.1)。T2 媒体转述的「预计 20:30 公布」
# 不行 —— 它支持不了 material claim,更支持不了一个会触发窄窗 tripwire 的精确时刻。
TIER1_AUTHORITIES: tuple[str, ...] = (
    "federalreserve.gov", "bls.gov", "bea.gov", "sec.gov", "treasury.gov",
    "census.gov", "issuer_ir",
)


@dataclass(frozen=True)
class TimeConfirmation:
    """一条来自 T1 官方源的**时刻确认**。

    `authority` 必须命中 `TIER1_AUTHORITIES`(域名或 `issuer_ir`);`source_url` 必须是公网
    http(s);`observed_at` 是**我们看到这条确认的时刻** —— 升级后的事件 `first_seen_ts` 会
    抬到它,因为在此之前我们只知道日期。
    """

    event_id: str
    scheduled_at_utc: datetime
    timezone: str
    source_url: str
    authority: str
    observed_at: datetime


def merge_time_quality(
    event: ExternalEvent,
    confirmations: Iterable[TimeConfirmation] = (),
    *,
    on_reject=None,
) -> ExternalEvent:
    """只为 FRED / yfinance 候选**补准确时刻**:能确认才升 `TIMED`,否则原样保持 `DATE_ONLY`。

    升级条件(全部满足才升,任一不满足 → 原样返回并把原因交给 `on_reject(reason)`):

    1. `confirmation.event_id == event.event_id`;
    2. `authority` 命中 `TIER1_AUTHORITIES`(§3.1:T4 聚合页永远升不了 TIMED);
    3. `source_url` 是公网 http(s)(canonicalize 通过);
    4. 确认时刻在事件 `timezone` 下的本地日 == `event.local_date`(**这条最值钱**:它逮的正是
       「把 08:30 ET 当成 08:30 CST」这类换算错,错一次就跨日);
    5. 事件当前不是 `TIMED`(已 TIMED 不覆盖,避免用二手时刻改写一手时刻)。

    升级产物:`time_quality=TIMED`、`scheduled_at_utc` 落地、`revision` 追加 `+timed@<观测时刻>`、
    `first_seen_ts = max(原, observed_at)`。**旧版本仍在调用方手里**(本函数不改原对象),
    `latest_revisions` 会在 cutoff 早于确认时刻时正确地只出旧的 DATE_ONLY 版本。

    多条确认时,取第一条通过全部检查的(确认之间若时刻不一致,后续的被 reject 并留原因 ——
    两个官方源打架不该由代码悄悄选一个)。
    """
    def _reject(reason: str) -> None:
        if on_reject is not None:
            on_reject(reason)

    if event.time_quality == TIME_QUALITY_TIMED:
        return event

    for conf in confirmations:
        if conf.event_id != event.event_id:
            _reject(f"event_id 不匹配:{conf.event_id!r} != {event.event_id!r}")
            continue
        auth = str(conf.authority or "").strip().lower()
        if not any(auth == a or auth.endswith("." + a) for a in TIER1_AUTHORITIES):
            _reject(f"{event.event_id}: authority={conf.authority!r} 非 T1 官方源 → 不升 TIMED")
            continue
        if not is_public_http_url(conf.source_url):
            _reject(f"{event.event_id}: 确认源 URL 非公网 http(s):{conf.source_url!r}")
            continue
        try:
            sched = to_utc(conf.scheduled_at_utc)
        except EventContractError as exc:
            _reject(f"{event.event_id}: 确认时刻非法:{exc}")
            continue
        tz = conf.timezone or event.timezone
        if local_date_of(sched, tz) != event.local_date:
            _reject(
                f"{event.event_id}: 确认时刻 {iso_z(sched)} 在 {tz} 下是 "
                f"{local_date_of(sched, tz)},与事件本地日 {event.local_date} 不符 → 不升 TIMED")
            continue
        return dataclasses.replace(
            event,
            scheduled_at_utc=sched,
            timezone=tz,
            time_quality=TIME_QUALITY_TIMED,
            source_url=conf.source_url or event.source_url,
            revision=f"{event.revision}+timed@{iso_z(conf.observed_at)}",
            first_seen_ts=max(event.first_seen_ts, to_utc(conf.observed_at)),
        )
    return event


def merge_time_quality_many(
    events: Iterable[ExternalEvent],
    confirmations: Iterable[TimeConfirmation] = (),
    *,
    on_reject=None,
) -> list[ExternalEvent]:
    """批量补齐(按 event_id 分桶后逐条走 `merge_time_quality`)。"""
    by_id: dict[str, list[TimeConfirmation]] = {}
    for c in confirmations:
        by_id.setdefault(c.event_id, []).append(c)
    return [merge_time_quality(e, by_id.get(e.event_id, ()), on_reject=on_reject) for e in events]


# ───────────────────────── 官方时刻抓取:待探针 ─────────────────────────

ENDPOINT = "official_event_calendar"          # lake 键 `official_event_calendar/<source>@<as_of>`

OFFICIAL_SOURCES: dict[str, str] = {
    # 只登记**入口页**,不登记任何解析逻辑 —— 解析规则没被真实响应验证过就是编造。
    "fed": "https://www.federalreserve.gov/newsevents/calendar.htm",
    "bls": "https://www.bls.gov/schedule/news_release/current_year.htm",
    "bea": "https://www.bea.gov/news/schedule",
}


def fetch_official_times(source: str, *, as_of: date | None = None, request=None):
    """【待探针 / 未实现】抓 Fed / BLS / BEA 官方日历,给 FRED / yfinance 候选补**准确时刻**。

    §9 该行的标注就是「待探针」。**故意不实现**:

    - 三家的日历页结构、是否有稳定的机读端点(ics / json)、限流与 UA 要求**都没被真实响应
      验证过**。凭印象写解析器 = 编造抓取逻辑;它跑不通时会静默返回空,跑通时会给出没人核过的
      时刻,而这个时刻**会直接决定事件进不进持仓隔夜窗**。
    - 没有它,系统仍然是正确的:`FRED` 给日期 → `DATE_ONLY` → 只显示为「当日风险」。
      这是**设计上可接受的降级**,不是缺陷(§2 铁律)。

    实现前置(探针要回答的):① 是否存在机读端点(ics/json)及其 schema;② 时刻是否带时区、
    是否覆盖改期;③ 限流 / UA 要求;④ 7 天 soak 的成功率、空结果率、schema 漂移(§9)。
    探针通过后,产物应转成 `TimeConfirmation` 喂 `merge_time_quality`,**不要**绕过它直接造
    `TIMED` 事件 —— 那道校验(本地日自洽)是防 DST 换算错的唯一一道。
    """
    raise NotImplementedError(
        f"official_event_calendar[{source}] 待探针(§9):官方日历页的机读端点/schema/限流"
        f"均未验证。在探针通过前,FRED 的 DATE_ONLY 就是正确答案 —— 不要用猜出来的时刻"
        f"把它伪装成 TIMED。")


__all__ = [
    "ExternalEvent", "TimeConfirmation", "EventContractError", "UnsafeURLError",
    "EVENT_TYPES", "TIME_QUALITIES", "STATUSES", "WINDOWS", "TIER1_AUTHORITIES",
    "TIME_QUALITY_TIMED", "TIME_QUALITY_DATE_ONLY", "TIME_QUALITY_UNKNOWN",
    "STATUS_SCHEDULED", "STATUS_RESCHEDULED", "STATUS_CANCELLED", "STATUS_RELEASED",
    "WINDOW_PRE_ENTRY", "WINDOW_HOLDING_OVERNIGHT", "WINDOW_DATE_RISK", "WINDOW_OUTSIDE",
    "TZ_SHANGHAI", "TZ_NEW_YORK", "ENDPOINT", "OFFICIAL_SOURCES",
    "classify_window", "assign_windows", "visible_at", "latest_revisions",
    "display_sort_key", "order_for_display", "merge_time_quality", "merge_time_quality_many",
    "fetch_official_times", "canonicalize_url", "is_public_http_url",
    "to_utc", "iso_z", "parse_ts", "parse_date", "local_date_of", "local_day_bounds",
    "require_aware", "us_session_window",
]
