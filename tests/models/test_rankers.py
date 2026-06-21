"""The 5 ported rankers: fit+predict finite scores of right length; LinearComposite parity.

NO network. Tiny panels + tiny n_estimators for speed. The LinearComposite parity test is the
load-bearing one: its predict must equal composite_score(feats, weights)["composite"] exactly.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from autoresearch.common.scoring import _load_weights, composite_score
from autoresearch.models.base import Dataset
from autoresearch.models.registry import ModelConfig, build
from tests.models._synth import make_panel

# small/fast overrides so the tree models train in well under a second
_FAST = {
    "lgbm": {"num_boost_round": 40, "params": {"min_data_in_leaf": 20}},
    "xgb": {"num_boost_round": 40},
    "catboost": {"params": {"iterations": 40}},
    "double_ensemble": {"num_models": 3, "num_boost_round": 30,
                        "sub_params": {"min_data_in_leaf": 20}},
}


def _dataset(panel: pd.DataFrame) -> Dataset:
    y = panel.groupby("date")["fwd_1_oo"].transform(lambda s: s.rank(pct=True))
    return Dataset(X=panel.reset_index(drop=True), y=y.reset_index(drop=True),
                   dates=panel["date"].reset_index(drop=True))


@pytest.mark.parametrize("kind", ["linear", "lgbm", "xgb", "catboost", "double_ensemble"])
def test_ranker_fit_predict_finite_right_length(kind):
    panel = make_panel(n_dates=6, n_stocks=120, signal=0.6)
    model = build(ModelConfig(kind=kind, params=_FAST.get(kind, {})))
    report = model.fit(_dataset(panel))
    assert report.n_rows > 0
    # predict one date's cross-section
    one = panel[panel["date"] == panel["date"].iloc[0]].reset_index(drop=True)
    scores = model.predict(one)
    assert isinstance(scores, pd.Series)
    assert len(scores) == len(one)
    assert np.isfinite(pd.to_numeric(scores, errors="coerce")).all(), f"{kind} produced non-finite scores"


def test_linear_composite_parity():
    """LinearComposite.predict must EXACTLY equal composite_score(feats, weights)["composite"]."""
    panel = make_panel(n_dates=2, n_stocks=150, signal=0.5)
    feats = panel[panel["date"] == panel["date"].iloc[0]].reset_index(drop=True)

    model = build(ModelConfig(kind="linear"))
    got = model.predict(feats)

    weights = _load_weights()
    expected = composite_score(feats, weights)["composite"]

    assert list(got.index) == list(expected.index)
    # exact equality (same function, same weights) — allow only float noise
    pd.testing.assert_series_equal(got, expected, check_names=False)


def test_linear_composite_fit_is_noop_and_predict_unchanged():
    """fit() is a no-op for LinearComposite: predictions identical before and after fit."""
    panel = make_panel(n_dates=2, n_stocks=100)
    feats = panel[panel["date"] == panel["date"].iloc[0]].reset_index(drop=True)
    model = build(ModelConfig(kind="linear"))
    before = model.predict(feats)
    model.fit(_dataset(panel))
    after = model.predict(feats)
    pd.testing.assert_series_equal(before, after, check_names=False)


def test_gbdt_ranker_trains_and_predicts_finite():
    """GBDTRanker: trains on a multi-date panel + predicts finite scores; best_iter recorded."""
    panel = make_panel(n_dates=8, n_stocks=150, signal=0.8)
    model = build(ModelConfig(kind="lgbm", params={"num_boost_round": 60,
                                                   "params": {"min_data_in_leaf": 20}}))
    report = model.fit(_dataset(panel))
    assert report.best_iter is not None and report.best_iter >= 1
    assert report.notes["n_features"] == len(model.features)
    scores = model.predict(panel)
    assert len(scores) == len(panel)
    assert np.isfinite(scores.to_numpy()).all()


def test_ranker_save_load_roundtrip(tmp_path):
    """Each ranker round-trips through save/load and predicts the same scores."""
    panel = make_panel(n_dates=6, n_stocks=100, signal=0.6)
    feats = panel[panel["date"] == panel["date"].iloc[0]].reset_index(drop=True)
    for kind in ["linear", "lgbm", "xgb", "catboost", "double_ensemble"]:
        model = build(ModelConfig(kind=kind, params=_FAST.get(kind, {})))
        model.fit(_dataset(panel))
        before = model.predict(feats)
        p = tmp_path / f"{kind}.pkl"
        model.save(p)
        reloaded = type(model).load(p)
        after = reloaded.predict(feats)
        pd.testing.assert_series_equal(before, after, check_names=False,
                                       rtol=1e-9, atol=1e-9)
