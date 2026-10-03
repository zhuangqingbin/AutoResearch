"""Active skill guidance must keep performance claims bound to their original ruler."""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import pytest

from autoresearch.common.ruler import MAIN_RULER

ROOT = Path(__file__).resolve().parents[1]
REGISTER = ROOT / "docs/research/2026-09-30-agent-skills-evidence-register.md"
ACTIVE_DOCS = (
    ROOT / ".claude/skills/scan-market/STAGES.md",
    ROOT / "docs/flow-handbook.md",
    ROOT / "docs/research/scan-negative-results.md",
)
STATUSES = {
    "CURRENT_SUPPORTED", "HISTORICAL_ONLY", "INSUFFICIENT", "REQUIRES_REVIEW", "REFUTED",
}
FIELDS = {
    "claim_id", "ruler", "strategy_version", "sample_window", "sample_status",
    "evidence_path", "applicability", "revalidation_trigger",
}
CLAIM_LINK = re.compile(r"\[证据:([a-z0-9_]+)\]\(([^)]+)\)")
LEGACY_EFFECTS = {
    "historical_l4_rating_ic": re.compile(r"(?:rank-IC|评级[^。;；]*IC)\s*\*{0,2}\+0\.55"),
    "historical_l4_rejection_value": re.compile(r"门价值[^。;；]*\+4\.35pp"),
    "historical_recall_capture": re.compile(r"4\.8%[^。;；]*(?:召回|过线)|(?:召回|过线)[^。;；]*4\.8%"),
    "historical_value_channel": re.compile(r"57\.6%"),
}


def _claims() -> dict[str, dict]:
    assert REGISTER.is_file(), "A2 evidence register is missing"
    text = REGISTER.read_text(encoding="utf-8")
    rows = [json.loads(body) for body in re.findall(r"```json\s*\n(.*?)\n```", text, re.S)]
    assert rows, "evidence register must contain reviewable JSON claim records"
    assert all(set(row) == FIELDS for row in rows)
    ids = [row["claim_id"] for row in rows]
    assert len(ids) == len(set(ids)), "duplicate claim IDs silently replace evidence"
    return {row["claim_id"]: row for row in rows}


def _unsupported_legacy_lines(text: str) -> list[str]:
    unsupported = []
    for line in text.splitlines():
        required = {claim_id for claim_id, pattern in LEGACY_EFFECTS.items() if pattern.search(line)}
        cited = {claim_id for claim_id, _ in CLAIM_LINK.findall(line)}
        if required and (
            not required <= cited or not any(word in line for word in ("历史", "旧尺", "待复核"))
        ):
            unsupported.append(line)
    return unsupported


def test_register_records_have_sources_scope_and_revalidation_triggers():
    for claim_id, row in _claims().items():
        assert re.fullmatch(r"[a-z][a-z0-9_]+", claim_id)
        assert row["sample_status"] in STATUSES
        assert row["ruler"] and row["strategy_version"] and row["applicability"]
        assert {"ruler", "selection_rule", "execution_policy"} <= set(row["revalidation_trigger"])
        source = Path(row["evidence_path"])
        assert not source.is_absolute() and ".." not in source.parts
        assert source.parts[0] == "docs", "do not borrow another engine's mutable evidence"
        assert (ROOT / source).is_file(), f"missing evidence for {claim_id}: {source}"
        window = row["sample_window"]
        if window is None:
            assert row["sample_status"] == "REQUIRES_REVIEW", claim_id
        else:
            assert set(window) == {"start", "end"}
            assert date.fromisoformat(window["start"]) <= date.fromisoformat(window["end"])
        if row["sample_status"] == "CURRENT_SUPPORTED":
            assert row["ruler"] == MAIN_RULER
            assert window is not None


def test_historical_gate_value_cannot_be_promoted_by_changing_the_label():
    row = _claims()["historical_l4_rejection_value"]
    assert row["ruler"] == "fwd_2_oc"
    assert row["sample_window"] == {"start": "2026-06-18", "end": "2026-07-08"}
    assert row["sample_status"] == "HISTORICAL_ONLY"
    assert "gap_c1_o2" in row["applicability"]


