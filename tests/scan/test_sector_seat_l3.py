"""行业席位在 L3 侧的三处可见性(2026-09-24 §2.3)。"""
from __future__ import annotations

import json

import pandas as pd


def _l2(n=30):
    df = pd.DataFrame({"code": [f"{600000 + i:06d}" for i in range(n)], "name": [f"票{i}" for i in range(n)],
                       "industry": "电力", "composite": list(range(n, 0, -1)), "gbdt_score": list(range(n, 0, -1)),
                       "recall_channels": "value", "n_channels": 1, "pct_1d": 0.5})
    df["sector_seat"] = False
    df.loc[df.index[-2:], "sector_seat"] = True          # composite 最低的两只 = 席位(不靠分进表)
    return df


def test_triage_keeps_sector_seats_as_protected_mandatory():
    from autoresearch.scan.l3.triage import triage_l2_for_l3
    kept, cut = triage_l2_for_l3(_l2(), target=5)
    seat_codes = {"600028", "600029"}
    assert seat_codes <= set(kept["code"])
    marks = kept[kept["code"].isin(seat_codes)]
    assert (marks["selection_reason"] == "conviction_guard").all()
    assert (marks["selection_detail"] == "sector_seat").all()


def test_write_finalists_marks_sector_seat_guard(tmp_path):
    from autoresearch.scan.l3.merge import write_finalists
    scan = tmp_path / "2026-09-17"
    scan.mkdir()
    judged = [{"code": "600001", "name": "甲", "sector": "电力", "conviction": 70, "finalist": True,
               "thesis": "t", "mechanism": "m", "risk": "r"},
              {"code": "600002", "name": "乙", "sector": "电力", "conviction": 66, "finalist": True,
               "thesis": "t", "mechanism": "m", "risk": "r"}]
    (scan / "_l3_judged.json").write_text(json.dumps(judged, ensure_ascii=False), encoding="utf-8")
    (scan / "_sector_seats.json").write_text(json.dumps({"schema_version": 1, "date": "2026-09-17",
                                                          "seats": [{"code": "600002", "industry": "电力"}]}),
                                             encoding="utf-8")
    write_finalists("2026-09-17", root=tmp_path)
    fin = pd.read_csv(scan / "finalists.csv", dtype={"code": str})
    assert fin.set_index("code").loc["600002", "guard"] == "sector_seat"
    assert fin.set_index("code").loc["600001", "guard"] != "sector_seat"


def test_run_facts_role_sector_seat(tmp_path):
    from autoresearch.scan.outcome import run_facts
    run = tmp_path / "20260917-0917_2152"
    (run / "trace" / "staging").mkdir(parents=True)
    (run / "manifest.json").write_text(json.dumps({"analysis_date": "2026-09-17"}), encoding="utf-8")
    (run / "trace" / "staging" / "finalists.csv").write_text(
        "code,name,sector,guard,lane\n600002,乙,电力,sector_seat,value\n", encoding="utf-8")
    facts = run_facts(run)
    assert facts["rows"]["600002"]["role"] == "sector_seat"
