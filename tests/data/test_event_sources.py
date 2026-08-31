"""外源事件四源(`official_event_calendar` / `fred_calendar` / `fomc_calendar` / `gnews_rss` /
`edgar`)的契约钉子。

design: `docs/specs/2026-08-28-external-evidence-expansion-design.md` §2(统一事件契约 +
time_quality + 两窄窗)、§3.1(来源分级 + URL 入账纪律)、§9(数据源规格)。

**测试禁止真网络**:每一处取数都走各模块自带的注入点(`request=` / `fetch=` / `history=` /
`path=`)。本文件不 import requests、不碰 yfinance。

这里钉的都是「错了不会有任何自然告警」的那一类:
  · DST 换季周错一小时 → 盘后财报到底在不在持仓窗内(§2 全部意义所在);
  · `DATE_ONLY` 被猜成 AMC/BMO → 「当日风险」伪装成「窄窗内定时事件」;
  · `first_seen_ts` 没把关 → 用当时不可知的事件做 PIT 回测;
  · 聚合页 snippet 被当成 material claim → 查无实据的「事实」进正文。
"""

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from autoresearch.data import contracts
from autoresearch.data.sources import (
    edgar,
    fomc_calendar as fomcc,
    fred_calendar as fredc,
    gnews_rss as gn,
    official_event_calendar as oec,
)

UTC = timezone.utc
ET = ZoneInfo(oec.TZ_NEW_YORK)
CST = ZoneInfo(oec.TZ_SHANGHAI)

SEEN = datetime(2026, 8, 20, 0, 0, tzinfo=UTC)


def _ev(**kw) -> oec.ExternalEvent:
    base = {
        "event_id": "e1",
        "event_type": "earnings",
        "subject": "NVDA FY27Q2",
        "scheduled_at_utc": None,
        "local_date": date(2026, 8, 26),
        "timezone": oec.TZ_NEW_YORK,
        "time_quality": oec.TIME_QUALITY_DATE_ONLY,
        "source_url": "https://www.sec.gov/x",
        "first_seen_ts": SEEN,
        "revision": "v1",
        "status": oec.STATUS_SCHEDULED,
    }
    base.update(kw)
    return oec.ExternalEvent(**base)


def _timed(et_dt: datetime, **kw) -> oec.ExternalEvent:
    """按 **ET 钟面**给定的定时事件(local_date 由换算得到 —— 不手写,免得测试自己写错)。"""
    return _ev(scheduled_at_utc=et_dt.astimezone(UTC),
               local_date=et_dt.astimezone(ET).date(),
               time_quality=oec.TIME_QUALITY_TIMED, **kw)


# ═════════════════════════ DST:夏 / 冬两侧换算 ═════════════════════════


def test_us_session_window_shifts_one_hour_between_edt_and_est():
    """同一个 09:30–16:00 ET 钟面,夏令 13:30–20:00Z、冬令 14:30–21:00Z。

    写死 UTC 偏移的实现在这里会有一边错整整一小时,而那一小时恰好是「盘后财报落不落在
    持仓窗内」的分界(§2)。
    """
    summer = oec.us_session_window(date(2026, 7, 15), is_trading_day=lambda d: True)
    winter = oec.us_session_window(date(2026, 1, 15), is_trading_day=lambda d: True)
    assert summer == (datetime(2026, 7, 15, 13, 30, tzinfo=UTC),
                      datetime(2026, 7, 15, 20, 0, tzinfo=UTC))
    assert winter == (datetime(2026, 1, 15, 14, 30, tzinfo=UTC),
                      datetime(2026, 1, 15, 21, 0, tzinfo=UTC))
    assert (winter[0] - summer[0]) % timedelta(days=1) == timedelta(hours=1)


@pytest.mark.parametrize("day,hours", [
    (date(2026, 3, 8), 23),      # EST → EDT(2026 年 3 月第二个周日)
    (date(2026, 11, 1), 25),     # EDT → EST(11 月第一个周日)
])
def test_local_day_bounds_handle_dst_transition_days(day, hours):
    """换季日的「本地日」是 23h / 25h —— `DATE_ONLY` 的当日风险区间靠它算。"""
    start, end = oec.local_day_bounds(day, oec.TZ_NEW_YORK)
    assert end - start == timedelta(hours=hours)


