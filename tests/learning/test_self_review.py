from autoresearch.learning import self_review


def test_selftest():
    assert self_review._selftest() == 0
