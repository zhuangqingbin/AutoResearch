from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from autoresearch.common.atomic import atomic_write_json, sha256_bytes
from autoresearch.contracts.session_plan import validate_plan
from autoresearch.session_agent import artifacts, store
from autoresearch.session_agent.plan import ready_tasks
from autoresearch.session_agent.validation import (
    DomainValidationError,
    validate_registered_contract,
    validate_submission_outputs,
)
from autoresearch.session_agent.workflows.macro import (
    build_macro_plan,
    macro_product_artifacts,
    required_macro_products,
)
from autoresearch.session_agent.workflows.macro_groups import (
    QUALITY_CHECKS,
    compare_macro_candidate,
)

from .test_macro import _context, _cross_asset_rows, _request

OPTIONALS = ["4_crossasset/credit.md", "6_meso_evidence/industry_cycle.md", "1_spine/debate.md"]
RAW = {"research.frame", "macro.data", "macro.global_tape", "macro.scan_meta", "macro.intel"}


def request(profile="six_groups_v1", optional=()):
    return dict(
        _request(),
        schema_version=4,
        card_research_profile="single-stage-v1",
        research_context={"venue": "XSHG", "usage": "macro", "calendar_source_path": None},
        macro_research_profile=profile,
        macro_optional_products=list(optional),
        sector_brief_profile="legacy",
    )


def groups(plan):
    return [task for task in plan["tasks"] if task["role"] == "macro.research"]


@pytest.mark.parametrize("optional", [(), OPTIONALS])
def test_candidate_exact_products_independent_frontier_and_complete_upstream(tmp_path, optional):
    plan = build_macro_plan(request(optional=optional), _context(tmp_path))
    validate_plan(plan)
    research = groups(plan)
    assert len(research) == 6
    required = {
        macro_product_artifacts()[relative]
        for relative in required_macro_products() | set(optional)
    }
    outputs = [output for task in research for output in task["output_artifact_ids"]]
    assert len(outputs) == len(required)
    assert set(outputs) == required
    states = {task["task_id"]: "SUCCEEDED" for task in plan["tasks"][:3]}
    assert {task["task_id"] for task in ready_tasks(plan["tasks"], states)} == {
        task["task_id"] for task in research[:3]
    }
    for index, task in enumerate(research):
        upstream = research[:index] if index >= 3 else []
        assert set(task["input_artifact_ids"]) == RAW | {
            output for earlier in upstream for output in earlier["output_artifact_ids"]
        }
        expected_deps = [earlier["task_id"] for earlier in upstream] or ["macro.global_intel"]
        assert task["dependencies"] == expected_deps
    assert research[-1]["expected_output_contract"] == "macro.allocation.v1"
    assert all(task["expected_output_contract"] == "macro.section.v1" for task in research[:-1])
    validation = next(task for task in plan["tasks"] if task["task_id"] == "macro.full.validate")
    assert required <= set(validation["input_artifact_ids"])
    assembler = plan["tasks"][-1]
    assert required <= set(assembler["input_artifact_ids"])


def test_old_requests_default_to_serial21_and_opt_in_changes_frozen_plan_hash(tmp_path):
    handle = _context(tmp_path)
    baseline = build_macro_plan(_request(), handle)
    explicit = build_macro_plan(request("serial21"), handle)
    candidate = build_macro_plan(request(), handle)
    assert len(groups(baseline)) == 21
    assert baseline["tasks"] == explicit["tasks"]
    assert candidate["plan_hash"] != baseline["plan_hash"]
    assert len(groups(build_macro_plan(request("serial21", OPTIONALS), handle))) == 24
    assert set(groups(baseline)[-1]["input_artifact_ids"]) == RAW | {
        output for task in groups(baseline)[:-1] for output in task["output_artifact_ids"]
    }


@pytest.mark.parametrize(
    "change",
    [
        {"schema_version": 3},
        {"requested_mode": "LITE"},
        {"macro_research_profile": "experimental"},
        {"macro_optional_products": ["invented.md"]},
        {"macro_optional_products": [OPTIONALS[0], OPTIONALS[0]]},
    ],
)
def test_invalid_profile_never_builds_candidate(tmp_path, change):
    with pytest.raises(ValueError):
        build_macro_plan(dict(request(), **change), _context(tmp_path))


