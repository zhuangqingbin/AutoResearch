"""Session plans and publication for standalone macro research."""
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
from autoresearch.macro import assemble
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
        "subject": None,
        "independent_context": False,
        "parent_task": None,
    }


def _artifact_id(relative: str) -> str:
    stem = relative[:-3] if relative.endswith(".md") else relative
    return "macro.full." + stem.replace("/", ".")


def macro_product_artifacts() -> dict[str, str]:
    relatives = {assemble.DECISION_REL}
    for _, items in assemble.SPINE + assemble.MESO + assemble.APPENDIX:
        relatives.update(relative for _, relative, _ in items)
    return {relative: _artifact_id(relative) for relative in sorted(relatives)}


def required_macro_products() -> set[str]:
    required = {assemble.DECISION_REL}
    for _, items in assemble.SPINE + assemble.MESO + assemble.APPENDIX:
        required.update(relative for _, relative, optional in items if not optional)
    return required


def _full_tasks() -> list[dict]:
    products = macro_product_artifacts()
    tasks = [
        _task(
            "macro.harvest",
            "DETERMINISTIC",
            dependencies=[],
            inputs=[],
            outputs=["macro.data", "macro.global_tape"],
            contract="macro.harvest.v1",
            operation="macro.harvest",
        )
    ]
    sequence = (
        ("regional.us", "3_regional/us.md"),
        ("regional.china", "3_regional/china.md"),
        ("regional.global", "3_regional/global.md"),
        ("crossasset.rates", "4_crossasset/rates.md"),
        ("crossasset.fx", "4_crossasset/fx.md"),
        ("crossasset.equities", "4_crossasset/equities.md"),
        ("crossasset.commodities", "4_crossasset/commodities.md"),
        ("crossasset.crypto", "4_crossasset/crypto.md"),
        ("sinous.divergence", "5_sinous/divergence.md"),
        ("sinous.desync", "5_sinous/desync.md"),
        ("sinous.geopolitics", "5_sinous/geopolitics.md"),
        ("sinous.relative", "5_sinous/relative.md"),
        ("meso.sector_map", "2_meso/sector_map.md"),
        ("meso.flows", "2_meso/flows.md"),
        ("meso.sentiment", "2_meso/sentiment.md"),
        ("meso.themes", "2_meso/themes.md"),
        ("spine.variant", "1_spine/variant.md"),
        ("spine.crossfire", "1_spine/crossfire.md"),
        ("spine.calendar", "1_spine/calendar.md"),
        ("spine.premortem", "1_spine/premortem.md"),
        ("spine.decision", "1_spine/decision.md"),
    )
    previous_task = "macro.harvest"
    previous_artifact = "macro.data"
    for name, relative in sequence:
        task_id = f"macro.{name}"
        contract = (
            "macro.allocation.v1"
            if relative in {assemble.DECISION_REL, assemble.SECTOR_MAP_REL}
            else "macro.section.v1"
        )
        tasks.append(
            _task(
                task_id,
                "INFERENCE",
                dependencies=[previous_task],
                inputs=["macro.data", previous_artifact]
                if previous_artifact != "macro.data"
                else ["macro.data"],
                outputs=[products[relative]],
                contract=contract,
                role="macro.research",
            )
        )
        previous_task = task_id
        previous_artifact = products[relative]
    required_inputs = [
        products[relative] for relative in sorted(required_macro_products())
    ]
    tasks.extend(
        [
            _task(
                "macro.full.validate",
                "DETERMINISTIC",
                dependencies=[previous_task],
                inputs=required_inputs,
                outputs=["macro.full.validation"],
                contract="macro.full.validation.v1",
                operation="macro.full.validate",
            ),
            _task(
                "macro.assemble",
                "DETERMINISTIC",
                dependencies=["macro.full.validate"],
                inputs=["macro.full.validation"],
                outputs=[
                    "macro.full.report",
                    "macro.state.candidate",
                    "macro.publication.bundle",
                ],
                contract="macro.full.publication.v1",
                operation="macro.full.assemble",
            ),
        ]
    )
    return tasks


