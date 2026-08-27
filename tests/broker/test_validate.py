"""schema.validate:A 级整文件拒收 / B 级记账 / 零股只 warn。"""
from __future__ import annotations

from datetime import date

import pytest

from autoresearch.broker import schema
from autoresearch.data import contracts
from autoresearch.data.contracts import DataContractError

TODAY = date(2026, 8, 27)


def _v(df_raw, **kw):
    df = schema.normalize(df_raw, source_kind="gtht", source_file="a.xlsx",
                          ingested_at="2026-08-27T10:00:00")
    return schema.validate(df, today=TODAY, **kw)


def test_empty_file_is_a_level(raw):
    with pytest.raises(DataContractError, match="0 行"):
        _v(raw().iloc[0:0])


def test_unknown_account_is_a_level(raw):
    with pytest.raises(DataContractError, match="账户"):
        _v(raw(account="xx"))


def test_future_or_garbage_date_is_a_level(raw):
    with pytest.raises(DataContractError, match="在未来"):
        _v(raw(trade_date="2099-01-01"))
    with pytest.raises(DataContractError, match="不可解析"):
        _v(raw(trade_date="昨天"))


def test_amount_identity_violation_is_a_level(raw):
    with pytest.raises(DataContractError, match="amount"):
        _v(raw(amount="1300"))          # 12.34×100 = 1234,差 66 元


def test_amount_tolerance_accepts_rounding(raw):
    rep = _v(raw(amount="1234.5"))      # 差 0.5 ≤ 1 元
    assert rep.rows == 1 and rep.accounts == ("gtht",)
    assert rep.period == ("2026-08-26", "2026-08-26")


def test_buy_without_code_or_qty_is_a_level(raw):
    with pytest.raises(DataContractError, match="code"):
        _v(raw(code=""))
    with pytest.raises(DataContractError, match="qty"):
        _v(raw(qty="0"))


def test_other_row_may_lack_code_and_price(raw):
    rep = _v(raw(code="", biz_type="利息归本", price="", qty="", amount="12.3"))
    assert rep.rows == 1 and rep.b_degradations == {}


def test_a_level_rejects_whole_file_even_if_one_row_bad(raw):
    with pytest.raises(DataContractError, match="第2行"):
        _v(raw.rows({}, {"amount": "9999"}))


def test_fee_missing_is_b_level_and_recorded(raw):
    contracts.clear_degradations()
    rep = _v(raw(commission="", other_fee="0"))
    assert rep.b_degradations == {"commission 缺": 1}
    recs = [d for d in contracts.degradations() if d["endpoint"] == "broker/gtht"]
    assert recs and recs[0]["key"] == "a.xlsx" and "commission 缺 ×1" in recs[0]["reasons"]


def test_net_amount_mismatch_is_b_level(raw):
    rep = _v(raw(net_amount="-1000", other_fee="0"))
    assert rep.b_degradations == {"net_amount 与 amount±费用 偏差>1元": 1}


def test_net_amount_consistent_sell(raw):
    rep = _v(raw(biz_type="证券卖出", price="12.5", qty="100", amount="1250",
                 commission="5", stamp_tax="0.63", transfer_fee="0.01", other_fee="0",
                 net_amount="1244.36"))
    assert rep.b_degradations == {}


def test_odd_lot_buy_is_warning_only(raw):
    rep = _v(raw(qty="150", amount="1851", other_fee="0", net_amount="-1856.01"))
    assert rep.warnings == ["BUY 非 100 股整数倍 ×1"] and rep.b_degradations == {}
