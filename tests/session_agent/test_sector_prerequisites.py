from __future__ import annotations

import json

import pandas as pd
import pytest

from autoresearch.session_agent import artifacts
from autoresearch.session_agent.domain_ops import sector_prepare

from .test_sector import _request
from .test_service import _handle


def test_sector_prepare_builds_only_required_deterministic_inputs(tmp_path, monkeypatch):
    handle = _handle(tmp_path)
    session = handle.workspace / "session"
    session.mkdir()
    (session / "request.json").write_text(json.dumps(_request("LITE")))
    frame = pd.DataFrame(
        {
            "code": ["600001", "600002"],
            "name": ["甲", "乙"],
            "industry": ["电子", "煤炭"],
            "pct_60d": [10.0, -5.0],
            "pe": [20.0, 8.0],
            "pb": [2.0, 1.0],
            "main_net_ratio": [0.1, -0.1],
            "mktcap_yi": [100.0, 80.0],
        }
    )
    monkeypatch.setattr(
        "autoresearch.scan.frame.build_market_frame",
        lambda *args, **kwargs: (frame, {"universe": 2, "after_gate_a": 2}),
    )
    output = handle.staging / "session_outputs"
    artifacts.register_artifact(handle, "sector.input.manifest", output / "sector.inputs.json", "WRITE")
    artifacts.register_artifact(handle, "sector.pack", output / "sector.pack.json", "WRITE")
    result = sector_prepare(handle, scan_root=tmp_path / "missing_scan")
    assert result["source"] == "generated_frame"
    assert result["pack"]["industry"] == "电子"
    assert "readthrough" not in result["pack"]
    assert (handle.staging / "sector_inputs/2026-09-13/L1_scored_full.csv").is_file()
    assert not (handle.staging / "sector_inputs/2026-09-13/_l3_judged.json").exists()


def test_sector_prepare_records_verified_same_engine_existing_inputs(tmp_path):
    handle = _handle(tmp_path)
    session = handle.workspace / "session"
    session.mkdir()
    (session / "request.json").write_text(json.dumps(_request("FULL")))
    scan = tmp_path / "scan/2026-09-13"
    scan.mkdir(parents=True)
    pd.DataFrame(
        {
            "code": ["600001"],
            "industry": ["电子"],
            "pct_60d": [10.0],
            "pe": [20.0],
            "pb": [2.0],
            "main_net_ratio": [0.1],
            "mktcap_yi": [100.0],
        }
    ).to_csv(scan / "L1_scored_full.csv", index=False)
    pd.DataFrame({"code": ["600001"], "industry": ["电子"]}).to_csv(
        scan / "L2_gbdt_top200.csv", index=False
    )
    output = handle.staging / "session_outputs"
    artifacts.register_artifact(handle, "sector.input.manifest", output / "sector.inputs.json", "WRITE")
    artifacts.register_artifact(handle, "sector.pack", output / "sector.pack.json", "WRITE")
    result = sector_prepare(handle, scan_root=tmp_path / "scan")
    assert result["source"] == "existing_scan"
    assert set(result["input_hashes"]) == {"L1_scored_full.csv", "L2_gbdt_top200.csv"}


def test_sector_prepare_rejects_an_unknown_industry(tmp_path, monkeypatch):
    handle = _handle(tmp_path)
    session = handle.workspace / "session"
    session.mkdir()
    (session / "request.json").write_text(json.dumps(_request("LITE", "不存在行业")))
    frame = pd.DataFrame(
        {"code": ["600001"], "industry": ["电子"], "pct_60d": [1.0]}
    )
    monkeypatch.setattr(
        "autoresearch.scan.frame.build_market_frame",
        lambda *args, **kwargs: (frame, {"universe": 1, "after_gate_a": 1}),
    )
    output = handle.staging / "session_outputs"
    artifacts.register_artifact(handle, "sector.input.manifest", output / "sector.inputs.json", "WRITE")
    artifacts.register_artifact(handle, "sector.pack", output / "sector.pack.json", "WRITE")
    with pytest.raises(ValueError, match="industry is absent"):
        sector_prepare(handle, scan_root=tmp_path / "missing_scan")
