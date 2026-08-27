"""Production workflows must put every deterministic module behind capture."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

WORKFLOWS = (
    Path(".claude/workflows/scan-market.js"),
    Path(".claude/workflows/l4-stock.js"),
)


def _executable_source(path: Path) -> str:
    return "\n".join(
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("//")
    )


@pytest.mark.parametrize("path", WORKFLOWS)
def test_workflow_requires_run_id_and_uses_run_scoped_staging(path):
    source = _executable_source(path)
    assert "run_id" in source
    assert "AUTORESEARCH_RUN_ID" in source
    assert "args.run_id" in source or "A.run_id" in source
    assert "/scan_runs/${RUN_ID}/staging/${date}" in source
    assert "`context_${ENGINE}/scan/${date}`" not in source
    assert "`${CTX}/scan/${date}`" not in source


@pytest.mark.parametrize("path", WORKFLOWS)
def test_no_deterministic_python_module_command_bypasses_capture(path):
    source = _executable_source(path)
    raw_modules = [
        match.group(0) for match in re.finditer(r"python -m autoresearch\.[A-Za-z0-9_.]+", source)
    ]
    assert raw_modules == ["python -m autoresearch.trace.exec_capture"]
    assert "${R}" not in source


def test_scan_workflow_has_stable_distinct_retry_and_stage_invocation_ids():
    source = _executable_source(WORKFLOWS[0])
    assert "const PYC = (stage, invocation" in source
    assert "PYC('frame', 'pack-check-attempt-1')" in source
    assert "PYC('frame', 'pack-check-attempt-2'" in source
    assert "PYC('l3', 'sector-list-attempt-1')" in source
    assert "PY('frame', 'frame-attempt-1')" in source
    assert "PY('frame', 'frame-attempt-2', 2)" in source
    assert "PY('prelude', 'prelude-attempt-1')" in source
    assert "PY('prelude', 'prelude-attempt-2', 2)" in source
    assert "PY('gate1', 'gate1-attempt-1')" in source
    assert "PY('gate2', 'gate2-attempt-1')" in source


def test_scan_handoff_propagates_run_and_engine_to_stock_workflows():
    source = _executable_source(WORKFLOWS[0])
    assert source.count("run_id: RUN_ID") >= 3
    assert source.count("engine: ENGINE") >= 3


def test_l4_workflow_invocation_ids_bind_stock_and_attempt():
    source = _executable_source(WORKFLOWS[1])
    assert "let taskAttempt = Math.max(1, Number(A.attempt) || 1)" in source
    assert "PY('l4', `l4-preflight-${code}-attempt-${taskAttempt}`, taskAttempt, code)" in source
    assert "PY('l4', `l4-prepare-${code}-attempt-${taskAttempt}`" in source
    assert "PY('l4', `l4-failure-${code}-attempt-${taskAttempt}`" in source
    assert "PY('l4', `l4-success-${code}-attempt-${taskAttempt}`" in source


@pytest.mark.parametrize("path", WORKFLOWS)
def test_workflow_propagates_engine_and_run_identity(path):
    source = _executable_source(path)
    assert "AUTORESEARCH_ENGINE=${ENGINE}" in source
    assert "--run-id ${RUN_ID}" in source
    assert "--stage ${stage}" in source
    assert "--invocation-id ${invocation}" in source
