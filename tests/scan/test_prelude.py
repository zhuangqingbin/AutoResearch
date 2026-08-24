"""prelude 编排骨架:单步失败不阻断、结果结构。整链为已测组件的编排,真跑验证。合成。

spec: docs/specs/2026-07-03-scan-run-reliability-design.md §2
"""
from __future__ import annotations

from autoresearch.common import workspace as ws
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


#: 2026-08-21 learning 层退役后 prelude 只剩 7 步;这里跳掉除 temperature 外的全部
#: (原常量还列着 retro_refresh/retro_pending/ledgers 三个已删步骤)。
_SKIP_ALL_BUT_TEMPERATURE = ("consensus", "universe", "calendar", "catalyst",
                             "menu", "dossier_pool", "news_catalog")


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


# ───────────────────────── D3 清欠:_ledgers 白名单加四账本 ─────────────────────────

_SKIP_ALL_BUT_LEDGERS = ("retro_refresh", "retro_pending", "consensus", "temperature",
                         "universe", "calendar", "catalyst", "menu")

_ALL_NINE_LEDGERS = ("journal", "buy_ledger", "cross_calib", "catalyst_ledger", "paper_nav",
                     "channel_ledger", "gate_ledger", "zero_buy_ledger",
                     "changelog_ledger")


# ───────────────────────── D1 清欠:retro_input 已备料未收尾 nag(仿 assemble._proposals_nag) ─────────────────────────


def test_run_prelude_writes_succeeded_stage_result(tmp_path, monkeypatch):
    from autoresearch.scan.prelude import run_prelude
    from autoresearch.scan.stage_result import load_stage_result

    monkeypatch.chdir(tmp_path)
    results = run_prelude("2026-07-28", skip=(
        "preflight",
        "retro_refresh", "retro_pending", "t1_pending", "learning_health",
        "consensus", "temperature", "universe", "calendar", "catalyst",
        "menu", "ledgers", "dossier_pool",
        "news_catalog",              # Wave12-T35 新步骤;本测试要的是"零步骤"的形状
    ))
    stage = load_stage_result(
        tmp_path / ws.scan_root() / "2026-07-28" / "stage_results" / "prelude.json"
    )
    assert results == []
    assert stage.status == "SUCCEEDED"
    assert stage.metrics == {"n_failed": 0, "n_steps": 0}
    assert stage.warnings == []


def test_run_prelude_writes_degraded_stage_result(tmp_path, monkeypatch):
    from autoresearch.scan import prelude
    from autoresearch.scan.stage_result import load_stage_result

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(prelude, "_run_steps", lambda steps: [
        {"step": "universe", "ok": False, "note": "RuntimeError: boom"},
        {"step": "calendar", "ok": True, "note": "ok"},
    ])
    prelude.run_prelude("2026-07-28", skip=())
    stage = load_stage_result(
        tmp_path / ws.scan_root() / "2026-07-28" / "stage_results" / "prelude.json"
    )
    assert stage.status == "DEGRADED"
    assert stage.metrics == {"n_failed": 1, "n_steps": 2}
    assert stage.warnings == ["universe: RuntimeError: boom"]
