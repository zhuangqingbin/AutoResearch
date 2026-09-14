from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from autoresearch.common.atomic import canonical_json, sha256_bytes
from autoresearch.contracts.forensic import evidence_plan_hash
from autoresearch.session_agent import artifacts
from autoresearch.session_agent.domain_ops import (
    collect_dossier_skeleton_snapshot,
    collect_sector_snapshot,
    render_dossier_publication_snapshot,
    render_dossier_skeleton_snapshot,
    render_sector_snapshot,
)
from autoresearch.session_agent.publication import replayable_plan_operations
from autoresearch.session_agent.replay_adapters import DomainReplayRunner
from autoresearch.session_agent.replay_adapters.common import safe_name
from autoresearch.session_agent.replay_registry import build_replay_plan
from autoresearch.session_agent.workflows.dossier import build_dossier_plan
from autoresearch.session_agent.workflows.sector import build_sector_plan
from autoresearch.trace.offline import strict_isolation_available
from autoresearch.trace.replay import REPLAY_ENV, execute_replay
from autoresearch.trace.source_receipts import record_response
from autoresearch.trace.source_tree import build_runtime_manifest, capture_source_tree


def _handle(tmp_path: Path, request: dict):
    workspace = tmp_path / "run"
    staging = workspace / "staging"
    (workspace / "session").mkdir(parents=True)
    (workspace / "session/request.json").write_text(
        json.dumps(request, ensure_ascii=False), encoding="utf-8"
    )
    return SimpleNamespace(
        run_id="20260914T040102000000Z",
        engine="codex",
        analysis_date=request["analysis_date"],
        workspace=workspace,
        staging=staging,
        contract=SimpleNamespace(
            contract_hash="a" * 64,
            config_hash="b" * 64,
            user_config={},
        ),
    )


def _request(kind: str, mode: str, subject: str) -> dict:
    return {
        "schema_version": 1,
        "kind": kind,
        "requested_mode": mode,
        "analysis_date": "2026-09-14",
        "subject": subject,
        "peers": [],
        "asset_type": None,
        "name": "贵州茅台" if kind == "dossier-init" else None,
        "force_full": False,
        "host_profile": {},
        "predecessor_run_id": None,
    }


def _scan_day(root: Path, day: str, *, momentum: float, brief: str | None = None) -> Path:
    target = root / day
    target.mkdir(parents=True)
    pd.DataFrame(
        {
            "code": ["600519"],
            "name": ["贵州茅台"],
            "industry": ["电子"],
            "pct_60d": [momentum],
            "pe": [20.0],
            "pb": [2.0],
            "main_net_ratio": [0.1],
            "mktcap_yi": [100.0],
        }
    ).to_csv(target / "L1_scored_full.csv", index=False)
    pd.DataFrame({"code": ["600519"], "industry": ["电子"]}).to_csv(
        target / "L2_gbdt_top200.csv", index=False
    )
    (target / "meta.json").write_text(json.dumps({"regime": "range"}))
    if brief is not None:
        brief_path = target / "sector_briefs/电子.md"
        brief_path.parent.mkdir()
        brief_path.write_text(brief, encoding="utf-8")
    return target


def test_sector_snapshot_replays_exact_source_after_live_tree_changes(tmp_path):
    request = _request("sector-research", "FULL", "电子")
    handle = _handle(tmp_path, request)
    scan_root = tmp_path / "scan"
    today = _scan_day(scan_root, "2026-09-14", momentum=9.0)

    snapshot = collect_sector_snapshot(handle, scan_root=scan_root)
    first = render_sector_snapshot(snapshot, staging_root=tmp_path / "first")
    (today / "L1_scored_full.csv").write_text("code,industry\n000001,煤炭\n")
    second = render_sector_snapshot(snapshot, staging_root=tmp_path / "second")

    assert first["manifest"].read_bytes() == second["manifest"].read_bytes()
    assert first["pack"].read_bytes() == second["pack"].read_bytes()
    assert json.loads(first["pack"].read_text())["n_market"] == 1


