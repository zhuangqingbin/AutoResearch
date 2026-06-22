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

    lb = train_zoo(h, ["20260101"], ["fwd_1_oo", "fwd_5_oc"], gate="positive",
                   store_root=tmp_path / "store", out_csv=tmp_path / "lb.csv")

    assert {"horizon", "model", "feature_set", "oos_rank_ic", "vs_linear", "status"} <= set(lb.columns)
    assert lb["status"].str.startswith("error").any(), "坏模型应记 error 不中断全 zoo"
    assert (lb["status"] == "ok").sum() >= 2, "linear + lgbm 应照常训练"
    assert (tmp_path / "lb.csv").exists()

    # champion 门:某 horizon 有非线性模型胜线性(vs_linear>0)**且正 IC**(>0) ⟺ store 落了 champion。
    for hz in ("fwd_1_oo", "fwd_5_oc"):
        beat = lb[(lb.horizon == hz) & (lb.vs_linear > 0) & (lb.oos_rank_ic > 0) & (lb.status == "ok")]
        champ = load_champion_any(_tag(hz), root=tmp_path / "store")
        assert (champ is not None) == (len(beat) > 0), f"{hz} champion 门与 leaderboard 不一致"


def test_champion_gate_restricts_to_core(tmp_path, monkeypatch):
    """L2 champion 只从 core 选(seq/graph 视图在召回帧不可得);即便 graph IC 最高也不晋升。"""
    import json

    from autoresearch.models.base import FitReport
    from autoresearch.models.linear import LinearComposite
    from autoresearch.models.trainer import TrainedModel
    monkeypatch.setattr(zoo, "_resolve_models", lambda names: [
        ("linear", "linear", "core"), ("good_core", "good_core", "core"),
        ("fake_graph", "fake_graph", "graph")])
    ics = {"linear": -0.01, "good_core": 0.01, "fake_graph": 0.99}

    def fake_train_one(handler, cfg, dates, label, **kw):
        return TrainedModel(model=LinearComposite(), report=FitReport(n_rows=1, n_dates=1, notes={}),
                            oos_rank_ic=ics[cfg.kind],
                            meta={"kind": cfg.kind, "feature_set": cfg.feature_set})
    monkeypatch.setattr(zoo, "_train_one", fake_train_one)
    zoo.train_zoo(object(), ["d"], ["fwd_1_oo"], store_root=tmp_path / "store")
    ptr = json.loads((tmp_path / "store" / "l2_fwd1" / "champion.json").read_text())
    assert ptr["kind"] == "good_core"     # 高 IC 的 fake_graph 不被选(graph 不可作 L2 champion)


def test_train_zoo_clears_stale_champion_on_no_promote(tmp_path, monkeypatch):
    """已有旧 champion + 新一轮全负(无合格)→ 清除旧 champion(L2 回落 composite,store 反映最新评估)。"""
    from autoresearch.models.base import FitReport
    from autoresearch.models.linear import LinearComposite
    from autoresearch.models.trainer import TrainedModel, save_champion
    store = tmp_path / "store"
    save_champion("l2_fwd1", TrainedModel(LinearComposite(), FitReport(n_rows=1, n_dates=1, notes={}),
                                          0.05, {"kind": "linear", "feature_set": "core"}), "v1", root=store)
    assert load_champion_any("l2_fwd1", root=store) is not None
    monkeypatch.setattr(zoo, "_resolve_models", lambda names: [("linear", "linear", "core"), ("c", "c", "core")])

    def neg(handler, cfg, dates, label, **kw):
        ic = -0.03 if cfg.kind == "linear" else -0.05   # 都负且都不胜线性 → 无合格者(两种 gate 都清)
        return TrainedModel(LinearComposite(), FitReport(n_rows=1, n_dates=1, notes={}),
                            ic, {"kind": cfg.kind, "feature_set": "core"})
    monkeypatch.setattr(zoo, "_train_one", neg)
    zoo.train_zoo(object(), ["d"], ["fwd_1_oo"], store_root=store)
    assert load_champion_any("l2_fwd1", root=store) is None    # 旧 champion 被清 → L2 回落


def test_beats_linear_gate_promotes_negative_but_better(tmp_path, monkeypatch):
    """默认 gate=beats_linear:负 IC 但胜线性的最优 core 仍晋升(最不伤切,优于 composite 回落);
    positive 门则因其 <0 不晋升、清旧。"""
    import json

    from autoresearch.models.base import FitReport
    from autoresearch.models.linear import LinearComposite
    from autoresearch.models.trainer import TrainedModel, load_champion_any
    store = tmp_path / "store"
    monkeypatch.setattr(zoo, "_resolve_models",
                        lambda names: [("linear", "linear", "core"), ("xgb_like", "xgb_like", "core")])

    def negbeat(handler, cfg, dates, label, **kw):
        ic = -0.06 if cfg.kind == "linear" else -0.02      # 都负,但 xgb_like 胜线性
        return TrainedModel(LinearComposite(), FitReport(n_rows=1, n_dates=1, notes={}),
                            ic, {"kind": cfg.kind, "feature_set": "core"})
    monkeypatch.setattr(zoo, "_train_one", negbeat)

    zoo.train_zoo(object(), ["d"], ["fwd_1_oo"], store_root=store)           # 默认 beats_linear
    assert json.loads((store / "l2_fwd1" / "champion.json").read_text())["kind"] == "xgb_like"
    zoo.train_zoo(object(), ["d"], ["fwd_1_oo"], store_root=store, gate="positive")  # 严格门
    assert load_champion_any("l2_fwd1", root=store) is None                  # 负 → 不晋升 + 清
