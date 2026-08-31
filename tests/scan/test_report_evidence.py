"""banner 聚合 / 评级同向一句依据 / 外源预算与时效契约 —— 鉴别力优先。

design:
- `docs/specs/2026-08-28-summary-slimdown-design.md` §6.6(banner 聚合)、§6.7(一句依据)
- `docs/specs/2026-08-28-external-evidence-expansion-design.md` §6.2(web_budget)、§6.4(时效契约 v2)

按 08-24 mutation 教训写(「改完先问:把这段删掉测试会红吗」),每组都带**对照臂**:
- 聚合:同 key 8 条 → 1 行;`aggregate=False` 仍 8 行且**逐字节**等于历史输出;
- 一句依据:六条链各一例 + 「反向段绝不作为回退」的两个方向;
- 预算:MEASURED 超/不超 / UNMEASURED / 无文件 四态,UNMEASURED **既不判超限也不判合规**;
- 时效:三套契约的 span 互不相同(同一份稿在两个契约下读数必须不同),DATE_ONLY 不得自称 T0。
"""
from __future__ import annotations

import json

from autoresearch.scan import self_review
from autoresearch.scan.l4.parsers import pick_rating_aligned_evidence
from autoresearch.scan.report_model import EVIDENCE_MAX_CHARS

# ══════════════════════════════════════════════════════════════════════════════
# 1. banner 聚合(§6.6)
# ══════════════════════════════════════════════════════════════════════════════

_CAP_DETAIL = ("自报 {} 条 > cap 20 —— 限频是指令级、无强制力(pr_20260714_007);"
               "未自报 = 无法对账,不等于合规")
_CLAIMED = (22, 31, 27, 22, 34, 37, 26, 25)     # 08-26 实测那 8 条


def _result(rows: list[dict]) -> dict:
    return {"failures": rows,
            "n_fail": sum(1 for r in rows if r["severity"] == "fail"),
            "n_warn": sum(1 for r in rows if r["severity"] != "fail")}


def _cap_rows() -> list[dict]:
    return [{"check": "产物形状·intel限频", "severity": "warn",
             "detail": _CAP_DETAIL.format(n), "code": f"00000{i}"}
            for i, n in enumerate(_CLAIMED)]


def test_banner_aggregates_eight_same_key_rows_into_one_line():
    """08-26 病灶原样:同一句话重复 8 遍占 summary 8.5% 字节 → 聚合成 1 行 `×8`。"""
    out = self_review.render_banner(_result(_cap_rows()), aggregate=True)
    body = [ln for ln in out.splitlines() if "**产物形状·intel限频**" in ln]
    assert len(body) == 1, out
    assert "×8" in body[0]
    # 范围摘要必须是**同一数位**的 min–max,且解释性长尾被砍掉(否则聚合等于没聚)
    assert "22–37" in body[0]
    assert "pr_20260714_007" not in body[0]


def test_banner_non_aggregate_keeps_every_row_and_is_byte_identical():
    """appendix A 要用原函数出全文 —— `aggregate=False` 必须逐字节等于历史输出。"""
    rows = _cap_rows()
    out = self_review.render_banner(_result(rows))
    expected = ("> ⚠️ 自检提示 — fail 0 / warn 8\n"
                + "".join(f"> ⚠️ **产物形状·intel限频**:{r['detail']}\n" for r in rows))
    assert out == expected
    assert len(out.splitlines()) == 9          # 顶行 + 8 条,一条都不许少


def test_banner_header_counts_are_per_row_not_per_group():
    """聚合的是**版面**不是事实:顶行 fail/warn 仍逐条计数。"""
    rows = _cap_rows()
    head = self_review.render_banner(_result(rows), aggregate=True).splitlines()[0]
    assert "warn 8" in head and "fail 0" in head


def test_banner_fail_groups_always_precede_warn_groups():
    """fail 是拦发布的那一类,不许被 warn 挤到下面(哪怕它出现得晚)。"""
    rows = [{"check": "warnA", "severity": "warn", "detail": "w1"},
            {"check": "warnA", "severity": "warn", "detail": "w2"},
            {"check": "failZ", "severity": "fail", "detail": "f1"},
            {"check": "failZ", "severity": "fail", "detail": "f2"}]
    body = self_review.render_banner(_result(rows), aggregate=True).splitlines()[1:]
    assert body[0].startswith("> 🛑") and "**failZ**" in body[0]
    assert body[1].startswith("> ⚠️") and "**warnA**" in body[1]


