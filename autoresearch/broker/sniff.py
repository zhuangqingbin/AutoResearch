#!/usr/bin/env python3
"""文件真身嗅探 —— 按 magic bytes + 编码试探定类型,**不信扩展名**。

design: docs/specs/2026-08-27-broker-trades-ingest-design.md §9

已知坑:券商「xls」常是 GBK 制表符文本或 HTML 表(通达信系客户端「输出」就是这么干的),
按扩展名走 xlrd 会直接炸。编码依次试 utf-8-sig → gb18030(GBK 超集)。
"""
from __future__ import annotations

from pathlib import Path

_ENCODINGS = ("utf-8-sig", "gb18030")
_HEAD = 2048


def _decode(raw: bytes, *, strict: bool = True) -> str:
    for enc in _ENCODINGS:
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    if strict:
        raise ValueError(f"无法按 {'/'.join(_ENCODINGS)} 解码(gb18030 也失败)")
    return raw.decode("utf-8", errors="replace")


def sniff(path: Path) -> str:
    """→ 'xlsx' | 'xls' | 'pdf' | 'image' | 'html' | 'text'。"""
    head = Path(path).read_bytes()[:_HEAD]
    if head.startswith(b"PK\x03\x04"):
        return "xlsx"
    if head.startswith(b"\xd0\xcf\x11\xe0"):
        return "xls"
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith((b"\x89PNG", b"\xff\xd8")):
        return "image"
    low = _decode(head, strict=False).lower()
    if "<html" in low or "<table" in low:
        return "html"
    return "text"


def read_text(path: Path) -> str:
    return _decode(Path(path).read_bytes())
