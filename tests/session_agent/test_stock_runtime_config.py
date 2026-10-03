from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from autoresearch.common import workspace as ws
from autoresearch.common.run_identity import write_run_contract
from autoresearch.scan import user_config
from autoresearch.session_agent import artifacts, service
from autoresearch.session_agent.dispatch import resolve_agent_spec
from autoresearch.trace import capsule

from .test_service import _planner, _request


@pytest.fixture
def stock_bootstrap(tmp_path, monkeypatch):
    config = user_config.load_user_config(user_config.DEFAULT_PATH)
    config["session"]["timeouts"]["mailbox"]["stock.card"] = 1234
    path = tmp_path / "scan_config.json"
    path.write_text(json.dumps(config))
    monkeypatch.setattr(user_config, "DEFAULT_PATH", path)
    monkeypatch.setattr(user_config, "_PRODUCTION_DEFAULT_PATH", path)
    monkeypatch.setattr(user_config, "load_codex_capabilities", lambda: None)
    monkeypatch.setattr(ws, "context_root", lambda: tmp_path / "context_codex")

    def begin_run(kind, date, engine, config, *, session_ref, bootstrap):
        run_id = "20260913T010203000000Z"
        workspace = ws.run_root(kind, run_id)
        workspace.mkdir(parents=True)
        contract = bootstrap(date, config=config, engine=engine, run_id=run_id,
                             workspace_path=workspace, session_ref=session_ref)
        write_run_contract(workspace / "run_contract.json", contract)
        return SimpleNamespace(contract=contract, engine=engine, run_id=run_id,
                               workspace=workspace, staging=workspace / "staging",
                               capsule=workspace / "capsule", analysis_date=date)

    monkeypatch.setattr(capsule, "begin_run", begin_run)
    return path, config


def test_stock_begin_freezes_separate_dispatch_config(stock_bootstrap):
    path, config = stock_bootstrap
    handle = service._default_begin_capsule(_request())
    frozen = handle.contract.user_config["orchestration_config"]
    assert handle.contract.user_config["ticker"] == "600519.SS"
    assert "ticker" not in frozen
    assert frozen["agents"] == config["agents"]
    path.write_text("{}")

    spec, tier, resolution = resolve_agent_spec(handle, "l4_card")

    assert spec == frozen["resolved_agent_bundle"]["roles"]["l4_card"]
    assert spec["model"]
    assert tier == config["agents"]["l4_card"]["tier"]
    assert resolution == "FROZEN_RUN_CONTRACT"


def test_stock_runtime_loader_uses_only_frozen_orchestration(stock_bootstrap, monkeypatch, capsys):
    path, config = stock_bootstrap
    handle = service._default_begin_capsule(_request())
    monkeypatch.setenv("AUTORESEARCH_RUN_ID", handle.run_id)
    monkeypatch.setenv("AUTORESEARCH_RUN_KIND", "stock-research")
    path.write_text("{}")

    assert user_config.load_user_config() == config
    from autoresearch.session_agent.config import session_cfg
    assert session_cfg()["timeouts"]["mailbox"]["stock.card"] == 1234
    assert "未知" not in capsys.readouterr().err


def test_stock_claim_dispatch_retains_frozen_model_and_timeout(stock_bootstrap):
    path, _ = stock_bootstrap
    handle = service._default_begin_capsule(_request())
    handle.staging.mkdir()
    (handle.capsule / "events").mkdir(parents=True)
    service.begin(_request(), begin_capsule=lambda request: handle, planner=_planner)
    artifacts.register_artifact(handle, "step.one.output", handle.staging / "one.txt", "WRITE")
    artifacts.register_artifact(handle, "step.two.output", handle.staging / "two.md", "WRITE")
    service.claim(handle.run_id, "step.one", 1, handle_loader=lambda _: handle)

    def runner(*args, **kwargs):
        (handle.staging / "one.txt").write_text("deterministic")
        return SimpleNamespace(exit_code=0, invocation={"status": "COMPLETED"})

    service.execute(handle.run_id, "step.one", 1, {"message": "ok"},
                    handle_loader=lambda _: handle, runner=runner)
    path.write_text("{}")
    claimed = service.claim(handle.run_id, "step.two", 1, handle_loader=lambda _: handle,
                            event_recorder=lambda *args, **kwargs: None)
    dispatch = claimed["result"]["dispatch_request"]

    frozen = handle.contract.user_config["orchestration_config"]["resolved_agents"]["l4_card"]
    assert dispatch["agent_spec"] == frozen
    assert dispatch["model"] == frozen["model"]
    assert dispatch["resolution"] == "FROZEN_RUN_CONTRACT"
    assert dispatch["timeout_seconds"] == 1234


@pytest.mark.parametrize("invalid", [{}, {"unexpected_stock_parameter": True}])
def test_stock_begin_rejects_invalid_dispatch_config_before_allocation(stock_bootstrap, invalid):
    path, _ = stock_bootstrap
    path.write_text(json.dumps(invalid))
    with pytest.raises(ValueError):
        service._default_begin_capsule(_request())
    assert not list(ws.context_root().glob("analyze_runs/*"))
