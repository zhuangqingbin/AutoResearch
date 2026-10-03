"""A5: every L3 admission path applies the same hard eligibility boundary."""
from __future__ import annotations

import json

import pandas as pd
import pytest

from autoresearch.scan.l3 import merge, validation


def pick(code, conviction=70, *, finalist=True, lane="value", sector="电子", **extra):
    return {"code": code, "name": f"票{code}", "sector": sector, "lenses": "估值",
            "conviction": conviction, "fragility": "资金背离", "thesis": "资金改善",
            "mechanism": "D1 收盘条件满足后，D2 开盘催化跟随买家兑现", "risk": "兑现不足",
            "catalyst": "2026-09-30 公告", "triage_lean": "Hold", "lane": lane,
            "pct_60d": 4.0, "sentiment": "中性", "finalist": finalist, **extra}


def veto(code="L3_CONSTRAINT_B"):
    return {"reason_code": code, "reason_text": "既有约束成立且未得到支持性反证",
            "evidence_refs": ["_l3_table.md#000001.main_net_ratio"]}


def ranked(code, **extra):
    return pick(code, schema_version=2, veto_reasons=[], **extra)


def test_chased_trend_removed_cannot_return_via_lane_quota():
    rows = [pick("000001", 73, lane="trend", pct_1d=10),
            pick("000002", 70, pct_1d=1),
            pick("000003", 65, finalist=False, pct_1d=1)]
    fin, bench = merge.merge_l3_finalists_v3(pd.DataFrame(rows), 10)
    assert set(fin.code) == {"000002", "000003"}
    assert bench.set_index("code").loc["000001", "guard"] == "chase_1d"


@pytest.mark.parametrize("path", ["chase", "lane", "sector"])
def test_chased_bench_is_ineligible_for_every_replacement(path):
    if path == "sector":
        rows = [pick(f"00000{i}", 74 - i) for i in range(1, 5)]
        bad = pick("000005", 68, finalist=False, sector="银行", pct_1d=10)
        good = pick("000006", 60, finalist=False, sector="煤炭", pct_1d=1)
    elif path == "lane":
        rows = [pick("000001", 70), pick("000002", 60)]
        bad = pick("000005", 68, finalist=False, lane="trend", pct_1d=10)
        good = pick("000006", 66, finalist=False, lane="trend", pct_1d=1)
    else:
        rows = [pick("000001", 74, pct_1d=10), pick("000002", 70, pct_1d=1)]
        bad = pick("000005", 68, finalist=False, pct_1d=10)
        good = pick("000006", 60, finalist=False, pct_1d=1)
    fin, _ = merge.merge_l3_finalists_v3(pd.DataFrame([*rows, bad, good]), 10)
    assert "000005" not in set(fin.code)
    assert "000006" in set(fin.code)


@pytest.mark.parametrize("reason", ["L3_CONSTRAINT_B", "L3_CONSTRAINT_E"])
def test_high_conviction_never_overrides_structured_veto(reason):
    bad = ranked("000001", conviction=90, finalist=False)
    bad["veto_reasons"] = [veto(reason)]
    fin, bench = merge.merge_l3_finalists_v3(pd.DataFrame([bad, ranked("000002")]), 10)
    assert list(fin.code) == ["000002"]
    row = bench.set_index("code").loc["000001"]
    assert row["guard"] == "structural_veto"
    assert row["veto_status"] == "VETO"
    assert row["veto_reasons"] == [veto(reason)]


@pytest.mark.parametrize("lane", ["trend", "lowturn"])
def test_lane_quota_cannot_admit_structured_veto(lane):
    bad = ranked("000001", conviction=68, finalist=False, lane=lane)
    bad["veto_reasons"] = [veto()]
    fin, _ = merge.merge_l3_finalists_v3(pd.DataFrame([bad, ranked("000002", conviction=60)]), 10)
    assert list(fin.code) == ["000002"]


def test_lowturn_and_misread_flags_do_not_invent_a_veto():
    row = ranked("000001", conviction=78, lane="lowturn", lowturn="低位转强", misread="背离")
    fin, _ = merge.merge_l3_finalists_v3(pd.DataFrame([row]), 10)
    assert list(fin.code) == ["000001"]
    assert fin.iloc[0]["veto_status"] == "CLEAR"


