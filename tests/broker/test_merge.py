"""store.merge:同价分笔按计数匹配、优先级只补缺、单源直通、trades.csv 列与排序。"""
from __future__ import annotations

import pandas as pd

from autoresearch.broker import schema, store

AT = "2026-08-27T10:00:00"


def _put(tmp_path, df_raw, kind, file="f", at=AT):
    df = schema.normalize(df_raw, source_kind=kind, source_file=file, ingested_at=at)
    store.upsert_raw(df, tmp_path)
    return df


def test_count_matching_two_fills_vs_one(tmp_path, raw):
    _put(tmp_path, raw.rows({}, {}), "gtht")                       # 同价两笔
    _put(tmp_path, raw(commission="", name=""), "chinaclear")      # 同键一笔
    t = store.merge(tmp_path)
    assert len(t) == 2
    assert sorted(t.sources) == ["gtht", "gtht+chinaclear"]
    assert list(t.columns) == list(schema.TRADES_COLUMNS)
    assert store.trades_path(tmp_path).exists()


def test_lower_priority_fills_blank_only(tmp_path, raw):
    g = _put(tmp_path, raw(commission=""), "gtht")
    _put(tmp_path, raw(commission="4.5", name="别名", trade_id="CC1"), "chinaclear")
    r = store.merge(tmp_path).iloc[0]
    assert r.commission == 4.5                 # 主源缺 → 补
    assert r["name"] == "平安银行"              # 主源有 → 不动(r.name 是 Series 索引标签,不能用)
    assert g.iloc[0].trade_id.startswith("h:") and r.trade_id == "CC1"   # 主源只有哈希 id → 取真成交编号
    assert r.sources == "gtht+chinaclear" and r.source_kind == "gtht"


def test_primary_real_trade_id_is_kept(tmp_path, raw):
    _put(tmp_path, raw(trade_id="GG1"), "gtht")
    _put(tmp_path, raw(trade_id="CC1"), "chinaclear")
    assert store.merge(tmp_path).iloc[0].trade_id == "GG1"


def test_reverse_count_matching_lower_priority_has_more(tmp_path, raw):
    _put(tmp_path, raw(), "gtht")
    _put(tmp_path, raw.rows({}, {}), "chinaclear")
    t = store.merge(tmp_path)
    assert len(t) == 2 and sorted(t.sources) == ["chinaclear", "gtht+chinaclear"]


def test_float_jitter_still_matches(tmp_path, raw):
    _put(tmp_path, raw(), "gtht")
    _put(tmp_path, raw(price="12.340000001", amount="1234.0000001"), "chinaclear")
    t = store.merge(tmp_path)
    assert len(t) == 1 and t.iloc[0].sources == "gtht+chinaclear"


def test_other_rows_with_different_amounts_do_not_collapse(tmp_path, raw):
    _put(tmp_path, raw(code="", biz_type="利息归本", price="", qty="", amount="1.5"), "gtht")
    _put(tmp_path, raw(code="", biz_type="银证转入", price="", qty="", amount="50000"), "chinaclear")
    t = store.merge(tmp_path)
    assert len(t) == 2 and sorted(t.amount) == [1.5, 50000.0]


def test_same_other_event_across_sources_merges(tmp_path, raw):
    _put(tmp_path, raw(biz_type="红利入账", price="", qty="", amount="88"), "gtht")
    _put(tmp_path, raw(biz_type="股息入账", price="", qty="", amount="88"), "chinaclear")
    t = store.merge(tmp_path)
    assert len(t) == 1 and t.iloc[0].sources == "gtht+chinaclear"


def test_ingested_at_is_earliest_across_sources(tmp_path, raw):
    _put(tmp_path, raw(), "gtht", at="2026-02-02T00:00:00")
    _put(tmp_path, raw(), "chinaclear", at="2026-01-01T00:00:00")
    assert store.merge(tmp_path).iloc[0].ingested_at == "2026-01-01T00:00:00"


def test_priority_screenshot_is_lowest(tmp_path, raw):
    _put(tmp_path, raw(), "screenshot")
    _put(tmp_path, raw(), "chinaclear")
    r = store.merge(tmp_path).iloc[0]
    assert r.source_kind == "chinaclear" and r.sources == "chinaclear+screenshot"


def test_single_source_rows_pass_through_sorted(tmp_path, raw):
    _put(tmp_path, raw.rows({"trade_date": "20260827", "biz_type": "证券卖出"},
                            {"trade_date": "20260826"}), "gtht")
    t = store.merge(tmp_path)
    assert t.trade_date.tolist() == ["2026-08-26", "2026-08-27"]
    assert set(t.sources) == {"gtht"}


def test_other_rows_without_code_survive_merge(tmp_path, raw):
    _put(tmp_path, raw(code="", biz_type="利息归本", price="", qty="", amount="1.5"), "gtht")
    t = store.merge(tmp_path)
    assert len(t) == 1 and t.iloc[0].side == "OTHER" and pd.isna(t.iloc[0].price)


def test_merge_without_raw_writes_header_only(tmp_path):
    t = store.merge(tmp_path)
    assert len(t) == 0 and list(t.columns) == list(schema.TRADES_COLUMNS)
    assert store.trades_path(tmp_path).read_text(encoding="utf-8").strip() == ",".join(schema.TRADES_COLUMNS)


def test_merge_is_rebuilt_not_appended(tmp_path, raw):
    _put(tmp_path, raw(), "gtht")
    store.merge(tmp_path)
    store.merge(tmp_path)
    assert len(store.merge(tmp_path)) == 1
