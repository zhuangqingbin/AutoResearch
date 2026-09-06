"""F7:概率记分与复核错误重合 —— 缺标注不是 0,Brier 不是校准度。"""
import pytest

from autoresearch.research.probability_eval import (
    DEFAULT_EDGES,
    error_overlap,
    probability_metrics,
)

EVENT = "net-positive-v1"


def row(p, y, event_id=EVENT):
    return {"event_id": event_id, "p": p, "y": y}


def test_probability_score_keeps_missing_separate():
    result = probability_metrics([row(.8, 1), row(.2, 0), row(.9, None)], event_id=EVENT)
    assert result["brier"] == pytest.approx(.04)
    assert result["n"] == 2 and result["missing"] == 1


def test_missing_outcome_is_not_a_loss():
    """缺成交/缺费用记成 y=0,等于给一个没发生的赌局判输 —— Brier 会凭空变差。"""
    with_missing = probability_metrics([row(.9, 1), row(.9, None)], event_id=EVENT)
    as_zero = probability_metrics([row(.9, 1), row(.9, 0)], event_id=EVENT)
    assert with_missing["brier"] == pytest.approx(.01)
    assert as_zero["brier"] == pytest.approx(.41)


def test_all_missing_gives_no_score_not_a_perfect_one():
    result = probability_metrics([row(.5, None), row(.5, None)], event_id=EVENT)
    assert result["n"] == 0 and result["missing"] == 2
    assert result["brier"] is None and result["base_rate"] is None


def test_empty_rows_are_empty_not_an_error():
    result = probability_metrics([], event_id=EVENT)
    assert result["n"] == 0 and result["brier"] is None
    assert [b["n"] for b in result["bins"]] == [0] * (len(DEFAULT_EDGES) - 1)


def test_mixed_event_targets_are_refused():
    with pytest.raises(ValueError):
        probability_metrics([row(.8, 1), row(.2, 0, event_id="other")], event_id=EVENT)


def test_extreme_probabilities_are_allowed_and_scored():
    assert probability_metrics([row(0, 0), row(1, 1)], event_id=EVENT)["brier"] == 0.0
    assert probability_metrics([row(0, 1), row(1, 0)], event_id=EVENT)["brier"] == 1.0


def test_p_equal_one_lands_in_the_last_bin():
    """最后一箱右闭 —— 否则 p=1.0 会掉出所有箱子,可靠性表少一行样本还看不出来。"""
    result = probability_metrics([row(1.0, 1)], event_id=EVENT)
    assert result["bins"][-1]["n"] == 1
    assert sum(b["n"] for b in result["bins"]) == result["n"]


def test_bins_partition_every_scored_row():
    rows = [row(p, 1) for p in (0.0, 0.19, 0.2, 0.5, 0.79, 0.8, 0.99, 1.0)]
    result = probability_metrics(rows, event_id=EVENT)
    assert sum(b["n"] for b in result["bins"]) == result["n"] == len(rows)


def test_empty_bin_reports_none_not_zero():
    """空箱的观测频率是「不知道」,写 0 会在可靠性图上画出一条不存在的完美点。"""
    result = probability_metrics([row(.9, 1)], event_id=EVENT)
    assert result["bins"][0]["observed_rate"] is None
    assert result["bins"][0]["mean_p"] is None


def test_base_rate_is_reported_next_to_brier():
    """只看 Brier 分不出「概率有区分力」还是「基率本来就低」。"""
    result = probability_metrics([row(.1, 0), row(.1, 0), row(.1, 1)], event_id=EVENT)
    assert result["base_rate"] == pytest.approx(1 / 3)


@pytest.mark.parametrize("bad", [1.5, -0.1, float("nan"), float("inf"), "0.5"])
def test_invalid_probability_is_rejected(bad):
    with pytest.raises(ValueError):
        probability_metrics([row(bad, 1)], event_id=EVENT)


@pytest.mark.parametrize("bad", [2, -1, True, False, "1"])
def test_invalid_outcome_is_rejected(bad):
    with pytest.raises(ValueError):
        probability_metrics([row(.5, bad)], event_id=EVENT)


@pytest.mark.parametrize("edges", [(0, 1, 0.5), (0.1, 1), (0, 0.9), (0.5,), (0, 0.5, 0.5, 1)])
def test_invalid_bin_edges_are_rejected(edges):
    with pytest.raises(ValueError):
        probability_metrics([row(.5, 1)], event_id=EVENT, edges=edges)


# ───────────────────────── 复核错误重合 ─────────────────────────

def test_identical_reviewers_have_full_error_overlap():
    labelled = {"a": "PASS", "b": "FAIL", "c": "PASS"}
    wrong = {"a": "FAIL", "b": "FAIL", "c": "PASS"}
    got = error_overlap(wrong, dict(wrong), labelled)
    assert got["n_common"] == 3 and got["both_wrong"] == 1
    assert got["error_jaccard"] == pytest.approx(1.0)


def test_disjoint_errors_have_zero_overlap():
    labelled = {"a": "PASS", "b": "FAIL"}
    got = error_overlap({"a": "FAIL", "b": "FAIL"}, {"a": "PASS", "b": "PASS"}, labelled)
    assert got["both_wrong"] == 0 and got["error_jaccard"] == 0.0


def test_no_errors_at_all_is_null_not_independence():
    """两人都全对时说不出独立与否 —— 分母为空,记 null。"""
    labelled = {"a": "PASS"}
    got = error_overlap({"a": "PASS"}, {"a": "PASS"}, labelled)
    assert got["error_jaccard"] is None and got["n_common"] == 1


def test_only_commonly_labelled_cases_count():
    """一方没看过的案例不能算进重合分母,否则「覆盖少」会伪装成「更独立」。"""
    labelled = {"a": "PASS", "b": "PASS"}
    got = error_overlap({"a": "FAIL", "b": "FAIL"}, {"a": "FAIL"}, labelled)
    assert got["n_common"] == 1
    assert got["both_wrong"] == 1 and got["error_jaccard"] == pytest.approx(1.0)


def test_unlabelled_cases_are_ignored():
    got = error_overlap({"a": "FAIL", "z": "FAIL"}, {"a": "FAIL", "z": "PASS"},
                        {"a": "PASS"})
    assert got["n_common"] == 1 and got["n_wrong_first"] == 1
