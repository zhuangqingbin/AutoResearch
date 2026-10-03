"""Session plans for standalone stock research."""

from __future__ import annotations

import contextlib
import fcntl
import json
from collections.abc import Iterator
from pathlib import Path

from autoresearch.analyze import assemble as stock_assemble
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
            "stock.harvest",
            "DETERMINISTIC",
            dependencies=[],
            inputs=[],
            outputs=["stock.slim", "stock.deep"],
            contract="stock.harvest.slim.v1",
            subject=ticker,
            operation="stock.harvest",
        ),
        _task(
            "stock.card",
            "INFERENCE",
            dependencies=["stock.harvest"],
            inputs=["stock.slim", "stock.deep"],
            outputs=["stock.card.output"],
            contract="stock.lite.v1",
            subject=ticker,
            role="stock.card",
        ),
        _task(
            "stock.validate",
            "DETERMINISTIC",
            dependencies=["stock.card"],
            inputs=["stock.card.output", "stock.slim", "stock.deep"],
            outputs=["stock.card.validation"],
            contract="stock.validation.v1",
            subject=ticker,
            operation="stock.validate",
        ),
        _task(
            "stock.publish",
            "DETERMINISTIC",
            dependencies=["stock.validate"],
            inputs=["stock.card.output", "stock.card.validation"],
            outputs=["stock.publication.bundle"],
            contract="stock.publication.v1",
            subject=ticker,
            operation="stock.publish",
        ),
    ]



def _two_stage_lite_tasks(ticker: str) -> list[dict]:
    tasks = _lite_tasks(ticker)
    facts = _task("stock.card.facts", "DETERMINISTIC", dependencies=["stock.harvest"],
                  inputs=["stock.slim", "stock.deep"], outputs=["stock.card.facts"],
                  contract="research.card.facts.v1", subject=ticker, operation="research.card.facts")
    initial = _task("stock.initial", "INFERENCE", dependencies=["stock.card.facts"],
                    inputs=["stock.card.facts"], outputs=["stock.card.initial"],
                    contract="research.card.initial.v1", subject=ticker, role="stock.card")
    initial["independent_context"] = True
    final = tasks[1]
    final.update(dependencies=["stock.initial"],
                 input_artifact_ids=["stock.card.facts", "stock.card.initial", "stock.slim", "stock.deep"],
                 output_artifact_ids=["stock.card.output", "stock.card.changes"],
                 expected_output_contract="research.card.decision.v1", independent_context=True)
    return [tasks[0], facts, initial, *tasks[1:]]

def _artifact_id(relative: str) -> str:
    stem = relative[:-3] if relative.endswith(".md") else relative
    return "stock.full." + stem.replace("/", ".")


def full_product_artifacts() -> dict[str, str]:
    relatives = {stock_assemble.DECISION_REL}
    for _, items in stock_assemble.SPINE + stock_assemble.APPENDIX:
        relatives.update(relative for _, relative, _ in items)
    return {relative: _artifact_id(relative) for relative in sorted(relatives)}


def required_full_products() -> set[str]:
    required = {stock_assemble.DECISION_REL}
    for _, items in stock_assemble.SPINE + stock_assemble.APPENDIX:
        required.update(relative for _, relative, optional in items if not optional)
    return required


