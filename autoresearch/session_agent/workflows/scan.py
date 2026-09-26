"""Frozen and dynamically expanded plans for whole-market research."""

from __future__ import annotations

import fcntl
import hashlib
import json
import re
import shutil
from contextlib import contextmanager
from pathlib import Path

from autoresearch.common import workspace as ws
from autoresearch.common.atomic import atomic_write_bytes, canonical_json, sha256_bytes
from autoresearch.contracts.session_plan import expansion_hash, plan_hash
from autoresearch.scan import run_mode
from autoresearch.scan.l4_tasks import MAX_ATTEMPTS
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
    subject: str | None = None,
    owner: str = "SESSION",
    independent: bool = False,
    parent_task: dict | None = None,
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
        "owner": owner,
        "subject": subject,
        "independent_context": independent,
        "parent_task": parent_task,
    }


def _fixed_tasks() -> list[dict]:
    return [
        _task(
            "scan.frame",
            "DETERMINISTIC",
            dependencies=[],
            inputs=[],
            outputs=["scan.market.pack", "scan.strategist.pack"],
            contract="scan.frame.v1",
            operation="scan.frame",
        ),
        _task(
            "scan.market_view",
            "INFERENCE",
            dependencies=["scan.frame"],
            inputs=["scan.strategist.pack"],
            outputs=["scan.market.view"],
            contract="macro.brief.v1",
            role="macro.brief",
        ),
        _task(
            "scan.prelude",
            "DETERMINISTIC",
            dependencies=[],
            inputs=[],
            outputs=["scan.prelude.summary", "scan.l2", "scan.prelude.bundle"],
            contract="scan.prelude.v1",
            operation="scan.prelude",
        ),
        _task(
            "scan.gate1",
            "DETERMINISTIC",
            dependencies=["scan.prelude", "scan.market_view"],
            inputs=["scan.l2", "scan.market.view", "scan.prelude.bundle"],
            outputs=["scan.gate1.result", "scan.run_mode"],
            contract="scan.gate1.v1",
            operation="scan.gate1",
        ),
    ]


def build_scan_plan(request: dict, handle) -> dict:
    if request["kind"] != "scan-market" or request["requested_mode"] != "AUTO":
        raise ValueError("scan plan requires scan-market/AUTO")
    config_hash = getattr(handle.contract, "config_hash", None) or sha256_bytes(
        canonical_json(getattr(handle.contract, "user_config", {})).encode("utf-8")
    )
    value = {
        "schema_version": 1,
        "engine": handle.engine,
        "run_id": handle.run_id,
        "run_kind": "scan-market",
        "requested_mode": "AUTO",
        "analysis_date": request["analysis_date"],
        "orchestration_version": "session_v1",
        "input_contract_hash": handle.contract.contract_hash,
        "config_hash": config_hash,
        "host_profile_hash": sha256_bytes(canonical_json(request["host_profile"]).encode("utf-8")),
        "roles_hash": roles_hash(),
        "tasks": _fixed_tasks(),
        "task_templates": [
            {
                "template_id": "scan.sectors",
                "expander": "scan.sectors",
                "depends_on": ["scan.gate1"],
                "allowed_roles": [],
            },
            {
                "template_id": "scan.l3",
                "expander": "scan.l3",
                "depends_on": ["scan.gate1"],
                "allowed_roles": ["sector.brief", "scan.l3"],
            },
            {
                "template_id": "scan.l3.repair",
                "expander": "scan.l3.repair",
                "depends_on": ["scan.gate1"],
                "allowed_roles": ["scan.l3.repair"],
            },
            {
                "template_id": "scan.l4",
                "expander": "scan.l4",
                "depends_on": ["scan.gate1"],
                "allowed_roles": ["scan.l4.intel", "scan.l4.card"],
            },
            {
                "template_id": "scan.reviews",
                "expander": "scan.reviews",
                "depends_on": ["scan.gate1"],
                "allowed_roles": ["scan.l4.review"],
            },
            {
                "template_id": "scan.review3",
                "expander": "scan.review3",
                "depends_on": ["scan.gate1"],
                "allowed_roles": ["scan.l4.review"],
            },
        ],
        "plan_hash": "0" * 64,
    }
    value["plan_hash"] = plan_hash(value)
    return value


def _expansion(
    plan: dict,
    template_id: str,
    snapshots: list[dict],
    tasks: list[dict],
) -> dict:
    value = {
        "schema_version": 1,
        "expansion_id": "",
        "plan_hash": plan["plan_hash"],
        "template_id": template_id,
        "input_artifacts": snapshots,
        "tasks": tasks,
        "expansion_hash": "",
    }
    value["expansion_hash"] = expansion_hash(value)
    value["expansion_id"] = f"{template_id}-{value['expansion_hash'][:16]}"
    return value


def sector_expansion(plan: dict, mode: dict, snapshot: dict) -> dict:
    actual = mode.get("mode")
    if actual not in run_mode.MODES:
        raise ValueError("invalid frozen scan run mode")
    full = actual in {run_mode.FULL, run_mode.FORCED_FULL}
    operation = "scan.sector.prepare" if full else "scan.sector.skip"
    task_id = "scan.sector.prepare" if full else "scan.sector.skip"
    return _expansion(
        plan,
        "scan.sectors",
        [snapshot],
        [
            _task(
                task_id,
                "DETERMINISTIC",
                dependencies=["scan.gate1"],
                inputs=["scan.run_mode", "scan.prelude.bundle"],
                outputs=["scan.sector.list", "scan.sector.source.bundle"],
                contract="scan.sector.plan.v1",
                operation=operation,
            )
        ],
    )


def _sector_key(industry: str) -> str:
    return hashlib.sha256(industry.encode("utf-8")).hexdigest()[:12]


