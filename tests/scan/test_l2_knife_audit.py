import pandas as pd

from autoresearch.scan import l2_knife_audit as ka


def test_splits_menu_rate_into_main_and_floor():
    l1 = pd.DataFrame({"code": list("abcdefgh"),
                       "pct_60d": [-30, -25, -10, -5, -30, -25, -10, -5]})
    l2 = pd.DataFrame({"code": ["a", "b", "c", "e", "f", "g"],
                       "pct_60d": [-30, -25, -10, -30, -25, -10],
                       "l2_lane_reserved": [False, False, False, True, True, True]})
    r = ka.knife_rates(l1, l2)
    assert r["market"] == 0.5            # 8 只里 4 只 < -20
    assert r["menu"] == 4 / 6            # 6 只里 4 只 < -20
    assert r["main"] == 2 / 3
    assert r["floor"] == 2 / 3
    assert r["n_main"] == 3 and r["n_floor"] == 3


def test_missing_reserved_column_puts_all_in_main():
    """老 run 无 l2_lane_reserved 列:全算主排序,floor 记 None 而不是编 0。"""
    l1 = pd.DataFrame({"code": ["a", "b"], "pct_60d": [-30, -5]})
    l2 = pd.DataFrame({"code": ["a"], "pct_60d": [-30]})
    r = ka.knife_rates(l1, l2)
    assert r["n_floor"] == 0
    assert r["floor"] is None


def test_empty_frames_return_none_not_zero():
    r = ka.knife_rates(pd.DataFrame(), pd.DataFrame())
    assert r["market"] is None and r["menu"] is None