def test_fomc_verified_year_converts_1400_et_across_dst(tmp_path):
    """3 月会议(EDT)= 18:00Z,12 月会议(EST)= 19:00Z。FOMC 换算到 CST 是 T+2 凌晨,正落窗内。"""
    p = tmp_path / "fomc.yaml"
    p.write_text(
        "version: 1\n"
        "source_url: https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm\n"
        "timezone: America/New_York\n"
        "revision: v2\n"
        "verified_by: qingbin\n"
        "years:\n"
        "  2026:\n"
        "    revision: v2\n"
        "    verified_by: qingbin\n"
        "    added_at: 2026-08-29T00:00:00Z\n"
        "    meetings:\n"
        "      - start_date: 2026-03-17\n"
        "        end_date: 2026-03-18\n"
        '        statement_local_time: "14:00"\n'
        "      - start_date: 2026-12-08\n"
        "        end_date: 2026-12-09\n"
        '        statement_local_time: "14:00"\n',
        encoding="utf-8")
    evs = fomcc.load_fomc(2026, path=p)
    assert [e.time_quality for e in evs] == [oec.TIME_QUALITY_TIMED] * 2
    assert evs[0].scheduled_at_utc == datetime(2026, 3, 18, 18, 0, tzinfo=UTC)   # EDT = UTC-4
    assert evs[1].scheduled_at_utc == datetime(2026, 12, 9, 19, 0, tzinfo=UTC)   # EST = UTC-5
    assert [e.local_date for e in evs] == [date(2026, 3, 18), date(2026, 12, 9)]
    assert all(e.first_seen_ts == datetime(2026, 8, 29, tzinfo=UTC) for e in evs)


# ═════════════════════════ 美国休市 / A 股休市 ═════════════════════════


def test_us_market_holiday_has_no_regular_session():
    assert oec.us_session_window(date(2026, 7, 3), is_trading_day=lambda d: False) is None


def test_holiday_calendar_is_injected_not_hardcoded():
    """本模块**不自带**美股假日表:同一天,注入器说开市就开市。

    自带一张没人复核的假日表,错了会静默地把「感恩节没有常规时段」算成「有」 —— 而且永远
    不会有人发现。判据必须与真实数据同源、可单测。
    """
    xmas = date(2026, 12, 25)
    assert oec.us_session_window(xmas, is_trading_day=lambda d: True) is not None
    assert oec.us_session_window(xmas, is_trading_day=lambda d: False) is None


def test_a_share_long_holiday_stretches_the_overnight_window():
    """A 股国庆长假:T+1 收盘与 T+2 开盘相隔 9 天,落在中间的美股事件照样进持仓窗。

    锚由 scan 按 `trade_days` 给,所以「A 股休市」不需要本模块知道任何假日 —— 它只表现为
    两个锚变远了(§2 `classify_window` docstring 末段)。
    """
    report = datetime(2026, 9, 29, 21, 20, tzinfo=CST)
    t1_close = datetime(2026, 9, 30, 14, 45, tzinfo=CST)
    t2_open = datetime(2026, 10, 9, 9, 30, tzinfo=CST)      # 长假后第一个 A 股交易日
    mid_holiday = _timed(datetime(2026, 10, 2, 16, 5, tzinfo=ET), event_id="amc-holiday")
    date_only = _ev(event_id="cpi", event_type="macro_release", local_date=date(2026, 10, 5))

    assert oec.classify_window(mid_holiday, report, t1_close, t2_open) == oec.WINDOW_HOLDING_OVERNIGHT
    assert oec.classify_window(date_only, report, t1_close, t2_open) == oec.WINDOW_DATE_RISK


def test_anchor_order_is_enforced():
    report = datetime(2026, 8, 26, 21, 20, tzinfo=CST)
    with pytest.raises(oec.EventContractError):
        oec.classify_window(_ev(), report, report - timedelta(hours=1), report + timedelta(days=1))


# ═════════════════════════ BMO / AMC ═════════════════════════

# §2 的真实例子:NVDA 2026-08-26 16:00 ET 盘后 → CST 08-27 04:00,对 08-26 21:20 发布的 run
# 落在**入场前**窗(T+1 14:45 下单决定之前就已知)。
_REPORT = datetime(2026, 8, 26, 21, 20, tzinfo=CST)
_T1_CLOSE = datetime(2026, 8, 27, 14, 45, tzinfo=CST)      # 入场截止 = 下单决定,不是 15:00
_T2_OPEN = datetime(2026, 8, 28, 9, 30, tzinfo=CST)


@pytest.mark.parametrize("et_dt,expect", [
    (datetime(2026, 8, 26, 16, 5, tzinfo=ET), oec.WINDOW_PRE_ENTRY),          # T 盘后 AMC
    (datetime(2026, 8, 27, 16, 5, tzinfo=ET), oec.WINDOW_HOLDING_OVERNIGHT),  # T+1 盘后 AMC
    (datetime(2026, 8, 27, 8, 0, tzinfo=ET), oec.WINDOW_HOLDING_OVERNIGHT),   # T+1 盘前 BMO
    (datetime(2026, 8, 28, 8, 0, tzinfo=ET), oec.WINDOW_OUTSIDE),             # T+2 盘前(卖出后)
    (datetime(2026, 8, 26, 8, 0, tzinfo=ET), oec.WINDOW_OUTSIDE),             # T 盘前(报告前已发生)
])
def test_bmo_amc_land_in_the_right_window(et_dt, expect):
    assert oec.classify_window(_timed(et_dt), _REPORT, _T1_CLOSE, _T2_OPEN) == expect


