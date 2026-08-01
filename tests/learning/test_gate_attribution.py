"""门归因 v3(A11):四态分类 / legacy 复现 / MULTI_GATE 去重 / participation 口径。合成,无网络。"""
from __future__ import annotations

import pandas as pd
import pytest

from autoresearch.learning.gate_attribution import (
    COHORT_LEGACY,
    COHORT_V3,
    FALSE_KILL_MIN_EXCESS,
    MULTI_GATE,
    UNDECIDABLE_GATE,
    binding_fires,
    build_day,
    build_participation_day,
    check_against_gate_ledger,
    classify_outcome,
    gate_participation,
    render_migration,
    roll,
    roll_participation,
    summarize,
)
from autoresearch.scan.decision_record import (
    DecisionRecord,
    write_decision_records,
)


def _mk_day(root, date, *, fires=None, decisions=None, attr_rows=None):
    day = root / date
    (day / "retro").mkdir(parents=True, exist_ok=True)
    if fires is not None:
        pd.DataFrame(fires).to_csv(day / "gate_fires.csv", index=False)
    if decisions is not None:
        write_decision_records(day, decisions)
    pd.DataFrame(attr_rows).to_csv(day / "retro" / "attribution.csv", index=False)
    return day


def _fire(date, gate, code):
    return {"date": date, "code": code, "check": f"OW三门·{gate}",
            "severity": "fail", "detail": "d", "level": "binding"}


def _decision(date, code, states, *, rating="Underweight"):
    return [DecisionRecord.build(
        analysis_date=date,
        contract_hash=None,
        code=code,
        source_rating=rating,
        rubric_rating=rating,
        gate_states=states,
        early_stop=None,
        ensemble_ratings=[],
        final_rating=rating,
        proposal="HOLD",
        reason="test",
        evidence_refs=[f"details/{code}.md"],
        first_rejection_stage="L4_RUBRIC",
    )]


# ────────────────────────── 四态分类(纯函数) ──────────────────────────

@pytest.mark.parametrize(
    "excess,expected",
    [
        (-0.001, "CORRECT"),
        (-0.50, "CORRECT"),
        (0.0, "NEUTRAL"),
        (0.0199, "NEUTRAL"),
        (FALSE_KILL_MIN_EXCESS, "FALSE_KILL"),
        (0.10, "FALSE_KILL"),
    ],
)
def test_classify_outcome_boundaries(excess, expected):
    """+2pp 是 FALSE_KILL 的**闭区间下界**;0 归 NEUTRAL 不归 CORRECT。"""
    assert classify_outcome(excess, tradable=True, mature=True)[0] == expected


def test_classify_outcome_unmeasured_precedence():
    # 漏肉幅度再大,门状态不可判时也不能算到任何一道门头上
    assert classify_outcome(
        0.20, tradable=True, mature=True, gate_state_known=False
    ) == ("UNMEASURED", "gate_state_unknown")
    assert classify_outcome(0.20, tradable=False, mature=True)[0] == "UNMEASURED"
    assert classify_outcome(None, tradable=True, mature=False) == (
        "UNMEASURED", "t2_immature")
    # 有 fwd 但当日没有市场基准 → 仍不可测,不得默认成 CORRECT
    assert classify_outcome(None, tradable=True, mature=True) == (
        "UNMEASURED", "no_market_baseline")


# ────────────────────────── legacy vs v3 分流 ──────────────────────────

