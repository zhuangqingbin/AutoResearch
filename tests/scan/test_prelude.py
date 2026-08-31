"""prelude 编排骨架:单步失败不阻断、结果结构。整链为已测组件的编排,真跑验证。合成。

spec: docs/specs/2026-07-03-scan-run-reliability-design.md §2
"""
from __future__ import annotations

from autoresearch.common import workspace as ws
from autoresearch.scan.prelude import STEP_NAMES
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


#: 跳掉除 temperature 外的全部步骤。**从生产的 `STEP_NAMES` 派生**,不再手抄 ——
#: 手抄清单曾经要在两个测试文件里各维护一份,加一步漏改一份就把「只验缺席」的用例弄红,
#: 而单跑一份测试看不见另一份(记忆:prelude-step-two-skip-lists)。步骤清单本身由
#: `test_step_names_inventory` 显式锁住,派生不会让「悄悄加了一步」变哑。
_SKIP_ALL_BUT_TEMPERATURE = tuple(n for n in STEP_NAMES if n != "temperature")


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
    # 本测试要的是「零步骤」的形状 → 跳全部(从生产清单派生,加步骤不必改这里)
    results = run_prelude("2026-07-28", skip=STEP_NAMES)
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


def test_step_names_inventory():
    """步骤清单显式锁 —— skip 清单改成派生之后,这里是「悄悄加/删了一步」的唯一哨兵。

    加步骤是合法动作,但必须**在这里露面**(改这一行 = 声明「我知道我在改 prelude 的
    步骤集」),否则派生就把「忘了同步」和「偷偷改了」一起变哑了。
    """
    assert STEP_NAMES == (
        "consensus", "temperature", "universe", "calendar", "catalyst", "menu",
        # 2026-08-28 §2.4 G2+G3:`ledger_views` 物化运行日历/市场行/逐级 KPI,
        # 必须排在 `outcome_fill` 之后(它们是结果账本的下游)。
        "l4_rejection", "outcome_fill", "ledger_views", "dossier_pool", "news_catalog",
        # 2026-08-29 D-2:`overseas` 落隔夜窗海外事件日历。**风险可见性,不喂判断层** ——
        # 只进 summary 📅 / brief ⑤ / 📌 哨兵三个展示点。
        "overseas",
    )


def test_every_step_name_has_an_impl(tmp_path, monkeypatch):
    """`STEP_NAMES` 里有名字却没实现 → `run_prelude` 当场 KeyError(而不是静默少跑一步)。
    这条用例正着证明:全跳时不炸,说明名字与实现是配齐的映射。"""
    from autoresearch.scan.prelude import run_prelude
    monkeypatch.chdir(tmp_path)
    assert run_prelude("2026-07-28", skip=STEP_NAMES) == []


def test_outcome_fill_step_runs_and_is_skippable(tmp_path, monkeypatch, capsys):
    """结果账本步骤(2026-08-26 §4.4):跑得起来、可跳、且**不写当日 staging 的决策面**。"""
    from autoresearch.scan import outcome as _outcome
    from autoresearch.scan.prelude import run_prelude

    monkeypatch.chdir(tmp_path)
    calls = []
    monkeypatch.setattr(_outcome, "fill",
                        lambda **kw: calls.append(kw) or {"filled": 2, "skipped": 1, "rows": 7,
                                                          "runs": ["r1", "r2"]})
    monkeypatch.setattr(_outcome, "ledger_line", lambda *a, **k: "结果账本:攒样本 2/20")
    only = tuple(n for n in STEP_NAMES if n != "outcome_fill")
    res = run_prelude("2026-07-28", skip=only)
    assert [r["step"] for r in res] == ["outcome_fill"]
    assert res[0]["ok"] and "回填 2 run" in res[0]["note"]
    assert calls == [{"now": "2026-07-28"}]
    assert run_prelude("2026-07-28", skip=STEP_NAMES) == []      # 可跳
    capsys.readouterr()