def test_us_bmo_can_never_beat_the_a_share_entry_cutoff():
    """美股盘前 08:00 ET = 20:00 CST,永远晚于 T+1 14:45 的下单决定 → BMO 进不了 `pre_entry`。

    这不是实现细节,是结构性事实(§2):它决定「盘前财报」只能算持仓风险,不能算入场否决项。
    """
    bmo = _timed(datetime(2026, 8, 27, 8, 0, tzinfo=ET))
    assert bmo.scheduled_at_utc.astimezone(CST).hour == 20
    assert oec.classify_window(bmo, _REPORT, _T1_CLOSE, _T2_OPEN) != oec.WINDOW_PRE_ENTRY


def test_nvda_amc_example_crosses_into_the_next_cst_day():
    amc = _timed(datetime(2026, 8, 26, 16, 5, tzinfo=ET))
    assert amc.local_date == date(2026, 8, 26)                                    # ET 本地日
    assert oec.local_date_of(amc.scheduled_at_utc, oec.TZ_SHANGHAI) == date(2026, 8, 27)


# ═════════════════════════ DATE_ONLY 不得升 TIMED ═════════════════════════


def test_date_only_cannot_carry_a_timestamp():
    with pytest.raises(oec.EventContractError, match="DATE_ONLY"):
        _ev(scheduled_at_utc=datetime(2026, 8, 26, 20, 30, tzinfo=UTC))


def test_timed_requires_a_timestamp_and_a_self_consistent_local_date():
    with pytest.raises(oec.EventContractError, match="TIMED 却没有"):
        _ev(time_quality=oec.TIME_QUALITY_TIMED)
    with pytest.raises(oec.EventContractError, match="DST"):
        # 把 08:30 ET 当成 08:30 UTC:本地日对不上 → 正是换算写错的签名
        _ev(time_quality=oec.TIME_QUALITY_TIMED,
            scheduled_at_utc=datetime(2026, 8, 27, 3, 0, tzinfo=UTC),
            local_date=date(2026, 8, 27))


def test_date_only_never_enters_the_two_narrow_windows():
    """哪怕这一天里「常识上的 08:30 ET」正落在窄窗里,DATE_ONLY 也只能是 `date_risk`。"""
    e = _ev(event_id="cpi", event_type="macro_release", local_date=date(2026, 8, 27))
    got = oec.classify_window(e, _REPORT, _T1_CLOSE, _T2_OPEN)
    assert got == oec.WINDOW_DATE_RISK
    assert got not in (oec.WINDOW_PRE_ENTRY, oec.WINDOW_HOLDING_OVERNIGHT)


@pytest.mark.parametrize("conf_kw,needle", [
    ({"authority": "reuters.com"}, "非 T1 官方源"),                        # T2 媒体转述不算
    ({"authority": "news.google.com"}, "非 T1 官方源"),                    # T4 聚合更不算
    ({"source_url": "file:///tmp/x.html"}, "非公网"),
    # 把 08:30 ET 读成了 08:30 UTC(= 04:30 ET 次日…)这类换算错的签名:本地日对不上 → 拒。
    ({"scheduled_at_utc": datetime(2026, 8, 28, 12, 30, tzinfo=UTC)}, "不符"),
])
def test_merge_time_quality_rejects_and_keeps_date_only(conf_kw, needle):
    e = _ev(event_id="cpi", event_type="macro_release", local_date=date(2026, 8, 27))
    conf = {
        "event_id": "cpi",
        "scheduled_at_utc": datetime(2026, 8, 27, 12, 30, tzinfo=UTC),     # 08:30 ET
        "timezone": oec.TZ_NEW_YORK,
        "source_url": "https://www.bls.gov/schedule/news_release/cpi.htm",
        "authority": "bls.gov",
        "observed_at": datetime(2026, 8, 25, tzinfo=UTC),
    }
    conf.update(conf_kw)
    rejects: list[str] = []
    got = oec.merge_time_quality(e, [oec.TimeConfirmation(**conf)], on_reject=rejects.append)
    assert got is e and got.time_quality == oec.TIME_QUALITY_DATE_ONLY
    assert any(needle in r for r in rejects), rejects


