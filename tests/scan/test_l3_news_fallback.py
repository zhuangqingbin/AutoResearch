"""anns_d 断链修复:reg 监管旗 L3_news 优先、空/缺回退 L3_webnews。合成,无网络。

spec: docs/specs/2026-07-05-evidence-catalyst-watchlist-card-wave-design.md §WS-B0
"""
from __future__ import annotations

import json

from autoresearch.scan.agents.l3_news import reg_hits_for_code


def _put(day_dir, sub, code, items):
    d = day_dir / sub
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{code}.json").write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")


def test_fallback_to_webnews_when_anns_empty(tmp_path):
    _put(tmp_path, "L3_news", "000001", [])                              # anns_d 断链:空列表
    _put(tmp_path, "L3_webnews", "000001", [{"title": "关于收到问询函的公告", "ann_date": "2026-07-01"}])
    assert reg_hits_for_code(tmp_path, "000001") == "问询"


def test_anns_present_takes_priority(tmp_path):
    _put(tmp_path, "L3_news", "000002", [{"title": "立案调查进展", "ann_date": "2026-07-01"}])
    _put(tmp_path, "L3_webnews", "000002", [{"title": "关于收到问询函的公告", "ann_date": "2026-07-01"}])
    assert reg_hits_for_code(tmp_path, "000002") == "立案"               # 不混入 webnews


def test_both_missing_or_bad_json_empty(tmp_path):
    assert reg_hits_for_code(tmp_path, "000003") == ""
    (tmp_path / "L3_news").mkdir()
    (tmp_path / "L3_news" / "000004.json").write_text("{bad", encoding="utf-8")
    assert reg_hits_for_code(tmp_path, "000004") == ""
