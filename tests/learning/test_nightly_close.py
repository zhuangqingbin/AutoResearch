"""夜间确定性欠账补跑(Wave7 P5)。

治的是全项目最贵的病:腿没人踢。判断力基建建成后大面积闲置 —— retro 欠 3 天、t1 快环
欠 1 对、账本落后一个 run,而这些欠账里**确定性的那一半**本来就不需要人。
边界(不是省略):本模块只跑算得出对错的部分,LLM 诊断段仍人工。
"""
from __future__ import annotations

import pytest

from autoresearch.learning import nightly_close as N
import json  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)

_FLASH_STUB = {"per_source": [{"source": "global_em", "status": "OK", "rows": 2,
                               "observations": 2}],
               "added": 2, "revised": 0, "unchanged": 0, "rejected": [],
               "any_ok": True, "health": {"n_observations": 2}}


@pytest.fixture(autouse=True)
def _no_live_news(monkeypatch):
    """禁挂真网(Wave12-T35):`news_flash` 步会真调 akshare 三个端点。

    ⚠️ 这个 fixture 是**必须**的,不是洁癖:接线首版没有它,本文件每条跑 `N.run` 的测试
    都会真的去拉 620 条快讯(实测整轮从 ~1s 涨到 ~2 分钟),而且结果随行情变化 ——
    既慢又不稳。`_news_flash` 用的是 `from ... import ingest_flash`(函数内 import),
    所以要 patch **catalog 模块上的名字**,patch `importlib.import_module` 拦不住它。
    """
    monkeypatch.setattr("autoresearch.news.catalog.ingest_flash",
                        lambda *a, **k: dict(_FLASH_STUB))


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
                                    "tripwire", "ledgers", "news_flash"]
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


def test_ledgers_step_imports_shadow_buys_before_paper_nav(monkeypatch):
    """I3 修复(final-review 2026-08-08):最初实现把 `shadow_buys` 排在 `gate_attribution`
    之后(位置 8),照办了 T11 任务书 Step2 的字面指示——但任务书那句指示本身是错的:
    `shadow_buys` 唯一的消费者是 `paper_nav`(`shadow_signals()` 读它的 csv),而
    `paper_nav` 排在第 5 位、`gate_attribution` 第 7 位,把生产者排在第 8 位等于**同一晚**
    先跑消费者、后跑生产者——当晚 `paper_nav` 的影子线仍读不到刚发布的信号,要等下一晚才
    补上。`gate_attribution` 与 `shadow_buys` 之间没有任何依赖(`shadow_buys` 只读
    `context/scan/` 原始产物),"排在 gate_attribution 之后"这条约束本身是多余且有害的
    ——真正的约束是"排在 paper_nav 之前"。此修复反向断言:任何把 shadow_buys 排在
    paper_nav 之后的实现都应该被这条测试挡下。"""
    calls: list[str] = []

    def _fake_import(name):
        calls.append(name)
        return type("M", (), {"main": staticmethod(lambda *a: None)})()

    _mk_ledgers_noop_chain(monkeypatch)
    monkeypatch.setattr("importlib.import_module", _fake_import)

    N.run("2026-07-28")

    learning_calls = [c.rsplit(".", 1)[-1] for c in calls if c.startswith("autoresearch.learning.")]
    assert "shadow_buys" in learning_calls and "paper_nav" in learning_calls
    assert learning_calls.index("shadow_buys") < learning_calls.index("paper_nav"), (
        "shadow_buys(生产者)必须先于 paper_nav(唯一消费者)跑,否则当晚发布的信号"
        "要等下一晚才会被影子线看到")


def test_ledgers_step_full_chain_all_modules_ok(monkeypatch):
    """mock 全链跑一遍:学习侧 19 个 + scan 侧 2 个(structural_audit/l2_slo)= 21 个模块
    全部 mock 成功 → 汇总行必须是 21/21。

    沿革:19/19 → 20/20(Wave12-T11 补 `shadow_buys`)→ 21/21(Wave12-T24 补
    `relative_ledger`)。**这个数字是个锁,不是个常数**:`_ledgers()` 的 names 是嵌套函数
    局部变量、不对外暴露,往表里加名字/删名字都不会有任何静态报错;这条断言逼着每一次
    改表的人来这里把数改对(顺便复核自己排的位置),这正是它存在的理由。
    """
    _mk_ledgers_noop_chain(monkeypatch)
    monkeypatch.setattr("importlib.import_module", lambda name: type(
        "M", (), {"main": staticmethod(lambda *a: None)})())

    res = {r[0]: r[2] for r in N.run("2026-07-28")}

    assert res["ledgers"] == "21/21 刷新"


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


# ── Wave12-T35:news_flash 夜间腿 ──


def test_news_flash_step_reports_counts(monkeypatch):
    """接线 + 记账:步骤跑通时把新增/未变/累计如实写进 note(不是只回一个 ✓)。"""
    monkeypatch.setattr("autoresearch.learning.retro.pending_days", lambda *a, **k: [])
    monkeypatch.setattr("autoresearch.learning.t1_review.pending_pairs", lambda *a, **k: [])
    monkeypatch.setattr("autoresearch.learning.t1_review.gap_finalize_pending",
                        lambda *a, **k: (0, []))
    monkeypatch.setattr("autoresearch.learning.tripwire_watch.check", lambda *a, **k: [])
    monkeypatch.setattr("importlib.import_module", lambda name: type(
        "M", (), {"main": staticmethod(lambda *a: None)})())
    note = {r[0]: r[2] for r in N.run("2026-07-28")}["news_flash"]
    assert "新增 2" in note and "累计 2" in note and "global_em:OK" in note


def test_news_flash_step_fails_loudly_when_all_sources_down(monkeypatch):
    """三源全挂 → 本步记 ✗(而不是"跑了但 0 条"的静默绿),但**不连坐**其它步骤。

    「降级不留痕」才是真病 —— 一个恒绿的 ingest 腿和一个死掉的 ingest 腿长得一样。
    """
    monkeypatch.setattr("autoresearch.learning.retro.pending_days", lambda *a, **k: [])
    monkeypatch.setattr("autoresearch.learning.t1_review.pending_pairs", lambda *a, **k: [])
    monkeypatch.setattr("autoresearch.learning.t1_review.gap_finalize_pending",
                        lambda *a, **k: (0, []))
    monkeypatch.setattr("autoresearch.learning.tripwire_watch.check", lambda *a, **k: [])
    # ⚠️ 顺序有意义:pytest 的 `monkeypatch.setattr("a.b.c", ...)` 内部要靠
    # `importlib.import_module` 解析模块路径 —— 先把 importlib 打成桩,这行就会去
    # 假模块上找 `news` 属性而报 AttributeError(首版实测撞到)。
    monkeypatch.setattr("autoresearch.news.catalog.ingest_flash", lambda *a, **k: {
        "per_source": [{"source": s, "status": "FETCH_FAILED", "rows": 0}
                       for s in ("global_em", "global_sina", "cjzc_em")],
        "added": 0, "revised": 0, "unchanged": 0, "rejected": [],
        "any_ok": False, "health": {"n_observations": 0}})
    monkeypatch.setattr("importlib.import_module", lambda name: type(
        "M", (), {"main": staticmethod(lambda *a: None)})())

    res = N.run("2026-07-28")
    by = {r[0]: r for r in res}
    assert by["news_flash"][1] is False and "全部未出数" in by["news_flash"][2]
    assert all(r[1] for r in res if r[0] != "news_flash"), "不连坐"
