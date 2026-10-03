"""Pure equal-weight sector benchmark over an IO-validated market membership."""
import numpy as np
import pandas as pd


def sector_excess(gaps, eligible, membership):
    gaps = pd.Series(gaps, dtype=float)
    eligible = pd.Series(eligible, dtype='boolean')
    members = membership.get('members', {})
    expected = set(membership.get('expected_codes', []))
    missing_mapping = sorted(c for c in expected if not members.get(c))
    meta = {'status': 'COMPLETE', 'expected_count': len(expected),
            'mapped_count': len(expected) - len(missing_mapping),
            'missing_mappings': missing_mapping, 'sectors': {}}
    values = pd.Series(np.nan, index=gaps.index, dtype=float)
    source_ok = membership.get('status') == 'COMPLETE' and expected and not missing_mapping
    for sector in sorted({members[c] for c in expected if members.get(c)}):
        codes = sorted(c for c in expected if members.get(c) == sector)
        flags = eligible.reindex(codes)
        eligible_codes = list(flags[flags.fillna(False)].index)
        returns = gaps.reindex(eligible_codes)
        missing_prices = list(returns[returns.isna()].index)
        valid = bool(source_ok and not flags.isna().any() and not missing_prices and eligible_codes)
        meta['sectors'][sector] = {
            'status': 'COMPLETE' if valid else 'UNKNOWN', 'expected_count': len(codes),
            'eligible_count': len(eligible_codes), 'priced_count': int(returns.notna().sum()),
            'unknown_eligibility': list(flags[flags.isna()].index), 'missing_prices': missing_prices,
        }
        if valid:
            subjects = gaps.index.intersection(codes)
            values.loc[subjects] = gaps.loc[subjects] - returns.mean()
        else:
            meta['status'] = 'UNKNOWN'
    if not source_ok:
        meta['status'] = 'UNKNOWN'
    return values, meta
