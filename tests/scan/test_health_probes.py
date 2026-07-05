"""run_health 数据病探针:anns 空稿率 + northbound 通道空转读数。合成,无网络。

spec: 2026-07-05 wave §B0/顺带修 —— best-effort 降级必须配读数,数据病不许隐身。
"""
from __future__ import annotations

import json

import pandas as pd

from autoresearch.scan.health import anns_empty_rate, northbound_probe


def test_anns_empty_rate(tmp_path):
    assert anns_empty_rate(tmp_path) is None                     # 无 L3_news 目录 → None
    d = tmp_path / "L3_news"
    d.mkdir()
    (d / "000001.json").write_text("[]", encoding="utf-8")
    (d / "000002.json").write_text(json.dumps([{"title": "回购"}]), encoding="utf-8")
    assert anns_empty_rate(tmp_path) == 0.5
    (d / "000003.json").write_text("{bad", encoding="utf-8")     # 坏 JSON 记作空
    assert anns_empty_rate(tmp_path) == round(2 / 3, 3)


def test_northbound_probe(tmp_path):
    assert northbound_probe(tmp_path) is None                    # 无 recall staging → None
    pd.DataFrame([
        {"code": "000001", "recall_channels": "northbound|value", "hk_ratio": float("nan")},
        {"code": "000002", "recall_channels": "northbound", "hk_ratio": float("nan")},
        {"code": "000003", "recall_channels": "momentum", "hk_ratio": 1.2},
    ]).to_csv(tmp_path / "L1_recall_top1000.csv", index=False)
    nb = northbound_probe(tmp_path)
    assert nb == {"n": 2, "hk_nan": 1.0}                         # 北向召回票 hk 全 NaN = 空转坐实
