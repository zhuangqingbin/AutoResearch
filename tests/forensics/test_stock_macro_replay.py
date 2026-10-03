from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from autoresearch.analyze import harvest as stock_harvest
from autoresearch.common.atomic import canonical_json, sha256_bytes
from autoresearch.contracts.forensic import evidence_plan_hash
from autoresearch.macro import harvest as macro_harvest
from autoresearch.session_agent.publication import replayable_plan_operations
from autoresearch.session_agent.replay_adapters import DomainReplayRunner
from autoresearch.session_agent.replay_adapters.common import safe_name
from autoresearch.session_agent.replay_registry import build_replay_plan
from autoresearch.session_agent.workflows.macro import build_macro_plan
from autoresearch.session_agent.workflows.stock import build_stock_plan
from autoresearch.trace.offline import strict_isolation_available
from autoresearch.trace.replay import REPLAY_ENV, execute_replay
from autoresearch.trace.source_receipts import record_response
from autoresearch.trace.source_tree import build_runtime_manifest, capture_source_tree

NOW = datetime(2026, 9, 14, 4, 1, 2, tzinfo=timezone.utc)


def _context(tmp_path: Path):
    return SimpleNamespace(
        run_id="20260914T040102000000Z",
        engine="codex",
        analysis_date="2026-09-14",
        workspace=tmp_path,
        staging=tmp_path / "staging",
        contract=SimpleNamespace(
            contract_hash="a" * 64,
            config_hash="b" * 64,
            user_config={},
        ),
    )


def _request(kind: str, mode: str, **changes) -> dict:
    value = {
        "schema_version": 1,
        "kind": kind,
        "requested_mode": mode,
        "analysis_date": "2026-09-14",
        "subject": "600519.SS" if kind == "stock-research" else None,
        "peers": [],
        "asset_type": "stock" if kind == "stock-research" else None,
        "name": "贵州茅台" if kind == "stock-research" else None,
        "force_full": False,
        "host_profile": {},
        "predecessor_run_id": None,
    }
    value.update(changes)
    return value


@pytest.mark.parametrize(
    ("ticker", "asset_type", "market"),
    [
        ("600519.SS", "stock", "ashare"),
        ("NVDA", "stock", "us"),
        ("0700.HK", "stock", "other"),
        ("BTC-USD", "crypto", "crypto"),
    ],
)
@pytest.mark.parametrize("slim", [False, True])
def test_stock_snapshot_renderer_is_clocked_and_market_explicit(
    tmp_path, ticker, asset_type, market, slim
):
    snapshot = {
        "schema_version": 1,
        "ticker": ticker,
        "trade_date": "2026-09-14",
        "asset_type": asset_type,
        "peers": ["AMD"] if ticker == "NVDA" else [],
        "slim": slim,
        "market": market,
        "instrument_context": "identity",
        "blocks": [
            {"name": "surface", "text": "\n## Surface\n\nvalue\n"},
            {"name": "income_statement", "text": "\n## Income statement\n\nvalue\n"},
        ],
        "side_artifacts": (
            {}
            if slim
            else {f"{ticker}_2026-09-14_indicators.md": "# indicators\n"}
        ),
    }

    first = tmp_path / "first"
    second = tmp_path / "second"
    stock_harvest.render_harvest_snapshot(snapshot, output_dir=first, clock=NOW)
    stock_harvest.render_harvest_snapshot(snapshot, output_dir=second, clock=NOW)

    first_files = {
        path.relative_to(first): path.read_bytes()
        for path in sorted(first.rglob("*"))
        if path.is_file()
    }
    second_files = {
        path.relative_to(second): path.read_bytes()
        for path in sorted(second.rglob("*"))
        if path.is_file()
    }
    assert first_files == second_files
    report = next(value.decode("utf-8") for key, value in first_files.items() if "indicators" not in key.name and "deep" not in key.name)
    assert "2026-09-14T04:01:02+00:00" in report


def test_macro_snapshot_renderer_is_clocked_and_carries_frozen_scan_meta(tmp_path):
    snapshot = {
        "schema_version": 1,
        "analysis_date": "2026-09-14",
        "sections": [
            {"title": "US macro (FRED)", "body": "us"},
            {"title": "Cross-asset price basket (yfinance)", "body": "cross"},
        ],
        "tape": {"as_of": "2026-09-14", "ok": False, "numbers": {}},
        "calendar": {"ok": False},
        "scan_meta": {"regime": {"label": "range"}},
    }

    outputs = macro_harvest.render_harvest_snapshot(
        snapshot, output_dir=tmp_path, clock=NOW
    )

    assert set(outputs) == {"data", "global_tape", "scan_meta"}
    assert "2026-09-14T04:01:02+00:00" in outputs["data"].read_text()
    assert json.loads(outputs["global_tape"].read_text())["generated_at"] == (
        "2026-09-14T04:01:02+00:00"
    )
    assert json.loads(outputs["scan_meta"].read_text())["regime"]["label"] == "range"


