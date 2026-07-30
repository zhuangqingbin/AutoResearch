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


def test_audit_records_skipped_dates_not_silent(tmp_path):
    """坏 CSV 的日子必须记进 skipped(降级必须记账,不许静默 continue 吞掉)。"""
    good = tmp_path / "2099-01-02"
    good.mkdir()
    pd.DataFrame({"code": ["x", "y"], "pct_60d": [-30, -5]}).to_csv(
        good / "L1_recall_top1000.csv", index=False)
    pd.DataFrame({"code": ["x"], "pct_60d": [-30], "l2_lane_reserved": [False]}).to_csv(
        good / "L2_gbdt_top200.csv", index=False)

    bad = tmp_path / "2099-01-01"
    bad.mkdir()
    pd.DataFrame({"code": ["x"], "pct_60d": [-30]}).to_csv(
        bad / "L1_recall_top1000.csv", index=False)
    (bad / "L2_gbdt_top200.csv").write_text("")  # 空文件 → EmptyDataError,真实的「坏 CSV」

    out = ka.audit(["2099-01-01", "2099-01-02"], root=tmp_path)

    assert out.attrs["requested"] == 2
    skipped_dates = [s["date"] for s in out.attrs["skipped"]]
    assert skipped_dates == ["2099-01-01"]
    assert out.attrs["skipped"][0]["reason"]     # 有具体原因,非空字符串
    assert list(out["date"]) == ["2099-01-02"]   # 坏日子不进结果表,但也没被悄悄吞掉不留痕
