"""universe.run 的权重档接线(2026-09-24 §2.1)。零网络,夹具同 test_universe_l2_cols。"""
from __future__ import annotations

import json

import pandas as pd

from tests.scan._synth_universe import synth_universe

DATE = "2026-09-17"
PW = {"momentum": 0.20, "tech": 0.15, "volprice": 0.15, "fund_main": 0.15, "chip": 0.05,
      "north": 0.05, "growth": 0.05, "value": 0.05, "fund_retail": -0.05, "rz": 0.0}


def _run(monkeypatch, tmp_path, cfg: dict):
    from autoresearch.scan import events as ev_mod, universe as U
    uni = synth_universe(n=300, seed=3)
    monkeypatch.setattr(U, "build_market_frame",
                        lambda *a, **k: (uni.copy(), {"universe_raw": len(uni), "universe": len(uni)}))
    monkeypatch.setattr(ev_mod, "market_event_counts", lambda *a, **k: pd.DataFrame({"code": []}))
    # universe.run 内部是函数体内 `from autoresearch.scan.user_config import ... load_user_config
    # as _luc`(每次调用现取),故只需 patch 模块上的 load_user_config 本体——没有 U._luc 这个
    # 模块级属性可 patch。
    monkeypatch.setattr("autoresearch.scan.user_config.load_user_config", lambda *a, **k: cfg)
    outdir = tmp_path / DATE
    U.run(DATE, outdir=outdir, recall_n=60, l2_n=20, recall_mode="multi",
          recall_channels=["composite", "momentum", "value"],
          weights_path=str(tmp_path / "no-such-weights.json"))
    return outdir


def test_preference_profile_is_recorded_in_meta_and_weights_used(monkeypatch, tmp_path):
    out = _run(monkeypatch, tmp_path, {"funnel": {"weight_profile": "preference", "preference_weights": PW}})
    meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    used = json.loads((out / "weights_used.json").read_text(encoding="utf-8"))
    assert meta["weight_profile"] == "preference" and meta["weights_source"] == "profile:preference"
    assert used["meta"]["profile"] == "preference" and used["weights"]["__global__"] == PW


def test_default_profile_is_calibrated_parity(monkeypatch, tmp_path):
    out = _run(monkeypatch, tmp_path, {})
    meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    assert meta["weight_profile"] == "calibrated" and meta["weights_source"].startswith("prior")
