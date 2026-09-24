"""行业席位(2026-09-24 §2.3):healthy top3 行业内、非落刀的健康上涨成员,≤per_sector/行业,直通 L1→L2。"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from tests.scan._synth_universe import synth_universe


def _frame():
    df = synth_universe(n=400, seed=9)
    rng = np.random.default_rng(1)
    # 全帧先给 pct_1d 一个非追高基线(synth_universe 本不含此列;`.loc[m, ...]` 只赋值 m 行会
    # 让其余行留 NaN —— `pick_sector_seats` 的追高剔除对 NaN 比较恒 False(不剔),若当季 seed
    # 下恰好有别的行业也过 healthy top3 资格门,那些非电力候选就会带着未定义的 pct_1d 进 seats,
    # 使下面 `< 9.5` 断言对不存在的值也要求为真——先铺满全帧再对电力做行业特定改写)。
    df["pct_1d"] = 1.0
    # 让「电力」成为合格行业:主力净比全正、60 日温和上涨、cmf 正、pe 正
    m = df["industry"] == "电力"
    df.loc[m, "main_net_ratio"] = rng.uniform(0.01, 0.08, m.sum())
    df.loc[m, "pct_60d"] = rng.uniform(2, 30, m.sum())
    df.loc[m, "cmf_20"] = rng.uniform(0.02, 0.4, m.sum())
    df.loc[m, "pe"] = rng.uniform(8, 40, m.sum())
    df.loc[m, "pct_1d"] = 1.0
    df["composite"] = rng.uniform(0, 100, len(df))
    # 埋一只落刀 + 一只当日涨停在电力里,都不得入席
    idx = df.index[m][:2]
    df.loc[idx[0], "pct_60d"] = -35.0
    df.loc[idx[1], "pct_1d"] = 10.0
    return df


def test_pick_sector_seats_only_healthy_non_knife_members():
    from autoresearch.common.scoring import falling_knife_mask, healthy_riser_mask
    from autoresearch.scan.sector_seats import pick_sector_seats
    df = _frame()
    seats = pick_sector_seats(df, per_sector=2, max_sectors=3)
    assert seats and all(s["industry"] == "电力" for s in seats if s["industry"] == "电力")
    codes = {s["code"] for s in seats}
    sub = df[df["code"].isin(codes)]
    assert healthy_riser_mask(sub).all() and not falling_knife_mask(sub).any()
    assert (sub["pct_1d"] < 9.5).all()
    per_ind = pd.Series([s["industry"] for s in seats]).value_counts()
    assert per_ind.max() <= 2


def test_pick_sector_seats_degrades_to_empty_without_columns():
    from autoresearch.scan.sector_seats import pick_sector_seats
    assert pick_sector_seats(pd.DataFrame({"code": ["000001"]})) == []


def test_select_l2_appends_sector_seat_rows_as_reserved():
    from autoresearch.scan.recall.l2_stratify import select_l2
    from tests.scan.test_l2_stratify import _universe
    df = _universe(600)
    df["sector_seat"] = False
    df["sector_seat_industry"] = ""
    df.loc[df.index[:3], ["sector_seat", "sector_seat_industry"]] = [True, "电力"]
    seat_codes = set(df.loc[df.index[:3], "code"])
    out, _ = select_l2(df, 200)
    assert len(out) == 203
    tail = out.tail(3)
    assert set(tail["code"]) == seat_codes
    assert (tail["selection_reason"] == "sector_seat").all() and (tail["selection_detail"] == "电力").all()
    assert tail["l2_lane_reserved"].all()
    assert out["code"].is_unique


def test_select_l2_sector_seat_does_not_squeeze_other_tickets():
    """镜像 tests/scan/test_pinned.py::test_select_l2_pinned_does_not_squeeze_other_tickets:
    席位行不占竞争名额 —— 把席位行从池子里整个抽掉后单跑 select_l2,和「席位打标、留在池子里
    交给 select_l2 自己抽」两条路必须产出**逐码相等**的竞争集,否则就是席位在挤占他票的名额。
    """
    from autoresearch.scan.recall.l2_stratify import select_l2
    from tests.scan.test_l2_stratify import _universe
    df = _universe(600)
    df["sector_seat"] = False
    df["sector_seat_industry"] = ""
    df.loc[df.index[:3], ["sector_seat", "sector_seat_industry"]] = [True, "电力"]

    rest_only, _ = select_l2(df[~df["sector_seat"]].copy(), 200)
    with_seats, _ = select_l2(df, 200)
    non_seat_after = set(with_seats.loc[~with_seats["sector_seat"].fillna(False), "code"])
    assert non_seat_after == set(rest_only["code"])


def test_universe_run_writes_sector_seats_and_columns(monkeypatch, tmp_path):
    from autoresearch.scan import events as ev_mod, universe as U
    uni = _frame().drop(columns=["composite"])
    monkeypatch.setattr(U, "build_market_frame",
                        lambda *a, **k: (uni.copy(), {"universe_raw": len(uni), "universe": len(uni)}))
    monkeypatch.setattr(ev_mod, "market_event_counts", lambda *a, **k: pd.DataFrame({"code": []}))
    cfg = {"l2": {"sector_seats": {"enabled": True, "per_sector": 2, "max_sectors": 3}}}
    monkeypatch.setattr("autoresearch.scan.user_config.load_user_config", lambda *a, **k: cfg)
    outdir = tmp_path / "2026-09-17"
    U.run("2026-09-17", outdir=outdir, recall_n=60, l2_n=20, recall_mode="multi",
          recall_channels=["composite", "momentum", "value"],
          weights_path=str(tmp_path / "no-such-weights.json"))
    doc = json.loads((outdir / "_sector_seats.json").read_text(encoding="utf-8"))
    assert doc["seats"]
    l2 = pd.read_csv(outdir / "L2_gbdt_top200.csv", dtype={"code": str})
    assert "sector_seat" in l2.columns
    assert set(l2.loc[l2["sector_seat"].astype(bool), "code"]) == {s["code"] for s in doc["seats"]}


def test_universe_run_feature_off_is_byte_identical(monkeypatch, tmp_path):
    """旋钮总开关(presence-gated everywhere):默认关 → 不落 `_sector_seats.json`、L1/L2 CSV
    不出现 `sector_seat`/`sector_seat_industry` 列,逐字节回到「本任务动工前」的产物。"""
    from autoresearch.scan import events as ev_mod, universe as U
    uni = _frame().drop(columns=["composite"])
    monkeypatch.setattr(U, "build_market_frame",
                        lambda *a, **k: (uni.copy(), {"universe_raw": len(uni), "universe": len(uni)}))
    monkeypatch.setattr(ev_mod, "market_event_counts", lambda *a, **k: pd.DataFrame({"code": []}))
    monkeypatch.setattr("autoresearch.scan.user_config.load_user_config", lambda *a, **k: {})
    outdir = tmp_path / "2026-09-17"
    U.run("2026-09-17", outdir=outdir, recall_n=60, l2_n=20, recall_mode="multi",
          recall_channels=["composite", "momentum", "value"],
          weights_path=str(tmp_path / "no-such-weights.json"))
    assert not (outdir / "_sector_seats.json").exists()
    l1 = pd.read_csv(outdir / "L1_recall_top1000.csv", dtype={"code": str}, nrows=0)
    l2 = pd.read_csv(outdir / "L2_gbdt_top200.csv", dtype={"code": str}, nrows=0)
    assert "sector_seat" not in l1.columns and "sector_seat_industry" not in l1.columns
    assert "sector_seat" not in l2.columns and "sector_seat_industry" not in l2.columns
    meta = json.loads((outdir / "meta.json").read_text(encoding="utf-8"))
    assert meta["sector_seat_n"] == 0
