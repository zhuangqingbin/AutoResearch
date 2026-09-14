from __future__ import annotations

from autoresearch.session_agent import domain_ops
from autoresearch.session_agent.workflows.scan import build_scan_plan, review3_expansion

from .test_scan_prelude import context, request


def test_review3_expansion_ends_in_original_assemble_gate_usage_observe_chain(tmp_path):
    plan = build_scan_plan(request(), context(tmp_path))
    expansion = review3_expansion(
        plan,
        {
            "decisions": [
                {
                    "code": "600519",
                    "trigger": None,
                    "rating": "Hold",
                    "review2_rating": None,
                    "same_tier": None,
                }
            ]
        },
        [{"artifact_id": "scan.review.decision", "sha256": "a" * 64}],
    )
    tasks = {task["task_id"]: task for task in expansion["tasks"]}
    assert tasks["scan.l4.complete"]["dependencies"] == ["l4.600519.a1.finalize"]
    assert tasks["scan.assemble"]["dependencies"] == ["scan.l4.complete"]
    assert tasks["scan.gate4"]["dependencies"] == ["scan.assemble"]
    assert tasks["scan.usage"]["dependencies"] == ["scan.gate4"]
    assert tasks["scan.observe"]["dependencies"] == ["scan.usage"]
    assert tasks["scan.observe"]["output_artifact_ids"] == [
        "scan.publication.bundle",
        "scan.pool.candidate",
        "scan.report.brief",
        "scan.report.summary",
        "scan.report.appendix",
        "scan.report.manifest",
        "scan.progress.final",
    ]


def test_empty_sentinel_has_the_same_final_gate_and_observation_chain(tmp_path):
    plan = build_scan_plan(request(), context(tmp_path))
    expansion = review3_expansion(
        plan,
        {"decisions": []},
        [{"artifact_id": "scan.review.decision", "sha256": "a" * 64}],
    )
    tasks = {task["task_id"]: task for task in expansion["tasks"]}
    assert tasks["scan.assemble"]["dependencies"] == ["scan.review3.skip"]
    assert "scan.observe" in tasks


def test_l4_complete_rejects_a_taskbook_with_blocked_tickets(tmp_path):
    import json
    from types import SimpleNamespace

    staging = tmp_path / "staging/2026-09-13"
    staging.mkdir(parents=True)
    (staging / "_l4_tasks.json").write_text(
        json.dumps({"schema_version": 1, "tasks": {"600519": {"status": "BLOCKED"}}})
    )
    handle = SimpleNamespace(staging=staging)
    import pytest

    with pytest.raises(RuntimeError, match="not all SUCCEEDED"):
        domain_ops.scan_l4_complete(handle)


def test_assemble_uses_the_original_publisher_in_run_staging(tmp_path, monkeypatch):
    from types import SimpleNamespace

    workspace = tmp_path / "run"
    staging = workspace / "staging/2026-09-13"
    staging.mkdir(parents=True)
    handle = SimpleNamespace(
        run_id="20260913T010203000000Z",
        engine="codex",
        analysis_date="2026-09-13",
        workspace=workspace,
        staging=staging,
    )

    def publish(date, *, scan_dir, out_root):
        target = out_root / "20260913-0913_1200"
        target.mkdir(parents=True)
        for name in ("brief.md", "summary.md", "appendix.md", "manifest.json"):
            (target / name).write_text(name)
        return target / "summary.md"

    monkeypatch.setattr("autoresearch.scan.publisher.run", publish)
    value = domain_ops.scan_assemble(handle)
    assert value["folder"] == "20260913-0913_1200"
    assert (staging / "session_outputs/report.plan.json").is_file()


def test_gate4_failure_remains_a_hard_failure(tmp_path, monkeypatch):
    from types import SimpleNamespace

    staging = tmp_path / "staging/2026-09-13"
    staging.mkdir(parents=True)
    handle = SimpleNamespace(staging=staging)
    monkeypatch.setattr(
        "autoresearch.scan.gates.gate4",
        lambda root: {"ok": False, "gate": "gate4", "reason": "hard failure"},
    )
    monkeypatch.setattr("autoresearch.scan.gates.record_gate_stage_result", lambda *args: None)
    import pytest

    with pytest.raises(RuntimeError, match="hard failure"):
        domain_ops.scan_gate4(handle)
    assert (staging / "session_outputs/gate4.json").is_file()
