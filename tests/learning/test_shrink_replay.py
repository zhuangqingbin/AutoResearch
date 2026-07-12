"""留一日回放裁决器(P0-3 裁决法):逐日 loader + 通用回放引擎 + verdict/render/main。合成,无网络。

spec: docs/specs/2026-07-12-selflearning-optimization-brainstorm.md §4 P0-3 裁决法。
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from autoresearch.learning import shrink_replay
from autoresearch.learning.shrink_replay import (
    _day_flip_obs,
    _day_tail_obs,
    _day_touch_obs,
    _replay_track,
)

CARD = "**Rating**: {rating}\n"


# ───────────────────────── 逐日 loader ─────────────────────────


def test_day_flip_obs_extracts_hiconv_rows_only(tmp_path):
    d = tmp_path / "2026-07-01"
    (d / "details").mkdir(parents=True)
    pd.DataFrame([{"code": "000001", "name": "n1"}, {"code": "000002", "name": "n2"}]
                ).to_csv(d / "finalists.csv", index=False)
    (d / "details" / "000001.md").write_text(CARD.format(rating="Underweight"), encoding="utf-8")
    (d / "details" / "000002.md").write_text(CARD.format(rating="Hold"), encoding="utf-8")
    pd.DataFrame([{"code": "000001", "lane": "吸筹", "conviction": 80},
                  {"code": "000002", "lane": "吸筹", "conviction": 75},
                  {"code": "000002", "lane": "趋势", "conviction": 50},        # <70 不入
                  {"code": "000003", "lane": "吸筹", "conviction": 90}]       # 无卡不入
                ).to_csv(d / "L3_judged_full.csv", index=False)
    obs = _day_flip_obs(d)
    assert set(obs["lane"]) == {"吸筹"}
    assert len(obs) == 2
    assert obs["is_flip"].sum() == 1.0       # 只有 000001(UW)翻案


def test_day_flip_obs_missing_file_returns_empty(tmp_path):
    assert _day_flip_obs(tmp_path / "nx").empty


def test_day_touch_obs_reads_regime_and_hi2(tmp_path):
    d = tmp_path / "2026-07-02"
    (d / "retro").mkdir(parents=True)
    pd.DataFrame({"code": ["000001", "000002"], "hi_2_oc": [0.10, 0.02]}
                ).to_csv(d / "retro" / "attribution.csv", index=False)
    (d / "meta.json").write_text(json.dumps({"regime": "range"}), encoding="utf-8")
    obs = _day_touch_obs(d)
    assert list(obs["regime"].unique()) == ["range"]
    assert obs["touch8"].tolist() == [1.0, 0.0]


def test_day_touch_obs_missing_regime_returns_empty(tmp_path):
    d = tmp_path / "2026-07-02"
    (d / "retro").mkdir(parents=True)
    pd.DataFrame({"code": ["000001"], "hi_2_oc": [0.10]}).to_csv(
        d / "retro" / "attribution.csv", index=False)
    assert _day_touch_obs(d).empty          # 无 meta.json → 无法归桶


def test_day_tail_obs_joins_fires_with_attribution(tmp_path):
    d = tmp_path / "2026-07-09"
    (d / "retro").mkdir(parents=True)
    pd.DataFrame([{"date": "2026-07-09", "check": "门A", "code": "000002"},
                  {"date": "2026-07-09", "check": "门A", "code": "000003"}]
                ).to_csv(d / "gate_fires.csv", index=False)
    pd.DataFrame([{"code": "000002", "fwd_2_oc": -0.08}, {"code": "000003", "fwd_2_oc": 0.02}]
                ).to_csv(d / "retro" / "attribution.csv", index=False)
    obs = _day_tail_obs(d)
    assert len(obs) == 2
    assert obs["tail"].tolist() == [1.0, 0.0]


def test_day_tail_obs_missing_gate_fires_returns_empty(tmp_path):
    assert _day_tail_obs(tmp_path / "nx").empty


# ───────────────────────── 通用回放引擎:_replay_track ─────────────────────────


def test_replay_track_basic_mae():
    """两桶(A/B)两日:day1 纯历史,day2 被预测。手算 shrink(1,4,0.5,15)=11.5/19、
    shrink(0,4,0.5,15)=7.5/19,MAE(raw)=0.75 vs MAE(shrunk)≈0.3553(shrunk 更准)。"""
    data = {
        Path("d1"): pd.DataFrame({"bucket": ["A", "A", "A", "A", "B", "B", "B", "B"],
                                  "obs": [1.0, 1.0, 1.0, 1.0, 0.0, 0.0, 0.0, 0.0]}),
        Path("d2"): pd.DataFrame({"bucket": ["A", "A", "B", "B"], "obs": [0.0, 1.0, 1.0, 1.0]}),
    }
    days = [Path("d1"), Path("d2")]
    out = _replay_track(days, lambda d: data[d], bucket_col="bucket", obs_col="obs", k=15)
    assert out["n_days"] == 1                 # 只有 d2 有历史可用于预测
    assert out["n_pairs"] == 2
    assert out["n_coldstart"] == 0
    assert out["mae_raw"] == 0.75
    assert abs(out["mae_shrunk"] - 0.3553) < 1e-4


def test_replay_track_coldstart_excluded_from_mae():
    """C 桶在历史里从未出现(冷启动)→ 单独计数,不进 pairs/MAE。"""
    data = {
        Path("d1"): pd.DataFrame({"bucket": ["A", "A", "A"], "obs": [1.0, 1.0, 1.0]}),
        Path("d2"): pd.DataFrame({"bucket": ["A", "A", "C", "C"], "obs": [1.0, 1.0, 0.0, 1.0]}),
    }
    days = [Path("d1"), Path("d2")]
    out = _replay_track(days, lambda d: data[d], bucket_col="bucket", obs_col="obs", k=15)
    assert out["n_pairs"] == 1          # 只有 A
    assert out["n_coldstart"] == 1      # C
    assert out["mae_raw"] == 0.0 and out["mae_shrunk"] == 0.0


def test_replay_track_first_day_never_contributes_pairs():
    data = {Path("d1"): pd.DataFrame({"bucket": ["A"], "obs": [1.0]})}
    out = _replay_track([Path("d1")], lambda d: data[d], bucket_col="bucket", obs_col="obs")
    assert out["n_pairs"] == 0 and out["mae_raw"] is None and out["mae_shrunk"] is None


def test_replay_track_no_data_anywhere_returns_none_mae():
    out = _replay_track([Path("d1"), Path("d2")],
                        lambda d: pd.DataFrame(columns=["bucket", "obs"]),
                        bucket_col="bucket", obs_col="obs")
    assert out["n_pairs"] == 0
    assert out["mae_raw"] is None and out["mae_shrunk"] is None


# ───────────────────────── verdict ─────────────────────────


def test_verdict_thin_sample_no_conclusion():
    v = shrink_replay.verdict({"n_pairs": 2, "mae_raw": 0.1, "mae_shrunk": 0.05})
    assert "样本过少" in v


def test_verdict_shrunk_better():
    v = shrink_replay.verdict({"n_pairs": 10, "mae_raw": 0.2, "mae_shrunk": 0.1})
    assert "shrunk 更优" in v


def test_verdict_raw_better_says_rollback():
    v = shrink_replay.verdict({"n_pairs": 10, "mae_raw": 0.1, "mae_shrunk": 0.2})
    assert "raw 更优" in v and "回滚" in v


def test_verdict_tie():
    v = shrink_replay.verdict({"n_pairs": 10, "mae_raw": 0.15, "mae_shrunk": 0.15})
    assert "打平" in v


# ───────────────────────── render / main ─────────────────────────


def test_render_includes_all_three_tracks_and_thin_note():
    flip = {"n_days": 3, "n_pairs": 5, "n_coldstart": 1, "mae_raw": 0.1, "mae_shrunk": 0.08}
    touch = {"n_days": 2, "n_pairs": 6, "n_coldstart": 0, "mae_raw": 0.2, "mae_shrunk": 0.25}
    tail = {"n_days": 1, "n_pairs": 1, "n_coldstart": 0, "mae_raw": None, "mae_shrunk": None}
    md = "\n".join(shrink_replay.render(flip, touch, tail, k=15))
    assert "翻案率" in md and "触达率" in md and "左尾率" in md
    assert "样本过少" in md      # tail n_pairs=1<5


def test_main_writes_report_even_with_no_scan_data(tmp_path, monkeypatch):
    """空 context/scan(或不存在)→ 三轨道全空手,仍正常写报告不炸(诚实报"样本过少")。"""
    monkeypatch.chdir(tmp_path)
    rc = shrink_replay.main()
    assert rc == 0
    out = tmp_path / "reports" / "learning" / "shrink_replay.md"
    assert out.exists()
    assert "翻案率" in out.read_text(encoding="utf-8")