def test_banner_mixed_keys_group_separately_and_keep_first_seen_order():
    rows = (_cap_rows()[:3]
            + [{"check": "intel_window_mismatch", "severity": "warn", "detail": "A 实距 226d"},
               {"check": "intel_window_mismatch", "severity": "warn", "detail": "B 实距 22d"},
               {"check": "产物形状·anns兜底承载", "severity": "warn", "detail": "兜底源承载 3234 行"}])
    body = self_review.render_banner(_result(rows), aggregate=True).splitlines()[1:]
    assert len(body) == 3
    assert "**产物形状·intel限频**" in body[0] and "×3" in body[0]
    assert "**intel_window_mismatch**" in body[1] and "×2" in body[1]
    # n == 1 → 原样输出该行(不许印成 `×1`)
    assert body[2] == "> ⚠️ **产物形状·anns兜底承载**:兜底源承载 3234 行"
    assert "×1" not in body[2]


def test_banner_single_row_group_is_verbatim_even_when_aggregating():
    rows = [{"check": "唯一", "severity": "fail", "detail": "决策卡 3/9 < 80%"}]
    assert (self_review.render_banner(_result(rows), aggregate=True)
            == self_review.render_banner(_result(rows)))


def test_banner_refuses_to_fake_a_range_across_different_sentences():
    """鉴别力:骨架不同的两句话,数字不可比 —— 只写 ×n,**不许**混成假区间。

    把不同句子的数字塞进一个 min–max 正是本仓「量错对象」家族的病。
    """
    rows = [{"check": "k", "severity": "warn", "detail": "自报 22 条 > cap 20"},
            {"check": "k", "severity": "warn", "detail": "实距 226d 超窗"}]
    line = self_review.render_banner(_result(rows), aggregate=True).splitlines()[1]
    assert line == "> ⚠️ **k**:×2"
    assert "22–226" not in line and "226" not in line


def test_banner_range_summary_keeps_constant_slots_and_spans_varying_ones():
    rows = [{"check": "k", "severity": "warn", "detail": f"自报 {n} 条 > cap 20"}
            for n in (37, 22, 25)]
    line = self_review.render_banner(_result(rows), aggregate=True).splitlines()[1]
    assert line == "> ⚠️ **k**:×3(自报 22–37 条 > cap 20)"


def test_banner_identical_details_collapse_without_a_bogus_range():
    rows = [{"check": "k", "severity": "warn", "detail": "全帧 0 只亮旗"} for _ in range(4)]
    line = self_review.render_banner(_result(rows), aggregate=True).splitlines()[1]
    assert line == "> ⚠️ **k**:×4(全帧 0 只亮旗)"


def test_banner_empty_failures_still_returns_empty_string():
    assert self_review.render_banner({"failures": [], "n_fail": 0, "n_warn": 0},
                                     aggregate=True) == ""


# ══════════════════════════════════════════════════════════════════════════════
# 2. 评级同向一句依据(§6.7)
# ══════════════════════════════════════════════════════════════════════════════

_BULLBEAR = "**一行多空**:多:在手订单覆盖两年 ｜ 空:估值已透支到 2028 年\n"
_BULL_ONLY = "**一行多空**:多:在手订单覆盖两年\n"
_BEAR_ONLY = "**一行多空**:空:估值已透支到 2028 年\n"
_EARLY_STOP = "**早停**:停于 P2 ｜ 停因:资金流出\n"
_GATE_SEG = "OW三门:主力真在 **✗** ｜ 业绩真兑现 ✓ ｜ 估值不透支 **✗**\n"
_ROW = {"thesis": "订单周期向上,产能爬坡兑现。第二句不该出现。",
        "risk": "第一大客户占比 62%;第二句不该出现。"}

_LE_HOLD = ("Hold", "Underweight", "Sell", "", "???")
_GE_OW = ("Buy", "Overweight")


def test_evidence_bull_takes_l4_bull_side():
    got = pick_rating_aligned_evidence(_BULLBEAR, "Overweight", _ROW)
    assert got == {"text": "在手订单覆盖两年", "source": "l4_bull", "polarity": "bull"}


def test_evidence_bear_takes_l4_bear_side():
    got = pick_rating_aligned_evidence(_BULLBEAR, "Hold", _ROW)
    assert got == {"text": "估值已透支到 2028 年", "source": "l4_bear", "polarity": "bear"}


def test_evidence_bear_falls_back_to_early_stop_reason():
    got = pick_rating_aligned_evidence(_EARLY_STOP, "Hold", _ROW)
    assert got == {"text": "资金流出", "source": "early_stop", "polarity": "bear"}


