from autoresearch.learning import stage_eval


def test_selftest():
    assert stage_eval._selftest() == 0