def run_setup(tmp_path):
    handle = _context(tmp_path)
    handle.staging.mkdir(parents=True)
    plan = build_macro_plan(request(optional=OPTIONALS), handle)
    atomic_write_json(
        tmp_path / "session/storage.json", {"schema_version": 1, "output_layout_version": 2}
    )
    owner = tmp_path / "session/tasks.json"
    store.initialize(owner, plan)
    for relative, key in macro_product_artifacts().items():
        artifacts.register_artifact(handle, key, handle.staging / relative, "WRITE")
    # Complete bootstrap through its own deterministic/accepted-state APIs.
    for task in plan["tasks"][:3]:
        store.claim(owner, task["task_id"], 1, "test")
        if task["kind"] == "DETERMINISTIC":
            for key in task["output_artifact_ids"]:
                artifacts.register_artifact(handle, key, handle.staging / f"{key}.json", "WRITE")
            paths = artifacts.output_paths(handle, task, 1)
            for key, path in paths.items():
                Path(path).write_text(
                    "**行业资金净流入(tushare)**:\n| 行业 | 主力净流入(亿) | 领涨股 |\n"
                    "|---|---:|---|\n| 电子 | 1 | 示例甲 |\n"
                    if key == "macro.data"
                    else "{}"
                )
            manifest = artifacts.capture_outputs(handle, task, 1)
            outputs = [
                {"artifact_id": key, "sha256": row["sha256"]} for key, row in manifest.items()
            ]
            store.complete_deterministic(
                owner,
                task["task_id"],
                1,
                outputs,
                {"status": "SUCCEEDED", "exit_code": 0},
                accepted_artifacts=manifest,
            )
        else:
            artifacts.register_artifact(
                handle, "macro.intel", handle.staging / "_global_intel.md", "WRITE"
            )
            for path in artifacts.output_paths(handle, task, 1).values():
                Path(path).write_text("bootstrap fixture")
            value = submission(handle, task, plan)
            store.accept(
                owner,
                value,
                lambda *_: None,
                prepare=lambda value, spec: artifacts.capture_outputs(
                    handle, spec, 1, expected=value["outputs"]
                ),
            )
    return handle, plan, owner


def submission(handle, task, plan, attempt=1):
    return {
        "schema_version": 1,
        "envelope": {
            "schema_version": 1,
            "engine": handle.engine,
            "run_id": handle.run_id,
            "task_id": task["task_id"],
            "role": task["role"],
            "input_artifact_ids": task["input_artifact_ids"],
            "input_contract_hash": plan["input_contract_hash"],
            "expected_output_contract": task["expected_output_contract"],
            "attempt": attempt,
        },
        "plan_hash": plan["plan_hash"],
        "host_receipt_id": None,
        "outputs": [
            {"artifact_id": key, "sha256": sha256_bytes(Path(path).read_bytes())}
            for key, path in artifacts.output_paths(handle, task, attempt).items()
        ],
    }


def accept(handle, owner, value):
    def prepare(submitted, task):
        manifest = artifacts.capture_outputs(
            handle, task, submitted["envelope"]["attempt"], expected=submitted["outputs"]
        )
        with artifacts.candidate_view(handle, manifest):
            validate_submission_outputs(
                handle,
                submitted,
                task,
                domain_validator=lambda value, spec: validate_registered_contract(
                    handle, value, spec
                ),
            )
        return manifest

    return store.accept(owner, value, lambda *_: None, prepare=prepare)


