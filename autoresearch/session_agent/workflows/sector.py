"""Session plans and publication for standalone sector research."""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
from collections.abc import Iterator
from pathlib import Path

from autoresearch.common import workspace as ws
from autoresearch.common.atomic import atomic_write_bytes, canonical_json, sha256_bytes
from autoresearch.contracts.session_plan import plan_hash
from autoresearch.sector.pack import _safe
from autoresearch.session_agent import artifacts
from autoresearch.session_agent.roles import roles_hash


def sector_key(industry: str) -> str:
    return "i" + hashlib.sha256(industry.encode("utf-8")).hexdigest()[:12]


def _task(
    task_id: str,
    kind: str,
    industry: str,
    *,
    dependencies: list[str],
    inputs: list[str],
    outputs: list[str],
    contract: str,
    role: str | None = None,
    operation: str | None = None,
) -> dict:
    return {
        "task_id": task_id,
        "kind": kind,
        "role": role,
        "operation": operation,
        "dependencies": dependencies,
        "input_artifact_ids": inputs,
        "output_artifact_ids": outputs,
        "expected_output_contract": contract,
        "owner": "SESSION",
        "subject": industry,
        "independent_context": False,
        "parent_task": None,
    }


def _tasks(industry: str, mode: str) -> list[dict]:
    prefix = f"sector.{sector_key(industry)}"
    prepare = _task(
        f"{prefix}.prepare",
        "DETERMINISTIC",
        industry,
        dependencies=[],
        inputs=[],
        outputs=["sector.input.manifest", "sector.pack", "sector.reuse"],
        contract="sector.prepare.v1",
        operation="sector.prepare",
    )
    if mode == "LITE":
        inference = _task(
            f"{prefix}.brief",
            "INFERENCE",
            industry,
            dependencies=[prepare["task_id"]],
            inputs=["sector.pack", "sector.reuse"],
            outputs=["sector.report"],
            contract="sector.terrain.v1",
            role="sector.brief",
        )
        prior = inference["task_id"]
        tasks = [prepare, inference]
    else:
        intel = _task(
            f"{prefix}.intel",
            "INFERENCE",
            industry,
            dependencies=[prepare["task_id"]],
            inputs=["sector.pack"],
            outputs=["sector.intel"],
            contract="sector.intel.v1",
            role="sector.intel",
        )
        research = _task(
            f"{prefix}.research",
            "INFERENCE",
            industry,
            dependencies=[intel["task_id"]],
            inputs=["sector.pack", "sector.intel"],
            outputs=["sector.report"],
            contract="sector.full.v1",
            role="sector.research",
        )
        prior = research["task_id"]
        tasks = [prepare, intel, research]
    tasks.extend(
        [
            _task(
                f"{prefix}.validate",
                "DETERMINISTIC",
                industry,
                dependencies=[prior],
                inputs=["sector.pack", "sector.report", "sector.reuse"],
                outputs=["sector.validation"],
                contract="sector.validation.v1",
                operation="sector.validate",
            ),
            _task(
                f"{prefix}.publish",
                "DETERMINISTIC",
                industry,
                dependencies=[f"{prefix}.validate"],
                inputs=["sector.report", "sector.validation"],
                outputs=["sector.publication.bundle"],
                contract="sector.publication.v1",
                operation="sector.publish",
            ),
        ]
    )
    return tasks


def build_sector_plan(request: dict, handle) -> dict:
    if request["kind"] != "sector-research":
        raise ValueError("sector plan requires sector-research request")
    config_hash = getattr(handle.contract, "config_hash", None) or sha256_bytes(
        canonical_json(getattr(handle.contract, "user_config", {})).encode("utf-8")
    )
    value = {
        "schema_version": 1,
        "engine": handle.engine,
        "run_id": handle.run_id,
        "run_kind": request["kind"],
        "requested_mode": request["requested_mode"],
        "analysis_date": request["analysis_date"],
        "orchestration_version": "session_v1",
        "input_contract_hash": handle.contract.contract_hash,
        "config_hash": config_hash,
        "host_profile_hash": sha256_bytes(
            canonical_json(request["host_profile"]).encode("utf-8")
        ),
        "roles_hash": roles_hash(),
        "tasks": _tasks(request["subject"], request["requested_mode"]),
        "task_templates": [],
        "plan_hash": "0" * 64,
    }
    value["plan_hash"] = plan_hash(value)
    return value


def register_sector_artifacts(request: dict, handle, plan: dict) -> None:
    del plan
    output = Path(handle.staging) / "session_outputs"
    registrations = {
        "sector.input.manifest": output / "sector.inputs.json",
        "sector.pack": output / "sector.pack.json",
        "sector.reuse": output / "sector.reuse.json",
        "sector.report": output / "sector.md",
        "sector.validation": output / "sector.validation.json",
        "sector.publication.bundle": output / "sector.publication.json",
    }
    if request["requested_mode"] == "FULL":
        registrations["sector.intel"] = output / "sector.intel.md"
    for artifact_id, path in registrations.items():
        artifacts.register_artifact(handle, artifact_id, path, "WRITE")


def validate_sector_operation_params(request: dict, task: dict, params: dict) -> None:
    del request, task
    if params != {}:
        raise ValueError("sector deterministic operations accept no parameters")


@contextlib.contextmanager
def _locked(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(f"{path.suffix}.lock").open("a+", encoding="utf-8") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def publish_sector(handle, *, reports_root: Path | str | None = None) -> Path:
    output = Path(handle.staging) / "session_outputs"
    bundle = json.loads((output / "sector.publication.json").read_text(encoding="utf-8"))
    source = output / "sector.md"
    if sha256_bytes(source.read_bytes()) != bundle["report_sha256"]:
        raise RuntimeError("sector report changed after publication preparation")
    base = Path(reports_root) if reports_root is not None else ws.reports_root() / "sector"
    target = base / bundle["analysis_date"] / f"{_safe(bundle['industry'])}.md"
    with _locked(target):
        if target.is_file() and target.read_bytes() != source.read_bytes():
            raise RuntimeError("sector report publication conflict")
        if not target.is_file():
            atomic_write_bytes(target, source.read_bytes())
    return target


__all__ = [
    "build_sector_plan",
    "publish_sector",
    "register_sector_artifacts",
    "sector_key",
    "validate_sector_operation_params",
]
