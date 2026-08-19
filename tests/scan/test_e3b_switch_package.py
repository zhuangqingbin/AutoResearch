"""E3b 消费侧切换包(task-2.4):三个跑在 writer-1 **之前**的消费点怎么不采信过期文件。

design: `docs/specs/2026-08-18-e6-activation-learning-slimdown-design.md` §3 E3b;
病灶取证: `docs/research/2026-08-19-decision-file-two-writers-and-taskbook-hash.md` §2。

本文件锁三件事,顺序即重要性:

1. **shadow parity 是硬要求**:`mode != "active"` 时每一处的行为与切换包落地**之前**逐字
   相同。每个改动点都有一条 parity 回归(标 `_shadow_`)。
2. **过期文件不得被采信**:scan 目录里躺着**前一日**的决策文件 → active 跑 `build_summary`
   → 渲染出自证占位符 / 显式回退标记,**不是**前一日的 BUY。
3. **注入断链立刻可见**:跳过 `safe_publish` → summary 里留着「看到本行说明注入未跑」。
   断言用的是**字面量**而不是模块常量 —— 把常量改成空串时 `"" in text` 恒真,那种探针
   永远不会红(「绿灯不等于有灯」同族)。

测试产物一律落 `tmp_path`。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoresearch.scan import report_sections as rs
from autoresearch.scan.assemble import build_summary
from autoresearch.scan.relative_buy import DECISION_FILENAME

DATE = "2026-08-19"
PREV = "2026-08-18"
_CARD = ("# 决策卡\n## 决策仪表盘\n| 评级 | 现价 | EV目标 | R:R | 置信度 |\n|---|---|---|---|---|\n"
         "| **{rating}** | 100元 | 130元(+30%) | 2.1:1 | 中 |\n\n**Rating**: {rating}\n\n"
         "FINAL TRANSACTION PROPOSAL: **{prop}**\n")


def _scan_dir(tmp_path: Path, *, regime: str = "range") -> Path:
    scan = tmp_path / "scan" / DATE
    (scan / "details").mkdir(parents=True)
    (scan / "meta.json").write_text(json.dumps({"regime": regime}), encoding="utf-8")
    (scan / "finalists.csv").write_text(
        "ticker,code,name,sector,lane\n"
        "600000,600000,甲,银行,healthy\n"
        "688766,688766,乙,半导体,healthy\n", encoding="utf-8")
    for code, rating, prop in (("600000", "Overweight", "BUY"), ("688766", "Hold", "HOLD")):
        (scan / "details" / f"{code}.md").write_text(
            _CARD.format(rating=rating, prop=prop), encoding="utf-8")
    return scan


def _decision(scan: Path, *, date: str, buys: list[str], blocked: bool = False) -> None:
    (scan / DECISION_FILENAME).write_text(json.dumps({
        "schema_version": 1, "rule_version": "e6.v2.0", "mode": "active", "date": date,
        "buys": [{"code": c, "basis": "relative", "rank": i + 1} for i, c in enumerate(buys)],
        "blocked": blocked, "blocked_reasons": [], "candidates": [], "counts": {},
    }, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def activate(tmp_path, monkeypatch):
    """把生产开关翻到 active(**只在本进程的临时 config 里**,不碰仓库那份)。"""
    cfg = tmp_path / "scan_config.jsonc"
    cfg.write_text(json.dumps({"relative_buy": {"mode": "active"}}), encoding="utf-8")
    monkeypatch.setattr("autoresearch.scan.user_config.DEFAULT_PATH", cfg)


# ── 1. shadow parity(改动点逐个回归)────────────────────────────────────────────


def test_shadow_summary_renders_the_legacy_lines_and_no_markers(tmp_path):
    """影子期:组合视角/仓位 overlay 仍是就地渲染的旧口径,报告里**没有**任何新 managed 标记。

    变异校验:把 `build_summary` 里的 `active = is_active()` 写死成 True,本条立刻变红。
    """
    scan = _scan_dir(tmp_path)
    md = build_summary(scan, DATE, "1200", "20260819_1200")

    assert "买入/超配 **1** 只" in md                    # 旧绝对门口径(600000 是 OW)
    assert "3–5 成" in md and "1 只买单在区间内" in md    # 旧 overlay 尾巴
    assert rs.PORTFOLIO_START not in md
    assert rs.OVERLAY_START not in md
    assert "看到本行说明注入未跑,读 `brief.md` ③" not in md


def test_shadow_summary_ignores_a_present_decision_file(tmp_path):
    """影子期即使盘上有当日决策文件,组合视角也不读它 —— parity 的另一面(别提前生效)。"""
    scan = _scan_dir(tmp_path)
    _decision(scan, date=DATE, buys=["688766"])

    md = build_summary(scan, DATE, "1200", "20260819_1200")

    assert "买入/超配 **1** 只" in md
    assert rs.PORTFOLIO_START not in md


def test_shadow_banner_flow_buys_n_is_the_rating_count(tmp_path, monkeypatch):
    """影子期 `flow.buys_n` 仍是 ≥OW 张数(逐字不变)。"""
    seen: dict = {}
    import autoresearch.learning.self_review as sr

    def _capture(ctx):
        seen.update(ctx)
        return {"ok": True, "n_fail": 0, "n_warn": 0, "failures": []}

    monkeypatch.setattr(sr, "review", _capture)
    scan = _scan_dir(tmp_path)
    rs._self_review_banner(scan, [{"code": "600000", "rating": "Overweight"}], "body")

    assert seen["flow"]["buys_n"] == 1
    assert seen["flow"]["buys_n_source"] == "rating≥OW(legacy 绝对门)"


def test_shadow_injection_is_a_structural_noop(tmp_path):
    """影子期报告没有标记 → 收尾注入连一个字节都改不了(parity 不靠"记得别调用")。"""
    scan = _scan_dir(tmp_path)
    md = build_summary(scan, DATE, "1200", "20260819_1200")

    assert rs.inject_deferred_blocks(md, scan, {"buys": [{"code": "688766"}]}) == md


# ── 2. active:过期文件不得被采信(E3b 验收 ①)─────────────────────────────────


def test_active_build_summary_never_trusts_a_stale_decision_file(tmp_path, activate):
    """**本 task 的核心验收**:scan 目录里放着前一日的决策文件 → active 跑 build_summary。

    三个断言分别对应三种错法:①采信了昨天的 BUY;②回退成旧 ≥OW 计数冒充买单数;
    ③什么都不说(静默)。正解只有一个:自证占位符。
    """
    scan = _scan_dir(tmp_path)
    _decision(scan, date=PREV, buys=["688766"])          # ← 前一日的文件躺在今天的目录里

    md = build_summary(scan, DATE, "1200", "20260819_1200")

    assert "688766" not in md.split("### 组合视角")[1].split("##")[0]   # ① 不采信昨天的 BUY
    assert "买入/超配 **1** 只" not in md                               # ② 不冒充
    assert "看到本行说明注入未跑,读 `brief.md` ③" in md                # ③ 自证占位符在场
    assert rs.PORTFOLIO_START in md and rs.OVERLAY_START in md


def test_active_banner_marks_buys_n_as_deferred(tmp_path, activate, monkeypatch):
    """active 期 `flow.buys_n` 不再拿 ≥OW 张数冒充买单数,而是 None + 显式来源标记。"""
    seen: dict = {}
    import autoresearch.learning.self_review as sr

    def _capture(ctx):
        seen.update(ctx)
        return {"ok": True, "n_fail": 0, "n_warn": 0, "failures": []}

    monkeypatch.setattr(sr, "review", _capture)
    scan = _scan_dir(tmp_path)
    _decision(scan, date=PREV, buys=["688766"])
    rs._self_review_banner(scan, [{"code": "600000", "rating": "Overweight"}], "body")

    assert seen["flow"]["buys_n"] is None
    assert "deferred" in seen["flow"]["buys_n_source"]


def test_active_overlay_placeholder_disappears_without_regime(tmp_path, activate):
    """缺 regime 时旧行为是整行消失 —— active 期也不许多出一个永远填不满的空块。"""
    scan = _scan_dir(tmp_path, regime="")

    md = build_summary(scan, DATE, "1200", "20260819_1200")

    assert rs.OVERLAY_START not in md
    assert rs.PORTFOLIO_START in md                       # 组合视角与 regime 无关,照常落占位


# ── 3. active:收尾注入把两块填对 ────────────────────────────────────────────────


def test_injection_fills_both_blocks_from_the_decision_file(tmp_path, activate):
    scan = _scan_dir(tmp_path)
    md = build_summary(scan, DATE, "1200", "20260819_1200")
    _decision(scan, date=DATE, buys=["688766"])           # writer-1 之后:当日文件在盘

    out = rs.inject_deferred_blocks(md, scan, rs_load(scan))

    assert "BUY(相对决策层) **1** 只" in out
    assert "1 只买单在区间内" in out
    # 两块的自证占位都被填掉了(🧭 仪表盘那句是另一个块,本测试不注它,故按 ③ 后缀区分)
    assert "看到本行说明注入未跑,读 `brief.md` ③" not in out
    assert "_relative_buy_decision.json" in out           # 口径自报


def test_injection_renders_blocked_honestly(tmp_path, activate):
    scan = _scan_dir(tmp_path)
    md = build_summary(scan, DATE, "1200", "20260819_1200")
    _decision(scan, date=DATE, buys=[], blocked=True)

    out = rs.inject_deferred_blocks(md, scan, rs_load(scan))

    assert "BUY(相对决策层) **0** 只" in out
    assert "🛑 当日 BLOCKED" in out
    assert "今日 **BLOCKED**" in out
    assert "今日 0 买 → 空仓" not in out                  # BLOCKED ≠ 择时空仓


def test_injection_marks_an_explicit_fallback_when_the_file_is_stale(tmp_path, activate):
    """注入时刻文件仍然过期(writer-1 没跑)→ 显式「不可用」,**不回退到旧 ≥OW 计数**。"""
    scan = _scan_dir(tmp_path)
    md = build_summary(scan, DATE, "1200", "20260819_1200")
    _decision(scan, date=PREV, buys=["688766"])

    out = rs.inject_deferred_blocks(md, scan, rs_load(scan))

    assert "BUY 数不可用" in out
    assert "买入/超配" not in out
    assert "688766" not in out.split("### 组合视角")[1].split("##")[0]


# ── 4. 注入断链探针(E3b 验收 ②)────────────────────────────────────────────────


def test_injection_gap_leaves_a_self_evident_placeholder(tmp_path, activate):
    """人为跳过收尾注入(= `safe_publish` 断链)→ summary 里必须留下自证文案。

    **变异校验**:把 `report_sections.portfolio_placeholder` / `overlay_placeholder` 的
    自证文案改成空字符串,本条必须变红 —— 所以这里断言的是**字面量**,不是模块常量
    (`"" in text` 恒真,拿常量断言的探针改坏了也永远绿)。
    """
    scan = _scan_dir(tmp_path)
    _decision(scan, date=DATE, buys=["688766"])
    md = build_summary(scan, DATE, "1200", "20260819_1200")   # ← 之后**不**跑 safe_publish

    assert md.count("看到本行说明注入未跑,读 `brief.md` ③。_") == 2   # 两个块各一句
    assert "BUY(相对决策层)" not in md
    assert "1 只买单在区间内" not in md


def rs_load(scan: Path):
    from autoresearch.scan.relative_buy import load_decision
    return load_decision(scan)


# ── 5. health.count_buys(E3b 裁定 3)─────────────────────────────────────────


def _final_ratings(scan: Path) -> None:
    """build_summary 会落这份;单测 count_buys 时直接写,免得跑整个 assemble。"""
    (scan / "_final_ratings.json").write_text(
        json.dumps({"600000": "Overweight", "688766": "Hold"}), encoding="utf-8")


def test_shadow_count_buys_is_the_rating_count_without_a_source_key(tmp_path):
    """影子期:≥OW 计数(现行为)+ run_health 里**没有** buys_source 键(逐字节不变)。"""
    from autoresearch.scan.health import count_buys, count_buys_with_source, run_health

    scan = _scan_dir(tmp_path)
    _final_ratings(scan)
    _decision(scan, date=DATE, buys=["688766"])

    assert count_buys(scan) == 1
    assert count_buys_with_source(scan) == (1, None)
    assert "buys_source" not in run_health(scan)["counts"]


def test_active_count_buys_reads_the_decision_file(tmp_path, activate):
    from autoresearch.scan.health import BUYS_SOURCE_DECISION, count_buys_with_source, run_health

    scan = _scan_dir(tmp_path)
    _final_ratings(scan)                                  # ≥OW 是 1 只(600000)
    _decision(scan, date=DATE, buys=["688766", "600000"])  # 决策文件是 2 只 —— 两数必须分得开

    assert count_buys_with_source(scan) == (2, BUYS_SOURCE_DECISION)
    assert run_health(scan)["counts"]["buys_source"] == BUYS_SOURCE_DECISION


def test_active_count_buys_falls_back_loudly_on_a_stale_file(tmp_path, activate):
    """E3b 验收 ①(count_buys 侧):前一日的文件不得被采信,回退必须**留标记**。"""
    from autoresearch.scan.health import BUYS_SOURCE_FALLBACK, count_buys_with_source, run_health

    scan = _scan_dir(tmp_path)
    _final_ratings(scan)
    _decision(scan, date=PREV, buys=["688766", "600000"])   # 昨天的 2 只 BUY

    n, source = count_buys_with_source(scan)

    assert (n, source) == (1, BUYS_SOURCE_FALLBACK)         # 1 = ≥OW 回退,不是昨天的 2
    assert run_health(scan)["counts"]["buys_source"] == BUYS_SOURCE_FALLBACK


def test_active_count_buys_marks_fallback_when_the_file_is_absent(tmp_path, activate):
    """决策文件缺席(writer-1 之前的早期快照就是这个形态)→ 同样是**带标记**的回退。"""
    from autoresearch.scan.health import BUYS_SOURCE_FALLBACK, count_buys_with_source

    scan = _scan_dir(tmp_path)
    _final_ratings(scan)

    assert count_buys_with_source(scan) == (1, BUYS_SOURCE_FALLBACK)


# ── 6. 接线回归(FN-1 家训:生产者没接线 = 特性只活在单测里)────────────────────


def test_safe_publish_is_the_real_injector(tmp_path, activate):
    """真调用链:`brief.safe_publish`(publisher.py:387 那一句)必须**真的**回填两个块。

    只测 `inject_deferred_blocks` 等于测一个可能没人调用的函数(「拆半个特性没人接线」
    同族)——这条锁的是接线本身。变异校验:把 `safe_publish` 里那句
    `inject_deferred_blocks(...)` 删掉,本条立刻变红。
    """
    from autoresearch.scan import brief

    scan = _scan_dir(tmp_path)
    md = build_summary(scan, DATE, "1200", "20260819_1200")
    out_dir = tmp_path / "report"
    out_dir.mkdir()
    summary = out_dir / "summary.md"
    summary.write_text(md, encoding="utf-8")
    _decision(scan, date=DATE, buys=["688766"])

    brief.safe_publish(scan, out_dir, summary, analysis_date=DATE,
                       run_folder="20260819_1200")

    text = summary.read_text(encoding="utf-8")
    assert "BUY(相对决策层) **1** 只" in text
    assert "看到本行说明注入未跑,读 `brief.md` ③" not in text
