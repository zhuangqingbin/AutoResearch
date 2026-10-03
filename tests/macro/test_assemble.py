"""Unit tests for autoresearch.macro.assemble: keyed-row allocation parsing + assembly."""
import re
import shutil
import sys
from pathlib import Path

import pytest

from autoresearch.macro import assemble as assemble_macro


@pytest.mark.unit
def test_parse_allocation_extracts_every_keyed_row():
    text = (
        "**跨资产配置表**\n\n"
        "- OVERALL 风险档: **Rating**: Hold — 中性偏防御\n"
        "- 美债: **Rating**: Overweight — 衰退对冲\n"
        "- USD: **Rating**: Underweight — 降息在即\n"
        "- 加密(BTC): **Rating**: Buy — 流动性+美元替代\n"
    )
    alloc = assemble_macro.parse_allocation(text)
    assert alloc["OVERALL 风险档"] == "Hold"
    assert alloc["美债"] == "Overweight"
    assert alloc["USD"] == "Underweight"
    assert alloc["加密(BTC)"] == "Buy"


@pytest.mark.unit
def test_parse_allocation_ignores_non_rating_lines():
    text = "# 标题\n普通段落,无评级。\n- 美股: 走强但拥挤\n"
    assert assemble_macro.parse_allocation(text) == {}


def _write(root: Path, rel: str, body: str):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


@pytest.mark.unit
def test_main_reports_missing_when_core_files_absent(tmp_path, capsys):
    argv = sys.argv
    sys.argv = ["assemble_macro", str(tmp_path)]
    try:
        ret = assemble_macro.main()
    finally:
        sys.argv = argv
    assert ret == 1
    assert "[MISSING]" in capsys.readouterr().out


@pytest.mark.unit
def test_main_assembles_and_validates_both_tables(tmp_path, capsys):
    # minimal required set
    _write(tmp_path, "1_spine/decision.md",
           "**配置表**\n" + "\n".join(
               f"- {key}: **Rating**: {'Overweight' if key == '美债' else 'Hold'}"
               for key in assemble_macro.CROSS_ASSET_KEYS
           ))
    _write(tmp_path, "data.md",
           "**行业资金净流入(tushare)**:\n| 行业 | 主力净流入(亿) | 领涨股 |\n"
           "|---|---:|---|\n| 电子 | 1 | 示例甲 |\n| 地产 | -1 | 示例乙 |\n")
    for rel in ("1_spine/variant.md", "1_spine/crossfire.md", "1_spine/calendar.md",
                "1_spine/premortem.md", "2_meso/flows.md", "2_meso/sentiment.md",
                "2_meso/themes.md", "3_regional/us.md", "3_regional/china.md",
                "3_regional/global.md", "4_crossasset/rates.md", "4_crossasset/fx.md",
                "4_crossasset/equities.md", "4_crossasset/commodities.md",
                "4_crossasset/crypto.md", "5_sinous/divergence.md", "5_sinous/desync.md",
                "5_sinous/geopolitics.md", "5_sinous/relative.md"):
        _write(tmp_path, rel, f"stub {rel}")
    _write(tmp_path, "2_meso/sector_map.md",
           "**行业表**\n- 电子: **Rating**: Overweight\n- 地产: **Rating**: Underweight\n")
    argv = sys.argv
    sys.argv = ["assemble_macro", str(tmp_path)]
    try:
        ret = assemble_macro.main()
    finally:
        sys.argv = argv
    out = capsys.readouterr().out
    assert ret == 0
    # main() writes reports/macro/<root.name no dashes>/<HHMM>_summary.md (relative to cwd)
    # and prints the real path on an `[assembled] <path>` line — assert THAT file exists.
    m = re.search(r"\[assembled\]\s+(\S+_summary\.md)", out)
    assert m, f"no [assembled] line in output:\n{out}"
    produced = Path(m.group(1))
    try:
        assert produced.exists()
        assert produced.name.endswith("_summary.md")
        assert produced.parent.name == tmp_path.name.replace("-", "")
        assert "cross-asset (11)" in out         # deterministic FULL scope
        assert "A股 sectors (2)" in out           # 电子 + 地产
    finally:
        shutil.rmtree(produced.parent, ignore_errors=True)


@pytest.mark.parametrize("text", [
    "- 美债: **Rating**: Strong Buy",
    "- 美债: **Rating**: Buyish",
    "- 美债: **Rating**: Hold/Buy",
    "- 美债: **Rating**: Hold\n- 美债: **Rating**: Hold",
    "- : **Rating**: Hold",
    "- **美债**: **Rating**: Hold",
    "美债: **Rating**: Hold",
    "- 美债 **Rating**: Hold",
    "- 美债: **Rating**:",
])
def test_allocation_rejects_malformed_rating_rows(text):
    with pytest.raises(ValueError):
        assemble_macro.parse_allocation(text)


def test_allocation_checks_only_declared_keys():
    text = "- 电子: **Rating**: Hold\n- 煤炭: **Rating**: Sell"
    assert assemble_macro.parse_allocation(text, expected_keys=["电子", "煤炭"]) == {
        "电子": "Hold", "煤炭": "Sell",
    }
    for expected in (["电子"], ["电子", "煤炭", "银行"], ["电子", "电子"], [""]):
        with pytest.raises(ValueError):
            assemble_macro.parse_allocation(text, expected_keys=expected)
    with pytest.raises(ValueError):
        assemble_macro.parse_allocation("", expected_keys=["电子"])
    assert assemble_macro.parse_allocation("", expected_keys=[]) == {}


@pytest.mark.parametrize("decision,expected", [
    ("- 美债: **Rating**: Invalid", None),
    ("", None),
    ("- 美债: **Rating**: Hold", {assemble_macro.DECISION_REL: ["美债", "黄金"]}),
])
def test_macro_assembly_checks_allocation_before_any_publication(tmp_path, decision, expected):
    root = tmp_path / "drafts"
    _write(root, assemble_macro.DECISION_REL, decision)
    for _, sections in assemble_macro.SPINE + assemble_macro.MESO + assemble_macro.APPENDIX:
        for _, relative, optional in sections:
            if not optional:
                _write(root, relative, "content")
    _write(root, assemble_macro.SECTOR_MAP_REL, "- 电子: **Rating**: Hold")
    output = tmp_path / "report"
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    previous = state_dir / "macro_state.json"
    previous.write_text('{"as_of": "2026-01-01"}')
    before = previous.read_bytes()
    assert assemble_macro.main([
        str(root), "--output-dir", str(output), "--state-out-dir", str(state_dir),
    ], expected_keys=expected) == 1
    assert not output.exists()
    assert previous.read_bytes() == before
