import csv
import json

import pandas as pd

from autoresearch.scan.agents.l3_select import write_finalists


def _judged():
    # agent 的 JSON 可能把 code 写成 int 62 或 str "000063",两种都要能救回
    return [
        {"code": 62, "name": "华东电脑", "sector": "计算机", "lane": "value",
         "conviction": 72, "fragility": 40, "triage_lean": "标配",
         "thesis": "t", "risk": "r", "catalyst": "c", "lenses": "价值", "sentiment": "中性"},
        {"code": "000063", "name": "中兴通讯", "sector": "通信", "lane": "trend",
         "conviction": 66, "fragility": 30, "triage_lean": "标配",
         "thesis": "t", "risk": "r", "catalyst": "c", "lenses": "趋势", "sentiment": "偏多"},
    ]


def test_write_finalists_preserves_leading_zeros(tmp_path):
    base = tmp_path / "context" / "scan"
    d = base / "2026-07-07"
    d.mkdir(parents=True)
    (d / "_l3_judged.json").write_text(json.dumps(_judged()), encoding="utf-8")
    pd.DataFrame({"code": ["000062", "000063"], "pct_60d": [12.0, 8.0]}).to_csv(
        d / "L2_gbdt_top200.csv", index=False)

    res = write_finalists("2026-07-07", budget=5, root=base)

    assert res["finalists_n"] == 2
    rows = list(csv.DictReader((d / "finalists.csv").open(encoding="utf-8")))
    assert {r["code"] for r in rows} == {"000062", "000063"}      # 前导零存活
    assert all(r["ticker"] == r["code"] for r in rows)             # ticker 与 code 同 6 位
