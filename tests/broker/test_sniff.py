"""sniff:不信扩展名只信 magic bytes;GBK/BOM 文本都能读;来源按父目录识别。"""
from __future__ import annotations

import pytest

from autoresearch.broker import adapters, sniff


def _w(tmp_path, name, data: bytes):
    p = tmp_path / name
    p.write_bytes(data)
    return p


def test_magic_bytes_beat_extension(tmp_path):
    assert sniff.sniff(_w(tmp_path, "a.xls", b"PK\x03\x04rest")) == "xlsx"
    assert sniff.sniff(_w(tmp_path, "b.txt", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1x")) == "xls"
    assert sniff.sniff(_w(tmp_path, "c.xlsx", b"%PDF-1.4\n")) == "pdf"
    assert sniff.sniff(_w(tmp_path, "d.csv", b"\x89PNG\r\n")) == "image"
    assert sniff.sniff(_w(tmp_path, "e.csv", b"\xff\xd8\xff\xe0")) == "image"


def test_gbk_html_disguised_as_xls(tmp_path):
    p = _w(tmp_path, "交割单.xls",
           "<html><table><tr><td>成交日期</td></tr></table></html>".encode("gbk"))
    assert sniff.sniff(p) == "html"


def test_gbk_tab_text_reads_back(tmp_path):
    p = _w(tmp_path, "交割单.xls", "成交日期\t证券代码\n20260826\t000001\n".encode("gbk"))
    assert sniff.sniff(p) == "text"
    assert sniff.read_text(p).splitlines()[0] == "成交日期\t证券代码"


def test_utf8_bom_is_stripped(tmp_path):
    p = _w(tmp_path, "s.csv", "﻿trade_date,code\n".encode())
    assert sniff.read_text(p).startswith("trade_date")


def test_undecodable_raises(tmp_path):
    p = _w(tmp_path, "bin.csv", b"\xff\xfe\x00\x00\x81\x81\xff\xff")
    with pytest.raises(ValueError, match="gb18030"):
        sniff.read_text(p)


def test_detect_source_kind_by_parent_dir(tmp_path):
    for kind in ("chinaclear", "gtht", "tpy", "screenshot"):
        d = tmp_path / kind
        d.mkdir()
        assert adapters.detect_source_kind(d / "x.csv") == kind
    assert adapters.detect_source_kind(tmp_path / "misc" / "x.csv") is None


def test_parse_unknown_source_raises_listing_known(tmp_path):
    with pytest.raises(ValueError, match="尚无 adapter"):
        adapters.parse(tmp_path / "x", "chinaclear")
