"""扫描数据日解析:缺省 = 最近已结算交易日(节假日/周末自动回退);显式非交易日拒绝并给建议。

2026-09-26(周六;9/25–9/27 中秋休市)主会话靠手工探 trade_cal 才定出 2026-09-24。
"""
from datetime import datetime

import pytest

# 2026-09 真实日历片段:9/19–20 周末,9/25–27 中秋休市,9/28 开市
_OPEN = ["20260917", "20260918", "20260921", "20260922", "20260923", "20260924", "20260928"]


@pytest.fixture
def cal(monkeypatch):
    import autoresearch.data.tushare_source as ts
    monkeypatch.setattr(ts, "_pro", lambda: object())
    monkeypatch.setattr(ts, "_trade_days",
                        lambda pro, s, e: [d for d in _OPEN if s.replace("-", "") <= d <= e.replace("-", "")])


def test_default_skips_holiday_and_weekend(cal):
    from autoresearch.scan.trade_date import resolve_scan_date
    assert resolve_scan_date(None, now=datetime(2026, 9, 26, 10, 47)) == "2026-09-24"   # 周六·中秋
    assert resolve_scan_date(None, now=datetime(2026, 9, 28, 9, 0)) == "2026-09-24"     # 开市日盘前
    assert resolve_scan_date(None, now=datetime(2026, 9, 28, 19, 30)) == "2026-09-28"   # 当日已结算


def test_explicit_trading_day_passes_through(cal):
    from autoresearch.scan.trade_date import resolve_scan_date
    assert resolve_scan_date("2026-09-24", now=datetime(2026, 9, 26, 10, 0)) == "2026-09-24"


def test_explicit_holiday_is_rejected_with_suggestion(cal):
    from autoresearch.scan.trade_date import NotATradingDay, resolve_scan_date
    with pytest.raises(NotATradingDay, match="2026-09-24"):
        resolve_scan_date("2026-09-25", now=datetime(2026, 9, 26, 10, 0))


def test_explicit_future_day_is_rejected(cal):
    from autoresearch.scan.trade_date import NotATradingDay, resolve_scan_date
    with pytest.raises(NotATradingDay):
        resolve_scan_date("2026-09-28", now=datetime(2026, 9, 26, 10, 0))


def test_cli_prints_only_the_date(cal, capsys):
    from autoresearch.scan import trade_date
    assert trade_date.main([], now=datetime(2026, 9, 26, 10, 47)) == 0
    assert capsys.readouterr().out.strip() == "2026-09-24"
    assert trade_date.main(["2026-09-25"], now=datetime(2026, 9, 26, 10, 47)) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and "2026-09-24" in captured.err
