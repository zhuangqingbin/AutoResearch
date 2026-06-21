from autoresearch.common import vol_series


def test_selftest():
    assert vol_series._selftest() == 0