def test_v3_collapses_multi_gate_but_legacy_double_counts(tmp_path):
    """同票踩两门:legacy 两个单门各记一次;v3 只记一行 MULTI_GATE。"""
    _mk_day(
        tmp_path, "2026-07-01",
        fires=[_fire("2026-07-01", "主力真在", "000001"),
               _fire("2026-07-01", "业绩真兑现", "000001"),
               _fire("2026-07-01", "估值不透支", "000002")],
        attr_rows=[
            {"code": "000001", "fwd_2_oc": 0.05, "tradable": True, "buyable": True},
            {"code": "000002", "fwd_2_oc": -0.05, "tradable": True, "buyable": True},
            {"code": "000003", "fwd_2_oc": 0.00, "tradable": True, "buyable": True},
        ],
    )
    legacy = build_day(tmp_path / "2026-07-01", cohort=COHORT_LEGACY)
    assert set(legacy["gate"]) == {"主力真在", "业绩真兑现", "估值不透支"}
    assert len(legacy) == 3
    assert (legacy["cohort_version"] == COHORT_LEGACY).all()

    v3 = build_day(tmp_path / "2026-07-01", cohort=COHORT_V3)
    assert set(v3["gate"]) == {MULTI_GATE, "估值不透支"}
    assert len(v3) == 2
    assert set(v3.loc[v3["gate"] == MULTI_GATE, "code"]) == {"000001"}
    # 000001 不得再出现在任何单门分母里
    assert "000001" not in set(v3.loc[v3["gate"] != MULTI_GATE, "code"])


def test_legacy_and_v3_use_different_market_baselines(tmp_path):
    """legacy=全表均值、v3=可交易成熟票中位 —— 同一天两个基准必须真的不同。"""
    _mk_day(
        tmp_path, "2026-07-02",
        fires=[_fire("2026-07-02", "主力真在", "000001")],
        attr_rows=[
            {"code": "000001", "fwd_2_oc": 0.01, "tradable": True, "buyable": True},
            {"code": "000002", "fwd_2_oc": 0.00, "tradable": True, "buyable": True},
            {"code": "000003", "fwd_2_oc": 0.50, "tradable": True, "buyable": True},
        ],
    )
    legacy = build_day(tmp_path / "2026-07-02", cohort=COHORT_LEGACY)
    v3 = build_day(tmp_path / "2026-07-02", cohort=COHORT_V3)
    assert legacy["market_baseline"].iloc[0] == "mean_all_rows"
    assert v3["market_baseline"].iloc[0] == "median_tradable_mature"
    assert abs(legacy["market_fwd_2"].iloc[0] - 0.17) < 1e-9   # (0.01+0+0.5)/3
    assert abs(v3["market_fwd_2"].iloc[0] - 0.01) < 1e-9        # median
    # 极端值把均值拖高 → 同一票 legacy 判 CORRECT、v3 判 NEUTRAL:两序列不可互冒充
    assert legacy["outcome"].iloc[0] == "CORRECT"
    assert v3["outcome"].iloc[0] == "NEUTRAL"


def test_v3_marks_untradable_unmeasured_but_legacy_does_not(tmp_path):
    _mk_day(
        tmp_path, "2026-07-03",
        fires=[_fire("2026-07-03", "主力真在", "000001")],
        attr_rows=[
            {"code": "000001", "fwd_2_oc": 0.09, "tradable": False, "buyable": True},
            {"code": "000002", "fwd_2_oc": 0.00, "tradable": True, "buyable": True},
        ],
    )
    assert build_day(tmp_path / "2026-07-03", cohort=COHORT_LEGACY)[
        "outcome"].iloc[0] == "FALSE_KILL"
    v3 = build_day(tmp_path / "2026-07-03", cohort=COHORT_V3)
    assert v3["outcome"].iloc[0] == "UNMEASURED"
    assert v3["outcome_reason"].iloc[0] == "not_tradable"


def test_structured_decisions_supersede_csv_fires(tmp_path):
    """与 gate_ledger 同规则:有 decision_records 就不再读 CSV 的 OW 行(否则重复计数)。"""
    day = _mk_day(
        tmp_path, "2026-07-06",
        fires=[_fire("2026-07-06", "主力真在", "000009")],
        decisions=_decision("2026-07-06", "000001",
                            {"主力真在": "FAIL", "业绩真兑现": "PASS",
                             "估值不透支": "PASS"}),
        attr_rows=[
            {"code": "000001", "fwd_2_oc": -0.03, "tradable": True, "buyable": True},
            {"code": "000009", "fwd_2_oc": 0.09, "tradable": True, "buyable": True},
        ],
    )
    fires = binding_fires(day)
    assert set(fires["code"]) == {"000001"}
    assert set(fires["source"]) == {"decision_records"}


