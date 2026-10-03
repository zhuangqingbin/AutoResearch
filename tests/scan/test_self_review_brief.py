"""brief 一致性 lint 回归(Wave12 T27 / 批C C3;E4 补第⑥条)。

六条(任务书 Step 1;severity 按 B-2 二分,见 `self_review.BRIEF_LINT_SEVERITY`):
  ① brief 缺失 = **warn**(B-2 降级)
  ② >3,000B = **warn**(B-2 降级)
  ③ brief 数字与 `sources` 边表逐项对账 —— **篡改一个评级/基准读数必红**(变异验收)= fail
  ④ brief 与 summary 的 BUY 数 / code / basis / 基准读数不一致 = fail
  ⑤ **仅 active 模式**:成功 run BUY_n<1 = fail;BLOCKED run 不得渲染成成功。
     影子期(mode=shadow)该检查**跳过并注明**——mode 从 `_relative_buy_decision.json` 读。
  ⑥ ③ 段相对 BUY 与决策文件同源(E4,`docs/specs/2026-08-18-e6-activation-learning-
     slimdown-design.md` §3 E4)—— 08-17 事故:brief 读到了 `_relative_buy_decision.json`
     早 25 秒的半成品,把本该 rank1 的 BUY 印成了 BLOCKED,两层报告一起错、lint 一起绿。
     brief ③ 段渲染出的六位代码集合 == `buys[].code` 集合(含空对空)违反 = fail。
     **不做 mtime 顺序检查**(2026-08-19 复核 Critical 修复轮 1 删除):决策文件有两个
     合法写者(`publisher._run_publish` 在 brief 之前;`post_run observe` 在 brief 之后
     无条件重写),mtime 顺序在健康日也会颠倒 —— 08-17 真实归档实证(22:15:36 vs
     22:15:33、内容同源)。

零网络;所有产物写 tmp_path(`conftest._forbid_production_report_writes` 护栏下必须通过)。
"""
from __future__ import annotations

import csv
import json
import os
from pathlib import Path

import pytest

from autoresearch.scan import brief, self_review
from autoresearch.scan.decision_record import DecisionRecord, write_decision_records
from autoresearch.scan.report_sections import inject_dashboard

_DATE = "2026-08-06"
_RUN = "20260806_2308"


def _decision(*, mode="shadow", blocked=False, buys=1) -> dict:
    cands = [{"code": "600018", "name": "上港集团", "sector": "航运港口", "pinned": False,
              "eligible": True, "rank": 1, "relative_decision_score": 0.795,
              "faces": {"target_align": 0.77, "recall_strength": 0.77,
                        "evidence": 0.77, "risk_safety": 0.86},
              "faces_missing": [], "research_rating": "Hold",
              "hard_gate": {"tradable": True, "data_a": True, "contract": True,
                            "no_redflag": True},
              "expected_abs_gap": {"value": None, "status": "UNMEASURED", "n": 0}}]
    rows = ([{"code": "600018", "basis": "relative", "rank": 1}]
            if (buys and not blocked) else [])
    return {"schema_version": 1, "rule_version": "e6.v1", "mode": mode, "date": _DATE,
            "ruler": "gap_c1_o2",
            "benchmark": {"market": {"column": "rel_gap_market", "n": 4237},
                          "sector": {"column": "rel_gap_sector", "n_sectors": 129}},
            "counts": {"candidates": 1, "eligible": 1, "excluded_rows": 0,
                       "buys": len(rows)},
            "candidates": cands, "buys": rows,
            "second_buy": {"fired": False, "reason": "v1 影子期无已验证阈值",
                           "threshold": None},
            "blocked": blocked,
            "blocked_reasons": ([{"reason": "hard_gate.no_redflag", "n": 1}]
                                if blocked else []),
            "excluded": []}


