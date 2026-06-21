"""Unit tests for autoresearch.analyze.assemble pure helpers (filename / slug / A股 detection)."""
import pytest

from autoresearch.analyze import assemble


@pytest.mark.unit
def test_is_ashare_matches_six_digit_codes_only():
    for t in ("600519", "000001.SZ", "688981.SS", "830799.BJ"):
        assert assemble._is_ashare(t)
    for t in ("NVDA", "0700.HK", "BTC-USD", "AAPL", ""):
        assert not assemble._is_ashare(t)


@pytest.mark.unit
def test_safe_name_strips_unsafe_chars_and_star_st():
    assert assemble._safe_name("*ST中潜") == "ST中潜"
    assert assemble._safe_name("a/b:c?d") == "abcd"
    assert assemble._safe_name("  ") == "未命名"          # empty after strip -> fallback


@pytest.mark.unit
def test_slug_keeps_cjk_drops_punct_spaces_to_dash():
    assert assemble._slug("S1 · 执行摘要 (PM)") == "s1-执行摘要-pm"


@pytest.mark.unit
def test_resolve_filename_ashare_falls_back_to_code(tmp_path):
    # no --name, no context to mine -> 6-digit code
    assert assemble._resolve_filename("600519.SS", tmp_path, None) == "600519"
    # explicit name wins
    assert assemble._resolve_filename("600519.SS", tmp_path, "贵州茅台") == "贵州茅台"
    # non-A-share -> ticker passthrough (safe-named)
    assert assemble._resolve_filename("NVDA", tmp_path, None) == "NVDA"
