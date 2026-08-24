"""卡契约 v4(隔夜口径)三段分界 —— T17。

design: 2026-08-05-wave11-ruler-config-l4concurrency-skills-design.md §A5。

语义坍缩(不是文字调整):隔夜窗内唯一实现价 = T+2 开盘。`ruler.SCHEMA_SWITCH_V4` 已从
占位 "9999-12-31" 绑定执行日真值 —— 本文件的边界断言用真实日期字面量(而非重新推导
"今天"),因为这是给"哪天切的 v4"钉一个历史快照,不该随每次跑测试时的日历漂移
(明天/下周重跑本文件,`SCHEMA_SWITCH_V4` 仍应=切换那天,不是"今天")。
"""
from __future__ import annotations

from autoresearch.common import ruler
from autoresearch.scan import self_review

CARD = ("# 决策卡\n\n| 评级 | 目标(EV) | R:R |\n|---|---|---|\n"
        "| Overweight | 120(EV) | 2:1 |\n\n**Rating**: Overweight\n")


# (2026-08-21 learning 层退役:本文件原有的 `buy_ledger._hi_col_for` / `target_calibration` /
#  `calibration_line` 三组断言随该模块删除;留下的是 ruler 分界日快照 + self_review 的 v4
#  卡契约 lint —— 那两件与闭环无关。)


# ───────────────────────── SCHEMA_SWITCH_V4:占位值已绑定执行日真值 ─────────────────────────


def test_schema_switch_v4_bound_to_real_date_not_placeholder():
    """T17 绑执行日真值(`date +%F`);不再是占位 9999-12-31 ——占位值会让 v4 分支永远
    不触发(9999-12-31 之前的每一天都被判"v3 未到期"),这条断言钉死绑定确实发生了。"""
    assert ruler.SCHEMA_SWITCH_V4 == "2026-08-07"
    assert ruler.SCHEMA_SWITCH_V4 != "9999-12-31"


# ───────────────────────── _hi_col_for:三段分界(brief 骨架 + 边界穷举) ─────────────────────────


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
