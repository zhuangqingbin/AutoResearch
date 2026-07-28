"""L4 共享上下文的 schema/hash/原子写契约。"""
from __future__ import annotations

import json

import pytest

from autoresearch.scan.context_blocks import (
    read_context_block,
    write_context_block,
)
from autoresearch.scan.market import market_context_block, market_context_parts


def test_same_content_and_sources_are_byte_stable(tmp_path):
    scan = tmp_path / "2026-07-28"
    scan.mkdir()
    source = scan / "market_pack.json"
    source.write_text('{"regime":"risk_off"}\n', encoding="utf-8")

    first = write_context_block(
        scan,
        kind="market",
        scope="all",
        content="## 市场地形\n- risk_off\n",
        source_paths=[source],
    )
    before = first.path.read_bytes()
    second = write_context_block(
        scan,
        kind="market",
        scope="all",
        content="## 市场地形\n- risk_off\n",
        source_paths=[source],
    )

    assert second.path == first.path
    assert second.path.read_bytes() == before
    assert second.block.content_sha256 == first.block.content_sha256


def test_source_change_changes_source_hash_even_when_rendered_content_same(tmp_path):
    scan = tmp_path / "2026-07-28"
    scan.mkdir()
    source = scan / "market_pack.json"
    source.write_text('{"v":1}', encoding="utf-8")
    first = write_context_block(
        scan, kind="market", scope="all", content="same", source_paths=[source]
    )
    source.write_text('{"v":2}', encoding="utf-8")
    second = write_context_block(
        scan, kind="market", scope="all", content="same", source_paths=[source]
    )
    assert first.block.content_sha256 == second.block.content_sha256
    assert first.block.source_hashes != second.block.source_hashes


def test_corrupt_block_is_rejected(tmp_path):
    scan = tmp_path / "2026-07-28"
    scan.mkdir()
    written = write_context_block(
        scan, kind="dossier", scope="000001", content="known", source_paths=[]
    )
    raw = json.loads(written.path.read_text(encoding="utf-8"))
    raw["content"] = "tampered"
    written.path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="content_sha256"):
        read_context_block(written.path)


def test_market_context_parts_recombine_to_legacy_bytes():
    pack = {
        "regime": {"label": "risk_off"},
        "breadth": {"above_ma60": 0.2, "med_pct_60d": -3.0, "falling_knife": 0.1},
        "valuation": {"med_pe": 20, "pe_top_decile": 60, "pe_gt_60": 0.1},
        "money": {"main_pos": 0.3, "cmf_pos": 0.4},
        "sectors": {
            "red": [{"industry": "银行", "median_pct_60d": 8.0}],
            "black": [{"industry": "半导体", "median_pct_60d": -9.0}],
        },
    }
    common, sector = market_context_parts(pack, industry="银行")
    assert common + sector == market_context_block(pack, industry="银行")
    assert "本股所在板块" not in common
    assert "本股所在板块" in sector
