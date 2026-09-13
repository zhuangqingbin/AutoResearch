"""Session plans for standalone stock research."""
from __future__ import annotations

import contextlib
import fcntl
import json
from collections.abc import Iterator
from pathlib import Path

from autoresearch.common import workspace as ws
from autoresearch.common.atomic import (
    atomic_write_bytes,
    atomic_write_json,
    canonical_json,
    sha256_bytes,
)
from autoresearch.contracts.session_plan import plan_hash
from autoresearch.dataflows.symbol_utils import normalize_symbol
from autoresearch.session_agent import artifacts
from autoresearch.session_agent.roles import roles_hash


def _task(
    task_id: str,
    kind: str,
    *,
    dependencies: list[str],
    inputs: list[str],
    outputs: list[str],
    contract: str,
    subject: str,
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
        "subject": subject,
        "independent_context": False,
        "parent_task": None,
    }


def _lite_tasks(ticker: str) -> list[dict]:
    return [
        _task(
            "stock.harvest", "DETERMINISTIC", dependencies=[], inputs=[],
            outputs=["stock.slim", "stock.deep"], contract="stock.harvest.slim.v1",
            subject=ticker, operation="stock.harvest",
        ),
        _task(
            "stock.card", "INFERENCE", dependencies=["stock.harvest"],
            inputs=["stock.slim"], outputs=["stock.card.output"],
            contract="stock.lite.v1", subject=ticker, role="stock.card",
        ),
        _task(
            "stock.validate", "DETERMINISTIC", dependencies=["stock.card"],
            inputs=["stock.card.output", "stock.slim"],
            outputs=["stock.card.validation"], contract="stock.validation.v1",
            subject=ticker, operation="stock.validate",
        ),
        _task(
            "stock.publish", "DETERMINISTIC", dependencies=["stock.validate"],
            inputs=["stock.card.output", "stock.card.validation"],
            outputs=["stock.publication.bundle"], contract="stock.publication.v1",
            subject=ticker, operation="stock.publish",
        ),
    ]


def build_stock_plan(request: dict, handle) -> dict:
    if request["kind"] != "stock-research":
        raise ValueError("stock plan requires stock-research request")
    if request["requested_mode"] != "LITE":
        raise ValueError("FULL stock plan is not implemented yet")
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
        "tasks": _lite_tasks(request["subject"]),
        "task_templates": [],
        "plan_hash": "0" * 64,
    }
    value["plan_hash"] = plan_hash(value)
    return value


def register_stock_artifacts(request: dict, handle, plan: dict) -> None:
    del plan
    ticker = normalize_symbol(request["subject"])
    analysis_date = request["analysis_date"]
    staging = Path(handle.staging)
    output = staging / "session_outputs"
    registrations = {
        "stock.slim": staging / f"{ticker}_{analysis_date}_slim.md",
        "stock.deep": staging / f"{ticker}_{analysis_date}_slim_deep.md",
        "stock.card.output": output / "card.md",
        "stock.card.validation": output / "card.validation.json",
        "stock.publication.bundle": output / "publication.json",
    }
    for artifact_id, path in registrations.items():
        artifacts.register_artifact(handle, artifact_id, path, "WRITE")


def validate_stock_operation_params(request: dict, task: dict, params: dict) -> None:
    if task["operation"] == "stock.harvest":
        expected = {
            "ticker": request["subject"],
            "analysis_date": request["analysis_date"],
            "asset_type": request["asset_type"],
            "peers": request["peers"],
            "slim": request["requested_mode"] == "LITE",
        }
        if params != expected:
            raise ValueError("stock.harvest params differ from frozen request")
    elif params != {}:
        raise ValueError(f"{task['operation']} accepts no parameters")


@contextlib.contextmanager
def _publish_lock(root: Path) -> Iterator[None]:
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".session-publish.lock").open("a+", encoding="utf-8") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def publish_stock(handle, *, reports_root: Path | None = None) -> Path:
    """Publish a validated LITE card under the existing analyze report layout."""
    with artifacts.open_artifact(handle, "stock.publication.bundle") as stream:
        bundle = json.loads(stream.read().decode("utf-8"))
    with artifacts.open_artifact(handle, "stock.card.output") as stream:
        card = stream.read()
    if sha256_bytes(card) != bundle["card_sha256"]:
        raise RuntimeError("stock card changed after publication bundle validation")
    root = Path(reports_root) if reports_root is not None else ws.run_reports_root(
        "stock-research"
    )
    run_hhmm = handle.run_id[9:13]
    report_dir = root / f"{bundle['analysis_date'].replace('-', '')}_{run_hhmm}"
    report = report_dir / bundle["output_name"]
    manifest = {
        "schema_version": 2,
        "engine": handle.engine,
        "run_id": handle.run_id,
        "ticker": bundle["ticker"],
        "name": bundle["name"],
        "market": "A股" if str(bundle["ticker"]).split(".")[0].isdigit() else "其他",
        "analysis_date": bundle["analysis_date"],
        "tier": "lite",
        "rating": bundle["rating"],
        "proposal": bundle["proposal"],
        "card_sha256": bundle["card_sha256"],
    }
    with _publish_lock(root):
        if report.is_file() and report.read_bytes() != card:
            raise RuntimeError("stock report already exists with different content")
        manifest_path = report_dir / "manifest.json"
        if manifest_path.is_file():
            current = json.loads(manifest_path.read_text(encoding="utf-8"))
            if canonical_json(current) != canonical_json(manifest):
                raise RuntimeError("stock report manifest conflicts with this run")
        atomic_write_bytes(report, card)
        atomic_write_json(manifest_path, manifest)
    return report_dir


__all__ = [
    "build_stock_plan", "publish_stock", "register_stock_artifacts",
    "validate_stock_operation_params",
]