def sector_artifact_ids(industry: str) -> tuple[str, str]:
    key = _sector_key(industry)
    return f"scan.sector.{key}.pack", f"scan.sector.{key}.brief"


def l3_expansion(
    plan: dict,
    mode: dict,
    sectors: list[str | dict],
    snapshots: list[dict],
) -> dict:
    actual = mode.get("mode")
    if actual not in run_mode.MODES:
        raise ValueError("invalid frozen scan run mode")
    if actual not in {run_mode.FULL, run_mode.FORCED_FULL}:
        return _expansion(
            plan,
            "scan.l3",
            snapshots,
            [
                _task(
                    "scan.gate2",
                    "DETERMINISTIC",
                    dependencies=["scan.sector.skip"],
                    inputs=[
                        "scan.run_mode",
                        "scan.sector.list",
                        "scan.prelude.bundle",
                        "scan.market.view",
                    ],
                    outputs=[
                        "scan.finalists",
                        "scan.gate2.result",
                        "scan.l3.final.bundle",
                    ],
                    contract="scan.gate2.v1",
                    operation="scan.gate2.skip",
                )
            ],
        )

    tasks = [
        _task(
            "scan.l3.prepare",
            "DETERMINISTIC",
            dependencies=["scan.sector.prepare"],
            inputs=[
                "scan.sector.list",
                "scan.market.view",
                "scan.prelude.bundle",
                "scan.sector.source.bundle",
            ],
            outputs=["scan.l3.table", "scan.l3.source.bundle"],
            contract="scan.l3.input.v1",
            operation="scan.l3.prepare",
        )
    ]
    sector_rows = [
        item if isinstance(item, dict) else {"industry": item, "reused": False} for item in sectors
    ]
    brief_ids = []
    brief_tasks = []
    for row in sector_rows:
        industry = str(row["industry"])
        pack_id, brief_id = sector_artifact_ids(industry)
        key = _sector_key(industry)
        brief_ids.append(brief_id)
        if row.get("reused"):
            continue
        brief_tasks.append(
            _task(
                f"scan.sector.{key}.brief",
                "INFERENCE",
                dependencies=["scan.sector.prepare"],
                inputs=[pack_id],
                outputs=[brief_id],
                contract="sector.terrain.v1",
                role="sector.brief",
                subject=industry,
            )
        )
    tasks.extend(brief_tasks)
    rank_dependencies = ["scan.l3.prepare", *[task["task_id"] for task in brief_tasks]]
    tasks.extend(
        [
            _task(
                "scan.l3.rank",
                "INFERENCE",
                dependencies=rank_dependencies,
                inputs=["scan.l3.table", "scan.market.view", *brief_ids],
                outputs=["scan.l3.judged"],
                contract="scan.l3.v1",
                role="scan.l3",
            ),
            _task(
                "scan.l3.lint",
                "DETERMINISTIC",
                dependencies=["scan.l3.rank"],
                inputs=[
                    "scan.l3.judged",
                    "scan.market.pack",
                    "scan.l3.source.bundle",
                    "scan.sector.list",
                    *brief_ids,
                ],
                outputs=[
                    "scan.l3.validation",
                    "scan.l3.repair.pack",
                    "scan.l3.repair.prompt",
                    "scan.l3.context.bundle",
                ],
                contract="scan.l3.validation.v1",
                operation="scan.l3.lint",
            ),
        ]
    )
    return _expansion(plan, "scan.l3", snapshots, tasks)


def l3_repair_expansion(plan: dict, validation: dict, snapshots: list[dict]) -> dict:
    """Expand at most one narrow L3 repair, then run the original finalist gate."""
    if type(validation.get("ok")) is not bool:
        raise ValueError("L3 validation lacks boolean ok")
    if validation["ok"]:
        repair_task = _task(
            "scan.l3.repair.skip",
            "DETERMINISTIC",
            dependencies=["scan.l3.lint"],
            inputs=["scan.l3.validation", "scan.l3.judged"],
            outputs=["scan.l3.repair.result", "scan.l3.effective.judged"],
            contract="scan.l3.repair.result.v1",
            operation="scan.l3.repair.skip",
        )
    else:
        repair_task = _task(
            "scan.l3.repair",
            "INFERENCE",
            dependencies=["scan.l3.lint"],
            inputs=["scan.l3.repair.prompt"],
            outputs=["scan.l3.repair.patch"],
            contract="scan.l3.repair.v1",
            role="scan.l3.repair",
        )
    tasks = [repair_task]
    if not validation["ok"]:
        tasks.append(
            _task(
                "scan.l3.repair.apply",
                "DETERMINISTIC",
                dependencies=[repair_task["task_id"]],
                inputs=[
                    "scan.l3.repair.pack",
                    "scan.l3.repair.patch",
                    "scan.l3.judged",
                    "scan.l3.context.bundle",
                ],
                outputs=["scan.l3.repair.result", "scan.l3.effective.judged"],
                contract="scan.l3.repair.result.v1",
                operation="scan.l3.repair.apply",
            )
        )
    tasks.append(
        _task(
            "scan.gate2",
            "DETERMINISTIC",
            dependencies=[tasks[-1]["task_id"]],
            inputs=[
                "scan.l3.effective.judged",
                "scan.l3.validation",
                "scan.l3.repair.result",
                "scan.gate1.result",
                "scan.run_mode",
                "scan.l3.context.bundle",
            ],
            outputs=[
                "scan.finalists",
                "scan.l3.bench",
                "scan.gate2.result",
                "scan.l3.final.bundle",
            ],
            contract="scan.gate2.v1",
            operation="scan.l3.merge",
        )
    )
    return _expansion(plan, "scan.l3.repair", snapshots, tasks)