def test_historical_missing_structured_veto_is_unknown_without_prose_inference():
    row = pick("000001", 80, finalist=False, thesis="命中硬约束 B，历史散文不可重判")
    fin, _ = merge.merge_l3_finalists_v3(pd.DataFrame([row]), 10)
    assert list(fin.code) == ["000001"]
    assert fin.iloc[0]["veto_status"] == "UNKNOWN"


def test_sector_soft_cap_high_conviction_exception_has_reason():
    rows = [pick(f"00000{i}", 85 - i) for i in range(1, 5)]
    fin, _ = merge.merge_l3_finalists_v3(pd.DataFrame(rows), 10)
    assert len(fin) == 4
    assert fin["sector_cap_exception"].str.contains("conviction", regex=False).all()


def test_sector_soft_cap_lane_exception_has_reason(monkeypatch):
    monkeypatch.setattr(merge, "L3_SECTOR_CAP", 1)
    rows = [pick("000001", 70, lane="trend"), pick("000002", 68, lane="trend")]
    fin, _ = merge.merge_l3_finalists_v3(pd.DataFrame(rows), 10)
    assert len(fin) == 2
    assert fin["sector_cap_exception"].str.contains("lane", regex=False).all()


@pytest.mark.parametrize("holding", [{"pinned": True}, {"lane": "pinned"}])
def test_pinned_is_excluded_from_new_candidate_eligibility(holding):
    rows = [pick("000001", 95, **holding), pick("000002", 70)]
    fin, bench = merge.merge_l3_finalists_v3(pd.DataFrame(rows), 10)
    assert list(fin.code) == ["000002"]
    assert bench.set_index("code").loc["000001", "guard"] == "pinned_research"


def test_existing_hard_guard_is_not_erased_before_ins75():
    rows = [pick("000001", 90, finalist=False, guard="chase_1d"), pick("000002")]
    fin, _ = merge.merge_l3_finalists_v3(pd.DataFrame(rows), 10)
    assert list(fin.code) == ["000002"]


def test_composite_seat_cannot_reintroduce_structural_veto():
    row = ranked("000001", conviction=90, finalist=False)
    row["veto_reasons"] = [veto()]
    judged = pd.DataFrame([row, ranked("000002")])
    fin, _ = merge.merge_l3_finalists_v3(judged, 10)
    actual = merge.inject_composite_seats(fin, [{"code": "000001", "sector": "电子"}], judged)
    assert list(actual.code) == ["000002"]


def test_pinned_mandatory_research_preserves_veto_and_denies_new_buy():
    row = ranked("000001", conviction=90, finalist=False)
    row["veto_reasons"] = [veto(), veto("L3_CONSTRAINT_E")]
    judged = pd.DataFrame([row, ranked("000002")])
    fin, _ = merge.merge_l3_finalists_v3(judged, 10)
    actual = merge._inject_pinned_finalists(fin, [{"code": "000001", "note": "持仓"}], judged=judged)
    pinned = actual.set_index("code").loc["000001"]
    assert pinned["lane"] == "pinned"
    assert pinned["veto_reasons"] == row["veto_reasons"]
    assert not bool(pinned["new_buy_eligible"])


def test_rank_v2_validator_and_legacy_detection():
    assert validation.validate_rank_rows([ranked("000001")], expected_version=2) == 2
    assert validation.validate_rank_rows([pick("000001")]) == 1
    with pytest.raises(ValueError, match="version"):
        validation.validate_rank_rows([pick("000001")], expected_version=2)


@pytest.mark.parametrize("change", [
    {"veto_reasons": None}, {"schema_version": 99}, {"schema_version": True},
    {"veto_reasons": [{"reason_code": "RANDOM", "reason_text": "x", "evidence_refs": ["r"]}]},
    {"veto_reasons": [{"reason_code": "L3_CONSTRAINT_B", "reason_text": "x", "evidence_refs": []}]},
])
def test_rank_v2_rejects_malformed_structured_veto(change):
    with pytest.raises(ValueError):
        validation.validate_rank_rows([{**ranked("000001"), **change}], expected_version=2)


def test_rank_v2_strict_fields_and_no_mixed_array_versions():
    row = ranked("000001")
    with pytest.raises(ValueError):
        validation.validate_rank_rows([{**row, "undeclared": "x"}], expected_version=2)
    with pytest.raises(ValueError):
        validation.validate_rank_rows([row, pick("000002")])


