"""Frozen plan builders for every research entry point."""
from __future__ import annotations


def build_plan(request: dict, handle) -> dict:
    if request["kind"] == "stock-research":
        from autoresearch.session_agent.workflows.stock import build_stock_plan

        return build_stock_plan(request, handle)
    raise ValueError(f"session workflow is not implemented: {request['kind']}")


def register_artifacts(request: dict, handle, plan: dict) -> None:
    if request["kind"] == "stock-research":
        from autoresearch.session_agent.workflows.stock import register_stock_artifacts

        register_stock_artifacts(request, handle, plan)
        return
    raise ValueError(f"session artifact workflow is not implemented: {request['kind']}")


def validate_operation_params(request: dict, task: dict, params: dict) -> None:
    if request["kind"] == "stock-research":
        from autoresearch.session_agent.workflows.stock import validate_stock_operation_params

        validate_stock_operation_params(request, task, params)
        return
    raise ValueError(f"session operation workflow is not implemented: {request['kind']}")


__all__ = ["build_plan", "register_artifacts", "validate_operation_params"]