def _l4_ids(code: str, attempt: int = 1) -> dict[str, str]:
    prefix = f"scan.l4.{code}.a{attempt}"
    return {
        "prompt": f"{prefix}.prompt",
        "slim": f"{prefix}.slim",
        "intel": f"{prefix}.intel",
        "intel_status": f"{prefix}.intel_status",
        "intel_bundle": f"{prefix}.intel_bundle",
        "card": f"{prefix}.card",
        "ticket": f"{prefix}.ticket",
    }


def l4_retry_expansion(
    plan: dict,
    code: str,
    attempt: int,
    snapshots: list[dict],
    *,
    intel_enabled: bool,
) -> dict:
    """Create a fresh child subtree for one retryable taskbook attempt."""
    code = str(code).zfill(6)
    if not code.isdigit() or len(code) != 6 or attempt < 2:
        raise ValueError("invalid L4 retry identity")
    if attempt > MAX_ATTEMPTS:
        raise ValueError("L4 retry exceeds the taskbook attempt cap")
    ids = _l4_ids(code, attempt)
    parent = {"owner": "L4_TASKBOOK", "subject": code, "attempt": attempt}
    prefix = f"l4.{code}.a{attempt}"
    tasks = [
        _task(
            prefix,
            "DETERMINISTIC",
            dependencies=["scan.l4.prepare"],
            # The taskbook (_l4_tasks.json) is the ticket owner's mutable state, not a
            # hash-frozen artifact: the claim freezes the preflight receipt instead.
            inputs=["scan.l4.source.bundle", ids["prompt"]],
            outputs=[ids["ticket"]],
            contract="scan.l4.ticket.v1",
            operation="scan.l4.ticket",
            subject=code,
            owner="L4_TASKBOOK",
        ),
        _task(
            f"{prefix}.slim",
            "DETERMINISTIC",
            dependencies=["scan.l4.prepare"],
            inputs=["scan.l4.source.bundle", ids["prompt"]],
            outputs=[ids["slim"]],
            contract="stock.harvest.slim.v1",
            operation="scan.l4.slim",
            subject=code,
            parent_task=parent,
        ),
    ]
    if intel_enabled:
        tasks.append(
            _task(
                f"{prefix}.intel",
                "INFERENCE",
                dependencies=["scan.l4.prepare"],
                inputs=[ids["prompt"]],
                outputs=[ids["intel"]],
                contract="scan.l4.intel.v1",
                role="scan.l4.intel",
                subject=code,
                parent_task=parent,
            )
        )
        status_dependencies = [f"{prefix}.intel"]
        status_inputs = ["scan.l4.source.bundle", ids["intel"]]
        status_operation = "scan.l4.intel.status"
    else:
        status_dependencies = ["scan.l4.prepare"]
        status_inputs = ["scan.l4.source.bundle", ids["prompt"]]
        status_operation = "scan.l4.intel.disabled"
    tasks.append(
        _task(
            f"{prefix}.intel_status",
            "DETERMINISTIC",
            dependencies=status_dependencies,
            inputs=status_inputs,
            outputs=[ids["intel_status"], ids["intel_bundle"]],
            contract="scan.l4.intel_status.v1",
            operation=status_operation,
            subject=code,
            parent_task=parent,
        )
    )
    tasks.append(
        _task(
            f"{prefix}.card",
            "INFERENCE",
            dependencies=[f"{prefix}.slim", f"{prefix}.intel_status"],
            inputs=[ids["prompt"], ids["slim"], ids["intel_status"]],
            outputs=[ids["card"]],
            contract="stock.lite.v1",
            role="scan.l4.card",
            subject=code,
            parent_task=parent,
        )
    )
    return _expansion(plan, "scan.l4", snapshots, tasks)


