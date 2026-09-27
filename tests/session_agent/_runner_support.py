"""Synthetic session_v1 runs for runner / executor tests (no network, no prelude).

The engine is pinned inside the fixture (``workspace.ENGINE``) so these tests pass
under both ``AUTORESEARCH_ENGINE=claude`` and ``codex`` hosts.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

from autoresearch.common import workspace as ws
from autoresearch.common.atomic import canonical_json, sha256_bytes
from autoresearch.contracts.session_plan import plan_hash
from autoresearch.session_agent import artifacts, service

RUN_ID = "20260913T010203000000Z"
ENGINE = "codex"
CARD_TEXT = "**Rating**: Hold\nFINAL TRANSACTION PROPOSAL: **HOLD**\n"


def profile(**changes) -> dict:
    value = {
        "schema_version": 1,
        "engine": ENGINE,
        "session_ref": "session-main",
        "deterministic_exec": True,
        "capture_binding": True,
        "inference_handoff": True,
        "safe_resume": True,
        "independent_context": False,
        "native_dispatch": False,
        "web_search": False,
        "web_fetch": False,
        "observed_model": "subscription-session",
        "observed_effort": None,
        "evidence_refs": ["test-host"],
    }
    value.update(changes)
    return value


def det(task_id: str, *, deps=(), inputs=(), outputs=None, owner="SESSION",
        subject=None, parent=None, operation="test.noop") -> dict:
    return {
        "task_id": task_id,
        "kind": "DETERMINISTIC",
        "role": None,
        "operation": operation,
        "dependencies": list(deps),
        "input_artifact_ids": list(inputs),
        "output_artifact_ids": list(outputs if outputs is not None else [f"{task_id}.out"]),
        "expected_output_contract": "test.output.v1",
        "owner": owner,
        "subject": subject,
        "independent_context": False,
        "parent_task": parent,
    }


def inf(task_id: str, *, deps=(), inputs=("synthetic.brief",), outputs=None, role="stock.card",
        contract="stock.lite.v1", subject=None, independent=False, parent=None) -> dict:
    return {
        "task_id": task_id,
        "kind": "INFERENCE",
        "role": role,
        "operation": None,
        "dependencies": list(deps),
        "input_artifact_ids": list(inputs),
        "output_artifact_ids": list(outputs if outputs is not None else [f"{task_id}.out"]),
        "expected_output_contract": contract,
        "owner": "SESSION",
        "subject": subject,
        "independent_context": independent,
        "parent_task": parent,
    }


@dataclass
class SyntheticRun:
    handle: SimpleNamespace
    tasks: list[dict]
    op_calls: list[str] = field(default_factory=list)
    op_failures: dict[str, int] = field(default_factory=dict)
    finish_calls: list[str] = field(default_factory=list)

    @property
    def run_id(self) -> str:
        return self.handle.run_id

    def output_path(self, artifact_id: str) -> Path:
        return Path(self.handle.staging) / "out" / f"{artifact_id}.md"

    def operation_runner(self, handle, stage, argv, invocation_id, attempt, subject, *, task_id):
        self.op_calls.append(f"{task_id}@{attempt}")
        if self.op_failures.get(task_id, 0) > 0:
            self.op_failures[task_id] -= 1
            return SimpleNamespace(exit_code=3, invocation={"status": "FAILED"})
        task = next(item for item in self.tasks if item["task_id"] == task_id)
        for artifact_id in task["output_artifact_ids"]:
            path = self.output_path(artifact_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"deterministic {task_id}\n", encoding="utf-8")
        return SimpleNamespace(exit_code=0, invocation={"status": "COMPLETED"})

    def hooks(self, **changes):
        from autoresearch.session_agent.runner import ServiceHooks

        values = {
            "handle_loader": lambda run_id: self.handle,
            "operation_runner": self.operation_runner,
            "event_recorder": lambda *args, **kwargs: None,
            "validator": lambda submission, task: None,
            "publisher": lambda current: self.finish_calls.append("publish") or None,
            "finalizer": lambda current, report: self.finish_calls.append("finalize") or {"ok": True},
        }
        values.update(changes)
        return ServiceHooks(**values)


def begin_synthetic_run(
    tmp_path,
    monkeypatch,
    tasks: list[dict],
    *,
    host: dict | None = None,
    run_kind: str = "stock-research",
    user_config: dict | None = None,
) -> SyntheticRun:
    monkeypatch.setattr(ws, "ENGINE", ENGINE)
    workspace = tmp_path / f"context_{ENGINE}" / "runs" / RUN_ID
    staging = workspace / "staging" / "2026-09-13"
    staging.mkdir(parents=True)
    capsule = workspace / "capsule"
    (capsule / "events").mkdir(parents=True)
    handle = SimpleNamespace(
        workspace=workspace,
        staging=staging,
        capsule=capsule,
        engine=ENGINE,
        run_id=RUN_ID,
        analysis_date="2026-09-13",
        contract=SimpleNamespace(
            contract_hash="a" * 64,
            config_hash="b" * 64,
            run_kind=run_kind,
            user_config=user_config or {},
        ),
    )
    run = SyntheticRun(handle=handle, tasks=tasks)
    host_profile = host or profile()
    if run_kind == "scan-market":
        request = {
            "schema_version": 1, "kind": "scan-market", "requested_mode": "AUTO",
            "analysis_date": "2026-09-13", "subject": None, "peers": [], "asset_type": None,
            "name": None, "force_full": False, "host_profile": host_profile,
            "predecessor_run_id": None,
        }
    else:
        request = {
            "schema_version": 1, "kind": "stock-research", "requested_mode": "LITE",
            "analysis_date": "2026-09-13", "subject": "600519.SS", "peers": [],
            "asset_type": "stock", "name": None, "force_full": False,
            "host_profile": host_profile, "predecessor_run_id": None,
        }

    def planner(req, current):
        value = {
            "schema_version": 1,
            "engine": current.engine,
            "run_id": current.run_id,
            "run_kind": req["kind"],
            "requested_mode": req["requested_mode"],
            "analysis_date": req["analysis_date"],
            "orchestration_version": "session_v1",
            "input_contract_hash": current.contract.contract_hash,
            "config_hash": sha256_bytes(canonical_json({}).encode()),
            "host_profile_hash": sha256_bytes(canonical_json(req["host_profile"]).encode()),
            "roles_hash": "d" * 64,
            "tasks": tasks,
            "task_templates": [],
            "plan_hash": "0" * 64,
        }
        value["plan_hash"] = plan_hash(value)
        return value

    def registrar(req, current, frozen):
        produced = set()
        for task in frozen["tasks"]:
            for artifact_id in task["output_artifact_ids"]:
                produced.add(artifact_id)
                artifacts.register_artifact(current, artifact_id, run.output_path(artifact_id), "WRITE")
        for task in frozen["tasks"]:
            for artifact_id in task["input_artifact_ids"]:
                if artifact_id in produced:
                    continue
                source = Path(current.staging) / "in" / f"{artifact_id}.md"
                if not source.is_file():
                    source.parent.mkdir(parents=True, exist_ok=True)
                    source.write_text(f"frozen input {artifact_id}\n", encoding="utf-8")
                    artifacts.register_artifact(current, artifact_id, source, "READ")

    service.begin(
        request,
        begin_capsule=lambda req: handle,
        planner=planner,
        artifact_registrar=registrar,
    )
    return run


__all__ = [
    "CARD_TEXT", "ENGINE", "RUN_ID", "SyntheticRun", "begin_synthetic_run", "det", "inf",
    "profile",
]
