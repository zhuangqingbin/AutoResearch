"""Phase 0 —— build_market_frame / market_pack_from_frame / sentinel_advice_from_frame。NO network。

design: docs/specs/2026-07-03-research-skills-altitude-refactor-design.md §5.1 / §7 Phase 0。
锚:帧入口与 scan-dir 入口**同谓词同口径**(帧 = L1_scored_full 的前身)——宏观 lite(Stage 0)
与盘前哨兵预告吃的读数,必须和 scan 内正式读数一致;缺列降级不抛。
"""
from __future__ import annotations

import json

import pandas as pd

from autoresearch.scan import frame as scan_frame
from autoresearch.scan.market import market_pack, market_pack_from_frame
from autoresearch.scan.menu import sentinel_advice, sentinel_advice_from_frame
from tests.scan._synth_universe import synth_universe

DATE = "2026-07-03"


# ───────────────────────── build_market_frame ─────────────────────────


def test_build_market_frame_gate_zfill_counts(monkeypatch):
    uni = synth_universe(n=50, seed=3)
    uni.loc[0, "amount_yi"] = 0.0          # 无流动性 → 轻门剔
    uni.loc[1, "close"] = float("nan")     # 无价 → 轻门剔
    uni.loc[2, "code"] = "1"               # 帧内 code 需 zfill
    monkeypatch.setattr("autoresearch.data.tushare_source.fetch_universe_tushare",
                        lambda *a, **k: uni.copy(), raising=True)
    monkeypatch.setattr("autoresearch.scan.frame._harvest_vol_series",
                        lambda codes, d, lookback=20: pd.DataFrame(columns=["code"]), raising=True)
    frame, counts = scan_frame.build_market_frame(DATE)
    assert counts["universe"] == 50 and counts["after_gate_a"] == 48
    assert "universe_raw" in counts
    assert len(frame) == 48
    assert (frame["code"].str.len() == 6).all()


def test_build_market_frame_no_vol_series(monkeypatch):
    """vol_series=False → 不碰 _harvest_vol_series(盘前省时;healthy 缺 cmf 自会降级)。"""
    uni = synth_universe(n=20, seed=4).drop(columns=["cmf_20", "obv_mom_20"])
    monkeypatch.setattr("autoresearch.data.tushare_source.fetch_universe_tushare",
                        lambda *a, **k: uni.copy(), raising=True)

    def _boom(*a, **k):
        raise AssertionError("vol_series=False 不应取多日量价")

    monkeypatch.setattr("autoresearch.scan.frame._harvest_vol_series", _boom, raising=True)
    frame, _ = scan_frame.build_market_frame(DATE, vol_series=False)
    assert "cmf_20" not in frame.columns


# ───────────────────────── market_pack:帧入口 ≡ scan-dir 入口 ─────────────────────────


def test_market_pack_from_frame_matches_scandir(tmp_path):
    """同一份数据:帧入口四段(regime/breadth/valuation/money)≡ L1_scored_full 入口。"""
    df = synth_universe(n=300, seed=11)
    df.to_csv(tmp_path / "L1_scored_full.csv", index=False)
    via_dir = market_pack(tmp_path)
    via_frame = market_pack_from_frame(df)
    for key in ("regime", "breadth", "valuation", "money"):
        assert via_frame[key] == via_dir[key], key
    # sectors:scan-dir 无 sectors.csv → None;帧入口由 groupby 生成(打分列缺省 None,描述性可缺)
    assert via_dir["sectors"] is None
    secs = via_frame["sectors"]
    assert secs and secs["red"] and secs["black"]
    assert secs["red"][0]["median_composite"] is None
    assert secs["red"][0]["n_recall"] >= 1


def test_market_pack_from_frame_empty():
    empty = {"regime": None, "breadth": None, "valuation": None, "money": None, "sectors": None}
    assert market_pack_from_frame(pd.DataFrame()) == empty
    assert market_pack_from_frame(None) == empty


# ───────────────────────── sentinel:帧入口 ≡ scan-dir 入口 ─────────────────────────


