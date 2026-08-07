"""卡契约 v4(隔夜口径)三段分界 —— T17。

design: 2026-08-05-wave11-ruler-config-l4concurrency-skills-design.md §A5。

语义坍缩(不是文字调整):隔夜窗内唯一实现价 = T+2 开盘。`ruler.SCHEMA_SWITCH_V4` 已从
占位 "9999-12-31" 绑定执行日真值 —— 本文件的边界断言用真实日期字面量(而非重新推导
"今天"),因为这是给"哪天切的 v4"钉一个历史快照,不该随每次跑测试时的日历漂移
(明天/下周重跑本文件,`SCHEMA_SWITCH_V4` 仍应=切换那天,不是"今天")。
"""
from __future__ import annotations

import pandas as pd

from autoresearch.common import ruler
from autoresearch.learning import self_review
from autoresearch.learning.buy_ledger import _hi_col_for, calibration_line, target_calibration

CARD = ("# 决策卡\n\n| 评级 | 目标(EV) | R:R |\n|---|---|---|\n"
        "| Overweight | 120(EV) | 2:1 |\n\n**Rating**: Overweight\n")


# ───────────────────────── SCHEMA_SWITCH_V4:占位值已绑定执行日真值 ─────────────────────────


def test_schema_switch_v4_bound_to_real_date_not_placeholder():
    """T17 绑执行日真值(`date +%F`);不再是占位 9999-12-31 ——占位值会让 v4 分支永远
    不触发(9999-12-31 之前的每一天都被判"v3 未到期"),这条断言钉死绑定确实发生了。"""
    assert ruler.SCHEMA_SWITCH_V4 == "2026-08-07"
    assert ruler.SCHEMA_SWITCH_V4 != "9999-12-31"


# ───────────────────────── _hi_col_for:三段分界(brief 骨架 + 边界穷举) ─────────────────────────


def test_hi_col_three_way():
    """brief 原句范例:三段各断言一次,分界日当天(边界含等号)取 v4 列。"""
    assert _hi_col_for("2026-07-09") == "hi_10_oc"                  # v3 前旧 swing 卡
    assert _hi_col_for("2026-07-15") == "hi_2_oc"                   # v3 超短卡
    assert _hi_col_for(ruler.SCHEMA_SWITCH_V4) == ruler.TOUCH_COL    # v4 起触价=T+2 开


def test_hi_col_boundaries_inclusive_exclusive():
    """三段边界穷举:两个分界日都是"当天起算"(>=),不是"当天仍算旧段"(>)。"""
    assert _hi_col_for("2026-07-09") == "hi_10_oc"                  # v3 分界日前一天
    assert _hi_col_for("2026-07-10") == "hi_2_oc"                   # v3 分界日当天(既有行为)
    assert _hi_col_for("2026-08-06") == "hi_2_oc"                   # v4 分界日前一天,仍是 v3 列
    assert _hi_col_for(ruler.SCHEMA_SWITCH_V4) == ruler.TOUCH_COL    # v4 分界日当天(边界含等号)
    assert _hi_col_for("2026-08-08") == ruler.TOUCH_COL              # v4 分界日之后


def test_hi_col_old_cards_not_wronged():
    """旧卡不冤枉:07-09/07-15 这类历史日期取到的列与本 task 之前(_hi_col_for 只有两段时)
    完全一致 —— 07-09 < v3分界 → hi_10_oc;07-15 ∈ [v3分界, v4分界) → hi_2_oc。加第三段
    (v4 起)后这两个历史日期的取列结果必须逐字节不变,这是「日期分界」手法的全部价值所在。
    """
    assert _hi_col_for("2026-07-09") == "hi_10_oc"
    assert _hi_col_for("2026-07-15") == "hi_2_oc"


# ───────────────────────── target_calibration/calibration_line:v4 双列过渡 ─────────────────────────