def l4_expansion(
    plan: dict,
    mode: dict,
    finalists: list[dict],
    snapshots: list[dict],
    *,
    intel_enabled: bool,
) -> dict:
    actual = mode.get("mode")
    if actual not in run_mode.MODES:
        raise ValueError("invalid frozen scan run mode")
    if actual == run_mode.SENTINEL_EMPTY:
        return _expansion(
            plan,
            "scan.l4",
            snapshots,
            [
                _task(
                    "scan.l4.skip",
                    "DETERMINISTIC",
                    dependencies=["scan.gate2"],
                    inputs=[
                        "scan.run_mode",
                        "scan.gate2.result",
                        "scan.l3.final.bundle",
                    ],
                    outputs=[
                        "scan.l4.plan",
                        "scan.review.plan",
                        "scan.l4.source.bundle",
                    ],
                    contract="scan.l4.plan.v1",
                    operation="scan.l4.skip",
                )
            ],
        )
    rows = []
    for row in finalists:
        code = str(row.get("code") or "").split(".")[0].zfill(6)
        if not code.isdigit() or len(code) != 6:
            raise ValueError(f"invalid finalist code: {row.get('code')}")
        rows.append({**row, "code": code})
    if not rows:
        raise ValueError("non-empty L4 finalists required outside SENTINEL_EMPTY")

    prompt_ids = [_l4_ids(row["code"])["prompt"] for row in rows]
    tasks = [
        _task(
            "scan.l4.prepare",
            "DETERMINISTIC",
            dependencies=["scan.gate2"],
            inputs=[
                "scan.finalists",
                "scan.gate2.result",
                "scan.run_mode",
                "scan.l3.final.bundle",
            ],
            outputs=[
                "scan.l4.plan",
                "scan.l4.source.bundle",
                *prompt_ids,
            ],
            contract="scan.l4.plan.v1",
            operation="scan.l4.prepare",
        )
    ]
    card_tasks = []
    for row in rows:
        code = row["code"]
        ids = _l4_ids(code)
        parent = {"owner": "L4_TASKBOOK", "subject": code, "attempt": 1}
        tasks.append(
            _task(
                f"l4.{code}.a1",
                "DETERMINISTIC",
                dependencies=["scan.l4.prepare"],
                # Taskbook = owner state, not an artifact (see l4_retry_expansion).
                inputs=["scan.l4.source.bundle", ids["prompt"]],
                outputs=[ids["ticket"]],
                contract="scan.l4.ticket.v1",
                operation="scan.l4.ticket",
                subject=code,
                owner="L4_TASKBOOK",
            )
        )
        tasks.append(
            _task(
                f"l4.{code}.a1.slim",
                "DETERMINISTIC",
                dependencies=["scan.l4.prepare"],
                inputs=["scan.l4.source.bundle", ids["prompt"]],
                outputs=[ids["slim"]],
                contract="stock.harvest.slim.v1",
                operation="scan.l4.slim",
                subject=code,
                parent_task=parent,
            )
        )
        if intel_enabled:
            tasks.append(
                _task(
                    f"l4.{code}.a1.intel",
                    "INFERENCE",
                    dependencies=["scan.l4.prepare"],
                    inputs=[ids["prompt"]],
                    outputs=[ids["intel"]],
                    contract="scan.l4.intel.v1",
                    role="scan.l4.intel",
                    subject=code,
                    parent_task=parent,
                )
            )
            status_dependencies = [f"l4.{code}.a1.intel"]
            status_inputs = ["scan.l4.source.bundle", ids["intel"]]
            status_operation = "scan.l4.intel.status"
        else:
            status_dependencies = ["scan.l4.prepare"]
            status_inputs = ["scan.l4.source.bundle", ids["prompt"]]
            status_operation = "scan.l4.intel.disabled"
        tasks.append(
            _task(
                f"l4.{code}.a1.intel_status",
                "DETERMINISTIC",
                dependencies=status_dependencies,
                inputs=status_inputs,
                outputs=[ids["intel_status"], ids["intel_bundle"]],
                contract="scan.l4.intel_status.v1",
                operation=status_operation,
                subject=code,
                parent_task=parent,
            )
        )
        card_task = _task(
            f"l4.{code}.a1.card",
            "INFERENCE",
            dependencies=[f"l4.{code}.a1.slim", f"l4.{code}.a1.intel_status"],
            inputs=[ids["prompt"], ids["slim"], ids["intel_status"]],
            outputs=[ids["card"]],
            contract="stock.lite.v1",
            role="scan.l4.card",
            subject=code,
            parent_task=parent,
        )
        tasks.append(card_task)
        card_tasks.append(card_task)
    tasks.append(
        _task(
            "scan.review.plan",
            "DETERMINISTIC",
            dependencies=[task["task_id"] for task in card_tasks],
            inputs=[
                "scan.finalists",
                "scan.l4.source.bundle",
                *[_l4_ids(row["code"])["card"] for row in rows],
            ],
            outputs=["scan.review.plan"],
            contract="scan.review.plan.v1",
            operation="scan.review.plan",
        )
    )
    return _expansion(plan, "scan.l4", snapshots, tasks)


def _review_ids(code: str, attempt: int = 1) -> dict[str, str]:
    prefix = f"scan.l4.{code}.a{attempt}"
    return {
        "review2": f"{prefix}.review2",
        "review3": f"{prefix}.review3",
        "none": f"{prefix}.review_none",
        "ticket": f"{prefix}.ticket",
    }


def review_expansion(plan: dict, review_plan: dict, snapshots: list[dict]) -> dict:
    """Expand the first independent review without exposing another review's result."""
    tasks = []
    dependencies = []
    decision_inputs = ["scan.review.plan"]
    for row in review_plan.get("reviews") or []:
        code = str(row.get("code") or "").zfill(6)
        if not code.isdigit() or len(code) != 6:
            raise ValueError(f"invalid review code: {row.get('code')}")
        trigger = row.get("trigger")
        attempt = int(row.get("attempt") or 1)
        parent = {"owner": "L4_TASKBOOK", "subject": code, "attempt": attempt}
        ids = _review_ids(code, attempt)
        if trigger in {"ow_review", "sell_review"}:
            task_id = f"l4.{code}.a{attempt}.review2"
            tasks.append(
                _task(
                    task_id,
                    "INFERENCE",
                    dependencies=["scan.review.plan"],
                    inputs=[_l4_ids(code, attempt)["prompt"]],
                    outputs=[ids["review2"]],
                    contract="stock.lite.v1",
                    role="scan.l4.review",
                    subject=code,
                    independent=True,
                    parent_task=parent,
                )
            )
            decision_inputs.append(ids["review2"])
        elif trigger is None:
            task_id = f"scan.review.none.{code}"
            tasks.append(
                _task(
                    task_id,
                    "DETERMINISTIC",
                    dependencies=["scan.review.plan"],
                    inputs=["scan.review.plan", "scan.l4.source.bundle"],
                    outputs=[ids["none"]],
                    contract="scan.review.none.v1",
                    operation="scan.review.none",
                    subject=code,
                    parent_task=parent,
                )
            )
            decision_inputs.append(ids["none"])
        else:
            raise ValueError(f"invalid review trigger: {trigger}")
        dependencies.append(task_id)
    if not tasks:
        return _expansion(
            plan,
            "scan.reviews",
            snapshots,
            [
                _task(
                    "scan.reviews.skip",
                    "DETERMINISTIC",
                    dependencies=["scan.l4.skip"],
                    inputs=["scan.review.plan"],
                    outputs=["scan.review.decision"],
                    contract="scan.review.decision.v1",
                    operation="scan.review.skip",
                )
            ],
        )
    tasks.append(
        _task(
            "scan.review.decide",
            "DETERMINISTIC",
            dependencies=dependencies,
            inputs=decision_inputs,
            outputs=["scan.review.decision"],
            contract="scan.review.decision.v1",
            operation="scan.review.decide",
        )
    )
    return _expansion(plan, "scan.reviews", snapshots, tasks)