def test_merge_time_quality_upgrades_only_on_a_tier1_confirmation():
    e = _ev(event_id="cpi", event_type="macro_release", local_date=date(2026, 8, 27))
    conf = oec.TimeConfirmation(
        event_id="cpi", scheduled_at_utc=datetime(2026, 8, 27, 12, 30, tzinfo=UTC),
        timezone=oec.TZ_NEW_YORK, source_url="https://www.bls.gov/schedule/x.htm",
        authority="bls.gov", observed_at=datetime(2026, 8, 25, tzinfo=UTC))
    got = oec.merge_time_quality(e, [conf])
    assert got.time_quality == oec.TIME_QUALITY_TIMED
    assert got.scheduled_at_utc == datetime(2026, 8, 27, 12, 30, tzinfo=UTC)
    assert got.revision.startswith("v1+timed@") and got.first_seen_ts == conf.observed_at
    assert e.time_quality == oec.TIME_QUALITY_DATE_ONLY                    # 原对象不变
    # 升级后才进得了窄窗(20:30 CST T+1 → 持仓隔夜)
    assert oec.classify_window(got, _REPORT, _T1_CLOSE, _T2_OPEN) == oec.WINDOW_HOLDING_OVERNIGHT


def test_pending_fomc_year_stays_date_only_even_with_a_statement_time():
    """入库的 `fomc_calendar.yaml` 是 `verified_by: pending` → 14:00 写在那里也不许用。"""
    doc = fomcc.load_raw()
    assert not fomcc.is_verified(doc, 2026)
    evs = fomcc.load_fomc(2026)
    assert evs and all(e.time_quality == oec.TIME_QUALITY_DATE_ONLY for e in evs)
    assert all(e.scheduled_at_utc is None for e in evs)
    assert all("pending" in e.revision for e in evs)
    assert all(oec.classify_window(e, _REPORT, _T1_CLOSE, _T2_OPEN)
               in (oec.WINDOW_DATE_RISK, oec.WINDOW_OUTSIDE) for e in evs)
    with pytest.raises(fomcc.FomcCalendarError, match="pending"):
        fomcc.load_fomc(2026, require_verified=True)


def test_fomc_missing_year_degrades_instead_of_raising():
    contracts.clear_degradations()
    got = fomcc.load_range(date(2030, 1, 1), date(2030, 12, 31))
    assert got == []
    assert any(r["endpoint"] == fomcc.ENDPOINT for r in contracts.degradations())


# ═════════════════════════ 改期 / revision / PIT 可见性 ═════════════════════════


def _rescheduled_pair():
    v1 = _ev(event_id="fomc:2026-09-16", event_type="fomc", local_date=date(2026, 9, 16),
             revision="v1", first_seen_ts=datetime(2026, 8, 20, tzinfo=UTC))
    v2 = _ev(event_id="fomc:2026-09-16", event_type="fomc", local_date=date(2026, 9, 17),
             revision="v2", status=oec.STATUS_RESCHEDULED,
             first_seen_ts=datetime(2026, 8, 25, tzinfo=UTC))
    return v1, v2


def test_reschedule_keeps_the_old_revision_and_shows_only_the_latest_visible_one():
    v1, v2 = _rescheduled_pair()
    pool = [v1, v2]
    before = oec.latest_revisions(pool, cutoff=datetime(2026, 8, 22, tzinfo=UTC))
    after = oec.latest_revisions(pool, cutoff=datetime(2026, 8, 26, tzinfo=UTC))
    assert [e.revision for e in before] == ["v1"]      # 改期还没被看到 → 只出旧版
    assert [e.revision for e in after] == ["v2"]       # 看到之后 → 同 event_id 只出最新
    assert pool == [v1, v2]                            # 旧 revision 仍在账本里,没被删


def test_visible_at_hides_events_first_seen_after_the_cutoff():
    v1, v2 = _rescheduled_pair()
    assert oec.visible_at([v1, v2], datetime(2026, 8, 21, tzinfo=UTC)) == [v1]
    assert oec.visible_at([v1, v2], datetime(2026, 8, 19, tzinfo=UTC)) == []


def test_event_first_seen_after_cutoff_is_outside_every_window():
    """当时不可知的事件进不了任何窗 —— 历史真实发生日只能用于事后事件研究(D-0)。"""
    late = _timed(datetime(2026, 8, 26, 16, 5, tzinfo=ET),
                  first_seen_ts=datetime(2026, 8, 27, 10, 0, tzinfo=UTC))
    assert oec.classify_window(late, _REPORT, _T1_CLOSE, _T2_OPEN) == oec.WINDOW_OUTSIDE
    # 同一条事件,把 cutoff 放宽到它之后就进窗了 → 证明拒它的是 PIT,不是时刻
    assert oec.classify_window(late, _REPORT, _T1_CLOSE, _T2_OPEN,
                               decision_cutoff=datetime(2026, 8, 27, 12, 0, tzinfo=UTC)) \
        == oec.WINDOW_PRE_ENTRY


def test_cancelled_events_are_outside():
    e = _timed(datetime(2026, 8, 26, 16, 5, tzinfo=ET), status=oec.STATUS_CANCELLED)
    assert oec.classify_window(e, _REPORT, _T1_CLOSE, _T2_OPEN) == oec.WINDOW_OUTSIDE


