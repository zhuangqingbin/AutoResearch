#!/usr/bin/env python3
"""DecisionRecord-first final-rating reads with explicit legacy fallback."""
from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from autoresearch.scan.decision_record import (
    DecisionRecord,
    load_decision_records,
)


def read_decisions(
    scan_dir: Path | str,
) -> dict[str, DecisionRecord]:
    """Load and verify the current scan's DecisionRecord book."""
    return load_decision_records(Path(scan_dir) / "decision_records.json")


def read_final_ratings(
    scan_dir: Path | str,
    *,
    card_fallback: Callable[[Path], dict[str, str]] | None = None,
) -> dict[str, str]:
    """Read final ratings from facts, then historical compatibility views.

    A present DecisionRecord book is authoritative and must validate. Historical
    dates may fall back to a non-empty legacy JSON object, then to an explicitly
    supplied card reader. A corrupt current fact book never falls through.
    """
    scan = Path(scan_dir)
    decision_path = scan / "decision_records.json"
    if decision_path.exists():
        return {
            code: record.final_rating
            for code, record in read_decisions(scan).items()
        }

    legacy = scan / "_final_ratings.json"
    if legacy.exists():
        try:
            raw = json.loads(legacy.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raw = None
        if isinstance(raw, dict) and raw:
            return {
                str(code).zfill(6): str(rating)
                for code, rating in raw.items()
            }

    return card_fallback(scan) if card_fallback is not None else {}