def review3_expansion(plan: dict, decision: dict, snapshots: list[dict]) -> dict:
    """Expand only mathematically necessary third reviews, then finalize each ticket."""
    tasks = []
    for row in decision.get("decisions") or []:
        code = str(row.get("code") or "").zfill(6)
        if not code.isdigit() or len(code) != 6:
            raise ValueError(f"invalid review decision code: {row.get('code')}")
        trigger = row.get("trigger")
        attempt = int(row.get("attempt") or 1)
        parent = {"owner": "L4_TASKBOOK", "subject": code, "attempt": attempt}
        ids = _review_ids(code, attempt)
        l4_ids = _l4_ids(code, attempt)
        inputs = [
            l4_ids["card"],
            l4_ids["prompt"],
            l4_ids["slim"],
            l4_ids["intel_status"],
            l4_ids["intel_bundle"],
            "scan.review.decision",
            "scan.l4.source.bundle",
        ]
        if trigger in {"ow_review", "sell_review"}:
            inputs.append(ids["review2"])
        if trigger is not None and row.get("same_tier") is False:
            review3_id = f"l4.{code}.a{attempt}.review3"
            tasks.append(
                _task(
                    review3_id,
                    "INFERENCE",
                    dependencies=["scan.review.decide"],
                    inputs=[_l4_ids(code, attempt)["prompt"]],
                    outputs=[ids["review3"]],
                    contract="stock.lite.v1",
                    role="scan.l4.review",
                    subject=code,
                    independent=True,
                    parent_task=parent,
                )
            )
            dependencies = [review3_id]
            inputs.append(ids["review3"])
        else:
            dependencies = ["scan.review.decide"]
        tasks.append(
            _task(
                f"l4.{code}.a{attempt}.finalize",
                "DETERMINISTIC",
                dependencies=dependencies,
                inputs=inputs,
                outputs=[ids["ticket"]],
                contract="scan.l4.ticket.v1",
                operation="scan.l4.finalize",
                subject=code,
                parent_task=parent,
            )
        )
    if not tasks:
        tasks.append(
            _task(
                "scan.review3.skip",
                "DETERMINISTIC",
                dependencies=["scan.reviews.skip"],
                inputs=[
                    "scan.review.decision",
                    "scan.l4.source.bundle",
                    "scan.l3.final.bundle",
                ],
                outputs=["scan.l4.complete", "scan.l4.final.bundle"],
                contract="scan.l4.complete.v1",
                operation="scan.review3.skip",
            )
        )
        complete_dependency = "scan.review3.skip"
    else:
        finalizers = [
            task["task_id"] for task in tasks if task.get("operation") == "scan.l4.finalize"
        ]
        tasks.append(
            _task(
                "scan.l4.complete",
                "DETERMINISTIC",
                dependencies=finalizers,
                inputs=[
                    "scan.l4.source.bundle",
                    "scan.l3.final.bundle",
                    "scan.review.decision",
                    *[
                        _review_ids(
                            str(row["code"]).zfill(6),
                            int(row.get("attempt") or 1),
                        )["ticket"]
                        for row in decision["decisions"]
                    ],
                ],
                outputs=["scan.l4.complete", "scan.l4.final.bundle"],
                contract="scan.l4.complete.v1",
                operation="scan.l4.complete",
            )
        )
        complete_dependency = "scan.l4.complete"
    tasks.extend(_l5_tasks(complete_dependency))
    return _expansion(plan, "scan.review3", snapshots, tasks)


def _l5_tasks(complete_dependency: str) -> list[dict]:
    return [
        _task(
            "scan.assemble",
            "DETERMINISTIC",
            dependencies=[complete_dependency],
            inputs=[
                "scan.l4.complete",
                "scan.l4.final.bundle",
                "scan.run_mode",
                "scan.finalists",
                "scan.market.view",
            ],
            outputs=["scan.report.plan", "scan.report.build.bundle"],
            contract="scan.report.plan.v1",
            operation="scan.assemble",
        ),
        _task(
            "scan.gate4",
            "DETERMINISTIC",
            dependencies=["scan.assemble"],
            inputs=["scan.report.plan", "scan.report.build.bundle"],
            outputs=["scan.gate4.result"],
            contract="scan.gate4.v1",
            operation="scan.gate4",
        ),
        _task(
            "scan.usage",
            "DETERMINISTIC",
            dependencies=["scan.gate4"],
            inputs=[
                "scan.report.plan",
                "scan.report.build.bundle",
                "scan.gate4.result",
            ],
            outputs=[
                "scan.token.usage",
                "scan.usage.reconcile",
                "scan.report.used.bundle",
            ],
            contract="scan.usage.v1",
            operation="scan.usage",
        ),
        _task(
            "scan.observe",
            "DETERMINISTIC",
            dependencies=["scan.usage"],
            inputs=[
                "scan.report.plan",
                "scan.report.build.bundle",
                "scan.report.used.bundle",
                "scan.gate4.result",
                "scan.token.usage",
                "scan.usage.reconcile",
            ],
            outputs=[
                "scan.publication.bundle",
                "scan.pool.candidate",
                "scan.report.brief",
                "scan.report.summary",
                "scan.report.appendix",
                "scan.report.manifest",
                "scan.progress.final",
            ],
            contract="scan.publication.v1",
            operation="scan.observe",
        ),
    ]


