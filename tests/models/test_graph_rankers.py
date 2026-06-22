"""GATs / HIST / IGMTF graph rankers: fit/predict finite, save/load roundtrip, learnable.

NO network. Synthetic graph panel (self + industry-context flat columns) built directly.
Graph relation is precomputed into features → row-independent → same Trainer + champion gate.
Tiny nets + few epochs + fixed seed → fast & deterministic.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from autoresearch.data.features import feature_columns
from autoresearch.models.base import Dataset
from autoresearch.models.registry import ModelConfig, build

_GRAPH_COLS = feature_columns("graph")
_SIG = "composite"   # a self feature carrying the learnable signal
_FAST = {"hidden": 16, "epochs": 8, "patience": 3}


def _graph_panel(n_dates: int = 6, n_stocks: int = 120, signal: float = 0.6, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    sig_idx = _GRAPH_COLS.index(_SIG)
    frames = []
    for di in range(n_dates):
        x = rng.standard_normal((n_stocks, len(_GRAPH_COLS))).astype("float32")
        y = signal * x[:, sig_idx] + (1.0 - signal) * rng.standard_normal(n_stocks)
        df = pd.DataFrame(x, columns=_GRAPH_COLS)
        df.insert(0, "code", [f"{600000 + i:06d}" for i in range(n_stocks)])
        df.insert(0, "date", f"202601{di + 1:02d}")
        df["fwd_1_oo"] = y
        frames.append(df)
    return pd.concat(frames, ignore_index=True)


def _dataset(panel: pd.DataFrame) -> Dataset:
    y = panel.groupby("date")["fwd_1_oo"].transform(lambda s: s.rank(pct=True))
    return Dataset(X=panel.reset_index(drop=True), y=y.reset_index(drop=True),
                   dates=panel["date"].reset_index(drop=True))


@pytest.mark.parametrize("kind", ["gats", "hist", "igmtf"])
def test_graph_ranker_fit_predict_finite(kind):
    panel = _graph_panel(n_dates=6, n_stocks=100, signal=0.6)
    model = build(ModelConfig(kind=kind, feature_set="graph", params=_FAST))
    report = model.fit(_dataset(panel))
    assert report.n_rows > 0
    one = panel[panel["date"] == panel["date"].iloc[0]].reset_index(drop=True)
    scores = model.predict(one)
    assert isinstance(scores, pd.Series)
    assert len(scores) == len(one)
    assert np.isfinite(scores.to_numpy()).all(), f"{kind} produced non-finite scores"


@pytest.mark.parametrize("kind", ["gats", "hist", "igmtf"])
def test_graph_ranker_save_load_roundtrip(tmp_path, kind):
    panel = _graph_panel(n_dates=5, n_stocks=90, signal=0.6)
    feats = panel[panel["date"] == panel["date"].iloc[0]].reset_index(drop=True)
    model = build(ModelConfig(kind=kind, feature_set="graph", params=_FAST))
    model.fit(_dataset(panel))
    before = model.predict(feats)
    p = tmp_path / f"{kind}.pkl"
    model.save(p)
    after = type(model).load(p).predict(feats)
    pd.testing.assert_series_equal(before, after, check_names=False, rtol=1e-5, atol=1e-5)


def test_gats_learns_signal():
    """Strong signal on a self feature → GATs oos rank-IC > 0 (deterministic seed)."""
    panel = _graph_panel(n_dates=8, n_stocks=160, signal=0.95)
    dts = sorted(panel["date"].unique())
    tr = panel[panel["date"].isin(dts[:-1])]
    te = panel[panel["date"] == dts[-1]].reset_index(drop=True)
    model = build(ModelConfig(kind="gats", feature_set="graph",
                              params={"hidden": 24, "epochs": 80, "patience": 20}))
    model.fit(_dataset(tr))
    pred = model.predict(te)
    ic = pd.Series(pred.to_numpy()).rank().corr(te["fwd_1_oo"].rank())
    assert np.isfinite(ic) and ic > 0, f"GATs failed to learn a strong signal: oos rank-IC={ic}"