def _mk_calib_day(root, date, hi2=0.03, touch=0.25, rating="Hold"):
    """一天一张卡:目标幅固定 0.20(close 基,120/100−1),gap_d1=0 → t_entry=0.20。

    同时落 `hi_2_oc` 与 `ruler.TOUCH_COL`(=gap_c1_o2)两列 —— 供 v4 双列过渡断言用;
    `_hi_col_for` 按 `date` 选列,不看行里有哪些列,pre-v4 日期即便本函数也写了 touch 列,
    也不会被选中(纯日期驱动,不是列存在驱动)。
    """
    d = root / date
    (d / "details").mkdir(parents=True)
    pd.DataFrame([{"code": "000001", "name": "甲", "sector": "半导体"}]).to_csv(
        d / "finalists.csv", index=False)
    (d / "details" / "000001.md").write_text(CARD.replace("Overweight", rating), encoding="utf-8")
    pd.DataFrame([{"code": "000001", "close": 100.0}]).to_csv(
        d / "L1_scored_full.csv", index=False)
    (d / "retro").mkdir()
    row = {"code": "000001", "fwd_1_oo": 0.01, "fwd_5_oc": 0.08, "fwd_10_oc": 0.10,
           "gap_d1": 0.0, "hi_2_oc": hi2}
    row[ruler.TOUCH_COL] = touch
    pd.DataFrame([row]).to_csv(d / "retro" / "attribution.csv", index=False)
    return d


def test_target_calibration_v4_day_uses_touch_col_as_primary(tmp_path):
    """v4 分界日起,主口径(hit_rate/med_mfe)读 `ruler.TOUCH_COL`(=T+2 开盘实现价),
    不再是 hi_2_oc —— touch=0.25≥目标0.20(触达),hi2=0.03<0.20(不触达):两者取值故意
    相反,若主口径仍误读 hi_2_oc,下面的断言会直接翻车。"""
    _mk_calib_day(tmp_path, ruler.SCHEMA_SWITCH_V4, hi2=0.03, touch=0.25)
    st = target_calibration(tmp_path, min_n=1)
    assert st is not None and st["n_mature"] == 1
    assert abs(st["med_mfe"] - 0.25) < 1e-9
    assert st["hit_rate"] == 1.0


def test_target_calibration_v4_day_keeps_hi2_as_separate_reference(tmp_path):
    """hi_2_oc 读数降为参考、双列并陈记账(不删旧读数):`ref_hit_rate`/`ref_n` 与主口径
    分开算 —— 参考口径(hi_2_oc=0.03,不触达)与主口径(touch=0.25,触达)结果相反,
    证明真的是两套独立计算,不是同一个数字改了标签。"""
    _mk_calib_day(tmp_path, ruler.SCHEMA_SWITCH_V4, hi2=0.03, touch=0.25)
    st = target_calibration(tmp_path, min_n=1)
    assert st["ref_n"] == 1
    assert st["ref_hit_rate"] == 0.0
    assert st["hit_rate"] == 1.0                      # 主口径不受参考口径拖累


def test_target_calibration_pre_v4_day_has_no_reference(tmp_path):
    """分界日前:col 恒 != ruler.TOUCH_COL,参考读数天然不产生(ref_n=0)——不是"关掉了",
    是这一天压根没有可供双列对照的东西。"""
    _mk_calib_day(tmp_path, "2026-07-15", hi2=0.03, touch=0.25)
    st = target_calibration(tmp_path, min_n=1)
    assert st["ref_n"] == 0 and st["ref_hit_rate"] is None
    assert st["hit_rate"] == 0.0                       # 主口径此时仍是 hi_2_oc=0.03<0.20


def test_calibration_line_v4_wording_and_reference_clause(tmp_path):
    """当日件文案:「目标带 vs T+2 开盘(v4)」落地,且 hi_2_oc 参考子句双列并陈出现;
    仍保留 📐/触达率 两个老锚点(下游 `_l4_shared_instructions.md` 消费点认这两串)。"""
    _mk_calib_day(tmp_path, ruler.SCHEMA_SWITCH_V4, hi2=0.03, touch=0.25)
    line = calibration_line(target_calibration(tmp_path, min_n=1))
    assert line.startswith("📐") and "触达率" in line
    assert "T+2 开盘" in line and "v4" in line
    assert "参考" in line and "hi_2_oc" in line         # 双列过渡:旧读数仍在场,不是被静默替换