def test_structured_undecidable_is_unmeasured(tmp_path):
    day = _mk_day(
        tmp_path, "2026-07-07",
        decisions=_decision("2026-07-07", "000001",
                            {"主力真在": "PASS", "业绩真兑现": "UNKNOWN",
                             "估值不透支": "PASS"}),
        attr_rows=[
            {"code": "000001", "fwd_2_oc": 0.09, "tradable": True, "buyable": True},
            {"code": "000002", "fwd_2_oc": 0.00, "tradable": True, "buyable": True},
        ],
    )
    v3 = build_day(day, cohort=COHORT_V3)
    assert v3["gate"].iloc[0] == UNDECIDABLE_GATE
    assert v3["outcome"].iloc[0] == "UNMEASURED"
    assert v3["outcome_reason"].iloc[0] == "gate_state_unknown"


# ────────────────────────── participation 口径 ──────────────────────────

def test_participation_counts_every_failing_gate(tmp_path):
    """attribution 把多门共拦收进 MULTI_GATE;participation 让三道门各记一次。"""
    day = _mk_day(
        tmp_path, "2026-07-08",
        fires=[_fire("2026-07-08", "主力真在", "000001"),
               _fire("2026-07-08", "业绩真兑现", "000001")],
        attr_rows=[
            {"code": "000001", "fwd_2_oc": 0.05, "tradable": True, "buyable": True},
            {"code": "000002", "fwd_2_oc": 0.00, "tradable": True, "buyable": True},
        ],
    )
    part = build_participation_day(day)
    assert set(part["gate"]) == {"主力真在", "业绩真兑现"}
    assert set(part["n_gates_failed"]) == {2}
    assert not part["sole_killer"].any()
    assert MULTI_GATE not in set(part["gate"])   # participation 不产 MULTI_GATE


def test_participation_from_structured_gate_states(tmp_path):
    day = _mk_day(
        tmp_path, "2026-07-09",
        decisions=_decision("2026-07-09", "000001",
                            {"主力真在": "FAIL", "业绩真兑现": "FAIL",
                             "估值不透支": "PASS"}),
        attr_rows=[
            {"code": "000001", "fwd_2_oc": -0.05, "tradable": True, "buyable": True},
            {"code": "000002", "fwd_2_oc": 0.00, "tradable": True, "buyable": True},
        ],
    )
    part = gate_participation(day)
    assert set(part["gate"]) == {"主力真在", "业绩真兑现"}
    assert set(part["n_gates_failed"]) == {2}


def test_participation_table_has_no_rate_columns(tmp_path):
    """分母重复计数的表**结构上**不给比率列 —— 不能只靠一句说明约束读表的人。"""
    _mk_day(
        tmp_path, "2026-07-10",
        fires=[_fire("2026-07-10", "主力真在", "000001"),
               _fire("2026-07-10", "业绩真兑现", "000001")],
        attr_rows=[
            {"code": "000001", "fwd_2_oc": 0.05, "tradable": True, "buyable": True},
            {"code": "000002", "fwd_2_oc": 0.00, "tradable": True, "buyable": True},
        ],
    )
    md = render_migration(
        summarize(roll(tmp_path, cohort=COHORT_LEGACY)),
        summarize(roll(tmp_path, cohort=COHORT_V3)),
        summarize(roll_participation(tmp_path)),
    )
    body = "\n".join(md)
    tail = body.split("## v3 participation")[1]
    header = next(ln for ln in tail.splitlines() if ln.startswith("| 门 |"))
    assert "错杀率" not in header and "拦对率" not in header
    assert "FALSE_KILL" in header          # 计数列保留,只砍不可用的比率列
    assert "不提供比率列" in tail
    # 上面的归因表必须仍然有比率列 —— 别把两张表一起砍了
    attr_header = next(
        ln for ln in body.split("## v3 cohort")[1].splitlines()
        if ln.startswith("| 门 |"))
    assert "错杀率" in attr_header


