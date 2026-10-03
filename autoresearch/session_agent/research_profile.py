"""Freeze the opt-in research graph before building a new session plan."""
from __future__ import annotations

import json
from pathlib import Path

from autoresearch.common.atomic import atomic_write_json
from autoresearch.contracts.profiles import validate_card_research_profile


def freeze_research_profile(handle, request: dict) -> str:
    selected = validate_card_research_profile(request.get("card_research_profile", "single-stage-v1"))
    path = Path(handle.capsule) / "verification/profile.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    if "card_research_profile" in payload:
        if validate_card_research_profile(payload["card_research_profile"]) != selected:
            raise ValueError("card research profile differs from frozen identity")
        return selected
    if (Path(handle.workspace) / "session/plan.json").exists():
        if selected != "single-stage-v1":
            raise ValueError("historical plan cannot be upgraded to two-stage research")
        return selected
    atomic_write_json(path, {**payload, "card_research_profile": selected})
    return selected
