from __future__ import annotations

import json

import pytest

from autoresearch.common.atomic import atomic_write_json
from autoresearch.contracts.session_plan import validate_expansion, validate_plan
from autoresearch.contracts.session_task import validate_begin_request
from autoresearch.session_agent import artifacts, domain_ops
from autoresearch.session_agent.operations import build_argv
from autoresearch.session_agent.sector_terrain import replay_terrain
from autoresearch.session_agent.workflows import expansions_after_task
from autoresearch.session_agent.workflows.scan import _paths_for_artifact, l3_expansion
from autoresearch.session_agent.workflows.sector import build_sector_plan, register_sector_artifacts
from tests.sector.test_pack import _mk_scan
from tests.session_agent.conftest import make_plan
from tests.session_agent.test_sector import _request
from tests.session_agent.test_service import _handle


def request():
    return dict(
        _request("LITE", "半导体"),
        schema_version=4,
        analysis_date="2026-07-03",
        card_research_profile="single-stage-v1",
        macro_research_profile="serial21",
        macro_optional_products=[],
        sector_brief_profile="deterministic-v1",
        research_context={"venue": "XSHG", "usage": "sector", "calendar_source_path": None},
    )


def setup_run(tmp_path, *, events=False):
    handle = _handle(tmp_path)
    handle.analysis_date = "2026-07-03"
    req = request()
    atomic_write_json(handle.workspace / "session/request.json", req)
    frame_path = handle.staging / "session_outputs/decision_frame.json"
    atomic_write_json(frame_path, {"knowledge_cutoff": "2026-07-03T18:00:00+08:00"})
    artifacts.register_artifact(handle, "research.frame", frame_path, "READ")
    plan = build_sector_plan(req, handle)
    register_sector_artifacts(req, handle, plan)
    root = tmp_path / "scan"
    day = _mk_scan(root)
    if events:
        atomic_write_json(
            day / "sector_evidence.json",
            {
                "signals": {
                    "半导体": {
                        "required_gaps": [
                            {
                                "id": "event-gap",
                                "detail": "公告内容待核",
                                "source_ref": "calendar.csv",
                            }
                        ]
                    }
                }
            },
        )
    return handle, req, plan, root


@pytest.mark.parametrize("events", [False, True])
def test_standalone_candidate_prepares_expands_and_renders_from_frozen_inputs(
    tmp_path, monkeypatch, events
):
    handle, req, plan, source = setup_run(tmp_path, events=events)
    validate_begin_request(req)
    validate_plan(plan)
    captures = []
    monkeypatch.setattr(
        "autoresearch.trace.source_receipts.record_active_response",
        lambda **kwargs: captures.append(kwargs),
    )
    result = domain_ops.sector_prepare(handle, scan_root=source)
    assert result["pack"]["terrain"]["profile"] == "deterministic-v1"
    for key in plan["tasks"][0]["output_artifact_ids"]:
        artifacts.bind_artifact_hash(handle, key)
    expansion = expansions_after_task(req, handle, plan, plan["tasks"][0])[0]
    validate_expansion(expansion)
    inference = [task for task in expansion["tasks"] if task["kind"] == "INFERENCE"]
    assert len(inference) == int(events)
    if events:
        assert inference[0]["expected_output_contract"] == "sector.events.v1"
        event_request = json.loads(artifacts.read_bytes(handle, "sector.events.request"))
        atomic_write_json(
            artifacts.declared_path(handle, "sector.events"),
            {
                "schema_version": 1,
                "pack_sha256": event_request["pack_sha256"],
                "events": [],
                "unresolved_reason_ids": ["event-gap"],
            },
        )
        artifacts.bind_artifact_hash(handle, "sector.events")
    render = next(task for task in expansion["tasks"] if task["task_id"].endswith(".render"))
    domain_ops.sector_terrain_render(handle, task=render)
    text = artifacts.declared_path(handle, "sector.report").read_text()
    assert "median_pe: 60.0 倍" in text
    if events:
        assert "event-gap 未核" in text
    snapshot = captures[-1]["outcome"]
    replay_text, stable = replay_terrain(snapshot)
    assert replay_text == text
    assert stable == json.loads(
        artifacts.declared_path(handle, "sector.stable.snapshot").read_text()
    )
    artifacts.bind_artifact_hash(handle, "sector.report")
    assert domain_ops.sector_lite_validate(handle)["contract"] == "sector.terrain.v1"


