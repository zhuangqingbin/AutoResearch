"""B5:验收仪器 —— 分母明确、缺人工标注就 IMMATURE、不许合成案例冒充标注。"""
import json

import pytest

from autoresearch.news import claim_acceptance as ca


def case(**changes):
    row = {"case_id": "c1", "predicate": "回购", "difficulty": "support",
           "expected": "PASS", "predicted": "PASS", "has_text": True,
           "source_ref": "cninfo://600000/2026-09-01", "source_hash": "a" * 64,
           "reviewer": "qingbin", "reviewed_on": "2026-09-07", "note": ""}
    return dict(row, **changes)


# ───────────────────────── 案例契约 ─────────────────────────

def test_valid_case_passes():
    assert ca.validate_case(case()) == case()


@pytest.mark.parametrize("field", sorted(ca.CASE_FIELDS))
def test_every_missing_field_rejected(field):
    row = case()
    del row[field]
    with pytest.raises(ValueError):
        ca.validate_case(row)


@pytest.mark.parametrize("changes", [
    {"predicate": "立案"}, {"difficulty": "hard"}, {"expected": "MAYBE"},
    {"predicted": "maybe"}, {"has_text": "yes"}, {"reviewer": "  "},
    {"source_ref": ""}, {"has_text": True, "source_hash": ""},
])
def test_invalid_case_rejected(changes):
    with pytest.raises(ValueError):
        ca.validate_case(case(**changes))


def test_a_case_without_source_text_can_only_be_expected_unknown():
    """没拿到原文,任何「应该是 PASS/FAIL」的标注都是标注者在凭印象。"""
    ca.validate_case(case(has_text=False, source_hash="", expected="UNKNOWN", predicted="UNKNOWN"))
    with pytest.raises(ValueError):
        ca.validate_case(case(has_text=False, source_hash="", expected="PASS"))


def test_unregistered_predicate_is_refused_so_the_quota_stays_honest():
    with pytest.raises(ValueError, match="unregistered predicate"):
        ca.validate_case(case(predicate="重组"))


def test_load_cases_rejects_duplicate_ids(tmp_path):
    path = tmp_path / "cases.json"
    path.write_text(json.dumps({"cases": [case(), case()]}), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate case_id"):
        ca.load_cases(path)


def test_load_cases_accepts_a_bare_list(tmp_path):
    path = tmp_path / "cases.json"
    path.write_text(json.dumps([case(), case(case_id="c2")]), encoding="utf-8")
    assert len(ca.load_cases(path)) == 2


# ───────────────────────── 四项读数 ─────────────────────────

def test_each_rate_has_its_own_denominator():
    rows = [case(case_id="a", predicted="PASS", expected="PASS"),
            case(case_id="b", predicted="PASS", expected="FAIL"),
            case(case_id="c", predicted="FAIL", expected="FAIL"),
            case(case_id="d", predicted="UNKNOWN", expected="UNKNOWN"),
            case(case_id="e", predicted="UNKNOWN", expected="UNKNOWN", has_text=False, source_hash="")]
    m = ca.verification_metrics(rows)
    assert m["false_pass_rate"] == pytest.approx(0.5) and m["n_predicted_pass"] == 2
    assert m["false_fail_rate"] == 0.0 and m["n_predicted_fail"] == 1
    assert m["unknown_rate"] == pytest.approx(0.4) and m["missing_text_rate"] == pytest.approx(0.2)


def test_empty_denominator_is_null_not_zero():
    """「没有预测 PASS」与「预测 PASS 全对」是两件事。"""
    rows = [case(predicted="UNKNOWN", expected="UNKNOWN")]
    m = ca.verification_metrics(rows)
    assert m["false_pass_rate"] is None and m["n_predicted_pass"] == 0
    assert m["false_fail_rate"] is None
    assert m["unknown_rate"] == 1.0


def test_no_rows_at_all_is_all_null():
    m = ca.verification_metrics([])
    assert m["n"] == 0 and m["unknown_rate"] is None and m["missing_text_rate"] is None


def test_unknown_rate_is_not_an_error_rate():
    """全 UNKNOWN 时错误率为 None(没预测过),不是 0 也不是 1。"""
    m = ca.verification_metrics([case(predicted="UNKNOWN", expected="UNKNOWN")])
    assert m["unknown_rate"] == 1.0 and m["false_pass_rate"] is None


# ───────────────────────── 覆盖增量与发布条件 ─────────────────────────

def test_new_coverage_is_zero_when_everything_stays_unknown():
    """B1 之后全是 UNKNOWN;增量为零 = 契约与比较器一条都没用上。"""
    rows = [case(case_id=str(i), predicted="UNKNOWN", expected="UNKNOWN") for i in range(5)]
    assert ca.new_coverage(rows) == {"n_decided": 0, "n_correct": 0, "share_decided": 0.0}


def full_set(**overrides):
    rows = []
    for predicate in ("回购", "增持", "减持", "中标"):
        for difficulty, n in ca.TARGET_PER_PREDICATE.items():
            verdict = {"support": "PASS", "refute": "FAIL", "insufficient": "UNKNOWN"}[difficulty]
            for i in range(n):
                rows.append(case(case_id=f"{predicate}-{difficulty}-{i}", predicate=predicate,
                                 difficulty=difficulty, expected=verdict, predicted=verdict,
                                 **({"has_text": False, "source_hash": ""} if difficulty == "insufficient" else {})))
    for key, value in overrides.items():
        rows[0][key] = value
    return rows


def test_a_complete_clean_set_passes_acceptance():
    got = ca.acceptance(full_set())
    assert got["status"] == "PASS" and got["failures"] == []
    assert got["metrics"]["n"] == ca.TARGET_TOTAL == 80


def test_any_false_pass_blocks_release():
    rows = full_set()
    rows[0]["expected"] = "FAIL"          # 预测 PASS 实际该 FAIL
    got = ca.acceptance(rows)
    assert got["status"] == "BLOCKED" and "FALSE_PASS_PRESENT" in got["failures"]


def test_short_sample_is_immature_not_a_pass():
    got = ca.acceptance(full_set()[:10])
    assert got["status"] == "BLOCKED"
    assert any(f.startswith("IMMATURE_SAMPLE:10/80") for f in got["failures"])


def test_quota_shortfall_names_the_missing_bucket():
    rows = [r for r in full_set() if not (r["predicate"] == "增持" and r["difficulty"] == "refute")]
    got = ca.acceptance(rows)
    assert any("增持.refute" in f for f in got["failures"] if f.startswith("QUOTA_SHORT"))


def test_all_unknown_set_is_blocked_for_zero_new_coverage():
    rows = [case(case_id=str(i), predicted="UNKNOWN", expected="UNKNOWN",
                 has_text=False, source_hash="") for i in range(80)]
    got = ca.acceptance(rows)
    assert "NO_NEW_VERIFIABLE_COVERAGE" in got["failures"]


def test_quota_table_shows_every_predicate_even_when_absent():
    table = ca.coverage_by_predicate([])
    assert set(table) == {"回购", "增持", "减持", "中标"}
    assert table["回购"]["support"] == {"n": 0, "target": 7}
