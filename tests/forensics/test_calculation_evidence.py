from __future__ import annotations

import json
from types import SimpleNamespace

from autoresearch.news.material_claims import bind_material_claim, verify_material_claims
from autoresearch.research.calculations import (
    calculate,
    persist_calculation,
    replay_calculation,
)
from autoresearch.trace.blobs import put_bytes


def _money(value: str, *, basis="single_period", currency="CNY", unit="million"):
    return {
        "value": value,
        "period_basis": basis,
        "currency": currency,
        "unit": unit,
    }


def test_period_ratio_has_replayable_inputs_and_code_identity():
    inputs = {
        "revenue": _money("100"),
        "profit": _money("9"),
    }
    parameters = {
        "period_start": "2026-01-01",
        "period_end": "2026-03-31",
        "shares_basis": None,
    }

    result = calculate("financial_period_ratios.v1", inputs, parameters)

    assert result["status"] == "SUCCEEDED"
    assert result["values"]["net_margin"] == "0.09"
    assert result["input_refs"] and len(result["code_hash"]) == 64
    assert replay_calculation(result, inputs, parameters)["values"] == result["values"]


def test_period_ratio_rejects_cumulative_single_period_and_currency_mixing():
    result = calculate(
        "financial_period_ratios.v1",
        {
            "revenue": _money("100", basis="cumulative"),
            "profit": _money("9", basis="single_period"),
        },
        {
            "period_start": "2026-01-01",
            "period_end": "2026-03-31",
            "shares_basis": None,
        },
    )
    assert result["status"] == "FAILED"
    assert result["error"]["category"] == "CalculationInputError"

    currency = calculate(
        "financial_period_ratios.v1",
        {
            "revenue": _money("100", currency="CNY"),
            "profit": _money("9", currency="USD"),
        },
        {
            "period_start": "2026-01-01",
            "period_end": "2026-03-31",
            "shares_basis": None,
        },
    )
    assert currency["status"] == "FAILED"
    assert "currency" in currency["error"]["message"]


def test_ah_premium_requires_comparable_quote_times_and_share_basis():
    inputs = {
        "a_price": {
            "value": "10",
            "currency": "CNY",
            "as_of": "2026-09-14T08:00:00+08:00",
            "share_basis": "ordinary_share",
        },
        "h_price": {
            "value": "8",
            "currency": "HKD",
            "as_of": "2026-09-14T08:00:30+08:00",
            "share_basis": "ordinary_share",
        },
        "fx": {
            "value": "0.9",
            "pair": "HKD/CNY",
            "as_of": "2026-09-14T08:00:10+08:00",
        },
    }
    result = calculate(
        "ah_premium.v1", inputs, {"max_time_skew_seconds": 60}
    )
    assert result["values"]["premium"] == "0.388888888888888888888888889"

    stale = {
        **inputs,
        "h_price": {**inputs["h_price"], "as_of": "2026-09-14T09:00:00+08:00"},
    }
    assert calculate(
        "ah_premium.v1", stale, {"max_time_skew_seconds": 60}
    )["status"] == "FAILED"


def test_conditional_base_rate_discloses_window_and_overlap_policy():
    result = calculate(
        "conditional_base_rates.v1",
        {
            "observations": [
                {"observation_id": "a", "date": "2026-01-02", "condition": True, "outcome": True},
                {"observation_id": "b", "date": "2026-01-03", "condition": True, "outcome": False},
                {"observation_id": "c", "date": "2026-01-04", "condition": False, "outcome": True},
            ]
        },
        {
            "window_start": "2026-01-01",
            "window_end": "2026-01-31",
            "overlap_policy": "disjoint_observations",
        },
    )
    assert result["values"] == {
        "n_total": 3,
        "n_condition": 2,
        "n_outcome": 2,
        "n_condition_and_outcome": 1,
        "base_rate": "0.6666666666666666666666666667",
        "conditional_rate": "0.5",
    }
    assert result["assumptions"]["overlap_policy"] == "disjoint_observations"


def test_dcf_reuses_registered_uzi_calculator_and_replays():
    inputs = {
        "fcf_base": _money("100"),
        "net_debt": _money("0"),
        "shares": {
            "value": "100",
            "unit": "million_shares",
            "shares_basis": "diluted_weighted_average",
        },
    }
    parameters = {
        "waccs": ["0.08", "0.10"],
        "growths": ["0.06", "0.10"],
        "terminal_growth": "0.03",
        "years": 5,
    }
    result = calculate("dcf_sensitivity.v1", inputs, parameters)
    assert result["status"] == "SUCCEEDED"
    assert result["values"]["matrix"][0][0] is not None
    assert replay_calculation(result, inputs, parameters)["calculation_id"] == result["calculation_id"]


