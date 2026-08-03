"""事件类型学单测 —— §1.2 D2 的三条升级 + 两条假设纪律。"""
from __future__ import annotations

import json

import pytest

from autoresearch.news import catalog as nc, typed_events as te

# ── 升级 1:一个标题可以同时是几件事 ────────────────────────────


def test_one_title_can_carry_multiple_types():
    event = te.tag("关于对深交所问询函回复暨风险提示的公告")
    assert "regulator_inquiry" in event.event_types
    assert "clarification" in event.event_types


def test_multi_label_is_not_collapsed_to_one():
    event = te.tag("关于回购股份并实施股权激励的公告")
    assert set(event.event_types) >= {"buyback", "equity_incentive"}


def test_no_match_yields_empty_types():
    event = te.tag("关于变更公司注册地址的公告")
    assert event.event_types == [] and event.confidence == 0.0


# ── 升级 2:生命周期(回购/减持/重组必须区分)──────────────────


@pytest.mark.parametrize("title,expected", [
    ("关于回购股份预案的公告", te.PLAN),
    ("关于回购股份进展的公告", te.IN_PROGRESS),
    ("关于回购股份实施完成的公告", te.COMPLETED),
    ("关于终止回购股份方案的公告", te.TERMINATED),
])
def test_lifecycle_is_separated(title, expected):
    assert te.tag(title).lifecycle_status == expected


def test_terminated_outranks_plan_in_the_same_title():
    """「终止回购方案」同时含「终止」与「方案」—— 必须判终止。"""
    assert te.tag("关于终止回购股份方案的公告").lifecycle_status == te.TERMINATED


def test_completed_outranks_progress():
    assert te.tag("回购实施完成进展公告").lifecycle_status == te.COMPLETED


def test_plan_and_completion_are_economically_opposite_and_distinguishable():
    plan = te.tag("关于拟回购股份的公告")
    done = te.tag("关于回购股份实施完成的公告")
    assert plan.lifecycle_status != done.lifecycle_status
    assert "buyback" in plan.event_types and "buyback" in done.event_types


def test_lifecycle_aware_type_without_lifecycle_loses_confidence():
    """该分生命周期却分不出 → 降信心,不是硬判。"""
    vague = te.tag("关于回购股份的公告")
    clear = te.tag("关于回购股份实施完成的公告")
    assert vague.lifecycle_status == te.UNKNOWN_LIFECYCLE
    assert vague.confidence < clear.confidence


def test_non_lifecycle_type_is_unaffected():
    assert te.tag("关于中标重大项目的公告").confidence == 0.9


# ── 升级 3:matched_spans 可复核 ────────────────────────────────


def test_matched_spans_point_at_the_actual_characters():
    title = "关于回购股份的公告"
    event = te.tag(title)
    span = next(s for s in event.matched_spans if s["type"] == "buyback")
    assert title[span["start"]:span["end"]] == span["word"] == "回购"


def test_lifecycle_spans_are_labelled_separately():
    event = te.tag("关于终止回购股份的公告")
    assert any(s["type"] == "__lifecycle__" for s in event.matched_spans)


# ── 方向:否定中性化、不翻转 ───────────────────────────────────


def test_negation_neutralizes_direction_but_keeps_the_type():
    event = te.tag("关于澄清重组传闻的公告")
    assert "restructuring" in event.event_types      # 类型还在
    assert event.direction == ""                     # 方向被中性化
    assert event.ambiguous is True


def test_opposing_types_mark_ambiguous():
    event = te.tag("关于股东减持及公司回购的公告")
    assert event.ambiguous is True


def test_terminating_a_bullish_event_is_flagged_not_flipped():
    """终止一件利多的事 ≈ 利空,但保守不翻转 —— 只标 ambiguous。"""
    event = te.tag("关于终止收购资产的公告")
    assert event.lifecycle_status == te.TERMINATED
    assert event.ambiguous is True


def test_clear_bullish_and_bearish():
    assert te.tag("关于中标重大项目的公告").direction == "利多"
    assert te.tag("关于收到证监会立案告知书的公告").direction == "利空"


# ── 假设纪律:两条都会抛错 ─────────────────────────────────────


def test_same_day_return_entry_is_blocked_for_every_type():
    event = te.tag("关于中标重大项目的公告")
    with pytest.raises(ValueError, match="当日涨幅"):
        te.assert_not_chase_signal(event, same_day_return=0.099)


def test_earnings_type_cannot_be_a_direct_signal():
    event = te.tag("关于 2026 年半年度业绩预告的公告")
    assert te.is_earnings(event)
    with pytest.raises(ValueError, match="业绩类"):
        te.assert_not_chase_signal(event, same_day_return=None)


def test_preregistered_earnings_signal_is_allowed():
    event = te.tag("关于业绩预告的公告")
    te.assert_not_chase_signal(event, same_day_return=None, preregistered=True)


def test_non_earnings_type_needs_no_preregistration_flag():
    te.assert_not_chase_signal(te.tag("关于中标的公告"), same_day_return=None)


def test_assumption_policy_names_the_negative_results():
    policy = te.ASSUMPTION_POLICY
    assert "−0.27%" in policy["earnings"]
    assert "t=−11.91" in policy["all_types"]
    assert "ann_date" in policy["signal_date"]
    assert "B 类" in policy["usage_tier"]


