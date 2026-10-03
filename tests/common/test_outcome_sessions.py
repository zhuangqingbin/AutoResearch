import pytest

from autoresearch.common import outcome_sessions as s

DAYS = ['20260930', '20261008', '20261009', '20261012', '20261013']

def test_holiday_calendar_and_independent_horizons():
    result = s.resolve_sessions('2026-09-30', sessions=DAYS, quality='trade_cal', today='20261009', horizons=(1,2,10))
    assert result['t1'] == '20261008'
    assert result['t2'] == '20261009'
    assert result['status'] == 'OK'
    assert result['horizon_status'][2] == 'OK'
    assert result['horizon_status'][10] == 'UNVERIFIED_CALENDAR'

@pytest.mark.parametrize('date,quality,today,status', [
    ('20261010','trade_cal','20261012','INVALID_ANALYSIS_DATE'),
    ('20260930','weekday_heuristic','20261012','UNVERIFIED_CALENDAR'),
    ('20260930','trade_cal','20261008','PENDING_SESSION'),
])
def test_invalid_weak_and_immature(date,quality,today,status):
    assert s.resolve_sessions(date,sessions=DAYS,quality=quality,today=today,horizons=(2,))['status'] == status
