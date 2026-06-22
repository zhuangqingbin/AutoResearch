"""LSTM + GRU sequence rankers: fit/predict finite, save/load roundtrip, learnable signal.

NO network. Synthetic seq panel built directly (the {feat}_t{w} flat columns) — no lake needed.
Tiny RNNs + few epochs + fixed seed → fast & deterministic. Same registry/build + Dataset +
unified Trainer + champion gate as every other ranker.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from autoresearch.data.features import SEQ_WINDOW, feature_columns
from autoresearch.models.base import Dataset
from autoresearch.models.registry import ModelConfig, build

_SEQ_COLS = feature_columns("seq")
_SIG_COL = f"r_t{SEQ_WINDOW - 1}"   # newest daily return — the learnable signal carrier
_FAST = {"hidden": 16, "epochs": 8, "patience": 3}


def _seq_panel(n_dates: int = 6, n_stocks: int = 120, signal: float = 0.6, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    sig_idx = _SEQ_COLS.index(_SIG_COL)
    frames = []
    for di in range(n_dates):
        date = f"202601{di + 1:02d}"
        x = rng.standard_normal((n_stocks, len(_SEQ_COLS))).astype("float32")
        y = signal * x[:, sig_idx] + (1.0 - signal) * rng.standard_normal(n_stocks)
        df = pd.DataFrame(x, columns=_SEQ_COLS)
        df.insert(0, "code", [f"{600000 + i:06d}" for i in range(n_stocks)])
        df.insert(0, "date", date)
        df["fwd_1_oo"] = y
        frames.append(df)
    return pd.concat(frames, ignore_index=True)


def _dataset(panel: pd.DataFrame) -> Dataset:
    y = panel.groupby("date")["fwd_1_oo"].transform(lambda s: s.rank(pct=True))
    return Dataset(X=panel.reset_index(drop=True), y=y.reset_index(drop=True),
                   dates=panel["date"].reset_index(drop=True))


@pytest.mark.parametrize("kind", ["lstm", "gru", "alstm", "tcn", "transformer",
                                  "localformer", "tft", "tra", "krnn", "sfm"])
def test_seq_ranker_fit_predict_finite(kind):
    panel = _seq_panel(n_dates=6, n_stocks=100, signal=0.6)
    model = build(ModelConfig(kind=kind, feature_set="seq", params=_FAST))
    report = model.fit(_dataset(panel))
    assert report.n_rows > 0
    one = panel[panel["date"] == panel["date"].iloc[0]].reset_index(drop=True)
    scores = model.predict(one)
    assert isinstance(scores, pd.Series)
    assert len(scores) == len(one)
    assert np.isfinite(scores.to_numpy()).all(), f"{kind} produced non-finite scores"


@pytest.mark.parametrize("kind", ["lstm", "gru", "alstm", "tcn", "transformer",
                                  "localformer", "tft", "tra", "krnn", "sfm"])
def test_seq_ranker_save_load_roundtrip(tmp_path, kind):
    panel = _seq_panel(n_dates=5, n_stocks=90, signal=0.6)
    feats = panel[panel["date"] == panel["date"].iloc[0]].reset_index(drop=True)
    model = build(ModelConfig(kind=kind, feature_set="seq", params=_FAST))
    model.fit(_dataset(panel))
    before = model.predict(feats)
    p = tmp_path / f"{kind}.pkl"
    model.save(p)
    after = type(model).load(p).predict(feats)
    pd.testing.assert_series_equal(before, after, check_names=False, rtol=1e-5, atol=1e-5)


def test_lstm_learns_signal():
    """Strong signal on the newest-return feature → LSTM oos rank-IC > 0 (deterministic seed)."""
    panel = _seq_panel(n_dates=8, n_stocks=160, signal=0.95)
    dts = sorted(panel["date"].unique())
    tr = panel[panel["date"].isin(dts[:-1])]
    te = panel[panel["date"] == dts[-1]].reset_index(drop=True)
    model = build(ModelConfig(kind="lstm", feature_set="seq",
                              params={"hidden": 24, "epochs": 80, "patience": 20}))
    model.fit(_dataset(tr))
    pred = model.predict(te)
    ic = pd.Series(pred.to_numpy()).rank().corr(te["fwd_1_oo"].rank())
    assert np.isfinite(ic) and ic > 0, f"LSTM failed to learn a strong signal: oos rank-IC={ic}"
