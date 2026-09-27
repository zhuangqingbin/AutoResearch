"""intel 死票门(2026-09-26 daily-engine §4 A3,批 6 Task 1–2;默认关 = 逐字 parity)。

谓词只读 L1 因子行(`main_inflow_yi`/`cmf_20`/`obv_mom_20`)+ 日历 📅 催化 + 近 10 日事件计数
+ 📌/证据席;**不读卡、不读情报**。Review Focus 1:三列任一缺 → 不判死,note 写「因子缺列」。
"""
from __future__ import annotations

import json

import pandas as pd
import pytest

from autoresearch.scan.l4 import intel_gate as ig


def _row(**kw):
    base = {"main_inflow_yi": -0.8, "cmf_20": -0.05, "obv_mom_20": -0.02, "has_catalyst": False,
            "pinned": False, "guard": ""}
    base.update(kw)
    return base


def test_dead_when_all_three_negative_and_no_catalyst():
    assert ig.is_dead(_row()) is True


def test_alive_if_any_leg_non_negative_or_catalyst_or_pinned_or_seat():
    assert ig.is_dead(_row(cmf_20=0.01)) is False
    assert ig.is_dead(_row(has_catalyst=True)) is False
    assert ig.is_dead(_row(pinned=True)) is False
    assert ig.is_dead(_row(guard="composite_seat")) is False


def test_zero_is_not_negative():
    """阈值是严格 <0:恰好 0 的一线 = 「有一线不为负」,不判死。"""
    assert ig.is_dead(_row(obv_mom_20=0.0)) is False


def test_missing_factor_is_not_dead_and_says_why():
    verdict = ig.decide_row(_row(cmf_20=float("nan")))
    assert verdict.dead is False and "缺列" in verdict.note
    assert ig.decide_row(_row(main_inflow_yi=None)).dead is False
    assert ig.decide_row({"pinned": False, "guard": ""}).dead is False


def test_decide_writes_gate_file_only_when_knob_on(tmp_path, monkeypatch):
    d = tmp_path / "2026-09-17"
    d.mkdir()
    pd.DataFrame([{"code": "600000", "lane": "trend", "guard": ""}]).to_csv(d / "finalists.csv", index=False)
    pd.DataFrame([{"code": "600000", "main_inflow_yi": -1, "cmf_20": -0.1, "obv_mom_20": -0.1}]).to_csv(
        d / "L1_scored_full.csv", index=False)
    monkeypatch.setattr("autoresearch.scan.user_config.load_user_config",
                        lambda path=None: {"l4_intel": {"enabled": True, "max_queries": 20,
                                                        "skip_when_dead": False}})
    assert ig.decide(d) == {"enabled": False, "skipped": [], "checked": 1}
    assert not (d / "_intel_gate.json").exists()                    # 关着 = 逐字 parity,不落文件
    monkeypatch.setattr("autoresearch.scan.user_config.load_user_config",
                        lambda path=None: {"l4_intel": {"enabled": True, "max_queries": 20,
                                                        "skip_when_dead": True}})
    res = ig.decide(d)
    assert res["skipped"] == ["600000"]
    assert json.loads((d / "_intel_gate.json").read_text())["skipped"] == ["600000"]


# ───────────────────────── 真实 staging 形状:催化 / 📌 / 缺行 ─────────────────────────

def _staging(tmp_path, *, calendar=None, catalyst=None, finalists=None):
    d = tmp_path / "2026-09-17"
    d.mkdir()
    fin = finalists or [
        {"code": "000001", "lane": "trend", "guard": "", "pinned_note": ""},
        {"code": "000002", "lane": "trend", "guard": "", "pinned_note": ""},
        {"code": "000003", "lane": "pinned", "guard": "", "pinned_note": ""},
        {"code": "000004", "lane": "reversion", "guard": "composite_seat", "pinned_note": ""},
        {"code": "000005", "lane": "value", "guard": "", "pinned_note": "持仓"},
        {"code": "000006", "lane": "trend", "guard": "", "pinned_note": ""},   # L1 缺行
    ]
    pd.DataFrame(fin).to_csv(d / "finalists.csv", index=False)
    dead = {"main_inflow_yi": -1.0, "cmf_20": -0.1, "obv_mom_20": -0.1}
    pd.DataFrame([{"code": c, **dead} for c in ("000001", "000002", "000003", "000004", "000005")]
                 ).to_csv(d / "L1_scored_full.csv", index=False)
    if calendar is not None:
        pd.DataFrame(calendar, columns=["code", "kind", "event_date", "detail", "ratio"]).to_csv(
            d / "calendar.csv", index=False)
    if catalyst is not None:
        pd.DataFrame(catalyst).to_csv(d / "L3_catalyst.csv", index=False)
    return d


