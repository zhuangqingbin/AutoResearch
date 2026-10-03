"""Frozen plan builders for every research entry point."""
from __future__ import annotations


def _build_domain_plan(request: dict, handle) -> dict:
    if request["kind"] == "scan-market":
        from autoresearch.session_agent.workflows.scan import build_scan_plan

        return build_scan_plan(request, handle)
    if request["kind"] == "stock-research":
        from autoresearch.session_agent.workflows.stock import build_stock_plan

        return build_stock_plan(request, handle)
    if request["kind"] == "macro-research":
        from autoresearch.session_agent.workflows.macro import build_macro_plan

        return build_macro_plan(request, handle)
    if request["kind"] == "sector-research":
        from autoresearch.session_agent.workflows.sector import build_sector_plan

        return build_sector_plan(request, handle)
    if request["kind"] == "dossier-init":
        from autoresearch.session_agent.workflows.dossier import build_dossier_plan

        return build_dossier_plan(request, handle)
    raise ValueError(f"session workflow is not implemented: {request['kind']}")


def build_plan(request: dict, handle) -> dict:
    from autoresearch.session_agent.decision_frame import attach_plan

    return attach_plan(_build_domain_plan(request, handle))


def register_artifacts(request: dict, handle, plan: dict) -> None:
    from autoresearch.session_agent.decision_frame import frame_in_plan, register_frame

    if frame_in_plan(plan):
        register_frame(request, handle)
    _register_domain_artifacts(request, handle, plan)


def _register_domain_artifacts(request: dict, handle, plan: dict) -> None:
    if request["kind"] == "scan-market":
        from autoresearch.session_agent.workflows.scan import register_scan_artifacts

        register_scan_artifacts(request, handle, plan)
        return
    if request["kind"] == "stock-research":
        from autoresearch.session_agent.workflows.stock import register_stock_artifacts

        register_stock_artifacts(request, handle, plan)
        return
    if request["kind"] == "macro-research":
        from autoresearch.session_agent.workflows.macro import register_macro_artifacts

        register_macro_artifacts(request, handle, plan)
        return
    if request["kind"] == "sector-research":
        from autoresearch.session_agent.workflows.sector import register_sector_artifacts

        register_sector_artifacts(request, handle, plan)
        return
    if request["kind"] == "dossier-init":
        from autoresearch.session_agent.workflows.dossier import register_dossier_artifacts

        register_dossier_artifacts(request, handle, plan)
        return
    raise ValueError(f"session artifact workflow is not implemented: {request['kind']}")


def validate_operation_params(request: dict, task: dict, params: dict) -> None:
    if task.get("operation") == "research.calculate":
        from autoresearch.session_agent.operations import build_argv

        build_argv("research.calculate", params)
        if not set(params["input_artifact_ids"]) <= set(task["input_artifact_ids"]):
            raise ValueError("calculation inputs are outside the frozen task")
        return
    if request["kind"] == "scan-market":
        from autoresearch.session_agent.workflows.scan import (
            validate_scan_operation_params,
        )

        validate_scan_operation_params(request, task, params)
        return
    if request["kind"] == "stock-research":
        from autoresearch.session_agent.workflows.stock import validate_stock_operation_params

        validate_stock_operation_params(request, task, params)
        return
    if request["kind"] == "macro-research":
        from autoresearch.session_agent.workflows.macro import (
            validate_macro_operation_params,
        )

        validate_macro_operation_params(request, task, params)
        return
    if request["kind"] == "sector-research":
        from autoresearch.session_agent.workflows.sector import (
            validate_sector_operation_params,
        )

        validate_sector_operation_params(request, task, params)
        return
    if request["kind"] == "dossier-init":
        from autoresearch.session_agent.workflows.dossier import (
            validate_dossier_operation_params,
        )

        validate_dossier_operation_params(request, task, params)
        return
    raise ValueError(f"session operation workflow is not implemented: {request['kind']}")


def expansions_after_task(request: dict, handle, plan: dict, task: dict) -> list[dict]:
    if request["kind"] == "sector-research":
        from autoresearch.session_agent.workflows.sector import expansions_after_task as expand
    elif request["kind"] == "scan-market":
        from autoresearch.session_agent.workflows.scan import expansions_after_task as expand
    else:
        return []

    expansions = expand(request, handle, plan, task)
    from autoresearch.session_agent import artifacts
    from autoresearch.session_agent.decision_frame import (
        FRAME_ID, attach_expansion, frame_in_plan,
    )

    if frame_in_plan(plan) and expansions:
        snapshot = artifacts.snapshot_artifact(handle, FRAME_ID)
        return [attach_expansion(value, snapshot) for value in expansions]
    return expansions


def inapplicable_templates(plan: dict, handle) -> frozenset[str]:
    """Templates the run's frozen facts make unreachable (``DONE`` must not wait for them)."""
    if plan["run_kind"] != "scan-market":
        return frozenset()
    from autoresearch.session_agent.workflows.scan import inapplicable_templates as scan

    return scan(handle)


def register_expansion_artifacts(request: dict, handle, expansion: dict) -> None:
    if request["kind"] == "sector-research":
        from autoresearch.session_agent.workflows.sector import register_sector_artifacts
        register_sector_artifacts(request, handle, expansion)
        return
    if request["kind"] != "scan-market":
        return
    from autoresearch.session_agent.workflows.scan import (
        register_scan_expansion_artifacts,
    )

    from autoresearch.session_agent import artifacts
    from autoresearch.session_agent.decision_frame import FRAME_ID

    # Shared metadata has its own immutable registration; domain registries only own
    # their domain artifacts and must not re-register the clock as a writable output.
    if any(FRAME_ID in task["input_artifact_ids"] for task in expansion["tasks"]):
        artifacts.snapshot_artifact(handle, FRAME_ID)
    registration = dict(expansion, tasks=[
        dict(task, input_artifact_ids=[item for item in task["input_artifact_ids"] if item != FRAME_ID])
        for task in expansion["tasks"]
    ])
    register_scan_expansion_artifacts(request, handle, registration)


__all__ = [
    "build_plan",
    "expansions_after_task",
    "inapplicable_templates",
    "register_artifacts",
    "register_expansion_artifacts",
    "validate_operation_params",
]
