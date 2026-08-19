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


@pytest.fixture(autouse=True)
def _isolate_experiment_stores(monkeypatch, tmp_path):
    """禁写真 registry(Wave12-T20 修复轮 1):`exp_observe` 步接线后,任何跑 `N.run` 的
    测试都会真的去 append 生产 `context/learning/experiments/registry.json`。

    `tests/learning/conftest.py` 的护栏只挡 `reports/` 与 `context/scan/`,**不挡
    `context/learning/`** —— 所以这层必须自己补。手法与 `_no_live_news` 同款:把模块级
    默认路径重定向到 tmp,而不是 mock 掉函数本身 —— 这样下面那条"计数会随夜跑增长"的探针
    仍然驱动**真代码路径**,只是把储存换到 tmp。
    """
    import autoresearch.learning.experiment_registry as R
    import autoresearch.learning.mainflow5d as MF

    iso = tmp_path / "_iso"
    monkeypatch.setattr(R, "DEFAULT_REGISTRY", iso / "registry.json")
    monkeypatch.setattr(MF, "DEFAULT_POPULATION", iso / "gate_participation_v3.csv")
    monkeypatch.setattr(MF, "DEFAULT_LEDGER", iso / "exp1_mainflow5d.jsonl")
    monkeypatch.setattr(MF, "DEFAULT_SCAN_ROOT", iso / "scan")
    monkeypatch.setattr(MF, "DEFAULT_LAKE", iso / "lake" / "moneyflow")
    return iso


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
                                    "tripwire", "ledgers", "exp_observe", "news_flash"]
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


# ══════════════════════════════════════════════════════════════════
# Wave12-T20 修复轮 1(复核 C1):EXP-1/EXP-2 观测的**夜间腿**
#
# 病灶:`observe_day` 写好了却零调用点 —— 20 条观测是一次性手工回填、**永不增长**;
# 而回填还抹掉了 `observations == []` 这个本该暴露 FN-1 的信号,比原病更隐蔽。
#
# ⚠️ 本节探针**刻意不**断言"函数存在 / 被调用过" —— 那种断言在腿死掉时照样绿
# (家训:自动腿必须有一个**会变的量**做断言,否则它死了也像活着)。这里驱动**真的**
# `nightly_close.run()`,连跑两晚,断言观测计数**真的从 0 长到 1 再长到 2**。
# ══════════════════════════════════════════════════════════════════

import pandas as pd  # noqa: E402

from autoresearch.learning import (  # noqa: E402
    experiment_registry as R,
    mainflow5d as MF,
)

_LAKE_DAYS = ["20260728", "20260729", "20260730", "20260731", "20260803",
              "20260804", "20260805"]


def _seed_world(iso, *, population_dates):
    """tmp 世界:7 天 moneyflow 湖 + 人口文件 + 当日 L1 帧 + 两条 PREREGISTERED 实验。"""
    lake = iso / "lake" / "moneyflow"
    lake.mkdir(parents=True, exist_ok=True)
    for day in _LAKE_DAYS:
        pd.DataFrame({
            "ts_code": ["000001.SZ"], "buy_lg_amount": [1e4], "sell_lg_amount": [0.0],
            "buy_elg_amount": [0.0], "sell_elg_amount": [0.0],
            "buy_sm_amount": [0.0], "sell_sm_amount": [0.0], "net_mf_amount": [1e4],
        }).to_parquet(lake / f"{day}.parquet", index=False)

    (iso / "gate_participation_v3.csv").parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([{"cohort_version": "v3", "date": d, "gate": "主力真在",
                   "code": "000001", "ruler": "gap_c1_o2"} for d in population_dates]
                 ).to_csv(iso / "gate_participation_v3.csv", index=False)

    for d in population_dates:
        day = iso / "scan" / d
        day.mkdir(parents=True, exist_ok=True)
        pd.DataFrame([{"code": "000001", "main_net_ratio": 0.05,
                       "main_inflow_yi": 1.2}]).to_csv(day / "L1_scored_full.csv",
                                                       index=False)

    reg = iso / "registry.json"
    R.set_stable_baseline(reg, name="b", pointer="p@1", content_hash="0" * 64,
                          approved_by="t", approved_at="2026-08-01T00:00:00+08:00")
    guards = {g: [{"metric": "m", "op": "gt", "value": 0}] for g in R.GUARD_DOMAINS}
    for exp_id in (MF.EXP1_ID, MF.EXP2_ID):
        R.register_experiment(reg, {
            "id": exp_id, "title": exp_id, "trial_family": exp_id,
            "definition": {"challenger": "x"}, "start_date": "2026-08-01",
            "expires_date": "2026-11-30", "primary_metric": "m",
            "promotion_guards": guards, "rollback_guards": guards,
            "challenger_pointer": {"kind": "k", "pointer": "p", "content_hash": "1" * 64},
            "minimums": {"forward_days": 20, "mature_events": 50,
                         "unique_events": 50, "regimes": 2},
            "rollback_window_runs": 5,
        }, registered_at="2026-08-01T00:00:00+08:00")
    return reg


