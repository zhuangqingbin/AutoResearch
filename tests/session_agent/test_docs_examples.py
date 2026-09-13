from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from autoresearch.contracts.session_task import validate_begin_request

ROOT = Path(__file__).resolve().parents[2]


def test_versioned_begin_request_examples_match_the_live_contract():
    examples = sorted((ROOT / "docs/session-agent/examples").glob("*.request.json"))
    assert len(examples) == 5
    for path in examples:
        value = json.loads(path.read_text(encoding="utf-8"))
        assert validate_begin_request(value, expected_engine="codex") == value


def test_documented_cli_subcommands_are_executable_parser_entries():
    env = dict(os.environ)
    env["AUTORESEARCH_ENGINE"] = "codex"
    for command in ("begin", "status", "next", "claim", "execute", "submit", "fail", "retry-l4", "resume", "finish"):
        result = subprocess.run(
            [sys.executable, "-m", "autoresearch.session_agent", command, "--help"],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0, (command, result.stderr)