def _lite_tasks() -> list[dict]:
    return [
        _task(
            "macro.frame",
            "DETERMINISTIC",
            dependencies=[],
            inputs=[],
            outputs=["macro.market_pack", "macro.strategist_pack"],
            contract="macro.frame.v1",
            operation="macro.lite.frame",
        ),
        _task(
            "macro.brief",
            "INFERENCE",
            dependencies=["macro.frame"],
            inputs=["macro.strategist_pack"],
            outputs=["macro.market_view"],
            contract="macro.brief.v1",
            role="macro.brief",
        ),
        _task(
            "macro.lite.validate",
            "DETERMINISTIC",
            dependencies=["macro.brief"],
            inputs=["macro.market_view"],
            outputs=["macro.lite.validation"],
            contract="macro.lite.validation.v1",
            operation="macro.lite.validate",
        ),
        _task(
            "macro.publish",
            "DETERMINISTIC",
            dependencies=["macro.lite.validate"],
            inputs=["macro.market_view", "macro.lite.validation"],
            outputs=["macro.publication.bundle"],
            contract="macro.lite.publication.v1",
            operation="macro.publish",
        ),
    ]


def build_macro_plan(request: dict, handle) -> dict:
    if request["kind"] != "macro-research":
        raise ValueError("macro plan requires macro-research request")
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
        "tasks": _lite_tasks() if request["requested_mode"] == "LITE" else _full_tasks(),
        "task_templates": [],
        "plan_hash": "0" * 64,
    }
    value["plan_hash"] = plan_hash(value)
    return value


def register_macro_artifacts(request: dict, handle, plan: dict) -> None:
    del plan
    staging = Path(handle.staging)
    output = staging / "session_outputs"
    if request["requested_mode"] == "LITE":
        registrations = {
            "macro.market_pack": output / "market_pack.json",
            "macro.strategist_pack": output / "strategist_pack.json",
            "macro.market_view": output / "market_view.md",
            "macro.lite.validation": output / "macro.lite.validation.json",
            "macro.publication.bundle": output / "macro.publication.json",
        }
    else:
        root = staging / "macro" / request["analysis_date"]
        registrations = {
            "macro.data": root / "data.md",
            "macro.global_tape": root / "global_tape.json",
            **{
                artifact_id: root / relative
                for relative, artifact_id in macro_product_artifacts().items()
            },
            "macro.full.validation": output / "macro.full.validation.json",
            "macro.full.report": output / "macro.full.report.md",
            "macro.state.candidate": output / "macro_state.json",
            "macro.publication.bundle": output / "macro.publication.json",
        }
    for artifact_id, path in registrations.items():
        artifacts.register_artifact(handle, artifact_id, path, "WRITE")


def validate_macro_operation_params(request: dict, task: dict, params: dict) -> None:
    del request, task
    if params != {}:
        raise ValueError("macro deterministic operations accept no parameters")


@contextlib.contextmanager
def _locked(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(f"{path.suffix}.lock").open("a+", encoding="utf-8") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def publish_macro(
    handle,
    *,
    reports_root: Path | str | None = None,
    state_path: Path | str | None = None,
) -> Path:
    request = json.loads(
        (Path(handle.workspace) / "session/request.json").read_text(encoding="utf-8")
    )
    output = Path(handle.staging) / "session_outputs"
    bundle = json.loads((output / "macro.publication.json").read_text(encoding="utf-8"))
    mode = bundle["mode"]
    source = output / ("market_view.md" if mode == "LITE" else "macro.full.report.md")
    if sha256_bytes(source.read_bytes()) != bundle["report_sha256"]:
        raise RuntimeError("macro report changed after publication preparation")
    base = Path(reports_root) if reports_root is not None else ws.reports_root() / "macro"
    target_dir = base / request["analysis_date"].replace("-", "")
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / bundle["output_name"]
    with _locked(target):
        if target.is_file() and target.read_bytes() != source.read_bytes():
            raise RuntimeError("macro report publication conflict")
        if not target.is_file():
            atomic_write_bytes(target, source.read_bytes())
    if mode == "FULL":
        candidate = json.loads((output / "macro_state.json").read_text(encoding="utf-8"))
        latest = Path(state_path) if state_path is not None else ws.context_root() / "macro/macro_state.json"
        with _locked(latest):
            current = json.loads(latest.read_text(encoding="utf-8")) if latest.is_file() else None
            current_as_of = str((current or {}).get("as_of") or "")
            candidate_as_of = str(candidate.get("as_of") or "")
            current_run = str((current or {}).get("session_run_id") or "")
            candidate_run = str(candidate.get("session_run_id") or "")
            if (current_as_of, current_run) < (candidate_as_of, candidate_run):
                atomic_write_json(latest, candidate)
            elif (current_as_of, current_run) == (candidate_as_of, candidate_run) and current != candidate:
                raise RuntimeError("same-date macro_state publication conflict")
    return target


__all__ = [
    "build_macro_plan",
    "macro_product_artifacts",
    "publish_macro",
    "register_macro_artifacts",
    "required_macro_products",
    "validate_macro_operation_params",
]
