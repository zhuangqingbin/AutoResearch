"""MLP + TabNet rankers: fit/predict finite, save/load roundtrip, learnable signal.

NO network. Tiny nets + few epochs + CPU + fixed seed (deterministic) → fast & non-flaky.
They go through the same registry/build + Dataset contract as the tree rankers, so the
unified Trainer + champion gate apply unchanged (Trainer is model-agnostic; covered in
test_framework). Here we exercise the torch-specific surface.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from autoresearch.models.base import Dataset
from autoresearch.models.registry import ModelConfig, build
from tests.models._synth import make_panel

_FAST = {"epochs": 8, "patience": 3}   # tiny train for the structural tests


def _dataset(panel: pd.DataFrame) -> Dataset:
    y = panel.groupby("date")["fwd_1_oo"].transform(lambda s: s.rank(pct=True))
    return Dataset(X=panel.reset_index(drop=True), y=y.reset_index(drop=True),
                   dates=panel["date"].reset_index(drop=True))


@pytest.mark.parametrize("kind", ["mlp", "tabnet"])
def test_torch_ranker_fit_predict_finite(kind):
    panel = make_panel(n_dates=6, n_stocks=120, signal=0.6)
    model = build(ModelConfig(kind=kind, params=_FAST))
    report = model.fit(_dataset(panel))
    assert report.n_rows > 0
    one = panel[panel["date"] == panel["date"].iloc[0]].reset_index(drop=True)
    scores = model.predict(one)
    assert isinstance(scores, pd.Series)
    assert len(scores) == len(one)
    assert np.isfinite(scores.to_numpy()).all(), f"{kind} produced non-finite scores"


@pytest.mark.parametrize("kind", ["mlp", "tabnet"])
def test_torch_ranker_save_load_roundtrip(tmp_path, kind):
    panel = make_panel(n_dates=5, n_stocks=100, signal=0.6)
    feats = panel[panel["date"] == panel["date"].iloc[0]].reset_index(drop=True)
    model = build(ModelConfig(kind=kind, params=_FAST))
    model.fit(_dataset(panel))
    before = model.predict(feats)
    p = tmp_path / f"{kind}.pkl"
    model.save(p)
    after = type(model).load(p).predict(feats)
    pd.testing.assert_series_equal(before, after, check_names=False, rtol=1e-5, atol=1e-5)


def test_mlp_learns_signal():
    """Strong monotone signal → MLP oos rank-IC > 0 on a held-out date (deterministic seed)."""
    panel = make_panel(n_dates=8, n_stocks=160, signal=0.95)
    dts = sorted(panel["date"].unique())
    tr = panel[panel["date"].isin(dts[:-1])]
    te = panel[panel["date"] == dts[-1]].reset_index(drop=True)
    model = build(ModelConfig(kind="mlp", params={"epochs": 80, "patience": 20}))
    model.fit(_dataset(tr))
    pred = model.predict(te)
    ic = pd.Series(pred.to_numpy()).rank().corr(te["fwd_1_oo"].rank())
    assert np.isfinite(ic) and ic > 0, f"MLP failed to learn a strong signal: oos rank-IC={ic}"
