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


def test_execution_probability_only_scores_declared_probability():
    from autoresearch.research import probability_eval as pe
    from autoresearch.research.execution_ledger import execution_plan_hash
    from tests.research.test_execution_ledger import overnight_fills, plan
    declaration = {"event_id": pe.PLANNED_OVERNIGHT_EVENT, "p": .8,
                   "declared_at": "2026-09-01T14:00:00+08:00", "plan_hash": execution_plan_hash(plan())}
    result = pe.execution_probability_row(declaration, plan=plan(), fills=overnight_fills())
    assert result["p"] == .8 and result["y"] == 1 and result["missing_reasons"] == []
    metrics = probability_metrics([result], event_id=pe.PLANNED_OVERNIGHT_EVENT)
    assert metrics["brier"] == pytest.approx(.04) and metrics["base_rate"] == 1
    declaration.pop("p")
    declaration["conviction"] = 80
    result = pe.execution_probability_row(declaration, plan=plan(), fills=overnight_fills())
    assert result["p"] is None and result["y"] is None
    assert "MISSING_DECLARED_PROBABILITY" in result["missing_reasons"]


def test_execution_probability_missing_or_late_evidence_stays_unlabelled():
    from autoresearch.research import probability_eval as pe
    from autoresearch.research.execution_ledger import execution_plan_hash
    from tests.research.test_execution_ledger import overnight_fills, plan
    declaration = {"event_id": pe.PLANNED_OVERNIGHT_EVENT, "p": .8,
                   "declared_at": "2026-09-02T14:00:00+08:00", "plan_hash": execution_plan_hash(plan())}
    result = pe.execution_probability_row(declaration, plan=plan(), fills=overnight_fills())
    assert result["y"] is None and "PROBABILITY_NOT_PREDECLARED" in result["missing_reasons"]
    declaration["declared_at"] = "2026-09-01T14:00:00+08:00"
    result = pe.execution_probability_row(declaration, plan=plan(), fills=overnight_fills()[:1])
    assert result["y"] is None and "INCOMPLETE_EXIT" in result["missing_reasons"]


def test_execution_probability_wrong_event_and_invalid_p_are_not_scored():
    from autoresearch.research import probability_eval as pe
    from autoresearch.research.execution_ledger import execution_plan_hash
    from tests.research.test_execution_ledger import overnight_fills, plan
    declaration = {"event_id": "some_other_event", "p": .8,
                   "declared_at": "2026-09-01T14:00:00+08:00", "plan_hash": execution_plan_hash(plan())}
    result = pe.execution_probability_row(declaration, plan=plan(), fills=overnight_fills())
    assert result["y"] is None and "EVENT_NOT_DECLARED" in result["missing_reasons"]
    declaration["p"] = 80
    with pytest.raises(ValueError, match="probability"):
        pe.execution_probability_row(declaration, plan=plan(), fills=overnight_fills())


def test_execution_probability_zero_net_is_zero_outcome():
    from autoresearch.research import probability_eval as pe
    from autoresearch.research.execution_ledger import execution_plan_hash
    from tests.research.test_execution_ledger import overnight_fills, plan
    fills = overnight_fills()
    fills[1]["amount"] = "1010"
    result = pe.execution_probability_row(
        {"event_id": pe.PLANNED_OVERNIGHT_EVENT, "p": .8,
         "declared_at": "2026-09-01T14:00:00+08:00", "plan_hash": execution_plan_hash(plan())}, plan=plan(), fills=fills)
    assert result["y"] == 0


def test_execution_probability_requires_a_bound_plan_hash():
    from autoresearch.research import probability_eval as pe
    from tests.research.test_execution_ledger import overnight_fills, plan
    declaration = {"event_id": pe.PLANNED_OVERNIGHT_EVENT, "p": .8,
                   "declared_at": "2026-09-01T14:00:00+08:00"}
    result = pe.execution_probability_row(declaration, plan=plan(), fills=overnight_fills())
    assert result["y"] is None
    assert "MISSING_DECLARED_PLAN_HASH" in result["missing_reasons"]


@pytest.mark.parametrize("field,value", [
    ("code", "000001"), ("execution_mode", "SNAPSHOT_SIMULATED"),
    ("cost_model_version", "other_cost_v1"), ("ruler", "intraday"),
    ("entry_window_start", "2026-09-01T14:58:00+08:00"),
    ("entry_window_end", "2026-09-01T15:01:00+08:00"),
    ("exit_window_start", "2026-09-02T09:24:00+08:00"),
    ("exit_window_end", "2026-09-02T09:32:00+08:00"),
])
def test_execution_probability_cannot_reuse_p_for_changed_plan_parameters(field, value):
    from autoresearch.research import probability_eval as pe
    from autoresearch.research.execution_ledger import execution_plan_hash
    from tests.research.test_execution_ledger import overnight_fills, plan
    declaration = {"event_id": pe.PLANNED_OVERNIGHT_EVENT, "p": .8,
                   "declared_at": "2026-09-01T14:00:00+08:00",
                   "plan_hash": execution_plan_hash(plan())}
    result = pe.execution_probability_row(declaration, plan=dict(plan(), **{field: value}),
                                          fills=overnight_fills())
    assert result["y"] is None and "DECLARED_PLAN_HASH_MISMATCH" in result["missing_reasons"]


