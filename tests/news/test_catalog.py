"""news_catalog 单测 —— §1.1 的最小证伪步与验收探针,逐条钉死。

最小证伪步(设计稿原文):
  「先对既有分片生成 1 日 manifest,用同一条新闻的**转载/更正 fixture** 验
   canonical/observation 分离,并验证 **L3 与 L4 两个 cutoff 不互相污染**。」

验收探针:
  manifest 与原始分片逐源对账;**first_seen 缺失率=0**;同一 run+stage 回放稳定;
  **late-arrival fixture 不泄漏**;源非空率/freshness 有时序。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from autoresearch.news import catalog as nc


def _obs(**over) -> nc.Observation:
    base = {
        "source": "cninfo", "title": "关于回购股份的公告", "url": "https://x/1",
        "published_ts": "2026-08-01 09:00:00",
        "first_seen_ts": "2026-08-01 18:00:00",
        "available_stage": "L3", "scope": nc.SCOPE_MARKET_WIDE,
        "codes": ("000651",), "code_method": nc.METHOD_SOURCE_FIELD,
    }
    base.update(over)
    return nc.Observation(**base)


def _cat(tmp_path) -> nc.NewsCatalog:
    return nc.NewsCatalog(tmp_path / "catalog")


# ── 身份纪律:转载 / 更正 ────────────────────────────────────────


def test_repost_is_two_observations_one_event(tmp_path):
    """同一条新闻被两家转载 → 两个 observation,**一个** canonical_event。"""
    cat = _cat(tmp_path)
    cat.ingest([_obs(source="a", url="https://a/1"),
                _obs(source="b", url="https://b/9")])
    obs = cat.observations()
    assert len(obs) == 2
    assert obs["canonical_event_id"].nunique() == 1


def test_correction_keeps_the_old_version(tmp_path):
    """同 url 内容更正 → 新 revision **新增一行**,旧版本保留,不覆盖。"""
    cat = _cat(tmp_path)
    cat.ingest([_obs(title="关于回购股份的公告")])
    cat.ingest([_obs(title="关于回购股份的更正公告")])
    obs = cat.observations().sort_values("revision")
    assert len(obs) == 2
    assert list(obs["revision"]) == ["0", "1"]
    assert obs["lineage_id"].nunique() == 1               # 同一血统
    assert "更正" in obs.iloc[1]["title"]
    assert "更正" not in obs.iloc[0]["title"]             # 旧版本还在


def test_reingesting_identical_content_is_idempotent(tmp_path):
    cat = _cat(tmp_path)
    cat.ingest([_obs()])
    result = cat.ingest([_obs()])
    assert result["unchanged"] == 1 and not result["added"]
    assert len(cat.observations()) == 1


def test_observation_key_is_not_the_event_identity():
    """`url|title|date` 的 hash 只能当 observation 候选键,不能同时承担事件身份。"""
    a = nc.lineage_id("a", "https://a/1", "标题")
    b = nc.lineage_id("b", "https://b/1", "标题")
    assert a != b                                          # 两个观测
    assert nc.canonical_event_id("标题", "2026-08-01") == \
        nc.canonical_event_id("标题", "2026-08-01")        # 一个事件


def test_title_normalization_does_not_merge_different_events():
    """过度归一会把两件事焊死 —— 「回购的公告」与「回购的进展公告」必须是两个事件。"""
    assert nc.canonical_event_id("关于回购的公告", "2026-08-01") != \
        nc.canonical_event_id("关于回购的进展公告", "2026-08-01")


def test_normalization_absorbs_punctuation_and_prefix():
    assert nc.normalize_title("关于 回购股份 的公告") == nc.normalize_title("回购股份的公告")


def test_url_less_source_falls_back_to_title_lineage(tmp_path):
    """快讯常常没有稳定 url —— 退回标题血统后,内容变了仍是「更正」而不是新条目。"""
    cat = _cat(tmp_path)
    cat.ingest([_obs(url="", title="快讯:某某中标")])
    cat.ingest([_obs(url="", title="快讯:某某中标", body_hash="v2")])
    obs = cat.observations()
    assert len(obs) == 2 and obs["lineage_id"].nunique() == 1


# ── PIT 纪律:first_seen 优先于 published ────────────────────────


def test_published_ts_cannot_substitute_for_first_seen(tmp_path):
    """08-01 发布、08-03 才抓到 → 08-02 的决策里它不存在。"""
    cat = _cat(tmp_path)
    cat.ingest([_obs(published_ts="2026-08-01 09:00:00",
                     first_seen_ts="2026-08-03 18:00:00")])
    assert len(cat.replay("000651", "2026-08-02 15:00:00", "L3")) == 0
    assert len(cat.replay("000651", "2026-08-03 20:00:00", "L3")) == 1


def test_late_arrival_does_not_leak_into_an_earlier_cutoff(tmp_path):
    """late-arrival fixture 不泄漏 —— §1.1 点名的验收探针。"""
    cat = _cat(tmp_path)
    cat.ingest([_obs(url="https://x/early", first_seen_ts="2026-08-01 10:00:00"),
                _obs(url="https://x/late", first_seen_ts="2026-08-05 10:00:00",
                     title="迟到的公告")])
    early = cat.replay("000651", "2026-08-01 23:59:59", "L3")
    assert set(early["title"]) == {"关于回购股份的公告"}


def test_l4_fulltext_does_not_pollute_same_day_l3_replay(tmp_path):
    """D4 在 finalist 之后抓到 → available_stage=L4 → 进不了同日 L3 回放。"""
    cat = _cat(tmp_path)
    cat.ingest([
        _obs(url="https://x/title-only", available_stage="L3", title="标题流公告"),
        _obs(url="https://x/fulltext", available_stage="L4", title="全文抽取:回购细则"),
    ])
    cutoff = "2026-08-01 23:00:00"
    l3 = cat.replay("000651", cutoff, "L3")
    l4 = cat.replay("000651", cutoff, "L4")
    assert set(l3["title"]) == {"标题流公告"}
    assert len(l4) == 2                                    # L4 看得到两条


def test_replay_is_stable_for_the_same_run_and_stage(tmp_path):
    cat = _cat(tmp_path)
    cat.ingest([_obs(url=f"https://x/{i}", title=f"公告{i}") for i in range(5)])
    a = cat.replay("000651", "2026-08-02 00:00:00", "L3")
    b = cat.replay("000651", "2026-08-02 00:00:00", "L3")
    assert list(a["source_observation_id"]) == list(b["source_observation_id"])


def test_snapshot_inferred_cannot_replay_before_its_snapshot(tmp_path):
    cat = _cat(tmp_path)
    cat.ingest([_obs(first_seen_basis=nc.BASIS_SNAPSHOT,
                     first_seen_ts="2026-08-04 03:00:00")])
    assert len(cat.replay("000651", "2026-08-03 23:59:59", "L3")) == 0
    got = cat.replay("000651", "2026-08-04 06:00:00", "L3")
    assert list(got["first_seen_basis"]) == [nc.BASIS_SNAPSHOT]


def test_replay_rejects_unknown_stage_and_bad_cutoff(tmp_path):
    cat = _cat(tmp_path)
    with pytest.raises(nc.CatalogError):
        cat.replay(None, "2026-08-01", "L9")
    with pytest.raises(nc.CatalogError):
        cat.replay(None, "昨天", "L3")


def test_replay_without_code_returns_everything_visible(tmp_path):
    cat = _cat(tmp_path)
    cat.ingest([_obs(codes=("000651",)), _obs(url="https://x/2", codes=("600519",))])
    assert len(cat.replay(None, "2026-08-02 00:00:00", "L3")) == 2


def test_replay_on_empty_catalog(tmp_path):
    assert not len(_cat(tmp_path).replay("000651", "2026-08-01", "L3"))


# ── 写入契约:缺 first_seen 直接拒 ──────────────────────────────


def test_missing_first_seen_is_rejected_not_guessed(tmp_path):
    cat = _cat(tmp_path)
    result = cat.ingest([_obs(first_seen_ts=None)])
    assert not result["added"]
    assert any("first_seen_ts" in p for p in result["rejected"][0]["problems"])


def test_first_seen_missing_rate_is_zero_by_construction(tmp_path):
    cat = _cat(tmp_path)
    cat.ingest([_obs(), _obs(url="https://x/2", first_seen_ts=None)])
    assert cat.health()["first_seen_missing_rate"] == 0.0
    assert cat.health()["n_observations"] == 1


@pytest.mark.parametrize("field_name,value", [
    ("first_seen_basis", "guessed"), ("available_stage", "L9"),
    ("scope", "whatever"), ("code_method", "vibes"), ("source", " "), ("title", ""),
])
def test_enum_and_required_field_violations_are_rejected(tmp_path, field_name, value):
    result = _cat(tmp_path).ingest([_obs(**{field_name: value})])
    assert result["rejected"] and not result["added"]


# ── 选择偏差:逐票采集不算市场热度 ────────────────────────────


def test_selective_scope_is_excluded_from_market_heat(tmp_path):
    cat = _cat(tmp_path)
    cat.ingest([_obs(url="https://x/wide", scope=nc.SCOPE_MARKET_WIDE),
                _obs(url="https://x/sel", scope=nc.SCOPE_SELECTIVE, title="逐票补的")])
    heat = cat.market_heat_eligible()
    assert len(heat) == 1 and set(heat["scope"]) == {nc.SCOPE_MARKET_WIDE}


def test_assert_not_selective_blocks_biased_input(tmp_path):
    cat = _cat(tmp_path)
    cat.ingest([_obs(scope=nc.SCOPE_SELECTIVE)])
    with pytest.raises(nc.CatalogError, match="选择偏差"):
        cat.assert_not_selective(cat.observations(), context="市场温度 v2")
    cat.assert_not_selective(cat.market_heat_eligible(), context="市场温度 v2")


# ── 源契约:HTTP 200 不算可用 ───────────────────────────────────


def _contract(**over) -> nc.SourceContract:
    base = {"source": "stock_news_em", "required_columns": ("title", "date"),
            "date_column": "date", "max_staleness_days": 3}
    base.update(over)
    return nc.SourceContract(**base)


def test_empty_frame_is_empty_not_ok():
    assert nc.check_source(_contract(), pd.DataFrame())["status"] == "EMPTY"
    assert nc.check_source(_contract(), None)["status"] == "EMPTY"


def test_missing_key_column_is_degraded():
    frame = pd.DataFrame({"title": ["a"]})
    result = nc.check_source(_contract(), frame)
    assert result["status"] == "DEGRADED"
    assert any("缺列" in p for p in result["problems"])


def test_low_nonnull_rate_is_degraded():
    frame = pd.DataFrame({"title": ["a", None, None, None],
                          "date": ["2026-08-01"] * 4})
    result = nc.check_source(_contract(), frame, as_of="2026-08-01")
    assert result["status"] == "DEGRADED"
    assert any("非空率不足" in p for p in result["problems"])
    assert result["nonnull_rate"] == pytest.approx(0.25)


def test_staleness_is_measured_against_as_of():
    frame = pd.DataFrame({"title": ["a"], "date": ["2026-07-01"]})
    result = nc.check_source(_contract(), frame, as_of="2026-08-01")
    assert result["freshness_days"] == 31
    assert any("陈旧" in p for p in result["problems"])


def test_non_monotonic_dates_flag_pagination_scramble():
    frame = pd.DataFrame({"title": list("abcd"),
                          "date": ["2026-08-01", "2026-08-05", "2026-08-02",
                                   "2026-08-04"]})
    result = nc.check_source(_contract(max_staleness_days=None), frame,
                             as_of="2026-08-05")
    assert any("非单调" in p for p in result["problems"])


def test_healthy_source_is_ok():
    frame = pd.DataFrame({"title": list("abc"),
                          "date": ["2026-08-03", "2026-08-02", "2026-08-01"]})
    result = nc.check_source(_contract(), frame, as_of="2026-08-03")
    assert result["status"] == "OK" and result["problems"] == []


def test_schema_drift_is_reported():
    frame = pd.DataFrame({"title": ["a"], "date": ["2026-08-01"]})
    stale = nc.check_source(_contract(schema_hash="deadbeef"), frame, as_of="2026-08-01")
    assert stale["schema_drift"] is True


def test_contract_records_rate_limit_and_terms():
    result = nc.check_source(_contract(rate_limit="1/min", terms="个人研究"),
                             pd.DataFrame({"title": ["a"], "date": ["2026-08-01"]}),
                             as_of="2026-08-01")
    assert result["rate_limit"] == "1/min" and result["terms"] == "个人研究"


# ── 最小证伪步:1 日 manifest + 逐源对账 ───────────────────────


def _write_shards(day, rows_by_code):
    folder = day / "L3_news"
    folder.mkdir(parents=True, exist_ok=True)
    for code, rows in rows_by_code.items():
        (folder / f"{code}.json").write_text(json.dumps(rows, ensure_ascii=False),
                                             encoding="utf-8")


def test_manifest_reconciles_with_the_raw_shards(tmp_path):
    day = tmp_path / "2026-08-01"
    _write_shards(day, {
        "000651": [{"title": "回购公告", "ann_date": "2026-08-01"},
                   {"title": "增持公告", "ann_date": "2026-07-31"}],
        "600519": [{"title": "问询函回复", "ann_date": "2026-08-01"}],
    })
    result = nc.manifest_day(day, catalog=_cat(tmp_path))
    assert result["n_files"] == 2 and result["n_rows_in_shards"] == 3
    assert result["ingested"] == 3
    assert result["reconciled"] is True
    assert result["health"]["first_seen_missing_rate"] == 0.0


def test_manifest_marks_snapshot_basis_and_selective_scope(tmp_path):
    day = tmp_path / "2026-08-01"
    _write_shards(day, {"000651": [{"title": "回购公告", "ann_date": "2026-08-01"}]})
    cat = _cat(tmp_path)
    nc.manifest_day(day, catalog=cat)
    obs = cat.observations()
    assert set(obs["first_seen_basis"]) == {nc.BASIS_SNAPSHOT}
    assert set(obs["scope"]) == {nc.SCOPE_SELECTIVE}      # 逐票 → 不算市场热度
    assert set(obs["rule_version"]) == {nc.RULE_VERSION}


def test_manifest_is_idempotent(tmp_path):
    day = tmp_path / "2026-08-01"
    _write_shards(day, {"000651": [{"title": "回购公告", "ann_date": "2026-08-01"}]})
    cat = _cat(tmp_path)
    nc.manifest_day(day, catalog=cat)
    second = nc.manifest_day(day, catalog=cat)
    assert second["unchanged"] == 1 and second["ingested"] == 0
    assert len(cat.observations()) == 1


def test_manifest_survives_a_corrupt_shard(tmp_path):
    day = tmp_path / "2026-08-01"
    _write_shards(day, {"000651": [{"title": "回购公告", "ann_date": "2026-08-01"}]})
    (day / "L3_news" / "999999.json").write_text("{ nope", encoding="utf-8")
    result = nc.manifest_day(day, catalog=_cat(tmp_path))
    assert result["ingested"] == 1
    assert any(f.get("error") == "unreadable" for f in result["per_file"])


def test_manifest_snapshot_time_is_the_file_mtime(tmp_path):
    day = tmp_path / "2026-08-01"
    _write_shards(day, {"000651": [{"title": "回购公告", "ann_date": "2026-08-01"}]})
    cat = _cat(tmp_path)
    nc.manifest_day(day, catalog=cat)
    seen = cat.observations().iloc[0]["first_seen_ts"]
    mtime = datetime.fromtimestamp((day / "L3_news" / "000651.json").stat().st_mtime,
                                   tz=timezone.utc)
    assert abs(datetime.fromisoformat(seen) - mtime) < timedelta(seconds=2)


def test_manifest_on_a_day_without_news(tmp_path):
    day = tmp_path / "2026-08-01"
    day.mkdir(parents=True)
    result = nc.manifest_day(day, catalog=_cat(tmp_path))
    assert result["n_files"] == 0 and result["ingested"] == 0


# ── 盘点 ────────────────────────────────────────────────────────


def test_inventory_counts_shards_and_real_bytes(tmp_path):
    lake = tmp_path / "lake"
    (lake / "stock_news_em").mkdir(parents=True)
    for i in range(3):
        (lake / "stock_news_em" / f"000651@2026080{i}.parquet").write_bytes(b"x" * 100)
    cache = tmp_path / "fallback"
    cache.mkdir()
    (cache / "a.json").write_bytes(b"y" * 50)

    result = nc.inventory(lake, cache)
    assert result["sources"]["stock_news_em"]["n_shards"] == 3
    assert result["sources"]["stock_news_em"]["bytes"] == 300
    assert result["sources"]["anns_fallback"]["n_shards"] == 1
    assert result["total_bytes"] == 350          # 实测字节,不是「忽略不计」


def test_inventory_on_missing_lake(tmp_path):
    result = nc.inventory(tmp_path / "nope", tmp_path / "nope2")
    assert result["total_shards"] == 0 and result["total_bytes"] == 0


# ── 渲染 / CLI ─────────────────────────────────────────────────


def test_render_health_flags_a_missing_first_seen():
    md = nc.render_health({"n_observations": 10, "first_seen_missing_rate": 0.1,
                           "n_events": 5, "n_links": 8, "revisions": 1,
                           "by_source": {"a": 10}, "by_scope": {"market_wide": 10},
                           "by_basis": {"observed": 10}})
    assert "🚨" in md


def test_cli_manifest_then_replay(tmp_path, capsys):
    day = tmp_path / "scan" / "2026-08-01"
    _write_shards(day, {"000651": [{"title": "回购公告", "ann_date": "2026-08-01"}]})
    root = str(tmp_path / "catalog")
    assert nc.main(["--root", root, "manifest", "2026-08-01",
                    "--scan-root", str(tmp_path / "scan")]) == 0
    capsys.readouterr()
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    assert nc.main(["--root", root, "replay", "000651", "--cutoff", future]) == 0
    assert "1 条" in capsys.readouterr().out


def test_cli_health_exits_nonzero_when_first_seen_is_missing(tmp_path, monkeypatch):
    cat = _cat(tmp_path)
    cat.ingest([_obs()])
    obs = cat.observations()
    obs.loc[0, "first_seen_ts"] = ""
    cat._write(obs, cat.events(), cat.links())
    assert nc.main(["--root", str(tmp_path / "catalog"), "health"]) == 1


def test_cli_inventory(tmp_path, capsys):
    assert nc.main(["inventory"]) == 0
    assert "total_bytes" in capsys.readouterr().out
