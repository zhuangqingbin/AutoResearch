"""reconcile:重叠期间三桶计数 + 金额差;无重叠/单源明说;只报不裁(退出码恒 0)。"""
from __future__ import annotations

from autoresearch.broker import reconcile, schema, store

AT = "2026-08-27T10:00:00"


def _put(tmp_path, df_raw, kind):
    store.upsert_raw(schema.normalize(df_raw, source_kind=kind, source_file=kind, ingested_at=AT),
                     tmp_path)


def test_three_buckets_and_amount_diff(tmp_path, raw):
    _put(tmp_path, raw.rows({}, {}, {"trade_date": "20260827", "price": "13", "amount": "1300"}),
         "gtht")
    _put(tmp_path, raw.rows({}, {"trade_date": "20260827", "price": "13.5", "amount": "1350"}),
         "chinaclear")
    out = reconcile.report(tmp_path)
    assert "gtht chinaclear↔gtht 2026-08-26..2026-08-27" in out
    assert "两边都有 1" in out and "仅 chinaclear 1" in out and "仅 gtht 2" in out
    assert "成交额 2,584 vs 3,768" in out


def test_no_overlap_is_said_not_hidden(tmp_path, raw):
    _put(tmp_path, raw(trade_date="20260801"), "gtht")
    _put(tmp_path, raw(trade_date="20260826"), "chinaclear")
    assert "无重叠期间" in reconcile.report(tmp_path)


def test_single_source_account_is_said(tmp_path, raw):
    _put(tmp_path, raw(), "gtht")
    _put(tmp_path, raw(account="tpy"), "screenshot")
    out = reconcile.report(tmp_path)
    assert "gtht:只有 ['gtht'] 一个来源" in out and "tpy:只有 ['screenshot'] 一个来源" in out


def test_since_and_account_filters(tmp_path, raw):
    _put(tmp_path, raw.rows({"trade_date": "20260801"}, {}), "gtht")
    _put(tmp_path, raw.rows({"trade_date": "20260801"}, {}), "chinaclear")
    out = reconcile.report(tmp_path, account="gtht", since="2026-08-20")
    assert "2026-08-26..2026-08-26" in out and "两边都有 1" in out


def test_main_prints_and_returns_zero(tmp_path, raw, capsys):
    _put(tmp_path, raw(), "gtht")
    assert reconcile.main(["--root", str(tmp_path)]) == 0
    assert "只报不裁" in capsys.readouterr().out


def test_empty_root(tmp_path):
    assert "无 raw" in reconcile.report(tmp_path / "nothing")


def test_other_rows_stay_out_of_buckets_but_are_reported(tmp_path, raw):
    _put(tmp_path, raw.rows({}, {"code": "", "biz_type": "利息归本", "price": "", "qty": "",
                                 "amount": "1.5"}), "gtht")
    _put(tmp_path, raw(), "chinaclear")
    out = reconcile.report(tmp_path)
    assert "两边都有 1 · 仅 chinaclear 0 · 仅 gtht 0" in out
    assert "OTHER 行(不入桶):chinaclear 0 / gtht 1" in out
    assert "桶只计 BUY/SELL" in out


def test_invalid_since_exits_2(tmp_path, raw, capsys):
    _put(tmp_path, raw(), "gtht")
    assert reconcile.main(["--root", str(tmp_path), "--since", "2026/08/01"]) == 0   # 可解析写法照收
    assert reconcile.main(["--root", str(tmp_path), "--since", "昨天"]) == 2
