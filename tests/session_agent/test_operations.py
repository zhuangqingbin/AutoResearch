from __future__ import annotations

import pytest

from autoresearch.session_agent.operations import build_argv, operation_spec


def test_noop_and_stock_harvest_have_static_argv_builders():
    assert build_argv("test.noop", {"message": "ok"})[-3:] == ["-c", "print('ok')", "ok"]
    assert build_argv("stock.harvest", {
        "ticker": "600519.SS",
        "analysis_date": "2026-09-13",
        "asset_type": "stock",
        "peers": [],
        "slim": True,
    }) == [
        "uv", "run", "--no-sync", "python", "-m", "autoresearch.analyze.harvest",
        "600519.SS", "2026-09-13", "stock", "", "--slim",
    ]
    assert operation_spec("stock.harvest")["idempotent"] is True


def test_operation_rejects_unknown_keys_unknown_operation_and_hostile_symbol():
    with pytest.raises(KeyError):
        build_argv("shell", {})
    with pytest.raises(ValueError, match="fields"):
        build_argv("test.noop", {"message": "ok", "argv": ["sh"]})
    with pytest.raises(ValueError, match="ticker"):
        build_argv("stock.harvest", {
            "ticker": "600519;touch-pwned",
            "analysis_date": "2026-09-13",
            "asset_type": "stock",
            "peers": [],
            "slim": True,
        })


def test_subject_scoped_scan_operations_get_identity_from_the_frozen_task():
    assert build_argv("scan.l4.slim", {}, subject="600519")[-2:] == [
        "--subject",
        "600519",
    ]
    with pytest.raises(ValueError, match="subject"):
        build_argv("scan.l4.slim", {})


def test_research_calculate_has_a_static_non_executable_argv():
    argv = build_argv(
        "research.calculate",
        {
            "calculator_id": "financial_period_ratios.v1",
            "input_artifact_ids": ["stock.fundamentals.json"],
            "parameters": {"period_start": "2026-01-01"},
        },
    )
    assert argv[:4] == [
        __import__("sys").executable,
        "-m",
        "autoresearch.session_agent.domain_ops",
        "research-calculate",
    ]
    assert "eval" not in " ".join(argv)
    with pytest.raises(KeyError, match="unregistered calculator"):
        build_argv(
            "research.calculate",
            {
                "calculator_id": "builtins.eval",
                "input_artifact_ids": ["stock.fundamentals.json"],
                "parameters": {},
            },
        )
