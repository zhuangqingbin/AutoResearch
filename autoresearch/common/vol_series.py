#!/usr/bin/env python3
"""多日量价序列指标(OBV / CMF / VWAP偏离 / 量价突破)· 纯函数,从日线面板(code×date pivot)算横截面值。

补 `uzi_lenses.volume_price_signals` 的**快照**版:这些是**多日序列**指标(需量能 + 高低收序列),由
`factor_lab` 从已缓存的 daily 面板(open/high/low/close/amount)计算 → 作**候选因子走 IC 验证**——
序列指标比单日 vol_ratio 更能分辨资金流方向(顶部放量=派发 / 底部=吸筹)。`amount`(成交额,money-volume)
作量能口径(比股数更稳、跨价位可比)。窗口末日 = 分析日 D,严格用 ≤D 的列(无未来泄漏)。

设计:Wyckoff/VSA「effort vs result」+ Chaikin CMF + Granville OBV + 机构 VWAP 折溢价。

用法:uv run --no-sync python -m autoresearch.common.vol_series --selftest
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd


def _safe_div(num: pd.Series, den: pd.Series) -> pd.Series:
    return num / den.replace(0, np.nan)


def _mfm(high: pd.DataFrame, low: pd.DataFrame, close: pd.DataFrame) -> pd.DataFrame:
    """Money Flow Multiplier((C−L)−(H−C))/(H−L) ∈ [−1,1];H==L(无区间)→ 0。"""
    rng = (high - low)
    out = ((close - low) - (high - close)) / rng.where(rng != 0)
    return out.fillna(0.0)


def cmf(high: pd.DataFrame, low: pd.DataFrame, close: pd.DataFrame, amount: pd.DataFrame,
        dates: list[str]) -> pd.Series:
    """Chaikin Money Flow:Σ(MFM×amount) / Σamount over window → 每只一个值(>0 买压/吸筹、<0 卖压/派发)。"""
    H, L, C, A = (df[dates] for df in (high, low, close, amount))
    return _safe_div((_mfm(H, L, C) * A).sum(axis=1), A.sum(axis=1))


def obv_momentum(close: pd.DataFrame, amount: pd.DataFrame, dates: list[str]) -> pd.Series:
    """OBV 归一动量:Σ(sign(ΔC)×amount) / Σamount → [−1,1](收盘方向 × 量,>0=资金净进/暗吸)。"""
    C, A = close[dates], amount[dates]
    sign = np.sign(C.diff(axis=1)).fillna(0.0)
    return _safe_div((sign * A).sum(axis=1), A.sum(axis=1))


def price_vs_vwap(high: pd.DataFrame, low: pd.DataFrame, close: pd.DataFrame, amount: pd.DataFrame,
                  dates: list[str]) -> pd.Series:
    """末日收盘 / 窗口 VWAP(typical×amount 加权)− 1 → 偏离率(>0 溢价、<0 折价/机构成本下方)。"""
    H, L, C, A = (df[dates] for df in (high, low, close, amount))
    typical = (H + L + C) / 3.0
    vwap = _safe_div((typical * A).sum(axis=1), A.sum(axis=1))
    return _safe_div(close[dates[-1]], vwap) - 1.0


def breakout_on_volume(close: pd.DataFrame, amount: pd.DataFrame, dates: list[str],
                       vol_mult: float = 1.5) -> pd.Series:
    """末日收盘=窗口最高收盘 ∧ 末日成交额≥vol_mult×窗口均额 → 1.0 else 0.0(量价确认突破,无量突破=假)。"""
    C, A = close[dates], amount[dates]
    last = dates[-1]
    is_high = C[last] >= C.max(axis=1)
    amt_ok = A[last] >= vol_mult * A.mean(axis=1)
    return (is_high & amt_ok).astype(float)


# ───────────────────────── 离线自测(纯函数,无 IO) ─────────────────────────


def _selftest() -> int:
    fails: list[str] = []
    dates = ["d1", "d2", "d3", "d4", "d5"]
    idx = ["UP", "FLAT", "DN"]
    # UP 持续涨、DN 持续跌、FLAT 不动;UP/DN 末日放量(amount 5)
    close = pd.DataFrame([[10, 11, 12, 13, 14], [10, 10, 10, 10, 10], [14, 13, 12, 11, 10]],
                         index=idx, columns=dates, dtype=float)
    high = close + 0.5
    low = close - 0.5
    amount = pd.DataFrame([[1, 1, 1, 1, 5], [1, 1, 1, 1, 1], [1, 1, 1, 1, 5]],
                          index=idx, columns=dates, dtype=float)

    # OBV 动量:UP>0、DN<0、FLAT≈0
    obv = obv_momentum(close, amount, dates)
    if not (obv["UP"] > 0.5 and obv["DN"] < -0.5 and abs(obv["FLAT"]) < 1e-9):
        fails.append(f"obv_momentum 方向错: {obv.to_dict()}")

    # 突破放量:UP 末日新高+放量=1;FLAT(末日=最高但无量)=0;DN(末日非新高)=0
    bo = breakout_on_volume(close, amount, dates, vol_mult=1.5)
    if not (bo["UP"] == 1.0 and bo["FLAT"] == 0.0 and bo["DN"] == 0.0):
        fails.append(f"breakout_on_volume 错: {bo.to_dict()}")

    # CMF:收盘贴上沿 → +;贴下沿 → −;居中 → 0
    cidx = ["TOP", "BOT", "MID"]
    cc = pd.DataFrame([[10, 10], [10, 10], [10, 10]], index=cidx, columns=["d1", "d2"], dtype=float)
    ch = pd.DataFrame([[10, 10], [11, 11], [10.5, 10.5]], index=cidx, columns=["d1", "d2"], dtype=float)
    cl = pd.DataFrame([[9, 9], [10, 10], [9.5, 9.5]], index=cidx, columns=["d1", "d2"], dtype=float)
    ca = pd.DataFrame([[1, 1], [1, 1], [1, 1]], index=cidx, columns=["d1", "d2"], dtype=float)
    cm = cmf(ch, cl, cc, ca, ["d1", "d2"])
    if not (cm["TOP"] > 0.9 and cm["BOT"] < -0.9 and abs(cm["MID"]) < 1e-9):
        fails.append(f"cmf 方向错(贴上沿+/贴下沿−/居中0): {cm.to_dict()}")

    # VWAP 偏离:UP 末日收盘 > 窗口均价 → +;DN → −
    pv = price_vs_vwap(high, low, close, amount, dates)
    if not (pv["UP"] > 0 and pv["DN"] < 0):
        fails.append(f"price_vs_vwap 方向错: {pv.to_dict()}")

    # H==L(无区间)→ MFM 0,不崩
    flat_hl = pd.DataFrame([[10, 10]], index=["X"], columns=["d1", "d2"], dtype=float)
    fa = pd.DataFrame([[1, 1]], index=["X"], columns=["d1", "d2"], dtype=float)
    if cmf(flat_hl, flat_hl, flat_hl, fa, ["d1", "d2"])["X"] != 0.0:
        fails.append("cmf H==L 应为 0(无区间)")
    # 全零量 → 安全 NaN 不崩
    za = pd.DataFrame([[0, 0]], index=["X"], columns=["d1", "d2"], dtype=float)
    if not np.isnan(obv_momentum(flat_hl, za, ["d1", "d2"])["X"]):
        fails.append("零量分母应安全 NaN")

    if fails:
        print("SELFTEST ❌")
        for f in fails:
            print("  -", f)
        return 1
    print("SELFTEST ✅  cmf(贴沿±/居中0/无区间)+ obv_momentum(涨跌方向)+ price_vs_vwap(溢/折价)"
          "+ breakout_on_volume(新高放量)+ 零量/无区间容错 全过")
    return 0


if __name__ == "__main__":
    raise SystemExit(_selftest() if "--selftest" in sys.argv else 0)
