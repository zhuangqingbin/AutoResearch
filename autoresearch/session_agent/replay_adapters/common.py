"""Shared virtual-workspace plumbing for domain replay adapters."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

from autoresearch.session_agent import artifacts


def safe_name(value: str) -> str:
    # urllib always leaves ASCII alphanumerics unescaped; only punctuation belongs here.
    return quote(value, safe="_.-")


def input_path(context, artifact_id: str) -> Path:
    return Path(context.inputs) / "artifacts" / safe_name(artifact_id)


def session_request(context) -> dict:
    path = input_path(context, "session.request")
    if not path.is_file():
        raise RuntimeError("frozen session.request is required for domain replay")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("session.request must be an object")
    return value


def virtual_handle(context, request: dict):
    root = Path(context.work) / "virtual"
    staging = root / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    session = root / "session"
    session.mkdir(parents=True, exist_ok=True)
    (session / "request.json").write_text(
        json.dumps(request, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )
    return SimpleNamespace(
        run_id=str(context.replay_run_id),
        engine=str(context.env["AUTORESEARCH_ENGINE"]),
        analysis_date=str(request["analysis_date"]),
        workspace=root,
        staging=staging,
        capsule=root / "capsule",
        contract=SimpleNamespace(
            run_kind=request["kind"],
            contract_hash="0" * 64,
            config_hash="0" * 64,
            user_config={},
        ),
    )


def stage_inputs(context, handle, register) -> None:
    register()
    registry_path = Path(handle.workspace) / "session/artifacts.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))["artifacts"]
    if "research.frame" not in registry and any(
        ref["artifact_id"] == "research.frame" for ref in context.unit["input_refs"]
    ):
        artifacts.register_artifact(handle, "research.frame",
                                    handle.staging / "session_outputs/decision_frame.json", "WRITE")
        registry = json.loads(registry_path.read_text(encoding="utf-8"))["artifacts"]
    for ref in context.unit["input_refs"]:
        artifact_id = ref["artifact_id"]
        if artifact_id not in registry:
            continue
        source = input_path(context, artifact_id)
        if not source.is_file():
            continue
        target = artifacts.declared_path(handle, artifact_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
        artifacts.bind_artifact_hash(handle, artifact_id)


def export_outputs(context, handle) -> None:
    registry_path = Path(handle.workspace) / "session/artifacts.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))["artifacts"]
    for ref in context.unit["expected_outputs"]:
        artifact_id = ref["artifact_id"]
        if artifact_id not in registry:
            raise RuntimeError(f"unregistered replay output: {artifact_id}")
        artifacts.bind_artifact_hash(handle, artifact_id)
        with artifacts.open_artifact(handle, artifact_id) as stream:
            context.output_path(artifact_id).write_bytes(stream.read())


def source_snapshot(context, endpoint: str) -> dict:
    from autoresearch.trace.replay import REPLAY_ENV
    from autoresearch.trace.source_receipts import replay_response

    matches = [row for row in context.source_receipts if row["endpoint"] == endpoint]
    if len(matches) != 1:
        raise RuntimeError(f"exactly one {endpoint} SourceReceipt is required")
    value = replay_response(context.env[REPLAY_ENV], matches[0]["receipt_id"])
    if not isinstance(value, dict):
        raise TypeError(f"{endpoint} snapshot must be a JSON object")
    return value
