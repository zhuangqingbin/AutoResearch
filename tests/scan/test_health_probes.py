"""run_health 数据病探针:anns 空稿率 + northbound 通道空转读数。合成,无网络。

spec: 2026-07-05 wave §B0/顺带修 —— best-effort 降级必须配读数,数据病不许隐身。
"""
from __future__ import annotations

import json

import pandas as pd

from autoresearch.scan.health import anns_empty_rate, northbound_probe, run_health


def test_anns_empty_rate(tmp_path):
    assert anns_empty_rate(tmp_path) is None                     # 无 L3_news 目录 → None
    d = tmp_path / "L3_news"
    d.mkdir()
    (d / "000001.json").write_text("[]", encoding="utf-8")
    (d / "000002.json").write_text(json.dumps([{"title": "回购"}]), encoding="utf-8")
    assert anns_empty_rate(tmp_path) == 0.5
    (d / "000003.json").write_text("{bad", encoding="utf-8")     # 坏 JSON 记作空
    assert anns_empty_rate(tmp_path) == round(2 / 3, 3)


def test_run_health_anns_expected_annotation(tmp_path):
    """anns_d 已退役(2026-07-18):`anns_empty_rate` 键名/数值原样保留(下游兼容),并列布尔
    `anns_expected` 标注 =1.0/None 为 expected(no-permission·covered by news_em+intel)非告警;
    出现部分数据(<1.0)才是意外读数 → False。"""
    h = run_health(tmp_path)                                     # 无 L3_news → None = expected
    assert h["anns_empty_rate"] is None and h["anns_expected"] is True
    d = tmp_path / "L3_news"
    d.mkdir()
    (d / "000001.json").write_text("[]", encoding="utf-8")       # 全空 = 无权限常态
    h = run_health(tmp_path)
    assert h["anns_empty_rate"] == 1.0 and h["anns_expected"] is True
    (d / "000002.json").write_text(json.dumps([{"title": "回购"}]), encoding="utf-8")
    h = run_health(tmp_path)
    assert h["anns_empty_rate"] == 0.5 and h["anns_expected"] is False


def test_northbound_probe(tmp_path):
    assert northbound_probe(tmp_path) is None                    # 无 recall staging → None
    pd.DataFrame([
        {"code": "000001", "recall_channels": "northbound|value", "hk_ratio": float("nan")},
        {"code": "000002", "recall_channels": "northbound", "hk_ratio": float("nan")},
        {"code": "000003", "recall_channels": "momentum", "hk_ratio": 1.2},
    ]).to_csv(tmp_path / "L1_recall_top1000.csv", index=False)
    nb = northbound_probe(tmp_path)
    assert nb == {"n": 2, "hk_nan": 1.0}                         # 北向召回票 hk 全 NaN = 空转坐实
