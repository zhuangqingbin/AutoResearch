import numpy as np
import pandas as pd

from autoresearch.learning import retro


def test_selftest():
    assert retro._selftest() == 0


def test_winner_follows_fwd2_not_fwd1():
    """主归因主尺=fwd_2_oc:T+1 大涨但 T+2 回吐的票不是赢家;反之才是。"""
    n = 40
    realized = pd.DataFrame({
        "code": [f"{i:06d}" for i in range(n)],
        "fwd_1_oo": [0.08] + [0.0] * (n - 1),            # 000000 只赢在 T+1
        "fwd_2_oc": [0.0] + [0.06] + [0.001] * (n - 2),  # 000001 赢在 T+2(主尺)
        "fwd_5_oc": [np.nan] * n,
        "buyable": [True] * n,
    })
    l1 = pd.DataFrame({"code": realized["code"], "composite": 0.5, "recalled": False})
    attr = retro.attribute_frame(l1, realized, buylist={})
    w = attr[attr["winner"]]
    assert set(w["code"]) == {"000001"}
