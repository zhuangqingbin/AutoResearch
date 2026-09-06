"""D1/D2:ResearchCard v1 —— 词表单源在 agent_output,schema 只管形状,研究规则只 warn 不拒。"""
import pytest

from autoresearch.contracts import agent_output as ao
from autoresearch.contracts.research_card import (
    card_lint_warnings,
    json_schema,
    validate_card,
)


def card_fixture(**changes):
    row = {
        "schema_version": 1, "card_origin": "parser_bridge_v1", "code": "600000",
        "analysis_date": "2026-09-01", "ruler": "gap_c1_o2",
        "dimensions": dict.fromkeys(ao.RUBRIC_DIMENSIONS, "中"),
        "gates": dict.fromkeys(ao.OW_GATES, "UNKNOWN"),
        "early_stop": None, "theses": [], "evidence_refs": [],
        "initial_rating": "Hold", "proposal": "HOLD", "rating_deviation_reason": "",
        "confidence": "低", "scenarios": [], "probability_basis": "not_provided",
        "holding": False, "management": "缺少可验证的隔夜触发,不据此建仓。",
        "target": "未核", "rr": "未核", "summary": "三门证据不足。",
        "entry_veto": [], "exec_lines": [], "tripwires": [], "base_rate_row": None,
        "ev": None, "conviction": None, "as_of": None, "engine": None, "model": None,
    }
    return dict(row, **changes)


def test_fixture_covers_the_full_field_union():
    assert set(card_fixture()) == set(ao.RESEARCH_CARD_FIELDS)


def test_valid_card_passes_unchanged():
    assert validate_card(card_fixture()) == card_fixture()


def test_rubric_dims_are_the_same_object_in_rubric_py():
    from autoresearch.scan.l4 import rubric
    assert rubric._RUBRIC_DIMS is ao.RUBRIC_DIMENSIONS
    assert rubric._OW_GATES is ao.OW_GATES


@pytest.mark.parametrize("field", sorted(ao.RESEARCH_CARD_FIELDS))
def test_every_missing_field_is_rejected(field):
    card = card_fixture()
    del card[field]
    with pytest.raises(ValueError):
        validate_card(card)


def test_card_cannot_override_final_buy_owner():
    with pytest.raises(ValueError):
        validate_card(card_fixture(relative_buy=True))


def test_unknown_gate_is_not_pass():
    from autoresearch.scan.l4.rubric import rubric_rating
    card = validate_card(card_fixture())
    rating, _ = rubric_rating(card["dimensions"], {k: v == "PASS" for k, v in card["gates"].items()})
    assert rating == "Hold"


@pytest.mark.parametrize("changes", [
    {"schema_version": 2}, {"schema_version": True}, {"card_origin": "llm"},
    {"code": "60000"}, {"ruler": "fwd_5_oc"}, {"gates": {"主力真在": "PASS"}},
    {"gates": dict.fromkeys(ao.OW_GATES, True)},
    {"dimensions": {**dict.fromkeys(ao.RUBRIC_DIMENSIONS, "中"), "基本面": "极强"}},
    {"initial_rating": "Strong Buy"}, {"proposal": "buy"}, {"confidence": "很高"},
    {"probability_basis": "calibrated"}, {"early_stop": {"phase": "P9", "reason": "数据不足"}},
    {"early_stop": {"phase": "P3", "reason": "随便"}}, {"holding": 1},
    {"tripwires": ["price < 10"]}, {"exec_lines": [1]}, {"ev": 1.5},
])
def test_invalid_shapes_rejected(changes):
    with pytest.raises(ValueError):
        validate_card(card_fixture(**changes))


def test_bool_gates_are_refused_because_their_direction_is_ambiguous():
    """gate_status 的 bool 是失守、rubric_rating 的 bool 是通过 —— JSON 里只许三态。"""
    with pytest.raises(ValueError):
        validate_card(card_fixture(gates=dict.fromkeys(ao.OW_GATES, False)))