def _full_tasks(ticker: str, *, ashare: bool, has_peers: bool) -> list[dict]:
    products = full_product_artifacts()
    tasks = [
        _task(
            "stock.harvest",
            "DETERMINISTIC",
            dependencies=[],
            inputs=[],
            outputs=["stock.context", "stock.indicators"],
            contract="stock.harvest.full.v1",
            subject=ticker,
            operation="stock.harvest",
        ),
        _task(
            "stock.intel",
            "INFERENCE",
            dependencies=["stock.harvest"],
            inputs=["stock.context"],
            outputs=["stock.full.intel"],
            contract="company.intel.v1",
            subject=ticker,
            role="company.intel" if ashare else "us.intel",
        ),
    ]
    analyst_specs = (
        ("market", "stock.market", "1_analysts/market.md", ["stock.harvest"]),
        ("news", "stock.news", "1_analysts/news.md", ["stock.intel"]),
        ("fundamentals", "stock.fundamentals", "1_analysts/fundamentals.md", ["stock.harvest"]),
        ("quality", "stock.quality", "1_analysts/quality.md", ["stock.fundamentals"]),
        ("valuation", "stock.valuation", "1_analysts/valuation.md", ["stock.fundamentals"]),
        ("positioning", "stock.positioning", "1_analysts/positioning.md", ["stock.harvest"]),
        ("solvency", "stock.solvency", "1_analysts/solvency.md", ["stock.fundamentals"]),
    )
    for name, role, relative, dependencies in analyst_specs:
        inputs = ["stock.context"]
        if name == "news":
            inputs.append("stock.full.intel")
        if name in {"quality", "valuation", "solvency"}:
            inputs.append(products["1_analysts/fundamentals.md"])
        if name == "market":
            inputs.append("stock.indicators")
        tasks.append(
            _task(
                f"stock.{name}",
                "INFERENCE",
                dependencies=dependencies,
                inputs=inputs,
                outputs=[products[relative]],
                contract="stock.section.v1",
                subject=ticker,
                role=role,
            )
        )
    if has_peers:
        tasks.append(
            _task(
                "stock.peer",
                "INFERENCE",
                dependencies=["stock.fundamentals"],
                inputs=["stock.context", products["1_analysts/fundamentals.md"]],
                outputs=[products["1_analysts/peer.md"]],
                contract="stock.section.v1",
                subject=ticker,
                role="stock.peer",
            )
        )
    analyst_dependencies = [
        task["task_id"]
        for task in tasks
        if task["task_id"].startswith("stock.")
        and task["task_id"] not in {"stock.harvest", "stock.intel"}
    ]
    evidence_inputs = [
        "research.frame", "stock.context", "stock.indicators", "stock.full.intel",
        *(task["output_artifact_ids"][0] for task in tasks
          if task["task_id"] in analyst_dependencies),
    ]
    tasks.append(_task(
        "stock.evidence_bundle", "DETERMINISTIC",
        dependencies=[*analyst_dependencies, "stock.intel"], inputs=evidence_inputs,
        outputs=["stock.evidence_bundle"], contract="stock.evidence_bundle.v1",
        subject=ticker, operation="stock.evidence_bundle",
    ))
    tasks.extend(
        [
            _task(
                "stock.reality_check",
                "INFERENCE",
                dependencies=["stock.evidence_bundle"],
                inputs=[
                    products["1_analysts/market.md"],
                    products["1_analysts/news.md"],
                    products["1_analysts/fundamentals.md"],
                ],
                outputs=[products["2_research/reality_check.md"]],
                contract="stock.section.v1",
                subject=ticker,
                role="stock.reality_check",
            ),
            _task(
                "stock.bull",
                "INFERENCE",
                dependencies=["stock.reality_check"],
                inputs=[products["2_research/reality_check.md"]],
                outputs=[products["2_research/bull.md"]],
                contract="stock.section.v1",
                subject=ticker,
                role="stock.bull",
            ),
            _task(
                "stock.bear",
                "INFERENCE",
                dependencies=["stock.bull"],
                inputs=[products["2_research/bull.md"]],
                outputs=[products["2_research/bear.md"]],
                contract="stock.section.v1",
                subject=ticker,
                role="stock.bear",
            ),
            _task(
                "stock.manager",
                "INFERENCE",
                dependencies=["stock.bear"],
                inputs=[products["2_research/bull.md"], products["2_research/bear.md"]],
                outputs=[products["2_research/manager.md"]],
                contract="stock.section.v1",
                subject=ticker,
                role="stock.manager",
            ),
            _task(
                "stock.risk",
                "INFERENCE",
                dependencies=["stock.manager"],
                inputs=[products["2_research/manager.md"]],
                outputs=[products["3_risk/debate.md"]],
                contract="stock.section.v1",
                subject=ticker,
                role="stock.risk",
            ),
            _task(
                "stock.premortem",
                "INFERENCE",
                dependencies=["stock.manager", "stock.risk"],
                inputs=[products["2_research/manager.md"], products["3_risk/debate.md"]],
                outputs=[products["3_risk/premortem.md"]],
                contract="stock.section.v1",
                subject=ticker,
                role="stock.premortem",
            ),
            _task(
                "stock.pm",
                "INFERENCE",
                dependencies=["stock.premortem"],
                inputs=[products["2_research/manager.md"], products["3_risk/premortem.md"]],
                outputs=[
                    products["4_portfolio/decision.md"],
                    products["4_portfolio/calendar.md"],
                    products["2_research/variant.md"],
                    products["2_research/faceoff.md"],
                ],
                contract="stock.pm.v1",
                subject=ticker,
                role="stock.pm",
            ),
            _task(
                "stock.full.validate",
                "DETERMINISTIC",
                dependencies=["stock.pm"],
                inputs=[
                    products[relative]
                    for relative in sorted(products)
                    if relative in required_full_products()
                ],
                outputs=["stock.full.validation"],
                contract="stock.full.validation.v1",
                subject=ticker,
                operation="stock.full.validate",
            ),
            _task(
                "stock.assemble",
                "DETERMINISTIC",
                dependencies=["stock.full.validate"],
                inputs=["stock.full.validation"],
                outputs=["stock.full.report", "stock.full.manifest", "stock.publication.bundle"],
                contract="stock.full.publication.v1",
                subject=ticker,
                operation="stock.full.assemble",
            ),
        ]
    )
    # A bundle is an index, never a capability to open undeclared paths. Every
    # downstream reasoning task receives the exact original inputs as well.
    extra_sections = {
        "bear": ["2_research/reality_check.md"],
        "manager": ["2_research/reality_check.md"],
        "risk": ["2_research/reality_check.md", "2_research/bull.md", "2_research/bear.md"],
        "premortem": ["2_research/reality_check.md", "2_research/bull.md", "2_research/bear.md"],
        "pm": ["3_risk/debate.md", "2_research/reality_check.md", "2_research/bull.md", "2_research/bear.md"],
    }
    downstream = {"reality_check", "bull", "bear", "manager", "risk", "premortem", "pm"}
    for task in tasks:
        if task["kind"] == "INFERENCE":
            task["independent_context"] = True
        name = task["task_id"].removeprefix("stock.")
        if name in downstream:
            task["input_artifact_ids"] = list(dict.fromkeys([
                "stock.evidence_bundle", *evidence_inputs, *task["input_artifact_ids"],
                *(products[rel] for rel in extra_sections.get(name, [])),
            ]))
    # The assembler reads the section tree directly.  Its forensic input contract must
    # therefore name every section it can render, not merely the validation hash list.
    assembled_products = [
        artifact_id
        for task in tasks
        if task["kind"] == "INFERENCE"
        for artifact_id in task["output_artifact_ids"]
        if artifact_id in set(products.values())
    ]
    tasks[-1]["input_artifact_ids"] = [
        "stock.full.validation",
        "stock.context",
        *dict.fromkeys(assembled_products),
    ]
    return tasks


