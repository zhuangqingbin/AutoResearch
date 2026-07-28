"""Claude transcript 的官方价折算契约。"""
from __future__ import annotations

import pytest

from autoresearch.trace.pricing import estimate_usd, price_for_model


def _million_each() -> dict:
    return {
        "input": 1_000_000,
        "cache_read": 1_000_000,
        "cache_create_5m": 1_000_000,
        "cache_create_1h": 1_000_000,
        "output": 1_000_000,
    }


def test_opus5_standard_price_includes_cache_and_output():
    got = estimate_usd("claude-opus-5", _million_each(), as_of="2026-07-28")
    assert got["pricing_status"] == "PRICED"
    assert got["total_usd"] == pytest.approx(5 + 0.5 + 6.25 + 10 + 25)
    assert got["output_usd"] == pytest.approx(25)
    assert got["source_effective_date"] == "2026-07-28"


def test_sonnet5_uses_introductory_price_during_july_2026():
    profile = price_for_model("claude-sonnet-5", as_of="2026-07-28")
    assert profile is not None
    assert profile.input_per_mtok == 2.0
    assert profile.output_per_mtok == 10.0


def test_sonnet5_uses_standard_price_after_intro_window():
    profile = price_for_model("claude-sonnet-5", as_of="2026-09-01")
    assert profile is not None
    assert profile.input_per_mtok == 3.0
    assert profile.output_per_mtok == 15.0


def test_current_fable_and_haiku_prices_are_explicit():
    assert price_for_model("claude-fable-5", as_of="2026-07-28").input_per_mtok == 10.0
    assert price_for_model(
        "claude-haiku-4-5-20251001", as_of="2026-07-28"
    ).output_per_mtok == 5.0


def test_unknown_model_is_unpriced_instead_of_family_guessed():
    got = estimate_usd("claude-mystery-9", _million_each(), as_of="2026-07-28")
    assert got["pricing_status"] == "UNKNOWN"
    assert got["total_usd"] is None


def test_unsupported_fast_mode_is_unpriced():
    got = estimate_usd(
        "claude-sonnet-5", _million_each(), as_of="2026-07-28", speed="fast"
    )
    assert got["pricing_status"] == "UNKNOWN"
    assert got["pricing_reason"] == "unsupported_speed:fast"
