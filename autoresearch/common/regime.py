#!/usr/bin/env python3
"""确定性市场 regime 分类器 —— 从已有横截面帧算 trend/range/risk_off。零网络、零 LLM。

design: docs/specs/2026-06-27-scan-l0-l5-optimizations-design.md §F。

动机:`composite` 权重是单 horizon fwd_2_oc 超短主尺 IC 校准,momentum/tech 等 IC 几乎全负(均值回归)。趋势
regime 下这些符号翻正,静态校准看不到 → regime 错配("连续两天 0 买")。本模块给 L1 权重选择 /
L5 标注 / 闭环 drift 一个**共用的、可在历史帧与今日帧上一致复算**的 regime 信号——直接用截面里
已有的 `above_ma60`(站上 MA60 占比 = breadth)+ median `pct_60d`(截面动量),不额外取数。

regime 同口径:`factor_lab.calibrate_regimes` 逐日 `classify_regime(factor_frame)` 分桶,`scan` 线上
`classify_regime(recall_frame)` 选权重——两处同函数,保证校准期与推理期 regime 定义一致。
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


def _num(s) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


@dataclass(frozen=True)
class RegimeState:
    """market regime 快照:标签 + 可解释指标(落 meta 备查)。"""

    label: str          # "trend" | "range" | "risk_off"
    breadth: float      # 站上 MA60 占比 0..1(缺 above_ma60 → pct_60d>0 占比代理)
    med_mom: float      # 截面 median pct_60d
    n: int

    def to_dict(self) -> dict:
        return {"label": self.label, "breadth": round(self.breadth, 4),
                "med_mom": round(self.med_mom, 4), "n": self.n}


def classify_regime(frame: pd.DataFrame, *, breadth_hi: float = 0.55, breadth_lo: float = 0.30,
                    ma_col: str = "above_ma60", mom_col: str = "pct_60d") -> RegimeState:
    """横截面帧 → RegimeState(确定性)。

    规则(policy,阈值可调):breadth≥hi 且 med_mom>0 → trend;breadth≤lo 且 med_mom<0 → risk_off;
    否则 range。空帧/缺列 → 安全退化 range(中性,不误导下游)。breadth 优先用站上 MA60 占比,
    缺 `above_ma60` 时用 `pct_60d>0` 占比代理。
    """
    n = int(len(frame))
    if n == 0:
        return RegimeState("range", 0.0, 0.0, 0)

    mom = _num(frame[mom_col]).dropna() if mom_col in frame.columns else pd.Series([], dtype=float)
    med_mom = float(mom.median()) if len(mom) else 0.0

    breadth: float | None = None
    if ma_col in frame.columns:
        a = _num(frame[ma_col]).dropna()
        if len(a):
            breadth = float((a > 0).mean())
    if breadth is None:                                 # 代理:动量为正的占比
        breadth = float((mom > 0).mean()) if len(mom) else 0.0

    if breadth >= breadth_hi and med_mom > 0:
        label = "trend"
    elif breadth <= breadth_lo and med_mom < 0:
        label = "risk_off"
    else:
        label = "range"
    return RegimeState(label, breadth, med_mom, n)


def detect_drift(current: RegimeState, weights_meta: dict | None) -> tuple[bool, str]:
    """今日 regime 与 weights.json 校准期主导 regime(`meta.regime_calib`)比对 → (drifted, reason)。

    无 regime_calib 基线(flat 校准、未跑 calibrate_regimes)→ (False, 提示)。drift=True 时上游
    (self_review warn + assemble banner)提示"建议重校准"。
    """
    base = (weights_meta or {}).get("regime_calib")
    if not base:
        return False, "weights 无 regime_calib 基线(calibrate_regimes 校准后才检测漂移)"
    if current.label != base:
        return True, f"当日 regime={current.label} ≠ 校准主导 {base},建议 calibrate_regimes 重校准"
    return False, f"regime={current.label} 与校准一致"
