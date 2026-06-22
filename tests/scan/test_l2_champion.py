"""L2 接线:默认 l2_fwd5、load_champion_any 动态 STORE_ROOT、champion_scores 派生+回落(NO network)。"""
from __future__ import annotations

from autoresearch.models.base import FitReport
from autoresearch.models.linear import LinearComposite
from autoresearch.models.trainer import TrainedModel, save_champion
from autoresearch.scan.config import ScanConfig
from tests.models._synth import make_panel


def _linear_trained():
    return TrainedModel(model=LinearComposite(),
                        report=FitReport(n_rows=1, n_dates=1, notes={}),
                        oos_rank_ic=0.05, meta={"kind": "linear", "feature_set": "core"})


def test_default_l2_model_is_swing():
    assert ScanConfig().l2_model == "l2_fwd5"


def test_load_champion_any_uses_dynamic_store_root(tmp_path, monkeypatch):
    import autoresearch.models.trainer as tr
    from autoresearch.models.trainer import load_champion_any
    monkeypatch.setattr(tr, "STORE_ROOT", tmp_path / "store")
    assert load_champion_any("l2_fwd5") is None              # 动态读 STORE_ROOT,空 → None
    save_champion("l2_fwd5", _linear_trained(), "v1", root=tmp_path / "store")
    assert load_champion_any("l2_fwd5") is not None          # 落 champion 后能加载


def test_champion_scores_none_without_champion(tmp_path, monkeypatch):
    import autoresearch.models.trainer as tr
    monkeypatch.setattr(tr, "STORE_ROOT", tmp_path / "empty")
    from autoresearch.scan.l2_model import champion_scores
    scores, engine = champion_scores(make_panel(n_dates=1, n_stocks=50), "l2_fwd5")
    assert scores is None and "no-champion" in engine


def test_champion_scores_with_linear_champion(tmp_path, monkeypatch):
    import autoresearch.models.trainer as tr
    monkeypatch.setattr(tr, "STORE_ROOT", tmp_path / "store")
    save_champion("l2_fwd5", _linear_trained(), "v1", root=tmp_path / "store")
    from autoresearch.scan.l2_model import champion_scores
    frame = make_panel(n_dates=1, n_stocks=50)
    scores, engine = champion_scores(frame, "l2_fwd5")
    assert scores is not None and len(scores) == len(frame) and "champion" in engine
