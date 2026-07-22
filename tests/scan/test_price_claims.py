# tests/scan/test_price_claims.py
from autoresearch.scan.price_claims import (
    extract_price_claims, reconcile_claims, audit_card_text,
)

NAME, CODE = "协创数据", "300857"


def test_extract_pct_claim_with_own_name():
    text = "协创数据 7-21 放量上涨 11.4%,量比 1.9。"
    claims = extract_price_claims(text, name=NAME, code6=CODE, year_hint=2026)
    assert len(claims) == 1
    c = claims[0]
    assert c["date"] == "20260721" and c["kind"] == "pct" and abs(c["value"] - 11.4) < 1e-9


def test_extract_limit_claim():
    text = "本股 07-15 涨停,随后三日回落。"
    claims = extract_price_claims(text, name=NAME, code6=CODE, year_hint=2026)
    assert len(claims) == 1 and claims[0]["kind"] == "limit" and claims[0]["date"] == "20260715"


def test_extract_skips_unattributed_index_sentence():
    # 句内没有本票名称/代码/本股指代 → 不认领(科创50 的涨幅不是本票断言)
    text = "7-21 工信部算力标准催化,科创50 单日 +10%。"
    assert extract_price_claims(text, name=NAME, code6=CODE, year_hint=2026) == []


def test_extract_skips_dateless():
    assert extract_price_claims("协创数据近期上涨 30%。", name=NAME, code6=CODE, year_hint=2026) == []


def test_reconcile_within_tolerance_passes():
    claims = [{"date": "20260721", "kind": "pct", "value": 11.4, "snippet": "s"}]
    assert reconcile_claims(claims, {"20260721": 11.42}, code6=CODE) == []


def test_reconcile_mismatch_caught():
    claims = [{"date": "20260721", "kind": "pct", "value": 5.3, "snippet": "s"}]
    out = reconcile_claims(claims, {"20260721": 1.2}, code6=CODE)
    assert len(out) == 1 and out[0]["claimed"] == 5.3 and out[0]["actual"] == 1.2


def test_reconcile_limit_board_aware():
    # 300 开头 20cm 板:实涨 19.99% 算涨停成立;9.98% 不算
    ok = [{"date": "20260715", "kind": "limit", "value": None, "snippet": "s"}]
    assert reconcile_claims(ok, {"20260715": 19.99}, code6="300857") == []
    assert len(reconcile_claims(ok, {"20260715": 9.98}, code6="300857")) == 1
    # 600 开头 10cm 板:9.98% 即成立
    assert reconcile_claims(ok, {"20260715": 9.98}, code6="600350") == []


def test_reconcile_nodata_skipped():
    claims = [{"date": "20260719", "kind": "pct", "value": 5.0, "snippet": "s"}]  # 周六,无 bar
    assert reconcile_claims(claims, {}, code6=CODE) == []


def test_audit_card_text_injectable_bars():
    text = "协创数据 07-21 大涨 11.4%;本股 07-15 涨停。"
    res = audit_card_text(text, name=NAME, code6=CODE, date="2026-07-21",
                          bars_fn=lambda c, ds, today: {"20260721": 1.0, "20260715": 19.99})
    assert res["n_claims"] == 2
    assert len(res["mismatches"]) == 1 and res["mismatches"][0]["date"] == "20260721"


def test_extract_direction_from_verb():
    claims = extract_price_claims("协创数据 07-16 下跌 11.4%。", name=NAME, code6=CODE, year_hint=2026)
    assert len(claims) == 1 and claims[0]["value"] == -11.4


def test_reconcile_direction_mismatch():
    claims = [{"date": "20260721", "kind": "pct", "value": 11.4, "snippet": "s"}]
    assert len(reconcile_claims(claims, {"20260721": -11.4}, code6=CODE)) == 1


def test_limit_claims_carry_direction():
    up = extract_price_claims("本股 07-15 涨停。", name=NAME, code6=CODE, year_hint=2026)[0]
    down = extract_price_claims("本股 07-15 跌停。", name=NAME, code6=CODE, year_hint=2026)[0]
    assert up["dir"] == 1 and down["dir"] == -1
    # 涨停断言撞上真实跌停日 → 必须抓出
    assert len(reconcile_claims([up], {"20260715": -19.99}, code6="300857")) == 1
    assert reconcile_claims([up], {"20260715": 19.99}, code6="300857") == []
    assert reconcile_claims([down], {"20260715": -19.99}, code6="300857") == []


def test_extract_skips_range_phrase():
    assert extract_price_claims("本股 预计有 5-10% 的下行空间,继续观察。",
                                name=NAME, code6=CODE, year_hint=2026) == []


def test_audit_bad_date_noop():
    assert audit_card_text("协创数据 07-21 大涨 11.4%。", name=NAME, code6=CODE,
                           date="", bars_fn=lambda c, d, t: {}) == {"n_claims": 0, "mismatches": []}
