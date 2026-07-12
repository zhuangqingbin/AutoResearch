"""目标价 hi_2_oc 基率锚:全 universe 2 日 MFE 分布 → target_calib.json(全体+按 regime 分位)。

spec: .superpowers/sdd/task-6-brief.md(漏斗 P0+P1 波 Task 6)。动机:全卡目标触达 43%、
中位目标 +8% vs 中位 MFE +4% = 目标价系统性 2× 过乐观,用 attribution 真实 hi_2_oc 分布
给 L4 卡目标价上基率锚。合成,无网络。
"""
import json

import pandas as pd

from autoresearch.learning import buy_ledger


def _day(tmp, date, hi2, regime="risk_off"):
    d = tmp / date
    (d / "retro").mkdir(parents=True)
    pd.DataFrame({"code": [f"{i:06d}" for i in range(len(hi2))], "hi_2_oc": hi2}).to_csv(
        d / "retro" / "attribution.csv", index=False)
    (d / "meta.json").write_text(json.dumps({"regime": regime}), encoding="utf-8")


def test_hi2_calibration_quantiles(tmp_path):
    _day(tmp_path, "2026-07-08", [0.01] * 6 + [0.05] * 4)
    _day(tmp_path, "2026-07-09", [0.02] * 10, regime="range")
    out = buy_ledger.hi2_calibration(scan_root=tmp_path, window=30)
    assert out["all"]["n"] == 20
    assert 0.01 <= out["all"]["hi2_p60"] <= 0.05
    assert out["by_regime"]["range"]["n"] == 10


def test_thin_regime_shown_with_thin_flag(tmp_path):
    """P0-3:n=5(≥MIN_N_INJECT=3,<_HI2_MIN_N=10)现在会出现在 `by_regime`,标 thin=True——
    不再二值断供,只是 ⚠ 标薄样本(design 2026-07-12-selflearning-optimization-brainstorm §4)。"""
    _day(tmp_path, "2026-07-09", [0.02] * 5, regime="trend")
    out = buy_ledger.hi2_calibration(scan_root=tmp_path, window=30)
    assert "trend" in out["by_regime"]
    assert out["by_regime"]["trend"]["thin"] is True
    assert out["by_regime"]["trend"]["n"] == 5


def test_below_floor_regime_dropped(tmp_path):
    """n<MIN_N_INJECT(=3)→ 绝对禁注,不出现在 `by_regime`(不受 shrink 开关影响)。"""
    _day(tmp_path, "2026-07-09", [0.02] * 2, regime="range")
    out = buy_ledger.hi2_calibration(scan_root=tmp_path, window=30)
    assert "range" not in out["by_regime"]


def test_regime_touch8_rate_is_shrunk_toward_all(tmp_path):
    """regime touch8_rate 向 all 组收缩:risk_off 全触达(1.0)+ range 全不触达(0.0)混合出
    all touch8_rate=0.5,各 regime 收缩后应落在 raw 与 0.5 之间(真实拉力,非二值)。"""
    _day(tmp_path, "2026-07-08", [0.10] * 5, regime="risk_off")   # 全部 ≥8% → raw touch8=1.0
    _day(tmp_path, "2026-07-09", [0.01] * 5, regime="range")      # 全部 <8% → raw touch8=0.0
    out = buy_ledger.hi2_calibration(scan_root=tmp_path, window=30, shrink=True, k=15)
    assert out["all"]["touch8_rate"] == 0.5
    ro = out["by_regime"]["risk_off"]["touch8_rate"]
    rg = out["by_regime"]["range"]["touch8_rate"]
    assert 0.5 < ro < 1.0
    assert 0.0 < rg < 0.5
    expected_ro = round((5 * 1.0 + 15 * 0.5) / (5 + 15), 4)
    assert abs(ro - expected_ro) < 1e-6


def test_regime_shrink_false_returns_raw(tmp_path):
    _day(tmp_path, "2026-07-08", [0.10] * 5, regime="risk_off")
    _day(tmp_path, "2026-07-09", [0.01] * 5, regime="range")
    out = buy_ledger.hi2_calibration(scan_root=tmp_path, window=30, shrink=False)
    assert out["by_regime"]["risk_off"]["touch8_rate"] == 1.0
    assert out["by_regime"]["range"]["touch8_rate"] == 0.0


# ───────────────────────── target_calib_line:L4 逐卡 📐 行(此前无测试覆盖) ─────────────────────────


def test_target_calib_line_all_only():
    calib = {"all": {"n": 20, "hi2_p60": 0.05, "touch8_rate": 0.3}, "by_regime": {}}
    line = buy_ledger.target_calib_line(calib, regime=None)
    assert "全体 2 日 MFE p60=+5.0%" in line
    assert "n=20" in line and "30%" in line
    assert "同 regime" not in line


def test_target_calib_line_below_min_n_returns_none():
    calib = {"all": {"n": 5, "hi2_p60": 0.05, "touch8_rate": 0.3}, "by_regime": {}}
    assert buy_ledger.target_calib_line(calib, regime=None, min_n=10) is None


def test_target_calib_line_none_calib_returns_none():
    assert buy_ledger.target_calib_line(None, regime="risk_off") is None


def test_target_calib_line_regime_clause_shows_touch_rate_and_warns_thin():
    """P0-3 新增:regime 分段现在也报收缩后的 touch8_rate,n<10(既有阈值)⚠ 标薄样本。"""
    calib = {"all": {"n": 50, "hi2_p60": 0.05, "touch8_rate": 0.3},
             "by_regime": {"trend": {"n": 6, "hi2_p60": 0.08, "touch8_rate": 0.42, "thin": True}}}
    line = buy_ledger.target_calib_line(calib, regime="trend")
    assert "同 regime p60=+8.0%(n=6⚠)" in line
    assert "触达率42%" in line


def test_target_calib_line_regime_not_thin_no_warning():
    calib = {"all": {"n": 50, "hi2_p60": 0.05, "touch8_rate": 0.3},
             "by_regime": {"trend": {"n": 12, "hi2_p60": 0.08, "touch8_rate": 0.42, "thin": False}}}
    line = buy_ledger.target_calib_line(calib, regime="trend")
    assert "(n=12)" in line
    assert "⚠" not in line.split("同 regime")[1]
