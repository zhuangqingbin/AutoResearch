"""截图 adapter:表头逐字锁死、账户来自参数或文件名、真身必须是文本。"""
from __future__ import annotations

import pytest

from autoresearch.broker import adapters, schema
from autoresearch.broker.adapters import screenshot
from autoresearch.data.contracts import DataContractError

HEADER = ",".join(screenshot.SCREENSHOT_HEADER)
ROW = "2026-08-25,09:31:05,000001,平安银行,证券买入,12.34,100,1234.00,5.00,0.00,0.01,0,-1239.01,100"


def _csv(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_header_is_verbatim_spec():
    assert screenshot.SCREENSHOT_HEADER == (
        "trade_date", "trade_time", "code", "name", "biz_type", "price", "qty", "amount",
        "commission", "stamp_tax", "transfer_fee", "other_fee", "net_amount", "balance_after")


def test_parse_account_from_filename(tmp_path):
    df = screenshot.parse(_csv(tmp_path, "gtht_20260825-20260826.csv", f"{HEADER}\n{ROW}\n"))
    assert list(df.columns) == list(schema.RAW_COLUMNS)
    r = df.iloc[0]
    assert r.account == "gtht" and r.code == "000001" and r.amount == "1234.00" and r.trade_id == ""


def test_account_param_conflicting_with_filename_raises(tmp_path):
    p = _csv(tmp_path, "gtht_20260825-20260826.csv", f"{HEADER}\n{ROW}\n")
    with pytest.raises(DataContractError, match="冲突"):
        screenshot.parse(p, account="tpy")
    assert screenshot.parse(p, account="gtht").iloc[0].account == "gtht"   # 一致则无事


def test_account_param_fills_when_filename_has_none(tmp_path):
    p = _csv(tmp_path, "shot.csv", f"{HEADER}\n{ROW}\n")
    assert screenshot.parse(p, account="tpy").iloc[0].account == "tpy"


def test_uppercase_filename_account(tmp_path):
    p = _csv(tmp_path, "GTHT_20260825-20260826.csv", f"{HEADER}\n{ROW}\n")
    assert screenshot.parse(p).iloc[0].account == "gtht"


def test_zero_byte_file_is_contract_error_not_pandas_error(tmp_path):
    p = tmp_path / "gtht_20260825-20260826.csv"
    p.write_bytes(b"")
    with pytest.raises(DataContractError, match="解析失败"):
        screenshot.parse(p)


def test_missing_account_raises(tmp_path):
    with pytest.raises(DataContractError, match="--account"):
        screenshot.parse(_csv(tmp_path, "shot.csv", f"{HEADER}\n{ROW}\n"))


def test_wrong_header_raises(tmp_path):
    bad = HEADER.replace("qty", "quantity")
    with pytest.raises(DataContractError, match="表头"):
        screenshot.parse(_csv(tmp_path, "gtht_20260825-20260826.csv", f"{bad}\n{ROW}\n"))


def test_non_text_body_raises(tmp_path):
    p = tmp_path / "gtht_20260825-20260826.csv"
    p.write_bytes(b"PK\x03\x04junk")
    with pytest.raises(DataContractError, match="真身"):
        screenshot.parse(p)


def test_registered_in_adapters(tmp_path):
    p = _csv(tmp_path, "gtht_20260825-20260826.csv", f"{HEADER}\n{ROW}\n")
    assert len(adapters.parse(p, "screenshot")) == 1


def test_blank_cells_stay_empty_strings(tmp_path):
    row = ROW.replace(",5.00,0.00,0.01,0,", ",,,,,")
    df = screenshot.parse(_csv(tmp_path, "gtht_20260825-20260826.csv", f"{HEADER}\n{row}\n"))
    assert df.iloc[0].commission == "" and df.iloc[0].other_fee == ""
