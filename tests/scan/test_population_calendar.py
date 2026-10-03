import json

import pandas as pd
import pytest

from autoresearch.scan import populations as p
from tests.scan.test_populations import CODES, DAYS, _full_run, _lake


def test_missing_whole_entry_day_never_shifts_price_legs(tmp_path):
    lake = _lake(tmp_path, days=DAYS[:4])
    (lake / f'{DAYS[1]}.parquet').unlink()
    table, _ = p.build_population(_full_run(tmp_path), lake_daily=lake,
        calendar=lambda *_: (DAYS, 'trade_cal'), today=DAYS[3])
    row = table.set_index('code').loc[CODES[0]]
    assert pd.isna(row['gap_c1_o2'])
    assert row['status_gap_c1_o2'] == 'UNAVAILABLE'
    assert row['entry_session_gap_c1_o2'] == DAYS[1]
    assert row['exit_session_gap_c1_o2'] == DAYS[2]
    assert json.loads(row['missing_reasons_gap_c1_o2'])


def test_calendar_maturity_is_independent_of_inventory(tmp_path):
    lake = _lake(tmp_path, days=DAYS[:3], gaps={CODES[0]: .04})
    table, _ = p.build_population(_full_run(tmp_path), lake_daily=lake,
        calendar=lambda *_: (DAYS, 'trade_cal'), today=DAYS[2])
    row = table.set_index('code').loc[CODES[0]]
    assert row['gap_c1_o2'] == pytest.approx(.04)
    assert row['status_gap_c1_o2'] == 'MATURE'
    assert row['status_fwd_10_oc'] == 'PENDING'
    assert len(row['calendar_digest']) == 64
    assert row['label_version'] == 'outcome_labels.v2'


def test_weak_calendar_never_labels_inventory(tmp_path):
    table, _ = p.build_population(_full_run(tmp_path), lake_daily=_lake(tmp_path),
        calendar=lambda *_: (DAYS, 'lake_partitions'), today=DAYS[-1])
    assert table['gap_c1_o2'].isna().all()


@pytest.mark.parametrize("suspended_on_d", [False, True])
def test_production_sector_benchmark_includes_nonfinalist_peer(tmp_path, monkeypatch, suspended_on_d):
    from autoresearch.data import benchmark_members as bm
    from autoresearch.scan import outcome
    from tests.scan.test_populations import _run
    lake = _lake(tmp_path, days=DAYS[:3], gaps={CODES[0]: .04, CODES[1]: -.02})
    if suspended_on_d:
        daily = pd.read_parquet(lake / (DAYS[0]+'.parquet'))
        daily[daily.ts_code != CODES[1]+'.SH'].to_parquet(lake / (DAYS[0]+'.parquet'), index=False)
    history = tmp_path / 'classification.parquet'
    pd.DataFrame([{'code': code, 'sector': 'S1' if i < 2 else 'S2',
        'valid_from':'20200101', 'valid_to':None} for i, code in enumerate(CODES)]).to_parquet(history)
    listing = tmp_path / 'listing.parquet'
    pd.DataFrame({'ts_code': [c+'.SH' for c in CODES], 'list_date': ['20200101']*len(CODES)}).to_parquet(listing)
    doc = bm.build_membership(DAYS[0], market_path=lake / (DAYS[0]+'.parquet'),
        classification_path=history, classification_provider='fixture', classification_version='v1',
        listing_path=listing, listing_snapshot_date=DAYS[0], observed_at='2026-08-03T19:00:00+08:00')
    monkeypatch.setattr(bm, 'load_membership', lambda *args, **kw: doc)
    run = _run(tmp_path, top1000=[CODES[0]], finalists=[CODES[0]])
    def cal(*_):
        return DAYS, 'trade_cal'
    table, _ = p.build_population(run, lake_daily=lake, calendar=cal, today=DAYS[2])
    assert len(table) == 1
    assert table.iloc[0]['rel_gap_sector'] == pytest.approx(.03)
    result = outcome.compute_outcome(run, lake_daily=lake, calendar=cal, today=DAYS[2])
    assert result['rows'][CODES[0]]['rel_gap_sector'] == pytest.approx(.03)
    assert result['sector_benchmark']['sectors']['S1']['eligible_count'] == 2


def test_temporary_calendar_and_market_gaps_remain_retryable(tmp_path):
    run = _full_run(tmp_path)
    lake = _lake(tmp_path)
    table, meta = p.build_population(run, lake_daily=lake,
        calendar=lambda *_: (DAYS, 'lake_partitions'), today=DAYS[-1])
    target = tmp_path / 'population.parquet'
    p._atomic_parquet(table, target, meta)
    assert p._has_pending(target)
    (lake / (DAYS[1]+'.parquet')).unlink()
    table, meta = p.build_population(run, lake_daily=lake,
        calendar=lambda *_: (DAYS, 'trade_cal'), today=DAYS[-1])
    p._atomic_parquet(table, target, meta)
    assert p._has_pending(target)


def test_old_versioned_labels_without_retry_metadata_are_revisited(tmp_path):
    target = tmp_path / 'population.parquet'
    pd.DataFrame({'status_gap_c1_o2':['UNAVAILABLE']}).to_parquet(target)
    assert p._has_pending(target)
