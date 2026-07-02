from autoresearch.learning import self_review


def test_selftest():
    assert self_review._selftest() == 0


def test_failures_carry_code_and_dump_gate_fires(tmp_path):
    """R3·门审计地基:failure 带结构化 code;dump_gate_fires 幂等落 csv(无 fail 也写表头)。"""
    import csv

    import autoresearch.learning.self_review as sr
    res = sr.review({"finalists": [{"code": "300001", "rating": "Buy", "winner_rate": 95}],
                     "n_cards_expected": 1, "n_cards_present": 1, "summary_text": ""})
    hit = [f for f in res["failures"] if f["check"].startswith("经验红线")]
    assert hit and hit[0]["code"] == "300001"
    p = sr.dump_gate_fires(tmp_path, res, "2026-07-02")
    rows = list(csv.DictReader(p.open(encoding="utf-8")))
    assert any(r["code"] == "300001" and r["severity"] == "fail" for r in rows)
    # 无 failures → 只有表头(区分"没拦"与"没跑")
    p2 = sr.dump_gate_fires(tmp_path, {"failures": []}, "2026-07-02")
    assert list(csv.DictReader(p2.open(encoding="utf-8"))) == []
