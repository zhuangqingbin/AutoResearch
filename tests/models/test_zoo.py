"""zoo train_zoo:多 horizon × 多模型 leaderboard + 故障隔离 + champion 门(NO network)。"""
from __future__ import annotations

import autoresearch.models.zoo as zoo
from autoresearch.models.trainer import load_champion_any
from autoresearch.models.zoo import _tag, train_zoo
from tests.models._synth import StubHandler, make_panel


def test_tag_horizon_to_champion_name():
    assert _tag("fwd_1_oo") == "l2_fwd1"
    assert _tag("fwd_5_oc") == "l2_fwd5"
    assert _tag("fwd_10_oc") == "l2_fwd10"


def test_train_zoo_leaderboard_isolation_and_champion(tmp_path, monkeypatch):
    h = StubHandler(make_panel(n_dates=10, n_stocks=150, signal=0.8))
    # 用 linear + lgbm(真训)+ boom(注入故障)三个 → 验证隔离与 champion 门。
    monkeypatch.setattr(zoo, "_resolve_models", lambda names: [
        ("linear", "linear", "core"), ("lgbm", "lgbm", "core"), ("boom", "boom", "core")])
    real_train_one = zoo._train_one

    def fake_train_one(handler, cfg, dates, label, **kw):
        if cfg.kind == "boom":
            raise RuntimeError("intentional")
        return real_train_one(handler, cfg, dates, label, **kw)

    monkeypatch.setattr(zoo, "_train_one", fake_train_one)

    lb = train_zoo(h, ["20260101"], ["fwd_1_oo", "fwd_5_oc"],
                   store_root=tmp_path / "store", out_csv=tmp_path / "lb.csv")

    assert {"horizon", "model", "feature_set", "oos_rank_ic", "vs_linear", "status"} <= set(lb.columns)
    assert lb["status"].str.startswith("error").any(), "坏模型应记 error 不中断全 zoo"
    assert (lb["status"] == "ok").sum() >= 2, "linear + lgbm 应照常训练"
    assert (tmp_path / "lb.csv").exists()

    # champion 门:某 horizon 有非线性模型胜线性(vs_linear>0) ⟺ store 落了 champion。
    for hz in ("fwd_1_oo", "fwd_5_oc"):
        beat = lb[(lb.horizon == hz) & (lb.vs_linear > 0) & (lb.status == "ok")]
        champ = load_champion_any(_tag(hz), root=tmp_path / "store")
        assert (champ is not None) == (len(beat) > 0), f"{hz} champion 门与 leaderboard 不一致"
