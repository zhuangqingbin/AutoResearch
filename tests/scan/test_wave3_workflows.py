"""Wave 3 工作流开关：流式 L4、稳定上下文和 finalist-only 行业 brief。"""
from __future__ import annotations

from pathlib import Path

WF = Path(".claude/workflows")


def test_scan_workflow_has_streaming_and_byte_compatible_legacy_branches():
    src = (WF / "scan-market.js").read_text(encoding="utf-8")
    assert "streaming_l4" in src
    assert "stable_context_blocks" in src
    assert "sector_brief_mode" in src
    assert "autoresearch.scan.l4_tasks init" in src
    assert "dispatch_batches" in src

    _, marker, legacy = src.partition("if (!streamingL4)")
    assert marker
    before_legacy = src[: src.index(marker)]
    assert "harvest-slim" not in before_legacy.rsplit("const plan =", 1)[-1]
    assert "harvest-slim" in legacy


def test_l4_stock_preflights_then_runs_slim_and_intel_in_parallel():
    src = (WF / "l4-stock.js").read_text(encoding="utf-8")
    preflight_at = src.index("`preflight ${code} ${date}`")
    card_at = src.index("phase('Card')")
    success_at = src.index("`success ${code} ${date}`")

    assert preflight_at < card_at < success_at
    intel = src[src.index("phase('Intel')"):card_at]
    assert "parallel([" in intel
    assert "`prepare ${code} ${date}`" in intel
    assert "agent(" in intel
    assert "DATA_INTEGRITY" in intel


def test_task_success_is_presence_gated_for_legacy_direct_invocations():
    src = (WF / "l4-stock.js").read_text(encoding="utf-8")
    assert "test -s ${TASK_BOOK}" in src
    assert '"action":"LEGACY"' in src
    assert "`success ${code} ${date}`" in src


def test_finalist_only_sector_briefs_run_after_gate2_before_prompts():
    src = (WF / "scan-market.js").read_text(encoding="utf-8")
    gate2 = src.index("const g2 =")
    finalist_briefs = src.index("finalistBriefSectors")
    prompts = src.index("autoresearch.scan.agents.l4_card prompts")

    assert gate2 < finalist_briefs < prompts
    assert "sectorBriefMode === 'all'" in src
    assert "sectorBriefMode === 'finalist_only'" in src
    assert "new Set" in src[finalist_briefs:prompts]