def build_stock_plan(request: dict, handle) -> dict:
    if request["kind"] != "stock-research":
        raise ValueError("stock plan requires stock-research request")
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
        "tasks": (
            (_two_stage_lite_tasks(request["subject"])
             if request.get("card_research_profile") == "two-stage-v1"
             else _lite_tasks(request["subject"]))
            if request["requested_mode"] == "LITE"
            else _full_tasks(
                request["subject"],
                ashare=str(request["subject"]).split(".")[0].isdigit(),
                has_peers=bool(request["peers"]),
            )
        ),
        "task_templates": [],
        "plan_hash": "0" * 64,
    }
    value["plan_hash"] = plan_hash(value)
    return value


def register_stock_artifacts(request: dict, handle, plan: dict) -> None:
    ticker = normalize_symbol(request["subject"])
    analysis_date = request["analysis_date"]
    staging = Path(handle.staging)
    output = staging / "session_outputs"
    if request["requested_mode"] == "LITE":
        registrations = {
            "stock.slim": staging / f"{ticker}_{analysis_date}_slim.md",
            "stock.deep": staging / f"{ticker}_{analysis_date}_slim_deep.md",
            "stock.card.output": output / "card.md",
            "stock.card.validation": output / "card.validation.json",
            "stock.publication.bundle": output / "publication.json",
        }
    else:
        root = staging / "analyze" / f"{ticker}_{analysis_date.replace('-', '')}"
        registrations = {
            "stock.context": staging / f"{ticker}_{analysis_date}.md",
            "stock.indicators": staging / f"{ticker}_{analysis_date}_indicators.md",
            "stock.full.intel": root
            / ("_company_intel.md" if ticker.split(".")[0].isdigit() else "_us_intel.md"),
            **{
                artifact_id: root / relative
                for relative, artifact_id in full_product_artifacts().items()
            },
            "stock.evidence_bundle": output / "evidence_bundle.json",
            "stock.full.validation": output / "full.validation.json",
            "stock.full.report": output / "full_report.md",
            "stock.full.manifest": output / "full_manifest.json",
            "stock.publication.bundle": output / "publication.json",
        }
    # Replay has the frozen request but no live profile or plan.
    if (any(task.get("expected_output_contract") == "research.card.initial.v1"
            for task in plan.get("tasks", []))
            or (not plan and request.get("card_research_profile") == "two-stage-v1")):
        registrations.update({"stock.card.facts": output / "card.facts.json",
                              "stock.card.initial": output / "card.initial.json",
                              "stock.card.changes": output / "card.changes.json"})
    for artifact_id, path in registrations.items():
        artifacts.register_artifact(handle, artifact_id, path, "WRITE")


