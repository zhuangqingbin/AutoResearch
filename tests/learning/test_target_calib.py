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


def test_thin_regime_dropped(tmp_path):
    _day(tmp_path, "2026-07-09", [0.02] * 5, regime="trend")   # n=5 <10 → 不出分组
    out = buy_ledger.hi2_calibration(scan_root=tmp_path, window=30)
    assert "trend" not in out["by_regime"]