def test_module_exposes_no_prompt_renderer():
    """§1.2 用途分层:渲染进 prompt 已是 B 类 —— 本模块(I 类)不该提供那个入口。"""
    names = [n for n in dir(te) if not n.startswith("_")]
    assert not any("prompt" in n.lower() or "inject" in n.lower() for n in names)


# ── 从目录批量打标 + PIT 覆盖矩阵 ──────────────────────────────


def _catalog(tmp_path, items):
    cat = nc.NewsCatalog(tmp_path / "catalog")
    cat.ingest(items)
    return cat


def _obs(title, **over):
    base = {"source": "cninfo", "title": title, "url": f"https://x/{hash(title) % 9999}",
            "first_seen_ts": "2026-08-01 18:00:00", "available_stage": "L3",
            "scope": nc.SCOPE_MARKET_WIDE, "codes": ("000651",),
            "first_seen_basis": nc.BASIS_OBSERVED}
    base.update(over)
    return nc.Observation(**base)


def test_tag_catalog_produces_a_long_table(tmp_path):
    cat = _catalog(tmp_path, [_obs("关于回购股份实施完成的公告"),
                              _obs("关于股东减持的预案公告")])
    frame = te.tag_catalog(cat)
    assert len(frame) == 2
    assert set(frame.columns) >= {"event_types", "lifecycle_status", "matched_spans"}
    assert json.loads(frame.iloc[0]["matched_spans"])


def test_tag_catalog_respects_pit_cutoff(tmp_path):
    cat = _catalog(tmp_path, [
        _obs("关于回购的公告", first_seen_ts="2026-08-01 10:00:00"),
        _obs("关于减持的公告", first_seen_ts="2026-08-09 10:00:00"),
    ])
    frame = te.tag_catalog(cat, cutoff="2026-08-02 00:00:00", stage="L3")
    assert len(frame) == 1


def test_tag_catalog_on_empty(tmp_path):
    assert not len(te.tag_catalog(nc.NewsCatalog(tmp_path / "empty")))


def test_coverage_blocks_a_thin_type(tmp_path):
    cat = _catalog(tmp_path, [_obs("关于中标的公告")])
    matrix = te.coverage_matrix(cat)
    order = matrix["types"]["order_win"]
    assert order["factor_lab_eligible"] is False
    assert any("market_wide" in r for r in order["blocking_reasons"])


def test_coverage_blocks_snapshot_inferred_first_seen(tmp_path):
    """拿推断时间做因子日期等于自造 PIT —— 必须挡。"""
    items = [_obs(f"关于中标项目{i}的公告", url=f"https://x/w{i}") for i in range(40)]
    items[0] = _obs("关于中标项目0的公告", url="https://x/w0",
                    first_seen_basis=nc.BASIS_SNAPSHOT)
    matrix = te.coverage_matrix(_catalog(tmp_path, items))
    reasons = matrix["types"]["order_win"]["blocking_reasons"]
    assert any("snapshot_inferred" in r for r in reasons)


def test_coverage_blocks_lifecycle_aware_type_with_unknown_lifecycle(tmp_path):
    items = [_obs(f"关于回购股份的公告{i}", url=f"https://x/b{i}") for i in range(40)]
    matrix = te.coverage_matrix(_catalog(tmp_path, items))
    reasons = matrix["types"]["buyback"]["blocking_reasons"]
    assert any("生命周期不可判" in r for r in reasons)


def test_coverage_clears_a_well_covered_type(tmp_path):
    items = [_obs(f"关于中标项目{i}的公告", url=f"https://x/ok{i}") for i in range(40)]
    matrix = te.coverage_matrix(_catalog(tmp_path, items))
    assert matrix["types"]["order_win"]["factor_lab_eligible"] is True
    assert matrix["types"]["order_win"]["blocking_reasons"] == []


def test_selective_observations_do_not_count_toward_market_wide(tmp_path):
    items = [_obs(f"关于中标项目{i}的公告", url=f"https://x/s{i}",
                  scope=nc.SCOPE_SELECTIVE) for i in range(40)]
    matrix = te.coverage_matrix(_catalog(tmp_path, items))
    assert matrix["types"]["order_win"]["market_wide"] == 0
    assert matrix["types"]["order_win"]["factor_lab_eligible"] is False


def test_coverage_on_empty_catalog(tmp_path):
    matrix = te.coverage_matrix(nc.NewsCatalog(tmp_path / "empty"))
    assert matrix["n_observations"] == 0 and matrix["types"] == {}


def test_render_shows_blocking_reasons(tmp_path):
    cat = _catalog(tmp_path, [_obs("关于中标的公告")])
    md = te.render(te.coverage_matrix(cat))
    assert "⛔" in md and "假设纪律" in md


# ── CLI ─────────────────────────────────────────────────────────


def test_cli_tag(capsys):
    assert te.main(["tag", "关于终止回购股份的公告"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["lifecycle_status"] == te.TERMINATED


def test_cli_coverage(tmp_path, capsys):
    cat_root = tmp_path / "catalog"
    nc.NewsCatalog(cat_root).ingest([_obs("关于中标的公告")])
    assert te.main(["coverage", "--catalog-root", str(cat_root),
                    "--json-out", str(tmp_path / "c.json")]) == 0
    assert "覆盖矩阵" in capsys.readouterr().out
