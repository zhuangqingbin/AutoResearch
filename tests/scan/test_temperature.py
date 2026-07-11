"""S1 情绪温度计纯函数(daily_metrics/score/phase)——零网络。"""
import tempfile
from pathlib import Path

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


def test_score_nan_fried_rate_returns_none():
    """NaN fried_rate 修复后应返回 None 而非抛 TypeError (Important#1 回归)。"""
    result = T.score({
        "n_limit_up": 50,
        "max_streak": 3,
        "fried_rate": float("nan"),
        "yesterday_premium": 1.0
    })
    assert result is None


def test_upsert_preserves_untouched_rows_bytes():
    """幂等回读: 写入含 max_streak 整数行 → 追加 max_streak=None →
    再追加无关新行 → 最早那行需数值等价(Minor#2 回归) (Minor#1 fmt漂移已知)。
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir) / "test_temp.csv"

        # 写入1:初始行含整数 max_streak
        df1 = pd.DataFrame({
            "date": ["2026-01-01"],
            "n_limit_up": [50],
            "n_limit_down": [5],
            "n_fried": [2],
            "max_streak": [5],
            "promote_rate": [0.5],
            "fried_rate": [0.038],
            "yesterday_premium": [1.0],
            "score": [55.0],
            "phase": ["修复"]
        })
        T._write(df1, tmp_path)

        # 写入2:追加一行含 max_streak=None 的行(这会导致 pandas dtype 推断变 float64)
        existing = T._load(tmp_path)
        df2_new = pd.DataFrame({
            "date": ["2026-01-02"],
            "n_limit_up": [0],
            "n_limit_down": [10],
            "n_fried": [0],
            "max_streak": [None],  # 这个 None 会导致整列变 float
            "promote_rate": [None],
            "fried_rate": [None],
            "yesterday_premium": [None],
            "score": [None],
            "phase": ["冰点"]
        })
        merged = T._upsert(existing, df2_new)
        T._write(merged, tmp_path)

        # 写入3:再追加一个无关新行
        existing = T._load(tmp_path)
        df3_new = pd.DataFrame({
            "date": ["2026-01-04"],
            "n_limit_up": [80],
            "n_limit_down": [2],
            "n_fried": [3],
            "max_streak": [7],
            "promote_rate": [0.6],
            "fried_rate": [0.036],
            "yesterday_premium": [2.5],
            "score": [70.0],
            "phase": ["发酵"]
        })
        merged = T._upsert(existing, df3_new)
        T._write(merged, tmp_path)

        # 验证:最早那行(2026-01-01)的 max_streak 需数值等价(可能格式变 5.0 但数值=5)
        result = T._load(tmp_path)
        first_row = result[result["date"] == "2026-01-01"].iloc[0]
        assert float(first_row["max_streak"]) == 5.0, \
            f"First row max_streak should equal 5.0 but got {first_row['max_streak']}"
