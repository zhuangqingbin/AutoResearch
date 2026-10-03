from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from autoresearch.analyze import runctl
from autoresearch.common.atomic import canonical_json, sha256_bytes
from autoresearch.session_agent import service
from autoresearch.session_agent.hosts.base import HostCapabilityError
from autoresearch.trace import capsule as capsule_mod
from tests.forensic_fixtures import redirect_roots

from .test_service import _profile, _request


@pytest.mark.parametrize(
    ("kind", "mode", "subject", "asset_type"),
    [
        ("scan-market", "AUTO", None, None),
        ("stock-research", "LITE", "600519.SS", "stock"),
        ("macro-research", "LITE", None, None),
        ("sector-research", "LITE", "半导体", None),
        ("dossier-init", "INIT", "600519", None),
    ],
)
def test_every_workflow_freezes_its_actual_session_origin(
    tmp_path,
    monkeypatch,
    kind,
    mode,
    subject,
    asset_type,
):
    redirect_roots(monkeypatch, tmp_path)
    request = {
        **_request(),
        "kind": kind,
        "requested_mode": mode,
        "subject": subject,
        "asset_type": asset_type,
        "host_profile": {**_profile(), "independent_context": True,
                         "web_search": True, "web_fetch": True},
    }

    result = service.begin(request)
    handle = capsule_mod.require_active_run(result["run_id"])
    origin = json.loads(
        (handle.capsule / "identity/execution_origin.json").read_text(encoding="utf-8")
    )

    assert origin["run_kind"] == kind
    assert origin["orchestration"] == "session_v1"
    assert origin["entrypoint"] == "autoresearch.session_agent.begin"


def test_session_begin_freezes_a_verifiable_execution_origin(tmp_path, monkeypatch):
    redirect_roots(monkeypatch, tmp_path)

    result = service.begin(_request())
    handle = capsule_mod.require_active_run(result["run_id"])
    origin_path = handle.capsule / "identity/execution_origin.json"
    origin = json.loads(origin_path.read_text(encoding="utf-8"))
    plan = json.loads((handle.workspace / "session/plan.json").read_text(encoding="utf-8"))

    assert origin == {
        "schema_version": 1,
        "engine": "codex",
        "run_id": handle.run_id,
        "run_kind": "stock-research",
        "orchestration": "session_v1",
        "entrypoint": "autoresearch.session_agent.begin",
        "plan_hash": plan["plan_hash"],
        "host_profile_hash": sha256_bytes(canonical_json(_profile()).encode("utf-8")),
        "legacy_reason": None,
        "created_at": origin["created_at"],
    }
    from autoresearch.session_agent.origin import verify_execution_origin

    assert verify_execution_origin(handle) == {"verified": True, "missing": []}

    from autoresearch.session_agent.evaluation import orchestration_status

    assert orchestration_status(handle) == {
        "orchestration": "session_v1",
        "orchestration_verified": True,
        "missing": [],
    }


def test_session_begin_rejects_missing_host_capability_before_creating_run(
    tmp_path,
    monkeypatch,
):
    redirect_roots(monkeypatch, tmp_path)
    request = _request()
    request["host_profile"] = {
        **request["host_profile"],
        "inference_handoff": False,
    }

    with pytest.raises(HostCapabilityError, match="inference_handoff"):
        service.begin(request)

    assert not any(tmp_path.rglob("state.json"))


def test_legacy_stock_begin_requires_a_reason_before_creating_run(tmp_path, monkeypatch):
    # Historical origin contract after a simulated legacy entry capability.
    from autoresearch.contracts import research_access
    monkeypatch.setattr(research_access, 'require_legacy_access', lambda: None)
    redirect_roots(monkeypatch, tmp_path)

    with pytest.raises(ValueError, match="legacy_reason"):
        runctl.begin(
            "600519.SS",
            "2026-09-14",
            mode="LITE",
            session_ref="legacy-session",
        )

    assert not any(tmp_path.rglob("state.json"))


def test_legacy_stock_begin_freezes_the_explicit_reason(tmp_path, monkeypatch):
    # Historical origin contract after a simulated legacy entry capability.
    from autoresearch.contracts import research_access
    monkeypatch.setattr(research_access, 'require_legacy_access', lambda: None)
    redirect_roots(monkeypatch, tmp_path)

    result = runctl.begin(
        "600519.SS",
        "2026-09-14",
        mode="LITE",
        session_ref="legacy-session",
        legacy_reason="session host handoff unavailable during controlled drill",
    )
    handle = capsule_mod.require_active_run(result["run_id"])
    origin = json.loads(
        (handle.capsule / "identity/execution_origin.json").read_text(encoding="utf-8")
    )

    assert origin["orchestration"] == "legacy"
    assert origin["entrypoint"] == "autoresearch.analyze.runctl.begin"
    assert origin["legacy_reason"] == (
        "session host handoff unavailable during controlled drill"
    )
    assert origin["plan_hash"] is None
    assert origin["host_profile_hash"] is None


def test_session_cli_never_silently_executes_the_legacy_entrypoint(tmp_path):
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(_request()), encoding="utf-8")
    env = {
        **os.environ,
        "AUTORESEARCH_ENGINE": "codex",
        "PYTHONPATH": str(os.getcwd()),
    }
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "autoresearch.session_agent",
            "begin",
            "--request-file",
            str(request_path),
            "--orchestration",
            "legacy",
            "--legacy-reason",
            "operator requested compatibility drill",
        ],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 7
    payload = json.loads(result.stdout)
    assert payload["errors"] == [
        {
            "code": "LEGACY_ENTRYPOINT_REQUIRED",
            "message": (
                "session_agent does not execute legacy workflows; use the explicit "
                "workflow legacy entrypoint"
            ),
        }
    ]
    assert not any(tmp_path.rglob("state.json"))


def test_generic_capsule_legacy_begin_requires_and_freezes_a_reason(
    tmp_path,
    monkeypatch,
    capsys,
):
    # Historical origin contract after a simulated legacy entry capability.
    from autoresearch.contracts import research_access
    monkeypatch.setattr(research_access, 'require_legacy_access', lambda: None)
    redirect_roots(monkeypatch, tmp_path)
    config = tmp_path / "scan-config.json"
    config.write_text("{}", encoding="utf-8")

    with pytest.raises(SystemExit):
        capsule_mod.main([
            "begin",
            "scan-market",
            "2026-09-14",
            "--engine",
            "codex",
            "--config-file",
            str(config),
        ])
    assert not any(tmp_path.rglob("state.json"))
    capsys.readouterr()

    assert capsule_mod.main([
        "begin",
        "scan-market",
        "2026-09-14",
        "--engine",
        "codex",
        "--config-file",
        str(config),
        "--legacy-reason",
        "operator requested compatibility drill",
    ]) == 0
    result = json.loads(capsys.readouterr().out)
    handle = capsule_mod.require_active_run(result["run_id"])
    origin = json.loads(
        (handle.capsule / "identity/execution_origin.json").read_text(encoding="utf-8")
    )
    assert origin["orchestration"] == "legacy"
    assert origin["entrypoint"] == "autoresearch.trace.capsule.begin"
    assert origin["legacy_reason"] == "operator requested compatibility drill"
