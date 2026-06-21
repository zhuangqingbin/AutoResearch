"""Model framework: register/build a dummy Model, run Trainer end-to-end, champion gate.

NO network. Uses a StubHandler (tiny synthetic 8-date panel) so the Trainer's materialize →
CS-rank label → time-series split → fit → oos rank-IC → promote path is exercised in isolation.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from autoresearch.models.base import Dataset, FitReport, Model
from autoresearch.models.registry import ModelConfig, build, register
from autoresearch.models.trainer import (
    TrainedModel,
    Trainer,
    _rank_ic_by_date,
    champion_ic,
    load_champion,
    save_champion,
)
from tests.models._synth import StubHandler, make_panel

# ───────────────────────── a dummy registered model ─────────────────────────


@register("dummy_passthrough")
class _DummyPassthrough(Model):
    """Predicts pct_60d directly (no real training) — gives a positive oos IC on the synth panel."""

    feature_set = "core"
    kind = "core"

    def __init__(self, col: str = "pct_60d"):
        self.col = col

    def fit(self, ds: Dataset) -> FitReport:
        n_dates = int(pd.Series(ds.dates).nunique())
        return FitReport(n_rows=len(ds.X), n_dates=n_dates, notes={"fit": "no-op"})

    def predict(self, feats: pd.DataFrame) -> pd.Series:
        return pd.to_numeric(feats[self.col], errors="coerce")

    def save(self, path):
        from pathlib import Path
        Path(path).write_text(self.col, encoding="utf-8")

    @classmethod
    def load(cls, path):
        from pathlib import Path
        return cls(col=Path(path).read_text(encoding="utf-8"))


def test_register_and_build_dummy():
    m = build(ModelConfig(kind="dummy_passthrough", feature_set="core"))
    assert isinstance(m, _DummyPassthrough)
    assert m.feature_set == "core"
    # duplicate registration is rejected
    with pytest.raises(KeyError):
        register("dummy_passthrough")(_DummyPassthrough)


def test_trainer_train_returns_oos_rank_ic():
    panel = make_panel(n_dates=8, n_stocks=120, signal=0.8)
    handler = StubHandler(panel)
    trainer = Trainer(handler, label="fwd_1_oo", valid_dates=2)
    trained = trainer.train(ModelConfig(kind="dummy_passthrough"), dates=sorted(panel["date"].unique()))
    assert isinstance(trained, TrainedModel)
    assert isinstance(trained.report, FitReport)
    # oos rank-IC is a finite float; the passthrough predicts the signal column → IC > 0
    assert np.isfinite(trained.oos_rank_ic)
    assert trained.oos_rank_ic > 0.0
    assert trained.meta["valid_dates"] >= 1
    assert trained.meta["n_dates"] == 8


def test_trainer_evaluate_matches_rank_ic_helper():
    """Trainer.evaluate must equal the standalone _rank_ic_by_date on the same predictions."""
    panel = make_panel(n_dates=6, n_stocks=100, signal=0.7)
    handler = StubHandler(panel)
    trainer = Trainer(handler)
    model = build(ModelConfig(kind="dummy_passthrough"))
    ic = trainer.evaluate(model, panel)
    expected = _rank_ic_by_date(model.predict(panel), panel["fwd_1_oo"], panel["date"])
    assert ic == pytest.approx(expected, abs=1e-12)


def test_champion_gate_promote_logic():
    """challenger ic 0.05 > champion 0.02 → True; 0.01 → False; no champion → True; NaN → False."""
    def tm(ic):
        return TrainedModel(model=build(ModelConfig(kind="dummy_passthrough")),
                            report=FitReport(n_rows=1, n_dates=1), oos_rank_ic=ic)

    assert Trainer.promote_if_better(tm(0.05), champion_ic=0.02) is True
    assert Trainer.promote_if_better(tm(0.01), champion_ic=0.02) is False
    assert Trainer.promote_if_better(tm(0.05), champion_ic=None) is True
    assert Trainer.promote_if_better(tm(float("nan")), champion_ic=0.02) is False


def test_champion_store_roundtrip(tmp_path):
    """save_champion writes <version>.pkl + champion.json; champion_ic/load_champion read back."""
    trained = TrainedModel(model=build(ModelConfig(kind="dummy_passthrough")),
                           report=FitReport(n_rows=10, n_dates=6), oos_rank_ic=0.033,
                           meta={"kind": "dummy_passthrough", "feature_set": "core"})
    save_champion("unit_test_model", trained, version="v1", root=tmp_path)
    assert (tmp_path / "unit_test_model" / "v1.pkl").exists()
    assert (tmp_path / "unit_test_model" / "champion.json").exists()
    assert champion_ic("unit_test_model", root=tmp_path) == pytest.approx(0.033)
    loaded = load_champion("unit_test_model", _DummyPassthrough, root=tmp_path)
    assert isinstance(loaded, _DummyPassthrough)
    # missing champion → None
    assert champion_ic("does_not_exist", root=tmp_path) is None