def test_evidence_bear_falls_back_to_breached_gates():
    """门柱走 `gate_status` 单一口径 —— 它认识加粗 `**✗**`(17.4% 误判那道疤)。"""
    got = pick_rating_aligned_evidence(_GATE_SEG, "Hold", _ROW)
    assert got["source"] == "gate" and got["polarity"] == "bear"
    assert got["text"] == "三门失守:主力真在、估值不透支"


def test_evidence_bull_falls_back_to_l3_thesis_first_sentence():
    got = pick_rating_aligned_evidence("卡片没有一行多空", "Buy", _ROW)
    assert got == {"text": "订单周期向上,产能爬坡兑现", "source": "l3_thesis", "polarity": "bull"}


def test_evidence_bear_falls_back_to_l3_risk_first_sentence():
    got = pick_rating_aligned_evidence("卡片没有一行多空", "Hold", _ROW)
    assert got == {"text": "第一大客户占比 62%", "source": "l3_risk", "polarity": "bear"}


def test_evidence_all_empty_yields_neutral_dash():
    assert pick_rating_aligned_evidence("", "Hold", None) == {
        "text": "—", "source": "none", "polarity": "neutral"}
    assert pick_rating_aligned_evidence("", "Buy", {}) == {
        "text": "—", "source": "none", "polarity": "neutral"}


def test_evidence_never_prints_a_bull_line_for_a_non_buy_rating():
    """≤Hold 的**所有**分支都不许出多头极性 —— 反向段绝不作为回退(宁缺毋误导)。"""
    cards = ("", _BULLBEAR, _BULL_ONLY, _BEAR_ONLY, _EARLY_STOP, _GATE_SEG,
             _BULL_ONLY + _EARLY_STOP, "卡片没有一行多空")
    for rating in _LE_HOLD:
        for card in cards:
            for row in (None, {}, _ROW, {"thesis": _ROW["thesis"]}):
                got = pick_rating_aligned_evidence(card, rating, row)
                assert got["polarity"] != "bull", (rating, card, row, got)
                assert got["source"] not in ("l4_bull", "l3_thesis"), (rating, card, got)


def test_evidence_never_prints_a_bear_line_for_a_buy_rating():
    cards = ("", _BULLBEAR, _BULL_ONLY, _BEAR_ONLY, _EARLY_STOP, _GATE_SEG,
             _BEAR_ONLY + _EARLY_STOP + _GATE_SEG, "卡片没有一行多空")
    for rating in _GE_OW:
        for card in cards:
            for row in (None, {}, _ROW, {"risk": _ROW["risk"]}):
                got = pick_rating_aligned_evidence(card, rating, row)
                assert got["polarity"] != "bear", (rating, card, row, got)
                assert got["source"] not in ("l4_bear", "early_stop", "gate", "l3_risk")


def test_evidence_hold_with_only_a_bull_segment_prints_dash_not_the_bull_line():
    """这一条是「反向段不回退」的靶心:有多头段、无任何空头素材 → 必须印 `—`。"""
    got = pick_rating_aligned_evidence(_BULL_ONLY, "Hold", {"thesis": "订单周期向上。"})
    assert got == {"text": "—", "source": "none", "polarity": "neutral"}


def test_evidence_buy_with_only_a_bear_segment_prints_dash():
    got = pick_rating_aligned_evidence(_BEAR_ONLY + _EARLY_STOP, "Buy",
                                       {"risk": "客户集中。"})
    assert got == {"text": "—", "source": "none", "polarity": "neutral"}


def test_evidence_source_values_stay_inside_the_declared_domain():
    domain = {"l4_bull", "l4_bear", "early_stop", "gate", "l3_thesis", "l3_risk", "none"}
    for rating in (*_GE_OW, *_LE_HOLD):
        for card in ("", _BULLBEAR, _EARLY_STOP, _GATE_SEG):
            assert pick_rating_aligned_evidence(card, rating, _ROW)["source"] in domain


def test_evidence_char_truncation_boundary_79_80_81():
    """按**字符**(不是字节)截断;80 是全链唯一上限。"""
    assert EVIDENCE_MAX_CHARS == 80
    for n in (79, 80):
        got = pick_rating_aligned_evidence("", "Buy", {"thesis": "涨" * n})
        assert got["text"] == "涨" * n and len(got["text"]) == n
    got = pick_rating_aligned_evidence("", "Buy", {"thesis": "涨" * 81})
    assert len(got["text"]) == EVIDENCE_MAX_CHARS
    assert got["text"] == "涨" * 79 + "…"


