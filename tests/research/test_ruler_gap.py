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


# ── review fix(2026-08-07):数缺时旗列必须读成「未知」,不是伪造的 False/True ──
# col() 对未发布日期降级全 NaN,但 <=/>= 对 NaN 操作数按 IEEE754 恒返回 False、不传染;
# 旧 fillna(False) 对本就不含 NaN 的纯 bool 列是无效兜底 —— 会把「不知道」误读成「卖得出/
# 买得进」,复现 data-contracts-fail-fast 同族反模式(降级不留痕)。


def test_unsellable_o2_na_when_d2_missing():
    """D+2 未发布(无 D2 行 → pivot 无该列,同 col() 的"越界/EOD 未发布"降级路径)→
    unsellable_o2 必须是 <NA>(未知),不是 False。gap_c1_o2 仍走算术传染,保持 NaN。"""
    piv = _piv([("000001", "D0", 9.0, 9.9, 8.9, 9.5, 1.0),
                ("000001", "D1", 9.6, 10.2, 9.4, 10.0, 5.26)])   # 无 D2 行 → D2 列整体缺失
    r = forward_returns(piv, P, "D0", 10)
    assert pd.isna(r.loc["000001", "unsellable_o2"]), "D+2 缺数必须读成未知,不是 False"
    assert pd.isna(r.loc["000001", "gap_c1_o2"])


def test_buyable_c1_na_when_d1_missing():
    """D+1 未发布(无 D1/D2 行)→ buyable_c1 必须是 <NA>(未知),不是 True(同族 bug:
    会把"不知道"误读成"可买")。"""
    piv = _piv([("000001", "D0", 9.0, 9.9, 8.9, 9.5, 1.0)])     # 只有 D0,D1/D2 全缺
    r = forward_returns(piv, P, "D0", 10)
    assert pd.isna(r.loc["000001", "buyable_c1"]), "D+1 缺数必须读成未知,不是 True"
