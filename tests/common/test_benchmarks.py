import pandas as pd
import pytest

from autoresearch.common import benchmarks as b


def view(members=None):
    return {'status': 'COMPLETE', 'expected_codes': ['A', 'B', 'C'],
            'members': members or {'A': 'S1', 'B': 'S1', 'C': 'S2'}}


def test_whole_sector_mean_includes_nonfinalist_and_self():
    values, meta = b.sector_excess(pd.Series({'A': .04, 'B': -.02, 'C': 0}),
        pd.Series({'A': True, 'B': True, 'C': True}), view())
    assert values['A'] == pytest.approx(.03)
    assert values['C'] == 0
    assert meta['sectors']['S1']['eligible_count'] == 2
    assert meta['sectors']['S2']['eligible_count'] == 1


@pytest.mark.parametrize('kind', ['mapping', 'eligibility', 'price', 'source'])
def test_incomplete_denominators_are_unknown(kind):
    membership = view()
    gaps = pd.Series({'A': .04, 'B': -.02, 'C': 0})
    eligible = pd.Series({'A': True, 'B': True, 'C': True}, dtype='boolean')
    if kind == 'mapping':
        del membership['members']['B']
    if kind == 'eligibility':
        eligible['B'] = pd.NA
    if kind == 'price':
        gaps['B'] = float('nan')
    if kind == 'source':
        membership['status'] = 'UNKNOWN'
    values, meta = b.sector_excess(gaps, eligible, membership)
    assert pd.isna(values['A'])
    assert meta['expected_count'] == 3
    assert meta['status'] == 'UNKNOWN'
