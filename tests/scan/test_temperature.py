"""S1 情绪温度计纯函数(daily_metrics/score/phase)——零网络。"""
import pandas as pd

from autoresearch.scan import temperature as T


def _lu(rows):   # rows = [(code, limit, limit_times)]
    return pd.DataFrame([{"ts_code": c, "limit": lim, "limit_times": t} for c, lim, t in rows])


def test_daily_metrics_counts_and_promote():
    prev = _lu([("A.SZ", "U", 1), ("B.SZ", "U", 2), ("C.SZ", "U", 1)])
    today = _lu([("A.SZ", "U", 2), ("D.SZ", "U", 1), ("E.SZ", "Z", 0), ("F.SZ", "D", 1)])
    pct = pd.DataFrame({"ts_code": ["A.SZ", "B.SZ", "C.SZ"], "pct_chg": [10.0, -2.0, 1.0]})
    m = T.daily_metrics(today, prev, pct)
    assert m["n_limit_up"] == 2 and m["n_limit_down"] == 1 and m["n_fried"] == 1
    assert m["max_streak"] == 2
    assert abs(m["promote_rate"] - 0.5) < 1e-9        # 昨 1 板 2 只(A,C)→今 2 板 1 只(A)
    assert abs(m["fried_rate"] - 1 / 3) < 1e-9        # Z / (U+Z)
    assert abs(m["yesterday_premium"] - 3.0) < 1e-9   # 昨 U 三只今日均涨幅


def test_metrics_presence_gated():
    m = T.daily_metrics(_lu([("A.SZ", "U", 1)]), None, None)
    assert m["promote_rate"] is None and m["yesterday_premium"] is None


def test_score_bounds_and_none():
    hot = {"n_limit_up": 120, "max_streak": 7, "fried_rate": 0.1, "yesterday_premium": 4.0}
    cold = {"n_limit_up": 15, "max_streak": 2, "fried_rate": 0.45, "yesterday_premium": -2.0}
    assert T.score(hot) > 70 > T.score(cold) > 0
    assert T.score({"n_limit_up": None, "max_streak": 3, "fried_rate": 0.2, "yesterday_premium": 1}) is None


def test_phase_hysteresis():
    assert T.phase(15, None, None) == "冰点"
    assert T.phase(30, 15, "冰点") == "修复"           # 上行跨带
    assert T.phase(50, 30, "修复") == "发酵"
    assert T.phase(70, 50, "发酵") == "高潮"
    assert T.phase(55, 70, "高潮") == "退潮"           # 下行 → 退潮
    assert T.phase(41, 42, "发酵") == "发酵"           # 带内小幅回落(<3)不切 = 滞回
