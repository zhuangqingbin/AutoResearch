"""结果账本(2026-08-26 §4.4):只记不学的推荐票事后读数。

判据围绕三件真实风险写:① 幂等(每晚重跑不得让「BUY n 笔」翻倍);② provenance 分列
(shadow 期的 BUY 明写「不执行」、读自共享 staging 的行未必是本 run 那份 —— 混读就是
把假战绩算进去);③ 口径与 `edge_census` 同源(两边读数要能直接对表)。
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from autoresearch.common import workspace as ws
from autoresearch.contracts.agent_output import (
    EXEC_LINE_MAX_PCT_1D,
    EXEC_LINE_MAX_POS_IN_RANGE,
)
from autoresearch.scan import outcome


def test_exec_line_thresholds_are_the_same_object_as_the_single_source():
    """D8.3⑤:`outcome.EXEC_MAX_*` 引用 `contracts.agent_output` 的同一个对象,
    不是复制一份数值——`is` 断言锁死单源,不是 `==`。"""
    assert outcome.EXEC_MAX_PCT_1D is EXEC_LINE_MAX_PCT_1D
    assert outcome.EXEC_MAX_POS_IN_RANGE is EXEC_LINE_MAX_POS_IN_RANGE
    assert outcome.EXEC_MAX_PCT_1D == 3.0
    assert outcome.EXEC_MAX_POS_IN_RANGE == 0.7

# ───────────────────────── 夹具:一个最小的已发布 run ─────────────────────────

def _run(tmp_path, run_id="20260825_2149", date="2026-08-25", *, mode="active",
         buys=("603317",), in_mirror=True):
    run = tmp_path / ws.reports_root() / "scan" / run_id
    run.mkdir(parents=True)
    (run / "manifest.json").write_text(json.dumps(
        {"analysis_date": date, "run_id": "20260825T123650212028Z",
         # 真 run 一定有发布时刻;时间锚(§2.4 G1)靠它判「什么时候才能下单」。
         "generated_at": f"{date}T21:49:00"}), encoding="utf-8")
    base = run / "trace" / "staging" if in_mirror else tmp_path / ws.scan_root() / date
    base.mkdir(parents=True)
    (base / "finalists.csv").write_text(
        "code,name,sector,lane,guard,conviction\n"
        "603317,天味食品,调味发酵品Ⅱ,reversion,,55\n"
        "300857,协创数据,消费电子,pinned,,57\n", encoding="utf-8")
    (base / "_final_ratings.json").write_text(
        json.dumps({"603317": "Underweight", "300857": "Underweight"}), encoding="utf-8")
    (base / "_early_stop.json").write_text(
        json.dumps({"603317": {"phase": "P3", "reason": "资金流出"}}), encoding="utf-8")
    (base / "_relative_buy_decision.json").write_text(json.dumps({
        "mode": mode, "rule_version": "e6.v2.0", "blocked": False,
        "buys": [{"code": c, "rank": 1, "basis": "relative"} for c in buys],
        "candidates": [{"code": "603317", "eligible": True, "rank": 2}],
    }), encoding="utf-8")
    return run


def _fake_market(monkeypatch, *, gap=0.01, pct1=-2.0, pos=0.3):
    """把湖那一层换成合成帧 —— 本测试要验的是账本逻辑,不是取数。

    2026-09-12 §2:`compute_outcome` 现在从 `meta` 读 `outcome_status`/`calendar_quality`/
    `calendar_digest`(§3 schema 2 新字段),所以这份 fake 的 `meta` 必须把它们补全为一个
    **已核验的成熟**结果——否则每个用这份 fake 的既有测试都会被新的 `_is_settled` 判定
    判成"未核验",第二次 `fill()` 不会再跳过(悄悄改变了一批与本次改动无关的既有测试的
    通过原因)。`fwd5_verified`/`fwd10_verified` 刻意不设(维持它们原有的、这份 fake 从来
    没启用过的 None→False 行为,不在这次改动里顺带改掉)。
    """
    codes = ["603317", "300857", "000001", "000002"]
    fr = pd.DataFrame(index=pd.Index(codes, name="code"))
    fr[outcome.MAIN] = [gap, 0.02, 0.0, -0.01]
    fr["buyable_c1"] = True
    fr["fwd_5_oc"] = 0.03
    fr["fwd_10_oc"] = 0.05
    fr["t1_open"] = 10.0
    fr["t1_high"] = 11.0
    fr["t1_low"] = 9.0
    fr["t1_close"] = 9.0 + 2.0 * pos
    fr["t1_pct_chg"] = pct1
    fr["t2_open"] = 10.5
    fr["t1_pos_in_range"] = pos
    monkeypatch.setattr(outcome, "market_frame",
                        lambda date, **k: (fr, {
                            "outcome_status": outcome.MATURE, "reason": "",
                            "calendar_quality": outcome.TRADE_CAL_QUALITY,
                            "calendar_digest": "testfakecalendar0",
                            "t1": "20260826", "t2": "20260827", "missing_sessions": [],
                            "n": len(fr),
                        }))
    return fr


# ───────────────────────── 幂等 ─────────────────────────

def test_upsert_is_idempotent(tmp_path, monkeypatch):
    """每晚 prelude 都会跑一次 fill;重复 append 会让「BUY n 笔」直接翻倍。"""
    monkeypatch.chdir(tmp_path)
    _fake_market(monkeypatch)
    run = _run(tmp_path)
    doc = outcome.compute_outcome(run)
    root = tmp_path / ws.reports_root() / "scan"
    outcome.upsert_ledger(doc, root)
    outcome.upsert_ledger(doc, root)
    outcome.upsert_ledger(doc, root)
    rows = outcome.load_ledger(root)
    assert len(rows) == 2                      # 两只票,不是六行
    assert len({(r["run_id"], r["code"]) for r in rows}) == 2


def test_upsert_ledger_csv_write_is_atomic_a_failed_write_never_truncates_it(tmp_path, monkeypatch):
    """2026-09-13 fix round 1(Task C3 finding 2):`recommendations.csv` 是「结果账本」
    这个模块存在的意义所在——旧实现是裸 `path.open("w")`(既无临时文件也无 fsync),
    打开的瞬间就把旧内容截断成 0 字节,写到一半进程死掉就是一份读不全的账本。改用
    `trace.atomic.atomic_write_bytes` 后,失败必须发生在"替换生效"之前:目标文件在
    失败的写入尝试前后必须逐字节相同——不是"部分写入"也不是"空文件"。
    """
    monkeypatch.chdir(tmp_path)
    _fake_market(monkeypatch)
    root = tmp_path / ws.reports_root() / "scan"
    good = outcome.compute_outcome(_run(tmp_path))
    outcome.upsert_ledger(good, root)
    path = outcome.ledger_root(root) / outcome.LEDGER_CSV
    good_bytes = path.read_bytes()
    assert good_bytes                                   # 夹具健全性:真的写出了内容

    def boom(*_a, **_k):
        raise OSError("simulated crash mid csv-write")

    monkeypatch.setattr(outcome, "atomic_write_bytes", boom)
    other = outcome.compute_outcome(_run(tmp_path, "20260826_2000", "2026-08-26"))
    with pytest.raises(OSError, match="simulated crash"):
        outcome.upsert_ledger(other, root)

    assert path.read_bytes() == good_bytes               # 逐字节不变——不是截断、不是半份


def test_fill_skips_already_complete_runs(tmp_path, monkeypatch):
    """增量:成熟的 run 不重算 —— 否则每天成本随历史长度线性增长。"""
    monkeypatch.chdir(tmp_path)
    _fake_market(monkeypatch)
    _run(tmp_path)
    root = tmp_path / ws.reports_root() / "scan"
    first = outcome.fill(reports_root=root)
    second = outcome.fill(reports_root=root)
    assert first["filled"] == 1 and second["filled"] == 0 and second["skipped"] == 1


def test_immature_run_gets_a_status_document_but_no_ledger_rows(tmp_path, monkeypatch):
    """D+2 还没落湖 = **状态不是故障**,但 2026-09-12 §2(ruling #2)起 `compute_outcome`
    不再用裸 `None` 丢掉原因:只要 run/facts 定位得到,就返回一份带 `outcome_status`/
    `reason` 的文档——`fill()` 因此会把它写盘(不再是"完全不写"),但它 `complete=False`、
    `rows` 为空,所以**没有一行**会进 `recommendations.csv`(没有可汇总的主尺数值,同旧
    版"不写半截结果"的精神,只是现在连"为什么还没有"也留了痕)。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(outcome, "market_frame", lambda date, **k: (None, {
        "outcome_status": outcome.PENDING_SESSION, "reason": "可信目标 T+2 尚未到",
        "calendar_quality": outcome.TRADE_CAL_QUALITY, "calendar_digest": "d0",
        "t1": "20260826", "t2": "20260827", "missing_sessions": [],
    }))
    _run(tmp_path)
    root = tmp_path / ws.reports_root() / "scan"
    res = outcome.fill(reports_root=root)
    assert res["filled"] == 1 and res["skipped"] == 0
    doc = json.loads(outcome.outcome_path("20260825_2149", root).read_text(encoding="utf-8"))
    assert doc["schema_version"] == 2
    assert doc["outcome_status"] == outcome.PENDING_SESSION
    assert doc["reason"] == "可信目标 T+2 尚未到"
    assert doc["complete"] is False
    assert doc["rows"] == {}                    # 没有可汇总的主尺数值,不是半个数字
    assert outcome.load_ledger(root) == []       # CSV 依然是空的


def test_facts_not_locatable_still_returns_bare_none_and_is_recorded_as_skipped(tmp_path, monkeypatch):
    """Ruling #2 的另一半:run/facts **完全定位不到**(没有数据日、没有任何票)时,
    `compute_outcome` 仍沿用旧的裸 `None` 返回——但 `fill()` 必须把跳过原因记下来,
    不能连"为什么跳过"都是静默的。"""
    monkeypatch.chdir(tmp_path)
    run = tmp_path / ws.reports_root() / "scan" / "20260825_0000"
    (run / "trace" / "staging").mkdir(parents=True)
    (run / "manifest.json").write_text(json.dumps({"analysis_date": ""}), encoding="utf-8")
    assert outcome.compute_outcome(run) is None
    root = tmp_path / ws.reports_root() / "scan"
    res = outcome.fill(reports_root=root)
    assert res["filled"] == 0 and res["skipped"] == 1
    assert res["skip_reasons"]["20260825_0000"] == "run_or_facts_not_locatable"
    assert not outcome.outcome_path("20260825_0000", root).exists()


# ───────────────────────── provenance 分列 ─────────────────────────

def test_shadow_buys_are_not_counted_as_active(tmp_path, monkeypatch):
    """shadow 期的 BUY 在当天报告里明写「非正式·不执行」。把它算进战绩 = 自欺。

    实测背景:2026-08-13/08-18 两天 brief 印的是 BLOCKED,而共享 staging 的决策文件被后来
    的影子回放改写成 `buys=[688766]`(还是一只 📌 持仓)。"""
    monkeypatch.chdir(tmp_path)
    _fake_market(monkeypatch)
    root = tmp_path / ws.reports_root() / "scan"
    outcome.upsert_ledger(outcome.compute_outcome(
        _run(tmp_path, "20260818_2043", "2026-08-18", mode="shadow")), root)
    line = outcome.ledger_line(root)
    assert "active BUY 0 笔" in line and "shadow 期 1 笔不计" in line


def test_src_column_flags_shared_staging_reads(tmp_path, monkeypatch):
    """读自共享 staging 的行必须自带标记 —— 同数据日重跑会覆盖它。"""
    monkeypatch.chdir(tmp_path)
    _fake_market(monkeypatch)
    run = _run(tmp_path, in_mirror=False)          # 只有共享 staging,没有 trace/staging
    doc = outcome.compute_outcome(run)
    assert doc["read_from_shared_staging"] is True
    root = tmp_path / ws.reports_root() / "scan"
    outcome.upsert_ledger(doc, root)
    assert {r["src"] for r in outcome.load_ledger(root)} == {"shared"}


def test_mirrored_run_is_marked_src_run(tmp_path, monkeypatch):
    """有 `trace/staging` 的 run 读自己那份 —— 这正是本波镜像存在的意义。"""
    monkeypatch.chdir(tmp_path)
    _fake_market(monkeypatch)
    doc = outcome.compute_outcome(_run(tmp_path))
    assert doc["read_from_shared_staging"] is False
    root = tmp_path / ws.reports_root() / "scan"
    outcome.upsert_ledger(doc, root)
    assert {r["src"] for r in outcome.load_ledger(root)} == {"run"}


# ───────────────────────── 角色与执行线 ─────────────────────────

def test_roles_distinguish_buy_pinned_finalist(tmp_path, monkeypatch):
    """📌 保送不是系统选的(2026-07-16 判例:保送不算判例),必须与 finalist 分账。"""
    monkeypatch.chdir(tmp_path)
    _fake_market(monkeypatch)
    doc = outcome.compute_outcome(_run(tmp_path))
    assert doc["rows"]["603317"]["role"] == "BUY"
    assert doc["rows"]["300857"]["role"] == "pinned"


@pytest.mark.parametrize("pct1,pos,expect", [
    (-2.0, 0.30, True),     # 不追高、收得弱 → 可执行
    (7.2, 0.93, False),     # 当日大涨 + 收在区间顶 → 正是四年逐年为负的那一档
    (1.0, 0.85, False),     # 涨幅达标但收在区间上 30%
    (5.0, 0.20, False),     # 收得弱但当日涨超 3%
])
def test_exec_ok_encodes_the_a4_line(tmp_path, monkeypatch, pct1, pos, expect):
    """A4 执行线:当日涨幅 ≤3% ∧ 收盘不在区间上 30% ∧ 未封涨停。
    这是**量尺不是门** —— 先记下来,才有「BUY 无条件」与「BUY ∧ 执行线」两条曲线可比。"""
    monkeypatch.chdir(tmp_path)
    _fake_market(monkeypatch, pct1=pct1, pos=pos)
    doc = outcome.compute_outcome(_run(tmp_path))
    assert doc["rows"]["603317"]["exec_ok"] is expect


def test_exec_ok_is_none_when_unbuyable_unknown(tmp_path, monkeypatch):
    """封涨停买不进 → 执行线不成立(不是「未知按可执行」)。"""
    monkeypatch.chdir(tmp_path)
    fr = _fake_market(monkeypatch)
    fr["buyable_c1"] = False
    doc = outcome.compute_outcome(_run(tmp_path))
    assert doc["rows"]["603317"]["exec_ok"] is False


# ───────────────────────── 口径 ─────────────────────────

def test_relative_columns_use_declared_denominators(tmp_path, monkeypatch):
    """三个相对列分母不同,**列名即口径**;混读会得到符号相反的结论(均值 vs 中位)。"""
    fr = pd.DataFrame({outcome.MAIN: [0.10, 0.00, 0.00, -0.02],
                       "buyable_c1": True},
                      index=["a", "b", "c", "d"])
    rel = outcome._relative_columns(fr, membership={"status": "COMPLETE", "expected_codes": list(fr.index), "members": {"a": "钢铁", "b": "钢铁", "c": "银行", "d": "银行"}})
    base = fr[outcome.MAIN]
    assert rel.loc["a", "rel_gap_market"] == pytest.approx(0.10 - base.mean())
    assert rel.loc["a", "excess_med_market"] == pytest.approx(0.10 - base.median())
    assert rel.loc["a", "rel_gap_sector"] == pytest.approx(0.10 - np.mean([0.10, 0.00]))


def test_sector_relative_is_nan_without_industry(tmp_path):
    """没有行业 → 行业相对列是 NaN,**不猜**(同 ruler.REL_SECTOR 的既定语义)。"""
    fr = pd.DataFrame({outcome.MAIN: [0.01, 0.02], "buyable_c1": True}, index=["a", "b"])
    rel = outcome._relative_columns(fr, {"a": "", "b": ""})
    assert pd.isna(rel.loc["a", "rel_gap_sector"])


def test_ledger_line_withholds_mean_below_min_n(tmp_path, monkeypatch):
    """<20 笔只印「攒样本」不印均值 —— 小样本均值会被当成结论读,而这条线存在的意义
    正是不让人再凭印象说「最近推荐得挺准」。"""
    monkeypatch.chdir(tmp_path)
    _fake_market(monkeypatch)
    root = tmp_path / ws.reports_root() / "scan"
    outcome.upsert_ledger(outcome.compute_outcome(_run(tmp_path)), root)
    line = outcome.ledger_line(root)
    assert "攒样本 1/20" in line and "均 gap" not in line


def test_ledger_line_empty_is_honest(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert "空" in outcome.ledger_line(tmp_path / ws.reports_root() / "scan")


def test_cli_fill_and_line(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _fake_market(monkeypatch)
    _run(tmp_path)
    assert outcome.main(["fill"]) == 0
    out = capsys.readouterr().out
    assert json.loads(out.splitlines()[0])["filled"] == 1
    assert outcome.main(["line"]) == 0
    capsys.readouterr()


# ───────────────────── 时间锚(2026-08-28 §2.4 G1)─────────────────────
#
# 账本按 `analysis_date` 锚 T+1,而报告是晚上跑的:61 个已发布 run 里 8 个(13%)在 T+1
# 收盘之后才就绪 —— 给它们记的「T+1 尾盘买」买的是**已经过去**的价格。下面四条钉住:
# 迟到 run 被标出来、被排除出主均值、反事实单列、正常 run 逐字不变。

def _late_run(tmp_path, **kw):
    """报告在 T+1(08-26)收盘之后才写完 —— 真实案发形状(`20260826_2000`)。"""
    run = _run(tmp_path, **kw)
    man = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    man["generated_at"] = "2026-08-26T20:00:00"
    (run / "manifest.json").write_text(json.dumps(man), encoding="utf-8")
    return run


def test_normal_run_is_actionable_and_has_no_counterfactual(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _fake_market(monkeypatch)
    root = tmp_path / ws.reports_root() / "scan"
    outcome.upsert_ledger(outcome.compute_outcome(_run(tmp_path)), root)
    row = outcome.load_ledger(root)[0]
    assert row["actionability"] == "ACTIONABLE"
    assert row["exec_lag"] == "0"
    # 锚点与主帧相同 → 不另算一份反事实(算了也是同一个数,徒增歧义)
    assert row["exec_gap_c1_o2"] == ""


def test_late_run_is_flagged_not_silently_counted(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _fake_market(monkeypatch)
    root = tmp_path / ws.reports_root() / "scan"
    outcome.upsert_ledger(outcome.compute_outcome(_late_run(tmp_path)), root)
    row = outcome.load_ledger(root)[0]
    assert row["actionability"] == "LATE_REVALIDATION_REQUIRED"
    assert row["anchor_session"] == "2026-08-27"      # T+1 已收盘 → 顺延到 T+2 尾盘
    assert row["exec_lag"] == "1"


def test_ledger_line_excludes_late_buys_from_the_mean(tmp_path, monkeypatch):
    """13% 的假单不能进战绩 —— 排除掉还要**明说排除了几笔**,不能静默。"""
    monkeypatch.chdir(tmp_path)
    _fake_market(monkeypatch)
    root = tmp_path / ws.reports_root() / "scan"
    outcome.upsert_ledger(outcome.compute_outcome(_late_run(tmp_path)), root)
    line = outcome.ledger_line(root)
    assert "可执行 0" in line and "迟到 1 笔不计" in line


def test_counterfactual_uses_the_first_reachable_close(tmp_path, monkeypatch):
    """迟到 run 的反事实:同一把尺,买腿改到第一个真正来得及的尾盘。

    2026-09-12 fix round 1:`exec_anchor_frame` 返回 `(fr, meta)`(review finding 1);
    这里的 fake 跟着新契约换成二元组,`meta["exec_outcome_status"]` 用 `MATURE` 代表
    "反事实算出来了"。
    """
    monkeypatch.chdir(tmp_path)
    _fake_market(monkeypatch)
    monkeypatch.setattr(outcome, "exec_anchor_frame",
                        lambda execution, **k: (pd.DataFrame(
                            {outcome.MAIN: [0.077]},
                            index=pd.Index(["603317"], name="code")),
                            {"exec_outcome_status": outcome.MATURE, "reason": "",
                             "anchor_session": "20260827"}))
    root = tmp_path / ws.reports_root() / "scan"
    outcome.upsert_ledger(outcome.compute_outcome(_late_run(tmp_path)), root)
    row = next(r for r in outcome.load_ledger(root) if r["code"] == "603317")
    assert row["exec_gap_c1_o2"] == "0.077"
    assert row["gap_c1_o2"] != row["exec_gap_c1_o2"]   # 两个人口,绝不互相顶替


def test_exec_outcome_status_distinguishes_error_from_not_applicable_in_the_csv(tmp_path, monkeypatch):
    """Ruling #3(C1 re-review Q2):`exec_outcome_status` 的"不适用"(正常 run,无需算,
    `exec_lag=0`)与"算过但失败"(有原因的错误态)落盘后必须是两个不同的单元格——
    不能塌缩成同一种"空"。两个 run 分开写,避免同 run_id 的整体替换互相覆盖。
    """
    monkeypatch.chdir(tmp_path)
    _fake_market(monkeypatch)
    root = tmp_path / ws.reports_root() / "scan"

    # 正常 run(exec_lag=0)—— 不适用,CSV 应为空单元格。
    outcome.upsert_ledger(outcome.compute_outcome(_run(tmp_path, "20260824_2000", "2026-08-24")),
                          root)
    normal_row = next(r for r in outcome.load_ledger(root) if r["run_id"] == "20260824_2000")
    assert normal_row["exec_outcome_status"] == ""

    # 迟到 run,但它自己的迟到锚在可信日历里都定位不出来 —— 有原因的错误态,不是空。
    monkeypatch.setattr(outcome, "exec_anchor_frame",
                        lambda execution, **k: (None, {
                            "exec_outcome_status": outcome.UNVERIFIED_CALENDAR,
                            "reason": "迟到锚的前一交易日无法用可信日历核验",
                            "anchor_session": None}))
    outcome.upsert_ledger(outcome.compute_outcome(
        _late_run(tmp_path, run_id="20260826_2000", date="2026-08-25")), root)
    late_row = next(r for r in outcome.load_ledger(root) if r["run_id"] == "20260826_2000")
    assert late_row["exec_outcome_status"] == outcome.UNVERIFIED_CALENDAR
    assert late_row["exec_outcome_status"] != normal_row["exec_outcome_status"]


# ───────────────────── 日历完整性:schema 2、撤回、标签(2026-09-12 Task C2)─────────────────────
#
# C10/C11/C14(见 `.superpowers/sdd/2026-09-12-outcome-trading-calendar-integrity/
# task-C2-brief.md` §7 验收矩阵)。

def test_c10_schema1_complete_true_is_reverified_not_trusted_forever(tmp_path, monkeypatch):
    """C10:旧 schema 1、`complete=True`、日期是当年"湖分区排序位置"算出来的错误日期——
    `fill()` 的跳过快捷路径不能因为 `complete=True` 就永远信任它,必须重新核验;核验不过
    (弱日历)时,那笔曾经被当作有效值使用的 gap 必须从统计里撤出。"""
    monkeypatch.chdir(tmp_path)
    root = tmp_path / ws.reports_root() / "scan"
    run = _run(tmp_path)          # "20260825_2149",2026-08-25,603317(BUY)/300857(pinned)
    old = {
        "schema_version": 1, "run_id": run.name, "contract_run_id": "20260825T214900000000Z",
        "analysis_date": "2026-08-25", "ruler": outcome.MAIN,
        "t1": "20260827", "t2": "20260828",       # 错的:真 T+1 本应是 08-26(旧 bug:湖缺
                                                   # 08-26,湖分区排序位置取了下一个文件当 T+1)
        "decision_mode": "active", "rule_version": "e6.v2.0",
        "read_from_shared_staging": False, "complete": True,
        "n_rows": 1, "n_scored": 1,
        "exec_line": {"max_pct_1d": 3.0, "max_pos_in_range": 0.7}, "execution": None,
        "rows": {"603317": {"code": "603317", "name": "天味食品", "sector": "调味发酵品Ⅱ",
                             "role": "BUY", "e6_buy": True, outcome.MAIN: 0.08}},
    }
    outcome.write_outcome(old, root)
    outcome.upsert_ledger(old, root)          # 旧 CSV 也带着这行(当年被当有效值用过)
    before = outcome.load_ledger(root)
    assert before and before[0][outcome.MAIN] == "0.08"

    def _weak_calendar(start, end):
        return (["2026-08-25", "2026-08-26", "2026-08-27"], "lake_partitions")

    result = outcome.fill(reports_root=root, calendar=_weak_calendar, today="2026-09-11")

    assert result["filled"] == 1                  # 没有被 complete=True 永久跳过
    new_doc = json.loads(outcome.outcome_path(run.name, root).read_text(encoding="utf-8"))
    assert new_doc["schema_version"] == 2
    assert new_doc["outcome_status"] == outcome.UNVERIFIED_CALENDAR
    assert new_doc["complete"] is False
    assert new_doc["rows"] == {}
    # 前镜像被留存(不是 destroy),但只在 `previous` 里,不会被当前统计读到
    assert new_doc["previous"]["rows"]["603317"][outcome.MAIN] == 0.08
    assert outcome.load_ledger(root) == []         # 未知日历的旧收益已撤出统计


def test_c11_recompute_failure_withdraws_a_previously_mature_schema2_run(tmp_path, monkeypatch):
    """C11:一个 run 曾经在 schema 2 下成熟过(真实数值已经写进 JSON 与 CSV),之后
    `rebuild=True` 重算却返回 MISSING_MARKET_DATA——旧的有效值必须从 CSV 撤出,新写的
    JSON 与新 CSV 必须互相一致(不能一个显示失败、另一个还留着老数字)。"""
    monkeypatch.chdir(tmp_path)
    root = tmp_path / ws.reports_root() / "scan"
    calls = {"n": 0}
    fail_meta = {"outcome_status": outcome.MISSING_MARKET_DATA,
                 "reason": "行情缺失(湖无分区):20260827",
                 "calendar_quality": outcome.TRADE_CAL_QUALITY, "calendar_digest": "d2",
                 "t1": "20260826", "t2": "20260827", "missing_sessions": ["20260827"]}
    _fake_market(monkeypatch)
    mature_fake = outcome.market_frame            # `_fake_market` 刚装上的那个 lambda

    def _sequenced(date, **k):
        calls["n"] += 1
        return mature_fake(date, **k) if calls["n"] == 1 else (None, fail_meta)

    monkeypatch.setattr(outcome, "market_frame", _sequenced)
    _run(tmp_path)
    first = outcome.fill(reports_root=root)
    assert first["filled"] == 1
    before = outcome.load_ledger(root)
    assert {r["code"] for r in before} == {"603317", "300857"}
    assert all(r[outcome.MAIN] not in ("", None) for r in before)

    # 2026-09-13 fix round 1(Task C3 finding 1):`fill(rebuild=True)` 裸调用现在会拒绝
    # (改道去防止未审阅覆写),这条用例测的是**朴素增量引擎自己的**撤回逻辑,
    # 直接调 `_fill_incremental`(`fill()` 的既有实现搬迁后的新名字)不受那条新守卫影响。
    second = outcome._fill_incremental(reports_root=root, rebuild=True)
    assert second["filled"] == 1
    new_doc = json.loads(outcome.outcome_path("20260825_2149", root).read_text(encoding="utf-8"))
    assert new_doc["outcome_status"] == outcome.MISSING_MARKET_DATA
    assert new_doc["rows"] == {}
    assert new_doc["previous"]["complete"] is True
    assert set(new_doc["previous"]["rows"]) == {"603317", "300857"}
    after = outcome.load_ledger(root)
    assert after == []                             # 旧有效值不再计入
    assert len({(r["run_id"], r["code"]) for r in after}) == len(after)   # (run_id,code) 唯一


def test_whole_run_replace_does_not_touch_other_runs_rows(tmp_path, monkeypatch):
    """bullet 5:一个 run 的行被整体替换/清空时,**只**影响它自己的 `(run_id, code)`——
    另一个 run 已经写好的行必须原样留在 CSV 里,不能被"重建"误伤(整体替换的范围是
    单个 run_id,不是整张表)。"""
    monkeypatch.chdir(tmp_path)
    root = tmp_path / ws.reports_root() / "scan"
    _fake_market(monkeypatch)
    other = outcome.compute_outcome(_run(tmp_path, "20260820_2000", "2026-08-20"))
    outcome.upsert_ledger(other, root)
    other_rows_before = {(r["run_id"], r["code"]) for r in outcome.load_ledger(root)}
    assert other_rows_before == {("20260820_2000", "603317"), ("20260820_2000", "300857")}

    calls = {"n": 0}
    fail_meta = {"outcome_status": outcome.MISSING_MARKET_DATA, "reason": "行情缺失",
                 "calendar_quality": outcome.TRADE_CAL_QUALITY, "calendar_digest": "d",
                 "t1": "20260826", "t2": "20260827", "missing_sessions": ["20260827"]}
    mature_fake = outcome.market_frame

    def _sequenced(date, **k):
        calls["n"] += 1
        return mature_fake(date, **k) if calls["n"] == 1 else (None, fail_meta)

    monkeypatch.setattr(outcome, "market_frame", _sequenced)
    target = _run(tmp_path, "20260825_2149", "2026-08-25")
    outcome.upsert_ledger(outcome.compute_outcome(target), root)      # 第一次:成熟
    outcome.upsert_ledger(outcome.compute_outcome(target), root)      # 第二次:重算失败

    after = outcome.load_ledger(root)
    after_keys = {(r["run_id"], r["code"]) for r in after}
    assert other_rows_before <= after_keys         # 另一个 run 的行原样还在
    assert not any(r["run_id"] == "20260825_2149" for r in after)     # 目标 run 的行清空了
    assert len(after_keys) == len(after)            # (run_id, code) 唯一


def test_c14_ledger_line_labels_gross_return_not_actual_fills(tmp_path, monkeypatch):
    """C14:毛收益(gap_c1_o2)、执行反事实(exec_gap_c1_o2)、真实成交(未接 broker,恒未知)
    三者标签与人口不能混——`ledger_line` 的渲染文本必须显式标"毛",绝不能自称
    "实际成交"或"净收益"(本模块从未接入 broker,没有任何输入能证明一笔真的成交了)。
    """
    monkeypatch.chdir(tmp_path)
    root = tmp_path / ws.reports_root() / "scan"
    rows = {}
    for i in range(outcome.MIN_LEDGER_N):
        code = f"{i:06d}"
        rows[code] = {
            "code": code, "name": f"测试{i}", "sector": "测试行业", "role": "BUY",
            "e6_buy": True, "buyable_c1": True, "t1_close": 10.1, "t1_pct_chg": 1.0,
            "t1_pos_in_range": 0.4, "exec_ok": True, "t2_open": 10.3,
            outcome.MAIN: 0.02, "rel_gap_market": 0.01,
        }
    doc = {
        "schema_version": outcome.OUTCOME_SCHEMA_VERSION, "run_id": "20260825_2149",
        "contract_run_id": "x", "analysis_date": "2026-08-25", "ruler": outcome.MAIN,
        "outcome_status": outcome.MATURE, "reason": "",
        "calendar_quality": outcome.TRADE_CAL_QUALITY, "calendar_digest": "d",
        "t1": "20260826", "t2": "20260827",
        "decision_mode": "active", "rule_version": "e6.v2.0", "read_from_shared_staging": False,
        "complete": True, "n_rows": len(rows), "n_scored": len(rows),
        "exec_line": {"max_pct_1d": 3.0, "max_pos_in_range": 0.7},
        "execution": {"first_available_session": "2026-08-26", "exec_lag": 0,
                      "actionability_status": "ACTIONABLE"},
        "rows": rows,
    }
    outcome.write_outcome(doc, root)
    outcome.upsert_ledger(doc, root)

    line = outcome.ledger_line(root)
    assert "净收益" not in line
    # "实际成交"允许出现,但只能是"非实际成交"的一部分(明确否定,不是在冒充)。
    assert "实际成交" not in line.replace("非实际成交", "")
    assert "毛" in line              # 显式标"毛",与"净"/"实际成交"区分


def test_ledger_line_shows_legacy_rows_as_calendar_unverified(tmp_path, monkeypatch):
    """bullet 6:迁移前的老行(`outcome_status`/`calendar_quality` 两列缺失或为空)必须
    显示"日历未验证",绝不能与 v2 已验证的结果混进同一个均值——即使老行恰好带着一个
    曾经被当作有效值使用的数字(同 `actionability` 的既定纪律:缺列/空值 = 未知)。"""
    monkeypatch.chdir(tmp_path)
    root = tmp_path / ws.reports_root() / "scan"
    _fake_market(monkeypatch)
    outcome.upsert_ledger(outcome.compute_outcome(_run(tmp_path)), root)   # 一笔 v2 合格行

    path = outcome.ledger_root(root) / outcome.LEDGER_CSV
    header = path.read_text(encoding="utf-8").splitlines()[0].split(",")
    legacy = {"run_id": "legacy_run", "analysis_date": "2026-07-01", "mode": "active",
             "src": "run", "code": "999999", "e6_buy": "True", "actionability": "ACTIONABLE",
             outcome.MAIN: "0.09"}                  # 没有 outcome_status/calendar_quality
    with path.open("a", encoding="utf-8") as fh:
        fh.write(",".join(legacy.get(col, "") for col in header) + "\n")

    line = outcome.ledger_line(root)
    assert "日历未验证 1 笔不计" in line
    assert "攒样本" in line          # 合格样本仍然只有 1 笔,远不到 20,不印均值


# ───────────────────────── 10 日尺成熟(2026-09-26 批 5 Task 1 · B1)─────────────────────────
#
# 主尺(隔夜 gap_c1_o2)D+2 成熟,10 日尺(`common.ruler.SWING_RULER` = fwd_10_oc)D+10 才
# 成熟 —— 两枚成熟章,互不替代。旧判据「D+2 一到就永久跳过重算」让 fwd_10_oc 永久冻成
# 缺失;新判据下 `fill` 对主尺已核验的 run 只补 10 日尺那几列,**主尺数值冻结不重写**
# (重写已核验的主尺历史只能走 §6 C3 的可审阅迁移,不能借 swing 回访悄悄发生)。

import datetime as _dt  # noqa: E402  (本节夹具专用,留在节内便于整节阅读)


def _weekdays(start: str, end: str) -> list[str]:
    s, e = _dt.date.fromisoformat(start), _dt.date.fromisoformat(end)
    out, cur = [], s
    while cur <= e:
        if cur.weekday() < 5:
            out.append(cur.isoformat())
        cur += _dt.timedelta(days=1)
    return out


#: 合成可信日历(无节假日的工作日),覆盖 D=2026-09-10 前后远超 10 个 session。
_SWING_SESSIONS = _weekdays("2026-08-03", "2026-11-30")
_SWING_CODES = ("603317", "300857", "000001")


def _write_swing_day(root, day: str, i: int, *, t2_open_bump: float = 0.0) -> None:
    px = 10.0 + 0.1 * i
    pd.DataFrame([{"ts_code": f"{c}.{'SH' if c[0] == '6' else 'SZ'}",
                   "open": px + t2_open_bump, "high": px * 1.05, "low": px * 0.95,
                   "close": px * 1.01, "pct_chg": 1.0, "amount": 1.0e8}
                  for c in _SWING_CODES]).to_parquet(root / f"{day.replace('-', '')}.parquet",
                                                     index=False)


@pytest.fixture
def tmp_lake_10_sessions(tmp_path, monkeypatch):
    """D=2026-09-10 之后 ≥10 个交易日的真 parquet 湖(09-01..09-30)+ 注入可信日历。

    日历走 `exec_anchor.trading_sessions` 的 monkeypatch(`market_frame` 缺省即读它),
    所以测试不联网、也不依赖真实 tushare token。T+10 = 2026-09-24。
    """
    from autoresearch.scan import exec_anchor as ea

    monkeypatch.setattr(ea, "trading_sessions", lambda start, end: (
        [d for d in _SWING_SESSIONS if start <= d <= end], "trade_cal"))
    root = tmp_path / "lake" / "daily"
    root.mkdir(parents=True)
    for i, day in enumerate(d for d in _SWING_SESSIONS if "2026-09-01" <= d <= "2026-09-30"):
        _write_swing_day(root, day, i)
    return root


def test_outcome_marks_swing_maturity_separately(tmp_lake_10_sessions):
    """D+2 成熟不等于 D+10 成熟:主尺 MATURE 而 10 日尺 PENDING_10 必须同时成立。"""
    fr, meta = outcome.market_frame("2026-09-10", lake_daily=tmp_lake_10_sessions, today="2026-09-15")
    assert meta["outcome_status"] == outcome.MATURE
    assert meta["outcome_status_swing"] == "PENDING_10" and meta["t10"] is not None
    fr2, meta2 = outcome.market_frame("2026-09-10", lake_daily=tmp_lake_10_sessions, today="2026-09-30")
    assert meta2["outcome_status_swing"] == "MATURE_10" and meta2["fwd10_verified"] is True


def test_swing_is_missing_market_data_when_t10_has_passed_but_the_lake_lacks_it(tmp_lake_10_sessions):
    """T+10 已过但湖里没有那天 → `MISSING_MARKET_DATA`(不是 PENDING,也不是 MATURE_10);
    主尺照旧 MATURE,不被拖累。"""
    (tmp_lake_10_sessions / "20260924.parquet").unlink()
    fr, meta = outcome.market_frame("2026-09-10", lake_daily=tmp_lake_10_sessions, today="2026-09-30")
    assert meta["outcome_status"] == outcome.MATURE
    assert meta["t10"] == "20260924"
    assert meta["outcome_status_swing"] == outcome.MISSING_MARKET_DATA
    assert meta["fwd10_verified"] is False


def test_swing_status_is_pending_when_the_main_ruler_is_still_pending(tmp_lake_10_sessions):
    """主尺还没到 T+2 → 10 日尺更不可能成熟:`PENDING_10`(而不是空,也不是失败态)。"""
    fr, meta = outcome.market_frame("2026-09-10", lake_daily=tmp_lake_10_sessions, today="2026-09-11")
    assert fr is None and meta["outcome_status"] == outcome.PENDING_SESSION
    assert meta["outcome_status_swing"] == outcome.PENDING_10


def test_ledger_carries_swing_status_and_t10_columns_and_pending_rows_have_no_value(
        tmp_path, monkeypatch, tmp_lake_10_sessions):
    """`recommendations.csv` 追加 `outcome_status_swing`/`t10` 两列(doc 级逐行广播);
    PENDING_10 的行 `fwd_10_oc` 是空单元格 —— 不是 0,也不是 "nan"。"""
    monkeypatch.chdir(tmp_path)
    _run(tmp_path, "20260910_2100", "2026-09-10")
    root = tmp_path / ws.reports_root() / "scan"
    outcome.fill(reports_root=root, lake_daily=tmp_lake_10_sessions, today="2026-09-15")
    doc = json.loads(outcome.outcome_path("20260910_2100", root).read_text(encoding="utf-8"))
    assert doc["outcome_status"] == outcome.MATURE
    assert doc["outcome_status_swing"] == outcome.PENDING_10 and doc["t10"] == "20260924"
    rows = outcome.load_ledger(root)
    assert rows and all(r["outcome_status_swing"] == "PENDING_10" for r in rows)
    assert all(r["t10"] == "20260924" for r in rows)
    assert all(r["fwd_10_oc"] == "" for r in rows)
    header = (outcome.ledger_root(root) / outcome.LEDGER_CSV).read_text(
        encoding="utf-8").splitlines()[0].split(",")
    assert header[-2:] == ["outcome_status_swing", "t10"]        # 追加在末尾,不插队


def test_fill_revisits_a_main_settled_run_until_the_swing_ruler_matures(
        tmp_path, monkeypatch, tmp_lake_10_sessions):
    """两枚成熟章:主尺已核验的 run 在 D+10 到期时**再补一次** 10 日尺;两枚都齐才永久跳过。
    期间(D+3..D+9)重复 fill 不重写文档,但跳过原因必须写明是在等 10 日尺。"""
    monkeypatch.chdir(tmp_path)
    _run(tmp_path, "20260910_2100", "2026-09-10")
    root = tmp_path / ws.reports_root() / "scan"

    first = outcome.fill(reports_root=root, lake_daily=tmp_lake_10_sessions, today="2026-09-15")
    assert first["filled"] == 1
    waiting = outcome.fill(reports_root=root, lake_daily=tmp_lake_10_sessions, today="2026-09-16")
    assert waiting["filled"] == 0 and waiting["skipped"] == 1
    assert "PENDING_10" in waiting["skip_reasons"]["20260910_2100"]

    matured = outcome.fill(reports_root=root, lake_daily=tmp_lake_10_sessions, today="2026-09-30")
    assert matured["filled"] == 1 and matured["runs"] == ["20260910_2100"]
    rows = {r["code"]: r for r in outcome.load_ledger(root)}
    assert rows["603317"]["outcome_status_swing"] == "MATURE_10"
    # fwd_10_oc = close[T+10] / open[T+1] − 1:T+1=09-11(第 9 个 session,i=8),T+10=09-24(i=17)
    expect = (10.0 + 0.1 * 17) * 1.01 / (10.0 + 0.1 * 8) - 1.0
    assert float(rows["603317"]["fwd_10_oc"]) == pytest.approx(expect, abs=1e-6)

    settled = outcome.fill(reports_root=root, lake_daily=tmp_lake_10_sessions, today="2026-10-09")
    assert settled["filled"] == 0
    assert settled["skip_reasons"]["20260910_2100"] == "already_verified_complete"


def test_swing_revisit_never_rewrites_the_verified_main_ruler_values(
        tmp_path, monkeypatch, tmp_lake_10_sessions):
    """swing 回访只补 10 日尺那几列:主尺已核验的 gap_c1_o2 **冻结**。即便湖在 T+2 那天被
    改写过(数据修订),已核验的隔夜读数也不会被这条路径悄悄换掉 —— 那是 §6 C3 可审阅
    迁移的职责(删掉「冻结主尺」这一层,这条测试必红)。"""
    monkeypatch.chdir(tmp_path)
    _run(tmp_path, "20260910_2100", "2026-09-10")
    root = tmp_path / ws.reports_root() / "scan"
    outcome.fill(reports_root=root, lake_daily=tmp_lake_10_sessions, today="2026-09-15",
                 now="2026-09-15T23:00:00")
    before = {r["code"]: r for r in outcome.load_ledger(root)}

    # 湖修订:T+2(09-14,i=9)的开盘价被改写 —— 若重算主尺,gap_c1_o2 必变。
    _write_swing_day(tmp_lake_10_sessions, "2026-09-14", 9, t2_open_bump=0.5)
    outcome.fill(reports_root=root, lake_daily=tmp_lake_10_sessions, today="2026-09-30",
                 now="2026-09-30T23:00:00")
    after = {r["code"]: r for r in outcome.load_ledger(root)}
    assert after["603317"]["outcome_status_swing"] == "MATURE_10"
    assert after["603317"]["fwd_10_oc"] != ""
    for code in before:
        assert after[code][outcome.MAIN] == before[code][outcome.MAIN]
        assert after[code]["outcome_status"] == before[code]["outcome_status"]
        assert after[code]["computed_at"] == before[code]["computed_at"]


def test_legacy_main_settled_docs_are_backfilled_as_swing_incomplete(
        tmp_path, monkeypatch, tmp_lake_10_sessions):
    """旧行按 swing 未完成回填:本波之前写的已核验文档没有 `outcome_status_swing` 键,
    缺键 ≠ MATURE_10 —— 下一次 fill 必须回访并补齐,而不是当成两枚章都已齐。
    另一个 run 的旧 CSV 行(旧表头没有两列)按空处理,原样保留。"""
    monkeypatch.chdir(tmp_path)
    _run(tmp_path, "20260910_2100", "2026-09-10")
    root = tmp_path / ws.reports_root() / "scan"
    doc = outcome.compute_outcome(root / "20260910_2100", lake_daily=tmp_lake_10_sessions,
                                  today="2026-09-15")
    for key in ("outcome_status_swing", "t10"):
        doc.pop(key, None)
    assert outcome._is_settled(doc)                     # 夹具健全性:主尺那一枚已齐
    outcome.write_outcome(doc, root)
    path = outcome.ledger_root(root) / outcome.LEDGER_CSV
    legacy_cols = [c for c in outcome.LEDGER_COLUMNS if c not in ("outcome_status_swing", "t10")]
    path.write_text(",".join(legacy_cols) + "\n" + ",".join(
        {"run_id": "20260801_2100", "analysis_date": "2026-08-01", "code": "000002",
         "outcome_status": "MATURE"}.get(c, "") for c in legacy_cols) + "\n", encoding="utf-8")

    res = outcome.fill(reports_root=root, lake_daily=tmp_lake_10_sessions, today="2026-09-30")
    assert res["filled"] == 1
    rows = {(r["run_id"], r["code"]): r for r in outcome.load_ledger(root)}
    assert rows[("20260910_2100", "603317")]["outcome_status_swing"] == "MATURE_10"
    legacy = rows[("20260801_2100", "000002")]
    assert legacy["outcome_status_swing"] == "" and legacy["t10"] == ""
    assert legacy["outcome_status"] == "MATURE"


# ─────────── 2026-09-26 复审修补(批 5 review fix pass)───────────


def test_a_crashing_swing_revisit_is_isolated_and_newer_runs_still_fill(
        tmp_path, monkeypatch, tmp_lake_10_sessions):
    """复审 M1:swing 回访是影子数据,它在某个历史 run 上崩了**绝不能**挡住更新 run 的主尺
    回填(`fill` 按目录序逐 run 走,09-17 那次停摆 9 天就是这个形状)。崩掉的 run 记
    `swing_revisit:error:<异常类名>` 进 `skip_reasons`,循环继续。"""
    monkeypatch.chdir(tmp_path)
    _run(tmp_path, "20260910_2100", "2026-09-10")
    root = tmp_path / ws.reports_root() / "scan"
    outcome.fill(reports_root=root, lake_daily=tmp_lake_10_sessions, today="2026-09-15")
    _run(tmp_path, "20260911_2100", "2026-09-11")          # 更新的 run:主尺还没算过

    real = outcome._swing_supplement

    def boom(run, existing, **kw):
        if run.name == "20260910_2100":
            raise RuntimeError("shadow recompute blew up")
        return real(run, existing, **kw)

    monkeypatch.setattr(outcome, "_swing_supplement", boom)
    res = outcome.fill(reports_root=root, lake_daily=tmp_lake_10_sessions, today="2026-09-30")
    assert res["skip_reasons"]["20260910_2100"] == "swing_revisit:error:RuntimeError"
    assert "20260911_2100" in res["runs"]
    doc = json.loads(outcome.outcome_path("20260911_2100", root).read_text(encoding="utf-8"))
    assert doc["outcome_status"] == outcome.MATURE


def test_swing_stamp_needs_most_rows_scored_else_it_stays_missing_and_is_revisited(
        tmp_path, monkeypatch, tmp_lake_10_sessions):
    """复审 M6:T+10 分区「存在」不等于「灌完了」。夜间撞上半截分区时,本 run 的票多数拿不到
    `fwd_10_oc` —— 这时盖 `MATURE_10` 会让这些票永久缺值(`_is_swing_settled` 从此跳过)。
    判据与主尺 `complete` 同一个「多数」:算得出的行不够 → 保持 `MISSING_MARKET_DATA`,
    下一晚分区灌完再回访,补齐后才盖章。"""
    monkeypatch.chdir(tmp_path)
    _run(tmp_path, "20260910_2100", "2026-09-10")
    root = tmp_path / ws.reports_root() / "scan"
    outcome.fill(reports_root=root, lake_daily=tmp_lake_10_sessions, today="2026-09-15")

    # 半截的 T+10 分区(09-24,i=17):只灌进了一只与本 run 无关的票。
    px = 10.0 + 0.1 * 17
    pd.DataFrame([{"ts_code": "000001.SZ", "open": px, "high": px * 1.05, "low": px * 0.95,
                   "close": px * 1.01, "pct_chg": 1.0, "amount": 1.0e8}]
                 ).to_parquet(tmp_lake_10_sessions / "20260924.parquet", index=False)
    partial = outcome.fill(reports_root=root, lake_daily=tmp_lake_10_sessions, today="2026-09-30")
    doc = json.loads(outcome.outcome_path("20260910_2100", root).read_text(encoding="utf-8"))
    assert doc["outcome_status_swing"] == outcome.MISSING_MARKET_DATA, partial
    assert not outcome._is_swing_settled(doc)

    _write_swing_day(tmp_lake_10_sessions, "2026-09-24", 17)     # 分区灌完
    healed = outcome.fill(reports_root=root, lake_daily=tmp_lake_10_sessions, today="2026-10-01")
    assert healed["runs"] == ["20260910_2100"]
    rows = {r["code"]: r for r in outcome.load_ledger(root)}
    assert rows["603317"]["outcome_status_swing"] == outcome.MATURE_10
    assert rows["603317"]["fwd_10_oc"] != ""


def test_candidate_map_alone_cannot_define_sector_benchmark():
    fr = pd.DataFrame({outcome.MAIN: [.04, -.02, 0], 'buyable_c1': True}, index=['a','b','c'])
    result = outcome._relative_columns(fr, {'a': 'S1'})
    assert result['rel_gap_sector'].isna().all()
    assert result['rel_gap_market'].notna().all()
    assert result.attrs['sector_benchmark']['expected_count'] == 3