def ensemble_record(
    code: str,
    rating: str,
    review2_rating: str | None,
    review3_rating: str | None,
    *,
    trigger: str,
    review3_dispatched: bool,
) -> dict:
    """Build the legacy ensemble record from observed dispatch and result facts."""
    from autoresearch.agents.utils.rating import RATINGS_5_TIER

    rank = {name: index for index, name in enumerate(reversed(RATINGS_5_TIER))}
    ratings = [item for item in (rating, review2_rating, review3_rating) if item]
    if any(item not in rank for item in ratings):
        raise ValueError("ensemble contains an unknown rating")
    if review2_rating is None:
        raise ValueError("ensemble review2 result required")
    same_tier = rank[rating] == rank[review2_rating]
    early_stopped = same_tier and not review3_dispatched
    degraded = bool(review3_dispatched and review3_rating is None)
    tiers = sorted(rank[item] for item in ratings)
    names = {value: key for key, value in rank.items()}
    return {
        "code": str(code).zfill(6),
        "ratings": ratings,
        "median": names[tiers[len(tiers) // 2]],
        "spread": tiers[-1] - tiers[0],
        "degraded": degraded,
        "trigger": trigger,
        "n_runs": len(ratings),
        "early_stopped": early_stopped,
        "role": "ens_review",
        "n_dispatch": 2 if review3_dispatched else 1,
    }


def _load_artifact_json(handle, artifact_id: str) -> dict:
    with artifacts.open_artifact(handle, artifact_id) as stream:
        value = json.loads(stream.read().decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {artifact_id}")
    return value


def _expansion_snapshot(handle, artifact_id: str) -> dict:
    value = artifacts.snapshot_artifact(handle, artifact_id)
    return {"artifact_id": value["artifact_id"], "sha256": value["sha256"]}


def inapplicable_templates(handle) -> frozenset[str]:
    """Sentinel modes skip L3 entirely, so the L3-repair template (expanded only after
    ``scan.l3.lint``) can never expand; everything else still must."""
    try:
        mode = _load_artifact_json(handle, "scan.run_mode")
    except (KeyError, ValueError, RuntimeError, OSError):
        return frozenset()
    if mode.get("mode") in {run_mode.SENTINEL_EMPTY, run_mode.SENTINEL_PINNED}:
        return frozenset({"scan.l3.repair"})
    return frozenset()


def expansions_after_task(request: dict, handle, plan: dict, task: dict) -> list[dict]:
    del request
    if task["task_id"] == "scan.gate1":
        mode = _load_artifact_json(handle, "scan.run_mode")
        return [
            sector_expansion(
                plan,
                mode,
                _expansion_snapshot(handle, "scan.run_mode"),
            )
        ]
    if task["task_id"] in {"scan.sector.prepare", "scan.sector.skip"}:
        mode = _load_artifact_json(handle, "scan.run_mode")
        sector_plan = _load_artifact_json(handle, "scan.sector.list")
        return [
            l3_expansion(
                plan,
                mode,
                list(sector_plan.get("sectors") or []),
                [
                    _expansion_snapshot(handle, "scan.run_mode"),
                    _expansion_snapshot(handle, "scan.sector.list"),
                ],
            )
        ]
    if task["task_id"] == "scan.l3.lint":
        validation = _load_artifact_json(handle, "scan.l3.validation")
        return [
            l3_repair_expansion(
                plan,
                validation,
                [_expansion_snapshot(handle, "scan.l3.validation")],
            )
        ]
    if task["task_id"] == "scan.gate2":
        import pandas as pd

        mode = _load_artifact_json(handle, "scan.run_mode")
        with artifacts.open_artifact(handle, "scan.finalists") as stream:
            finalists = pd.read_csv(stream, dtype={"code": str}).to_dict("records")
        config = getattr(handle.contract, "user_config", {}) or {}
        intel_enabled = bool((config.get("l4_intel") or {}).get("enabled"))
        return [
            l4_expansion(
                plan,
                mode,
                finalists,
                [
                    _expansion_snapshot(handle, "scan.finalists"),
                    _expansion_snapshot(handle, "scan.gate2.result"),
                ],
                intel_enabled=intel_enabled,
            )
        ]
    if task["task_id"] in {"scan.review.plan", "scan.l4.skip"}:
        review_plan = _load_artifact_json(handle, "scan.review.plan")
        return [
            review_expansion(
                plan,
                review_plan,
                [_expansion_snapshot(handle, "scan.review.plan")],
            )
        ]
    if task["task_id"] in {"scan.review.decide", "scan.reviews.skip"}:
        decision = _load_artifact_json(handle, "scan.review.decision")
        return [
            review3_expansion(
                plan,
                decision,
                [_expansion_snapshot(handle, "scan.review.decision")],
            )
        ]
    return []


def _paths_for_artifact(handle, task: dict, artifact_id: str) -> tuple[Path, str]:
    staging = Path(handle.staging)
    if artifact_id.startswith("scan.sector.") and artifact_id.endswith(".pack"):
        key = artifact_id.removeprefix("scan.sector.").removesuffix(".pack")
        return staging / "session_inputs/sectors" / f"{key}.json", "READ"
    if artifact_id.startswith("scan.sector.") and artifact_id.endswith(".brief"):
        from autoresearch.sector.pack import _safe

        industry = task.get("subject")
        if industry is None:
            key = artifact_id.removeprefix("scan.sector.").removesuffix(".brief")
            sector_plan = _load_artifact_json(handle, "scan.sector.list")
            matches = [row for row in sector_plan.get("sectors") or [] if row.get("key") == key]
            if len(matches) != 1:
                raise KeyError(f"sector brief key is not in frozen sector list: {key}")
            industry = matches[0]["industry"]
        return staging / "sector_briefs" / f"{_safe(industry)}.md", (
            "WRITE" if artifact_id in task["output_artifact_ids"] else "READ"
        )
    match = re.fullmatch(
        r"scan\.l4\.(\d{6})\.a(\d+)\."
        r"(prompt|slim|intel|intel_status|intel_bundle|card|review2|review3|review_none|ticket)",
        artifact_id,
    )
    if match:
        from autoresearch.dataflows.symbol_utils import normalize_symbol

        code, attempt_text, kind = match.groups()
        attempt = int(attempt_text)
        if attempt == 1:
            paths = {
                "prompt": staging / f"_l4_prompt_{code}.md",
                "slim": staging
                / "_external_inputs"
                / f"{normalize_symbol(code)}_{handle.analysis_date}_slim.md",
                "intel": staging / f"_l4_intel_{code}.md",
                "intel_status": staging / f"_l4_intel_status_{code}.json",
                "intel_bundle": staging
                / "session_outputs"
                / "intel_bundles"
                / f"{code}.a1.json",
                "card": staging / "details" / f"{code}.md",
                "review2": staging / "ensemble" / f"{code}.run2.md",
                "review3": staging / "ensemble" / f"{code}.run3.md",
                "review_none": staging / "session_outputs/reviews" / f"{code}.none.json",
                "ticket": staging / "session_outputs/tickets" / f"{code}.a1.json",
            }
        else:
            retry = staging / "session_attempts" / code / f"a{attempt}"
            paths = {
                "prompt": staging / f"_l4_prompt_{code}.md",
                "slim": retry / "slim.md",
                "intel": retry / "intel.md",
                "intel_status": retry / "intel_status.json",
                "intel_bundle": retry / "intel_bundle.json",
                "card": retry / "card.md",
                "review2": retry / "review2.md",
                "review3": retry / "review3.md",
                "review_none": retry / "review_none.json",
                "ticket": staging / "session_outputs/tickets" / f"{code}.a{attempt}.json",
            }
        return paths[kind], "WRITE" if artifact_id in task["output_artifact_ids"] else "READ"
    mapping = {
        "scan.prelude.bundle": staging / "session_outputs/prelude.bundle.json",
        "scan.sector.source.bundle": staging / "session_outputs/sector.source.bundle.json",
        "scan.l3.source.bundle": staging / "session_outputs/l3.source.bundle.json",
        "scan.l3.context.bundle": staging / "session_outputs/l3.context.bundle.json",
        "scan.l3.final.bundle": staging / "session_outputs/l3.final.bundle.json",
        "scan.l4.source.bundle": staging / "session_outputs/l4.source.bundle.json",
        "scan.l4.final.bundle": staging / "session_outputs/l4.final.bundle.json",
        "scan.report.build.bundle": staging / "session_outputs/report.build.bundle.json",
        "scan.report.used.bundle": staging / "session_outputs/report.used.bundle.json",
        "scan.l3.table": staging / "_l3_table.md",
        "scan.l3.judged": staging / "_l3_judged.json",
        "scan.l3.effective.judged": staging / "_l3_effective_judged.json",
        "scan.l3.validation": staging / "session_outputs/l3.validation.json",
        "scan.l3.repair.prompt": staging / "_l3_repair_prompt.md",
        "scan.l3.repair.pack": staging / "_l3_repair_pack.json",
        "scan.l3.repair.patch": staging / "_l3_repair_patch.json",
        "scan.l3.repair.result": staging / "session_outputs/l3.repair.json",
        "scan.finalists": staging / "finalists.csv",
        "scan.l3.bench": staging / "_l3_bench.csv",
        "scan.gate2.result": staging / "session_outputs/gate2.json",
        "scan.l4.plan": staging / "session_outputs/l4.plan.json",
        "scan.l4.taskbook": staging / "_l4_tasks.json",
        "scan.review.plan": staging / "session_outputs/review.plan.json",
        "scan.review.decision": staging / "session_outputs/review.decision.json",
        "scan.l4.complete": staging / "session_outputs/l4.complete.json",
        "scan.report.plan": staging / "session_outputs/report.plan.json",
        "scan.gate4.result": staging / "session_outputs/gate4.json",
        "scan.token.usage": staging / "_token_usage.json",
        "scan.usage.reconcile": staging / "_usage_reconcile.json",
        "scan.publication.bundle": staging / "session_outputs/scan.publication.json",
        "scan.pool.candidate": staging / "session_outputs/scan.pool.candidate.json",
        "scan.report.brief": staging / "session_outputs/report_files/brief.md",
        "scan.report.summary": staging / "session_outputs/report_files/summary.md",
        "scan.report.appendix": staging / "session_outputs/report_files/appendix.md",
        "scan.report.manifest": staging / "session_outputs/report_files/manifest.json",
        "scan.progress.final": staging / "session_outputs/progress.final.json",
    }
    if artifact_id not in mapping:
        raise KeyError(f"scan artifact path is not registered: {artifact_id}")
    return mapping[artifact_id], "WRITE"


def register_scan_artifacts(request: dict, handle, plan: dict) -> None:
    del request, plan
    staging = Path(handle.staging)
    mapping = {
        "scan.market.pack": staging / "market_pack.json",
        "scan.strategist.pack": staging / "strategist_pack.json",
        "scan.market.view": staging / "market_view.md",
        "scan.prelude.summary": staging / "_prelude_summary.md",
        "scan.l2": staging / "L2_gbdt_top200.csv",
        "scan.prelude.bundle": staging / "session_outputs/prelude.bundle.json",
        "scan.gate1.result": staging / "session_outputs/gate1.json",
        "scan.run_mode": staging / "run_mode.json",
        "scan.sector.list": staging / "session_outputs/sector.list.json",
    }
    for artifact_id, path in mapping.items():
        artifacts.register_artifact(handle, artifact_id, path, "WRITE")


def register_scan_expansion_artifacts(request: dict, handle, expansion: dict) -> None:
    del request
    produced = {
        artifact_id for task in expansion["tasks"] for artifact_id in task["output_artifact_ids"]
    }
    seen = set()
    for task in expansion["tasks"]:
        for artifact_id in [
            *task["input_artifact_ids"],
            *task["output_artifact_ids"],
        ]:
            if artifact_id in seen or artifact_id in {
                "scan.run_mode",
                "scan.gate1.result",
                "scan.market.view",
                "scan.market.pack",
                "scan.sector.list",
                "scan.l4.taskbook",
            }:
                continue
            seen.add(artifact_id)
            path, access = _paths_for_artifact(handle, task, artifact_id)
            if artifact_id in produced:
                access = "WRITE"
            artifacts.register_artifact(handle, artifact_id, path, access)


def validate_scan_operation_params(request: dict, task: dict, params: dict) -> None:
    del request, task
    if params != {}:
        raise ValueError("scan deterministic operations take no model parameters")


def directory_manifest(root: Path | str) -> dict[str, str]:
    base = Path(root)
    if not base.is_dir():
        raise ValueError("scan report candidate is missing")
    return {
        path.relative_to(base).as_posix(): sha256_bytes(path.read_bytes())
        for path in sorted(base.rglob("*"))
        if path.is_file()
    }


@contextmanager
def _publish_lock(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".session-agent.lock").open("a+") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _publish_scan_active(handle, *, reports_root: Path | str | None = None) -> Path:
    """Publish the entire verified legacy report bundle after the task graph is done."""
    with artifacts.open_artifact(handle, "scan.publication.bundle") as stream:
        bundle = json.loads(stream.read().decode("utf-8"))
    if bundle.get("run_id") != handle.run_id or bundle.get("engine") != handle.engine:
        raise RuntimeError("scan publication identity mismatch")
    workspace = Path(handle.workspace).resolve(strict=True)
    candidate = (workspace / str(bundle["candidate_relative"])).resolve(strict=True)
    try:
        candidate.relative_to(workspace)
    except ValueError as exc:
        raise RuntimeError("scan publication candidate escaped its run") from exc
    expected = bundle.get("files")
    if expected != directory_manifest(candidate):
        raise RuntimeError("scan report candidate changed after observation")
    root = Path(reports_root) if reports_root is not None else ws.run_reports_root("scan-market")
    target = root / str(bundle["folder"])
    with _publish_lock(root):
        if target.is_dir():
            if directory_manifest(target) != expected:
                raise RuntimeError("scan report publication conflict")
        else:
            temp = root / f".{target.name}.{handle.run_id}.tmp"
            shutil.rmtree(temp, ignore_errors=True)
            shutil.copytree(candidate, temp)
            temp.replace(target)
    if bundle.get("pool_mutation"):
        from autoresearch.dossier import pool as dossier_pool

        pool_candidate = Path(handle.staging) / "session_outputs/scan.pool.candidate.json"
        payload = pool_candidate.read_bytes()
        if sha256_bytes(payload) != bundle.get("pool_after_sha256"):
            raise RuntimeError("scan pool candidate changed after publication preparation")
        pool_target = Path(dossier_pool.POOL_PATH)
        with _publish_lock(pool_target.parent):
            current_hash = sha256_bytes(pool_target.read_bytes()) if pool_target.is_file() else None
            if current_hash not in {
                bundle.get("pool_before_sha256"),
                bundle.get("pool_after_sha256"),
            }:
                raise RuntimeError("CONFLICT: coverage pool changed after scan began")
            if current_hash != bundle.get("pool_after_sha256"):
                atomic_write_bytes(pool_target, payload)
    return target


def publish_scan(handle, *, reports_root: Path | str | None = None) -> Path:
    """Publish only while the bound scan run still owns its write window."""
    from autoresearch.trace.write_guard import assert_output_path, guarded_handle_write

    with guarded_handle_write(handle, "scan.publish") as tracked:
        if tracked is not None:
            root = (
                Path(reports_root)
                if reports_root is not None
                else ws.run_reports_root("scan-market")
            )
            assert_output_path(root, ws.run_reports_root("scan-market"))
            with artifacts.open_artifact(handle, "scan.publication.bundle") as stream:
                bundle = json.loads(stream.read().decode("utf-8"))
            assert_output_path(root / str(bundle["folder"]), root)
        return _publish_scan_active(handle, reports_root=reports_root)


def prepare_scan_bundle(handle) -> dict:
    """Describe every verified report member without copying it to a public path."""
    with artifacts.open_artifact(handle, "scan.publication.bundle") as stream:
        bundle = json.loads(stream.read().decode("utf-8"))
    workspace = Path(handle.workspace).resolve(strict=True)
    candidate = (workspace / str(bundle["candidate_relative"])).resolve(strict=True)
    candidate.relative_to(workspace)
    if directory_manifest(candidate) != bundle["files"]:
        raise RuntimeError("scan report candidate changed after observation")
    files = []
    inline = {}
    for relative, digest in sorted(bundle["files"].items()):
        artifact_id = f"scan.report.{digest[:24]}"
        if artifact_id in inline:
            artifact_id = f"{artifact_id}.{sha256_bytes(relative.encode())[:8]}"
        source = candidate / relative
        inline[artifact_id] = source
        files.append(
            {
                "artifact_id": artifact_id,
                "relative_path": f"report/{relative}",
                "media_type": "application/json" if relative.endswith(".json") else "text/markdown",
            }
        )
    mutations = []
    if bundle.get("pool_mutation"):
        mutations.append(
            {
                "target_key": "dossier.coverage_pool",
                "expected_before_hash": bundle.get("pool_before_sha256"),
                "after_artifact_id": "scan.pool.candidate",
                "apply_policy": "CAS_REPLACE",
            }
        )
    return {
        "business_files": files,
        "state_mutations": mutations,
        "inline_artifacts": inline,
    }


__all__ = [
    "build_scan_plan",
    "l3_expansion",
    "l3_repair_expansion",
    "l4_expansion",
    "l4_retry_expansion",
    "review3_expansion",
    "review_expansion",
    "ensemble_record",
    "directory_manifest",
    "publish_scan",
    "prepare_scan_bundle",
    "expansions_after_task",
    "inapplicable_templates",
    "register_scan_artifacts",
    "register_scan_expansion_artifacts",
    "sector_artifact_ids",
    "sector_expansion",
    "validate_scan_operation_params",
]
