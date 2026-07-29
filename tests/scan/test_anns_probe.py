"""anns 双源探针(Wave9 A-1 下半):存在性 ≠ 有效性——「无权限」不再与「当日故障」同形。

`health.anns_source_status` 按行内 `source` 标签拆主源/兜底源三态(ok/fallback/blind);
`self_review.product_shape_lint` 据此把 blind 判成 warn(此前是 info/expected 放行),
fallback 显式记账为 info。旧 run(无 `anns_source_status` 键)必须回落旧 `anns_empty_rate`
口径,不能让历史 run 一复盘就集体报 warn(假警报)。

合成 fixture,零网络零 LLM。add() 落的真实键名是 `check`/`severity`(见
`autoresearch/learning/self_review.py::product_shape_lint` 内的 `add()` 闭包),
不是 `probe`/`level`。
"""
from __future__ import annotations

import json

from autoresearch.learning.self_review import product_shape_lint
from autoresearch.scan import health


def _mk(tmp_path, news: dict[str, list]):
    d = tmp_path / "2026-07-29"
    (d / "L3_news").mkdir(parents=True)
    for code, rows in news.items():
        (d / "L3_news" / f"{code}.json").write_text(
            json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    return d


def test_status_ok_when_primary_has_rows(tmp_path):
    d = _mk(tmp_path, {"000651": [{"ann_date": "20260729", "title": "x"}]})
    st = health.anns_source_status(d)
    assert st["status"] == "ok"


def test_status_fallback_when_only_fallback_rows(tmp_path):
    from autoresearch.data.sources.anns_fallback import SOURCE_TAG
    d = _mk(tmp_path, {"000651": [{"ann_date": "20260729", "title": "x", "source": SOURCE_TAG}],
                       "000333": []})
    st = health.anns_source_status(d)
    assert st["status"] == "fallback"
    assert st["fallback_rows"] == 1


def test_status_blind_when_both_empty(tmp_path):
    d = _mk(tmp_path, {"000651": [], "000333": []})
    st = health.anns_source_status(d)
    assert st["status"] == "blind"
    assert st["primary_empty_rate"] == 1.0


def test_probe_warns_on_blind_not_info(tmp_path):
    """核心回归:双源皆空必须是 warn,不再是 info/expected 放行。"""
    d = _mk(tmp_path, {"000651": [], "000333": []})
    (d / "run_health.json").write_text(json.dumps({
        "anns_empty_rate": 1.0,
        "anns_source_status": {"primary_empty_rate": 1.0, "fallback_rows": 0,
                               "status": "blind"}}), encoding="utf-8")
    hits = product_shape_lint(d, "2026-07-29")
    anns = [h for h in hits if "anns" in h["check"]]
    assert anns and anns[0]["severity"] == "warn"


def test_probe_info_when_fallback_carries(tmp_path):
    from autoresearch.data.sources.anns_fallback import SOURCE_TAG
    d = _mk(tmp_path, {"000651": [{"ann_date": "20260729", "title": "x", "source": SOURCE_TAG}]})
    (d / "run_health.json").write_text(json.dumps({
        "anns_empty_rate": 0.0,
        "anns_source_status": {"primary_empty_rate": 1.0, "fallback_rows": 1,
                               "status": "fallback"}}), encoding="utf-8")
    hits = product_shape_lint(d, "2026-07-29")
    anns = [h for h in hits if "anns" in h["check"]]
    assert anns and anns[0]["severity"] == "info"


def test_probe_legacy_run_without_status_key_is_info_not_warn(tmp_path):
    """旧 run 兼容(硬要求):`run_health.json` 只有 `anns_empty_rate=1.0`、没有
    `anns_source_status` 键时,必须回落旧 expected 口径出 **info**,不能因新探针上线就把
    所有历史 run 一复盘集体判 warn——那才是真正的假警报(与本任务「blind 才该 warn」
    的意图恰好相反:历史 run 压根没有双源状态数据,谈不上"blind",不该被误判)。"""
    d = _mk(tmp_path, {"000651": [], "000333": []})
    (d / "run_health.json").write_text(json.dumps({
        "anns_empty_rate": 1.0}), encoding="utf-8")
    hits = product_shape_lint(d, "2026-07-29")
    anns = [h for h in hits if "anns" in h["check"]]
    assert anns and anns[0]["severity"] == "info"