def test_pinned_quality_rule_is_a_warning_not_a_rejection():
    """整卡 ValueError 会让持仓票变 card_missing,E6/brief 就看不到持仓卡(08-26 的疤)。"""
    card = card_fixture(holding=True, dimensions={**dict.fromkeys(ao.RUBRIC_DIMENSIONS, "中"), "偿付": "未核"})
    assert validate_card(card) == card
    assert "PINNED_QUALITY_UNREVIEWED" in card_lint_warnings(card)


def test_full_card_with_no_gate_marks_is_flagged_not_passed():
    assert "FULL_CARD_WITHOUT_GATE_MARKS" in card_lint_warnings(card_fixture())
    assert "FULL_CARD_WITHOUT_GATE_MARKS" not in card_lint_warnings(
        card_fixture(early_stop={"phase": "P3", "reason": "数据不足"}))


# ───────────────────────── 嵌套 ─────────────────────────

def scenarios(bull=.3, base=.5, bear=.2):
    return [{"name": n, "assumptions": "", "return_fraction": r, "probability": p, "invalidators": ""}
            for n, r, p in (("bull", .05, bull), ("base", .0, base), ("bear", -.04, bear))]


def test_subjective_probabilities_must_sum_to_one_and_be_labelled_subjective():
    validate_card(card_fixture(scenarios=scenarios(), probability_basis="subjective"))
    with pytest.raises(ValueError):
        validate_card(card_fixture(scenarios=scenarios(bear=.3), probability_basis="subjective"))
    with pytest.raises(ValueError):
        validate_card(card_fixture(scenarios=scenarios(), probability_basis="not_provided"))


@pytest.mark.parametrize("bad", [float("nan"), -0.1, 1.5])
def test_invalid_probability_values_rejected(bad):
    with pytest.raises(ValueError):
        validate_card(card_fixture(scenarios=scenarios(bull=bad, base=.5, bear=.5 - bad if bad == bad else .5),
                                   probability_basis="subjective"))


def test_missing_one_scenario_is_rejected():
    with pytest.raises(ValueError):
        validate_card(card_fixture(scenarios=scenarios()[:2], probability_basis="subjective"))


def test_evidence_ref_shape_and_hash():
    ref = {"claim_id": "c1", "source_observation_id": "o1", "blob_hash": "a" * 64,
           "rule_version": "claim_support.v2", "support_verdict": "UNKNOWN"}
    validate_card(card_fixture(evidence_refs=[ref]))
    with pytest.raises(ValueError):
        validate_card(card_fixture(evidence_refs=[dict(ref, blob_hash="deadbeef")]))
    with pytest.raises(ValueError):
        validate_card(card_fixture(evidence_refs=[dict(ref, support_verdict="MAYBE")]))


def test_duplicate_thesis_ids_rejected():
    thesis = {"thesis_id": "t1", "statement": "", "observable": "", "source_refs": [],
              "verification_date": "", "falsifier": "", "unknowns": ""}
    validate_card(card_fixture(theses=[thesis]))
    with pytest.raises(ValueError):
        validate_card(card_fixture(theses=[thesis, dict(thesis)]))


def test_json_schema_is_the_two_engine_intersection():
    schema = json_schema()
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(ao.RESEARCH_CARD_FIELDS)
    assert schema["properties"]["gates"]["properties"]["主力真在"]["enum"] == list(ao.GATE_STATES)
    assert schema["properties"]["probability_basis"]["enum"] == list(ao.PROBABILITY_BASES)


def test_card_sources_and_profile_default_keep_production_on_legacy_md():
    from autoresearch.scan.run_profile import scan_profile
    assert scan_profile().card_source == "legacy_md"
    assert scan_profile(card_source="candidate_json").card_source == "candidate_json"
    with pytest.raises(ValueError):
        scan_profile(card_source="json_first")