def test_gate_rows_protect_pinned_seat_and_missing_rows(tmp_path):
    rows = {r["code"]: ig.decide_row(r) for r in ig.gate_rows(_staging(tmp_path))}
    assert rows["000001"].dead and rows["000002"].dead
    assert not rows["000003"].dead and not rows["000004"].dead      # 📌 lane / 证据席
    assert not rows["000005"].dead                                   # 📌 第二代标记 pinned_note
    assert not rows["000006"].dead and "缺列" in rows["000006"].note  # L1 缺行 = 因子缺列


def test_dated_calendar_catalyst_keeps_intel(tmp_path):
    """spec A3「无 📅 催化」:预约披露 / 指数调样(非生效前夜)= 📅;解禁 ⚠️ 与生效前夜 ⛔ 不是催化。"""
    cal = [["000001", "disclosure", "20261020", "三季报", None],
           ["000002", "unlock", "20261001", "限售", 5.0]]
    rows = {r["code"]: ig.decide_row(r) for r in ig.gate_rows(_staging(tmp_path, calendar=cal))}
    assert not rows["000001"].dead and "📅" in rows["000001"].note
    assert rows["000002"].dead


def test_dated_catalyst_set_agrees_with_the_l4_brief_calendar_flags(tmp_path):
    """反漂移:门认的 📅 票 == L4 简报 `calendar_flags` 印出 📅 行的票(同一份 calendar.csv)。"""
    from autoresearch.scan.calendar import calendar_flags

    cal = [["000001", "disclosure", "20261020", "三季报", None],
           ["000002", "unlock", "20261001", "限售", 5.0],
           ["000004", "index_rebalance", "20261214", "沪深300 调入|announced", None],
           ["000005", "index_rebalance", "20260918", "中证500 调出|passive_close_eve", None]]
    d = _staging(tmp_path, calendar=cal)
    flagged = {c for c in ("000001", "000002", "000004", "000005")
               if any(ln.startswith("- 📅") for ln in calendar_flags(d, c))}
    assert ig.dated_catalyst_codes(d) == flagged == {"000001", "000004"}


def test_recent_event_counts_keep_intel(tmp_path):
    """近 10 日事件(回购/增减持/调研;`L3_catalyst.csv`)任一 >0 → 派 intel(保守)。"""
    cat = [{"code": "000001", "rep_impl": 0, "rep_plan": 0, "holder_in": 0, "holder_de": 1, "surv_n": 0},
           {"code": "000002", "rep_impl": 0, "rep_plan": 0, "holder_in": 0, "holder_de": 0, "surv_n": 0}]
    rows = {r["code"]: ig.decide_row(r) for r in ig.gate_rows(_staging(tmp_path, catalyst=cat))}
    assert not rows["000001"].dead and "事件" in rows["000001"].note
    assert rows["000002"].dead


def test_gate_file_is_a_registered_artifact():
    from autoresearch.contracts import artifacts as ca

    assert ca.by_name("intel_gate").path == ig.GATE_FILENAME == "_intel_gate.json"


# ───────────────────────── 离线回放(批 6 Task 2;证据门) ─────────────────────────

_HEAD = ("# 活体情报 — {code} 测 @ 2026-09-17\n〔intel v1·盲搜·as-of ≤ 2026-09-17〕\n\n"
         "## 事件段(≤10 行;按时效窗排序:T0 → 24h → 催化挂 → 背景)\n"
         "| 日期 | 时效窗 | 事件(一行,含量级) | 源(含 http(s) 链接) | 净分 |\n|---|---|---|---|---|\n")


