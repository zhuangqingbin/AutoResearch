"""目标价 hi_2_oc 基率锚:全 universe 2 日 MFE 分布 → target_calib.json(全体+按 regime 分位)。

spec: .superpowers/sdd/task-6-brief.md(漏斗 P0+P1 波 Task 6)。动机:全卡目标触达 43%、
中位目标 +8% vs 中位 MFE +4% = 目标价系统性 2× 过乐观,用 attribution 真实 hi_2_oc 分布
给 L4 卡目标价上基率锚。合成,无网络。
"""
import json

import pandas as pd

from autoresearch.learning import buy_ledger


def _day(tmp, date, hi2, regime="risk_off"):
    """v3 窗口内的一天(调用方必须传 `_SCHEMA_SWITCH`≤date<`SCHEMA_SWITCH_V4` 的日期字面量,
    如 "2026-07-20"——C3 修复后 `hi2_calibration` 按 `_hi_col_for(date)` 逐日选源列,日期落
    在 "2026-07-10" 前会被判成 `hi_10_oc` 段,本 fixture 只写 `hi_2_oc` 会读出 0 行)。"""
    d = tmp / date
    (d / "retro").mkdir(parents=True)
    pd.DataFrame({"code": [f"{i:06d}" for i in range(len(hi2))], "hi_2_oc": hi2}).to_csv(
        d / "retro" / "attribution.csv", index=False)
    (d / "meta.json").write_text(json.dumps({"regime": regime}), encoding="utf-8")


def test_hi2_calibration_quantiles(tmp_path):
    _day(tmp_path, "2026-07-20", [0.01] * 6 + [0.05] * 4)
    _day(tmp_path, "2026-07-21", [0.02] * 10, regime="range")
    out = buy_ledger.hi2_calibration(scan_root=tmp_path, window=30)
    assert out["all"]["n"] == 20
    assert 0.01 <= out["all"]["hi2_p60"] <= 0.05
    assert out["by_regime"]["range"]["n"] == 10


def test_thin_regime_shown_with_thin_flag(tmp_path):
    """P0-3:n=5(≥MIN_N_INJECT=3,<_HI2_MIN_N=10)现在会出现在 `by_regime`,标 thin=True——
    不再二值断供,只是 ⚠ 标薄样本(design 2026-07-12-selflearning-optimization-brainstorm §4)。"""
    _day(tmp_path, "2026-07-21", [0.02] * 5, regime="trend")
    out = buy_ledger.hi2_calibration(scan_root=tmp_path, window=30)
    assert "trend" in out["by_regime"]
    assert out["by_regime"]["trend"]["thin"] is True
    assert out["by_regime"]["trend"]["n"] == 5


def test_below_floor_regime_dropped(tmp_path):
    """n<MIN_N_INJECT(=3)→ 绝对禁注,不出现在 `by_regime`(不受 shrink 开关影响)。"""
    _day(tmp_path, "2026-07-21", [0.02] * 2, regime="range")
    out = buy_ledger.hi2_calibration(scan_root=tmp_path, window=30)
    assert out["all"]["n"] == 2, "先确认这天真的被读进 all(不是又读错列意外凑出同一个断言)"
    assert "range" not in out["by_regime"]


def test_regime_touch8_rate_is_shrunk_toward_all(tmp_path):
    """regime touch8_rate 向 all 组收缩:risk_off 全触达(1.0)+ range 全不触达(0.0)混合出
    all touch8_rate=0.5,各 regime 收缩后应落在 raw 与 0.5 之间(真实拉力,非二值)。"""
    _day(tmp_path, "2026-07-20", [0.10] * 5, regime="risk_off")   # 全部 ≥8% → raw touch8=1.0
    _day(tmp_path, "2026-07-21", [0.01] * 5, regime="range")      # 全部 <8% → raw touch8=0.0
    out = buy_ledger.hi2_calibration(scan_root=tmp_path, window=30, shrink=True, k=15)
    assert out["all"]["touch8_rate"] == 0.5
    ro = out["by_regime"]["risk_off"]["touch8_rate"]
    rg = out["by_regime"]["range"]["touch8_rate"]
    assert 0.5 < ro < 1.0
    assert 0.0 < rg < 0.5
    expected_ro = round((5 * 1.0 + 15 * 0.5) / (5 + 15), 4)
    assert abs(ro - expected_ro) < 1e-6


def test_regime_shrink_false_returns_raw(tmp_path):
    _day(tmp_path, "2026-07-20", [0.10] * 5, regime="risk_off")
    _day(tmp_path, "2026-07-21", [0.01] * 5, regime="range")
    out = buy_ledger.hi2_calibration(scan_root=tmp_path, window=30, shrink=False)
    assert out["by_regime"]["risk_off"]["touch8_rate"] == 1.0
    assert out["by_regime"]["range"]["touch8_rate"] == 0.0


