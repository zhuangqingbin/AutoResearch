from __future__ import annotations

import json
import os
import subprocess
import sys


def _request():
    return {
        "schema_version": 1,
        "kind": "stock-research",
        "requested_mode": "LITE",
        "analysis_date": "2026-09-13",
        "subject": "600519.SS",
        "peers": [],
        "asset_type": "stock",
        "name": None,
        "force_full": False,
        "host_profile": {
            "schema_version": 1,
            "engine": "codex",
            "session_ref": "session-main",
            "deterministic_exec": True,
            "capture_binding": True,
            "inference_handoff": True,
            "safe_resume": True,
            "independent_context": False,
            "native_dispatch": False,
            "web_search": False,
            "web_fetch": False,
            "observed_model": None,
            "observed_effort": None,
            "evidence_refs": ["test"],
        },
        "predecessor_run_id": None,
    }


def test_help_is_available_without_importing_a_workspace_engine():
    env = dict(os.environ)
    env.pop("AUTORESEARCH_ENGINE", None)
    result = subprocess.run(
        [sys.executable, "-m", "autoresearch.session_agent", "--help"],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0
    assert "begin" in result.stdout and "submit" in result.stdout
    assert "bind-host-evidence" in result.stdout


def test_non_help_command_requires_explicit_engine():
    env = dict(os.environ)
    env.pop("AUTORESEARCH_ENGINE", None)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "autoresearch.session_agent",
            "status",
            "--run-id",
            "20260913T010203000000Z",
        ],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["errors"][0]["code"] == "EXPLICIT_ENGINE_REQUIRED"


def test_begin_scalar_assertion_cannot_override_request(tmp_path):
    request_file = tmp_path / "request.json"
    request_file.write_text(json.dumps(_request()), encoding="utf-8")
    env = {**os.environ, "AUTORESEARCH_ENGINE": "codex"}
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "autoresearch.session_agent",
            "begin",
            "--request-file",
            str(request_file),
            "--kind",
            "scan-market",
        ],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["errors"][0]["code"] == "CONTRACT_ERROR"
    assert "conflicts" in payload["errors"][0]["message"]