def test_sector_reuse_hit_and_invalidated_source_are_frozen(tmp_path):
    request = _request("sector-research", "LITE", "电子")
    handle = _handle(tmp_path, request)
    scan_root = tmp_path / "scan"
    old = _scan_day(
        scan_root,
        "2026-09-12",
        momentum=8.0,
        brief="# 行业 brief\n\n## 地形段(描述性)\n- 成分 1 只\n",
    )
    _scan_day(scan_root, "2026-09-14", momentum=9.0)

    hit = collect_sector_snapshot(handle, scan_root=scan_root)
    assert hit["reuse"]["reused"] is True
    (old / "meta.json").write_text(json.dumps({"regime": "risk_off"}))
    invalid = collect_sector_snapshot(handle, scan_root=scan_root)

    rendered_hit = render_sector_snapshot(hit, staging_root=tmp_path / "hit")
    rendered_invalid = render_sector_snapshot(invalid, staging_root=tmp_path / "invalid")
    assert json.loads(rendered_hit["reuse"].read_text())["reused"] is True
    assert json.loads(rendered_invalid["reuse"].read_text())["reused"] is False


def test_dossier_skeleton_snapshot_does_not_read_newer_live_sources(tmp_path):
    request = _request("dossier-init", "INIT", "600519")
    handle = _handle(tmp_path, request)
    scan_root = tmp_path / "scan"
    source = _scan_day(scan_root, "2026-09-12", momentum=8.0)
    pd.DataFrame(
        {"code": ["600519"], "conviction": [0.8], "lane": ["research"], "risk": ["现金流"]}
    ).to_csv(source / "finalists.csv", index=False)
    prefetch = tmp_path / "prefetch.json"
    prefetch.write_text(
        json.dumps(
            {
                "code": "600519",
                "asof": "2026-09-14",
                "mainbz": [],
                "fwd_eps": None,
                "val_band": None,
                "notes": [],
            }
        )
    )
    target = tmp_path / "live/600519.md"

    snapshot = collect_dossier_skeleton_snapshot(
        handle, target_path=target, scan_root=scan_root
    )
    first = render_dossier_skeleton_snapshot(
        snapshot,
        prefetch_path=prefetch,
        output_dir=tmp_path / "first",
        scratch_root=tmp_path / "scratch-first",
    )
    (source / "finalists.csv").write_text("code,conviction\n000001,0.1\n")
    target.parent.mkdir(parents=True)
    target.write_text("newer live dossier", encoding="utf-8")
    second = render_dossier_skeleton_snapshot(
        snapshot,
        prefetch_path=prefetch,
        output_dir=tmp_path / "second",
        scratch_root=tmp_path / "scratch-second",
    )

    assert first["skeleton"].read_bytes() == second["skeleton"].read_bytes()
    assert first["permissions"].read_bytes() == second["permissions"].read_bytes()
    permissions = json.loads(first["permissions"].read_text())
    assert permissions["opening_target_sha256"] is None


def test_dossier_publication_snapshot_is_a_pure_versioned_patch(tmp_path):
    request = _request("dossier-init", "INIT", "600519")
    handle = _handle(tmp_path, request)
    output = handle.staging / "session_outputs"
    output.mkdir(parents=True)
    candidate = b"candidate dossier\n"
    (output / "dossier.candidate.md").write_bytes(candidate)
    (output / "dossier.validation.json").write_text(
        json.dumps({"candidate_sha256": sha256_bytes(candidate)})
    )
    (output / "dossier.permissions.json").write_text(
        json.dumps({"opening_target_sha256": "1" * 64})
    )
    for artifact_id, name in {
        "dossier.candidate": "dossier.candidate.md",
        "dossier.validation": "dossier.validation.json",
        "dossier.permissions": "dossier.permissions.json",
        "dossier.pool.candidate": "dossier.pool.candidate.json",
        "dossier.publication.bundle": "dossier.publication.json",
    }.items():
        artifacts.register_artifact(handle, artifact_id, output / name, "WRITE")
        if (output / name).is_file():
            artifacts.bind_artifact_hash(handle, artifact_id)
    pool_snapshot = {
        "schema_version": 1,
        "pool_before_text": json.dumps({"stocks": {}, "cap": 30}),
        "current_pool": {"stocks": {}, "cap": 30},
    }

    effects = render_dossier_publication_snapshot(handle, pool_snapshot)

    assert {effect["target_key"] for effect in effects} == {
        "dossier.stock.600519",
        "dossier.coverage_pool",
    }
    assert all(effect["apply_policy"] == "CAS_REPLACE" for effect in effects)
    assert not (tmp_path / "live/600519.md").exists()


