"""夜间确定性欠账补跑(Wave7 P5)。

治的是全项目最贵的病:腿没人踢。判断力基建建成后大面积闲置 —— retro 欠 3 天、t1 快环
欠 1 对、账本落后一个 run,而这些欠账里**确定性的那一半**本来就不需要人。
边界(不是省略):本模块只跑算得出对错的部分,LLM 诊断段仍人工。
"""
from __future__ import annotations

from autoresearch.learning import nightly_close as N


def test_step_captures_failure_without_raising():
    """单步失败不连坐,也不上抛 —— 夜间任务不该把 launchd 搞成红灯常亮。"""
    name, ok, note = N._step("boom", lambda: (_ for _ in ()).throw(RuntimeError("炸了")))
    assert name == "boom" and ok is False and "RuntimeError" in note and "炸了" in note


def test_step_records_success_note():
    assert N._step("fine", lambda: "补 3 日") == ("fine", True, "补 3 日")


def test_run_is_isolated_per_step(monkeypatch):
    """一步炸掉,其余三步照常跑完 —— 这正是「不连坐」的可观测形式。"""
    monkeypatch.setattr("autoresearch.learning.retro.pending_days",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("湖挂了")))
    monkeypatch.setattr("autoresearch.learning.t1_review.pending_pairs", lambda *a, **k: [])
    monkeypatch.setattr("autoresearch.learning.tripwire_watch.check", lambda *a, **k: [])
    monkeypatch.setattr("importlib.import_module", lambda name: type(
        "M", (), {"main": staticmethod(lambda: None)})())

    res = N.run("2026-07-28")

    assert [r[0] for r in res] == ["retro_refresh", "t1_backfill", "tripwire", "ledgers"]
    assert res[0][1] is False and "OSError" in res[0][2]
    assert all(r[1] for r in res[1:]), "一步失败把后续步骤也带崩了 = 连坐"


def test_run_reports_counts(monkeypatch):
    monkeypatch.setattr("autoresearch.learning.retro.pending_days",
                        lambda *a, **k: ["2026-07-16", "2026-07-17"])
    monkeypatch.setattr("autoresearch.learning.retro.attribute", lambda d, *a, **k: None)
    monkeypatch.setattr("autoresearch.learning.t1_review.pending_pairs",
                        lambda *a, **k: [{"t": "2026-07-24", "t1": "2026-07-27"}])
    monkeypatch.setattr("autoresearch.learning.t1_review.backfill_day", lambda t, *a, **k: {})
    monkeypatch.setattr("autoresearch.learning.tripwire_watch.check",
                        lambda *a, **k: [{"code": "601869"}])
    monkeypatch.setattr("importlib.import_module", lambda name: type(
        "M", (), {"main": staticmethod(lambda: None)})())

    res = dict((r[0], r[2]) for r in N.run("2026-07-28"))

    assert "归因 2/2 日" in res["retro_refresh"]
    assert "确定性回补 1/1 对" in res["t1_backfill"]
    assert "⚡ 1 条触发" in res["tripwire"]


def test_run_says_so_when_nothing_pending(monkeypatch):
    """无欠账要明说,不能静默 —— 「什么都没打印」和「跑了但没事做」得分得清。"""
    monkeypatch.setattr("autoresearch.learning.retro.pending_days", lambda *a, **k: [])
    monkeypatch.setattr("autoresearch.learning.t1_review.pending_pairs", lambda *a, **k: [])
    monkeypatch.setattr("autoresearch.learning.tripwire_watch.check", lambda *a, **k: [])
    monkeypatch.setattr("importlib.import_module", lambda name: type(
        "M", (), {"main": staticmethod(lambda: None)})())

    res = dict((r[0], r[2]) for r in N.run("2026-07-28"))

    assert res["retro_refresh"] == "无待归因日"
    assert res["t1_backfill"] == "无待复盘对"
    assert res["tripwire"] == "无触发"


def test_render_marks_failures_visibly():
    md = N.render([("a", True, "ok"), ("b", False, "OSError: x")], "2026-07-28")
    assert "✓ a" in md and "✗ b" in md and "1 步失败" in md


def test_render_all_green():
    md = N.render([("a", True, "ok")], "2026-07-28")
    assert "1/1 成功" in md and "步失败" not in md


def test_main_exit_code_is_always_zero(monkeypatch, capsys):
    """恒 0 是刻意的:失败状态看汇总行,不靠退出码 —— 否则 launchd 会红灯常亮。"""
    monkeypatch.setattr(N, "run", lambda today: [("a", False, "boom")])
    assert N.main(["2026-07-28"]) == 0
    assert "✗ a" in capsys.readouterr().out
