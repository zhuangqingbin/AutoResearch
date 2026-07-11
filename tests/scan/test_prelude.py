"""prelude 编排骨架:单步失败不阻断、结果结构。整链为已测组件的编排,真跑验证。合成。

spec: docs/specs/2026-07-03-scan-run-reliability-design.md §2
"""
from __future__ import annotations

from autoresearch.scan.prelude import _run_steps


def test_run_steps_isolation():
    def ok():
        return "好"

    def boom():
        raise RuntimeError("炸")

    res = _run_steps([("a", ok), ("b", boom), ("c", ok)])
    assert [r["step"] for r in res] == ["a", "b", "c"]      # b 炸不阻 c
    assert [r["ok"] for r in res] == [True, False, True]
    assert res[0]["note"] == "好" and "炸" in res[1]["note"]


def test_calib_suggestion_lines(tmp_path):
    """当日件建议行收集(spec 2026-07-05 §8 验收⑤):无现场 → 空;有卡少样本 → thin 禁注行。"""
    import pandas as pd

    from autoresearch.scan.prelude import calib_suggestion_lines
    assert calib_suggestion_lines(tmp_path / "nx") == []
    d = tmp_path / "2026-07-01"
    (d / "details").mkdir(parents=True)
    (d / "retro").mkdir()
    pd.DataFrame([{"code": "000001", "name": "甲"}]).to_csv(d / "finalists.csv", index=False)
    pd.DataFrame([{"code": "000001", "close": 100.0}]).to_csv(d / "L1_scored_full.csv", index=False)
    (d / "details" / "000001.md").write_text(
        "# 卡\n\n| 评级 | 目标(EV) | R:R |\n|---|---|---|\n| Hold | 120(EV) | 2:1 |\n\n"
        "OW三门:主力真在✓ · 业绩真兑现✗ · 估值不透支✓ → 压 Hold\n\n**Rating**: Hold\n",
        encoding="utf-8")
    pd.DataFrame([{"code": "000001", "fwd_1_oo": 0.01, "fwd_5_oc": 0.08, "fwd_10_oc": 0.1,
                   "hi_10_oc": 0.25, "gap_d1": 0.02}]).to_csv(
        d / "retro" / "attribution.csv", index=False)
    lines = calib_suggestion_lines(tmp_path)
    assert lines and any(ln.startswith("📐") for ln in lines)
    assert all("禁注" in ln for ln in lines)                 # n=1 全 thin → 全带禁注


_SKIP_ALL_BUT_TEMPERATURE = ("retro_refresh", "retro_pending", "consensus", "universe",
                             "calendar", "watchlist", "catalyst", "menu", "ledgers")


def test_temperature_step_reports_score_and_phase(tmp_path, monkeypatch):
    """新 prelude 步 temperature:rollup 有新行 → 摘要含 score/phase(NO network,rollup 打桩)。"""
    import pandas as pd

    from autoresearch.scan.prelude import run_prelude
    monkeypatch.chdir(tmp_path)   # 隔离 _write_t0 落盘(context/scan/<date>/_t0.json)

    def _fake_rollup(start, end):
        assert start == end == "2026-07-09"          # 当日增量:start==end==今日
        return pd.DataFrame([{"date": end, "score": 62.1, "phase": "发酵"}])
    monkeypatch.setattr("autoresearch.scan.temperature.rollup", _fake_rollup)
    results = run_prelude("2026-07-09", skip=_SKIP_ALL_BUT_TEMPERATURE)
    row = next(r for r in results if r["step"] == "temperature")
    assert row["ok"] is True
    assert "score=62.1" in row["note"] and "phase=发酵" in row["note"]


def test_temperature_step_empty_rollup_degrades_not_fails(tmp_path, monkeypatch):
    """rollup 空回填(取数失败/presence-gated)→ 步骤仍 ok=True,note 明确降级说明。"""
    import pandas as pd

    from autoresearch.scan.prelude import run_prelude
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("autoresearch.scan.temperature.rollup", lambda start, end: pd.DataFrame())
    results = run_prelude("2026-07-09", skip=_SKIP_ALL_BUT_TEMPERATURE)
    row = next(r for r in results if r["step"] == "temperature")
    assert row["ok"] is True and "无新增" in row["note"]
