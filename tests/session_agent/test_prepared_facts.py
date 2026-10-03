"""Prepared facts execution retains frozen bytes and real captured subprocess IO."""
import json
import subprocess
import sys

from autoresearch.common.atomic import sha256_bytes
from autoresearch.session_agent import artifacts, card_facts, service, store

from ._runner_support import begin_synthetic_run, det


def test_prepared_facts_cli_reads_only_frozen_bytes_and_leaves_output_commit_to_owner(tmp_path, monkeypatch):
    task = det("facts", subject="600519", operation="research.card.facts",
               inputs=["raw.slim", "raw.deep", "research.frame"], outputs=["research.facts"])
    run = begin_synthetic_run(tmp_path, monkeypatch, [task])
    service.claim(run.run_id, "facts", 1, handle_loader=lambda _: run.handle)
    expected = card_facts.project_facts("600519", {key: artifacts.read_bytes(run.handle, key)
                                                for key in task["input_artifact_ids"]})
    request_path = card_facts.prepare_projection(run.handle, task, 1)
    request_bytes = request_path.read_bytes()
    result = subprocess.run([sys.executable, "-m", "autoresearch.session_agent.card_facts",
                             "--prepared-request", str(request_path), "--sha256", sha256_bytes(request_bytes)],
                            capture_output=True, check=True)
    assert json.loads(result.stdout) == expected
    assert store.read_entry(service._store_path(run.handle), "facts")["state"] == "RUNNING"
    assert not artifacts.declared_path(run.handle, "research.facts").exists()
    assert request_path.read_bytes() == request_bytes
    request_path.write_text("tampered")
    bad = subprocess.run([sys.executable, "-m", "autoresearch.session_agent.card_facts",
                          "--prepared-request", str(request_path), "--sha256", sha256_bytes(request_bytes)],
                         capture_output=True)
    assert bad.returncode != 0


def test_owner_commits_prepared_result_and_revalidates_claim(tmp_path, monkeypatch):
    from autoresearch.session_agent import executor

    task = det("facts", subject="600519", operation="research.card.facts",
               inputs=["raw.slim", "raw.deep", "research.frame"], outputs=["research.facts"])
    run = begin_synthetic_run(tmp_path, monkeypatch, [task])
    service.claim(run.run_id, "facts", 1, handle_loader=lambda _: run.handle)
    expected = card_facts.project_facts("600519", {key: artifacts.read_bytes(run.handle, key)
                                                for key in task["input_artifact_ids"]})
    seen = []
    def compute(handle, current, attempt, params, *, owner_callback):
        assert not artifacts.declared_path(handle, "research.facts").exists()
        owner_callback(lambda: True)
        return {"status": "SUCCEEDED", "exit_code": 0, "prepared_output": expected}
    monkeypatch.setattr(executor, "execute_operation", compute)
    result = service.execute(run.run_id, "facts", 1, {}, handle_loader=lambda _: run.handle,
                             owner_callback=lambda alive: seen.append(alive()))
    assert result["state"] == "DONE"
    assert seen == [True]
    assert json.loads(artifacts.read_bytes(run.handle, "research.facts")) == expected
    assert "prepared_output" not in result["result"]["execution"]
