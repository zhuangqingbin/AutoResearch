"""Shared forensic-run fixtures for the trace test package."""

from __future__ import annotations

import pytest

from tests.forensic_fixtures import (  # noqa: F401
    FIXTURE_DATE,
    FIXTURE_NOW,
    FIXTURES,
    begin_fixture_run,
    copy_fixture,
    read_jsonl,
    redirect_roots,
)


@pytest.fixture
def codex_run(tmp_path, monkeypatch):
    """One active Codex run plus a writable copy of the rollout fixture."""
    handle = begin_fixture_run(tmp_path, monkeypatch)
    source = copy_fixture("codex/rollout.jsonl", tmp_path / "harness")
    return handle, source