def test_repair_preserves_versioned_veto_bytes(tmp_path):
    day = tmp_path / "2026-09-30"
    day.mkdir()
    row = ranked("000001")
    row["thesis"] = "roe 9999"
    row["veto_reasons"] = [veto()]
    (day / "_l3_judged.json").write_text(json.dumps([row]), encoding="utf-8")
    pd.DataFrame([{"code": "000001", "roe": 5}]).to_csv(day / "L2_gbdt_top200.csv", index=False)
    validation.build_repair_pack("2026-09-30", root=tmp_path)
    (day / "_l3_repair_patch.json").write_text(json.dumps([{"code": "000001", "thesis": "资金偏弱"}]))
    validation.apply_repair_patch("2026-09-30", root=tmp_path)
    actual = json.loads((day / "_l3_judged.json").read_text())[0]
    assert actual == {**row, "thesis": "资金偏弱"}


def test_generated_input_declares_v2_and_overnight_execution_window(tmp_path, monkeypatch):
    from autoresearch.scan.l3 import prompt
    frame = pd.DataFrame([{"code": "000001", "name": "平安银行", "industry": "银行"}])
    monkeypatch.setattr(prompt, "load_l3_input", lambda *a, **k: frame.copy())
    text = prompt.l3_table_md("2026-09-30", root=tmp_path, delta=False)
    assert "〔L3输出契约 v2〕" in text
    assert "veto_reasons" in text and "schema_version=2" in text
    assert "D1 收盘" in text and "D2 开盘" in text


@pytest.mark.parametrize("consumer", ["lint", "merge"])
def test_new_legacy_pipeline_cannot_omit_declared_v2_fields(tmp_path, consumer):
    day = tmp_path / "2026-09-30"
    day.mkdir()
    (day / "_l3_table.md").write_text("〔L3输出契约 v2〕\n", encoding="utf-8")
    (day / "_l3_judged.json").write_text(json.dumps([pick("000001")]), encoding="utf-8")
    if consumer == "lint":
        result = validation.lint_judged("2026-09-30", root=tmp_path)
        assert result["ok"] is False
        assert "version" in result["reason"]
    else:
        with pytest.raises(ValueError, match="version"):
            merge.write_finalists("2026-09-30", root=tmp_path)


def test_write_preserves_veto_json_and_pinned_does_not_consume_seat(tmp_path, monkeypatch):
    from autoresearch.scan import user_config
    day = tmp_path / "2026-09-30"
    day.mkdir()
    rows = [ranked("000001", conviction=98), ranked("000002", conviction=72),
            ranked("000003", conviction=71), ranked("000004", conviction=80, finalist=False)]
    rows[-1]["veto_reasons"] = [veto()]
    (day / "_l3_judged.json").write_text(json.dumps(rows), encoding="utf-8")
    monkeypatch.setattr(user_config, "load_pinned", lambda *a, **k: {"kept": [{"code": "000001"}]})
    monkeypatch.setattr(user_config, "load_user_config", lambda *a, **k: {})
    result = merge.write_finalists("2026-09-30", root=tmp_path, budget=2)
    fin = pd.read_csv(day / "finalists.csv", dtype={"code": str})
    bench = pd.read_csv(day / "_l3_bench.csv", dtype={"code": str}).set_index("code")
    assert set(fin.code) == {"000001", "000002", "000003"}
    assert result["finalist_n"] == 2
    assert fin.set_index("code").loc["000001", "new_buy_eligible"] == False  # noqa: E712
    assert json.loads(bench.loc["000004", "veto_reasons"]) == [veto()]


def test_v2_duplicate_code_cannot_hide_a_structured_veto():
    clear = ranked("000001", conviction=90)
    blocked = ranked("000001", conviction=90)
    blocked["veto_reasons"] = [veto()]
    with pytest.raises(ValueError, match="duplicate"):
        validation.validate_rank_rows([clear, blocked, ranked("000002")], expected_version=2)


def test_write_finalists_rejects_duplicate_v2_before_publishing(tmp_path):
    day = tmp_path / "2026-09-30"
    day.mkdir()
    blocked = ranked("000001", conviction=90)
    blocked["veto_reasons"] = [veto()]
    (day / "_l3_judged.json").write_text(json.dumps([ranked("000001"), blocked]))
    with pytest.raises(ValueError, match="duplicate"):
        merge.write_finalists("2026-09-30", root=tmp_path)
    assert not (day / "finalists.csv").exists()