# ────────────────────────── 汇总 / 渲染 / 边界 ──────────────────────────

def test_summarize_rate_denominator_excludes_unmeasured(tmp_path):
    _mk_day(
        tmp_path, "2026-07-13",
        fires=[_fire("2026-07-13", "主力真在", "000001"),
               _fire("2026-07-13", "主力真在", "000002"),
               _fire("2026-07-13", "主力真在", "000003")],
        attr_rows=[
            {"code": "000001", "fwd_2_oc": 0.09, "tradable": True, "buyable": True},
            {"code": "000002", "fwd_2_oc": -0.09, "tradable": True, "buyable": True},
            {"code": "000003", "fwd_2_oc": 0.09, "tradable": False, "buyable": True},
            {"code": "000004", "fwd_2_oc": 0.00, "tradable": True, "buyable": True},
        ],
    )
    row = summarize(roll(tmp_path, cohort=COHORT_V3)).set_index("gate").loc["主力真在"]
    assert row["n_fires"] == 3 and row["UNMEASURED"] == 1 and row["measured_n"] == 2
    assert abs(row["false_kill_rate"] - 0.5) < 1e-9   # 1/2,不是 1/3


def test_render_marks_two_cohorts_as_different_series(tmp_path):
    md = "\n".join(render_migration(pd.DataFrame(), pd.DataFrame()))
    assert "不是同一序列" in md
    assert "左尾" in md and "不得互相翻译" in md


def test_empty_and_immature_days_are_graceful(tmp_path):
    assert len(roll(tmp_path)) == 0
    assert len(roll_participation(tmp_path)) == 0
    # 有拦截但 retro 未成熟(无 attribution.csv)→ 空帧,不臆造 outcome
    day = tmp_path / "2026-07-14"
    day.mkdir()
    pd.DataFrame([_fire("2026-07-14", "主力真在", "000001")]).to_csv(
        day / "gate_fires.csv", index=False)
    assert len(build_day(day)) == 0
    assert len(build_participation_day(day)) == 0


def test_unknown_cohort_rejected(tmp_path):
    with pytest.raises(ValueError):
        build_day(tmp_path / "2026-07-01", cohort="v4")


def test_check_against_gate_ledger_catches_divergence(tmp_path, monkeypatch):
    """对账探针必须真会红 —— 篡改一边的取数,`check_against_gate_ledger` 要报不一致。"""
    _mk_day(
        tmp_path, "2026-07-15",
        fires=[_fire("2026-07-15", "主力真在", "000001"),
               _fire("2026-07-15", "主力真在", "000002")],
        attr_rows=[
            {"code": "000001", "fwd_1_oo": 0.0, "fwd_2_oc": -0.09,
             "tradable": True, "buyable": True},
            {"code": "000002", "fwd_1_oo": 0.0, "fwd_2_oc": -0.05,
             "tradable": True, "buyable": True},
            {"code": "000003", "fwd_1_oo": 0.0, "fwd_2_oc": 0.00,
             "tradable": True, "buyable": True},
        ],
    )
    ok, notes = check_against_gate_ledger(tmp_path)
    assert ok, notes

    import autoresearch.learning.gate_attribution as mod

    real = mod.binding_fires
    monkeypatch.setattr(
        mod, "binding_fires", lambda d: real(d).head(1)   # 少读一行 = 取数分叉
    )
    bad_ok, bad_notes = check_against_gate_ledger(tmp_path)
    assert not bad_ok and any("✗" in note for note in bad_notes)
