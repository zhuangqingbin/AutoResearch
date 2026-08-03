"""D4 公告正文单测 —— §1.4 四条边界逐条钉死。"""
from __future__ import annotations

import pandas as pd
import pytest

from autoresearch.news import fulltext as ft


def _ex(**over) -> ft.Excerpt:
    base = {"source_observation_id": "ln_abc#r0", "code": "000651", "page": 3,
            "text": "本次回购已实施完成,累计回购 1,234 万股。",
            "category": "financial_delivery", "doc_hash": "doc1"}
    base.update(over)
    return ft.Excerpt(**base)


# ── 1. 预算按模型输入,不按字节 ────────────────────────────────


def test_chinese_tokens_are_not_estimated_as_bytes():
    """中文一个字往往就是 1 token —— 按字节估会低估两三倍。"""
    text = "本次回购已实施完成"
    assert ft.estimate_tokens(text) == len(text)
    assert ft.estimate_tokens(text) * 3 > len(text.encode("utf-8")) / 3


def test_ascii_is_cheaper_per_character():
    assert ft.estimate_tokens("abcdefghij") < ft.estimate_tokens("一二三四五六七八九十")


def test_budget_judgement_is_a_measured_prompt_delta():
    measured = ft.measure_prompt_delta("基础提示词", [_ex()])
    assert measured["basis"] == "formatted_prompt_delta"
    assert measured["delta_chars"] > 0 and measured["delta_tokens"] > 0
    assert "不用" in measured["note"]


def test_empty_excerpts_produce_zero_delta():
    measured = ft.measure_prompt_delta("基础提示词", [])
    assert measured["delta_chars"] == 0 and measured["delta_tokens"] == 0


def test_over_budget_truncates_by_priority_and_says_so():
    """超限从**低优先级**往高砍,并显式标记 —— 静默丢会让人以为就这点材料。"""
    excerpts = [
        _ex(category="boilerplate", text="样板段" * 200, source_observation_id="o1"),
        _ex(category="financial_delivery", text="业绩兑现段" * 5,
            source_observation_id="o2"),
        _ex(category="risk_disclosure", text="风险段" * 5, source_observation_id="o3"),
    ]
    result = ft.fit_budget(excerpts, token_budget=100)
    kept = {e.category for e in result["kept"]}
    assert "financial_delivery" in kept          # 高优先级保住
    assert result["truncated"] is True
    assert "boilerplate" in result["truncation_notice"]
    assert any(d["category"] == "boilerplate" for d in result["dropped"])


def test_within_budget_keeps_everything_and_says_nothing():
    result = ft.fit_budget([_ex()], token_budget=10_000)
    assert result["truncated"] is False and result["truncation_notice"] == ""
    assert len(result["kept"]) == 1


def test_dropped_entries_carry_a_reason():
    result = ft.fit_budget([_ex(text="长" * 500)], token_budget=10)
    assert result["dropped"][0]["reason"].startswith("超 token 预算")


# ── 2. 假设边界:D4 不是门读全文,且不得引用错误的 60% ────────


def test_assumption_boundary_disclaims_the_wrong_number():
    assert "不是门直接读取全文" in ft.ASSUMPTION_BOUNDARY
    assert "错杀 60%" in ft.ASSUMPTION_BOUNDARY
    assert "cross_calib" in ft.ASSUMPTION_BOUNDARY


def test_experiment_design_forbids_before_after_comparison():
    assert "paired shadow" in ft.EXPERIMENT_DESIGN
    assert "上线前后" in ft.EXPERIMENT_DESIGN


# ── 3. 注入契约:三件套缺一即拒 ────────────────────────────────


def test_valid_excerpt_is_injectable():
    ft.assert_injectable(_ex())


@pytest.mark.parametrize("field_name,value", [
    ("source_observation_id", ""), ("page", 0), ("category", "vibes"),
    ("available_stage", "L3"),
])
def test_missing_or_bad_contract_field_is_rejected(field_name, value):
    with pytest.raises(ft.FulltextError):
        ft.assert_injectable(_ex(**{field_name: value}))


def test_tampered_excerpt_hash_is_rejected():
    item = _ex()
    item.excerpt_hash = "deadbeef"
    with pytest.raises(ft.FulltextError, match="摘录被改过"):
        ft.assert_injectable(item)


def test_hash_is_derived_from_the_text():
    assert _ex().excerpt_hash == ft.excerpt_hash(_ex().text)
    assert _ex(text="别的").excerpt_hash != _ex().excerpt_hash


def test_render_includes_observation_page_and_hash():
    block = ft.render_excerpts([_ex()])
    assert "ln_abc#r0" in block and "p3" in block and _ex().excerpt_hash in block


