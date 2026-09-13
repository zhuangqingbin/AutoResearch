from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from autoresearch.session_agent.artifacts import (
    ArtifactConflict,
    bind_artifact_hash,
    open_artifact,
    register_artifact,
)


def _handle(tmp_path):
    workspace = tmp_path / "context_codex" / "analyze_runs" / "20260913T010203000000Z"
    workspace.mkdir(parents=True)
    return SimpleNamespace(workspace=workspace, engine="codex", run_id="20260913T010203000000Z")


def test_registered_input_is_opened_by_identity_and_hash(tmp_path):
    handle = _handle(tmp_path)
    source = handle.workspace / "staging" / "slim.md"
    source.parent.mkdir()
    source.write_bytes(b"verified input")
    descriptor = register_artifact(handle, "stock.slim", source, "READ")
    assert descriptor["sha256"] is not None
    with open_artifact(handle, "stock.slim") as stream:
        assert stream.read() == b"verified input"
    registry = json.loads((handle.workspace / "session" / "artifacts.json").read_text())
    assert registry["artifacts"]["stock.slim"] == descriptor


def test_changed_or_replaced_input_is_rejected(tmp_path):
    handle = _handle(tmp_path)
    source = handle.workspace / "staging" / "slim.md"
    source.parent.mkdir()
    source.write_text("before")
    register_artifact(handle, "stock.slim", source, "READ")
    source.write_text("after")
    with pytest.raises(ArtifactConflict, match="changed"):
        open_artifact(handle, "stock.slim")


def test_path_escape_and_symlink_escape_are_rejected(tmp_path):
    handle = _handle(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    with pytest.raises(ValueError, match="outside run workspace"):
        register_artifact(handle, "bad.absolute", outside, "READ")
    link = handle.workspace / "outside-link"
    link.symlink_to(outside)
    with pytest.raises(ValueError, match="symlink"):
        register_artifact(handle, "bad.link", link, "READ")


def test_output_is_predeclared_then_bound_once(tmp_path):
    handle = _handle(tmp_path)
    output = handle.workspace / "staging" / "card.md"
    descriptor = register_artifact(handle, "stock.card", output, "WRITE")
    assert descriptor["sha256"] is None
    output.parent.mkdir(exist_ok=True)
    output.write_text("# card")
    bound = bind_artifact_hash(handle, "stock.card")
    assert bound["sha256"] is not None
    assert bind_artifact_hash(handle, "stock.card") == bound
    output.write_text("# replaced")
    with pytest.raises(ArtifactConflict, match="changed"):
        bind_artifact_hash(handle, "stock.card")