@pytest.mark.parametrize("defect", ["missing", "invalid", "conflicting_hash"])
def test_failed_group_accepts_nothing_and_only_failed_group_retries(tmp_path, defect):
    handle, plan, owner = run_setup(tmp_path)
    first, second, third = groups(plan)[:3]
    for task in (first, second, third):
        store.claim(owner, task["task_id"], 1, "test")
        for path in artifacts.output_paths(handle, task, 1).values():
            Path(path).write_text("evidence\n置信度: 中\n")
    for task in (first, third):
        accept(handle, owner, submission(handle, task, plan))
    preserved = {
        task["task_id"]: store.read_entry(owner, task["task_id"]) for task in (first, third)
    }
    failed_paths = artifacts.output_paths(handle, second, 1)
    bad = submission(handle, second, plan)
    if defect == "missing":
        bad["outputs"].pop()
    elif defect == "invalid":
        Path(list(failed_paths.values())[-1]).write_text("no confidence")
        bad = submission(handle, second, plan)
    else:
        bad["outputs"][-1]["sha256"] = "0" * 64
    with pytest.raises((DomainValidationError, store.TaskConflict, ValueError)):
        accept(handle, owner, bad)
    assert store.read_entry(owner, second["task_id"])["outputs"] == []
    for key in failed_paths:
        with pytest.raises(artifacts.ArtifactConflict):
            artifacts.artifact_path(handle, key)
    store.mark_failed(owner, second["task_id"], 1, {"code": "INVALID_OUTPUT"}, retryable=True)
    store.claim(owner, second["task_id"], 2, "test")
    for path in artifacts.output_paths(handle, second, 2).values():
        Path(path).write_text("repaired evidence\n置信度: 中\n")
    accept(handle, owner, submission(handle, second, plan, 2))
    for task_id, entry in preserved.items():
        assert store.read_entry(owner, task_id) == entry
    for path in failed_paths.values():
        Path(path).write_text("late stale output")
    for key in failed_paths:
        assert artifacts.read_bytes(handle, key) == "repaired evidence\n置信度: 中\n".encode()
    assert {task["task_id"] for task in ready_tasks(plan["tasks"], store.read_states(owner))} == {
        "macro.group.sinous"
    }


def comparison_kwargs():
    products = dict.fromkeys(required_macro_products(), "evidence\n置信度: 中")
    return {
        "engine": "codex",
        "baseline_run_id": "20260913T010203000000Z",
        "candidate_run_id": "20260913T020304000000Z",
        "input_identity_equal": True,
        "config_identity_equal": True,
        "baseline_products": products,
        "candidate_products": deepcopy(products),
        "quality_checks": {
            name: {"verdict": "PASS", "evidence_refs": [f"review/{name}.json"]}
            for name in QUALITY_CHECKS
        },
    }


def test_comparison_preserves_judgment_changes_and_quality_gate(tmp_path):
    kwargs = comparison_kwargs()
    key = "1_spine/decision.md"
    kwargs["baseline_products"][key] = "- 美股: **Rating**: Buy — 事实甲\n置信度: 中"
    kwargs["candidate_products"][key] = "- 美股: **Rating**: Hold — 事实乙\n置信度: 低"
    result = compare_macro_candidate(**kwargs)
    assert result["comparison"]["verdict"] == "PASS"
    assert result["comparison"]["research_diffs"] == [
        {
            "path": key,
            "baseline": kwargs["baseline_products"][key],
            "candidate": kwargs["candidate_products"][key],
        }
    ]
    kwargs["quality_checks"]["risk_coverage"]["verdict"] = "FAIL"
    assert compare_macro_candidate(**kwargs)["comparison"]["verdict"] == "FAIL"


@pytest.mark.parametrize(
    "missing", ["actual_measurement", "fact_references", "artifact", "optional"]
)
def test_comparison_cannot_pass_without_products_and_evidence(missing):
    kwargs = comparison_kwargs()
    if missing == "artifact":
        kwargs["candidate_products"].pop("1_spine/calendar.md")
    elif missing == "optional":
        kwargs["optional_products"] = OPTIONALS
    else:
        kwargs["quality_checks"].pop(missing)
    result = compare_macro_candidate(**kwargs)["comparison"]
    assert result["verdict"] == "INCOMPLETE"
    assert result["missing_evidence"]


def write_valid_group(handle, task, attempt=1):
    for key, path in artifacts.output_paths(handle, task, attempt).items():
        text = "evidence\n置信度: 中\n"
        if key.endswith(".decision"):
            text = _cross_asset_rows()
        elif key.endswith(".sector_map"):
            text = "- 电子: **Rating**: Hold\n置信度: 中\n"
        Path(path).write_text(text)


