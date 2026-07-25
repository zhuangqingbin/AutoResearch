"""t1 记分卡带早停桶:最近 5 次里 3 次真选全 Hold、verdict 全「—」= 快环无样本可评。"""
from __future__ import annotations

import json

import pandas as pd

from autoresearch.learning import t1_review


def _setup(tmp_path):
    d = tmp_path / "2026-07-21"
    d.mkdir(parents=True)
    (d / "finalists.csv").write_text(
        "code,name,lane,conviction\n000651,格力电器,composite,70\n"
        "300857,协创数据,composite,65\n", encoding="utf-8")
    (d / "details").mkdir()
    for c in ("000651", "300857"):
        (d / "details" / f"{c}.md").write_text("**Rating**: Hold\n", encoding="utf-8")
    (d / "_early_stop.json").write_text(
        json.dumps({"000651": {"phase": "P3", "reason": "涨停追高"}}, ensure_ascii=False),
        encoding="utf-8")
    return d


def _prices():
    return pd.DataFrame({"code": ["000651", "300857", "000002"],
                         "industry": ["家电", "消费电子", "地产"],
                         "close_t": [40.0, 20.0, 10.0], "close_t1": [42.0, 19.0, 10.1],
                         "cc1": [0.05, -0.05, 0.01], "oc1": [0.04, -0.04, 0.01],
                         "hi_oc": [0.06, 0.01, 0.02]})


def test_scorecard_carries_early_stop_column(tmp_path):
    _setup(tmp_path)
    res = t1_review.build_scorecard("2026-07-21", scan_root=tmp_path,
                                    prices=_prices(), cal=["2026-07-21", "2026-07-22"])
    sc = res["scorecard"]
    assert "early_stop" in sc.columns
    row = sc[sc["code"] == "000651"].iloc[0]
    assert row["early_stop"] == "涨停追高"
    assert sc[sc["code"] == "300857"].iloc[0]["early_stop"] == ""


def test_render_has_early_stop_section(tmp_path):
    _setup(tmp_path)
    res = t1_review.build_scorecard("2026-07-21", scan_root=tmp_path,
                                    prices=_prices(), cal=["2026-07-21", "2026-07-22"])
    md = t1_review.render_scorecard_md(res)
    assert "早停桶" in md
    assert "涨停追高" in md
