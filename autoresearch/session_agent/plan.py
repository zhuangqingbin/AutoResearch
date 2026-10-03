"""Freeze session plans and validate deterministic dynamic expansions."""
from __future__ import annotations

import json
from pathlib import Path

from autoresearch.common.atomic import atomic_write_json, canonical_json
from autoresearch.contracts.session_expansion import _expansion_scope, apply_expansion
from autoresearch.contracts.session_plan import validate_plan


def ready_tasks(tasks: list[dict], states: dict[str, str]) -> list[dict]:
    """Return PENDING tasks whose declared dependencies all succeeded."""
    def parent_running(task: dict) -> bool:
        parent = task.get("parent_task")
        if parent is None:
            return True
        parent_id = f"l4.{parent['subject']}.a{parent['attempt']}"
        return states.get(parent_id) == "RUNNING"

    return [
        task
        for task in tasks
        if states.get(task["task_id"], "PENDING") == "PENDING"
        and parent_running(task)
        and all(
            states.get(dependency) in {"SUCCEEDED", "SUPERSEDED"}
            for dependency in task["dependencies"]
        )
    ]


def freeze_plan(path: Path | str, payload: dict) -> Path:
    """Write a validated plan once; reordered JSON is an idempotent replay."""
    target = Path(path)
    if target.is_file():
        current = json.loads(target.read_text(encoding="utf-8"))
        if canonical_json(current) != canonical_json(payload):
            raise RuntimeError("frozen plan conflict")
        validate_plan(current)
        return target
    validate_plan(payload)
    return atomic_write_json(target, payload)



def persist_expansion(
    session_dir: Path | str,
    plan: dict,
    expansion: dict,
    *,
    existing_tasks: list[dict] | None = None,
) -> Path:
    """Persist one immutable expansion per template and frozen input snapshot."""
    scope = _expansion_scope(plan, expansion)
    root = Path(session_dir) / "expansions"
    root.mkdir(parents=True, exist_ok=True)
    for candidate in sorted(root.glob("*.json")):
        current = json.loads(candidate.read_text(encoding="utf-8"))
        if current.get("template_id") != expansion["template_id"]:
            continue
        if _expansion_scope(plan, current) != scope:
            continue
        if canonical_json(current) == canonical_json(expansion):
            return candidate
        raise RuntimeError("template input conflict")
    apply_expansion(plan, expansion, existing_tasks=existing_tasks)
    return atomic_write_json(root / f"{expansion['expansion_id']}.json", expansion)


__all__ = ["apply_expansion", "freeze_plan", "persist_expansion", "ready_tasks"]
