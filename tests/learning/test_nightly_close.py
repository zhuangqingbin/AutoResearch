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
    """一步炸掉,其余步骤照常跑完 —— 这正是「不连坐」的可观测形式。"""
    monkeypatch.setattr("autoresearch.learning.retro.pending_days",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("湖挂了")))
    monkeypatch.setattr("autoresearch.learning.t1_review.pending_pairs", lambda *a, **k: [])
    monkeypatch.setattr("autoresearch.learning.t1_review.gap_finalize_pending",
                        lambda *a, **k: (0, []))
    monkeypatch.setattr("autoresearch.learning.tripwire_watch.check", lambda *a, **k: [])
    monkeypatch.setattr("importlib.import_module", lambda name: type(
        "M", (), {"main": staticmethod(lambda: None)})())

    res = N.run("2026-07-28")

    assert [r[0] for r in res] == ["retro_refresh", "t1_backfill", "t1_gap_finalize",
                                    "tripwire", "ledgers"]
    assert res[0][1] is False and "OSError" in res[0][2]
    assert all(r[1] for r in res[1:]), "一步失败把后续步骤也带崩了 = 连坐"


def test_run_reports_counts(monkeypatch):
    monkeypatch.setattr("autoresearch.learning.retro.pending_days",
                        lambda *a, **k: ["2026-07-16", "2026-07-17"])
    monkeypatch.setattr("autoresearch.learning.retro.attribute", lambda d, *a, **k: None)
    monkeypatch.setattr("autoresearch.learning.retro.write_retro_input", lambda d, a, **k: None)
    monkeypatch.setattr("autoresearch.learning.t1_review.pending_pairs",
                        lambda *a, **k: [{"t": "2026-07-24", "t1": "2026-07-27"}])
    monkeypatch.setattr("autoresearch.learning.t1_review.backfill_day", lambda t, *a, **k: {})
    monkeypatch.setattr("autoresearch.learning.t1_review.gap_finalize_pending",
                        lambda *a, **k: (1, []))
    monkeypatch.setattr("autoresearch.learning.tripwire_watch.check",
                        lambda *a, **k: [{"code": "601869"}])
    monkeypatch.setattr("importlib.import_module", lambda name: type(
        "M", (), {"main": staticmethod(lambda: None)})())

    res = {r[0]: r[2] for r in N.run("2026-07-28")}

    assert "归因+备料 2/2 日" in res["retro_refresh"]
    assert "确定性回补 1/1 对" in res["t1_backfill"]
    assert "gap 终判回填 1 日" in res["t1_gap_finalize"]
    assert "⚡ 1 条触发" in res["tripwire"]


def test_t1_gap_finalize_step_surfaces_failed_days(monkeypatch):
    """`gap_finalize_pending` 返回的失败日名单必须原样拼进汇总行,不能悄悄消失
    (中等严重度 review 发现:此前只有一个裸计数,补了几日就断了看不出来)。"""
    monkeypatch.setattr("autoresearch.learning.retro.pending_days", lambda *a, **k: [])
    monkeypatch.setattr("autoresearch.learning.t1_review.pending_pairs", lambda *a, **k: [])
    monkeypatch.setattr("autoresearch.learning.t1_review.gap_finalize_pending",
                        lambda *a, **k: (2, ["2026-07-20"]))
    monkeypatch.setattr("autoresearch.learning.tripwire_watch.check", lambda *a, **k: [])
    monkeypatch.setattr("importlib.import_module", lambda name: type(
        "M", (), {"main": staticmethod(lambda: None)})())

    res = {r[0]: r[2] for r in N.run("2026-07-28")}

    assert res["t1_gap_finalize"] == "gap 终判回填 2 日;1 日失败(2026-07-20)"


def test_run_says_so_when_nothing_pending(monkeypatch):
    """无欠账要明说,不能静默 —— 「什么都没打印」和「跑了但没事做」得分得清。"""
    monkeypatch.setattr("autoresearch.learning.retro.pending_days", lambda *a, **k: [])
    monkeypatch.setattr("autoresearch.learning.t1_review.pending_pairs", lambda *a, **k: [])
    monkeypatch.setattr("autoresearch.learning.t1_review.gap_finalize_pending",
                        lambda *a, **k: (0, []))
    monkeypatch.setattr("autoresearch.learning.tripwire_watch.check", lambda *a, **k: [])
    monkeypatch.setattr("importlib.import_module", lambda name: type(
        "M", (), {"main": staticmethod(lambda: None)})())

    res = {r[0]: r[2] for r in N.run("2026-07-28")}

    assert res["retro_refresh"] == "无待归因日"
    assert res["t1_backfill"] == "无待复盘对"
    assert res["t1_gap_finalize"] == "无待终判日"
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