@pytest.mark.parametrize("mode", ["FULL", "LITE"])
def test_stock_plan_closes_every_real_assembler_input(tmp_path, mode):
    request = _request("stock-research", mode, peers=["AMD"])
    plan = build_stock_plan(request, _context(tmp_path))
    tasks = {task["task_id"]: task for task in plan["tasks"]}

    assert replayable_plan_operations(plan) == tuple(
        task["operation"] for task in plan["tasks"] if task["kind"] == "DETERMINISTIC"
    )
    if mode == "FULL":
        produced = {
            artifact_id
            for task in plan["tasks"]
            if task["kind"] == "INFERENCE"
            for artifact_id in task["output_artifact_ids"]
            if artifact_id.startswith("stock.full.1_")
            or artifact_id.startswith("stock.full.2_")
            or artifact_id.startswith("stock.full.3_")
            or artifact_id.startswith("stock.full.4_")
        }
        assert produced <= set(tasks["stock.assemble"]["input_artifact_ids"])
        assert "stock.context" in tasks["stock.assemble"]["input_artifact_ids"]


@pytest.mark.parametrize("mode", ["FULL", "LITE"])
def test_macro_plan_closes_state_and_assembler_inputs(tmp_path, mode):
    plan = build_macro_plan(_request("macro-research", mode), _context(tmp_path))
    tasks = {task["task_id"]: task for task in plan["tasks"]}

    assert replayable_plan_operations(plan) == tuple(
        task["operation"] for task in plan["tasks"] if task["kind"] == "DETERMINISTIC"
    )
    if mode == "FULL":
        produced = {
            artifact_id
            for task in plan["tasks"]
            if task["kind"] == "INFERENCE"
            for artifact_id in task["output_artifact_ids"]
        }
        assemble_inputs = set(tasks["macro.assemble"]["input_artifact_ids"])
        assert produced <= assemble_inputs
        assert {"macro.global_tape", "macro.scan_meta"} <= assemble_inputs
        assert "macro.scan_meta" in tasks["macro.harvest"]["output_artifact_ids"]


@pytest.mark.parametrize(("peers", "expected"), [([], False), (["AMD"], True)])
def test_stock_full_peer_branch_is_part_of_the_frozen_plan(tmp_path, peers, expected):
    request = _request(
        "stock-research",
        "FULL",
        subject="NVDA",
        name=None,
        peers=peers,
    )
    task_ids = {task["task_id"] for task in build_stock_plan(request, _context(tmp_path))["tasks"]}
    assert ("stock.peer" in task_ids) is expected


def test_macro_lite_reapplies_freshness_and_preserves_same_day_version(tmp_path):
    from autoresearch.session_agent.domain_ops import render_macro_lite_snapshot

    base = {
        "schema_version": 1,
        "analysis_date": "2026-09-14",
        "market_payload": {"regime": {"label": "range"}},
        "macro_state_source": "FIXTURE",
    }
    fresh_a = {
        "as_of": "2026-09-14",
        "ttl_days": 7,
        "regime_at_run": "range",
        "overall_rating": "Hold",
    }
    fresh_b = {**fresh_a, "overall_rating": "Underweight"}
    a = tmp_path / "a"
    b = tmp_path / "b"
    render_macro_lite_snapshot(
        {**base, "macro_state_text": canonical_json(fresh_a)}, output_dir=a
    )
    render_macro_lite_snapshot(
        {**base, "macro_state_text": canonical_json(fresh_b)}, output_dir=b
    )
    market_a = json.loads((a / "market_pack.json").read_text())
    market_b = json.loads((b / "market_pack.json").read_text())
    assert "新鲜" in market_a["macro_state_note"]
    assert market_a["macro_state"]["overall_rating"] == "Hold"
    assert market_b["macro_state"]["overall_rating"] == "Underweight"
    assert (a / "market_pack.json").read_bytes() != (b / "market_pack.json").read_bytes()

    stale = tmp_path / "stale"
    render_macro_lite_snapshot(
        {
            **base,
            "macro_state_text": canonical_json({**fresh_a, "as_of": "2026-09-01"}),
        },
        output_dir=stale,
    )
    stale_market = json.loads((stale / "market_pack.json").read_text())
    assert stale_market["macro_state"] is None
    assert "过期" in stale_market["macro_state_note"]


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
    root = tmp_path_factory.mktemp("stock-macro-identity")
    repo = Path(__file__).resolve().parents[2]
    identity = root / "identity"
    capture_source_tree(repo, identity, environ={})
    dependencies = subprocess.run(
        ["uv", "pip", "freeze", "--python", sys.executable],
        check=True,
        capture_output=True,
    ).stdout
    _write_json(identity / "runtime_manifest.json", build_runtime_manifest(repo, dependencies=dependencies))
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


