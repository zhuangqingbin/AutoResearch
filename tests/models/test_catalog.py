"""models/catalog.py: the 5 core models are ported; the Qlib zoo's pending tiers are listed."""
from __future__ import annotations

from autoresearch.models.catalog import MODELS, by_status, ported
from autoresearch.models.registry import registered_kinds

_PORTED = {"linear", "lgbm", "xgb", "catboost", "double_ensemble", "mlp", "tabnet"}


def test_core_tabular_models_are_ported():
    for name in _PORTED:
        assert name in MODELS, f"{name} missing from MODELS"
        assert MODELS[name]["status"] == "ported", f"{name} should be ported"
        assert MODELS[name]["feature_set"] == "core"
    assert set(ported()) >= _PORTED   # 7 core tabular are ported (seq lstm/gru also ported, see below)


def test_ported_models_are_actually_registered():
    """Every 'ported' catalog entry must have a live @register binding (kind in registry)."""
    reg = set(registered_kinds())
    for name in ported():
        assert MODELS[name]["kind"] in reg, f"{name} declared ported but kind not registered"


def test_seq_tier_all_ported():
    # all 10 sequence models ported on the seq feature_set (incl. KRNN/SFM, recategorized from graph)
    for name in ("lstm", "gru", "alstm", "tcn", "transformer", "localformer", "tft", "tra", "krnn", "sfm"):
        assert MODELS[name]["status"] == "ported", f"{name} should be ported"
        assert MODELS[name]["feature_set"] == "seq"
    assert by_status("pending-seq") == []


def test_torch_tier_ported_and_graph_pending():
    # mlp/tabnet are now ported (torch installed + native impl); pending-torch tier is empty
    assert by_status("pending-torch") == []
    assert {"mlp", "tabnet"} <= set(ported())
    graph = set(by_status("pending-graph"))
    for name in ("gats", "hist", "igmtf"):   # the 3 true graph models (sfm/krnn recategorized to seq)
        assert name in graph, f"{name} should be pending-graph"
        assert MODELS[name]["feature_set"] == "graph"


def test_every_entry_has_required_keys():
    for name, entry in MODELS.items():
        assert {"kind", "feature_set", "status", "ref"} <= set(entry), f"{name} missing keys"
        assert entry["status"] in {"ported", "pending-torch", "pending-seq", "pending-graph"}
