"""F6 Step 1:W3 三格家族的登记必须过 F1 的方案契约 —— 登记不是散文,是机器可校验的冻结方案。"""
import json
from pathlib import Path

from autoresearch.contracts import research_experiment as rx

SPEC = Path(__file__).resolve().parents[2] / "docs" / "research" / "2026-09-07-w3-three-grids-family.spec.json"


def test_w3_family_spec_is_a_valid_frozen_experiment():
    spec = rx.validate_spec(json.loads(SPEC.read_text(encoding="utf-8")))
    assert spec["experiment_family"] == "w3-three-grids"
    assert [h["hypothesis_id"] for h in spec["hypotheses"]] == [
        "w3_first_board_pullback", "w3_late_seal_premium", "w3_institution_seat_5_20d"]


def test_seat_grid_is_labelled_on_sensitivity_rulers_not_the_main_ruler():
    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    seat = next(h for h in spec["hypotheses"] if h["hypothesis_id"] == "w3_institution_seat_5_20d")
    assert "fwd_5_oc" in seat["label"] and "fwd_10_oc" in seat["label"]
    assert set(spec["sensitivity_rulers"]) == {"fwd_5_oc", "fwd_10_oc"}
    assert spec["ruler"] == rx.MAIN_RULER


def test_every_grid_has_a_falsifiable_rejection_and_the_cost_floor():
    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    for h in spec["hypotheses"]:
        assert h["rejection_condition"].strip()
    assert "0.15" in spec["stop_rule"] and "不自动上线" in spec["stop_rule"]