def test_render_refuses_an_uninjectable_excerpt():
    with pytest.raises(ft.FulltextError):
        ft.render_excerpts([_ex(page=0)])


def test_render_empty_is_empty():
    assert ft.render_excerpts([]) == ""


def test_available_stage_is_l4_so_it_cannot_enter_same_day_l3():
    """D4 在 finalist 之后抓到 —— catalog 的 PIT 据此把它挡在同日 L3 之外。"""
    assert ft.AVAILABLE_STAGE == "L4"
    assert _ex().available_stage == "L4"


# ── 4. 独立 verifier(不复用价格对账器)────────────────────────


def test_verifier_passes_when_the_page_contains_the_excerpt():
    item = _ex()
    docs = {"doc1": {"pages": {3: f"页眉…{item.text}…页脚"}}}
    result = ft.verify_excerpt(item, docs)
    assert result["verdict"] == "PASS"
    assert result["checks"]["text_present"] == "PASS"


def test_verifier_fails_when_the_page_lacks_the_excerpt():
    docs = {"doc1": {"pages": {3: "完全无关的一页"}}}
    result = ft.verify_excerpt(_ex(), docs)
    assert result["verdict"] == "FAIL"
    assert "对不上" in result["reasons"][0]


def test_verifier_fails_on_a_missing_page():
    docs = {"doc1": {"pages": {1: "第一页"}}}
    result = ft.verify_excerpt(_ex(page=3), docs)
    assert result["verdict"] == "FAIL"
    assert result["checks"]["page_present"] == "FAIL"


def test_verifier_unknown_without_a_document():
    """「我们没留档」不等于「它是假的」。"""
    result = ft.verify_excerpt(_ex(), {})
    assert result["verdict"] == "UNKNOWN"
    assert "不判假" in result["reasons"][0]


def test_verifier_accepts_string_page_keys():
    docs = {"doc1": {"pages": {"3": _ex().text}}}
    assert ft.verify_excerpt(_ex(), docs)["verdict"] == "PASS"


def test_verifier_declares_its_boundary_against_price_claims():
    result = ft.verify_excerpt(_ex(), {})
    assert "price_claims" in result["boundary"]
    assert result["verifier"] == "fulltext.verify_excerpt"


def test_module_does_not_import_the_price_verifier():
    """§1.4 核验分工:不得复用价格对账器。"""
    import inspect

    source = inspect.getsource(ft)
    assert "from autoresearch.scan.price_claims" not in source
    assert "import price_claims" not in source


# ── 抓取计划:≤10 只/日 + 选择性范围 ──────────────────────────


def test_plan_caps_at_ten_and_lists_the_excluded():
    plan = ft.plan_fetch([f"{i:06d}" for i in range(14)])
    assert len(plan["codes"]) == ft.MAX_FINALISTS_PER_DAY
    assert len(plan["excluded"]) == 4
    assert plan["excluded_reason"]


def test_plan_under_the_cap_excludes_nobody():
    plan = ft.plan_fetch(["000651", "600519"])
    assert plan["excluded"] == [] and plan["excluded_reason"] == ""


def test_plan_pads_codes_to_six_digits():
    assert ft.plan_fetch(["651"])["codes"] == ["000651"]


def test_plan_declares_selective_scope():
    """finalist 逐票抓取有选择偏差 —— 不得计入市场新闻量。"""
    assert "选择性" in ft.plan_fetch(["000651"])["scope_note"]
    assert "市场新闻量" in ft.plan_fetch(["000651"])["scope_note"]


def test_plan_on_empty_finalists():
    plan = ft.plan_fetch([])
    assert plan["n_finalists"] == 0 and plan["codes"] == []


# ── 渲染 / CLI ─────────────────────────────────────────────────


def test_report_states_both_boundaries():
    md = ft.render_report(ft.plan_fetch(["000651"]), ft.fit_budget([_ex()]))
    assert "不是门直接读取全文" in md and "paired shadow" in md


def test_report_surfaces_truncation():
    budget = ft.fit_budget([_ex(text="长" * 400)], token_budget=10)
    md = ft.render_report(ft.plan_fetch(["000651"]), budget)
    assert "截断" in md


def test_cli_plan(tmp_path, capsys):
    day = tmp_path / "2026-08-01"
    day.mkdir(parents=True)
    pd.DataFrame({"code": ["000651", "600519"]}).to_csv(day / "finalists.csv",
                                                        index=False)
    assert ft.main(["plan", "--scan-dir", str(day),
                    "--json-out", str(tmp_path / "p.json")]) == 0
    assert "计划抓取 **2/2**" in capsys.readouterr().out


def test_cli_plan_without_finalists(tmp_path, capsys):
    day = tmp_path / "2026-08-01"
    day.mkdir(parents=True)
    assert ft.main(["plan", "--scan-dir", str(day)]) == 0