def _quiet_other_steps(monkeypatch):
    """把**除 exp_observe 之外**的步骤打哑:它们会写真 `context/learning/`(护栏不挡那里)。

    `_exp_observe` 用的是 `from ... import mainflow5d`(走 `__import__`),**不经过**
    `importlib.import_module` —— 所以下面这一行打哑 `_ledgers` 的 20 个模块,却拦不住
    本步。这正是要的:被测那一步跑真身,其余全静音。

    ⚠️ stub **必须按模块名挑**,不能一律返回假模块:`observe_pending` 要读 parquet,而
    pandas 的 `import_optional_dependency` 内部也走 `importlib.import_module` 去加载
    pyarrow —— 首版无差别 stub 把 pyarrow 也换成了假模块,真跑当场
    `AttributeError: 'M' object has no attribute '__name__'`。
    """
    import importlib as _il

    real_import = _il.import_module
    monkeypatch.setattr("autoresearch.learning.retro.pending_days", lambda *a, **k: [])
    monkeypatch.setattr("autoresearch.learning.t1_review.pending_pairs", lambda *a, **k: [])
    monkeypatch.setattr("autoresearch.learning.t1_review.gap_finalize_pending",
                        lambda *a, **k: (0, []))
    monkeypatch.setattr("autoresearch.learning.tripwire_watch.check", lambda *a, **k: [])
    monkeypatch.setattr("importlib.import_module", lambda name, *a, **k: (
        type("M", (), {"main": staticmethod(lambda *aa, **kk: None)})()
        if name.startswith(("autoresearch.learning.", "autoresearch.scan."))
        else real_import(name, *a, **k)))


def _count(reg, exp_id=None):
    return len(R.get_experiment(reg, exp_id or MF.EXP1_ID)["observations"])


def test_nightly_run_actually_grows_the_observation_count(_isolate_experiment_stores,
                                                          monkeypatch):
    """**C1 的主探针**:连跑两晚真 `nightly_close.run()`,观测计数必须真的增长。

    这条断言的对象是**会变的量**本身。腿哪天被摘掉(步骤表里删掉 `exp_observe`)、或
    `observe_pending` 退化成 no-op,它立刻变红 —— 而"函数存在"那种断言不会。
    """
    iso = _isolate_experiment_stores
    reg = _seed_world(iso, population_dates=["2026-08-04"])
    _quiet_other_steps(monkeypatch)

    assert _count(reg) == 0, "起点必须是空的 —— 那正是 FN-1 当初唯一的线索"

    # 第一晚
    res1 = dict((n, (ok, note)) for n, ok, note in N.run("2026-08-04"))
    assert res1["exp_observe"][0] is True, res1["exp_observe"][1]
    assert _count(reg) == 1, f"第一晚没长:{res1['exp_observe'][1]}"
    assert _count(reg, MF.EXP2_ID) == 1, "两条实验都要长"

    # 第二晚:人口文件多出一天(现实里由 gate_attribution 在 ledgers 步刷新)
    _seed_world(iso, population_dates=["2026-08-04", "2026-08-05"])
    res2 = dict((n, (ok, note)) for n, ok, note in N.run("2026-08-05"))
    assert res2["exp_observe"][0] is True, res2["exp_observe"][1]
    assert _count(reg) == 2, f"第二晚没长:{res2['exp_observe'][1]}"
    assert _count(reg, MF.EXP2_ID) == 2


