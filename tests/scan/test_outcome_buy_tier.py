import json


def _run(tmp_path, tier):
    run = tmp_path / "20260917-0917_2152"
    st = run / "trace" / "staging"
    st.mkdir(parents=True)
    (run / "manifest.json").write_text(json.dumps({"analysis_date": "2026-09-17"}), encoding="utf-8")
    st.joinpath("finalists.csv").write_text("code,name,sector,guard,lane\n600001,甲,电力,,value\n", encoding="utf-8")
    st.joinpath("_relative_buy_decision.json").write_text(json.dumps({
        "mode": "active", "rule_version": "e6.v4.0",
        "buys": [{"code": "600001", "tier": tier, "basis": "card_backed", "rank": 1}],
        "candidates": [{"code": "600001", "rank": 1, "eligible": True}]}), encoding="utf-8")
    st.joinpath("_buyability.json").write_text(json.dumps({"wall": "none"}), encoding="utf-8")
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
