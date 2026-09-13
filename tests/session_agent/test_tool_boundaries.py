from __future__ import annotations

from pathlib import Path

import pytest

from autoresearch.session_agent.hosts.base import HostCapabilityError, render_request
from autoresearch.session_agent.operations import operation_catalog, operation_spec
from autoresearch.session_agent.workflows import build_plan

from .test_dossier import _context as dossier_context, _request as dossier_request
from .test_macro import _context as macro_context, _request as macro_request
from .test_sector import _context as sector_context, _request as sector_request
from .test_service import _profile, _request as stock_request
from .test_stock_full import _full_request
from .test_stock_lite import _context as stock_context


def test_every_workflow_operation_has_complete_catalog_metadata(tmp_path):
    plans = [
        build_plan(stock_request(), stock_context(tmp_path)),
        build_plan(_full_request(), stock_context(tmp_path)),
        build_plan(macro_request("FULL"), macro_context(tmp_path)),
        build_plan(macro_request("LITE"), macro_context(tmp_path)),
        build_plan(sector_request("FULL"), sector_context(tmp_path)),
        build_plan(sector_request("LITE"), sector_context(tmp_path)),
        build_plan(dossier_request(), dossier_context(tmp_path)),
    ]
    used = {
        task["operation"]
        for plan in plans
        for task in plan["tasks"]
        if task["kind"] == "DETERMINISTIC" and task["operation"] != "test.noop"
    }
    catalog = operation_catalog()
    assert used <= set(catalog)
    required = {
        "params",
        "side_effects",
        "outputs",
        "callers",
        "errors",
        "limits",
        "idempotent",
        "stage",
        "retained_cli",
    }
    assert all(set(catalog[operation]) == required for operation in used)
    document = Path("docs/session-agent/tool-catalog.md").read_text(encoding="utf-8")
    assert all(f"`{operation}`" in document for operation in used)


def test_operation_registry_does_not_expose_arbitrary_modules():
    with pytest.raises(KeyError, match="unknown operation"):
        operation_spec("python.module.from.model")


def test_web_role_requires_observed_host_web_capability():
    task = {
        "task_id": "macro.region",
        "kind": "INFERENCE",
        "role": "macro.research",
        "operation": None,
        "dependencies": [],
        "input_artifact_ids": ["macro.data"],
        "output_artifact_ids": ["macro.region"],
        "expected_output_contract": "macro.section.v1",
        "owner": "SESSION",
        "subject": None,
        "independent_context": False,
        "parent_task": None,
    }
    profile = {**_profile(), "web_search": False, "web_fetch": False}
    with pytest.raises(HostCapabilityError, match="web capability"):
        render_request(task, profile)