def test_nightly_run_is_idempotent_within_the_same_night(_isolate_experiment_stores,
                                                         monkeypatch):
    """同一晚重跑不产生第二条(夜间腿会被 launchd 重试,重复跑必须无害)。"""
    iso = _isolate_experiment_stores
    reg = _seed_world(iso, population_dates=["2026-08-04"])
    _quiet_other_steps(monkeypatch)
    N.run("2026-08-04")
    N.run("2026-08-04")
    assert _count(reg) == 1


def test_nightly_run_self_heals_a_missed_night(_isolate_experiment_stores, monkeypatch):
    """漏跑一晚 → 第二晚把欠的一起补上(同 `_retro_refresh` 的自愈姿势)。"""
    iso = _isolate_experiment_stores
    reg = _seed_world(iso, population_dates=["2026-08-03", "2026-08-04", "2026-08-05"])
    _quiet_other_steps(monkeypatch)
    N.run("2026-08-05")                       # 前两晚都没跑过
    assert _count(reg) == 3


def test_exp_observe_does_not_observe_the_future(_isolate_experiment_stores, monkeypatch):
    """`today` 之后的人口日不得被观测(夜跑当晚不能预支明天的读数)。"""
    iso = _isolate_experiment_stores
    reg = _seed_world(iso, population_dates=["2026-08-04", "2026-08-05"])
    _quiet_other_steps(monkeypatch)
    N.run("2026-08-04")
    assert _count(reg) == 1
    keys = {o["key"] for o in R.get_experiment(reg, MF.EXP1_ID)["observations"]}
    assert keys == {"2026-08-04"}


def test_exp_observe_failure_does_not_block_later_steps(_isolate_experiment_stores,
                                                        monkeypatch):
    """本步炸掉不连坐 `news_flash`(夜间腿的既有纪律)。"""
    _quiet_other_steps(monkeypatch)
    monkeypatch.setattr(MF, "observe_pending",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("湖挂了")))
    res = dict((n, (ok, note)) for n, ok, note in N.run("2026-08-04"))
    assert res["exp_observe"][0] is False and "RuntimeError" in res["exp_observe"][1]
    assert res["news_flash"][0] is True, "一步失败把后续步骤也带崩了 = 连坐"


def test_exp_observe_runs_after_ledgers(_isolate_experiment_stores, monkeypatch):
    """顺序锁:人口文件由 `ledgers` 步里的 `gate_attribution` 刷新 —— 排在它前面,
    今晚新成熟的日子要等明晚才被观测到(同 shadow_buys→paper_nav 的生产者-消费者约束)。"""
    _quiet_other_steps(monkeypatch)
    names = [n for n, _, _ in N.run("2026-08-04")]
    assert names.index("exp_observe") > names.index("ledgers")


def test_exp_observe_note_reports_zero_when_nothing_is_new(_isolate_experiment_stores,
                                                           monkeypatch):
    """汇总行必须如实说「今晚补了 0 日」——**这一行就是人判断腿死活的唯一出口**。

    ⚠️ 变异探针 F3 逼出来的:`observe_pending` 里去掉 `- observed_dates(reg)` 之后,
    因为 `append_observation` 自己幂等,观测计数照样不变、既有测试**全绿** —— 但每晚都会
    把全部历史日重跑一遍,而且汇总行天天写「补 20 日」。计数没错,**播报却在说谎**:
    腿真死了的那天,人看到的仍然是「补 20 日」。
    """
    iso = _isolate_experiment_stores
    _seed_world(iso, population_dates=["2026-08-04"])
    _quiet_other_steps(monkeypatch)

    first = dict((n, note) for n, _, note in N.run("2026-08-04"))["exp_observe"]
    assert "补 1 日" in first, first

    second = dict((n, note) for n, _, note in N.run("2026-08-04"))["exp_observe"]
    assert "补 0 日" in second, f"没有新东西时必须说 0,实为:{second}"
    assert "待观测 0" in second
