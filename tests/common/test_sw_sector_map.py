from autoresearch.common import sw_sector_map


def test_selftest():
    assert sw_sector_map._selftest() == 0
