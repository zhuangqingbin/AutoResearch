from __future__ import annotations

import pytest

from autoresearch.session_agent.validation import validate_news_evidence


def _evidence(**changes):
    value = {
        "schema_version": 1,
        "analysis_date": "2026-09-13",
        "title": "交易所公告",
        "claim": "公司披露董事会决议",
        "source_url": "https://example.com/aggregator",
        "source_tier": "T4",
        "published_at": None,
        "available_at": None,
        "canonical_status": "FOLLOWED",
        "canonical_url": "https://example.com/exchange/original",
    }
    value.update(changes)
    return value


def test_news_evidence_keeps_unknown_timing_unknown():
    result = validate_news_evidence(_evidence(), analysis_date="2026-09-13")
    assert result["published_at"] is None
    assert result["available_at"] is None


@pytest.mark.parametrize("field", ["published_at", "available_at"])
def test_news_evidence_rejects_future_information(field):
    with pytest.raises(ValueError, match="future"):
        validate_news_evidence(
            _evidence(**{field: "2026-09-14T00:00:00+08:00"}),
            analysis_date="2026-09-13",
        )


def test_aggregator_requires_a_canonical_followup():
    with pytest.raises(ValueError, match="canonical"):
        validate_news_evidence(
            _evidence(canonical_status="MISSING", canonical_url=None),
            analysis_date="2026-09-13",
        )


def test_evidence_contract_rejects_secret_or_credential_fields():
    value = _evidence()
    value["token"] = "secret"
    with pytest.raises(ValueError, match="fields mismatch"):
        validate_news_evidence(value, analysis_date="2026-09-13")