def test_calibration_line_pre_v4_day_has_no_reference_clause(tmp_path):
    """分界日前的窗口:ref_n=0 → 不附空参考子句(没有可参考的第二套读数就不该硬凑一句)。"""
    _mk_calib_day(tmp_path, "2026-07-15", hi2=0.03, touch=0.25)
    line = calibration_line(target_calibration(tmp_path, min_n=1))
    assert "参考" not in line


def test_calibration_line_thin_branch_unchanged():
    """thin 分支文案未动(样本少/禁注一字不改)——本 task 只碰非 thin 分支的措辞。"""
    line = calibration_line({"thin": True, "n_mature": 0, "min_n": 10})
    assert "样本少" in line and "禁注" in line
    assert calibration_line(None) is None


# ───────────────────────── self_review:v4 卡契约口径声明缺失 check ─────────────────────────


def _mk_card(scan_dir, code, text):
    (scan_dir / "details").mkdir(parents=True, exist_ok=True)
    (scan_dir / "details" / f"{code}.md").write_text(text, encoding="utf-8")


def test_card_v4_marker_lint_warns_when_missing_on_switch_day(tmp_path):
    scan_dir = tmp_path / ruler.SCHEMA_SWITCH_V4
    _mk_card(scan_dir, "000001", CARD)                              # 无标记行
    out = self_review.card_v4_marker_lint(scan_dir, ruler.SCHEMA_SWITCH_V4)
    assert len(out) == 1
    assert out[0]["check"] == "卡契约v4·口径声明缺失"
    assert out[0]["severity"] == "warn" and out[0]["code"] == "000001"


def test_card_v4_marker_lint_passes_when_marker_present(tmp_path):
    scan_dir = tmp_path / ruler.SCHEMA_SWITCH_V4
    _mk_card(scan_dir, "000001", CARD + "\n〔卡契约 v4·隔夜 c1→o2〕目标带与 EV 均指 T+2 开盘\n")
    assert self_review.card_v4_marker_lint(scan_dir, ruler.SCHEMA_SWITCH_V4) == []


def test_card_v4_marker_lint_not_triggered_before_switch_day(tmp_path):
    """分界日前的卡不触发 —— 旧卡没有这行,不是它的契约,不该被冤枉。"""
    scan_dir = tmp_path / "2026-07-09"
    _mk_card(scan_dir, "000001", CARD)                              # 同样没写标记行
    assert self_review.card_v4_marker_lint(scan_dir, "2026-07-09") == []


def test_card_v4_marker_lint_day_before_switch_still_exempt(tmp_path):
    """v4 分界日前一天(边界紧邻)仍豁免 —— 只有 >= 分界日才起检查。"""
    scan_dir = tmp_path / "2026-08-06"
    _mk_card(scan_dir, "000001", CARD)
    assert self_review.card_v4_marker_lint(scan_dir, "2026-08-06") == []


def test_card_v4_marker_lint_presence_gated_no_details_dir(tmp_path):
    assert self_review.card_v4_marker_lint(tmp_path / "nope", ruler.SCHEMA_SWITCH_V4) == []


def test_card_v4_marker_lint_wired_into_product_shape_lint(tmp_path):
    """接线回归(FN-1 家训:生产者没接线):`product_shape_lint` 是 `_self_review_banner`
    (assemble 生产路径)的真正入口 —— 必须真把这条 warn 合进来,不是只有独立函数存在
    而未接生产。"""
    scan_dir = tmp_path / ruler.SCHEMA_SWITCH_V4
    _mk_card(scan_dir, "000001", CARD)
    out = self_review.product_shape_lint(scan_dir, ruler.SCHEMA_SWITCH_V4)
    assert any(c["check"] == "卡契约v4·口径声明缺失" and c["code"] == "000001" for c in out)


def test_card_v4_marker_lint_wired_but_silent_before_switch(tmp_path):
    """同一条接线路径在分界日前必须保持沉默(不是"关掉了这条检查",是这一天的卡不归它管)。"""
    scan_dir = tmp_path / "2026-07-09"
    _mk_card(scan_dir, "000001", CARD)
    out = self_review.product_shape_lint(scan_dir, "2026-07-09")
    assert not any(c["check"] == "卡契约v4·口径声明缺失" for c in out)
