"""Trainer 传 kind=feature_set(修 seq/graph)+ load_champion_any 按 kind 自解析(NO network)。"""
from __future__ import annotations

from autoresearch.models.base import FitReport
from autoresearch.models.linear import LinearComposite
from autoresearch.models.registry import ModelConfig
from autoresearch.models.trainer import (
    TrainedModel,
    Trainer,
    load_champion_any,
    save_champion,
)
from tests.models._synth import make_panel


class _RecordHandler:
    """记录 materialize 收到的 kind,验证 Trainer 把 kind 传成 feature_set。"""

    def __init__(self, panel):
        self._p = panel
        self.kinds = []

    def materialize(self, dates, feature_set="core", kind="core", cap_floor=30.0,
                    *, price_dates=None, fwd=10):
        self.kinds.append(kind)
        return self._p.copy()


def test_trainer_forwards_kind_equal_feature_set():
    h = _RecordHandler(make_panel())
    Trainer(h, label="fwd_1_oo").train(ModelConfig(kind="linear", feature_set="graph"), ["20260101"])
    assert h.kinds[-1] == "graph", "Trainer 必须把 kind 传成 feature_set(否则 seq/graph 取错视图)"


def test_load_champion_any_resolves_kind(tmp_path):
    store = tmp_path / "store"
    trained = TrainedModel(model=LinearComposite(),
                           report=FitReport(n_rows=1, n_dates=1, notes={}),
                           oos_rank_ic=0.05, meta={"kind": "linear", "feature_set": "core"})
    save_champion("l2_fwd5", trained, "v1", root=store)
    loaded = load_champion_any("l2_fwd5", root=store)
    assert loaded is not None and hasattr(loaded, "predict")


def test_load_champion_any_missing_returns_none(tmp_path):
    assert load_champion_any("l2_fwd5", root=tmp_path / "empty") is None
