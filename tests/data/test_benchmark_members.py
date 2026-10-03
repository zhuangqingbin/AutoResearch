import pandas as pd

from autoresearch.data import benchmark_members as bm


def sources(tmp_path):
    market = tmp_path / '20260901.parquet'
    pd.DataFrame({'ts_code': ['600000.SH','600001.SH','600002.SH'], 'trade_date': ['20260901']*3}).to_parquet(market)
    classification = tmp_path / 'history.parquet'
    pd.DataFrame([
        {'code':'600000','sector':'S0','valid_from':'20260101','valid_to':'20260831'},
        {'code':'600000','sector':'S1','valid_from':'20260901','valid_to':None},
        {'code':'600001','sector':'S1','valid_from':'20200101','valid_to':'20260902'},
        {'code':'600002','sector':'S2','valid_from':'20200101','valid_to':None},
    ]).to_parquet(classification)
    return market, classification


def listing_args(tmp_path):
    listing = tmp_path / 'listing.parquet'
    pd.DataFrame({'ts_code':['600000.SH','600001.SH','600002.SH'],
        'list_date':['20200101']*3, 'delist_date':[None,None,None]}).to_parquet(listing)
    return {'listing_path': listing, 'listing_snapshot_date': '20260901',
                'observed_at': '2026-09-01T19:00:00+08:00'}


def test_sourced_history_preserves_change_and_later_delisted_peer(tmp_path):
    market, history = sources(tmp_path)
    document = bm.build_membership('2026-09-01', market_path=market, classification_path=history,
        classification_provider='fixture:historical', classification_version='v1', **listing_args(tmp_path))
    assert document['status'] == 'COMPLETE'
    view = bm.computation_view(document)
    assert view['members']['600000'] == 'S1'
    assert view['members']['600001'] == 'S1'
    assert len(document['membership_hash']) == 64


def test_current_survivors_cannot_fill_history(tmp_path):
    market, history = sources(tmp_path)
    pd.DataFrame({'code':['600000'], 'industry':['S1']}).to_parquet(history)
    document = bm.build_membership('2026-09-01', market_path=market, classification_path=history,
        classification_provider='fixture:current', classification_version='v1', snapshot_date='2026-10-01')
    assert document['status'] == 'UNKNOWN'
    assert bm.computation_view(document)['members'] == {}


def test_forged_verified_flag_cannot_replace_source_bytes(tmp_path):
    market, history = sources(tmp_path)
    document = bm.build_membership('2026-09-01', market_path=market, classification_path=history,
        classification_provider='fixture:historical', classification_version='v1', **listing_args(tmp_path))
    history.write_bytes(b'tampered')
    assert bm.computation_view({**document, 'status':'VERIFIED'})['status'] == 'UNKNOWN'


def test_old_day_never_fetches_live_classification(tmp_path, monkeypatch):
    from datetime import datetime, timezone

    from autoresearch.data import tushare_source
    monkeypatch.setattr(bm, 'membership_root', lambda: tmp_path / 'snapshots')
    monkeypatch.setattr(tushare_source, 'fetch_fundamentals_yjbb', lambda *_: (_ for _ in ()).throw(AssertionError('historical backfill attempted')))
    doc = bm.freeze_current_membership('2026-09-01', lake_daily=tmp_path,
        now=datetime(2026,10,1,tzinfo=timezone.utc))
    assert doc['status'] == 'UNKNOWN'


def test_observed_snapshot_requires_same_day_source_receipt(tmp_path):
    market, history = sources(tmp_path)
    pd.DataFrame({'code':['600000','600001','600002'], 'industry':['S1','S1','S2']}).to_parquet(history)
    args = {'market_path': market, 'classification_path': history,
        'classification_provider': 'fixture:current', 'classification_version': 'v1', 'snapshot_date': '20260901'}
    assert bm.build_membership('20260901', **args)['status'] == 'UNKNOWN'
    assert bm.build_membership('20260901', **args, **listing_args(tmp_path))['status'] == 'COMPLETE'