def _write_scandir(tmp_path, df, regime=None):
    df.to_csv(tmp_path / "L1_scored_full.csv", index=False)
    if regime is not None:
        (tmp_path / "meta.json").write_text(json.dumps({"regime": regime}), encoding="utf-8")


def test_sentinel_frame_sentinel_when_no_healthy(tmp_path):
    """全市场健康=0 → 两入口同判 sentinel(材料枯竭)。"""
    df = synth_universe(n=200, seed=5)
    df["pct_60d"] = -30.0                                 # 无一健康上涨
    _write_scandir(tmp_path, df)
    assert sentinel_advice(tmp_path)[0] == "sentinel"
    assert sentinel_advice_from_frame(df)[0] == "sentinel"


def test_sentinel_frame_full_when_rich(tmp_path):
    df = synth_universe(n=200, seed=6)
    df["pct_60d"], df["main_net_ratio"], df["cmf_20"] = 20.0, 0.05, 0.3   # 全员健康
    _write_scandir(tmp_path, df)
    assert sentinel_advice(tmp_path)[0] == "full"
    assert sentinel_advice_from_frame(df)[0] == "full"


def test_sentinel_frame_midband_risk_off(tmp_path):
    """中带(3–5%)+ risk_off → consider(2026-07-08 放宽:删掉 risk_off 升级档,不再 auto-skip);
    帧入口 regime 现算 ≡ scan-dir 读 meta 的同一标签(两入口同判)。"""
    df = synth_universe(n=200, seed=7)
    df["pct_60d"], df["main_net_ratio"], df["cmf_20"] = -30.0, -0.05, -0.1
    df["above_ma60"] = 0.0                                # breadth=0 + med_mom<0 → risk_off
    healthy = df.index[:8]                                # 8/200 = 4% ∈ (3%,5%)
    df.loc[healthy, ["pct_60d", "main_net_ratio", "cmf_20"]] = [20.0, 0.05, 0.3]
    _write_scandir(tmp_path, df, regime="risk_off")
    assert sentinel_advice(tmp_path)[0] == "consider"
    assert sentinel_advice_from_frame(df)[0] == "consider"


def test_sentinel_frame_missing_col_degrades():
    df = synth_universe(n=50, seed=8).drop(columns=["cmf_20"])
    level, reason = sentinel_advice_from_frame(df)
    assert level == "full" and "降级" in reason


# ───────────────────────── CLI 盘前预告 ─────────────────────────


def test_frame_cli_smoke(monkeypatch, capsys, tmp_path):
    df = synth_universe(n=100, seed=9)
    monkeypatch.setattr(scan_frame, "build_market_frame",
                        lambda d, **kw: (df, {"universe_raw": 100, "universe": 100,
                                              "after_gate_a": 100}))
    monkeypatch.setattr("autoresearch.macro.state.load_macro_state",   # 隔离真实 context/macro
                        lambda today, regime_today=None, path=None:
                        (None, "无 macro_state.json → 只用日频 pack"), raising=True)
    monkeypatch.chdir(tmp_path)   # 隔离真实 context/scan/<date>(Plan A3 T1 起 --json 落 echo 文件)
    rc = scan_frame.main([DATE, "--json"])
    captured = capsys.readouterr()
    out, err = captured.out, captured.err
    assert rc == 0
    # 信息行走 stderr(2026-07-11 修:--json 时 stdout 须是纯 JSON,不再混日志——见 test_frame_json_clean.py)
    assert "[sentinel·盘前预告]" in err
    assert "[macro_state]" in err         # Phase 2:宏观视图新鲜度一行
    assert "[sentinel·盘前预告]" not in out
    assert "[macro_state]" not in out
    assert '"breadth"' in out            # --json 打印 market_pack(宏观 lite 输入)
    assert '"macro_state_note"' in out   # 捆绑进 JSON,缺文件 → null + note(presence-gated)
    assert '"user_config"' in out        # Plan A3 T1:用户配置层回显(缺文件 → {})
