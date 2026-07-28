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
                             "calendar", "catalyst", "menu", "ledgers")


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


def test_ledgers_step_runs_all_nine(tmp_path, monkeypatch):
    """_ledgers 步覆盖九个账本(watchlist_ledger 已随观察单退役,fb_20260714_002),
    且单点故障不连坐(镜像既有六个的隔离风格)。"""
    import importlib

    from autoresearch.scan.prelude import run_prelude
    monkeypatch.chdir(tmp_path)
    calls: list[str] = []
    for name in _ALL_NINE_LEDGERS:
        mod = importlib.import_module(f"autoresearch.learning.{name}")
        monkeypatch.setattr(mod, "main", (lambda n: lambda: calls.append(n))(name))
    # 一个刻意炸,验证其余九个不受牵连
    boom_mod = importlib.import_module("autoresearch.learning.gate_ledger")

    def _boom():
        calls.append("gate_ledger")
        raise RuntimeError("炸")
    monkeypatch.setattr(boom_mod, "main", _boom)

    results = run_prelude("2026-07-09", skip=_SKIP_ALL_BUT_LEDGERS)
    row = next(r for r in results if r["step"] == "ledgers")
    assert row["ok"] is True                       # contextlib.suppress:单点故障不阻断步骤本身
    assert set(calls) == set(_ALL_NINE_LEDGERS)
    assert "watchlist_ledger" not in calls         # 退役账本不得复活(fb_20260714_002)
    for name in ("channel", "gate", "zero_buy", "changelog"):
        assert name in row["note"]


# ───────────────────────── D1 清欠:retro_input 已备料未收尾 nag(仿 assemble._proposals_nag) ─────────────────────────


def test_retro_input_nag_empty_when_no_scan_root(tmp_path):
    from autoresearch.scan.prelude import _retro_input_nag
    assert _retro_input_nag(tmp_path / "nx") == ""


def test_retro_input_nag_flags_stalled_days_only(tmp_path):
    """有 retro_input.md 但无 done.json = 诊断烂尾,该报;已收尾(有 done.json)的不报。"""
    from autoresearch.scan.prelude import _retro_input_nag
    d1 = tmp_path / "2026-07-07" / "retro"
    d1.mkdir(parents=True)
    (d1 / "retro_input.md").write_text("x", encoding="utf-8")
    d2 = tmp_path / "2026-07-08" / "retro"
    d2.mkdir(parents=True)
    (d2 / "retro_input.md").write_text("x", encoding="utf-8")
    (d2 / "done.json").write_text("{}", encoding="utf-8")
    nag = _retro_input_nag(tmp_path)
    assert "2026-07-07" in nag and "2026-07-08" not in nag
    assert "done.json" in nag


def test_retro_input_nag_silent_when_no_retro_input(tmp_path):
    """无 retro_input.md(还没跑到那步)→ 不是本 nag 的管辖(那是 retro_pending 步的事),保持静默。"""
    from autoresearch.scan.prelude import _retro_input_nag
    d = tmp_path / "2026-07-07" / "retro"
    d.mkdir(parents=True)
    assert _retro_input_nag(tmp_path) == ""


def test_run_prelude_prints_retro_input_nag(tmp_path, monkeypatch, capsys):
    """集成:run_prelude 汇总屏真的把 nag 行打印出来(仿 proposals nag 挂在汇总屏的方式)。"""
    from autoresearch.scan.prelude import run_prelude
    monkeypatch.chdir(tmp_path)
    stalled = tmp_path / "context" / "scan" / "2026-07-07" / "retro"
    stalled.mkdir(parents=True)
    (stalled / "retro_input.md").write_text("x", encoding="utf-8")
    run_prelude("2026-07-09", skip=_SKIP_ALL_BUT_LEDGERS + ("ledgers",))
    out = capsys.readouterr().out
    assert "retro_input" in out and "2026-07-07" in out


def test_run_prelude_writes_succeeded_stage_result(tmp_path, monkeypatch):
    from autoresearch.scan.prelude import run_prelude
    from autoresearch.scan.stage_result import load_stage_result

    monkeypatch.chdir(tmp_path)
    results = run_prelude("2026-07-28", skip=(
        "retro_refresh", "retro_pending", "t1_pending", "learning_health",
        "consensus", "temperature", "universe", "calendar", "catalyst",
        "menu", "ledgers", "dossier_pool",
    ))
    stage = load_stage_result(
        tmp_path / "context" / "scan" / "2026-07-28" / "stage_results" / "prelude.json"
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
        tmp_path / "context" / "scan" / "2026-07-28" / "stage_results" / "prelude.json"
    )
    assert stage.status == "DEGRADED"
    assert stage.metrics == {"n_failed": 1, "n_steps": 2}
    assert stage.warnings == ["universe: RuntimeError: boom"]
