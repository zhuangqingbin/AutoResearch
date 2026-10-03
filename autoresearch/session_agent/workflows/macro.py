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
from autoresearch.macro.grouped_products import required_macro_products
from autoresearch.session_agent import artifacts
from autoresearch.session_agent.roles import roles_hash
from autoresearch.session_agent.workflows.macro_groups import grouped_products


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
        "independent_context": kind == "INFERENCE" and role in {"macro.research", "global.intel"},
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




def _full_tasks(profile: str = "serial21", optional_products: tuple[str, ...] = ()) -> list[dict]:
    products = macro_product_artifacts()
    tasks = [
        _task(
            "macro.harvest",
            "DETERMINISTIC",
            dependencies=[],
            inputs=[],
            outputs=["macro.data", "macro.global_tape", "macro.scan_meta"],
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
    tasks.extend([
        _task("macro.intel.prepare", "DETERMINISTIC", dependencies=["macro.harvest"],
              inputs=["research.frame", "macro.global_tape", "macro.intel.policy"], outputs=["macro.intel.request"],
              contract="macro.intel.request.v1", operation="macro.intel.prepare"),
        _task("macro.global_intel", "INFERENCE", dependencies=["macro.intel.prepare"],
              inputs=["macro.intel.request"], outputs=["macro.intel"],
              contract="global.intel.v1", role="global.intel"),
    ])
    raw_inputs = ["research.frame", "macro.data", "macro.global_tape", "macro.scan_meta", "macro.intel"]
    research_tasks = []
    if profile == "six_groups_v1":
        for name, relatives, dependencies in grouped_products(optional_products):
            upstream = [task for task in research_tasks if task["task_id"] in dependencies]
            research_tasks.append(_task(
                f"macro.group.{name}", "INFERENCE",
                dependencies=dependencies or ["macro.global_intel"],
                inputs=[*raw_inputs, *(output for task in upstream for output in task["output_artifact_ids"])],
                outputs=[products[relative] for relative in relatives],
                contract="macro.allocation.v1" if name == "decision" else "macro.section.v1",
                role="macro.research",
            ))
    elif profile == "serial21":
        previous_task = "macro.global_intel"
        # Optional files are frozen before dispatch, never late unregistered writes.
        optional_after = {
            "4_crossasset/crypto.md": ("crossasset.credit", "4_crossasset/credit.md"),
            "2_meso/themes.md": ("meso.industry_cycle", "6_meso_evidence/industry_cycle.md"),
            "1_spine/premortem.md": ("spine.debate", "1_spine/debate.md"),
        }
        expanded = []
        for name, relative in sequence:
            expanded.append((name, relative))
            optional = optional_after.get(relative)
            if optional and optional[1] in optional_products:
                expanded.append(optional)
        for name, relative in expanded:
            task_id = f"macro.{name}"
            contract = (
                "macro.allocation.v1"
                if relative in {assemble.DECISION_REL, assemble.SECTOR_MAP_REL}
                else "macro.section.v1"
            )
            research_tasks.append(_task(
                task_id, "INFERENCE", dependencies=[previous_task],
                inputs=[*raw_inputs, *(output for task in research_tasks for output in task["output_artifact_ids"])],
                outputs=[products[relative]], contract=contract, role="macro.research",
            ))
            previous_task = task_id
    else:
        raise ValueError(f"unknown macro research profile: {profile}")
    tasks.extend(research_tasks)
    previous_task = research_tasks[-1]["task_id"]
    required_inputs = [products[relative] for relative in sorted(required_macro_products() | set(optional_products))]
    tasks.extend(
        [
            _task(
                "macro.full.validate",
                "DETERMINISTIC",
                dependencies=[previous_task],
                inputs=["macro.data", *required_inputs],
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
    assembled_products = [
        artifact_id
        for task in tasks
        if task["kind"] == "INFERENCE"
        for artifact_id in task["output_artifact_ids"]
    ]
    tasks[-1]["input_artifact_ids"] = [
        "macro.full.validation",
        "macro.data",
        "macro.global_tape",
        "macro.scan_meta",
        *dict.fromkeys(assembled_products),
    ]
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
    profile = request.get("macro_research_profile", "serial21")
    optional_products = tuple(request.get("macro_optional_products", []))
    if (profile != "serial21" or optional_products) and request.get("schema_version", 1) < 4:
        raise ValueError("macro profile selection requires frozen begin request v4")
    if (profile != "serial21" or optional_products) and request["requested_mode"] != "FULL":
        raise ValueError("macro profile selection requires FULL")
    # Validate selections even for serial layouts; the assembler remains the path owner.
    optional_scope = set(macro_product_artifacts()) - required_macro_products()
    if len(set(optional_products)) != len(optional_products) or set(optional_products) - optional_scope:
        raise ValueError("invalid macro optional products")
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
        "host_profile_hash": sha256_bytes(canonical_json(request["host_profile"]).encode("utf-8")),
        "roles_hash": roles_hash(),
        "tasks": _lite_tasks() if request["requested_mode"] == "LITE" else _full_tasks(profile, optional_products),
        "task_templates": [],
        "plan_hash": "0" * 64,
    }
    value["plan_hash"] = plan_hash(value)
    return value


def global_intel_policy(config: dict | None) -> dict:
    """`session.global_intel_max_queries` is frozen once as a replayable input."""
    cap = (config or {}).get("session", {}).get("global_intel_max_queries", 8)
    if type(cap) is not int or cap < 1:
        raise ValueError("global intel source budget must be a positive integer")
    return {"schema_version": 1, "source_budget": {"max_queries": cap, "unit": "SEARCH_AND_FETCH"}}


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
        policy_path = output / "macro.intel.policy.json"
        if not policy_path.exists():
            atomic_write_json(policy_path, global_intel_policy(getattr(handle.contract, "user_config", {})))
        registrations = {
            "macro.intel.policy": policy_path,
            "macro.data": root / "data.md",
            "macro.intel.request": output / "macro.intel.request.json",
            "macro.intel": root / "_global_intel.md",
            "macro.global_tape": root / "global_tape.json",
            "macro.scan_meta": root / "scan_meta.json",
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
        artifacts.register_artifact(handle, artifact_id, path,
                                    "READ" if artifact_id == "macro.intel.policy" else "WRITE")


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


def _publish_macro_active(
    handle,
    *,
    reports_root: Path | str | None = None,
    state_path: Path | str | None = None,
) -> Path:
    request = json.loads(
        (Path(handle.workspace) / "session/request.json").read_text(encoding="utf-8")
    )
    with artifacts.open_artifact(handle, "macro.publication.bundle") as stream:
        bundle = json.load(stream)
    mode = bundle["mode"]
    report_id = "macro.market_view" if mode == "LITE" else "macro.full.report"
    with artifacts.open_artifact(handle, report_id) as stream:
        report = stream.read()
    if sha256_bytes(report) != bundle["report_sha256"]:
        raise RuntimeError("macro report changed after publication preparation")
    base = Path(reports_root) if reports_root is not None else ws.reports_root() / "macro"
    target_dir = base / request["analysis_date"].replace("-", "")
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / bundle["output_name"]
    with _locked(target):
        if target.is_file() and target.read_bytes() != report:
            raise RuntimeError("macro report publication conflict")
        if not target.is_file():
            atomic_write_bytes(target, report)
    if mode == "FULL":
        with artifacts.open_artifact(handle, "macro.state.candidate") as stream:
            candidate = json.load(stream)
        latest = (
            Path(state_path)
            if state_path is not None
            else ws.context_root() / "macro/macro_state.json"
        )
        with _locked(latest):
            current = json.loads(latest.read_text(encoding="utf-8")) if latest.is_file() else None
            current_as_of = str((current or {}).get("as_of") or "")
            candidate_as_of = str(candidate.get("as_of") or "")
            current_run = str((current or {}).get("session_run_id") or "")
            candidate_run = str(candidate.get("session_run_id") or "")
            if (current_as_of, current_run) < (candidate_as_of, candidate_run):
                atomic_write_json(latest, candidate)
            elif (current_as_of, current_run) == (
                candidate_as_of,
                candidate_run,
            ) and current != candidate:
                raise RuntimeError("same-date macro_state publication conflict")
    return target


def publish_macro(
    handle,
    *,
    reports_root: Path | str | None = None,
    state_path: Path | str | None = None,
) -> Path:
    """Publish report and optional state only inside an active run write window."""
    from autoresearch.trace.write_guard import assert_output_path, guarded_handle_write

    with guarded_handle_write(handle, "macro.publish") as tracked:
        if tracked is not None:
            base = Path(reports_root) if reports_root is not None else ws.reports_root() / "macro"
            assert_output_path(base, ws.run_reports_root("macro-research"))
            request = json.loads(
                (Path(handle.workspace) / "session/request.json").read_text(encoding="utf-8")
            )
            with artifacts.open_artifact(handle, "macro.publication.bundle") as stream:
                bundle = json.load(stream)
            assert_output_path(
                base / request["analysis_date"].replace("-", "") / bundle["output_name"],
                base,
            )
            if state_path is not None:
                assert_output_path(state_path, ws.context_root())
        return _publish_macro_active(
            handle,
            reports_root=reports_root,
            state_path=state_path,
        )


def prepare_macro_bundle(handle) -> dict:
    """Describe the immutable report and optional latest-state mutation."""
    with artifacts.open_artifact(handle, "macro.publication.bundle") as stream:
        bundle = json.load(stream)
    report_id = "macro.market_view" if bundle["mode"] == "LITE" else "macro.full.report"
    mutations = []
    if bundle["mode"] == "FULL":
        mutations.append(
            {
                "target_key": "macro.latest_state",
                "expected_before_hash": None,
                "after_artifact_id": "macro.state.candidate",
                "apply_policy": "ADVANCE_IF_NEWER",
            }
        )
    return {
        "business_files": [
            {
                "artifact_id": report_id,
                "relative_path": f"report/{bundle['output_name']}",
                "media_type": "text/markdown",
            }
        ],
        "state_mutations": mutations,
        "inline_artifacts": {},
    }


__all__ = [
    "build_macro_plan",
    "macro_product_artifacts",
    "prepare_macro_bundle",
    "publish_macro",
    "register_macro_artifacts",
    "required_macro_products",
    "validate_macro_operation_params",
]
