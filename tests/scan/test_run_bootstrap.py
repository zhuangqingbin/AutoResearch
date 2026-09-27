"""Configuration-only scan bootstrap and active-contract resolution."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from autoresearch.common import workspace as ws
from autoresearch.scan.run_bootstrap import (
    prepare_scan_run,
    resolve_active_scan_contract,
)
from autoresearch.trace.capsule import begin_run

DATE = "2026-08-27"
NOW = datetime(2026, 8, 27, 1, 2, 3, 456789, tzinfo=timezone.utc)
RUN_ID = "20260827T010203456789Z"


def test_prepare_scan_run_builds_complete_v3_contract_without_market_fetch(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(ws, "ENGINE", "codex")
    monkeypatch.setattr(ws, "context_root", lambda: tmp_path / "context_codex")
    monkeypatch.setattr(
        "autoresearch.scan.user_config.DEFAULT_PINNED_PATH",
        tmp_path / "missing-pinned.jsonc",
    )
    monkeypatch.setattr(
        "autoresearch.scan.frame.build_market_frame",
        lambda *a, **k: pytest.fail("bootstrap fetched market data"),
    )
    workspace = ws.scan_run_root(RUN_ID)
    contract = prepare_scan_run(
        DATE,
        config={
            "l0": {"source": "em", "cap_floor_yi": 42, "include_bj": False},
            "pinned": {"cap": 3, "ttl_days": 7},
            "l4": {"max_cards": 8},
        },
        run_id=RUN_ID,
        engine="codex",
        workspace_path=workspace,
        now=NOW,
        git_sha="deadbeef",
    )

    assert contract.schema_version == 3
    assert contract.run_id == RUN_ID
    assert contract.engine == "codex"
    assert contract.workspace_path == str(workspace)
    assert contract.data_policy == {
        "source": "em",
        "cap_floor_yi": 42.0,
        "include_bj": False,
    }
    assert contract.stage_budgets["l4_max_cards"] == 8
    assert contract.stage_budgets["pinned_cap"] == 3
    assert contract.stage_budgets["pinned_ttl_days"] == 7
    assert contract.artifact_schema_versions
    assert isinstance(contract.prompt_hashes, dict)


def test_prepare_scan_run_loads_and_validates_config_file(tmp_path, monkeypatch):
    monkeypatch.setattr(ws, "ENGINE", "codex")
    monkeypatch.setattr(ws, "context_root", lambda: tmp_path / "context_codex")
    bad = tmp_path / "bad.jsonc"
    bad.write_text(json.dumps({"typo": True}), encoding="utf-8")
    with pytest.raises(ValueError, match="未知顶层键"):
        prepare_scan_run(
            DATE,
            config=bad,
            run_id=RUN_ID,
            engine="codex",
            workspace_path=ws.scan_run_root(RUN_ID),
            now=NOW,
        )


def test_prepare_scan_run_validates_already_decoded_config(tmp_path, monkeypatch):
    monkeypatch.setattr(ws, "ENGINE", "codex")
    monkeypatch.setattr(ws, "context_root", lambda: tmp_path / "context_codex")
    with pytest.raises(ValueError, match="未知顶层键"):
        prepare_scan_run(
            DATE,
            config={"typo": True},
            run_id=RUN_ID,
            engine="codex",
            workspace_path=ws.scan_run_root(RUN_ID),
            now=NOW,
        )


def test_prepare_scan_run_resolves_agents_once_into_hashed_user_config(
    tmp_path, monkeypatch
):
    from autoresearch.scan import user_config as uc

    monkeypatch.setattr(ws, "ENGINE", "codex")
    monkeypatch.setattr(ws, "context_root", lambda: tmp_path / "context_codex")
    monkeypatch.setattr(uc, "DEFAULT_PINNED_PATH", tmp_path / "missing-pinned.jsonc")
    agents = {role: {"effort": "high"} for role in uc._AGENT_ROLES}
    config = tmp_path / "scan_config.jsonc"
    config.write_text(json.dumps({"agents": agents}), encoding="utf-8")
    contract = prepare_scan_run(
        DATE,
        config=config,
        run_id=RUN_ID,
        engine="codex",
        workspace_path=ws.scan_run_root(RUN_ID),
        now=NOW,
    )
    assert set(contract.user_config["resolved_agents"]) == uc._AGENT_ROLES
    assert contract.user_config["engine"] == "codex"
    assert contract.agents == agents


def test_active_contract_explicit_expected_config_still_detects_mismatch(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(ws, "ENGINE", "codex")
    monkeypatch.setattr(ws, "context_root", lambda: tmp_path / "context_codex")
    monkeypatch.setattr(
        "autoresearch.scan.user_config.DEFAULT_PINNED_PATH",
        tmp_path / "missing-pinned.jsonc",
    )
    expected = tmp_path / "expected.jsonc"
    expected.write_text(json.dumps({"pinned": {"cap": 3}}), encoding="utf-8")
    other = tmp_path / "other.jsonc"
    other.write_text(json.dumps({"pinned": {"cap": 4}}), encoding="utf-8")
    handle = begin_run("scan-market", DATE, "codex", expected, now=NOW)
    monkeypatch.setenv("AUTORESEARCH_RUN_ID", handle.run_id)
    with pytest.raises(RuntimeError, match="config mismatch"):
        resolve_active_scan_contract(DATE, config=other)
