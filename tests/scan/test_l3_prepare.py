import pandas as pd

from autoresearch.scan.agents.l3_select import prepare_l3_table


def test_prepare_writes_l3_table(tmp_path):
    base = tmp_path / "context" / "scan"
    d = base / "2026-06-20"
    (d / "L3_news").mkdir(parents=True)
    pd.DataFrame([{"code": f"{i:06d}", "name": f"s{i}", "industry": "电子", "composite": 90 - i,
                   "gbdt_score": 0.5, "pct_60d": 10.0, "main_net_ratio": 0.01,
                   "winner_rate": 30.0, "np_yoy": 50.0, "n_channels": 2,
                   "recall_channels": "composite"} for i in range(3)]).to_csv(
        d / "L2_gbdt_top200.csv", index=False)
    for c in ("000000", "000001", "000002"):
        (d / "L3_news" / f"{c}.json").write_text("[]", encoding="utf-8")

    res = prepare_l3_table("2026-06-20", root=base, do_harvest=False)

    assert res["codes"] == 3 and res["table_bytes"] > 0
    assert (d / "_l3_table.md").exists()


def test_prepare_with_harvest_default(tmp_path, monkeypatch):
    """Test prepare_l3_table with do_harvest=True (default path) using monkeypatched harvest functions."""
    base = tmp_path / "context" / "scan"
    d = base / "2026-06-20"
    (d / "L3_news").mkdir(parents=True)
    (d / "L3_evidence").mkdir(parents=True)
    pd.DataFrame([{"code": f"{i:06d}", "name": f"s{i}", "industry": "电子", "composite": 90 - i,
                   "gbdt_score": 0.5, "pct_60d": 10.0, "main_net_ratio": 0.01,
                   "winner_rate": 30.0, "np_yoy": 50.0, "n_channels": 2,
                   "recall_channels": "composite"} for i in range(3)]).to_csv(
        d / "L2_gbdt_top200.csv", index=False)
    for c in ("000000", "000001", "000002"):
        (d / "L3_news" / f"{c}.json").write_text("[]", encoding="utf-8")
        (d / "L3_evidence" / f"{c}.json").write_text("{}", encoding="utf-8")

    # Monkeypatch harvest functions to no-op to prevent network calls
    monkeypatch.setattr("autoresearch.scan.agents.l3_select.harvest_l3_evidence",
                       lambda *a, **k: {})
    monkeypatch.setattr("autoresearch.scan.agents.l3_news.harvest_l3_news",
                       lambda *a, **k: {})

    # Call with default do_harvest=True
    res = prepare_l3_table("2026-06-20", root=base)

    assert res["codes"] == 3 and res["table_bytes"] > 0
    assert (d / "_l3_table.md").exists()
