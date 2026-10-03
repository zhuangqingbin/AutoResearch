"""Freeze sourced full-market membership; never reconstruct history from survivors.

The universe includes every security listed on D, including suspended names, plus D daily evidence.
Sector labels need interval evidence or an observed snapshot from that exact day.
Published membership is immutable; missing historical evidence stays UNKNOWN.
"""
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from autoresearch.common import workspace as ws
from autoresearch.common.outcome_sessions import compact

VERSION = 'benchmark_members.v1'


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def _ref(path):
    path = Path(path).resolve()
    return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def _unknown(day, reason):
    return {'schema_version': VERSION, 'analysis_date': compact(day), 'status': 'UNKNOWN',
            'classification_provider': None, 'classification_version': None,
            'universe_rule': 'listed_on_analysis_day.v1', 'source_refs': {},
            'expected_codes': [], 'members': [], 'missing_reasons': [reason]}


def build_membership(analysis_date, *, market_path, classification_path,
                     classification_provider, classification_version, snapshot_date=None,
                     observed_at=None, listing_path=None, listing_snapshot_date=None):
    day = compact(analysis_date)
    if day is None:
        raise ValueError('invalid membership analysis date')
    document = _unknown(day, 'MISSING_CLASSIFICATION_HISTORY')
    market = pd.read_parquet(market_path)
    if 'trade_date' not in market or not market['trade_date'].astype(str).eq(day).all():
        raise ValueError('market membership session mismatch')
    expected = set(market['ts_code'].astype(str).str[:6])
    listed_verified = bool(listing_path and compact(listing_snapshot_date) == day and
                           observed_at and compact(str(observed_at)[:10]) == day)
    if listed_verified:
        listing = pd.read_parquet(listing_path)
        if not {'ts_code', 'list_date'} <= set(listing.columns):
            listed_verified = False
        else:
            starts = listing['list_date'].map(compact)
            ends = listing.get('delist_date', pd.Series(None, index=listing.index, dtype=object)).map(compact)
            active = starts.notna() & (starts <= day) & (ends.isna() | (ends > day))
            expected |= set(listing.loc[active, 'ts_code'].astype(str).str[:6])
    expected = sorted(expected)
    document.update(classification_provider=classification_provider,
        classification_version=classification_version, expected_codes=expected,
        source_refs={'market': _ref(market_path), 'classification': _ref(classification_path)},
        snapshot_date=compact(snapshot_date), observed_at=observed_at,
        listing_snapshot_date=compact(listing_snapshot_date))
    if listing_path:
        document["source_refs"]["listing"] = _ref(listing_path)
    rows = pd.read_parquet(classification_path)
    if {'valid_from', 'valid_to'} <= set(rows.columns):
        start = rows['valid_from'].map(compact)
        end = rows['valid_to'].map(compact)
        rows = rows[start.notna() & (start <= day) & (end.isna() | (end >= day))].copy()
    elif compact(snapshot_date) == day and observed_at and compact(str(observed_at)[:10]) == day:
        rows = rows.copy()
        rows['valid_from'] = rows['valid_to'] = day
    else:
        rows = pd.DataFrame(columns=['code', 'sector', 'valid_from', 'valid_to'])
    if 'sector' not in rows and 'industry' in rows:
        rows = rows.rename(columns={'industry': 'sector'})
    mapping = []
    for row in rows.to_dict('records'):
        code = str(row.get('code', row.get('ts_code', ''))).split('.')[0].zfill(6)
        sector = row.get('sector')
        if code not in expected or pd.isna(sector) or str(sector) in {'', '未分类', 'nan'}:
            continue
        mapping.append({'subject': code, 'sector': str(sector),
            'valid_from': compact(row['valid_from']), 'valid_to': compact(row['valid_to'])})
    counts = pd.Series([r['subject'] for r in mapping], dtype=object).value_counts()
    duplicates = set(counts[counts > 1].index)
    mapping = [r for r in mapping if r['subject'] not in duplicates]
    document['members'] = sorted(mapping, key=lambda r: r['subject'])
    mapped = {r['subject'] for r in mapping}
    document['missing_reasons'] = (['MISSING_OR_AMBIGUOUS_CLASSIFICATION:' + c for c in sorted(set(expected) - mapped)])
    if not listed_verified:
        document['missing_reasons'].append('MISSING_LISTING_UNIVERSE')
    document['status'] = 'COMPLETE' if listed_verified and expected and mapped == set(expected) and classification_provider and classification_version else 'UNKNOWN'
    document['membership_hash'] = _hash(document)
    return document