def test_display_order_puts_mapped_timed_events_first_and_reports_overflow():
    timed_mapped = _timed(datetime(2026, 8, 27, 16, 5, tzinfo=ET), event_id="amc-nvda",
                          mapped_symbols=("NVDA",))
    timed_other = _timed(datetime(2026, 8, 27, 16, 6, tzinfo=ET), event_id="amc-other")
    day_only = _ev(event_id="cpi", event_type="macro_release", local_date=date(2026, 8, 27))
    events = oec.assign_windows([day_only, timed_other, timed_mapped],
                                _REPORT, _T1_CLOSE, _T2_OPEN)
    shown, overflow = oec.order_for_display(events, watched_symbols=["NVDA"], limit=2)
    assert [e.event_id for e in shown] == ["amc-nvda", "amc-other"]
    assert overflow == 1


def test_roundtrip_to_dict_and_back():
    e = oec.assign_windows([_timed(datetime(2026, 8, 27, 16, 5, tzinfo=ET))],
                           _REPORT, _T1_CLOSE, _T2_OPEN)[0]
    assert oec.ExternalEvent.from_dict(e.to_dict()) == e
    assert e.to_dict()["window"] == oec.WINDOW_HOLDING_OVERNIGHT


# ═════════════════════════ canonicalize_url 四类 ═════════════════════════


@pytest.mark.parametrize("bad", [
    "http://localhost/x", "http://127.0.0.1/x", "http://192.168.0.9/x",
    "http://10.1.2.3/x", "http://[::1]/x", "http://box.internal/x", "http://api.local/x",
])
def test_canonicalize_rejects_private_and_loopback_hosts(bad):
    with pytest.raises(oec.UnsafeURLError):
        oec.canonicalize_url(bad)
    assert not oec.is_public_http_url(bad)


@pytest.mark.parametrize("bad", [
    "file:///etc/passwd", "ftp://example.com/x", "data:text/html,<b>x", "javascript:alert(1)",
    "https://user:pw@example.com/x", "",
])
def test_canonicalize_rejects_non_public_http_schemes_and_userinfo(bad):
    with pytest.raises(oec.UnsafeURLError):
        oec.canonicalize_url(bad)


def test_canonicalize_strips_tracking_params_but_keeps_the_real_ones():
    got = oec.canonicalize_url(
        "https://www.reuters.com/a?utm_source=x&utm_medium=y&fbclid=z&gclid=w&id=7&page=2")
    assert got == "https://www.reuters.com/a?id=7&page=2"


def test_canonicalize_drops_the_fragment_and_normalizes_host_port_path():
    assert oec.canonicalize_url("https://EXAMPLE.com:443#section-2") == "https://example.com/"
    assert oec.canonicalize_url("http://example.com:80/a#frag") == "http://example.com/a"


def test_canonicalize_revalidates_after_following_a_redirect():
    """跟随一跳后**重走全套校验** —— 「是重定向来的」不给任何豁免。"""
    with pytest.raises(oec.UnsafeURLError):
        oec.canonicalize_url("https://news.google.com/x", resolve=lambda u: "file:///etc/passwd")
    with pytest.raises(oec.UnsafeURLError):
        oec.canonicalize_url("https://news.google.com/x", resolve=lambda u: "https://evil.com/y",
                             allowed_hosts=["reuters.com"])
    assert oec.canonicalize_url("https://news.google.com/x",
                                resolve=lambda u: "https://www.reuters.com/y?utm_source=g",
                                allowed_hosts=["reuters.com"]) == "https://www.reuters.com/y"


def test_event_rejects_a_non_public_source_url():
    with pytest.raises(oec.EventContractError, match="source_url"):
        _ev(source_url="http://localhost:8000/x")


# ═════════════════════════ gnews:恒 T4 / UNVERIFIED ═════════════════════════

_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <item>
    <title>协创数据披露半年报</title>
    <link>https://www.reuters.com/tech/a?utm_source=gnews#top</link>
    <pubDate>Wed, 26 Aug 2026 12:30:00 GMT</pubDate>
    <description>&lt;p&gt;摘要正文 &lt;b&gt;很长&lt;/b&gt;   多空白&lt;/p&gt;</description>
    <source url="https://www.reuters.com">Reuters</source>
  </item>
  <item>
    <title>内网测试条目</title>
    <link>http://192.168.1.9/leak</link>
    <pubDate>Wed, 26 Aug 2026 13:00:00 GMT</pubDate>
  </item>
  <item>
    <title>无时区时间戳</title>
    <link>https://finance.sina.com.cn/x</link>
    <pubDate>26 Aug 2026 13:00:00</pubDate>
  </item>