# ───────────────────────── Wave12-T11 · shadow_buys 入 nightly 账本链 ─────────────────────────
#
# design: docs/specs/2026-08-08-wave12-seven-topics-design.md(2026-08-08 复核)——
# `shadow_buys` 生成器不在 `_ledgers()` names 表,是 near-miss「差一点」节 5/6 run 静默
# 缺席的根因:该节读 `context/learning/shadow_buys.csv` 当日行,唯一写入路径是
# `publisher.py` 的 `is_real` 门控块(`contextlib.suppress(Exception)` 包裹,失败即静默无
# 补救),夜间链此前没有任何兜底重跑。`_ledgers()` 的 names 是嵌套函数局部变量、不对外暴露,
# 只能靠 mock `importlib.import_module` 捕获实际调用顺序来断言(不是读一个模块级常量)。


def _mk_ledgers_noop_chain(monkeypatch):
    """把 run() 前四步(retro/t1_backfill/t1_gap_finalize/tripwire)全部钉成「无待办」,
    只留 `_ledgers()` 这一步的 `importlib.import_module` 调用可观测——与本文件其余测试的
    既有 no-op 钉法同构。"""
    monkeypatch.setattr("autoresearch.learning.retro.pending_days", lambda *a, **k: [])
    monkeypatch.setattr("autoresearch.learning.t1_review.pending_pairs", lambda *a, **k: [])
    monkeypatch.setattr("autoresearch.learning.t1_review.gap_finalize_pending",
                        lambda *a, **k: (0, []))
    monkeypatch.setattr("autoresearch.learning.tripwire_watch.check", lambda *a, **k: [])


def test_ledgers_step_imports_shadow_buys_after_gate_attribution(monkeypatch):
    calls: list[str] = []

    def _fake_import(name):
        calls.append(name)
        return type("M", (), {"main": staticmethod(lambda *a: None)})()

    _mk_ledgers_noop_chain(monkeypatch)
    monkeypatch.setattr("importlib.import_module", _fake_import)

    N.run("2026-07-28")

    learning_calls = [c.rsplit(".", 1)[-1] for c in calls if c.startswith("autoresearch.learning.")]
    assert "shadow_buys" in learning_calls
    assert learning_calls.index("shadow_buys") > learning_calls.index("gate_attribution")


def test_ledgers_step_full_chain_19_plus_1_all_ok(monkeypatch):
    """mock 全链跑一遍:补 shadow_buys 后,学习侧 18 个 + scan 侧 2 个(structural_audit/
    l2_slo)= 20 个模块全部 mock 成功 → 汇总行必须是 20/20(此前 19/19;19+1=20)。"""
    _mk_ledgers_noop_chain(monkeypatch)
    monkeypatch.setattr("importlib.import_module", lambda name: type(
        "M", (), {"main": staticmethod(lambda *a: None)})())

    res = {r[0]: r[2] for r in N.run("2026-07-28")}

    assert res["ledgers"] == "20/20 刷新"


def test_retro_step_writes_input_not_just_attribution(monkeypatch):
    """归因与备料必须成对:write_retro_input 吃的是 attribute() 的**内存帧**
    (CSV 落盘丢了 tradable 等派生列,从 CSV 重读会 KeyError)。首版只跑 attribute,
    人第二天打开 scan-retro 才发现 retro_input.md 不在 —— 自动化只省了半步。"""
    seen = {}
    monkeypatch.setattr("autoresearch.learning.retro.pending_days",
                        lambda *a, **k: ["2026-07-24"])
    def _fake_attribute(d, *a, **k):
        seen["attr"] = d
        return "FRAME"

    monkeypatch.setattr("autoresearch.learning.retro.attribute", _fake_attribute)
    monkeypatch.setattr("autoresearch.learning.retro.write_retro_input",
                        lambda d, frame, **k: seen.update(input_day=d, frame=frame))
    monkeypatch.setattr("autoresearch.learning.t1_review.pending_pairs", lambda *a, **k: [])
    monkeypatch.setattr("autoresearch.learning.t1_review.gap_finalize_pending",
                        lambda *a, **k: (0, []))
    monkeypatch.setattr("autoresearch.learning.tripwire_watch.check", lambda *a, **k: [])
    monkeypatch.setattr("importlib.import_module", lambda name: type(
        "M", (), {"main": staticmethod(lambda: None)})())

    N.run("2026-07-28")

    assert seen["attr"] == "2026-07-24"
    assert seen["input_day"] == "2026-07-24", "只归因没备料 = 自动化只省了半步"
    assert seen["frame"] == "FRAME", "备料必须吃内存帧,不是从 CSV 重读"
