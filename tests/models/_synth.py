"""Tiny synthetic core panels for model-framework tests (NO network, NO lake).

`make_panel(...)` returns a DataFrame shaped like `DataHandler.materialize(feature_set="core")`
output: date/code/<core features>/fwd_1_oo/buyable (+ industry for composite weighting). One
group input column (`pct_60d`) carries real signal into `fwd_1_oo` so a learner can show a
positive oos rank-IC on a small panel; the rest are noise. Kept deliberately small + cheap.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from autoresearch.data.features import feature_columns

# Columns the scoring primitives (_factor_groups / composite_score) actually read.
_GROUP_INPUTS = [
    "pct_60d", "pct_ytd", "main_net_ratio", "main_inflow_yi", "retail_net_yi",
    "chip_concentration", "price_to_cost", "hk_ratio", "rsi6", "rsi12",
    "cmf_20", "obv_mom_20", "pe", "vol_ratio", "winner_rate",
]


def make_panel(n_dates: int = 8, n_stocks: int = 120, signal: float = 0.6,
               seed: int = 7) -> pd.DataFrame:
    """A multi-date core panel. `pct_60d` predicts `fwd_1_oo` (strength=`signal`); rest noise.

    Every column in FEATURE_SETS["core"] is present (NaN-filled if not explicitly set) so the
    panel is a faithful stand-in for materialize() output. `industry` + `buyable` included.
    """
    rng = np.random.default_rng(seed)
    core = feature_columns("core")
    industries = ["半导体", "白酒", "煤炭", "医药"]
    frames = []
    for di in range(n_dates):
        date = f"2026010{di + 1}" if di < 9 else f"202601{di + 1}"
        codes = [f"{600000 + i:06d}" for i in range(n_stocks)]
        df = pd.DataFrame({"date": date, "code": codes})
        df["industry"] = rng.choice(industries, n_stocks)
        # group inputs: random, but pct_60d carries signal
        for c in _GROUP_INPUTS:
            df[c] = rng.normal(size=n_stocks)
        df["pe"] = rng.uniform(5, 120, n_stocks)
        df["vol_ratio"] = rng.uniform(0.4, 4.0, n_stocks)
        df["winner_rate"] = rng.uniform(0, 100, n_stocks)
        df["ma_bull"] = rng.integers(0, 2, n_stocks).astype(float)
        df["above_ma60"] = rng.integers(0, 2, n_stocks).astype(float)
        # label: signal*pct_60d + noise (per-date), so cross-sectional rank-IC > 0 is learnable
        df["fwd_1_oo"] = signal * df["pct_60d"] + rng.normal(scale=1.0, size=n_stocks)
        df["buyable"] = True
        # fill any remaining core columns with noise so the panel has the full core schema
        for c in core:
            if c not in df.columns:
                df[c] = rng.normal(size=n_stocks)
        frames.append(df)
    panel = pd.concat(frames, ignore_index=True)
    ordered = ["date", "code", "industry", *core, "fwd_1_oo", "buyable"]
    seen, cols = set(), []
    for c in ordered:
        if c not in seen and c in panel.columns:
            seen.add(c)
            cols.append(c)
    return panel[cols]


class StubHandler:
    """Minimal DataHandler stand-in: `materialize` returns a fixed pre-built panel.

    Decouples the Trainer test from the lake/parquet layer — the Trainer only ever calls
    `handler.materialize(dates, feature_set=..., ...)`, so a stub is sufficient and fast.
    """

    def __init__(self, panel: pd.DataFrame):
        self._panel = panel

    def materialize(self, dates, feature_set="core", kind="core", cap_floor=30.0,
                    *, price_dates=None, fwd=10) -> pd.DataFrame:
        return self._panel.copy()