def _model_payload(artifact_id: str) -> bytes:
    if artifact_id.endswith("4_portfolio.decision"):
        return b"**Rating**: Hold\nFINAL TRANSACTION PROPOSAL: **HOLD**\n"
    if artifact_id.endswith("1_spine.decision"):
        from autoresearch.macro.assemble import CROSS_ASSET_KEYS

        return ("\n".join(f"- {key}: **Rating**: Hold" for key in CROSS_ASSET_KEYS)
                + "\n置信度: 中\n").encode()
    if artifact_id.endswith("2_meso.sector_map"):
        return "- 电子: **Rating**: Overweight\n置信度: 中\n".encode()
    if artifact_id == "stock.card.output":
        return b"**Rating**: Hold\nFINAL TRANSACTION PROPOSAL: **HOLD**\n"
    if artifact_id == "macro.market_view":
        return "\n".join(f"{index}. **section {index}**: value" for index in range(1, 7)).encode()
    return f"# {artifact_id}\ncontent\n置信度: 中\n".encode()


def _source_snapshot(kind: str, mode: str, request: dict) -> tuple[str, dict]:
    if kind == "stock-research":
        ticker = request["subject"]
        slim = mode == "LITE"
        snapshot = {
            "schema_version": 1,
            "ticker": ticker,
            "trade_date": request["analysis_date"],
            "asset_type": request["asset_type"],
            "peers": request["peers"],
            "slim": slim,
            "market": stock_harvest.market_key_for(ticker, request["asset_type"]),
            "instrument_context": "fixture identity",
            "blocks": [
                {"name": "surface", "text": "\n## Surface\n\nvalue\n"},
                {"name": "income_statement", "text": "\n## Income statement\n\nvalue\n"},
            ],
            "side_artifacts": (
                {}
                if slim
                else {f"{ticker}_{request['analysis_date']}_indicators.md": "# indicators\n"}
            ),
        }
        return "stock.harvest.snapshot.v1", snapshot
    if mode == "FULL":
        return "macro.harvest.snapshot.v1", {
            "schema_version": 1,
            "analysis_date": request["analysis_date"],
            "sections": [
                {"title": "US macro (FRED)", "body": "fixture"},
                {"title": "A股中观", "body": "**行业资金净流入(tushare)**:\n"
                 "| 行业 | 主力净流入(亿) | 领涨股 |\n|---|---:|---|\n| 电子 | 1 | 示例甲 |\n"},
            ],
            "tape": {"as_of": request["analysis_date"], "ok": False, "numbers": {}},
            "calendar": {"ok": False},
            "scan_meta": {"regime": {"label": "range"}},
        }
    return "macro.lite.frame.snapshot.v1", {
        "schema_version": 1,
        "analysis_date": request["analysis_date"],
        "market_payload": {"regime": {"label": "range"}, "sector_healthy_top3": ["电子"]},
        "macro_state_text": canonical_json(
            {
                "as_of": request["analysis_date"],
                "ttl_days": 7,
                "regime_at_run": "range",
                "cross_asset": {},
                "ashare_sectors": {},
            }
        ),
        "macro_state_source": "FIXTURE",
    }