@pytest.mark.parametrize("group_index", range(6))
def test_every_group_missing_a_file_is_rejected_before_any_accept(tmp_path, group_index):
    handle, plan, owner = run_setup(tmp_path)
    for index, task in enumerate(groups(plan)):
        store.claim(owner, task["task_id"], 1, "test")
        write_valid_group(handle, task)
        value = submission(handle, task, plan)
        if index != group_index:
            accept(handle, owner, value)
            continue
        Path(list(artifacts.output_paths(handle, task, 1).values())[-1]).unlink()
        with pytest.raises((OSError, ValueError, RuntimeError)):
            accept(handle, owner, value)
        assert store.read_entry(owner, task["task_id"])["outputs"] == []
        for key in task["output_artifact_ids"]:
            with pytest.raises(artifacts.ArtifactConflict):
                artifacts.artifact_path(handle, key)
        return


@pytest.mark.parametrize("bad_output", ["decision", "sector_map"])
def test_decision_group_validates_both_allocation_tables_atomically(tmp_path, bad_output):
    handle, plan, owner = run_setup(tmp_path)
    for task in groups(plan):
        store.claim(owner, task["task_id"], 1, "test")
        write_valid_group(handle, task)
        if task["task_id"] != "macro.group.decision":
            accept(handle, owner, submission(handle, task, plan))
            continue
        paths = artifacts.output_paths(handle, task, 1)
        bad_key = next(key for key in paths if key.endswith("." + bad_output))
        path = Path(paths[bad_key])
        original = path.read_text()
        path.write_text(original.replace("Hold", "Maybe", 1))
        with pytest.raises(DomainValidationError, match=bad_key):
            accept(handle, owner, submission(handle, task, plan))
        for key in paths:
            with pytest.raises(artifacts.ArtifactConflict):
                artifacts.artifact_path(handle, key)
        store.mark_failed(owner, task["task_id"], 1, {"code": "INVALID_ALLOCATION"}, retryable=True)
        store.claim(owner, task["task_id"], 2, "test")
        write_valid_group(handle, task, 2)
        receipt = accept(handle, owner, submission(handle, task, plan, 2))
        assert receipt["status"] == "ACCEPTED"
        assert store.read_states(owner)[task["task_id"]] == "SUCCEEDED"


@pytest.mark.parametrize("identity", ["input_identity_equal", "config_identity_equal"])
def test_comparison_rejects_changed_frozen_inputs_or_common_config(identity):
    kwargs = comparison_kwargs()
    kwargs[identity] = False
    assert compare_macro_candidate(**kwargs)["comparison"]["verdict"] == "FAIL"


def test_v4_profile_and_industry_cycle_instruction_freezing(tmp_path):
    from autoresearch.contracts.session_task import validate_begin_request
    from autoresearch.session_agent.task_access import freeze_instructions

    assert (
        validate_begin_request(request(optional=OPTIONALS))["macro_research_profile"]
        == "six_groups_v1"
    )
    outputs = {"macro.full.6_meso_evidence.industry_cycle": str(tmp_path / "industry_cycle.md")}
    destination = tmp_path / "instructions"
    destination.mkdir()
    paths = freeze_instructions(
        "macro.research", [".claude/agents/macro-full.md"], destination, outputs
    )
    text = Path(paths[0]).read_text()
    assert "## meso" in text and "industry_cycle" in text
    assert "## regional" not in text


def test_full_validation_binds_selected_optional_product_hashes(tmp_path):
    from autoresearch.session_agent.domain_ops import macro_full_validate

    from .test_macro import _macro_data

    handle = _context(tmp_path)
    handle.staging.mkdir(parents=True)
    (tmp_path / "session").mkdir()
    (tmp_path / "session/request.json").write_text(
        __import__("json").dumps(request(optional=OPTIONALS))
    )
    root = handle.staging / "macro/2026-09-13"
    _macro_data(handle, root)
    for relative, key in macro_product_artifacts().items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        text = (
            _cross_asset_rows()
            if relative.endswith("/decision.md")
            else (
                "- 电子: **Rating**: Hold\n置信度: 中\n"
                if relative.endswith("/sector_map.md")
                else "evidence\n置信度: 中\n"
            )
        )
        path.write_text(text)
        artifacts.register_artifact(handle, key, path, "WRITE")
        artifacts.bind_artifact_hash(handle, key)
    artifacts.register_artifact(
        handle,
        "macro.full.validation",
        handle.staging / "session_outputs/macro.full.validation.json",
        "WRITE",
    )
    result = macro_full_validate(handle)
    assert set(result["required_products"]) == required_macro_products()
    assert set(result["hashes"]) == required_macro_products() | set(OPTIONALS)