def harvest_params(request: dict) -> dict:
    """The only parameters ``stock.harvest`` may run with: a projection of the frozen request."""
    return {
        "ticker": request["subject"],
        "analysis_date": request["analysis_date"],
        "asset_type": request["asset_type"],
        "peers": list(request["peers"]),
        "slim": request["requested_mode"] == "LITE",
    }


def validate_stock_operation_params(request: dict, task: dict, params: dict) -> None:
    if task["operation"] == "stock.harvest":
        if params != harvest_params(request):
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


def _publish_stock_active(handle, *, reports_root: Path | None = None) -> Path:
    """Publish a validated stock artifact under the existing analyze layout."""
    with artifacts.open_artifact(handle, "stock.publication.bundle") as stream:
        bundle = json.loads(stream.read().decode("utf-8"))
    if bundle["mode"] == "LITE":
        with artifacts.open_artifact(handle, "stock.card.output") as stream:
            report_bytes = stream.read()
        if sha256_bytes(report_bytes) != bundle["card_sha256"]:
            raise RuntimeError("stock card changed after publication bundle validation")
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
        from autoresearch.contracts.profiles import CURRENT_CARD_RULES
        from autoresearch.trace.completeness import card_rules_from_capsule
        if getattr(handle, "capsule", None) is not None and card_rules_from_capsule(handle.capsule) == CURRENT_CARD_RULES:
            from autoresearch.news.card_claims import registered_card_semantics
            from autoresearch.trace.completeness import card_rating_bands_from_capsule
            with artifacts.open_artifact(handle, "research.frame") as stream:
                frame = json.loads(stream.read())
            semantics = registered_card_semantics(handle, report_bytes.decode("utf-8"), subject=bundle["ticker"], frame=frame,
                                               artifact_id="stock.card.output",
                                               frame_hash=artifacts.snapshot_artifact(handle, "research.frame")["sha256"],
                                               bands=card_rating_bands_from_capsule(handle.capsule))
            manifest.update({key: semantics[key] for key in ("machine_suggestion", "machine_reason", "execution", "claim_usage")})
    elif bundle["mode"] == "FULL":
        with artifacts.open_artifact(handle, "stock.full.report") as stream:
            report_bytes = stream.read()
        if sha256_bytes(report_bytes) != bundle["report_sha256"]:
            raise RuntimeError("stock full report changed after assembly")
        with artifacts.open_artifact(handle, "stock.full.manifest") as stream:
            manifest = json.loads(stream.read().decode("utf-8"))
        if manifest.get("run_id") != handle.run_id:
            raise RuntimeError("stock full manifest run identity mismatch")
        from autoresearch.contracts.profiles import CURRENT_CARD_RULES
        from autoresearch.trace.completeness import (
            card_rating_bands_from_capsule,
            card_rules_from_capsule,
        )
        if getattr(handle, "capsule", None) is not None and card_rules_from_capsule(handle.capsule) == CURRENT_CARD_RULES:
            from autoresearch.news.card_claims import registered_card_semantics
            frame = json.loads(artifacts.read_bytes(handle, "research.frame"))
            semantics = registered_card_semantics(handle,
                artifacts.read_bytes(handle, "stock.full.4_decision.decision").decode(), subject=bundle["ticker"],
                frame=frame, frame_hash=artifacts.snapshot_artifact(handle, "research.frame")["sha256"],
                artifact_id="stock.full.4_decision.decision", bands=card_rating_bands_from_capsule(handle.capsule))
            if manifest.get("claim_usage") != semantics["claim_usage"]:
                raise RuntimeError("full report claim usage changed since assembly")
    else:
        raise RuntimeError(f"unknown stock publication mode: {bundle['mode']}")
    root = Path(reports_root) if reports_root is not None else ws.run_reports_root("stock-research")
    run_hhmm = handle.run_id[9:13]
    report_dir = root / f"{bundle['analysis_date'].replace('-', '')}_{run_hhmm}"
    report = report_dir / bundle["output_name"]
    with _publish_lock(root):
        if report.is_file() and report.read_bytes() != report_bytes:
            raise RuntimeError("stock report already exists with different content")
        manifest_path = report_dir / "manifest.json"
        if manifest_path.is_file():
            current = json.loads(manifest_path.read_text(encoding="utf-8"))
            if canonical_json(current) != canonical_json(manifest):
                raise RuntimeError("stock report manifest conflicts with this run")
        atomic_write_bytes(report, report_bytes)
        atomic_write_json(manifest_path, manifest)
        if "claim_usage" in manifest:
            from autoresearch.news.card_claims import usage_note
            atomic_write_json(report_dir / "claim_usage.json", manifest["claim_usage"])
            atomic_write_bytes(report_dir / "claim_usage.md", usage_note(manifest["claim_usage"]).encode())
    return report_dir