</channel></rss>"""


def test_rss_items_are_always_t4_and_unverified():
    rows = gn.parse_rss(_RSS, discovered_via=gn.SOURCE_GOOGLE)
    assert len(rows) == 2                                   # 私网那条被整项丢弃
    assert all(r["evidence_tier"] == "T4_discovery" for r in rows)
    assert all(r["verification_status"] == "UNVERIFIED" for r in rows)
    assert gn.EVIDENCE_TIER == "T4_discovery" and gn.VERIFICATION_STATUS == "UNVERIFIED"


def test_rss_url_is_canonicalized_and_summary_is_short():
    r = gn.parse_rss(_RSS, discovered_via=gn.SOURCE_GOOGLE)[0]
    assert r["url"] == "https://www.reuters.com/tech/a"      # 追踪参数 + fragment 都没了
    assert r["source_name"] == "Reuters"
    assert r["published_ts"] == "2026-08-26T12:30:00Z"
    assert "<" not in r["summary"] and len(r["summary"]) <= gn.SUMMARY_MAX_CHARS
    assert r["summary"] == "摘要正文 很长 多空白"           # 去标签 + 压空白,**不是正文**
    assert r["content_hash"]


def test_rss_naive_pubdate_becomes_none_instead_of_a_guessed_timezone():
    rows = gn.parse_rss(_RSS, discovered_via=gn.SOURCE_GOOGLE)
    assert rows[1]["published_ts"] is None and rows[1]["published_raw"]


def test_search_stays_unverified_even_with_redirect_following():
    """跟到了 canonical 原文也**不升格** —— 升格只能由 claim_ledger 在抓到原文后另行判定。"""
    calls = []

    def fetch(url):
        calls.append(url)
        return _RSS

    rows = gn.search("协创数据", "zh", fetch=fetch, provider="google",
                     resolve=lambda u: u, allowed_hosts=["reuters.com", "sina.com.cn"])
    assert calls == [gn.google_news_rss_url("协创数据", "zh")]
    assert rows and all(r["verification_status"] == "UNVERIFIED" for r in rows)
    assert all(r["discovered_via"] == gn.SOURCE_GOOGLE for r in rows)
    assert all(r["evidence_tier"] == gn.EVIDENCE_TIER for r in gn.to_catalog_rows(rows, first_seen_ts=SEEN))


def test_search_dedupes_across_providers_and_survives_one_source_failing():
    contracts.clear_degradations()

    def fetch(url):
        if gn.BING_RSS_BASE in url:
            raise RuntimeError("bing 429")
        return _RSS

    rows = gn.search("NVDA", "en", fetch=fetch, provider="both")
    assert [r["url"] for r in rows] == ["https://www.reuters.com/tech/a",
                                        "https://finance.sina.com.cn/x"]
    assert any(r["endpoint"] == gn.SOURCE_BING for r in contracts.degradations())


def test_rss_query_urls_and_lang_guard():
    u = gn.google_news_rss_url("协创数据", "zh")
    assert u.startswith(gn.GOOGLE_RSS_BASE) and "hl=zh-CN" in u and "ceid=CN:zh-Hans" in u
    assert "q=%E5%8D%8F" in u                            # 查询词 quote_plus,ceid 的冒号照留(合法)
    assert "format=rss" in gn.bing_news_rss_url("NVDA", "en")
    with pytest.raises(ValueError):
        gn.google_news_rss_url("x", "ja")


def test_broken_rss_raises_instead_of_returning_empty():
    with pytest.raises(gn.NewsRssError):
        gn.parse_rss("<rss><channel>", discovered_via=gn.SOURCE_GOOGLE)


# ═════════════════════════ EDGAR:UA 头 + 缺 CIK 降级 ═════════════════════════

_SUBMISSIONS = {
    "filings": {"recent": {
        "accessionNumber": ["0001045810-26-000123", "0001045810-26-000010"],
        "form": ["8-K", "4"],
        "filingDate": ["2026-08-20", "2026-01-05"],
        "reportDate": ["2026-08-20", ""],
        "primaryDocument": ["nvda-8k.htm", "doc4.xml"],
        "acceptanceDateTime": ["2026-08-20T16:05:00-04:00", ""],
        "items": ["2.02", ""],
    }}
}


@pytest.fixture()
def _ua(monkeypatch):
    monkeypatch.setenv(edgar.UA_ENV, "TradingAgents research@example.com")
    return "TradingAgents research@example.com"


def test_edgar_sends_the_required_user_agent_header(_ua):
    seen = {}

    def request(url, hdrs):
        seen[url] = hdrs
        return _SUBMISSIONS

    df = edgar.fetch_submissions("NVDA", as_of=date(2026, 8, 29), days=90,
                                 mapping={"NVDA": "0001045810"}, request=request)
    (url, hdrs), = seen.items()
    assert url == edgar.SUBMISSIONS_URL.format(cik10="0001045810")
    assert hdrs["User-Agent"] == _ua                       # SEC 的合规要求,不是技术细节
    assert hdrs["Host"] == "data.sec.gov"
    assert list(df["form"]) == ["8-K"]                     # 90 日窗把 1 月那条筛掉了
    assert df.iloc[0]["url"].endswith("/1045810/000104581026000123/nvda-8k.htm")


def test_edgar_without_ua_env_degrades_instead_of_guessing_a_contact(monkeypatch):
    monkeypatch.delenv(edgar.UA_ENV, raising=False)
    contracts.clear_degradations()
    with pytest.raises(edgar.EdgarNotConfiguredError):
        edgar.user_agent()
    out = edgar.fetch_submissions_safe("NVDA", mapping={"NVDA": "0001045810"},
                                       request=lambda u, h: _SUBMISSIONS)
    assert out.status == "FAILED" and not out.ok
    assert any(r["endpoint"] == edgar.ENDPOINT for r in contracts.degradations())


def test_edgar_missing_cik_degrades_as_no_cik_not_failed(_ua):
    """`NO_CIK`(这只票本来就没有 EDGAR)与 `FAILED`(我们坏了)必须分得开。"""
    contracts.clear_degradations()
    out = edgar.fetch_submissions_safe("600519.SS", mapping={"NVDA": "0001045810"},
                                       request=lambda u, h: _SUBMISSIONS)
    assert out.status == "NO_CIK" and out.rows.empty
    # `ok` 只对 OK / EMPTY 为真 —— NO_CIK 归到「不 ok」(现状,与 fred 的 `status != FAILED`
    # 口径不同)。消费侧要区分「这只票没有 EDGAR」与「我们坏了」必须读 `status`,不能读 `ok`。
    assert not out.ok
    assert list(out.rows.columns) == list(edgar._FILING_COLS)   # 空也保结构
    recs = contracts.degradations()
    assert any("未命中" in r["reasons"][0] for r in recs)
    # 现状留痕:NO_CIK 记成 `degraded`(会进告警渲染),不是 `legit_empty` —— A 股票每次路过
    # 都会点亮一次告警面。改不改是产品裁定,这里只钉住「今天是这样」。
    assert [r.get("kind") for r in recs] == ["degraded"]


# 2026-08-29 已修:`resolve_cik` 现在要求**显式 `CIK` 前缀**才走数字路,否则 ≤6 位纯数字
# 一律当 A 股代码拒掉(真 CIK 是 7–10 位)。xfail 随修复删除 —— 留着 strict xfail 会 XPASS 变红。
def test_edgar_must_not_take_a_bare_a_share_code_as_a_cik(_ua):
    with pytest.raises(edgar.EdgarError):
        edgar.resolve_cik("600519", mapping={"NVDA": "0001045810"})


def test_edgar_never_guesses_a_ticker_to_cik_mapping(_ua):
    with pytest.raises(edgar.EdgarError, match="不猜"):
        edgar.resolve_cik("ZZZZ", mapping={"NVDA": "0001045810"})
    assert edgar.resolve_cik("CIK1045810", mapping={}) == "0001045810"
    assert edgar.to_cik10(1045810) == "0001045810"


def test_edgar_legit_empty_is_not_a_failure(_ua):
    contracts.clear_degradations()
    out = edgar.fetch_submissions_safe("NVDA", mapping={"NVDA": "0001045810"},
                                       request=lambda u, h: {"filings": {"recent": {}}})
    assert out.status == "EMPTY" and out.ok
    assert [r["kind"] for r in contracts.degradations()] == ["legit_empty"]


def test_edgar_schema_drift_raises_rather_than_pretending_to_be_empty(_ua):
    with pytest.raises(edgar.EdgarError, match="filings"):
        edgar.fetch_submissions("NVDA", mapping={"NVDA": "0001045810"},
                                request=lambda u, h: {"cik": "1045810"})


def test_edgar_acceptance_timestamp_keeps_the_et_offset(_ua):
    row = {"acceptance_datetime": "2026-08-20T16:05:00-04:00"}
    assert edgar.acceptance_ts(row) == datetime(2026, 8, 20, 20, 5, tzinfo=UTC)
    assert edgar.acceptance_ts({"acceptance_datetime": ""}) is None     # filingDate 不许冒充时刻


def test_edgar_company_tickers_table_is_parsed_from_the_official_shape(_ua):
    payload = {"0": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA CORP"},
               "1": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}}
    assert edgar.fetch_company_tickers(request=lambda u, h: payload) == {
        "NVDA": "0001045810", "AAPL": "0000320193"}


# ═════════════════════════ FRED:恒 DATE_ONLY + 空/失败可分 ═════════════════════════


def _fred_payload(page):
    return {"release_dates": page}


def test_fred_paginates_and_asks_for_future_dates_with_names():
    pages = [
        _fred_payload([{"release_id": 10, "release_name": "Consumer Price Index", "date": "2026-09-10"},
                       {"release_id": 50, "release_name": "Employment Situation", "date": "2026-09-04"}]),
        _fred_payload([{"release_id": 53, "release_name": "Gross Domestic Product", "date": "2026-09-25"}]),
    ]
    seen = []

    def request(path, params):
        seen.append((path, params))
        return pages[len(seen) - 1]

    df = fredc.fetch_releases("2026-09-01", date(2026, 9, 30), request=request, limit=2)
    assert len(df) == 3 and len(seen) == 2
    assert seen[0][0] == fredc.RELEASES_DATES_PATH
    # 不开这个开关,**未来**的待发布日永远回不来(日历里就没有明天)
    assert seen[0][1]["include_release_dates_with_no_data"] == "true"
    assert (seen[0][1]["realtime_start"], seen[0][1]["realtime_end"]) == ("2026-09-01", "2026-09-30")
    assert seen[1][1]["offset"] == 2


@pytest.mark.parametrize("payload,needle", [
    ([], "不是 dict"),
    ({"releases": []}, "缺 `release_dates`"),
    ({"release_dates": {}}, "不是 list"),
])
def test_fred_shape_drift_raises_instead_of_returning_an_empty_frame(payload, needle):
    with pytest.raises(fredc.FredCalendarError, match=needle):
        fredc.fetch_releases("2026-09-01", "2026-09-30", request=lambda p, q: payload)


def test_fred_safe_separates_legit_empty_from_failure():
    contracts.clear_degradations()
    empty = fredc.fetch_releases_safe("2026-09-01", "2026-09-30",
                                      request=lambda p, q: _fred_payload([]))
    assert empty.status == "EMPTY" and empty.ok
    assert [r["kind"] for r in contracts.degradations()] == ["legit_empty"]

    contracts.clear_degradations()
    failed = fredc.fetch_releases_safe("2026-09-01", "2026-09-30",
                                       request=lambda p, q: (_ for _ in ()).throw(RuntimeError("429")))
    assert failed.status == "FAILED" and not failed.ok and "429" in failed.reason
    assert [r.get("kind") for r in contracts.degradations()] == ["degraded"]


def test_fred_events_are_always_date_only_and_dated_in_et():
    df = pd.DataFrame([{"release_id": 10, "release_name": "Consumer Price Index", "date": "2026-09-10"}])
    (e,) = fredc.releases_to_events(df, first_seen_ts=SEEN)
    assert e.time_quality == oec.TIME_QUALITY_DATE_ONLY and e.scheduled_at_utc is None
    assert e.timezone == oec.TZ_NEW_YORK and e.local_date == date(2026, 9, 10)
    assert e.event_id == "fred:10:2026-09-10" and e.event_type == "macro_release"
    assert e.first_seen_ts == SEEN                     # 调用方显式给,模块绝不 now()
    assert oec.classify_window(e, _REPORT, _T1_CLOSE, _T2_OPEN) == oec.WINDOW_OUTSIDE


def test_fred_family_filter_keeps_only_the_preregistered_families():
    df = pd.DataFrame([
        {"release_id": 10, "release_name": "Consumer Price Index", "date": "2026-09-10"},
        {"release_id": 50, "release_name": "Employment Situation", "date": "2026-09-04"},
        {"release_id": 99, "release_name": "Weekly Coil Steel Report", "date": "2026-09-05"},
    ])
    got = fredc.releases_to_events(df, first_seen_ts=SEEN, families=["cpi", "employment"])
    assert [e.event_id for e in got] == ["fred:10:2026-09-10", "fred:50:2026-09-04"]
    assert fredc.classify_family("Weekly Coil Steel Report") == "other"
    assert fredc.classify_family("PERSONAL INCOME AND OUTLAYS") == "pce"


def test_fred_fetch_events_returns_empty_on_failure_without_raising():
    events, outcome = fredc.fetch_events(
        "2026-09-01", "2026-09-30", first_seen_ts=SEEN,
        request=lambda p, q: (_ for _ in ()).throw(RuntimeError("boom")))
    assert events == [] and outcome.status == "FAILED"


def test_fred_rejects_an_inverted_range():
    with pytest.raises(fredc.FredCalendarError, match="晚于"):
        fredc.fetch_releases("2026-09-30", "2026-09-01", request=lambda p, q: _fred_payload([]))


# ═════════════════════════ 官方时刻抓取:待探针 ═════════════════════════


def test_official_time_fetch_is_deliberately_unimplemented():
    """没被真实响应验证过的解析器 = 编造。宁可保持 DATE_ONLY,也不要一个没人核过的时刻。"""
    with pytest.raises(NotImplementedError, match="待探针"):
        oec.fetch_official_times("fed")
    assert set(oec.OFFICIAL_SOURCES) == {"fed", "bls", "bea"}
