"""Card semantics replay context: what a live publish consumed is what replay restores."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from autoresearch.common.atomic import canonical_json, sha256_bytes

RUN_ID = "20261001T070429018207Z"


def _live_handle(tmp_path: Path) -> SimpleNamespace:
    workspace = tmp_path / "context_claude/analyze_runs" / RUN_ID
    capsule = workspace / "capsule"
    (capsule / "verification").mkdir(parents=True)
    (capsule / "verification/profile.json").write_text(canonical_json(
        {"card_rules_version": "skills-gap-v3", "card_rating_bands": {"Buy": 4}}))
    (workspace / "session").mkdir(parents=True)
    (workspace / "session/tasks.json").write_text(canonical_json({
        "engine": "claude", "run_id": RUN_ID, "tasks": {
            "stock.card": {"state": "SUCCEEDED", "attempt": 1, "spec": {"task_id": "stock.card"}},
            "stock.publish": {"state": "RUNNING", "attempt": 1,
                              "spec": {"task_id": "stock.publish"}}}}))
    claims = capsule / "evidence/material_claims"
    claims.mkdir(parents=True)
    (claims / "side-1.json").write_text(canonical_json(
        {"sidecar_id": "side-1", "task_id": "stock.card", "attempt": 1,
         "source_receipt_ids": ["r1"], "review_receipt_ids": ["r2"]}))
    lineage = capsule / "lineage"
    lineage.mkdir()
    rows = [{"receipt_id": "r1", "supersedes_receipt_ids": ["r0"]},
            {"receipt_id": "r2"}, {"receipt_id": "r0"}, {"receipt_id": "unrelated"}]
    (lineage / "source_receipts.jsonl").write_text(
        "".join(canonical_json(row) + "\n" for row in rows))
    return SimpleNamespace(run_id=RUN_ID, engine="claude", workspace=workspace, capsule=capsule)


def test_live_publish_freezes_exactly_what_semantics_consume(tmp_path, monkeypatch):
    from autoresearch.session_agent import card_semantics_context as context

    handle = _live_handle(tmp_path)
    monkeypatch.setenv("AUTORESEARCH_TASK_ID", "stock.publish")
    monkeypatch.setenv("AUTORESEARCH_ATTEMPT", "1")
    monkeypatch.setattr(context, "read_receipts",
                        lambda capsule: [json.loads(line) for line in
                                         (Path(capsule) / "lineage/source_receipts.jsonl")
                                         .read_text().splitlines()])

    path = context.freeze(handle)

    value = json.loads(Path(path).read_text())
    assert Path(path) == (handle.capsule / "evidence/attempt_records/stock.publish/a1"
                          / "card_semantics_context.json")
    assert (value["engine"], value["run_id"], value["task_id"], value["attempt"]) == (
        "claude", RUN_ID, "stock.publish", 1)
    assert value["profile"]["card_rules_version"] == "skills-gap-v3"
    assert value["task_store"]["tasks"]["stock.card"]["state"] == "SUCCEEDED"
    assert [row["sidecar_id"] for row in value["material_claims"]] == ["side-1"]
    # Claim sources, reviews and their supersession chain; never unrelated receipts.
    assert value["source_receipt_ids"] == ["r0", "r1", "r2"]
    assert context.freeze(handle) == path  # identical refreeze is idempotent


def test_outside_a_session_operation_nothing_is_frozen(tmp_path, monkeypatch):
    from autoresearch.session_agent import card_semantics_context as context

    monkeypatch.delenv("AUTORESEARCH_TASK_ID", raising=False)
    assert context.freeze(_live_handle(tmp_path)) is None


def _replay_context(tmp_path: Path, value: dict, receipts: list[dict]):
    from autoresearch.session_agent.replay_adapters.common import safe_name
    from autoresearch.trace.replay import REPLAY_ENV

    inputs = tmp_path / "replay/inputs"
    (inputs / "artifacts").mkdir(parents=True)
    (inputs / "artifacts" / safe_name("card.semantics_context")).write_text(canonical_json(value))
    source = tmp_path / "replay/source_capsule"
    source.mkdir(parents=True)
    return SimpleNamespace(inputs=inputs, work=tmp_path / "replay/work",
                           source_receipts=tuple(receipts), env={REPLAY_ENV: str(source)},
                           unit={"input_refs": [{"artifact_id": "card.semantics_context"}]})


def test_replay_restores_profile_owner_state_and_claims(tmp_path):
    """Rules go to the artifact handle; owner state/claims to a separate claim handle."""
    from autoresearch.session_agent import card_semantics_context as context

    value = {"schema_version": 1, "engine": "claude", "run_id": RUN_ID,
             "task_id": "stock.publish", "attempt": 1,
             "profile": {"card_rules_version": "skills-gap-v3"},
             "task_store": {"engine": "claude", "run_id": RUN_ID, "tasks": {}},
             "material_claims": [{"sidecar_id": "side-1", "task_id": "stock.card"}],
             "source_receipt_ids": []}
    replay = _replay_context(tmp_path, value, [])
    virtual = SimpleNamespace(run_id=RUN_ID, engine="claude", workspace=tmp_path / "virtual",
                              capsule=tmp_path / "virtual/capsule")

    claim = context.restore(replay, virtual)

    assert json.loads((virtual.capsule / "verification/profile.json").read_text()) == value["profile"]
    # The live owner store would redirect accepted-artifact resolution; keep it apart.
    assert not (virtual.workspace / "session/tasks.json").exists()
    assert (claim.run_id, claim.engine) == (RUN_ID, "claude")
    assert json.loads((claim.workspace / "session/tasks.json").read_text()) == value["task_store"]
    assert json.loads((claim.capsule / "evidence/material_claims/side-1.json").read_text()) == (
        value["material_claims"][0])


def test_replay_without_frozen_context_is_untouched(tmp_path):
    from autoresearch.session_agent import card_semantics_context as context

    replay = SimpleNamespace(unit={"input_refs": []})
    virtual = SimpleNamespace(capsule=tmp_path / "virtual/capsule")
    assert context.restore(replay, virtual) is None
    assert not (tmp_path / "virtual").exists()


@pytest.mark.parametrize("field, wrong", [("run_id", "20990101T000000000000Z"),
                                          ("engine", "codex")])
def test_replay_rejects_context_of_another_run(tmp_path, field, wrong):
    from autoresearch.session_agent import card_semantics_context as context

    value = {"schema_version": 1, "engine": "claude", "run_id": RUN_ID, "task_id": "stock.publish",
             "attempt": 1, "profile": {}, "task_store": {}, "material_claims": [],
             "source_receipt_ids": [], field: wrong}
    virtual = SimpleNamespace(run_id=RUN_ID, engine="claude", workspace=tmp_path / "v",
                              capsule=tmp_path / "v/capsule")
    with pytest.raises(ValueError, match="identity"):
        context.restore(_replay_context(tmp_path, value, []), virtual)


def test_replay_plan_feeds_the_frozen_context_to_its_publish_unit(tmp_path):
    from autoresearch.session_agent.replay_registry import semantics_context_ref

    capsule = tmp_path / "capsule"
    record = capsule / "evidence/attempt_records/stock.publish/a1/card_semantics_context.json"
    record.parent.mkdir(parents=True)
    record.write_text(canonical_json({"source_receipt_ids": ["r0", "r1"]}))

    ref, receipts = semantics_context_ref(capsule, "stock.publish", 1, "stock.publish")

    assert ref == {"artifact_id": "card.semantics_context",
                   "sha256": sha256_bytes(record.read_bytes()),
                   "captured_path": "evidence/attempt_records/stock.publish/a1/card_semantics_context.json"}
    assert receipts == ["r0", "r1"]
    assert semantics_context_ref(capsule, "stock.validate", 1, "stock.validate") == (None, [])
    assert semantics_context_ref(capsule, "stock.publish", 2, "stock.publish") == (None, [])