def test_sector_and_dossier_profiles_project_real_plan_operations(tmp_path):
    sector_request = _request("sector-research", "FULL", "电子")
    dossier_request = _request("dossier-init", "INIT", "600519")
    sector = build_sector_plan(sector_request, _handle(tmp_path / "sector", sector_request))
    dossier = build_dossier_plan(
        dossier_request, _handle(tmp_path / "dossier", dossier_request)
    )

    for plan in (sector, dossier):
        assert replayable_plan_operations(plan) == tuple(
            task["operation"]
            for task in plan["tasks"]
            if task["kind"] == "DETERMINISTIC"
        )
    dossier_publish = next(task for task in dossier["tasks"] if task["operation"] == "dossier.publish")
    assert "dossier.permissions" in dossier_publish["input_artifact_ids"]


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical_json(value) + "\n", encoding="utf-8")


def _ref(capsule: Path, artifact_id: str, relative: str, payload: bytes) -> dict:
    path = capsule / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return {
        "artifact_id": artifact_id,
        "sha256": sha256_bytes(payload),
        "captured_path": relative,
    }


@pytest.fixture(scope="module")
def replay_identity(tmp_path_factory):
    root = tmp_path_factory.mktemp("sector-dossier-identity")
    repo = Path(__file__).resolve().parents[2]
    identity = root / "identity"
    capture_source_tree(repo, identity, environ={})
    dependencies = subprocess.run(
        ["uv", "pip", "freeze", "--python", sys.executable],
        check=True,
        capture_output=True,
    ).stdout
    _write_json(
        identity / "runtime_manifest.json",
        build_runtime_manifest(repo, dependencies=dependencies),
    )
    return identity