def publish_stock(handle, *, reports_root: Path | None = None) -> Path:
    """Publish only while the bound run still owns an active write window."""
    from autoresearch.trace.write_guard import assert_output_path, guarded_handle_write

    with guarded_handle_write(handle, "stock.publish") as tracked:
        if tracked is not None:
            root = (
                Path(reports_root)
                if reports_root is not None
                else ws.run_reports_root("stock-research")
            )
            assert_output_path(root, ws.run_reports_root("stock-research"))
            with artifacts.open_artifact(handle, "stock.publication.bundle") as stream:
                bundle = json.loads(stream.read().decode("utf-8"))
            report_dir = root / (
                f"{bundle['analysis_date'].replace('-', '')}_{handle.run_id[9:13]}"
            )
            assert_output_path(report_dir / bundle["output_name"], root)
            assert_output_path(report_dir / "manifest.json", root)
        return _publish_stock_active(handle, reports_root=reports_root)


def prepare_stock_bundle(handle) -> dict:
    """Describe immutable stock files without publishing compatibility views."""
    with artifacts.open_artifact(handle, "stock.publication.bundle") as stream:
        bundle = json.loads(stream.read().decode("utf-8"))
    if bundle["mode"] == "LITE":
        files = [
            {
                "artifact_id": "stock.card.output",
                "relative_path": f"report/{bundle['output_name']}",
                "media_type": "text/markdown",
            }
        ]
    elif bundle["mode"] == "FULL":
        files = [
            {
                "artifact_id": "stock.full.report",
                "relative_path": f"report/{bundle['output_name']}",
                "media_type": "text/markdown",
            },
            {
                "artifact_id": "stock.full.manifest",
                "relative_path": "report/manifest.json",
                "media_type": "application/json",
            },
        ]
    else:
        raise RuntimeError(f"unknown stock publication mode: {bundle['mode']}")
    return {"business_files": files, "state_mutations": [], "inline_artifacts": {}}


__all__ = [
    "build_stock_plan",
    "prepare_stock_bundle",
    "publish_stock",
    "register_stock_artifacts",
    "required_full_products",
    "validate_stock_operation_params",
]
