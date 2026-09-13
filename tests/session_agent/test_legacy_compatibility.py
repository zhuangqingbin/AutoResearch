from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_legacy_workflows_remain_parseable_fallbacks_with_an_explicit_label():
    for name in ("scan-market.js", "l4-stock.js", "dossier-init.js"):
        text = (ROOT / ".claude/workflows" / name).read_text(encoding="utf-8")
        assert "LEGACY_ORCHESTRATION_FALLBACK" in text, name


def test_historical_domain_clis_remain_present_for_capsule_replay():
    for path in (
        "autoresearch/scan/assemble.py",
        "autoresearch/analyze/assemble.py",
        "autoresearch/macro/assemble.py",
        "autoresearch/scan/l4_tasks.py",
    ):
        assert (ROOT / path).is_file()
