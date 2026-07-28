"""Wave 5 deterministic CLI and operator report integration."""
from __future__ import annotations

import json

from autoresearch.learning import experiment_registry, promotion, rollback_watch


def _guards(*, rollback=False):
    return {
        "research": [
            {
                "metric": "research.edge",
                "op": "gte" if rollback else "gt",
                "value": -0.01 if rollback else 0.0,
            }
        ],
        "decision": [
            {
                "metric": "decision.false_buy_delta",
                "op": "lte",
                "value": 0.02 if rollback else 0.0,
            }
        ],
        "token": [
            {
                "metric": "token.cost_delta",
                "op": "lte",
                "value": 0.05 if rollback else 0.0,
            }
        ],
        "speed": [
            {
                "metric": "speed.p90_delta",
                "op": "lte",
                "value": 0.05 if rollback else 0.0,
            }
        ],
        "architecture": [
            {"metric": "architecture.failures", "op": "eq", "value": 0}
        ],
    }


def _spec():
    return {
        "id": "exp_cli",
        "title": "CLI challenger",
        "trial_family": "cli-family",
        "definition": {"switch": "cli"},
        "start_date": "2026-07-28",
        "expires_date": "2026-10-31",
        "primary_metric": "research.edge",
        "promotion_guards": _guards(),
        "rollback_guards": _guards(rollback=True),
        "challenger_pointer": {
            "kind": "git",
            "pointer": "git:cli",
            "content_hash": "b" * 64,
        },
        "minimums": {
            "forward_days": 20,
            "mature_events": 10,
            "unique_events": 0,
            "regimes": 2,
        },
        "rollback_window_runs": 2,
    }


def _facts(*, false_buy=-0.01):
    return {
        "sample": {
            "forward_days": 20,
            "mature_events": 10,
            "unique_events": 0,
            "regimes": ["range", "risk_off"],
            "trial_count": 2,
            "definition_breakpoints": [],
        },
        "metrics": {
            "research": {"edge": 0.01},
            "decision": {"false_buy_delta": false_buy},
            "token": {"cost_delta": -0.10},
            "speed": {"p90_delta": -0.10},
            "architecture": {"failures": 0},
        },
    }


def test_registry_promotion_watch_and_report_cli(tmp_path, capsys):
    reg = tmp_path / "registry.json"
    spec = tmp_path / "spec.json"
    facts = tmp_path / "facts.json"
    watch = tmp_path / "watch.json"
    promotion_out = tmp_path / "promotion.json"
    watch_out = tmp_path / "rollback.json"
    report = tmp_path / "experiments.md"
    spec.write_text(json.dumps(_spec()), encoding="utf-8")
    facts.write_text(json.dumps(_facts()), encoding="utf-8")
    watch.write_text(json.dumps(_facts(false_buy=0.03)), encoding="utf-8")

    assert experiment_registry.main(
        [
            "--registry",
            str(reg),
            "baseline",
            "--name",
            "wave4",
            "--pointer",
            "git:6a06bc8",
            "--content-hash",
            "a" * 64,
            "--approved-by",
            "user",
            "--approved-at",
            "2026-07-28T12:00:00+08:00",
        ]
    ) == 0
    json.loads(capsys.readouterr().out)
    assert experiment_registry.main(
        [
            "--registry",
            str(reg),
            "register",
            "--spec",
            str(spec),
            "--registered-at",
            "2026-07-28T12:01:00+08:00",
        ]
    ) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "PREREGISTERED"

    assert promotion.main(
        [
            "--registry",
            str(reg),
            "evaluate",
            "exp_cli",
            "--facts",
            str(facts),
            "--evaluated-at",
            "2026-08-20T12:00:00+08:00",
            "--json-out",
            str(promotion_out),
        ]
    ) == 0
    assert json.loads(capsys.readouterr().out)["recommendation"] == "PROMOTE"

    assert experiment_registry.main(
        [
            "--registry",
            str(reg),
            "approve",
            "exp_cli",
            "--approved-by",
            "user",
            "--approved-at",
            "2026-08-21T12:00:00+08:00",
        ]
    ) == 0
    capsys.readouterr()
    assert experiment_registry.main(
        [
            "--registry",
            str(reg),
            "activate",
            "exp_cli",
            "--activated-by",
            "user",
            "--activated-at",
            "2026-08-22T12:00:00+08:00",
        ]
    ) == 0
    capsys.readouterr()

    assert rollback_watch.main(
        [
            "--registry",
            str(reg),
            "observe",
            "exp_cli",
            "--facts",
            str(watch),
            "--run-id",
            "run-bad",
            "--observed-at",
            "2026-08-23T12:00:00+08:00",
            "--json-out",
            str(watch_out),
        ]
    ) == 0
    assert json.loads(capsys.readouterr().out)["recommendation"] == "ROLLBACK"

    assert experiment_registry.main(
        [
            "--registry",
            str(reg),
            "report",
            "--out",
            str(report),
        ]
    ) == 0
    report_result = json.loads(capsys.readouterr().out)
    text = report.read_text(encoding="utf-8")

    assert report_result == {"path": str(report)}
    assert "# Experiment Governance" in text
    assert "git:6a06bc8" in text
    assert "exp_cli" in text
    assert "ROLLBACK_RECOMMENDED" in text
    assert "research" in text and "decision" in text
    assert "git:6a06bc8" in text
    assert promotion_out.exists() and watch_out.exists()


def test_show_and_list_cli_emit_json(tmp_path, capsys):
    reg = tmp_path / "registry.json"
    experiment_registry.set_stable_baseline(
        reg,
        name="wave4",
        pointer="git:base",
        content_hash="a" * 64,
        approved_by="user",
    )
    experiment_registry.register_experiment(reg, _spec())

    assert experiment_registry.main(
        ["--registry", str(reg), "show", "exp_cli"]
    ) == 0
    assert json.loads(capsys.readouterr().out)["id"] == "exp_cli"
    assert experiment_registry.main(["--registry", str(reg), "list"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["id"] == "exp_cli"