def test_daily_presence_alone_is_not_complete_listing_universe(tmp_path):
    market, history = sources(tmp_path)
    doc = bm.build_membership('20260901', market_path=market, classification_path=history,
        classification_provider='fixture', classification_version='v1')
    assert doc['status'] == 'UNKNOWN'
    assert 'MISSING_LISTING_UNIVERSE' in doc['missing_reasons']


def test_listed_suspended_member_included_and_tomorrow_listing_excluded(tmp_path):
    market, history = sources(tmp_path)
    listing = tmp_path / 'listing.parquet'
    pd.DataFrame({'ts_code':['600000.SH','600001.SH','600002.SH','600003.SH','600004.SH'],
        'list_date':['20200101']*4+['20260902'], 'delist_date':[None]*5}).to_parquet(listing)
    rows = pd.read_parquet(history)
    pd.concat([rows, pd.DataFrame([{'code':'600003','sector':'S1','valid_from':'20200101', 'valid_to':None}])]).to_parquet(history)
    doc = bm.build_membership('20260901', market_path=market, classification_path=history,
        listing_path=listing, listing_snapshot_date='20260901', observed_at='2026-09-01T19:00:00+08:00',
        classification_provider='fixture', classification_version='v1')
    assert doc['status'] == 'COMPLETE'
    assert '600003' in doc['expected_codes']
    assert '600004' not in doc['expected_codes']


def test_stale_listing_fallback_cannot_be_certified_today(tmp_path, monkeypatch):
    from datetime import datetime, timezone

    from autoresearch.data import cache, tushare_source
    market, history = sources(tmp_path)
    monkeypatch.setattr(bm, 'membership_root', lambda: tmp_path / 'snapshots')
    monkeypatch.setattr(tushare_source, 'fetch_fundamentals_yjbb', lambda *_: pd.DataFrame({'code':['600000','600001','600002'], 'industry':['S1','S1','S2']}))
    listing = pd.DataFrame({'ts_code':['600000.SH','600001.SH','600002.SH'], 'list_date':['20200101']*3})
    old = tmp_path / 'stale-listing.parquet'
    listing.to_parquet(old)
    import os
    os.utime(old, (1,1))
    monkeypatch.setattr(tushare_source, '_fetch_stock_basic', lambda *_: listing)
    monkeypatch.setattr(cache, 'lake_path', lambda *a, **kw: old)
    doc = bm.freeze_current_membership('2026-09-01', lake_daily=tmp_path,
        now=datetime(2026,9,1,12,tzinfo=timezone.utc))
    assert doc['status'] == 'UNKNOWN'
    assert 'MISSING_LISTING_UNIVERSE' in doc['missing_reasons']


def test_unpublished_partial_snapshot_directory_can_be_retried(tmp_path, monkeypatch):
    import os
    from datetime import datetime, timezone

    from autoresearch.data import cache, tushare_source
    market, history = sources(tmp_path)
    root = tmp_path / 'snapshots'
    (root / '20260901').mkdir(parents=True)
    (root / '20260901' / 'market.parquet').write_bytes(b'interrupted')
    monkeypatch.setattr(bm, 'membership_root', lambda: root)
    monkeypatch.setattr(tushare_source, 'fetch_fundamentals_yjbb', lambda *_: pd.DataFrame({'code':['600000','600001','600002'], 'industry':['S1','S1','S2']}))
    listing = pd.DataFrame({'ts_code':['600000.SH','600001.SH','600002.SH'], 'list_date':['20200101']*3})
    source = tmp_path / 'listing-current.parquet'
    listing.to_parquet(source)
    now = datetime(2026,9,1,12,tzinfo=timezone.utc)
    os.utime(source, (now.timestamp(),now.timestamp()))
    monkeypatch.setattr(tushare_source, '_fetch_stock_basic', lambda *_: listing)
    monkeypatch.setattr(cache, 'lake_path', lambda *a, **kw: source)
    doc = bm.freeze_current_membership('20260901', lake_daily=tmp_path, now=now)
    assert doc['status'] == 'COMPLETE'
    assert bm.computation_view(doc)['status'] == 'COMPLETE'
