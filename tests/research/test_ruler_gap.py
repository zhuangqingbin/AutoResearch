"""Wave11 A1:隔夜尺三列 —— 腿别搞错(gap≠oo≠oc),可执行域按收盘封板/一字跌停开。"""
import pandas as pd

from autoresearch.research.factor_lab import forward_returns


def _piv(rows):
    df = pd.DataFrame(rows, columns=["code", "date", "open", "high", "low", "close", "pct_chg"])
    return {f: df.pivot_table(index="code", columns="date", values=f)
            for f in ("open", "high", "low", "close", "pct_chg")}


P = ["D0", "D1", "D2"]

def test_gap_legs_exact():
    piv = _piv([("000001", "D0", 9.0, 9.9, 8.9, 9.5, 1.0),
                ("000001", "D1", 9.6, 10.2, 9.4, 10.0, 5.26),    # c1=10.0
                ("000001", "D2", 10.5, 11.0, 10.3, 10.8, 8.0)])  # o2=10.5
    r = forward_returns(piv, P, "D0", 10)
    assert abs(r.loc["000001", "gap_c1_o2"] - 0.05) < 1e-9       # 10.5/10.0−1;错腿(c2/c1=8%)会红


def test_buyable_c1_limit_up_close():
    piv = _piv([("300999", "D0", 10.0, 10.0, 10.0, 10.0, 0.0),
                ("300999", "D1", 11.0, 12.0, 11.0, 12.0, 20.0),  # 20cm 收盘=最高=封板
                ("300999", "D2", 12.5, 12.6, 12.4, 12.5, 4.2)])
    r = forward_returns(piv, P, "D0", 10)
    assert not bool(r.loc["300999", "buyable_c1"])


def test_unsellable_o2_one_line_down_open():
    piv = _piv([("600002", "D0", 10.0, 10.1, 9.9, 10.0, 0.0),
                ("600002", "D1", 10.0, 10.1, 9.9, 10.0, 0.0),    # c1=10.0(10cm)
                ("600002", "D2", 9.0, 9.3, 9.0, 9.1, -9.0)])     # o2=9.0=low=跌停开
    r = forward_returns(piv, P, "D0", 10)
    assert bool(r.loc["600002", "unsellable_o2"])
    assert abs(r.loc["600002", "gap_c1_o2"] + 0.10) < 1e-9       # 亏损留样本
