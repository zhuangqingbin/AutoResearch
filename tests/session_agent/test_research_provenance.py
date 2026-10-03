import json

import pytest


def test_portable_deep_proof_revalidates_actual_transcript_and_tamper(tmp_path, monkeypatch):
    from autoresearch.session_agent import artifacts, dispatch, host_evidence, service
    from autoresearch.session_agent.research_provenance import freeze_read_proof
    from autoresearch.trace.read_observation import verify_read_bundle
    from tests.forensics.test_host_evidence import _rollout, _running_case

    monkeypatch.setattr(
        dispatch,
        "_render_domain_prompt",
        lambda *args, **kwargs: "Fixture: read the declared deep input.",
    )
    handle, _, claimed = _running_case(tmp_path, input_id="stock.deep", role="scan.l4.card")
    path = artifacts.artifact_path(handle, "stock.deep")
    source = _rollout(tmp_path)
    rows = [json.loads(line) for line in source.read_text().splitlines()]
    ordinal = max(row["ordinal"] for row in rows) + 1
    rows.extend(
        [
            {
                "timestamp": "2026-09-13T01:02:03Z",
                "ordinal": ordinal,
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "name": "read_file",
                    "call_id": "deep-full",
                    "arguments": json.dumps({"path": str(path)}),
                },
            },
            {
                "timestamp": "2026-09-13T01:02:04Z",
                "ordinal": ordinal + 1,
                "type": "response_item",
                "payload": {
                    "type": "function_call_output",
                    "call_id": "deep-full",
                    "output": path.read_text(),
                },
            },
        ]
    )
    source.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    host_evidence.bind_task_transcript(
        handle.run_id,
        "inference.one",
        1,
        source,
        context_ref="synthetic-agent",
        parent_context_ref="session-main",
        session_ref="session-main",
        start_ordinal=0,
        end_ordinal=ordinal + 1,
        context_source="SUBAGENT",
        handle_loader=lambda _: handle,
    )
    target = tmp_path / "exported-proof"
    proof = freeze_read_proof(
        handle, service._task(handle, "inference.one"), 1, "stock.deep", target
    )
    value = json.loads((target / "proof.json").read_text())
    result = verify_read_bundle(value, read_bytes=lambda rel: (target / rel).read_bytes())
    assert result["call_id"] == "deep-full" and proof["sha256"]
    # The lower research reader consumes the complete proof without importing session_agent.
    import shutil

    from autoresearch.scan.populations import FrozenSources, research_facts
    from autoresearch.scan.research_provenance import freeze_research_provenance
    from autoresearch.trace.capsule import write_manifest

    published = tmp_path / "published"
    stage = published / "trace/staging"
    stage.mkdir(parents=True)
    shutil.copytree(target, stage / "research_reads/600519")
    (published / "manifest.json").write_text(
        json.dumps({"run_id": handle.run_id, "analysis_date": handle.analysis_date})
    )
    frozen_ref = {"path": "research_reads/600519/proof.json", "sha256": proof["sha256"]}
    freeze_research_provenance(
        stage / "_research_provenance.json",
        run_id=handle.run_id,
        analysis_date=handle.analysis_date,
        contract_hash=handle.contract.contract_hash,
        profile="two-stage-v1",
        base_inputs=[],
        candidates={"600519": {"deep_declared": True, "deep_read_proof": frozen_ref}},
    )
    write_manifest(published)
    rows, _, missing = research_facts(FrozenSources(published, verify_hashes=True))
    assert rows["600519"]["deep_read_verified"] is True and not missing
    (target / "artifact.bin").write_text("changed")
    with pytest.raises(ValueError, match="hash"):
        verify_read_bundle(value, read_bytes=lambda rel: (target / rel).read_bytes())


def test_collector_only_uses_accepted_inputs_and_retains_failed_card(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from autoresearch.session_agent import artifacts
    from autoresearch.session_agent.research_provenance import collect_and_freeze
    from tests.session_agent.test_service import _handle

    handle = _handle(tmp_path)
    # Minimal actual accepted task source; assertions exercise the artifact owner.
    role = "scan.l4.card"
    code = "600000"
    task = {
        "task_id": "scan.l4.600000.a1.card",
        "role": role,
        "subject": code,
        "expected_output_contract": "scan.l4.card.v1",
        "input_artifact_ids": ["scan.l4.600000.a1.slim"],
        "output_artifact_ids": [],
        "parent_task": {"attempt": 1},
    }
    source = handle.staging / "slim.md"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("actual slim")
    artifacts.register_artifact(handle, task["input_artifact_ids"][0], source, "READ")
    session = handle.workspace / "session"
    session.mkdir(exist_ok=True)
    (session / "tasks.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "engine": handle.engine,
                "run_id": handle.run_id,
                "tasks": {task["task_id"]: {"spec": task, "state": "FAILED", "attempt": 1}},
            }
        )
    )
    (session / "request.json").write_text(json.dumps({"card_research_profile": "single-stage-v1"}))
    # The run contract is already frozen by the orchestrator.
    handle.contract = SimpleNamespace(contract_hash="a" * 64)
    target = collect_and_freeze(handle)
    value = json.loads(target.read_text())
    assert value["profile"] == "single-stage-v1"
    assert value["candidates"][code]["force_full"] is None
    assert value["candidates"][code]["deep_read_verified"] is None
    # A registered file without an original dispatch manifest cannot prove the task's frozen inputs.
    assert value["base_inputs"] == []


def test_collector_retry_preserves_bytes_and_rejects_changed_accepted_input(tmp_path):
    from autoresearch.session_agent import artifacts
    from autoresearch.session_agent.research_provenance import collect_and_freeze
    from tests.session_agent.test_service import _handle

    handle = _handle(tmp_path)
    task = {
        "task_id": "scan.l4.600000.a1.card",
        "role": "scan.l4.card",
        "subject": "600000",
        "expected_output_contract": "scan.l4.card.v1",
        "input_artifact_ids": ["scan.l4.600000.a1.slim"],
        "output_artifact_ids": [],
        "parent_task": {"attempt": 1},
    }
    source = handle.staging / "slim.md"
    source.write_text("original")
    artifacts.register_artifact(handle, task["input_artifact_ids"][0], source, "READ")
    session = handle.workspace / "session"
    (session / "tasks.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "engine": handle.engine,
                "run_id": handle.run_id,
                "tasks": {task["task_id"]: {"spec": task, "state": "FAILED", "attempt": 1}},
            }
        )
    )
    (session / "request.json").write_text(json.dumps({"card_research_profile": "single-stage-v1"}))
    path = collect_and_freeze(handle)
    before = path.read_bytes()
    assert collect_and_freeze(handle) == path and path.read_bytes() == before
    source.write_text("changed later")
    with pytest.raises((ValueError, RuntimeError), match="changed|content|identity"):
        collect_and_freeze(handle)
