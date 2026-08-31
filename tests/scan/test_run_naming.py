"""发布目录名:数据日在前、发布时刻在后(2026-08-28 用户裁定)。"""

from __future__ import annotations

from datetime import datetime

import pytest

from autoresearch.scan.run_naming import (
    format_run_dir,
    is_run_dir,
    parse_run_dir,
)


def test_new_format_puts_the_data_date_first():
    assert format_run_dir("2026-08-25", datetime(2026, 8, 26, 20, 0)) == "20260825-0826_2000"


def test_format_rejects_a_compact_date():
    # 传错这一段 = 整条复盘链(账本锚点/chain_view/session view)一起错,所以硬校验。
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        format_run_dir("20260825", datetime(2026, 8, 26, 20, 0))


def test_parse_new_format_reads_the_data_date():
    got = parse_run_dir("20260825-0826_2000")
    assert (got.analysis_date, got.published_mmdd, got.hhmm, got.legacy) == (
        "2026-08-25", "0826", "2000", False)
    assert got.published_date() == "2026-08-26"


def test_parse_legacy_refuses_to_guess_the_data_date():
    """legacy 首段是**跑动日**,把它当数据日读正是本模块要终结的那个错误。"""
    got = parse_run_dir("20260826_2000")
    assert got.legacy is True
    assert got.analysis_date is None          # 不猜 —— 真值只在 manifest.analysis_date
    assert got.run_local_date == "2026-08-26"


def test_published_year_rolls_over():
    got = parse_run_dir("20261231-0101_0030")
    assert got.published_date() == "2027-01-01"


@pytest.mark.parametrize("name", ["20260825-0826_2000", "20260826_2000"])
def test_is_run_dir_accepts_both_generations(name):
    assert is_run_dir(name)


@pytest.mark.parametrize("name", ["_ledger", "_failed", "_capsule_archive",
                                  "2026-08-25", "20260825-0826", "20260825_20260826_2000"])
def test_is_run_dir_rejects_the_neighbours(name):
    # `_ledger`/`_failed`/`_capsule_archive` 与 run 目录同级;旧判据(`[:2]=="20"` 且含 `_`)
    # 只是恰好因为它们没有 manifest.json 才没出事。
    assert not is_run_dir(name)


def test_new_format_sorts_by_data_date_not_run_date():
    """同一数据日的多次重跑要聚在一起 —— 旧格式按跑动日排会把它们打散。"""
    names = sorted(["20260825-0826_2000", "20260825-0825_2149", "20260824-0825_2100"])
    assert names == ["20260824-0825_2100", "20260825-0825_2149", "20260825-0826_2000"]
