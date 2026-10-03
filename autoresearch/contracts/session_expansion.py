"""Pure validation of a dynamic expansion against its frozen session plan."""
from __future__ import annotations

import re

from autoresearch.contracts.session_plan import validate_expansion, validate_plan


def apply_expansion(
    plan: dict,
    expansion: dict,
    *,
    existing_tasks: list[dict] | None = None,
) -> list[dict]:
    """Return the combined task view without mutating the frozen plan."""
    validate_plan(plan)
    templates = {item["template_id"]: item for item in plan["task_templates"]}
    validate_expansion(expansion, allowed_expanders=frozenset(templates))
    if expansion["plan_hash"] != plan["plan_hash"]:
        raise ValueError("expansion plan_hash mismatch")
    template = templates[expansion["template_id"]]
    _expansion_scope(plan, expansion)
    allowed_roles = set(template["allowed_roles"])
    for task in expansion["tasks"]:
        if task["kind"] == "INFERENCE" and task["role"] not in allowed_roles:
            raise ValueError(f"expanded role is not allowed: {task['role']}")

    current = list(plan["tasks"] if existing_tasks is None else existing_tasks)
    base_ids = {task["task_id"] for task in plan["tasks"]}
    if not base_ids <= {task["task_id"] for task in current}:
        raise ValueError("existing task view omits frozen plan tasks")
    combined = [*current, *expansion["tasks"]]
    ids = [item["task_id"] for item in combined]
    if len(ids) != len(set(ids)):
        raise ValueError("expanded task identity collision")
    known = set(ids)
    for task in combined:
        missing = set(task["dependencies"]) - known
        if missing:
            raise ValueError(f"expanded task has missing dependencies: {sorted(missing)}")
    return combined


def _expansion_scope(plan: dict, expansion: dict) -> str | None:
    template = next(item for item in plan["task_templates"] if item["template_id"] == expansion["template_id"])
    if template["expander"] not in {"scan.reviews.per-stock-v1", "scan.review3.per-stock-v1"}:
        return None
    kind = "plan" if template["template_id"] == "scan.reviews" else "decision"
    inputs = [row for row in expansion["input_artifacts"] if row["artifact_id"] != "research.frame"]
    if len(inputs) != 1:
        raise ValueError("stock-scoped review expansion requires one frozen input")
    artifact_id = inputs[0]["artifact_id"]
    if artifact_id == f"scan.review.{kind}":
        return None  # explicit empty-sentinel branch
    match = re.fullmatch(rf"scan\.review\.{kind}\.([0-9]{{6}})", artifact_id)
    if match is None or any(task.get("subject") != match.group(1) for task in expansion["tasks"]):
        raise ValueError("stock-scoped review expansion identity mismatch")
    return match.group(1)