def _intel(code: str, *rows: tuple[str, str]) -> str:
    return _HEAD.format(code=code) + "".join(
        f"| 2026-09-17 | {window} | 事件 | [x](https://e.x/{i}) | {score} |\n"
        for i, (window, score) in enumerate(rows))


def _replay_run(tmp_path, name="20260917-0917_2152"):
    run = tmp_path / name / "trace" / "staging"
    run.mkdir(parents=True)
    pd.DataFrame([{"code": c, "lane": "trend", "guard": "", "pinned_note": ""}
                  for c in ("000001", "000002", "000003", "000004")]).to_csv(run / "finalists.csv", index=False)
    dead = {"main_inflow_yi": -1.0, "cmf_20": -0.1, "obv_mom_20": -0.1}
    pd.DataFrame([{"code": "000001", **dead}, {"code": "000002", **dead}, {"code": "000004", **dead},
                  {"code": "000003", **dead, "cmf_20": 0.2}]).to_csv(run / "L1_scored_full.csv", index=False)
    (run / "_final_ratings.json").write_text(json.dumps(
        {"000001": "Underweight", "000002": "Hold", "000003": "Hold", "000004": "Sell"}), encoding="utf-8")
    (run / "_l4_intel_000001.md").write_text(
        _intel("000001", ("T0", "−2.0"), ("背景", "0.0")), encoding="utf-8")   # 全角减号的真格式
    (run / "_l4_intel_000002.md").write_text(_intel("000002", ("T0", "0.0")), encoding="utf-8")
    (run / "_l4_intel_000003.md").write_text(_intel("000003", ("T0", "-1.0")), encoding="utf-8")
    (run / "_l4_intel_000004.md").write_text(          # 旧 schema:没有时效窗列 → T0 无法测
        "## 事件段\n| 日期 | 事件 | 源 | 2日内可发酵? | 净分 |\n|---|---|---|---|---|\n"
        "| 2026-09-17 | 事件 | [x](https://e.x/9) | 否 | -1 |\n", encoding="utf-8")
    return run


def test_replay_rows_join_the_predicate_with_intel_and_final_rating(tmp_path):
    rows = {r["code"]: r for r in ig.replay([_replay_run(tmp_path)])}
    assert set(ig.REPLAY_COLUMNS) >= {"run", "code", "dead", "final_rating",
                                      "intel_t0_negative", "intel_events"}
    one = rows["000001"]
    assert one["run"] == "20260917-0917_2152" and one["dead"] is True
    assert one["intel_t0_negative"] is True and one["intel_events"] == 2      # −2.0 也认得出
    assert one["final_rating"] == "Underweight"
    assert rows["000002"]["dead"] is True and rows["000002"]["intel_t0_negative"] is False
    assert rows["000003"]["dead"] is False                                     # cmf_20 为正
    assert rows["000004"]["intel_t0_negative"] is None                         # 旧稿:不猜


def test_replay_summary_counts_and_applies_the_preregistered_stop_rules(tmp_path):
    rows = ig.replay([_replay_run(tmp_path)])
    got = ig.replay_summary(rows)
    assert got["n_runs"] == 1 and got["n_checked"] == 4 and got["n_dead"] == 3
    assert got["n_dead_t0_negative"] == 1 and got["n_dead_t0_unmeasured"] == 1
    assert got["n_dead_rated_ge_hold"] == 1 and got["n_dead_rated_ge_ow"] == 0
    # 最坏情况:(1 确认 + 1 无法测)/ 3 > 10%;≥Hold 1/3 > 5% → 不上线
    assert got["t0_negative_share_worst"] == pytest.approx(2 / 3)
    assert got["plan_rule_pass"] is False and got["spec_rule_pass"] is False
    assert got["launch_eligible"] is False


def test_replay_cli_prints_csv_rows_and_a_summary(tmp_path, capsys):
    assert ig.main(["replay", str(_replay_run(tmp_path))]) == 0
    out, err = capsys.readouterr()
    lines = out.strip().splitlines()
    assert lines[0].split(",")[:3] == list(ig.REPLAY_COLUMNS[:3]) and len(lines) == 5
    assert json.loads(err.strip().splitlines()[-1])["n_dead"] == 3