def test_rehashed_month_later_exit_cannot_label_the_overnight_event():
    from autoresearch.research import probability_eval as pe
    from autoresearch.research.execution_ledger import execution_plan_hash
    from tests.research.test_execution_ledger import overnight_fills, plan
    changed = plan()
    changed.update(exit_window_start="2026-10-02T09:25:00+08:00",
                   exit_window_end="2026-10-02T09:31:00+08:00")
    fills = overnight_fills()
    fills[1]["trade_date"] = "2026-10-02"
    declaration = {"event_id": pe.PLANNED_OVERNIGHT_EVENT, "p": .8,
                   "declared_at": "2026-09-01T14:00:00+08:00",
                   "plan_hash": execution_plan_hash(changed)}
    result = pe.execution_probability_row(declaration, plan=changed, fills=fills)
    assert result["y"] is None
    assert "INVALID_D1_D2_CALENDAR" in result["missing_reasons"]


def test_clustered_probability_baseline_never_uses_test_labels():
    from autoresearch.research import probability_eval as pe

    rows = [
        {**row(0.8, 1), "analysis_date": "2026-01-01", "case_id": "a"},
        {**row(0.8, 1), "analysis_date": "2026-01-01", "case_id": "a"},
        {**row(0.2, 0), "analysis_date": "2026-01-02", "case_id": "b"},
        {**row(0.9, None), "analysis_date": "2026-01-02", "case_id": "c"},
    ]
    train = [{**row(0.5, 0), "analysis_date": "2025-01-01", "case_id": "train"}]
    result = pe.clustered_probability_metrics(
        rows,
        event_id=EVENT,
        training_rows=train,
        split={
            "train": ["2025-01-01", "2025-02-01"],
            "validation": ["2025-02-01", "2026-01-01"],
            "test": ["2026-01-01", "2026-02-01"],
        },
    )
    assert result["brier"] == pytest.approx(0.04)
    assert result["n"] == 2 and result["missing"] == 1 and result["n_clusters"] == 2
    assert result["n_repeated"] == 1 and result["baseline_probability"] == 0
    assert result["baseline_brier"] == 0.5
    with pytest.raises(ValueError, match="training"):
        pe.clustered_probability_metrics(
            rows,
            event_id=EVENT,
            training_rows=rows,
            split={
                "train": ["2025-01-01", "2025-02-01"],
                "validation": ["2025-02-01", "2026-01-01"],
                "test": ["2026-01-01", "2026-02-01"],
            },
        )


def test_error_overlap_one_perfect_reviewer_is_zero_not_undefined():
    result = error_overlap({"a": 1}, {"a": 0}, {"a": 1})
    assert result["error_jaccard"] == 0
    assert result["first_error_rate"] == 0 and result["second_error_rate"] == 1


def test_frozen_training_baseline_is_derived_from_imported_trades(tmp_path):
    import csv

    from autoresearch.common.atomic import sha256_file
    from autoresearch.research import probability_eval as pe
    from tests.research.test_execution_audit import TRADE_COLS
    from tests.research.test_execution_ledger import overnight_fills, plan

    trades = tmp_path / "synthetic-trades.csv"
    with trades.open("w") as stream:
        writer = csv.DictWriter(stream, fieldnames=TRADE_COLS)
        writer.writeheader()
        for f in overnight_fills():
            writer.writerow(
                {
                    **{k: f.get(k, "") for k in TRADE_COLS},
                    "account": "synthetic",
                    "trade_id": f["fill_id"],
                    "source_kind": "synthetic",
                    "source_file": "fixture.csv",
                }
            )
    value = {"trades_path": str(trades), "trades_sha256": sha256_file(trades), "plans": [plan()]}
    rows = pe.import_training_outcomes(value)
    assert rows[0]["y"] == 1 and rows[0]["analysis_date"] == "2026-08-31"
    trades.write_text("changed")
    with pytest.raises(ValueError, match="hash"):
        pe.import_training_outcomes(value)


def test_new_probability_sizing_binding_preserves_old_interface_and_rejects_changes():
    from autoresearch.research import probability_eval as pe
    from autoresearch.research.execution_ledger import execution_plan_hash
    from tests.research.test_execution_ledger import overnight_fills, plan

    declared = {
        "event_id": pe.PLANNED_OVERNIGHT_EVENT,
        "p": 0.8,
        "declared_at": "2026-08-31T00:00:00Z",
        "plan_hash": execution_plan_hash(plan()),
        "sizing_hash": pe.execution_sizing_hash(plan(), qty="100", weight=".2"),
    }
    sizing = {"qty": "100", "weight": ".2", "initial_capital": "10000"}
    assert (
        pe.execution_probability_row(declared, plan=plan(), fills=overnight_fills(), sizing=sizing)[
            "y"
        ]
        == 1
    )
    altered = pe.execution_probability_row(
        declared, plan=plan(), fills=overnight_fills(), sizing={**sizing, "weight": ".05"}
    )
    assert altered["y"] is None
    assert "DECLARED_SIZING_HASH_MISMATCH" in altered["missing_reasons"]
    assert "FROZEN_ALLOCATION_EXCEEDED" in altered["missing_reasons"]
    assert pe.execution_probability_row(declared, plan=plan(), fills=overnight_fills())["y"] == 1
