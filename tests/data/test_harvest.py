"""lake-native harvest:plan_harvest 区间 + harvest 落湖 + 断点续(NO network,注入 fetch)。"""
from __future__ import annotations

import pandas as pd

from autoresearch.data import cache
from autoresearch.data.harvest import harvest, plan_harvest

_CAL = [d.strftime("%Y%m%d") for d in pd.bdate_range("2024-01-01", periods=200)]


def test_plan_harvest_F_every_step_and_P_covers_back_fwd():
    F, P = plan_harvest(_CAL, _CAL[80], _CAL[120], step=5, back=60, fwd=10)
    assert _CAL[80:121:5] == F
    assert P[0] == _CAL[80 - 60] and P[-1] == _CAL[120 + 10]
    assert set(F) <= set(P)


def test_plan_harvest_empty_when_no_days_in_range():
    F, P = plan_harvest(_CAL, "20990101", "20990201", step=3)
    assert F == [] and P == []


def test_harvest_writes_lake_and_is_resumable(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "LAKE", tmp_path / "lake")
    seen = {"n": 0}

    def fake_fetch(endpoint, params):
        seen["n"] += 1
        return pd.DataFrame({"ts_code": ["600000.SH"], "trade_date": [params.get("trade_date", "static")]})

    r1 = harvest(_CAL[80], _CAL[90], step=5, today=_CAL[150], trade_days=_CAL, fetch=fake_fetch)
    assert r1["calls"] > 0
    assert (cache.LAKE / "daily").exists() and (cache.LAKE / "stock_basic" / "static.parquet").exists()
    assert (cache.LAKE / "margin_detail").exists()        # 一个 UZI 端点也落湖
    n_after_first = seen["n"]

    r2 = harvest(_CAL[80], _CAL[90], step=5, today=_CAL[150], trade_days=_CAL, fetch=fake_fetch)
    assert seen["n"] == n_after_first, "断点续:第二次不应再调 fetch(湖命中)"
    assert r2["calls"] == 0