# ───────────────────────── C3 修复(final-review 2026-08-08):日期分界口径 ─────────────────────────
# hi2_calibration 此前恒读字面量 "hi_2_oc",与同一份 L4 prompt 里日级 calibration_line 的
# v4 文案直接矛盾。这里锁:v4 起(>= ruler.SCHEMA_SWITCH_V4)读 ruler.TOUCH_COL,双列过渡
# 参考读数(all_ref)只由 v4 贡献的日子填,混窗口两代真实并存不强行统一。


def _day_v4(tmp, date, touch, hi2=None, regime="risk_off"):
    """v4 起(date >= ruler.SCHEMA_SWITCH_V4)的一天:写 `ruler.TOUCH_COL`(必选)+ 可选
    `hi_2_oc`(双列过渡参考读数)。"""
    from autoresearch.common import ruler
    d = tmp / date
    (d / "retro").mkdir(parents=True)
    row = {"code": [f"{i:06d}" for i in range(len(touch))], ruler.TOUCH_COL: touch}
    if hi2 is not None:
        row["hi_2_oc"] = hi2
    pd.DataFrame(row).to_csv(d / "retro" / "attribution.csv", index=False)
    (d / "meta.json").write_text(json.dumps({"regime": regime}), encoding="utf-8")


def test_hi2_calibration_v4_day_reads_touch_col_not_hi2(tmp_path):
    """v4 起主口径读 `ruler.TOUCH_COL`,不是 `hi_2_oc`——两列故意给不同值,读错列 p60
    会落在完全不同的区间;`all_ref` 取自同一天的 `hi_2_oc`(双列过渡参考读数)。"""
    from autoresearch.common import ruler
    _day_v4(tmp_path, ruler.SCHEMA_SWITCH_V4, touch=[0.01] * 6 + [0.05] * 4, hi2=[0.20] * 10)
    out = buy_ledger.hi2_calibration(scan_root=tmp_path, window=30)
    assert out["all"]["n"] == 10
    assert 0.01 <= out["all"]["hi2_p60"] <= 0.05     # 来自 touch;若误读 hi2 会落在 0.20 附近
    assert out["all_ref"]["n"] == 10
    assert out["all_ref"]["hi2_p60"] == 0.20


def test_hi2_calibration_mixed_window_keeps_both_vintages_honest(tmp_path):
    """window 横跨分界日:v3 日按 hi_2_oc 计入 all,v4 日按 touch 计入 all——两代真实并存,
    不强行统一成一种口径;只有 v4 贡献的日子进 `all_ref`(v3 日没有第二套读数可参考)。"""
    from autoresearch.common import ruler
    _day(tmp_path, "2026-07-20", [0.02] * 5)                                    # v3 日
    _day_v4(tmp_path, ruler.SCHEMA_SWITCH_V4, touch=[0.04] * 5, hi2=[0.09] * 5)  # v4 日
    out = buy_ledger.hi2_calibration(scan_root=tmp_path, window=30)
    assert out["all"]["n"] == 10                        # 两天都计入 all(诚实混窗)
    assert out["all_ref"]["n"] == 5                      # 只有 v4 那天贡献参考读数


def test_hi2_calibration_pre_v4_window_has_no_ref(tmp_path):
    """纯 v3 窗口:压根没有 `all_ref` 键(不是空字典)——没有第二套读数就不该假装有。"""
    _day(tmp_path, "2026-07-20", [0.02] * 5)
    out = buy_ledger.hi2_calibration(scan_root=tmp_path, window=30)
    assert out["all"]["n"] == 5
    assert "all_ref" not in out


# ───────────────────────── target_calib_line:L4 逐卡 📐 行(此前无测试覆盖) ─────────────────────────


def test_target_calib_line_all_only():
    """C3 修复(final-review 2026-08-08):文案改为 v4 口径(隔夜窗/T+2 开盘),与日级
    calibration_line 措辞对齐,不再是硬编码的「2 日 MFE」(此前与 v4 文案互相矛盾)。"""
    calib = {"all": {"n": 20, "hi2_p60": 0.05, "touch8_rate": 0.3}, "by_regime": {}}
    line = buy_ledger.target_calib_line(calib, regime=None)
    assert "全体隔夜窗" in line and "T+2 开盘" in line and "v4" in line
    assert "2 日 MFE" not in line, "旧口径措辞不该再出现在主口径行(会跟日级 v4 文案打架)"
    assert "p60=+5.0%" in line
    assert "n=20" in line and "30%" in line
    assert "同 regime" not in line
    assert "参考" not in line, "没有 all_ref 就不该硬凑参考子句"


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


def test_target_calib_line_shows_ref_clause_when_all_ref_present():
    """C3 修复(final-review 2026-08-08):`all_ref` 非空(窗口内有 v4 贡献的日子)→ 附
    `hi_2_oc` 参考子句,镜像 `calibration_line` 的双列过渡手法。"""
    calib = {"all": {"n": 20, "hi2_p60": 0.05, "touch8_rate": 0.3},
             "all_ref": {"n": 8, "hi2_p60": 0.09, "touch8_rate": 0.5},
             "by_regime": {}}
    line = buy_ledger.target_calib_line(calib, regime=None)
    assert "参考(hi_2_oc·2日盘中触达)" in line
    assert "+9.0%" in line and "n=8" in line