class _AdapterContext:
    def __init__(
        self,
        root: Path,
        unit: dict,
        request: dict,
        operation_request: dict,
        source_receipts: list[dict],
        artifacts_by_id: dict[str, bytes],
        run_id: str,
        capsule: Path,
    ) -> None:
        self.unit = unit
        self.replay_run_id = run_id
        self.inputs = root / "inputs"
        self.work = root / "work"
        self.outputs = root / "outputs"
        self.effects = root / "effects"
        for path in (self.inputs / "artifacts", self.work, self.outputs, self.effects):
            path.mkdir(parents=True, exist_ok=True)
        self.env = {
            "AUTORESEARCH_ENGINE": "codex",
            "AUTORESEARCH_FROZEN_CLOCK": operation_request["frozen_clock"],
            REPLAY_ENV: str(capsule),
        }
        self.operation_request = operation_request
        self.source_receipts = tuple(source_receipts)
        (self.inputs / "artifacts" / safe_name("session.request")).write_text(
            canonical_json(request), encoding="utf-8"
        )
        for artifact_id in unit["input_artifact_ids"]:
            (self.inputs / "artifacts" / safe_name(artifact_id)).write_bytes(
                artifacts_by_id[artifact_id]
            )

    def output_path(self, artifact_id: str) -> Path:
        target = self.outputs / safe_name(artifact_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        return target


def _sector_snapshot(mode: str, *, reused: bool = False) -> dict:
    frame = pd.DataFrame(
        {
            "code": ["600519"],
            "name": ["贵州茅台"],
            "industry": ["电子"],
            "pct_60d": [9.0],
            "pe": [20.0],
            "pb": [2.0],
            "main_net_ratio": [0.1],
            "mktcap_yi": [100.0],
        }
    ).to_csv(index=False)
    return {
        "schema_version": 1,
        "engine": "codex",
        "analysis_date": "2026-09-14",
        "industry": "电子",
        "mode": mode,
        "source": "existing_scan",
        "input_files": {
            "L1_scored_full.csv": base64.b64encode(frame.encode()).decode(),
        },
        "readthrough": None,
        "reuse": {
            "reused": reused,
            "source": "/frozen/2026-09-12/sector_briefs/电子.md" if reused else None,
            "previous_date": "2026-09-12" if reused else None,
            "shift_pp": 1.0 if reused else None,
            "source_body": (
                "# 行业 brief\n\n## 地形段(描述性)\n- 成分 1 只\n" if reused else None
            ),
        },
    }


def _dossier_snapshots() -> dict[str, dict]:
    prefetch = {
        "code": "600519",
        "asof": "2026-09-14",
        "mainbz": [],
        "fwd_eps": None,
        "val_band": None,
        "notes": ["fixture degraded"],
    }
    return {
        "dossier.prefetch": {"schema_version": 1, "data": prefetch},
        "dossier.skeleton": {
            "schema_version": 1,
            "analysis_date": "2026-09-14",
            "code": "600519",
            "name": "贵州茅台",
            "target": "/frozen/context/dossiers/600519.md",
            "opening_target": None,
            "scan_files": {},
        },
        "dossier.publish": {
            "schema_version": 1,
            "pool_before_text": canonical_json({"stocks": {}, "cap": 30}) + "\n",
            "current_pool": {"stocks": {}, "cap": 30},
        },
    }


def _completed_dossier(skeleton: bytes) -> bytes:
    text = skeleton.decode("utf-8").replace("initiated: null", "initiated: 2026-09-14")
    for old, new in (
        ("- 业务: (待首覆)", "- 业务: 高端白酒品牌与渠道"),
        ("- 驱动: (待首覆)", "- 驱动: 量价与渠道库存"),
        ("- 风险: (待首覆)", "- 风险: 动销不及预期"),
        ("- 催化: (待首覆)", "- 催化: 旺季回款"),
    ):
        text = text.replace(old, new)
    return text.replace("<!-- LLM:待首覆 -->", "首覆研究内容", 3).encode("utf-8")


def _model_outputs(task: dict, values: dict[str, bytes]) -> dict[str, bytes]:
    if "dossier.candidate" in task["output_artifact_ids"]:
        return {"dossier.candidate": _completed_dossier(values["dossier.skeleton"])}
    if "sector.report" in task["output_artifact_ids"]:
        if task["expected_output_contract"] == "sector.terrain.v1":
            text = "# 行业 brief\n\n## 地形段(描述性)\n- 成分 1 只\n"
        else:
            text = "\n".join(
                [*(f"## {index}. section" for index in range(1, 7)), "历史估值分位：缺失"]
            )
        return {"sector.report": text.encode("utf-8")}
    return {
        artifact_id: f"# {artifact_id}\nfixture\n".encode()
        for artifact_id in task["output_artifact_ids"]
    }


def _forensic_case(
    tmp_path: Path,
    replay_identity: Path,
    kind: str,
    mode: str,
) -> tuple[SimpleNamespace, Path]:
    run_id = "20260914T040102000000Z"
    subject = "电子" if kind == "sector-research" else "600519"
    request = _request(kind, mode, subject)
    capsule = tmp_path / f"{kind}-{mode}" / "capsule"
    identity = capsule / "identity"
    shutil.copytree(replay_identity, identity)
    handle = SimpleNamespace(capsule=capsule, engine="codex", run_id=run_id)
    plan_context = SimpleNamespace(
        run_id=run_id,
        engine="codex",
        workspace=tmp_path,
        staging=tmp_path / "staging",
        contract=SimpleNamespace(
            contract_hash="a" * 64,
            config_hash="b" * 64,
            user_config={},
        ),
    )
    plan = (
        build_sector_plan(request, plan_context)
        if kind == "sector-research"
        else build_dossier_plan(request, plan_context)
    )
    _write_json(identity / "session/plan.json", plan)
    _write_json(identity / "session/request.json", request)

    snapshots = (
        {"sector.prepare": _sector_snapshot(mode)}
        if kind == "sector-research"
        else _dossier_snapshots()
    )
    endpoints = {
        "sector.prepare": "sector.prepare.snapshot.v1",
        "dossier.prefetch": "dossier.prefetch.snapshot.v1",
        "dossier.skeleton": "dossier.skeleton.snapshot.v1",
        "dossier.publish": "dossier.pool.snapshot.v1",
    }
    receipts = {}
    for operation, snapshot in snapshots.items():
        task = next(task for task in plan["tasks"] if task["operation"] == operation)
        receipts[operation] = record_response(
            handle,
            {
                "engine": "codex",
                "run_id": run_id,
                "task_id": task["task_id"],
                "attempt": 1,
                "provider": "fixture",
                "endpoint": endpoints[operation],
                "normalized_params": {"analysis_date": request["analysis_date"]},
                "started_at": "2026-09-14T04:01:02Z",
                "ended_at": "2026-09-14T04:01:02Z",
                "as_of": request["analysis_date"],
                "available_at": "2026-09-14T04:01:02Z",
                "consumer_refs": [],
            },
            snapshot,
        )

    artifact_values: dict[str, bytes] = {}
    evidence_rows = []
    for task in plan["tasks"]:
        task_id = task["task_id"]
        operation = task["operation"]
        if task["kind"] == "INFERENCE":
            outputs = _model_outputs(task, artifact_values)
        else:
            operation_request = {
                "schema_version": 1,
                "task_id": task_id,
                "attempt": 1,
                "operation": operation,
                "subject": task["subject"],
                "params": {},
                "frozen_clock": "2026-09-14T04:01:02Z",
            }
            _write_json(
                capsule / f"evidence/attempt_records/{task_id}/a1/operation_request.json",
                operation_request,
            )
            unit = {
                **task,
                "expected_outputs": [
                    {"artifact_id": artifact_id}
                    for artifact_id in task["output_artifact_ids"]
                ],
                "input_refs": [
                    {"artifact_id": artifact_id}
                    for artifact_id in task["input_artifact_ids"]
                ],
            }
            task_receipts = [receipts[operation]] if operation in receipts else []
            adapter_context = _AdapterContext(
                tmp_path / f"generate-{kind}-{mode}-{safe_name(task_id)}",
                unit,
                request,
                operation_request,
                task_receipts,
                artifact_values,
                run_id,
                capsule,
            )
            old_clock = os.environ.get("AUTORESEARCH_FROZEN_CLOCK")
            os.environ["AUTORESEARCH_FROZEN_CLOCK"] = operation_request["frozen_clock"]
            try:
                if kind == "sector-research":
                    from autoresearch.session_agent.replay_adapters.sector import execute
                else:
                    from autoresearch.session_agent.replay_adapters.dossier import execute
                execute(unit, adapter_context)
            finally:
                if old_clock is None:
                    os.environ.pop("AUTORESEARCH_FROZEN_CLOCK", None)
                else:
                    os.environ["AUTORESEARCH_FROZEN_CLOCK"] = old_clock
            outputs = {
                artifact_id: adapter_context.output_path(artifact_id).read_bytes()
                for artifact_id in task["output_artifact_ids"]
            }
        input_refs = [
            _ref(
                capsule,
                artifact_id,
                f"evidence/tasks/{task_id}/a1/inputs/{artifact_id}",
                artifact_values[artifact_id],
            )
            for artifact_id in task["input_artifact_ids"]
        ]
        output_refs = [
            _ref(
                capsule,
                artifact_id,
                f"evidence/tasks/{task_id}/a1/outputs/{artifact_id}",
                payload,
            )
            for artifact_id, payload in outputs.items()
        ]
        artifact_values.update(outputs)
        task_receipt = receipts.get(operation)
        evidence = {
            "schema_version": 1,
            "engine": "codex",
            "run_id": run_id,
            "task_id": task_id,
            "attempt": 1,
            "owner": "SESSION",
            "subject": task["subject"],
            "claim_ref": None,
            "receipt_ref": None,
            "input_refs": input_refs,
            "output_refs": output_refs,
            "command_ref": None if task["kind"] == "INFERENCE" else {
                "argv": ["python", "-m", str(operation)],
                "cwd": ".",
                "exit_code": 0,
                "signal": None,
                "stdout_sha256": "1" * 64,
                "stderr_sha256": "2" * 64,
                "operation_version": f"{operation}.v1",
            },
            "transcript_refs": [] if task["kind"] == "DETERMINISTIC" else [{
                "engine": "codex",
                "status": "PRESENT",
                "role": task["role"],
                "subject": task["subject"],
                "invocation_id": f"session-{task_id}-a1",
                "session_ref": "fixture",
                "start_ordinal": 1,
                "end_ordinal": 2,
                "captured_path": f"agents/session/transcripts/{safe_name(task_id)}.json",
                "sha256": "3" * 64,
                "context_source": "MAIN",
            }],
            "source_receipt_ids": [task_receipt["receipt_id"]] if task_receipt else [],
            "status": "PRESENT",
            "reasons": [],
        }
        _write_json(capsule / f"evidence/tasks/{task_id}/a1/evidence.json", evidence)
        evidence_rows.append(
            {
                "task_id": task_id,
                "attempt": 1,
                "owner": "SESSION",
                "subject": task["subject"],
                "state": "SUCCEEDED",
                "superseded_by": None,
                "requirements": ["claim", "outputs", "accepted_receipt"],
            }
        )
    denominator = {
        "schema_version": 1,
        "engine": "codex",
        "run_id": run_id,
        "plan_hash": plan["plan_hash"],
        "expansion_hashes": [],
        "task_keys": evidence_rows,
        "closure_cutoff": "2026-09-14T04:01:02Z",
        "scope": [kind],
        "evidence_plan_hash": "0" * 64,
    }
    denominator["evidence_plan_hash"] = evidence_plan_hash(denominator)
    _write_json(capsule / "evidence/evidence_plan.json", denominator)
    return handle, capsule


@pytest.mark.parametrize(
    ("kind", "mode"),
    [
        ("sector-research", "FULL"),
        ("sector-research", "LITE"),
        ("dossier-init", "INIT"),
    ],
)
def test_real_sector_and_dossier_replay_is_offline_and_exact(
    tmp_path, replay_identity, kind, mode
):
    if not strict_isolation_available():
        pytest.skip("INCOMPLETE: no supported system replay sandbox is available")
    handle, capsule = _forensic_case(tmp_path, replay_identity, kind, mode)
    live_state = tmp_path / "persistent"
    live_state.mkdir()
    (live_state / "coverage_pool.json").write_text("newer-live-version")
    before = {path.name: path.read_bytes() for path in live_state.iterdir()}

    result = execute_replay(
        build_replay_plan(handle),
        capsule,
        tmp_path / f"audit-{kind}-{mode}",
        DomainReplayRunner(capsule, denied_write_roots=(live_state,)),
    )

    assert result["compute_status"] == "FULL", json.dumps(
        result, ensure_ascii=False, indent=2
    )
    assert result["model_status"] == "EVIDENCE_ONLY"
    assert result["diffs"] == []
    assert {path.name: path.read_bytes() for path in live_state.iterdir()} == before
    if kind == "dossier-init":
        assert {effect["target_key"] for effect in result["effects"]} == {
            "dossier.stock.600519",
            "dossier.coverage_pool",
        }
