"""Claude transcript 的官方价折算契约。"""
from __future__ import annotations

import pytest

from autoresearch.trace.pricing import (
    KNOWN_MODEL_IDS,
    PRICE_SOURCE_EFFECTIVE_DATE,
    canonical_model_id,
    estimate_usd,
    price_for_model,
)


def _million_each() -> dict:
    return {
        "input": 1_000_000,
        "cache_read": 1_000_000,
        "cache_create_5m": 1_000_000,
        "cache_create_1h": 1_000_000,
        "output": 1_000_000,
    }


def test_opus5_standard_price_includes_cache_and_output():
    got = estimate_usd("claude-opus-5", _million_each())
    assert got["pricing_status"] == "PRICED"
    assert got["total_usd"] == pytest.approx(5 + 0.5 + 6.25 + 10 + 25)
    assert got["output_usd"] == pytest.approx(25)
    assert got["source_effective_date"] == PRICE_SOURCE_EFFECTIVE_DATE == "2026-10-03"


def test_opus55_has_its_own_row_and_is_not_priced_as_opus5():
    """2026-09-22 起 `opus` 别名指向 Opus 5.5;旧价表用子串 `"claude-opus-5" in id` 把它按 Opus 5 计价。"""
    got = estimate_usd("claude-opus-5-5", _million_each())
    assert got["pricing_status"] == "PRICED"
    assert got["price_profile"] == "opus55"
    assert got["total_usd"] == pytest.approx(4 + 0.20 + 5 + 8 + 20)
    assert got["cache_read_usd"] == pytest.approx(0.20)


def test_sonnet5_and_sonnet55_share_one_standard_price_at_any_date():
    """官方价表脚注 3:Sonnet 5 的 $2/$10 已成为标准价,原定 09-01 涨到 $3/$15 取消。"""
    for model in ("claude-sonnet-5", "claude-sonnet-5-5"):
        for as_of in ("2026-07-28", "2026-09-01", "2026-12-31"):
            profile = price_for_model(model, as_of=as_of)
            assert profile is not None, model
            assert (profile.input_per_mtok, profile.output_per_mtok) == (2.0, 10.0)
            assert profile.cache_read_per_mtok == 0.2


def test_fable51_cache_read_differs_from_fable5():
    assert price_for_model("claude-fable-5-1").cache_read_per_mtok == 0.25
    assert price_for_model("claude-fable-5").cache_read_per_mtok == 1.0
    assert price_for_model("claude-fable-5-1").input_per_mtok == 10.0


def test_haiku_dated_snapshot_and_context_suffix_resolve_to_the_base_id():
    assert price_for_model("claude-haiku-4-5-20251001").output_per_mtok == 5.0
    assert canonical_model_id("claude-opus-5-5[1m]") == "claude-opus-5-5"
    assert price_for_model("claude-opus-5-5[1m]").name == "opus55"


@pytest.mark.parametrize(
    "model",
    ["claude-opus-5-6", "claude-opus-6", "claude-sonnet-5-9", "claude-fable-5-2",
     "claude-mystery-9", "opus", "sonnet", "haiku", "", None],
)
def test_unregistered_id_is_unpriced_never_matched_by_prefix_or_family(model):
    got = estimate_usd(model, _million_each())
    assert got["pricing_status"] == "UNKNOWN"
    assert got["total_usd"] is None
    assert got["pricing_reason"] == f"unknown_model:{model or '—'}"


def test_known_model_ids_is_a_closed_set_of_full_ids():
    assert {"claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5", "claude-sonnet-5",
            "claude-fable-5-1", "claude-haiku-4-5"} <= KNOWN_MODEL_IDS
    assert all(model.startswith("claude-") for model in KNOWN_MODEL_IDS)
    assert all(price_for_model(model) is not None for model in KNOWN_MODEL_IDS)


def test_fast_mode_is_priced_only_for_listed_opus_models():
    opus55 = estimate_usd("claude-opus-5-5", _million_each(), speed="fast")
    assert opus55["pricing_status"] == "PRICED"
    assert opus55["input_usd"] == pytest.approx(8)
    assert opus55["output_usd"] == pytest.approx(40)
    assert opus55["cache_read_usd"] == pytest.approx(0.4)
    opus5 = estimate_usd("claude-opus-5", _million_each(), speed="fast")
    assert opus5["input_usd"] == pytest.approx(10) and opus5["output_usd"] == pytest.approx(50)


def test_unsupported_fast_mode_is_unpriced():
    got = estimate_usd("claude-sonnet-5", _million_each(), speed="fast")
    assert got["pricing_status"] == "UNKNOWN"
    assert got["pricing_reason"] == "unsupported_speed:fast"