def test_evidence_strips_pipes_and_newlines_that_would_split_a_table_cell():
    row = {"risk": "客户 | 集中\n度 **高**"}
    got = pick_rating_aligned_evidence("", "Hold", row)
    assert "|" not in got["text"] and "\n" not in got["text"] and "**" not in got["text"]


# ══════════════════════════════════════════════════════════════════════════════
# 3. 网查预算(doc2 §6.2):真值优先,UNMEASURED 既不判超限也不判合规
# ══════════════════════════════════════════════════════════════════════════════

_INTEL_HEAD = "# 活体情报 — {code} @ 2026-07-27\n\n## 事件段(≤10 行)\n"
_INTEL_DECL = "\n## 声明行\n网查 {n} 条 ｜ T0面=有增量 ｜ as-of ≤ 2026-07-27\n"


def _make_intel(tmp_path, claims: dict[str, int]):
    for code, n in claims.items():
        (tmp_path / f"_l4_intel_{code}.md").write_text(
            _INTEL_HEAD.format(code=code) + _INTEL_DECL.format(n=n), encoding="utf-8")
    return tmp_path


def _budget(tmp_path, **over) -> str:
    obj = {"measurement": "MEASURED", "tool_calls": 9, "search_queries": 26,
           "fetched_urls": 14, "failed_units": 0, "duplicate_urls": 2, "wall_s": 41.2,
           "cap_unit": "search_queries", "cap": 20,
           "cap_enforcement": "OBSERVED_ONLY", "self_report_delta": None}
    obj.update(over)
    p = tmp_path / "web_budget.json"
    p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return str(p)


def test_cap_lint_without_budget_path_keeps_the_old_self_report_rows(tmp_path):
    """默认 `None` = 旧自报路,逐码 3 键行,一个字段都不多。"""
    d = _make_intel(tmp_path, {"000001": 22, "000002": 9})
    assert self_review.intel_query_cap_lint(d, cap=20) == [
        {"code": "000001", "claimed": 22, "cap": 20}]


def test_cap_lint_missing_budget_file_falls_back_to_self_report(tmp_path):
    """给了路径但文件不在 → 不许静默变 0:UNMEASURED,自报只作诊断。"""
    d = _make_intel(tmp_path, {"000001": 22})
    got = self_review.intel_query_cap_lint(d, cap=20, web_budget_path=tmp_path / "nope.json")
    assert [r["kind"] for r in got] == ["unmeasured"]
    assert got[0]["measurement"] == "UNMEASURED" and got[0]["used"] is None
    assert got[0]["self_report_total"] == 22


def test_cap_lint_measured_over_cap_uses_the_real_value(tmp_path):
    d = _make_intel(tmp_path, {"000001": 9, "000002": 9})       # 自报都不超
    got = self_review.intel_query_cap_lint(d, cap=20, web_budget_path=_budget(tmp_path))
    assert len(got) == 1 and got[0]["kind"] == "measured"
    assert got[0]["used"] == 26 and got[0]["cap"] == 20 and got[0]["unit"] == "search_queries"
    assert got[0]["code"] is None                                # run 级,不栽给任何一只
    assert got[0]["self_report_delta"] == 18 - 26                # 自报降级为诊断项


def test_cap_lint_measured_under_cap_emits_nothing(tmp_path):
    """对照臂:真值不超 → 一条都不出(否则「超限」这条永远为真 = 没有鉴别力)。"""
    d = _make_intel(tmp_path, {"000001": 31})                   # 自报超,真值不超
    got = self_review.intel_query_cap_lint(
        d, cap=20, web_budget_path=_budget(tmp_path, search_queries=12))
    assert got == []


def test_cap_lint_unmeasured_is_neither_over_nor_compliant(tmp_path):
    d = _make_intel(tmp_path, {"000001": 31})
    got = self_review.intel_query_cap_lint(
        d, cap=20, web_budget_path=_budget(tmp_path, measurement="UNMEASURED",
                                           search_queries=0))
    assert [r["kind"] for r in got] == ["unmeasured"]
    assert got[0]["used"] is None                                # 0 ≠ 量不到


