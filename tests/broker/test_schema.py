"""schema.normalize:补零/ts_code/side/数值化/同价分笔 seq/row_hash/trade_id 的行为锁。"""
from __future__ import annotations

import pandas as pd
import pytest

from autoresearch.broker import schema
from autoresearch.data.contracts import DataContractError

AT = "2026-08-27T10:00:00"


def _norm(df, kind="gtht", file="a.xlsx"):
    return schema.normalize(df, source_kind=kind, source_file=file, ingested_at=AT)


def test_normalize_code_zfill_ts_code_side_and_numbers(raw):
    out = _norm(raw())
    r = out.iloc[0]
    assert r.code == "000001" and r.ts_code == "000001.SZ" and r.side == "BUY"
    assert r.trade_date == "2026-08-26" and r.trade_time == "09:31:05"
    assert r.amount == 1234.0 and r.commission == 5.0 and pd.isna(r.other_fee)
    assert r.trade_id == "h:" + r.row_hash[:16]
    assert r.source_kind == "gtht" and r.source_file == "a.xlsx" and r.ingested_at == AT
    assert list(out.columns) == list(schema.RAW_STORE_COLUMNS)


def test_code_with_suffix_and_sh_board(raw):
    r = _norm(raw(code="600519.SS")).iloc[0]
    assert r.code == "600519" and r.ts_code == "600519.SH"
    assert _norm(raw(code="300857")).iloc[0].ts_code == "300857.SZ"


def test_fullwidth_digits_are_normalized(raw):
    r = _norm(raw(code="０００００１")).iloc[0]
    assert r.code == "000001" and r.ts_code == "000001.SZ"


def test_unparseable_number_is_a_level_not_silent_nan(raw):
    with pytest.raises(DataContractError, match="commission '5.0O' 不可解析"):
        _norm(raw(commission="5.0O"))


def test_seq_is_independent_of_file_row_order(raw):
    a = _norm(raw.rows({"trade_time": "09:31:05"}, {"trade_time": "09:32:00"}))
    b = _norm(raw.rows({"trade_time": "09:32:00"}, {"trade_time": "09:31:05"}))
    assert sorted(a.row_hash) == sorted(b.row_hash)


def test_seq_group_key_includes_account(raw):
    out = _norm(raw.rows({"account": "gtht"}, {"account": "tpy"}))
    assert out.seq.tolist() == [0, 0]


def test_natural_key_rounds_and_adds_amount_only_for_other(raw):
    buy = _norm(raw(price="12.340000001")).iloc[0]
    other = _norm(raw(code="", biz_type="利息归本", price="", qty="", amount="1.5")).iloc[0]
    kb, ko = schema.natural_key(buy), schema.natural_key(other)
    assert kb == ("gtht", "2026-08-26", "000001", "BUY", "12.3400", "100.0000", "")
    assert ko == ("gtht", "2026-08-26", "", "OTHER", "", "", "1.5000")
    assert schema.natural_key(buy.to_dict()) == kb


def test_to_side_keywords():
    assert schema.to_side("证券买入") == "BUY"
    assert schema.to_side("担保品卖出") == "SELL"
    assert schema.to_side("红利入账") == "OTHER"
    assert schema.to_side("买入撤单") == "OTHER"
    assert schema.to_side("") == "OTHER"


def test_same_price_split_fills_keep_both_rows(raw):
    out = _norm(raw.rows({}, {}))
    assert out.seq.tolist() == [0, 1]
    assert out.row_hash.nunique() == 2


def test_normalize_is_deterministic(raw):
    assert _norm(raw()).row_hash.tolist() == _norm(raw()).row_hash.tolist()


def test_hash_ignores_ingested_at_but_not_price(raw):
    a = schema.normalize(raw(), source_kind="gtht", source_file="a", ingested_at="2026-01-01T00:00:00")
    b = schema.normalize(raw(), source_kind="gtht", source_file="a", ingested_at="2026-02-02T00:00:00")
    c = _norm(raw(price="12.35"))
    assert a.row_hash.iloc[0] == b.row_hash.iloc[0] != c.row_hash.iloc[0]


def test_source_trade_id_wins_over_hash(raw):
    assert _norm(raw(trade_id="  A1B2 ")).iloc[0].trade_id == "A1B2"


def test_source_trade_id_hash_is_stable_across_export_ranges(raw):
    full = _norm(raw.rows(
        {"trade_id": "T1", "trade_time": "09:31:05"},
        {"trade_id": "T2", "trade_time": "09:32:00"},
    ), file="full.xlsx")
    partial = _norm(raw(trade_id="T2", trade_time="09:32:00"), file="partial.xlsx")
    assert full.loc[full.trade_id == "T2", "row_hash"].item() == partial.row_hash.item()


def test_other_row_without_code_or_price(raw):
    r = _norm(raw(code="", biz_type="利息归本", price="", qty="", amount="12.3")).iloc[0]
    assert r.side == "OTHER" and r.code == "" and r.ts_code == "" and pd.isna(r.price)


def test_missing_raw_column_raises(raw):
    with pytest.raises(DataContractError, match="缺列"):
        _norm(raw().drop(columns=["qty"]))


def test_empty_frame_normalizes_to_zero_rows(raw):
    out = _norm(raw().iloc[0:0])
    assert len(out) == 0 and list(out.columns) == list(schema.RAW_STORE_COLUMNS)