def test_persisted_calculation_and_quote_bind_a_material_claim(tmp_path):
    capsule = tmp_path / "capsule"
    capsule.mkdir()
    handle = SimpleNamespace(
        capsule=capsule,
        engine="codex",
        run_id="20260914T120000000000Z",
    )
    result = calculate(
        "financial_period_ratios.v1",
        {"revenue": _money("100"), "profit": _money("9")},
        {
            "period_start": "2026-01-01",
            "period_end": "2026-03-31",
            "shares_basis": None,
        },
        task_id="stock.fundamentals",
        attempt=1,
    )
    calculation_path = persist_calculation(handle, result)
    source = "issuer filing says quarterly profit was CNY 9m"
    blob_hash = put_bytes(capsule, source.encode("utf-8"))
    quote = source.index("profit")

    sidecar = bind_material_claim(
        capsule,
        engine="codex",
        run_id=handle.run_id,
        task_id="stock.fundamentals",
        attempt=1,
        claim_id="profit-margin",
        statement="quarterly net margin was 9%",
        source_receipt_ids=[],
        calculation_ids=[result["calculation_id"]],
        quote_refs=[{
            "blob_hash": blob_hash,
            "start": quote,
            "end": quote + len("profit"),
            "text": "profit",
        }],
    )

    assert calculation_path.is_file()
    assert sidecar["calculation_ids"] == [result["calculation_id"]]
    assert verify_material_claims(capsule)["ok"] is True
    calculation_path.unlink()
    assert verify_material_claims(capsule)["ok"] is False


def test_calculation_registry_never_accepts_dynamic_code_or_imports():
    try:
        calculate("builtins.eval", {"source": "2 + 2"}, {})
    except KeyError as exc:
        assert "unregistered calculator" in str(exc)
    else:
        raise AssertionError("dynamic calculator unexpectedly accepted")


def test_calculation_result_is_canonical_json_serializable():
    result = calculate(
        "financial_period_ratios.v1",
        {"revenue": _money("100"), "profit": _money("9")},
        {
            "period_start": "2026-01-01",
            "period_end": "2026-03-31",
            "shares_basis": None,
        },
    )
    json.dumps(result, sort_keys=True)


def test_session_calculation_is_bounded_to_a_running_parent_attempt(tmp_path):
    from autoresearch.common.atomic import canonical_json, sha256_bytes
    from autoresearch.contracts.session_plan import plan_hash
    from autoresearch.session_agent import artifacts, service, store
    from autoresearch.session_agent.roles import roles_hash
    from tests.session_agent.test_service import _handle, _request

    handle = _handle(tmp_path)
    input_path = handle.staging / "financials.json"
    input_path.write_text(
        json.dumps({
            "revenue": _money("100"),
            "profit": _money("9"),
        }),
        encoding="utf-8",
    )
    output_path = handle.staging / "answer.md"

    def planner(request, current):
        task = {
            "task_id": "stock.fundamentals",
            "kind": "INFERENCE",
            "role": "stock.fundamentals",
            "operation": None,
            "dependencies": [],
            "input_artifact_ids": ["stock.financials"],
            "output_artifact_ids": ["stock.answer"],
            "expected_output_contract": "stock.section.v1",
            "owner": "SESSION",
            "subject": "600519.SS",
            "independent_context": False,
            "parent_task": None,
        }
        value = {
            "schema_version": 1,
            "engine": current.engine,
            "run_id": current.run_id,
            "run_kind": request["kind"],
            "requested_mode": request["requested_mode"],
            "analysis_date": request["analysis_date"],
            "orchestration_version": "session_v1",
            "input_contract_hash": current.contract.contract_hash,
            "config_hash": sha256_bytes(canonical_json({}).encode()),
            "host_profile_hash": sha256_bytes(
                canonical_json(request["host_profile"]).encode()
            ),
            "roles_hash": roles_hash(),
            "tasks": [task],
            "task_templates": [],
            "plan_hash": "0" * 64,
        }
        value["plan_hash"] = plan_hash(value)
        return value

    def register(request, current, plan):
        artifacts.register_artifact(current, "stock.financials", input_path, "READ")
        artifacts.register_artifact(current, "stock.answer", output_path, "WRITE")

    service.begin(
        _request(),
        begin_capsule=lambda unused: handle,
        planner=planner,
        artifact_registrar=register,
    )
    service.claim(
        handle.run_id,
        "stock.fundamentals",
        1,
        handle_loader=lambda unused: handle,
        event_recorder=lambda *args, **kwargs: None,
    )
    result = service.calculate(
        handle.run_id,
        "stock.fundamentals",
        1,
        {
            "calculator_id": "financial_period_ratios.v1",
            "input_artifact_ids": ["stock.financials"],
            "parameters": {
                "period_start": "2026-01-01",
                "period_end": "2026-03-31",
                "shares_basis": None,
            },
        },
        handle_loader=lambda unused: handle,
    )

    assert result["result"]["values"]["net_margin"] == "0.09"
    assert store.read_entry(
        handle.workspace / "session/tasks.json", "stock.fundamentals"
    )["state"] == "RUNNING"
    calculation_id = result["result"]["calculation_id"]
    assert (
        handle.capsule / f"evidence/calculations/{calculation_id}.json"
    ).is_file()