def _forensic_case(
    tmp_path: Path,
    replay_identity: Path,
    kind: str,
    mode: str,
) -> tuple[SimpleNamespace, Path]:
    run_id = "20260914T040102000000Z"
    request = _request(kind, mode)
    request["host_profile"] = {}
    capsule = tmp_path / f"{kind}-{mode}" / "capsule"
    identity = capsule / "identity"
    shutil.copytree(replay_identity, identity)
    handle = SimpleNamespace(capsule=capsule, engine="codex", run_id=run_id)
    plan_context = _context(tmp_path)
    plan_context.run_id = run_id
    plan = (
        build_stock_plan(request, plan_context)
        if kind == "stock-research"
        else build_macro_plan(request, plan_context)
    )
    _write_json(identity / "session/plan.json", plan)
    _write_json(identity / "session/request.json", request)

    artifact_values: dict[str, bytes] = {}
    if kind == "macro-research" and mode == "FULL":
        from autoresearch.session_agent.workflows.macro import global_intel_policy

        artifact_values["macro.intel.policy"] = canonical_json(global_intel_policy({"session": {"global_intel_max_queries": 3}})).encode("utf-8")
    if any("research.frame" in task["input_artifact_ids"] for task in plan["tasks"]):
        from autoresearch.common.execution_math import build_decision_frame

        artifact_values["research.frame"] = canonical_json(build_decision_frame(
            analysis_session=request["analysis_date"], knowledge_cutoff="2026-09-14T04:01:02Z",
            venue="XSHG", research_depth=mode, usage="standalone", sessions=[],
            calendar_quality="UNKNOWN",
        )).encode("utf-8")
    evidence_rows = []
    source_task = "stock.harvest" if kind == "stock-research" else (
        "macro.harvest" if mode == "FULL" else "macro.frame"
    )
    endpoint, snapshot = _source_snapshot(kind, mode, request)
    receipt = record_response(
        handle,
        {
            "engine": "codex",
            "run_id": run_id,
            "task_id": source_task,
            "attempt": 1,
            "provider": "fixture",
            "endpoint": endpoint,
            "normalized_params": {"analysis_date": request["analysis_date"]},
            "started_at": "2026-09-14T04:01:02Z",
            "ended_at": "2026-09-14T04:01:02Z",
            "as_of": request["analysis_date"],
            "available_at": "2026-09-14T04:01:02Z",
            "consumer_refs": [],
        },
        snapshot,
    )

    for task in plan["tasks"]:
        task_id = task["task_id"]
        if task["kind"] == "INFERENCE":
            outputs = {
                artifact_id: _model_payload(artifact_id)
                for artifact_id in task["output_artifact_ids"]
            }
        else:
            params = (
                {
                    "ticker": request["subject"],
                    "analysis_date": request["analysis_date"],
                    "asset_type": request["asset_type"],
                    "peers": request["peers"],
                    "slim": mode == "LITE",
                }
                if task["operation"] == "stock.harvest"
                else {}
            )
            operation_request = {
                "schema_version": 1,
                "task_id": task_id,
                "attempt": 1,
                "operation": task["operation"],
                "subject": task["subject"],
                "params": params,
                "frozen_clock": "2026-09-14T04:01:02Z",
            }
            _write_json(
                capsule / f"evidence/attempt_records/{task_id}/a1/operation_request.json",
                operation_request,
            )
            unit = {**task, "expected_outputs": [
                {"artifact_id": artifact_id} for artifact_id in task["output_artifact_ids"]
            ], "input_refs": [{"artifact_id": artifact_id} for artifact_id in task["input_artifact_ids"]]}
            adapter_context = _AdapterContext(
                tmp_path / f"generate-{kind}-{mode}-{safe_name(task_id)}",
                unit,
                request,
                operation_request,
                [receipt] if task_id == source_task else [],
                artifact_values,
                run_id,
                capsule,
            )
            old_clock = os.environ.get("AUTORESEARCH_FROZEN_CLOCK")
            os.environ["AUTORESEARCH_FROZEN_CLOCK"] = operation_request["frozen_clock"]
            try:
                if kind == "stock-research":
                    from autoresearch.session_agent.replay_adapters.stock import execute
                else:
                    from autoresearch.session_agent.replay_adapters.macro import execute
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
                "argv": ["python", "-m", task["operation"]],
                "cwd": ".",
                "exit_code": 0,
                "signal": None,
                "stdout_sha256": "1" * 64,
                "stderr_sha256": "2" * 64,
                "operation_version": f"{task['operation']}.v1",
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
            "source_receipt_ids": [receipt["receipt_id"]] if task_id == source_task else [],
            "status": "PRESENT",
            "reasons": [],
        }
        _write_json(capsule / f"evidence/tasks/{task_id}/a1/evidence.json", evidence)
        evidence_rows.append({
            "task_id": task_id,
            "attempt": 1,
            "owner": "SESSION",
            "subject": task["subject"],
            "state": "SUCCEEDED",
            "superseded_by": None,
            "requirements": ["claim", "outputs", "accepted_receipt"],
        })

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
        ("stock-research", "FULL"),
        ("stock-research", "LITE"),
        ("macro-research", "FULL"),
        ("macro-research", "LITE"),
    ],
)
def test_real_domain_replay_matches(tmp_path, replay_identity, kind, mode):
    if not strict_isolation_available():
        pytest.skip("INCOMPLETE: no supported system replay sandbox is available")
    handle, capsule = _forensic_case(tmp_path, replay_identity, kind, mode)
    replay_plan = build_replay_plan(handle)

    result = execute_replay(
        replay_plan,
        capsule,
        tmp_path / f"audit-{kind}-{mode}",
        DomainReplayRunner(capsule),
    )

    assert result["compute_status"] == "FULL", result
    assert result["model_status"] == "EVIDENCE_ONLY"
    assert result["diffs"] == []