def test_cap_lint_missing_any_schema_key_is_unmeasured(tmp_path):
    """严格按 11 个键读:缺任一键 = UNMEASURED(缺键不得以「未超」蒙混)。"""
    d = _make_intel(tmp_path, {"000001": 9})
    for drop in self_review.WEB_BUDGET_KEYS:
        _budget(tmp_path)                                        # 每轮重建完整 schema
        obj = json.loads((tmp_path / "web_budget.json").read_text(encoding="utf-8"))
        obj.pop(drop)
        (tmp_path / "web_budget.json").write_text(json.dumps(obj), encoding="utf-8")
        got = self_review.intel_query_cap_lint(
            d, cap=20, web_budget_path=tmp_path / "web_budget.json")
        assert [r["kind"] for r in got] == ["unmeasured"], drop


def test_cap_lint_is_presence_gated_on_intel_sheets(tmp_path):
    assert self_review.intel_query_cap_lint(
        tmp_path, cap=20, web_budget_path=_budget(tmp_path)) == []


def test_product_shape_lint_wires_the_budget_path_through(tmp_path):
    """接线回归(FN-1 家训:生产者没接线 = 死了也像活着)。"""
    d = _make_intel(tmp_path, {"000001": 9})

    def checks(rows):
        return [r["check"] for r in rows]

    over = self_review.product_shape_lint(d, "2026-07-27",
                                          web_budget_path=_budget(tmp_path))
    assert "产物形状·intel限频" in checks(over)
    assert any("真值 26 search_queries > cap 20" in r["detail"]
               for r in over if r["check"] == "产物形状·intel限频")

    un = self_review.product_shape_lint(
        d, "2026-07-27",
        web_budget_path=_budget(tmp_path, measurement="UNMEASURED", search_queries=0))
    assert "intel_budget_unmeasured" in checks(un)
    assert "产物形状·intel限频" not in checks(un)                # 量不到 ≠ 超限
    assert any("量不到 ≠ 没超" in r["detail"]
               for r in un if r["check"] == "intel_budget_unmeasured")

    ok = self_review.product_shape_lint(
        d, "2026-07-27", web_budget_path=_budget(tmp_path, search_queries=3))
    assert "产物形状·intel限频" not in checks(ok)
    assert "intel_budget_unmeasured" not in checks(ok)

    old = self_review.product_shape_lint(d, "2026-07-27")        # 不传 = 旧自报路
    assert "intel_budget_unmeasured" not in checks(old)


# ══════════════════════════════════════════════════════════════════════════════
# 4. 时效契约 v2(doc2 §6.4):三套 span + DATE_ONLY
# ══════════════════════════════════════════════════════════════════════════════

_EV_HEAD = ("# 活体情报 — 000001 甲 @ 2026-07-27\n\n## 事件段(≤10 行)\n"
            "| 日期 | 时效窗 | 事件 | 源 | 净分 |\n|---|---|---|---|---|\n")
_EV_DECL = "\n## 声明行\n网查 9 条 ｜ T0面=有增量 ｜ as-of ≤ 2026-07-27\n"


def _events(tmp_path, body: str, code: str = "000001"):
    (tmp_path / f"_l4_intel_{code}.md").write_text(_EV_HEAD + body + _EV_DECL,
                                                   encoding="utf-8")
    return tmp_path


def test_contract_v1_is_the_default_and_unchanged(tmp_path):
    d = _events(tmp_path,
                "| 2026-07-27 | T0 | 盘后公告 | http://x | +2 |\n"
                "| 2026-07-26 | 24h | 行业提价 | http://y | +1 |\n"
                "| 2026-07-23 | 背景 | 调研纪要 | http://z | +0.5 |\n")
    assert self_review.intel_recency_lint(d, "2026-07-27") == []
    assert (self_review.intel_recency_lint(d, "2026-07-27")
            == self_review.intel_recency_lint(d, "2026-07-27", "intel_v1"))


def test_contract_v2_full_widens_the_background_window(tmp_path):
    """同一份稿,两套契约必须读出**不同**结果 —— 否则参数化等于没做。

    30 天前的「背景」:v1 的背景窗只到 7 天(超窗 + 净分未衰减),v2_full 到 60 天(合规)。
    """
    d = _events(tmp_path, "| 2026-06-27 | 背景 | 一月前的产业调研 | http://z | +0.5 |\n")
    v1 = [r["check"] for r in self_review.intel_recency_lint(d, "2026-07-27", "intel_v1")]
    assert "intel_window_mismatch" in v1 and "intel_stale_score" in v1
    assert self_review.intel_recency_lint(d, "2026-07-27", "intel_v2_full") == []


