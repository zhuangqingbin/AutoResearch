from __future__ import annotations

import re
from types import SimpleNamespace

from autoresearch.session_agent.workflows.dossier import build_dossier_plan

from .test_service import _profile


def _request():
    return {
        "schema_version": 1,
        "kind": "dossier-init",
        "requested_mode": "INIT",
        "analysis_date": "2026-09-13",
        "subject": "600519",
        "peers": [],
        "asset_type": None,
        "name": "贵州茅台",
        "force_full": False,
        "host_profile": {**_profile(), "web_search": True, "web_fetch": True},
        "predecessor_run_id": None,
    }


def _context(tmp_path):
    return SimpleNamespace(
        run_id="20260913T010203000000Z",
        engine="codex",
        workspace=tmp_path,
        staging=tmp_path / "staging/2026-09-13",
        contract=SimpleNamespace(contract_hash="a" * 64, config_hash="b" * 64),
    )


def test_dossier_plan_has_one_research_owner_and_deterministic_guards(tmp_path):
    plan = build_dossier_plan(_request(), _context(tmp_path))
    tasks = plan["tasks"]
    assert [task["task_id"].split(".")[-1] for task in tasks] == [
        "prefetch",
        "skeleton",
        "research",
        "lint",
        "publish",
    ]
    assert [task["role"] for task in tasks if task["kind"] == "INFERENCE"] == [
        "dossier.init"
    ]
    assert all(re.fullmatch(r"[a-z0-9_.-]+", task["task_id"]) for task in tasks)