def test_unreported_windows_remain_unknown_instead_of_using_report_dates():
    claims = _claims()
    for claim_id in (
        "historical_l4_rating_ic", "historical_value_channel", "historical_recall_capture",
        "historical_index_rebalance", "historical_index_prediction",
    ):
        assert claims[claim_id]["sample_window"] is None
        assert claims[claim_id]["sample_status"] == "REQUIRES_REVIEW"
    assert claims["historical_l4_rating_ic"]["ruler"] == "fwd_2_oc"
    assert claims["historical_value_channel"]["ruler"].startswith("UNKNOWN")


def test_current_ruler_rejection_evidence_retains_its_small_sample_limit():
    row = _claims()["overnight_l4_rejection_unproven"]
    assert row["ruler"] == MAIN_RULER == "gap_c1_o2"
    assert row["sample_window"] == {"start": "2026-06-22", "end": "2026-08-19"}
    assert row["sample_status"] == "INSUFFICIENT"
    assert row["evidence_path"] == "docs/research/2026-08-22-edge-census.md"


def test_same_ruler_channel_reading_cannot_hide_calibration_overlap():
    row = _claims()["overnight_channel_census"]
    assert row["ruler"] == MAIN_RULER
    assert row["sample_status"] == "INSUFFICIENT"
    assert "calibrated" in row["strategy_version"]
    assert "preference" in row["revalidation_trigger"]["selection_rule"]


def test_current_supported_negative_result_keeps_execution_population():
    row = _claims()["overnight_chase_executable"]
    assert row["ruler"] == MAIN_RULER
    assert row["sample_status"] == "CURRENT_SUPPORTED"
    assert row["sample_window"] == {"start": "2022-03-02", "end": "2026-08-05"}
    assert "原可执行人口" in row["applicability"]
    assert "当夜打板" in row["applicability"]


@pytest.mark.parametrize("path", ACTIVE_DOCS, ids=lambda path: path.name)
def test_active_claim_links_resolve_to_registered_evidence(path: Path):
    claims = _claims()
    links = CLAIM_LINK.findall(path.read_text(encoding="utf-8"))
    assert links, f"{path.name} has no scoped evidence references"
    for claim_id, target in links:
        assert claim_id in claims, f"{path.name}: unregistered claim {claim_id}"
        filename, separator, anchor = target.partition("#")
        assert separator and anchor == claim_id
        assert (path.parent / filename).resolve() == REGISTER.resolve()
        assert f'<a id="{claim_id}"></a>' in REGISTER.read_text(encoding="utf-8")


@pytest.mark.parametrize("path", ACTIVE_DOCS, ids=lambda path: path.name)
def test_active_legacy_effect_numbers_require_inline_historical_scope(path: Path):
    assert not _unsupported_legacy_lines(path.read_text(encoding="utf-8")), path.name


@pytest.mark.parametrize("path", ACTIVE_DOCS[:2], ids=lambda path: path.name)
def test_active_overview_does_not_claim_a_proven_current_rejection_edge(path: Path):
    text = path.read_text(encoding="utf-8")
    assert "判断层已证的 edge" not in text
    assert "系统真正有效的是 L4 的「拒绝」" not in text
    assert "0 买的根因在召回线" not in text
    assert "[证据:overnight_l4_rejection_unproven]" in text
    assert "[证据:historical_recall_capture]" in text
    assert "[证据:historical_value_channel]" in text


def test_effect_reference_check_rejects_unqualified_or_uncited_claims():
    assert _unsupported_legacy_lines("L4 评级 rank-IC +0.55，因此当前拒绝有效。")
    assert _unsupported_legacy_lines("旧尺门价值 +4.35pp，因此不用再验。")
    assert _unsupported_legacy_lines(
        "历史旧尺门价值 +4.35pp；"
        "[证据:historical_value_channel](register.md#historical_value_channel)"
    )
    assert not _unsupported_legacy_lines(
        "历史旧尺门价值 +4.35pp，仅限原窗口；"
        "[证据:historical_l4_rejection_value](register.md#historical_l4_rejection_value)"
    )
