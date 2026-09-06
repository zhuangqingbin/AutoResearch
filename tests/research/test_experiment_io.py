"""F1:实验方案的冻结、字段契约与尺子字面量漂移锁。

`freeze_spec` 的排他写是整套离线研究里唯一挡得住事后合理化的物理动作:结果看过之后再改
假设,只能换 experiment_id 开新实验。这组测试守的就是「改不动」这件事本身。
"""
import json

import pytest

from autoresearch.contracts import research_experiment as rx
from autoresearch.research import experiment_io as eio


def hypothesis(**changes):
    row = {"hypothesis_id": "h1", "mechanism": "尾盘承接减弱 → 次日开盘缺乏买盘",
           "expected_direction": "negative", "available_at": "2026-09-01T14:45:00+08:00",
           "metric_definition": "close/vwap 的当日分位", "population": "L2-200",
           "label": "gap_c1_o2", "rejection_condition": "日等权均值 CI 上界 < 0"}
    return dict(row, **changes)


def spec(**changes):
    row = {
        "schema_version": 1, "experiment_id": "EXP_20260906_01", "engine": "claude",
        "created_at": "2026-09-06T12:00:00+08:00", "code_sha": "a" * 40,
        "prompt_hashes": {}, "input_manifest_hash": "b" * 64,
        "experiment_family": "late-session-strength", "hypotheses": [hypothesis()],
        "population_rule": "当日 L2-200 且 entry_tradable", "selection_rule": "L3 finalist",
        "ruler": "gap_c1_o2", "sensitivity_rulers": ["fwd_5_oc", "fwd_10_oc"],
        "return_unit": "fraction", "baseline": "同日同人口等权",
        "evidence_mode": "EOD_PROXY", "weighting": "day_equal",
        "cost_model_version": "none", "split": {"train": ["2022-03-01", "2025-01-01"],
                                                "validation": ["2025-01-01", "2026-01-01"],
                                                "test": ["2026-01-01", "2026-09-01"]},
        "purge_rule": "label 区间相交即剔", "embargo_sessions": 2,
        "bootstrap": {"n_boot": 2000, "seed": 20260906}, "multiplicity": "BY",
        "maturity_policy": "scan_days >= 20", "quality_constraints": "GATE1/2/4 通过的 run",
        "stop_rule": "样本终点 2026-12-31,不因读数好看提前停",
    }
    return dict(row, **changes)


# ───────────────────────── ① 字面量不漂移 ─────────────────────────

def test_ruler_literals_do_not_drift():
    """contracts 不能 import common/scan(向上),所以尺子字面量靠这条测试锁死。"""
    from autoresearch.common import ruler
    from autoresearch.scan import populations
    assert ruler.MAIN_RULER == rx.MAIN_RULER
    assert {ruler.REL_MARKET, ruler.REL_SECTOR} <= rx.SENSITIVITY_RULERS
    assert set(populations.UNIVERSE_RULERS) - {ruler.MAIN_RULER} <= rx.SENSITIVITY_RULERS


# ───────────────────────── ② 方案字段 ─────────────────────────

def test_valid_spec_passes():
    assert rx.validate_spec(spec()) == spec()


@pytest.mark.parametrize("field", sorted(rx.REQUIRED_SPEC))
def test_every_missing_field_is_rejected(field):
    row = spec()
    del row[field]
    with pytest.raises(ValueError):
        rx.validate_spec(row)


def test_unknown_field_is_rejected():
    with pytest.raises(ValueError):
        rx.validate_spec(spec(best_variant="v3"))


def test_main_ruler_is_the_only_decision_label():
    with pytest.raises(ValueError):
        rx.validate_spec(spec(ruler="fwd_5_oc"))


def test_sensitivity_rulers_may_be_empty_but_not_unregistered():
    rx.validate_spec(spec(sensitivity_rulers=[]))
    with pytest.raises(ValueError):
        rx.validate_spec(spec(sensitivity_rulers=["fwd_2_oc"]))
    with pytest.raises(ValueError):
        rx.validate_spec(spec(sensitivity_rulers=["fwd_5_oc", "fwd_5_oc"]))


def test_day_equal_weighting_is_mandatory():
    """A 包刚把区间口径统一成日等权;这里换回行等权,两边读数就不可比。"""
    with pytest.raises(ValueError):
        rx.validate_spec(spec(weighting="row_equal"))


