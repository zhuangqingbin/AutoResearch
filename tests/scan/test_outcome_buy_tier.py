import json

import pandas as pd


def _run(tmp_path, tier, *, wall="none", write_buyability=True):
    run = tmp_path / "20260917-0917_2152"
    st = run / "trace" / "staging"
    st.mkdir(parents=True)
    (run / "manifest.json").write_text(json.dumps({"analysis_date": "2026-09-17"}), encoding="utf-8")
    st.joinpath("finalists.csv").write_text("code,name,sector,guard,lane\n600001,甲,电力,,value\n", encoding="utf-8")
    st.joinpath("_relative_buy_decision.json").write_text(json.dumps({
        "mode": "active", "rule_version": "e6.v4.0",
        "buys": [{"code": "600001", "tier": tier, "basis": "card_backed", "rank": 1}],
        "candidates": [{"code": "600001", "rank": 1, "eligible": True}]}), encoding="utf-8")
    if write_buyability:
        st.joinpath("_buyability.json").write_text(json.dumps({"wall": wall}), encoding="utf-8")
    return run


def test_run_facts_carries_buy_tier(tmp_path):
    from autoresearch.scan.outcome import LEDGER_COLUMNS, run_facts
    row = run_facts(_run(tmp_path, "A"))["rows"]["600001"]
    assert row["e6_buy"] is True and row["buy_tier"] == "A"
    assert "buy_tier" in LEDGER_COLUMNS


def test_runs_view_counts_a_tier_and_wall(tmp_path):
    from autoresearch.scan.ledger_views import RUNS_COLUMNS, _run_counts, _wall_of
    assert {"n_buy_a", "wall"} <= set(RUNS_COLUMNS)
    run = _run(tmp_path, "A")
    assert _run_counts(run, "FULL") == (1, 1, 1)
    assert _wall_of(run) == "none"


# ───────────────────────── fix round 1 (reviewer mutation testing) ─────────────────────────
# `run_facts` giving `buy_tier` a value is not the same claim as the ledger CSV actually
# persisting it, `n_buy_a` actually filtering on the tier, or `_wall_of` actually
# distinguishing "no artifact" from "artifact says none". Each test below was written
# against a real mutation the reviewer ran that the two tests above did not catch.

def test_compute_outcome_persists_buy_tier_into_the_ledger_csv(tmp_path, monkeypatch):
    """Mutation: deleting `"buy_tier"` from `compute_outcome`'s row-copy key tuple
    (outcome.py, the line right after `"e6_rank", "e6_eligible", "e6_buy"`) stayed
    green against the whole suite. `run_facts()` carrying the field is not enough —
    that tuple is what carries it from `run_facts` into the doc that `upsert_ledger`
    writes to `recommendations.csv`. This goes through the real write + read path,
    not just the in-memory doc.
    """
    from autoresearch.scan import outcome

    run = _run(tmp_path, "A")
    fr = pd.DataFrame(index=pd.Index(["600001"], name="code"))
    fr[outcome.MAIN] = [0.01]
    fr["buyable_c1"] = True
    fr["fwd_5_oc"] = 0.02
    fr["fwd_10_oc"] = 0.03
    fr["t1_open"] = 10.0
    fr["t1_high"] = 11.0
    fr["t1_low"] = 9.0
    fr["t1_close"] = 10.0
    fr["t1_pct_chg"] = 1.0
    fr["t2_open"] = 10.2
    fr["t1_pos_in_range"] = 0.5
    monkeypatch.setattr(outcome, "market_frame",
                        lambda date, **k: (fr, {
                            "outcome_status": outcome.MATURE, "reason": "",
                            "calendar_quality": outcome.TRADE_CAL_QUALITY,
                            "calendar_digest": "testfakecalendar0",
                            "t1": "20260918", "t2": "20260919", "missing_sessions": [],
                            "n": len(fr)}))
    doc = outcome.compute_outcome(run)
    assert doc["rows"]["600001"]["buy_tier"] == "A"

    root = tmp_path / "ledger_root"
    outcome.upsert_ledger(doc, root)
    persisted = {r["code"]: r for r in outcome.load_ledger(root)}
    assert persisted["600001"]["buy_tier"] == "A"           # survives the CSV round-trip


def test_run_counts_n_buy_a_only_counts_a_tier_not_all_buys(tmp_path):
    """Mutation: loosening `n_buy_a`'s filter to count every `e6_buy` row (dropping
    the `buy_tier == "A"` check) stayed green, because every prior fixture's `buys`
    list only ever varied `tier` off a single code — never mixed A and R in the same
    run. This one does, so a filter that cannot tell them apart reads the wrong count.
    """
    from autoresearch.scan.ledger_views import _run_counts

    run = tmp_path / "20260917-0917_2210"
    st = run / "trace" / "staging"
    st.mkdir(parents=True)
    (run / "manifest.json").write_text(json.dumps({"analysis_date": "2026-09-17"}), encoding="utf-8")
    st.joinpath("finalists.csv").write_text(
        "code,name,sector,guard,lane\n"
        "600001,甲,电力,,value\n"
        "600002,乙,电力,,value\n", encoding="utf-8")
    st.joinpath("_relative_buy_decision.json").write_text(json.dumps({
        "mode": "active", "rule_version": "e6.v4.0",
        "buys": [{"code": "600001", "tier": "A", "basis": "card_backed", "rank": 1},
                 {"code": "600002", "tier": "R", "basis": "relative_forced", "rank": 2}],
        "candidates": [{"code": "600001", "rank": 1, "eligible": True},
                       {"code": "600002", "rank": 2, "eligible": True}]}), encoding="utf-8")
    st.joinpath("_buyability.json").write_text(json.dumps({"wall": "none"}), encoding="utf-8")

    # 2 finalists, 2 buys, but only the first is A-tier — a mutant that counts every
    # buy as `n_buy_a` reads 2 here, not 1.
    assert _run_counts(run, "FULL") == (2, 2, 1)


def test_wall_of_reads_the_real_non_none_value(tmp_path):
    """Mutation: hardcoding `_wall_of`'s return to the literal string `"none"` stayed
    green, because every prior fixture's real wall value already happened to be
    `"none"`. This fixture's real value is `"gates"`.
    """
    from autoresearch.scan.ledger_views import _wall_of
    run = _run(tmp_path, "A", wall="gates")
    assert _wall_of(run) == "gates"


def test_wall_of_is_none_when_the_artifact_is_missing(tmp_path):
    """Mutation: the same hardcode also stayed green on the missing-artifact branch,
    because it was never separately exercised — every prior fixture wrote
    `_buyability.json`. Old runs never wrote it at all; that must read as Python
    `None`, not the string `"none"`.
    """
    from autoresearch.scan.ledger_views import _wall_of
    run = _run(tmp_path, "A", write_buyability=False)
    assert _wall_of(run) is None
