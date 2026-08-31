"""D-2 隔夜窗海外事件日历(`autoresearch/scan/overseas.py`)。

design: docs/specs/2026-08-28-external-evidence-expansion-design.md §2 / §10 D-2

本文件钉三件事:**窗口锚算对**(A 股交易日历 + IANA 时区,过 DST)、**PIT 不透视**
(`first_seen_ts > cutoff` 不可见)、**展示是提示不是触发器**(不产生否决/仓位/评级)。
零真网络:所有源都 monkeypatch。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from autoresearch.data.sources import official_event_calendar as oec
from autoresearch.scan import overseas

CN = ZoneInfo("Asia/Shanghai")
US = ZoneInfo("America/New_York")


def _ev(event_id, *, at=None, local_date=None, tq="TIMED", first_seen=None,
        subject="X", symbols=(), status="scheduled", revision="r1",
        event_type="earnings"):
    if at is not None and local_date is None:
        local_date = at.astimezone(US).date()
    return oec.ExternalEvent(
        event_id=event_id, event_type=event_type, subject=subject,
        scheduled_at_utc=at.astimezone(ZoneInfo("UTC")) if at else None,
        local_date=local_date, timezone="America/New_York", time_quality=tq,
        source_url="https://example.com/a", revision=revision, status=status,
        first_seen_ts=first_seen or datetime(2026, 8, 26, 1, 0, tzinfo=ZoneInfo("UTC")),
        mapped_symbols=tuple(symbols))


def test_anchors_follow_the_ashare_calendar_and_operational_cutoff():
    """三锚 = (报告时刻, T+1 **14:45**, T+2 09:30);T+1/T+2 走 A 股交易日历。

    14:45 不是交易所的 14:57 —— 人读完报告还要下单(与 `scan/exec_anchor.py` 同一个数)。
    """
    rep, t1, t2 = overseas.anchors("2026-08-26")
    assert (t1.hour, t1.minute) == (14, 45)
    assert (t2.hour, t2.minute) == (9, 30)
    assert t1.tzinfo is not None and t2.tzinfo is not None      # 一律 aware
    assert rep.date().isoformat() == "2026-08-26"
    assert t1.date() > rep.date() and t2.date() > t1.date()


def test_windows_split_pre_entry_from_holding_overnight():
    """T+1 14:45 之前 = 入场前(人审核对项);T+1 15:00 之后 = 持仓隔夜(风险)。"""
    rep, t1, t2 = overseas.anchors("2026-08-26")
    before = _ev("a", at=t1 - timedelta(hours=3))
    after = _ev("b", at=t1 + timedelta(hours=6))
    assert oec.classify_window(before, rep, t1, t2) == "pre_entry"
    assert oec.classify_window(after, rep, t1, t2) == "holding_overnight"


def test_events_not_yet_seen_at_cutoff_are_invisible():
    """PIT:报告时点还不可知的事件进不了任何窗 —— 历史真实发生日只能用于事后研究。"""
    rep, t1, t2 = overseas.anchors("2026-08-26")
    late = _ev("c", at=t1 + timedelta(hours=2), first_seen=t2)   # 到 T+2 才被看到
    assert oec.classify_window(late, rep, t1, t2) == "outside"
    assert oec.visible_at([late], rep) == []


def test_date_only_never_pretends_to_be_a_timed_window():
    """只有日期没有时刻 → 最多是「当日风险」,**不许猜 AMC/BMO** 补成精确窗。"""
    rep, t1, t2 = overseas.anchors("2026-08-26")
    d = _ev("d", local_date=t1.date(), tq="DATE_ONLY")
    assert oec.classify_window(d, rep, t1, t2) in ("date_risk", "outside")
    assert d.scheduled_at_utc is None


def test_write_csv_keeps_header_when_there_are_no_events(tmp_path):
    """空日历也落表头 —— 「空文件」与「这一步没跑」必须分得开。"""
    p = overseas.write_csv(tmp_path, [])
    assert p.exists()
    head = p.read_text(encoding="utf-8").splitlines()[0]
    assert head.startswith("event_id,event_type,subject,window")
    assert overseas.summary_lines(tmp_path) == []
    assert overseas.brief_line(tmp_path) == ""


def test_summary_lines_cap_and_fold(tmp_path):
    """展示上限:summary ≤4 行,超出折成 `+N`(不是丢弃)。"""
    rep, t1, _ = overseas.anchors("2026-08-26")
    evs = []
    for i in range(6):
        e = _ev(f"e{i}", at=t1 - timedelta(hours=i + 1), subject=f"事件{i}")
        evs.append(oec.ExternalEvent(**{**e.__dict__, "window": "pre_entry"}))
    overseas.write_csv(tmp_path, evs)
    lines = overseas.summary_lines(tmp_path)
    assert len(lines) == overseas.MAX_SUMMARY_ROWS + 1
    assert "+2" in lines[-1]
    assert all("**入场前**" in ln or "另有" in ln for ln in lines)


def test_tripwire_rows_only_fire_for_mapped_symbols(tmp_path):
    """📌 哨兵只对**该票映射名**命中的事件出行;无映射 / 不相关 → []。"""
    rep, t1, _ = overseas.anchors("2026-08-26")
    e = _ev("f", at=t1 + timedelta(hours=5), subject="NVDA 财报", symbols=("NVDA",))
    overseas.write_csv(tmp_path, [oec.ExternalEvent(**{**e.__dict__, "window": "holding_overnight"})])
    assert overseas.tripwire_rows(tmp_path, "300857", ["NVDA"])
    assert overseas.tripwire_rows(tmp_path, "300857", ["AAPL"]) == []
    assert overseas.tripwire_rows(tmp_path, "300857", []) == []


def test_run_never_raises_when_every_source_is_down(tmp_path, monkeypatch):
    """B 级:所有源炸掉 → 落空表 + 记降级,**不抛**(日历不能挡住 prelude)。"""
    def boom(*a, **k):
        raise RuntimeError("source down")
    monkeypatch.setattr(overseas, "_fred_events", boom)
    monkeypatch.setattr(overseas, "_fomc_events", boom)
    monkeypatch.setattr(overseas, "_earnings_events", boom)
    got = overseas.run("2026-08-26", tmp_path, symbols=("NVDA",))
    assert got["n"] == 0
    assert (tmp_path / overseas.OVERSEAS_CSV).exists()


def test_collect_returns_a_flat_event_list_after_display_sort(monkeypatch):
    """展示排序的 `(rows, overflow)` 契约不能泄漏给 CSV 消费者。"""
    _, t1, _ = overseas.anchors("2026-08-26")
    event = _ev("flat", at=t1 - timedelta(hours=3))
    monkeypatch.setattr(overseas, "_fred_events", lambda *a, **k: [event])
    monkeypatch.setattr(overseas, "_fomc_events", lambda *a, **k: [])
    monkeypatch.setattr(overseas, "_earnings_events", lambda *a, **k: [])

    got = overseas.collect("2026-08-26", symbols=())

    assert isinstance(got, list)
    assert [e.event_id for e in got] == ["flat"]


def test_display_is_advice_not_a_trigger(tmp_path):
    """展示层纪律:行文里不得出现动作/方向措辞(它只是人工复核提示)。"""
    rep, t1, _ = overseas.anchors("2026-08-26")
    e = _ev("g", at=t1 + timedelta(hours=4), subject="FOMC 决议", event_type="fomc")
    overseas.write_csv(tmp_path, [oec.ExternalEvent(**{**e.__dict__, "window": "holding_overnight"})])
    text = "\n".join(overseas.summary_lines(tmp_path)) + overseas.brief_line(tmp_path)
    for banned in ("买入", "卖出", "减仓", "加仓", "看多", "看空", "所以", "传导", "带动"):
        assert banned not in text


@pytest.mark.parametrize("date_str", ["2026-01-15", "2026-07-15"])
def test_anchor_timezone_is_iana_not_a_hardcoded_offset(date_str):
    """冬/夏两侧都要成立 —— 写死 UTC+8 / ET 偏移的实现会在其中一侧错一小时。"""
    rep, t1, t2 = overseas.anchors(date_str)
    for ts in (rep, t1, t2):
        assert ts.tzinfo is not None
        assert ts.tzinfo.key == "Asia/Shanghai"      # type: ignore[attr-defined]


def test_t2_anchor_uses_the_trading_calendar_not_a_calendar_day():
    """T+2 必须也走交易日历 —— 周五的 run 曾把「T+2 开盘」锚到**周六 09:30**。

    2026-08-29 D-0 普查逮到:原实现只有 T+1 走了日历,T+2 写成 `t1 + 1 自然日`,
    于是整个周末的美股事件被判成窗外(而周末恰恰是「T+1 周五买 → T+2 周一开盘卖」
    这条真实持仓路径最长的一段)。
    """
    class _Cal:
        def next_trade_day(self, d):
            from datetime import date, timedelta
            cur = date.fromisoformat(d) + timedelta(days=1)
            while cur.weekday() >= 5:          # 跳过周六日
                cur += timedelta(days=1)
            return cur.isoformat()

    # 2026-08-27 是周四 → T+1 周五 08-28、T+2 **周一 08-31**(不是周六 08-29)
    rep, t1, t2 = overseas.anchors("2026-08-27", trade_days=_Cal())
    assert t1.date().isoformat() == "2026-08-28" and t1.weekday() == 4
    assert t2.date().isoformat() == "2026-08-31", "T+2 落到了非交易日"
    assert t2.weekday() < 5


def test_fred_leg_returns_events_not_a_dataframe(monkeypatch):
    """FRED 腿必须产出 `ExternalEvent`(且全 `DATE_ONLY`)。

    原实现直接返回 `fetch_releases` 的 DataFrame,到 `visible_at` 就炸、又被 `collect`
    的 B 级 except 吞掉 —— 日历里 FRED 恒为空**且不报警**。
    """
    import pandas as pd
    from autoresearch.data.sources import fred_calendar

    class _Out:
        rows = pd.DataFrame([{"release_id": 10, "release_name": "Consumer Price Index",
                              "date": "2026-08-28"}])
        status, reason = "OK", ""
    monkeypatch.setattr(fred_calendar, "fetch_releases_safe", lambda *a, **k: _Out())
    got = overseas._fred_events("2026-08-26", "2026-09-09", None)
    assert got and all(isinstance(e, oec.ExternalEvent) for e in got)
    assert all(e.time_quality == "DATE_ONLY" and e.scheduled_at_utc is None for e in got)


def test_a_dead_source_is_recorded_not_swallowed(monkeypatch):
    """少一个源可以,**不说话不行**:逐源异常必须落 `record_degradation`。"""
    seen = []
    monkeypatch.setattr("autoresearch.data.contracts.record_degradation",
                        lambda key, detail="": seen.append(key))

    def boom(*a, **k):
        raise RuntimeError("limit is not between 1 and 1000")
    monkeypatch.setattr(overseas, "_fred_events", boom)
    monkeypatch.setattr(overseas, "_fomc_events", lambda *a, **k: [])
    monkeypatch.setattr(overseas, "_earnings_events", lambda *a, **k: [])
    overseas.collect("2026-08-26")
    assert any("fred" in k for k in seen), "源死了却没记降级 —— 静默降级正是要治的病"
