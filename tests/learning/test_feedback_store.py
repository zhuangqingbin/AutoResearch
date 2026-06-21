from autoresearch.learning import feedback_store


def test_selftest():
    assert feedback_store._selftest() == 0