def _scan(root: Path, *, decision: dict | None = None) -> Path:
    scan = root / "context" / "scan" / _DATE
    (scan / "details").mkdir(parents=True)
    (scan / "meta.json").write_text(json.dumps({
        "analysis_date": _DATE, "universe_raw": 5496, "universe": 4237,
        "recall_n": 1000, "l2_n": 203, "regime": "range"}), encoding="utf-8")
    with (scan / "finalists.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["code", "name", "sector", "lane"])
        w.writeheader()
        w.writerow({"code": "600018", "name": "上港集团", "sector": "航运港口", "lane": ""})
        w.writerow({"code": "601688", "name": "华泰证券", "sector": "证券Ⅱ", "lane": "pinned"})
    ratings = {"600018": "Hold", "601688": "Hold"}
    (scan / "_final_ratings.json").write_text(json.dumps(ratings, ensure_ascii=False),
                                              encoding="utf-8")
    write_decision_records(scan, [
        DecisionRecord.build(
            analysis_date=_DATE, contract_hash=None, code=c, source_rating=r,
            rubric_rating=r, gate_states={"主力真在": "FAIL"}, early_stop=None,
            ensemble_ratings=[], final_rating=r, proposal="HOLD", reason="synthetic",
            evidence_refs=[], first_rejection_stage=None)
        for c, r in sorted(ratings.items())])
    (scan / "run_mode.json").write_text(json.dumps({"mode": "FORCED_FULL"}),
                                        encoding="utf-8")
    (scan / "run_health.json").write_text(json.dumps({
        "counts": {"l1_full": 4237, "recall": 1000, "l2": 203, "finalists": 2,
                   "cards": 2, "buys": 0},
        "churn": {"prev_date": "2026-08-05", "n_today": 2, "n_repeat": 1},
        "degraded_fields": []}, ensure_ascii=False), encoding="utf-8")
    (scan / brief.DECISION_FILENAME).write_text(
        json.dumps(decision or _decision(), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8")
    return scan


def _publish(root: Path, scan: Path) -> Path:
    """生产同姿势:落 brief.md + 把 ①②③④ 注回 summary 的 managed 块。"""
    report = root / "reports" / "scan" / _RUN
    report.mkdir(parents=True, exist_ok=True)
    built = brief.build(scan, run_folder=_RUN)
    brief.write(scan, report, built=built)
    summary = ("# A股扫描 v2 — 2026-08-06\n\n"
               f"{'<!-- SCAN_DASHBOARD_START -->'}\n## 🧭 决策仪表盘\n\n占位\n"
               f"{'<!-- SCAN_DASHBOARD_END -->'}\n\n## 3. 投资建议\n\n## 诚实局限\n")
    (report / "summary.md").write_text(
        inject_dashboard(summary, brief.dashboard_block(built)), encoding="utf-8")
    return report


@pytest.fixture
def published(tmp_path):
    scan = _scan(tmp_path)
    return _publish(tmp_path, scan), scan


@pytest.fixture(autouse=True)
def _isolate_ledger_root(monkeypatch):
    """Task 4 fix round 1:`_publish()` above calls `brief.build` directly, and
    `brief.collect_facts` unconditionally calls `_e6_realized_stats()` →
    `outcome.load_ledger(None)` → `ws.reports_root()/scan/_ledger/recommendations.csv`.
    That CSV is a real, live project artifact (gitignored, grows with every published
    scan) — without isolation, every one of the ~30 tests sharing the `published`
    fixture would render whatever the dev machine's ledger says *today*, not the
    synthetic decision fixture this file actually controls.

    Same reasoning, same fix as `tests/scan/test_brief.py::_isolate_ledger_root` /
    `tests/scan/test_publisher_artifact_map.py::_isolate_ledger_root` — **file-scoped
    autouse, not a shared `conftest.py` fixture**: `ws.reports_root()` is used
    elsewhere in `tests/scan/` as a composable *relative* path fragment
    (`tmp_path / ws.reports_root() / ...`, e.g. `test_retention.py`), and a global
    absolute-nonexistent override broke that composition once already. Scoping this
    fixture to one file's own tests avoids that blast radius entirely. A test that
    wants real ledger content may still monkeypatch `ws.reports_root` itself inside
    the test body (executes after fixture setup, so it wins)."""
    from autoresearch.common import workspace as ws
    monkeypatch.setattr(ws, "reports_root", lambda: Path("/nonexistent/tests-no-real-ledger"))


def _checks(rows) -> set[str]:
    return {r["check"] for r in rows}


def _fails(rows) -> list[dict]:
    return [r for r in rows if r["severity"] == "fail"]


# ───────────────────────────── 干净盘 ─────────────────────────────

def test_clean_publish_has_no_fail(published):
    rows = self_review.brief_lint(*published)
    assert not _fails(rows), f"干净盘不该 fail:{_fails(rows)}"


def test_shadow_mode_is_explicitly_noted_not_silently_skipped(published):
    """⑤ 影子期跳过 BUY≥1 契约检查,但**必须留痕**——静默跳过 = 死了也像活着(FN-1)。"""
    rows = self_review.brief_lint(*published)
    note = [r for r in rows if r["check"] == "brief·BUY契约(active 期)"]
    assert len(note) == 1
    assert note[0]["severity"] == "info"
    assert "shadow" in note[0]["detail"] and "跳过" in note[0]["detail"]


# ───────────────────────────── ① 缺失 / ② 超预算 ─────────────────────────────

def test_missing_brief_is_warn_not_fail(published):
    """B-2:brief 没落盘 = 报告**畸形**,不是报告**说假话** —— 报出来但不毙掉整条流水线。"""
    report, scan = published
    (report / brief.BRIEF_FILENAME).unlink()
    rows = self_review.brief_lint(report, scan)
    hit = [r for r in rows if r["check"] == "brief·缺失"]
    assert hit and hit[0]["severity"] == "warn"
    assert not _fails(rows)


def test_oversize_brief_is_warn_not_fail(published):
    """B-2:排版超限是**展示层**问题。「GATE3 差 16 字节毙 60min 流水线」同族,不再复发。"""
    report, scan = published
    path = report / brief.BRIEF_FILENAME
    path.write_text(path.read_text(encoding="utf-8") + "填" * 3000, encoding="utf-8")
    rows = self_review.brief_lint(report, scan)
    hit = [r for r in rows if r["check"] == "brief·超预算"]
    assert hit and hit[0]["severity"] == "warn"
    assert "3000" in hit[0]["detail"]


# ───────────────────────────── ③ sources 逐项对账(变异验收) ─────────────────────────────

def test_tampered_rating_in_brief_turns_red(published):
    """**变异验收**:把 brief 里一个评级从 Hold 改成 Buy → 对账必红。"""
    report, scan = published
    path = report / brief.BRIEF_FILENAME
    path.write_text(path.read_text(encoding="utf-8")
                    .replace("华泰证券 601688 **Hold**", "华泰证券 601688 **Buy**"),
                    encoding="utf-8")
    rows = self_review.brief_lint(report, scan)
    hit = [r for r in rows if r["check"] == "brief·数字对账"]
    assert hit, "篡改评级后 sources 对账没有报警 —— 这条 lint 是零鉴别力的"
    assert hit[0]["severity"] == "fail"
    assert "pinned.601688" in " ".join(r["detail"] for r in hit)


def test_tampered_benchmark_reading_turns_red(published):
    report, scan = published
    path = report / brief.BRIEF_FILENAME
    path.write_text(path.read_text(encoding="utf-8").replace("4237", "9999"),
                    encoding="utf-8")
    rows = self_review.brief_lint(report, scan)
    assert "brief·数字对账" in _checks(rows)
    assert _fails(rows)


def test_stale_sources_table_is_reported_as_warn(published):
    """边表自己过期(输入变了但没重生成)必须**报出来** —— lint 真**重算**一次,不只对自己。
    B-2 把它降为 warn:边表是对账夹具、不是报告陈述,它坏了不等于 brief 在说假话。"""
    report, scan = published
    payload = json.loads((scan / brief.SOURCES_FILENAME).read_text(encoding="utf-8"))
    payload["rows"][0]["value"] = "篡改过的值"
    (scan / brief.SOURCES_FILENAME).write_text(json.dumps(payload, ensure_ascii=False),
                                               encoding="utf-8")
    rows = self_review.brief_lint(report, scan)
    hit = [r for r in rows if r["check"] == "brief·边表过期"]
    assert hit and hit[0]["severity"] == "warn"
    assert not _fails(rows)


def test_missing_sources_table_is_warn(published):
    report, scan = published
    (scan / brief.SOURCES_FILENAME).unlink()
    rows = self_review.brief_lint(report, scan)
    hit = [r for r in rows if r["check"] == "brief·边表缺失"]
    assert hit and hit[0]["severity"] == "warn"
    assert not _fails(rows)


def test_source_outside_whitelist_is_fail(published):
    report, scan = published
    payload = json.loads((scan / brief.SOURCES_FILENAME).read_text(encoding="utf-8"))
    payload["rows"][0]["file"] = "details/600018.md"
    (scan / brief.SOURCES_FILENAME).write_text(json.dumps(payload, ensure_ascii=False),
                                               encoding="utf-8")
    rows = self_review.brief_lint(report, scan)
    assert "brief·白名单外取数" in _checks(rows)


# ───────────────────────────── ④ brief ↔ summary ─────────────────────────────

def test_summary_not_carrying_dashboard_is_fail(published):
    """注入没跑(summary 还留占位)→ 两层报告说的不是同一件事。"""
    report, scan = published
    summary = report / "summary.md"
    summary.write_text(summary.read_text(encoding="utf-8")
                       .replace("生产 BUY 0 只", "生产 BUY 3 只"), encoding="utf-8")
    rows = self_review.brief_lint(report, scan)
    hit = [r for r in rows if r["check"] == "brief↔summary不一致"]
    assert hit and hit[0]["severity"] == "fail"
    assert "buys.production_n" in hit[0]["detail"]


def test_summary_missing_relative_code_is_fail(published):
    report, scan = published
    summary = report / "summary.md"
    summary.write_text(summary.read_text(encoding="utf-8").replace("600018", "600019"),
                       encoding="utf-8")
    rows = self_review.brief_lint(report, scan)
    assert "brief↔summary不一致" in _checks(rows)


def test_missing_summary_is_fail(published):
    report, scan = published
    (report / "summary.md").unlink()
    rows = self_review.brief_lint(report, scan)
    assert "brief↔summary不一致" in _checks(rows)


# ───────────────────────────── ⑤ active 期 BUY 契约 ─────────────────────────────

def test_active_mode_zero_buy_is_fail(tmp_path):
    scan = _scan(tmp_path, decision=_decision(mode="active", buys=0))
    report = _publish(tmp_path, scan)
    rows = self_review.brief_lint(report, scan)
    hit = [r for r in rows if r["check"] == "brief·BUY契约(active 期)"]
    assert hit and hit[0]["severity"] == "fail"
    assert "BUY_n" in hit[0]["detail"]


def test_active_mode_blocked_must_not_read_as_success(tmp_path):
    scan = _scan(tmp_path, decision=_decision(mode="active", blocked=True))
    report = _publish(tmp_path, scan)
    path = report / brief.BRIEF_FILENAME
    path.write_text(path.read_text(encoding="utf-8").replace("BLOCKED", "一切正常"),
                    encoding="utf-8")
    rows = self_review.brief_lint(report, scan)
    hit = [r for r in rows if r["check"] == "brief·BUY契约(active 期)"]
    assert hit and any(r["severity"] == "fail" for r in hit)
    assert any("BLOCKED" in r["detail"] for r in hit)


def test_active_mode_with_one_buy_is_clean(tmp_path):
    scan = _scan(tmp_path, decision=_decision(mode="active", buys=1))
    report = _publish(tmp_path, scan)
    rows = self_review.brief_lint(report, scan)
    assert not _fails(rows), f"active + 1 只 BUY 不该 fail:{_fails(rows)}"


def test_shadow_zero_buy_is_not_fail(tmp_path):
    """影子期 BLOCKED / 0 BUY **不是** fail —— 那条契约只在 active 生效(任务书 §4)。"""
    scan = _scan(tmp_path, decision=_decision(mode="shadow", blocked=True))
    report = _publish(tmp_path, scan)
    rows = self_review.brief_lint(report, scan)
    assert not _fails(rows), f"影子期不该因 0 BUY 变红:{_fails(rows)}"


# ───── ⑥ ③ 段相对 BUY 与决策文件**内容**同源(E4,08-17 事故:brief 读到半成品决策) ─────
#
# **不设 mtime 顺序判据**(2026-08-19 复核 Critical,修复轮 1 删除):`_relative_buy_decision.json`
# 有两个合法写者 —— `publisher._run_publish`(brief 之前)与 `post_run observe`
# (STAGES 步骤 5,brief 之后无条件重写)。08-17 真实归档:决策文件 mtime 22:15:36 晚于
# brief.md 的 22:15:33,但两边内容同源(000779)—— mtime 顺序在健康日也会颠倒,一条
# 「决策比 brief 新就 fail」的判据在此会对健康 run 误报,比没有这条判据更糟(data 类的存在
# 意义是「说假话才连坐」)。真正要防的「brief 读了半成品」由下面的**内容同源**判据直接抓。

_E6_CHECK = "brief③相对BUY与决策文件不同源"


def test_relative_buy_code_mismatch_is_fail(tmp_path):
    """同源断言:brief ③ 印 600188,决策文件 `buys[0].code` 却是 000001 → fail。"""
    decision = _decision(mode="shadow", blocked=False, buys=1)
    decision["candidates"][0]["code"] = "000001"
    decision["buys"][0]["code"] = "000001"
    scan = _scan(tmp_path, decision=decision)
    report = _publish(tmp_path, scan)
    path = report / brief.BRIEF_FILENAME
    text = path.read_text(encoding="utf-8")
    assert "000001" in text, "锚点没先出现,后面的篡改是空操作"
    path.write_text(text.replace("000001", "600188"), encoding="utf-8")
    rows = self_review.brief_lint(report, scan)
    hit = [r for r in rows if r["check"] == _E6_CHECK]
    assert hit, f"两边代码不同源(600188 vs 000001)没有报警:{rows}"
    assert hit[0]["severity"] == "fail"
    assert "600188" in hit[0]["detail"] and "000001" in hit[0]["detail"]


def test_relative_buy_code_match_is_not_flagged(published):
    """同源断言:两边都是同一个六位代码(干净盘)→ 不生成该 check。"""
    rows = self_review.brief_lint(*published)
    assert _E6_CHECK not in _checks(rows)


def test_relative_buy_blocked_empty_vs_empty_is_not_flagged(tmp_path):
    """空对空:决策 blocked=true/buys=[] ∧ brief 印 BLOCKED(无六位代码)—— 两边都是空集,
    不该被判「不同源」。"""
    scan = _scan(tmp_path, decision=_decision(mode="shadow", blocked=True))
    report = _publish(tmp_path, scan)
    rows = self_review.brief_lint(report, scan)
    assert _E6_CHECK not in _checks(rows)


def test_relative_buy_decision_has_buy_but_brief_prints_blocked_is_fail(published):
    """08-17 事故的真实形状:决策文件 buys[0] 是真 BUY(600018),但 brief ③ 段被
    (半成品/篡改)渲染成了 BLOCKED,没印出任何六位代码 → fail。"""
    report, scan = published
    path = report / brief.BRIEF_FILENAME
    text = path.read_text(encoding="utf-8")
    # 2026-08-26:③ 行在 `basis=` 与 `合格内` 之间插了 `· 池=…`(v3.0 候选池来源)。
    # 锚点跟着更新 —— 本用例测的是「决策文件有 BUY 而 brief 印 BLOCKED 要 fail」,
    # 不是这句话的措辞。
    # `(N 只)` 后缀是 presence-gated 的:老决策文件没有 `counts.in_pool` → 不渲染
    # (本 fixture 正是老形状)。这也顺带证明 v3 的新键对历史产物是逐字无害的。
    old = ("上港集团 600018 · basis=relative · 池=L3 finalist 全体"
           " · 合格内 #1/1(候选 1) · 卡面 Hold")
    new = "**BLOCKED**(全部候选被硬资格否决:hard_gate.no_redflag×1;候选 1 / 合格 0)"
    assert old in text, "锚点没先出现,后面的篡改是空操作"
    path.write_text(text.replace(old, new), encoding="utf-8")
    rows = self_review.brief_lint(report, scan)
    hit = [r for r in rows if r["check"] == _E6_CHECK]
    assert hit, f"决策文件有真 BUY 但 brief 印成 BLOCKED 没有报警:{rows}"
    assert hit[0]["severity"] == "fail"
    assert "600018" in hit[0]["detail"]


def test_decision_file_newer_than_brief_with_matching_content_is_not_flagged(published):
    """回归锁(2026-08-19 复核 Critical):`post_run observe` 在 brief 之后无条件重写决策
    文件是**健康日的正常序**(STAGES 步骤 5,08-17 真实归档同形)——mtime 颠倒本身不是
    「说假话」,内容依旧同源就不该 fail。这条防的是「将来有人把 mtime 顺序判据补回来」;
    若 self_review.py 重新长出该判据,本用例会先红。"""
    report, scan = published
    brief_path = report / brief.BRIEF_FILENAME
    decision_path = scan / brief.DECISION_FILENAME
    newer = brief_path.stat().st_mtime + 5
    os.utime(decision_path, (newer, newer))
    rows = self_review.brief_lint(report, scan)
    assert _E6_CHECK not in _checks(rows), \
        f"决策文件比 brief 新(内容仍同源)不该 fail —— mtime 顺序判据已被裁定删除:{rows}"


# ───────────────────────────── 容错 ─────────────────────────────

def test_lint_is_wired_into_publisher(tmp_path, capsys, monkeypatch):
    """接线真身断言(FN-1:生产者没接线 = 死码)。走 `assemble.run`,lint 结果必须
    进 `gate_fires.csv`(与 R3 门审计同一本账)并打给 CP7。

    2026-08-19(task-2.5b,E6 转正后实测发现):`assemble.run` 内部经
    `post_run.publish_run_observation`(writer-1 语义)用**生产** `scan_config.jsonc`
    的 `relative_buy` 现算并覆盖这份合成夹具预置的决策文件;`brief.build()` 在覆盖
    **之后**才跑。这份夹具没配 L4 派发护照,active 期现算恒得
    `blocked=true/no_candidates`,brief.md 因此天然渲染成同一个 BLOCKED——两边一致,
    ⑤/⑥两条 lint 检查零发现(不是没接线,是这一天真的没什么可报)。本测试要锁的是
    接线本身,不是某个 mode 下的具体判据结果,故显式钉死 shadow(⑤ 的 info 留痕在
    shadow 期恒无条件触发),与生产 `relative_buy.mode` 当前是 shadow 还是 active 解耦
    (同一手法见 `test_configured_relative_buy_defaults_to_shadow_without_config`)。"""
    from autoresearch.scan import assemble

    monkeypatch.setattr("autoresearch.scan.relative_buy.configured_relative_buy",
                        lambda: ("shadow", False, None))
    scan = _scan(tmp_path)
    assemble.run(_DATE, scan_dir=scan, out_root=tmp_path / "reports" / "scan",
                 hhmm="2308", run_date="2026-08-06")
    assert "[brief lint]" in capsys.readouterr().out, "CP7 拿不到 lint 读数"
    fired = list(csv.DictReader((scan / "gate_fires.csv").open(encoding="utf-8")))
    assert any(str(r.get("check", "")).startswith("brief·") for r in fired), \
        "lint 条目没进 gate_fires.csv —— 跑了等于没跑"


# ═════════ B-2:GATE4 语义二分(2026-08-09 控制方裁定,`BRIEF_LINT_SEVERITY`)═════════
#
# **病灶**:`publisher` 把 brief_lint 的结果 `append_gate_fires` 进 `gate_fires.csv`,而
# `gates.gate4` 的判据是「有任意一行 `severity=="fail"` 就不过」。于是一份人类可读摘要
# **排版超限**就能毙掉整条约 60 分钟的流水线 —— 本仓有「GATE3 差 16 字节毙 60min 流水线」
# 的疤,同一形状。而本波自己两处写着「失败不阻断发布」(`publisher`)、`brief.safe_publish`
# 刻意吞异常:一边为了不阻断而吞,另一边把吞下去的结果变成门失败。
#
# **裁定的分法只有一问:报告是不是在说假话。**
#   fail(硬门必须拦):数字对账 / brief↔summary 不一致 / 白名单外取数 / active 期 BUY 契约
#   warn(不该毁掉一次已跑完的扫描):缺失 / 超预算 / 边表缺失 / 边表过期
#
# 下面把**八条判据逐条**接到真 `gates.gate4` 上验行为——不是验 severity 字符串,而是验
# 「这条触发时那道门到底放不放行」。


def _break_missing_brief(report, scan):
    (report / brief.BRIEF_FILENAME).unlink()


def _break_oversize(report, scan):
    path = report / brief.BRIEF_FILENAME
    path.write_text(path.read_text(encoding="utf-8") + "填" * 3000, encoding="utf-8")


def _break_missing_sources(report, scan):
    (scan / brief.SOURCES_FILENAME).unlink()


def _break_stale_sources(report, scan):
    payload = json.loads((scan / brief.SOURCES_FILENAME).read_text(encoding="utf-8"))
    payload["rows"][0]["value"] = "篡改过的值"
    (scan / brief.SOURCES_FILENAME).write_text(json.dumps(payload, ensure_ascii=False),
                                               encoding="utf-8")


def _break_tampered_number(report, scan):
    path = report / brief.BRIEF_FILENAME
    path.write_text(path.read_text(encoding="utf-8")
                    .replace("华泰证券 601688 **Hold**", "华泰证券 601688 **Buy**"),
                    encoding="utf-8")


def _break_summary_mismatch(report, scan):
    summary = report / "summary.md"
    summary.write_text(summary.read_text(encoding="utf-8")
                       .replace("生产 BUY 0 只", "生产 BUY 3 只"), encoding="utf-8")


def _break_outside_whitelist(report, scan):
    payload = json.loads((scan / brief.SOURCES_FILENAME).read_text(encoding="utf-8"))
    payload["rows"][0]["file"] = "details/600018.md"
    (scan / brief.SOURCES_FILENAME).write_text(json.dumps(payload, ensure_ascii=False),
                                               encoding="utf-8")


_SOFT_CHECKS = [("brief·缺失", _break_missing_brief),
                ("brief·超预算", _break_oversize),
                ("brief·边表缺失", _break_missing_sources),
                ("brief·边表过期", _break_stale_sources)]
_HARD_CHECKS = [("brief·数字对账", _break_tampered_number),
                ("brief↔summary不一致", _break_summary_mismatch),
                ("brief·白名单外取数", _break_outside_whitelist)]


def _lint_then_gate4(report, scan) -> tuple[list[dict], dict, list[dict]]:
    """生产同姿势:lint → `append_gate_fires` → 真 `gates.gate4`。"""
    from autoresearch.scan import gates
    rows = self_review.brief_lint(report, scan)
    self_review.append_gate_fires(scan, rows, _DATE)
    fired = list(csv.DictReader((scan / "gate_fires.csv").open(encoding="utf-8")))
    return rows, gates.gate4(scan), fired


def test_gate4_passes_on_a_clean_publish(published):
    report, scan = published
    _rows, gate, _fired = _lint_then_gate4(report, scan)
    assert gate["ok"], gate


@pytest.mark.parametrize(("check", "breaker"), _SOFT_CHECKS,
                         ids=[c for c, _ in _SOFT_CHECKS])
def test_display_layer_defects_warn_but_gate4_still_passes(published, check, breaker):
    """降级四条:各自触发时 GATE4 **仍通过**,且 warn 在 `gate_fires.csv` 里**可见**。"""
    report, scan = published
    breaker(report, scan)
    rows, gate, fired = _lint_then_gate4(report, scan)
    assert check in _checks(rows), f"{check} 没触发 —— 这条用例量错了对象"
    assert gate["ok"], f"{check} 把整条流水线毙了:{gate}"
    assert any(r["check"] == check and r["severity"] == "warn" for r in fired), \
        f"{check} 的 warn 没进 gate_fires.csv —— 降级变成了静默"


@pytest.mark.parametrize(("check", "breaker"), _HARD_CHECKS,
                         ids=[c for c, _ in _HARD_CHECKS])
def test_lying_report_is_fail_and_gate4_blocks(published, check, breaker):
    """保留四条之三:报告陈述与事实/自身矛盾 —— GATE4 **必须不过**。"""
    report, scan = published
    breaker(report, scan)
    rows, gate, _fired = _lint_then_gate4(report, scan)
    hit = [r for r in rows if r["check"] == check]
    assert hit and hit[0]["severity"] == "fail"
    assert not gate["ok"], f"{check} 触发了但 GATE4 放行:{gate}"
    assert check in gate["reason"]


def test_active_buy_contract_is_fail_and_gate4_blocks(tmp_path):
    """保留四条之四:active 期成功 run 却 0 BUY = 报告在说假话,GATE4 不过。"""
    scan = _scan(tmp_path, decision=_decision(mode="active", buys=0))
    report = _publish(tmp_path, scan)
    rows, gate, _fired = _lint_then_gate4(report, scan)
    hit = [r for r in rows if r["check"] == "brief·BUY契约(active 期)"]
    assert hit and hit[0]["severity"] == "fail"
    assert not gate["ok"], gate


def test_severity_table_covers_exactly_the_eleven_criteria():
    """裁定表 = 单一事实源。十一条判据一条不多一条不少,五硬六软(E4 新增第九条同属 fail;
    2026-09-26 §5 B3 新增两条观察席 warn:§12 缺节 / §12 措辞)。"""
    table = self_review.BRIEF_LINT_SEVERITY
    assert set(table) == {"brief·缺失", "brief·超预算", "brief·边表缺失", "brief·边表过期",
                          "brief·数字对账", "brief↔summary不一致", "brief·白名单外取数",
                          "brief·BUY契约(active 期)", "brief③相对BUY与决策文件不同源",
                          "观察席·缺节", "观察席·措辞"}
    assert sorted(k for k, v in table.items() if v == "fail") == sorted(
        ["brief·数字对账", "brief↔summary不一致", "brief·白名单外取数",
         "brief·BUY契约(active 期)", "brief③相对BUY与决策文件不同源"])
    assert sorted(k for k, v in table.items() if v == "warn") == sorted(
        ["brief·缺失", "brief·超预算", "brief·边表缺失", "brief·边表过期",
         "观察席·缺节", "观察席·措辞"])


def test_warn_lines_are_printed_to_cp7(tmp_path, capsys):
    """降级后 warn 必须**仍然播给人看** —— 只数 fail 的播报行会让降级等于消音。"""
    from autoresearch.scan import assemble
    scan = _scan(tmp_path)
    report_root = tmp_path / "reports" / "scan"
    assemble.run(_DATE, scan_dir=scan, out_root=report_root, hhmm="2308",
                 run_date="2026-08-06")
    run_dir = next(report_root.iterdir())
    path = run_dir / brief.BRIEF_FILENAME
    path.write_text(path.read_text(encoding="utf-8") + "填" * 3000, encoding="utf-8")
    capsys.readouterr()
    rows = self_review.brief_lint(run_dir, scan)
    print(self_review.brief_lint_banner(rows))
    out = capsys.readouterr().out
    assert "warn 1" in out and "brief·超预算" in out


def test_never_raises_on_garbage(tmp_path):
    """lint 是发布后自检,坏输入只报条目、绝不抛(与本文件其余 lint 同惯例)。"""
    empty = tmp_path / "nothing"
    empty.mkdir()
    rows = self_review.brief_lint(empty, empty)
    assert isinstance(rows, list)
    assert "brief·缺失" in _checks(rows)


# ───── ⑥ 续:E6 v4 分级行(2026-09-27/28/29 三场真扫的 GATE4 假 fail) ─────
#
# R 级把 ③ 行的前导字形从 ✅ 换成了 🟥(`brief._buy_lines`),而 ⑥ 仍按「行里有 🕶 或 ✅」找那一行
# → 找不到 → brief 侧代码集合恒空 → 与决策文件 `buys[].code` 对不上 → fail → GATE4 毙掉一场
# 已经出了 BUY 的扫描。定位口径改为渲染器导出的 `brief.relative_buy_line`(同一组前缀常量)。

def _tiered_decision(tier: str) -> dict:
    decision = _decision(mode="active", buys=1)
    decision["buys"][0].update(
        {"tier": tier, "basis": "relative_forced" if tier == "R" else "card_backed"})
    decision["tiering"] = True
    decision["tier_counts"] = {"A": int(tier == "A"), "R": int(tier == "R")}
    return decision


@pytest.mark.parametrize("tier", ["R", "A"])
def test_active_tiered_relative_buy_is_same_source(tmp_path, tier):
    scan = _scan(tmp_path, decision=_tiered_decision(tier))
    report = _publish(tmp_path, scan)
    text = (report / brief.BRIEF_FILENAME).read_text(encoding="utf-8")
    assert ("🟥" in text) == (tier == "R"), "夹具没渲染出本用例要测的前导字形"
    assert "600018" in text
    rows = self_review.brief_lint(report, scan)
    assert _E6_CHECK not in _checks(rows), rows
    assert not _fails(rows), _fails(rows)


def test_forced_tier_code_mismatch_is_still_fail(tmp_path):
    """修定位不能把判据修没:R 级行里的代码被换掉,仍然必须 fail 并同时点出两边的代码。"""
    scan = _scan(tmp_path, decision=_tiered_decision("R"))
    report = _publish(tmp_path, scan)
    path = report / brief.BRIEF_FILENAME
    text = path.read_text(encoding="utf-8")
    assert "🟥" in text and "600018" in text, "锚点没先出现,后面的篡改是空操作"
    path.write_text(text.replace("600018", "600188"), encoding="utf-8")
    hit = [r for r in self_review.brief_lint(report, scan) if r["check"] == _E6_CHECK]
    assert hit and hit[0]["severity"] == "fail"
    assert "600188" in hit[0]["detail"] and "600018" in hit[0]["detail"]


def test_forced_tier_fail_reaches_gate4_only_when_codes_differ(tmp_path):
    """端到端到门:R 级干净盘 GATE4 必过(09-29 真扫的形状);同一份盘改掉代码后 GATE4 必拦。"""
    scan = _scan(tmp_path, decision=_tiered_decision("R"))
    report = _publish(tmp_path, scan)
    _, gate, fails = _lint_then_gate4(report, scan)
    assert gate["ok"] is True, (gate, fails)
    path = report / brief.BRIEF_FILENAME
    path.write_text(path.read_text(encoding="utf-8").replace("600018", "600188"), encoding="utf-8")
    _, gate, fails = _lint_then_gate4(report, scan)
    assert gate["ok"] is False and any(r["check"] == _E6_CHECK for r in fails), (gate, fails)