def test_return_unit_must_be_fraction():
    with pytest.raises(ValueError):
        rx.validate_spec(spec(return_unit="pp"))


@pytest.mark.parametrize("bad", [-1, 1.5, True, "2"])
def test_invalid_embargo_is_rejected(bad):
    with pytest.raises(ValueError):
        rx.validate_spec(spec(embargo_sessions=bad))


def test_split_must_name_three_ordered_windows():
    with pytest.raises(ValueError):
        rx.validate_spec(spec(split={"train": ["2022-03-01", "2025-01-01"]}))
    with pytest.raises(ValueError):
        rx.validate_spec(spec(split={"train": ["2025-01-01", "2022-03-01"],
                                     "validation": ["2025-01-01", "2026-01-01"],
                                     "test": ["2026-01-01", "2026-09-01"]}))


def test_stop_rule_may_not_be_blank():
    """收益好看时提前停、还叫自己「固定方案」—— 这条字段就是拿来挡它的。"""
    with pytest.raises(ValueError):
        rx.validate_spec(spec(stop_rule="   "))


def test_empty_hypothesis_family_is_rejected():
    with pytest.raises(ValueError):
        rx.validate_spec(spec(hypotheses=[]))
    with pytest.raises(ValueError):
        rx.validate_spec(spec(experiment_family=""))


@pytest.mark.parametrize("field", sorted(rx.REQUIRED_HYPOTHESIS))
def test_hypothesis_missing_any_field_is_rejected(field):
    row = hypothesis()
    del row[field]
    with pytest.raises(ValueError):
        rx.validate_spec(spec(hypotheses=[row]))


def test_hypothesis_needs_a_falsifiable_rejection_condition():
    with pytest.raises(ValueError):
        rx.validate_spec(spec(hypotheses=[hypothesis(rejection_condition="  ")]))


def test_hypothesis_direction_is_a_closed_vocabulary():
    with pytest.raises(ValueError):
        rx.validate_spec(spec(hypotheses=[hypothesis(expected_direction="up")]))


def test_duplicate_hypothesis_ids_are_rejected():
    with pytest.raises(ValueError):
        rx.validate_spec(spec(hypotheses=[hypothesis(), hypothesis()]))


# ───────────────────────── ③ 冻结 ─────────────────────────

def test_spec_cannot_be_silently_replaced(tmp_path):
    path, digest = eio.freeze_spec(tmp_path, {"experiment_id": "fixture",
                                              "hypothesis": "fixed before results"})
    assert len(digest) == 64 and path.exists()
    with pytest.raises(FileExistsError):
        eio.freeze_spec(tmp_path, {"experiment_id": "fixture",
                                   "hypothesis": "changed after results"})
    assert json.loads(path.read_text(encoding="utf-8"))["hypothesis"] == "fixed before results"


def test_digest_is_independent_of_key_order(tmp_path):
    """同一份方案换个书写顺序不该拿到第二个 hash —— 否则输入身份就失去了意义。"""
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    _, first = eio.freeze_spec(tmp_path / "a", {"b": 2, "a": 1})
    _, second = eio.freeze_spec(tmp_path / "b", {"a": 1, "b": 2})
    assert first == second


def test_digest_matches_the_bytes_on_disk(tmp_path):
    _, digest = eio.freeze_spec(tmp_path, spec())
    assert eio.spec_digest(tmp_path) == digest


def test_freeze_validated_spec_refuses_a_bad_plan(tmp_path):
    with pytest.raises(ValueError):
        eio.freeze_validated_spec(tmp_path, spec(weighting="row_equal"))
    assert not (tmp_path / eio.SPEC_NAME).exists()      # 坏方案不留半份文件


def test_freeze_validated_spec_round_trips(tmp_path):
    eio.freeze_validated_spec(tmp_path, spec())
    assert rx.validate_spec(eio.read_spec(tmp_path)) == spec()


def test_experiment_dir_is_created_exclusively(tmp_path):
    made = eio.create_experiment_dir(tmp_path, "EXP_20260906_01")
    assert made.is_dir()
    with pytest.raises(FileExistsError):
        eio.create_experiment_dir(tmp_path, "EXP_20260906_01")


@pytest.mark.parametrize("bad", ["", "../escape", "a/b", "x" * 81, "空格 id"])
def test_invalid_experiment_id_is_rejected(tmp_path, bad):
    with pytest.raises(ValueError):
        eio.create_experiment_dir(tmp_path, bad)
