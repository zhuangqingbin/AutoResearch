"""Publication adapters for completed subscription-session runs."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from autoresearch.contracts.profiles import profile_factory
from autoresearch.session_agent.roles import role_stage

_PUBLISHERS: dict[str, Callable[[object], object]] = {}


def register_publisher(run_kind: str, publisher: Callable[[object], object]) -> None:
    if not run_kind or not callable(publisher):
        raise ValueError("run kind and callable publisher required")
    if run_kind in _PUBLISHERS and _PUBLISHERS[run_kind] is not publisher:
        raise RuntimeError(f"publisher already registered: {run_kind}")
    _PUBLISHERS[run_kind] = publisher


def publish(handle):
    """Invoke the registered domain publisher for a finished task graph."""
    try:
        publisher = _PUBLISHERS[handle.contract.run_kind]
    except KeyError as exc:
        raise RuntimeError(
            f"no session publisher registered for {handle.contract.run_kind}"
        ) from exc
    return publisher(handle)


def session_profile(handle, *, business_status: str = "SUCCEEDED"):
    """Build the legacy-compatible evidence profile from the frozen task plan."""
    plan_path = Path(handle.workspace) / "session/plan.json"
    frozen_plan = json.loads(plan_path.read_text(encoding="utf-8"))
    tasks = list(frozen_plan["tasks"])
    expansion_root = Path(handle.workspace) / "session/expansions"
    if expansion_root.is_dir():
        for path in sorted(expansion_root.glob("*.json")):
            expansion = json.loads(path.read_text(encoding="utf-8"))
            tasks.extend(expansion["tasks"])
    roles = tuple(
        dict.fromkeys(task["role"] for task in tasks if task["kind"] == "INFERENCE")
    )
    mapping = {role: role_stage(role) for role in roles}
    from autoresearch.trace.capsule import _last_reliable_checkpoint, resolve_run_mode

    return profile_factory(handle.contract.run_kind)(
        mode=resolve_run_mode(handle),
        business_status=business_status,
        last_stage=_last_reliable_checkpoint(handle.capsule),
        agent_roles=roles,
        role_stages=mapping,
    )


__all__ = ["publish", "register_publisher", "session_profile"]