def test_scan_candidate_prepares_all_selected_sectors_and_builds_renderer_tasks(
    tmp_path, monkeypatch
):
    handle, req, _, source = setup_run(tmp_path)
    req.update(kind="scan-market", requested_mode="AUTO", subject=None)
    req["research_context"]["usage"] = "scan"
    atomic_write_json(handle.workspace / "session/request.json", req)
    # This fixture only supplies deterministic staging; no market scan is started.
    import shutil

    for path in (source / "2026-07-03").iterdir():
        shutil.copy(path, handle.staging / path.name)
    monkeypatch.setattr(domain_ops, "_use_frozen_scan_runtime_inputs", lambda handle: None)
    monkeypatch.setattr(domain_ops, "_record_scan_source", lambda **kwargs: None)
    monkeypatch.setattr(
        "autoresearch.sector.reuse.find_stable_snapshot", lambda *args, **kwargs: None
    )
    result = domain_ops.scan_sector_prepare(handle)
    assert len(result["sectors"]) == 3
    assert all(row["profile"] == "deterministic-v1" for row in result["sectors"])
    plan = make_plan(run_kind="scan-market", requested_mode="AUTO")
    expansion = l3_expansion(
        plan,
        {"mode": "FULL"},
        result["sectors"],
        [{"artifact_id": "scan.sector.list", "sha256": "a" * 64}],
    )
    renderers = [
        task for task in expansion["tasks"] if task.get("operation") == "scan.sector.render"
    ]
    assert {task["subject"] for task in renderers} == {"半导体", "白酒", "煤炭"}
    assert not any(task["role"] == "sector.brief" for task in expansion["tasks"])
    for task in renderers:
        for key in [*task["input_artifact_ids"], *task["output_artifact_ids"]]:
            if key == "research.frame":
                continue
            path, access = _paths_for_artifact(handle, task, key)
            artifacts.register_artifact(handle, key, path, access)
        monkeypatch.setattr(
            "autoresearch.trace.source_receipts.record_active_response", lambda **kwargs: None
        )
        domain_ops.sector_terrain_render(handle, task=task)
        brief_id = next(key for key in task["output_artifact_ids"] if key.endswith(".brief"))
        assert artifacts.declared_path(handle, brief_id).read_text().count("## ") == 1


def test_new_render_operations_have_registered_replay_and_cli():
    from autoresearch.contracts.operation_replay import operation_replay_classification

    for key in ("sector.terrain.render", "scan.sector.render"):
        assert build_argv(key, {})[-1] == "sector-terrain-render"
        assert operation_replay_classification(key) == "SOURCE_REPLAY"


def test_renderer_freezes_verified_stable_facts_for_source_replay(tmp_path, monkeypatch):
    from tests.sector.test_deterministic_terrain import stable_fact

    handle, req, plan, source = setup_run(tmp_path)
    atomic_write_json(
        source / "2026-07-03/sector_evidence.json", {"stable_facts": {"半导体": [stable_fact()]}}
    )
    captures = []
    monkeypatch.setattr(
        "autoresearch.trace.source_receipts.record_active_response",
        lambda **kwargs: captures.append(kwargs),
    )
    monkeypatch.setattr(
        "autoresearch.session_agent.sector_terrain.source_binding",
        lambda handle: lambda fact, request: {"verdict": "PASS"},
    )
    domain_ops.sector_prepare(handle, scan_root=source)
    for key in plan["tasks"][0]["output_artifact_ids"]:
        artifacts.bind_artifact_hash(handle, key)
    expansion = expansions_after_task(req, handle, plan, plan["tasks"][0])[0]
    render = next(task for task in expansion["tasks"] if task["task_id"].endswith(".render"))
    domain_ops.sector_terrain_render(handle, task=render)
    snapshot = captures[-1]["outcome"]
    assert snapshot["stable_facts"] == [stable_fact()]
    replay_text, stable = replay_terrain(snapshot)
    assert replay_text == artifacts.declared_path(handle, "sector.report").read_text()
    assert stable["stable_facts"] == [stable_fact()]


@pytest.mark.parametrize("provider,expected", [("deterministic", "UNKNOWN"), ("host_tool", "UNKNOWN")])
def test_sector_host_binding_rejects_unregistered_review_provider_labels(
    tmp_path, monkeypatch, provider, expected
):
    from autoresearch.session_agent.sector_terrain import source_binding
    from tests.news.test_frozen_claim_sources import CUTOFF, TEXT, frame, setup_claim

    handle, source, _, _ = setup_claim(tmp_path, provider=provider)
    handle.workspace = tmp_path
    handle.staging = tmp_path / "staging"
    handle.staging.mkdir()
    path = handle.staging / "frame.json"
    atomic_write_json(path, frame())
    artifacts.register_artifact(handle, "research.frame", path, "READ")
    owner = tmp_path / "session/tasks.json"
    owner.parent.mkdir(exist_ok=True)
    atomic_write_json(
        owner, {"run_id": handle.run_id, "engine": handle.engine, "artifact_layout_version": 1}
    )
    monkeypatch.setattr(
        "autoresearch.session_agent.store.read_entries",
        lambda path: {
            "intel.600000": {"state": "SUCCEEDED", "attempt": 1, "spec": {"subject": "半导体"}}
        },
    )
    event = {
        "claim": TEXT,
        "published_at": CUTOFF,
        "available_at": CUTOFF,
        "source_observation_id": source["receipt_id"],
        "source_text_sha256": source["payload_hash"],
        "source_url": "https://issuer/a",
        "quote": TEXT,
    }
    binding = source_binding(handle)
    assert binding(event, {"knowledge_cutoff": CUTOFF, "industry": "半导体"})["verdict"] == expected
    assert binding(event, {"knowledge_cutoff": CUTOFF, "industry": "煤炭"})["verdict"] == "UNKNOWN"
