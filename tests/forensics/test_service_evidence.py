from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from autoresearch.broker import reconcile as broker_reconcile, schema as broker_schema
from autoresearch.broker.ingest import normalize_source_file
from autoresearch.common.atomic import canonical_json, sha256_bytes
from autoresearch.dossier import builder, delta
from autoresearch.dossier.reconcile import render_reconcile_candidate
from autoresearch.research.stage_value import paired_daily_selection
from autoresearch.session_agent.replay_adapters.services import replay_operation_evidence
from autoresearch.trace.operation_evidence import (
    operation_output_reference,
    record_operation_evidence,
)

_HEADER = (
    "trade_date,trade_time,code,name,biz_type,price,qty,amount,commission,stamp_tax,"
    "transfer_fee,other_fee,net_amount,balance_after"
)
_ROW = "2026-08-25,09:31:05,000001,平安银行,证券买入,12.34,100,1234.00,5,0,.01,0,-1239.01,100"


def _json(value: object) -> bytes:
    return (canonical_json(value) + "\n").encode()


def _tree(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): sha256_bytes(path.read_bytes())
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _record(tmp_path: Path, operation: str) -> dict:
    code = Path(__file__)
    evidence_root = tmp_path / "evidence"
    if operation == "prewarm":
        parameters = {
            "date": "2026-09-14",
            "started_at": 1.0,
            "ended_at": 2.0,
            "steps": [{"step": "frame_lake", "ok": True, "note": "fixture"}],
        }
        before, after = {"daily/a": "a" * 64}, {
            "daily/a": "a" * 64,
            "daily/b": "b" * 64,
        }
        effects = [
            {"kind": "VIRTUAL_LAKE_WRITE", "path": "daily/b", "sha256": "b" * 64}
        ]
        return record_operation_evidence(
            operation,
            parameters=parameters,
            inputs={
                "prewarm.lake.before": before,
                "prewarm.lake.after": after,
                "prewarm.manifest.source": json.dumps(
                    parameters, ensure_ascii=False, indent=1
                ),
            },
            outputs={
                "prewarm.manifest": json.dumps(
                    parameters, ensure_ascii=False, indent=1
                )
            },
            effects=effects,
            code_paths=[code],
            evidence_root=evidence_root,
        )
    if operation == "dossier.reconcile":
        dossier = builder.build_skeleton(
            "300857", "2026-09-14", name="协创数据", sector="消费电子"
        )["path"]
        opening = delta.set_frontmatter_key(
            dossier.read_text(encoding="utf-8"), "initiated", "2026-09-14"
        )
        actual = {"kind": "express", "ann_date": "20260913", "line": "净利同比 +12%"}
        candidate, result = render_reconcile_candidate(
            opening, "300857", "20260630", "2026-09-14", actual
        )
        effects = [
            {
                "kind": "DOSSIER_PATCH",
                "code": "300857",
                "before_sha256": sha256_bytes(opening.encode()),
                "after_sha256": sha256_bytes(candidate.encode()),
            }
        ]
        return record_operation_evidence(
            operation,
            parameters={
                "code": "300857",
                "period": "20260630",
                "today": "2026-09-14",
            },
            inputs={"dossier.opening": opening, "dossier.actual": actual},
            outputs={"dossier.candidate": candidate, "dossier.result": result},
            effects=effects,
            code_paths=[code],
            evidence_root=evidence_root,
        )
    if operation == "broker.ingest":
        source = tmp_path / "gtht_20260825-20260825.csv"
        source.write_text(f"{_HEADER}\n{_ROW}\n", encoding="utf-8")
        parameters = {
            "source_kind": "screenshot",
            "source_file": source.name,
            "account": None,
            "ingested_at": "2026-09-14T10:00:00",
            "today": "2026-09-14",
        }
        frame, report = normalize_source_file(
            source,
            source_kind="screenshot",
            account=None,
            ingested_at=parameters["ingested_at"],
        )
        outputs = {
            "broker.normalized": frame.to_csv(index=False),
            "broker.validation": report,
        }
        return record_operation_evidence(
            operation,
            parameters=parameters,
            inputs={"broker.source": source},
            outputs=outputs,
            effects=[{"kind": "BROKER_NORMALIZED_ROWS", "rows": 1}],
            code_paths=[code],
            evidence_root=evidence_root,
        )
    if operation == "broker.reconcile":
        raw = pd.DataFrame(
            {
                column: [] for column in broker_schema.RAW_STORE_COLUMNS
            }
        )
        raw_bytes = raw.to_csv(index=False).encode()
        expected = broker_reconcile.report(tmp_path / "no-data") + "\n"
        return record_operation_evidence(
            operation,
            parameters={"account": None, "since": None},
            inputs={"broker.raw.gtht": raw_bytes},
            outputs={"broker.reconcile.report": expected},
            effects=[],
            code_paths=[code],
            evidence_root=evidence_root,
        )
    candidates = pd.DataFrame(
        [
            {
                "date": "20260914",
                "code": "600000",
                "baseline": True,
                "refined": True,
                "value": 0.02,
            }
        ]
    )
    expected = paired_daily_selection(candidates)
    return record_operation_evidence(
        "research.evaluate",
        parameters={"stage": "menu_to_l3"},
        inputs={"research.candidates": candidates.to_csv(index=False)},
        outputs={"research.evaluation": expected.to_csv(index=False)},
        effects=[{"kind": "CANDIDATE_EVALUATION", "rows": 1, "stage": "menu_to_l3"}],
        code_paths=[code],
        evidence_root=evidence_root,
    )


@pytest.mark.parametrize(
    "operation",
    [
        "prewarm",
        "dossier.reconcile",
        "broker.ingest",
        "broker.reconcile",
        "research.evaluate",
    ],
)
def test_service_replay_only_produces_scratch_effects(tmp_path, operation):
    persistent = tmp_path / "persistent"
    persistent.mkdir()
    (persistent / "state.json").write_text('{"version":1}\n')
    evidence = _record(tmp_path, operation)
    before = _tree(persistent)

    result = replay_operation_evidence(evidence["evidence_root"], tmp_path / "replay")

    assert result["status"] == "MATCH", json.dumps(result, ensure_ascii=False)
    assert _tree(persistent) == before


def test_operation_evidence_is_standalone_and_consumers_bind_original_root(tmp_path):
    evidence = _record(tmp_path, "research.evaluate")
    assert "run_id" not in evidence
    ref = operation_output_reference(evidence["evidence_root"], "research.evaluation")
    assert ref["operation_id"] == evidence["operation_id"]
    assert ref["sha256"]
    assert Path(ref["evidence_root"]).samefile(evidence["evidence_root"])


@pytest.mark.parametrize("operation", ["ops.delete", "ops.migrate", "trade.execute"])
def test_destructive_or_real_trading_operations_have_no_handler(tmp_path, operation):
    with pytest.raises(ValueError, match="unsupported standalone operation"):
        record_operation_evidence(
            operation,
            parameters={},
            inputs={},
            outputs={},
            effects=[],
            code_paths=[Path(__file__)],
            evidence_root=tmp_path,
        )


def test_operation_id_detects_sidecar_rewrite(tmp_path):
    evidence = _record(tmp_path, "prewarm")
    path = Path(evidence["evidence_root"]) / "evidence.json"
    value = json.loads(path.read_text())
    value["parameters"]["date"] = "2026-09-15"
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="operation_id mismatch"):
        replay_operation_evidence(path, tmp_path / "replay-corrupt")