def computation_view(document):
    """Re-read/hash the sourced bytes and rebuild; a self-reported status grants no trust."""
    unknown = {'status': 'UNKNOWN', 'expected_codes': document.get('expected_codes', []), 'members': {}}
    try:
        refs = document['source_refs']
        if any(_ref(row['path']) != row for row in refs.values()):
            return unknown
        rebuilt = build_membership(document['analysis_date'], market_path=refs['market']['path'],
            classification_path=refs['classification']['path'],
            classification_provider=document['classification_provider'],
            classification_version=document['classification_version'],
            snapshot_date=document.get('snapshot_date'), observed_at=document.get('observed_at'),
            listing_path=refs.get('listing', {}).get('path'),
            listing_snapshot_date=document.get('listing_snapshot_date'))
        if rebuilt != document:
            return unknown
        return {'status': rebuilt['status'], 'expected_codes': rebuilt['expected_codes'],
                'members': {r['subject']: r['sector'] for r in rebuilt['members']}}
    except (KeyError, OSError, ValueError):
        return unknown


def membership_root():
    return ws.context_root() / 'benchmarks' / VERSION


def load_membership(analysis_date, *, lake_daily=None):
    day = compact(analysis_date)
    path = membership_root() / day / 'membership.json'
    if path.is_file():
        return json.loads(path.read_text())
    document = _unknown(day, 'NO_HISTORICAL_MEMBERSHIP_SNAPSHOT')
    market = (Path(lake_daily) if lake_daily else ws.lake_root() / 'daily') / f'{day}.parquet'
    if market.is_file():
        document['expected_codes'] = sorted(set(pd.read_parquet(market)['ts_code'].astype(str).str[:6]))
    return document


def freeze_current_membership(analysis_date, *, lake_daily=None, now=None):
    """Live acquisition is permitted only for today's snapshot, never for old dates."""
    day = compact(analysis_date)
    injected_now = now
    now = now or datetime.now(timezone(timedelta(hours=8)))
    if now.astimezone(timezone(timedelta(hours=8))).strftime('%Y%m%d') != day:
        return load_membership(day, lake_daily=lake_daily)
    import tempfile
    import uuid

    from autoresearch.common.published_state import target_locks
    root = membership_root()
    with target_locks(root, [day]):
        target = root / day
        if (target / 'membership.json').exists():
            return load_membership(day, lake_daily=lake_daily)
        market = (Path(lake_daily) if lake_daily else ws.lake_root() / 'daily') / f'{day}.parquet'
        if not market.is_file():
            return _unknown(day, 'MISSING_ANALYSIS_DAY_MARKET')
        from autoresearch.data import cache
        from autoresearch.data.tushare_source import (
            _STOCK_BASIC_PARAMS,
            _fetch_stock_basic,
            fetch_fundamentals_yjbb,
            latest_reported_quarter,
        )
        rows = fetch_fundamentals_yjbb(f'{day[:4]}-{day[4:6]}-{day[6:]}')
        listing = _fetch_stock_basic(analysis_date)
        observed = injected_now or datetime.now(timezone(timedelta(hours=8)))
        listing_source = cache.lake_path('stock_basic', _STOCK_BASIC_PARAMS)
        source_day = (datetime.fromtimestamp(listing_source.stat().st_mtime,
            timezone(timedelta(hours=8))).strftime('%Y%m%d') if listing_source.is_file() else None)
        if source_day != day:
            document = _unknown(day, 'MISSING_LISTING_UNIVERSE')
            document['missing_reasons'].append('STALE_OR_UNVERIFIABLE_LISTING_SOURCE')
            document['listing_snapshot_date'] = source_day
            return document
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=f'.{day}-', dir=root) as temporary:
            staging = Path(temporary)
            (staging / 'market.parquet').write_bytes(market.read_bytes())
            rows.to_parquet(staging / 'classification.parquet', index=False)
            listing.to_parquet(staging / 'listing.parquet', index=False)
            document = build_membership(day, market_path=staging / 'market.parquet',
                classification_path=staging / 'classification.parquet', classification_provider='eastmoney:stock_yjbb_em',
                classification_version='reported_quarter:' + latest_reported_quarter(f'{day[:4]}-{day[4:6]}-{day[6:]}'),
                snapshot_date=day, observed_at=observed.isoformat(),
                listing_path=staging / 'listing.parquet', listing_snapshot_date=source_day)
            for reference in document['source_refs'].values():
                reference['path'] = str((target / Path(reference['path']).name).resolve())
            document.pop('membership_hash')
            document['membership_hash'] = _hash(document)
            (staging / 'membership.json').write_text(json.dumps(document, ensure_ascii=False, sort_keys=True) + '\n')
            if target.exists():
                # Preserve interrupted unpublished bytes without mistaking them for a completed snapshot.
                target.rename(root / f'.interrupted-{day}-{uuid.uuid4().hex}')
            staging.rename(target)
        return document
