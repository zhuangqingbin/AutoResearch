"""Shared forensic-run fixtures for the trace test package."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest

from autoresearch.common import workspace as ws
from autoresearch.trace import capsule as capsule_mod

FIXTURES = Path(__file__).parent / "fixtures"
FIXTURE_DATE = "2026-08-27"
FIXTURE_NOW = datetime(2026, 8, 27, 1, 2, 3, 456789, tzinfo=timezone.utc)


def redirect_roots(monkeypatch, tmp_path: Path) -> None:
    """Point every engine root at ``tmp_path`` and stub identity snapshotting."""
    monkeypatch.setattr(ws, "ENGINE", "codex")
    monkeypatch.setattr(ws, "context_root", lambda: tmp_path / "context_codex")
    monkeypatch.setattr(ws, "reports_root", lambda: tmp_path / "reports_codex")
    monkeypatch.delenv("AUTORESEARCH_RUN_ID", raising=False)
    monkeypatch.setattr(
        "autoresearch.scan.user_config.DEFAULT_PINNED_PATH",
        tmp_path / "missing-pinned.jsonc",
    )
    monkeypatch.setattr(
        capsule_mod,
        "snapshot_identity",
        lambda *args, **kwargs: {
            "ok": True,
            "components": {},
            "missing": [],
            "errors": [],
        },
    )


def begin_fixture_run(tmp_path: Path, monkeypatch, *, now=FIXTURE_NOW):
    """Begin one hermetic Codex run rooted inside ``tmp_path``."""
    redirect_roots(monkeypatch, tmp_path)
    return capsule_mod.begin_run("scan-market", FIXTURE_DATE, "codex", {}, now=now)


def copy_fixture(relative: str, destination: Path) -> Path:
    """Copy one packaged transcript fixture into a writable location."""
    source = FIXTURES / relative
    target = Path(destination) / source.name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    return target


def read_jsonl(path: Path | str) -> list[dict]:
    text = Path(path).read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


@pytest.fixture
def codex_run(tmp_path, monkeypatch):
    """One active Codex run plus a writable copy of the rollout fixture."""
    handle = begin_fixture_run(tmp_path, monkeypatch)
    source = copy_fixture("codex/rollout.jsonl", tmp_path / "harness")
    return handle, source