def test_contract_v2_full_recognises_its_own_window_vocabulary(tmp_path):
    d = _events(tmp_path,
                "| 2026-07-26 | 24h | 昨夜公告 | http://x | +1 |\n"
                "| 2026-07-22 | 本周 | 五日前中标 | http://y | +1 |\n"
                "| 2026-07-05 | 本月 | 三周前定增 | http://z | +0.5 |\n")
    assert self_review.intel_recency_lint(d, "2026-07-27", "intel_v2_full") == []
    # 对照臂:同一份稿拿 v1 的尺去量,「本月」那行(22 天前、净分 +0.5)就成了未衰减 ——
    # 这正是「量错对象」:v2 的月窗系数 0.5 合法,v1 的周窗外必须归零。
    assert [r["check"] for r in self_review.intel_recency_lint(d, "2026-07-27", "intel_v1")] \
        == ["intel_stale_score"]


def test_contract_v2_macro_has_a_48h_window_the_others_lack(tmp_path):
    """48h 只在宏观契约里存在,且跨度 (0,2):3 天前的 48h 行必须被逮到。"""
    d = _events(tmp_path,
                "| 2026-07-24 | 48h | 三天前的美联储发言 | http://x | +1 |\n"
                "| 2026-07-07 | 本月 | 月内 CPI | http://y | +0.5 |\n")
    macro = self_review.intel_recency_lint(d, "2026-07-27", "intel_v2_macro")
    assert [r["check"] for r in macro] == ["intel_window_mismatch"]
    assert "实距 3d" in macro[0]["detail"]
    # v2_full 没有 48h 窗 → 这一行无尺可量,不报(不许拿别的契约的尺硬套)
    assert self_review.intel_recency_lint(d, "2026-07-27", "intel_v2_full") == []


def test_contract_v2_macro_accepts_a_two_day_old_48h_event(tmp_path):
    d = _events(tmp_path, "| 2026-07-25 | 48h | 两天前的议息 | http://x | +1 |\n")
    assert self_review.intel_recency_lint(d, "2026-07-27", "intel_v2_macro") == []


def test_unknown_contract_falls_back_to_v1_but_says_so(tmp_path):
    """静默换尺 = 量错对象;降级必须留痕。"""
    d = _events(tmp_path, "| 2026-07-27 | T0 | 盘后公告 | http://x | +2 |\n")
    got = self_review.intel_recency_lint(d, "2026-07-27", "intel_v9_typo")
    assert [r["check"] for r in got] == ["intel_contract_unknown"]


def test_date_only_row_may_not_claim_t0(tmp_path):
    d = _events(tmp_path,
                "| 2026-07-27 / DATE_ONLY | T0 | 只有日期的盘后公告 | http://x | +2 |\n")
    got = self_review.intel_recency_lint(d, "2026-07-27")
    assert [r["check"] for r in got] == ["intel_window_date_only_t0"]
    assert "DATE_ONLY" in got[0]["detail"]


def test_exact_timestamp_row_may_claim_t0(tmp_path):
    """对照臂:带时刻的行照旧合法(否则这条检查恒为真)。"""
    d = _events(tmp_path,
                "| 2026-07-27 18:12 / EXACT | T0 | 盘后公告 | http://x | +2 |\n")
    assert self_review.intel_recency_lint(d, "2026-07-27") == []


def test_bare_date_row_is_not_retro_flagged_as_date_only(tmp_path):
    """v1 存量稿(裸日期、无 time_quality)判不出质量 → 不报(分不清的地方就不报)。"""
    d = _events(tmp_path, "| 2026-07-27 | T0 | 盘后公告 | http://x | +2 |\n")
    assert self_review.intel_recency_lint(d, "2026-07-27") == []


def test_date_only_is_fine_on_a_day_granularity_window(tmp_path):
    """只有 T0 声称精确时点;24h 是日粒度窗,DATE_ONLY 合法。"""
    d = _events(tmp_path,
                "| 2026-07-26 / DATE_ONLY | 24h | 昨日公告 | http://x | +1 |\n")
    assert self_review.intel_recency_lint(d, "2026-07-27") == []


def test_v2_first_cell_format_does_not_break_the_other_two_checks(tmp_path):
    """新首格格式必须仍能被时效/衰减两条检查读到(解析放宽 ≠ 检查失明)。"""
    d = _events(tmp_path,
                "| 2026-07-01 / DATE_ONLY | 背景 | 26 天前旧闻 | http://x | +2 |\n")
    checks = [r["check"] for r in self_review.intel_recency_lint(d, "2026-07-27")]
    assert "intel_window_mismatch" in checks and "intel_stale_score" in checks
