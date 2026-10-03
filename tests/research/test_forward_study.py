import json
import subprocess
from datetime import datetime, timedelta, timezone

import pytest

from autoresearch.research import forward_study as fs
from tests.research.test_experiment_io import spec


@pytest.fixture
def setup(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (
        ["init", "-q"],
        ["config", "user.email", "fixture@example.test"],
        ["config", "user.name", "fixture"],
    ):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    (repo / "prompt").write_text("frozen prompt")
    (repo / "config").write_text("frozen config")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=repo, check=True)
    start = datetime(2026, 9, 1, tzinfo=timezone.utc)
    calendar = {
        "source": "fixture:exchange-calendar-export",
        "sessions": [
            {
                "date": (start + timedelta(days=i)).date().isoformat(),
                "open_at": (start + timedelta(days=i, hours=1)).isoformat(),
                "decision_cutoff": (start + timedelta(days=i, hours=14)).isoformat(),
                "label_available_at": (start + timedelta(days=i, hours=2)).isoformat(),
            }
            for i in range(64)
        ],
    }
    value = spec(
        engine="codex",
        experiment_id="FORWARD",
        split={
            "train": ["2022-01-01", "2025-01-01"],
            "validation": ["2025-01-01", "2026-09-01"],
            "test": ["2026-09-01", "2027-01-01"],
        },
        maturity_policy="scan_days >= 60",
    )
    for name in ("code_sha", "prompt_hashes", "input_manifest_hash", "created_at"):
        value.pop(name)
    monkeypatch.setattr(fs, "_now", lambda: start - timedelta(days=1))
    kwargs = {
        "parent": tmp_path / "studies",
        "template": value,
        "calendar": calendar,
        "start_date": "2026-09-01",
        "prompt_paths": {"card": repo / "prompt"},
        "config_paths": {"main": repo / "config"},
        "repo_root": repo,
    }
    return kwargs, start


def test_forward_two_freezes_use_real_code_and_do_not_prefill_inputs(setup, monkeypatch, tmp_path):
    kwargs, start = setup
    directory = fs.begin(**kwargs)
    protocol = fs.read_protocol(directory)
    assert len(protocol["days"]) == 60
    assert protocol["days"][0]["exit_date"] == "2026-09-03"
    assert "input_manifest_hash" not in protocol["template"]
    with pytest.raises(FileExistsError):
        fs.begin(**kwargs)
    inputs = tmp_path / "inputs.csv"
    labels = []
    for day in protocol["days"]:
        now = datetime.fromisoformat(day["decision_cutoff"]) - timedelta(hours=1)
        monkeypatch.setattr(fs, "_now", lambda now=now: now)
        inputs.write_text(
            "analysis_date,code,baseline,refined\n" + day["date"] + ",600000,True,False\n"
        )
        fs.freeze_day(
            directory,
            day["date"],
            [{"artifact_id": "population", "path": str(inputs), "available_at": now.isoformat()}],
        )
        target = tmp_path / (day["date"] + ".csv")
        import pandas as pd

        from autoresearch.common.outcome_sessions import calendar_digest
        price_inputs = {}
        for session in (day['entry_date'], day['exit_date']):
            price_path = tmp_path / (session + '.parquet')
            pd.DataFrame([{'ts_code': '600000.SH', 'trade_date': session.replace('-', ''), 'open': 10.1, 'close': 10.0}]).to_parquet(price_path)
            price_inputs[session.replace('-', '')] = str(price_path)
        pd.DataFrame([{"analysis_date": day['date'], "code": '600000', "baseline": True, "refined": False,
            "gap_c1_o2": .01, "label_version": 'outcome_labels.v2', "venue": 'CN_A',
            "calendar_quality": 'trade_cal', "calendar_digest": calendar_digest(
                [s['date'].replace('-', '') for s in protocol['calendar']['sessions']], 'trade_cal'),
            "entry_session_gap_c1_o2": day['entry_date'].replace('-', ''),
            "exit_session_gap_c1_o2": day['exit_date'].replace('-', ''), "status_gap_c1_o2": 'MATURE',
            "price_input_hashes_gap_c1_o2": json.dumps({d: fs.sha256_file(p) for d,p in price_inputs.items()})
        }]).to_csv(target, index=False)
        labels.append(
            {"date": day["date"], "path": str(target), "available_at": day["label_due_at"], "price_inputs": price_inputs}
        )
    monkeypatch.setattr(fs, "_now", lambda: start + timedelta(days=62))
    first = __import__("pathlib").Path(labels[0]["path"])
    original = first.read_text()
    for bad in [
        original.splitlines()[0] + "\n",
        original.replace("600000", "600001"),
        original.replace("True,False", "False,True"),
    ]:
        first.write_text(bad)
        with pytest.raises(ValueError, match="population|selection"):
            fs.finalize(directory, labels)
    first.write_text(original)
    result = fs.finalize(directory, labels)
    final = json.loads(result.read_text())
    assert final["code_sha"] == protocol["code_provenance"]["declared"]
    assert final["created_at"] != protocol["registered_at"]
    manifest = json.loads((directory / "final" / "manifest.json").read_text())
    assert manifest["protocol_sha256"] == fs.protocol_digest(directory)
    assert len(manifest["daily_inputs"]) == 60
    assert len(manifest["price_inputs"]) == 120
    assert all(fs.sha256_file(directory / "final" / ref["path"]) == ref["sha256"] for ref in manifest["price_inputs"])


def test_dirty_provenance_and_unknown_calendar_day_refused(setup):
    kwargs, _ = setup
    with pytest.raises(ValueError, match="calendar"):
        fs.begin(**{**kwargs, "start_date": "2026-08-30"})
    (kwargs["repo_root"] / "config").write_text("dirty")
    with pytest.raises(ValueError, match="dirty"):
        fs.begin(**kwargs)


def test_day_future_input_duplicate_and_protocol_tamper_fail(setup, monkeypatch, tmp_path):
    kwargs, start = setup
    directory = fs.begin(**kwargs)
    monkeypatch.setattr(fs, "_now", lambda: start + timedelta(hours=12))
    source = tmp_path / "input"
    source.write_text("input")
    row = {
        "artifact_id": "x",
        "path": str(source),
        "available_at": (start + timedelta(hours=13)).isoformat(),
    }
    with pytest.raises(ValueError, match="future"):
        fs.freeze_day(directory, "2026-09-01", [row])
    row["available_at"] = (start + timedelta(hours=11)).isoformat()
    fs.freeze_day(directory, "2026-09-01", [row])
    with pytest.raises(FileExistsError):
        fs.freeze_day(directory, "2026-09-01", [row])
    path = directory / "protocol.json"
    value = json.loads(path.read_text())
    value["template"]["baseline"] = "changed"
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="protocol"):
        fs.freeze_day(directory, "2026-09-02", [row])


def test_abort_keeps_attempts_and_does_not_extend_window(setup):
    kwargs, _ = setup
    directory = fs.begin(**kwargs)
    fs.abort(directory, "source contract failure")
    with pytest.raises(ValueError, match="aborted"):
        fs.finalize(directory, [])
    events = [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]
    assert any(row["event"] == "ABORTED" for row in events)
    assert len(fs.read_protocol(directory)["days"]) == 60


def test_daily_archive_tamper_is_rejected_and_unmatured_finalization_is_recorded(
    setup, monkeypatch, tmp_path
):
    kwargs, start = setup
    directory = fs.begin(**kwargs)
    monkeypatch.setattr(fs, "_now", lambda: start + timedelta(hours=12))
    source = tmp_path / "input"
    source.write_text("before")
    fs.freeze_day(
        directory,
        "2026-09-01",
        [
            {
                "artifact_id": "x",
                "path": str(source),
                "available_at": (start + timedelta(hours=11)).isoformat(),
            }
        ],
    )
    with pytest.raises(ValueError, match="mature"):
        fs.finalize(directory, [])
    (directory / "days" / "2026-09-01" / "0000.bin").write_text("tamper")
    monkeypatch.setattr(fs, "_now", lambda: start + timedelta(days=65))
    with pytest.raises(ValueError, match="archive changed"):
        fs.finalize(directory, [])


def test_code_drift_after_registration_prevents_daily_decision_capture(
    setup, monkeypatch, tmp_path
):
    kwargs, start = setup
    directory = fs.begin(**kwargs)
    monkeypatch.setattr(fs, "_now", lambda: start + timedelta(hours=12))
    (kwargs["repo_root"] / "config").write_text("changed after freeze")
    source = tmp_path / "input"
    source.write_text("data")
    with pytest.raises(ValueError, match="dirty"):
        fs.freeze_day(
            directory,
            "2026-09-01",
            [{"artifact_id": "x", "path": str(source), "available_at": start.isoformat()}],
        )


def test_daily_cutoff_duplicate_paths_and_symlink_rejected(setup, monkeypatch, tmp_path):
    kwargs, start = setup
    directory = fs.begin(**kwargs)
    monkeypatch.setattr(fs, "_now", lambda: start + timedelta(hours=12))
    source = tmp_path / "input"
    source.write_text("data")
    row = {"artifact_id": "x", "path": str(source), "available_at": start.isoformat()}
    with pytest.raises(ValueError, match="duplicate"):
        fs.freeze_day(directory, "2026-09-01", [row, {**row, "artifact_id": "y"}])
    link = tmp_path / "link"
    link.symlink_to(source)
    with pytest.raises(ValueError, match="symlink"):
        fs.freeze_day(directory, "2026-09-01", [{**row, "path": str(link)}])
    monkeypatch.setattr(fs, "_now", lambda: start + timedelta(hours=14))
    with pytest.raises(ValueError, match="cutoff"):
        fs.freeze_day(directory, "2026-09-01", [row])


def test_evaluation_file_cannot_claim_another_analysis_day(tmp_path):
    tmp_path / "labels.csv"
    content = b"analysis_date,code,gap_c1_o2\n2026-09-02,600000,.1\n"
    with pytest.raises(ValueError, match="analysis date"):
        fs._validate_labels(content, suffix=".csv", analysis_date="2026-09-01")


def test_begin_rechecks_real_clock_after_provenance_capture(setup, monkeypatch):
    kwargs, start = setup
    verify = fs.verify_code_provenance

    def delayed(*args, **kw):
        result = verify(*args, **kw)
        monkeypatch.setattr(fs, "_now", lambda: start + timedelta(hours=14, seconds=1))
        return result

    monkeypatch.setattr(fs, "verify_code_provenance", delayed)
    with pytest.raises(ValueError, match="cutoff"):
        fs.begin(**kwargs)


def test_day_rechecks_real_clock_after_input_capture(setup, monkeypatch, tmp_path):
    kwargs, start = setup
    directory = fs.begin(**kwargs)
    monkeypatch.setattr(fs, "_now", lambda: start + timedelta(hours=13, minutes=59))
    capture = fs._capture_inputs

    def delayed(*args, **kw):
        result = capture(*args, **kw)
        monkeypatch.setattr(fs, "_now", lambda: start + timedelta(hours=14, seconds=1))
        return result

    monkeypatch.setattr(fs, "_capture_inputs", delayed)
    source = tmp_path / "input"
    source.write_text("data")
    with pytest.raises(ValueError, match="cutoff"):
        fs.freeze_day(
            directory,
            "2026-09-01",
            [{"artifact_id": "x", "path": str(source), "available_at": start.isoformat()}],
        )
    assert not (directory / "days" / "2026-09-01" / "manifest.json").exists()


def test_missing_label_requires_explicit_unknown_without_dropping_population():
    population = fs._frozen_population(
        b"analysis_date,code,baseline,refined\n2026-09-01,600000,True,False\n",
        suffix=".csv",
        analysis_date="2026-09-01",
    )
    content = b"analysis_date,code,baseline,refined,gap_c1_o2\n2026-09-01,600000,True,False,\n"
    with pytest.raises(ValueError, match="explicitly retain UNKNOWN"):
        fs._validate_labels(
            content, suffix=".csv", analysis_date="2026-09-01", population=population
        )
    explicit = b"analysis_date,code,baseline,refined,gap_c1_o2,status_gap_c1_o2\n2026-09-01,600000,True,False,,UNKNOWN\n"
    fs._validate_labels(explicit, suffix=".csv", analysis_date="2026-09-01", population=population)


def test_label_lineage_requires_exact_sessions_and_bytes(tmp_path):
    import pandas as pd

    from autoresearch.common.outcome_sessions import calendar_digest
    sessions = ['20260901', '20260902', '20260903']
    paths = {}
    for day in sessions[1:]:
        path = tmp_path / (day + '.parquet')
        pd.DataFrame([{'ts_code': '600000.SH', 'trade_date': day, 'open': 10.1, 'close': 10.0}]).to_parquet(path)
        paths[day] = str(path)
    hashes = {day: fs.sha256_file(path) for day, path in paths.items()}
    row = {"analysis_date": '2026-09-01', "code": '600000', "gap_c1_o2": .01,
        "label_version": 'outcome_labels.v2', "venue": 'CN_A', "calendar_quality": 'trade_cal',
        "calendar_digest": calendar_digest(sessions, 'trade_cal'),
        "entry_session_gap_c1_o2": sessions[1], "exit_session_gap_c1_o2": sessions[2],
        "price_input_hashes_gap_c1_o2": json.dumps(hashes), "status_gap_c1_o2": 'MATURE'}
    def validate(value):
        return fs._validate_labels(pd.DataFrame([value]).to_csv(index=False).encode(), suffix='.csv',
            analysis_date='2026-09-01', expected_sessions=sessions, price_inputs=paths)
    validate(row)
    for change in ({'entry_session_gap_c1_o2': sessions[2]}, {'calendar_digest': '0'*64},
                   {'price_input_hashes_gap_c1_o2': '{}'}, {'gap_c1_o2': .04},
                   {'label_version': 'legacy'}):
        with pytest.raises(ValueError, match='label|price|calendar'):
            validate({**row, **change})


def test_price_source_requires_actual_trade_date(tmp_path):
    import pandas as pd

    from autoresearch.common.outcome_sessions import calendar_digest
    sessions = ['20260901','20260902','20260903']
    paths = {}
    for day in sessions[1:]:
        path = tmp_path / (day+'.parquet')
        pd.DataFrame([{'ts_code':'600000.SH','open':10.1,'close':10.0}]).to_parquet(path)
        paths[day] = str(path)
    row = {"analysis_date": '20260901',"code": '600000',"gap_c1_o2": .01,
        "label_version": 'outcome_labels.v2',"venue": 'CN_A',"calendar_quality": 'trade_cal',
        "calendar_digest": calendar_digest(sessions,'trade_cal'),"entry_session_gap_c1_o2": sessions[1],
        "exit_session_gap_c1_o2": sessions[2],"status_gap_c1_o2": 'MATURE',
        "price_input_hashes_gap_c1_o2": json.dumps({d:fs.sha256_file(p) for d,p in paths.items()})}
    with pytest.raises(ValueError, match='price.*session'):
        fs._validate_labels(pd.DataFrame([row]).to_csv(index=False).encode(), suffix='.csv',
            analysis_date='20260901',expected_sessions=sessions,price_inputs=paths)


def test_probability_candidate_is_frozen_before_entry_not_backdated(setup, monkeypatch, tmp_path):
    """Synthetic declared p is an actual frozen input, not a label-time field."""
    from autoresearch.research.execution_ledger import execution_plan_hash
    from autoresearch.research.probability_eval import PLANNED_OVERNIGHT_EVENT
    from tests.research.test_execution_ledger import plan

    kwargs, start = setup
    directory = fs.begin(**kwargs)
    now = start.replace(hour=1)
    monkeypatch.setattr(fs, "_now", lambda: now)
    value = {
        "schema_version": 1,
        "status": "SELECTED",
        "candidates": [
            {
                "run_id": "synthetic-run",
                "plan": plan(),
                "declaration": {
                    "event_id": PLANNED_OVERNIGHT_EVENT,
                    "p": 0.8,
                    "declared_at": now.isoformat(),
                    "plan_hash": execution_plan_hash(plan()),
                },
                "qty": "100",
                "weight": "0.1",
                "sector": "银行",
            }
        ],
    }
    path = tmp_path / "candidates.json"
    path.write_text(json.dumps(value))
    # Plan belongs to analysis 08-31, not the frozen study day 09-01.
    with pytest.raises(ValueError, match="analysis"):
        fs.freeze_day(
            directory,
            "2026-09-01",
            [
                {
                    "artifact_id": "execution.candidates",
                    "path": str(path),
                    "available_at": now.isoformat(),
                }
            ],
        )
    value["candidates"][0]["plan"]["analysis_session"] = "2026-09-01"
    value["candidates"][0]["declaration"]["plan_hash"] = execution_plan_hash(
        value["candidates"][0]["plan"]
    )
    monkeypatch.setattr(fs, "_now", lambda: start.replace(hour=8))  # after actual 14:59 +08 entry
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="entry"):
        fs.freeze_day(
            directory,
            "2026-09-01",
            [
                {
                    "artifact_id": "execution.candidates",
                    "path": str(path),
                    "available_at": now.isoformat(),
                }
            ],
        )


def test_real_freeze_to_execution_audit_probability_uses_archived_candidate(
    setup, monkeypatch, tmp_path
):
    """Actual filesystem protocol/candidate freezes, exclusively synthetic fills."""
    import csv
    from copy import deepcopy

    from autoresearch.research import execution_audit as audit, execution_import as imp
    from autoresearch.research.execution_ledger import execution_plan_hash
    from autoresearch.research.probability_eval import (
        PLANNED_OVERNIGHT_EVENT,
        execution_sizing_hash,
    )
    from tests.research.test_execution_audit import POLICY, TRADE_COLS
    from tests.research.test_execution_day_panel import policy
    from tests.research.test_execution_ledger import overnight_fills, plan

    kwargs, start = setup
    rules = policy()
    rules.update(
        account_hash=imp.account_hash("synthetic"), cost_model_version=POLICY["cost_model_version"]
    )
    repo = kwargs["repo_root"]
    portfolio = repo / "portfolio.json"
    portfolio.write_text(json.dumps(rules))
    frozen_cost=repo/"execution_cost.json"
    frozen_cost.write_text(json.dumps(POLICY))
    kwargs["config_paths"]["execution_cost"]=frozen_cost
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "synthetic portfolio rules"], cwd=repo, check=True)
    kwargs["config_paths"]["execution_portfolio"] = portfolio
    kwargs["template"].update(
        evidence_mode="OBSERVED_FILL", cost_model_version=POLICY["cost_model_version"]
    )
    directory = fs.begin(**kwargs)
    frozen_plan = deepcopy(plan())
    frozen_plan["analysis_session"] = "2026-09-01"
    frozen_plan["cost_model_version"] = POLICY["cost_model_version"]
    for key in ("entry_window_start", "entry_window_end", "exit_window_start", "exit_window_end"):
        frozen_plan[key] = (
            datetime.fromisoformat(frozen_plan[key]) + timedelta(days=1)
        ).isoformat()
    now = start.replace(hour=1)
    declaration = {
        "event_id": PLANNED_OVERNIGHT_EVENT,
        "p": 0.8,
        "declared_at": now.isoformat(),
        "plan_hash": execution_plan_hash(frozen_plan),
        "sizing_hash": execution_sizing_hash(frozen_plan,qty="100",weight=".2"),
    }
    candidate = {
        "schema_version": 1,
        "status": "SELECTED",
        "candidates": [
            {
                "run_id": "synthetic-run",
                "plan": frozen_plan,
                "declaration": declaration,
                "qty": "100",
                "weight": ".2",
                "sector": "银行",
            }
        ],
    }
    source = tmp_path / "candidate.json"
    source.write_text(json.dumps(candidate))
    monkeypatch.setattr(fs, "_now", lambda: now)
    fs.freeze_day(
        directory,
        "2026-09-01",
        [
            {
                "artifact_id": "execution.candidates",
                "path": str(source),
                "available_at": now.isoformat(),
            }
        ],
    )
    source.write_text("changed after freeze")  # captured bytes, never reread mutable source
    trades = tmp_path / "synthetic-trades.csv"
    with trades.open("w") as stream:
        writer = csv.DictWriter(stream, fieldnames=TRADE_COLS)
        writer.writeheader()
        for f in overnight_fills():
            f["trade_date"] = (
                (datetime.fromisoformat(f["trade_date"]) + timedelta(days=1)).date().isoformat()
            )
            writer.writerow(
                {
                    **{k: f.get(k, "") for k in TRADE_COLS},
                    "account": "synthetic",
                    "trade_id": f["fill_id"],
                    "source_kind": "synthetic",
                    "source_file": "fixture.csv",
                }
            )
    cost = tmp_path / "cost.json"
    cost.write_text(json.dumps(POLICY))
    output = audit.run(
        experiment_id="synthetic-integration",
        snapshots=None,
        trades=trades,
        policy=cost,
        runs_root=None,
        lake_daily=None,
        max_age_seconds=60,
        parent=tmp_path / "result",
        engine="codex",
        forward_study=directory,
    )
    result = json.loads((output / "execution_day_panel.json").read_bytes())
    assert result["probability_rows"][0]["y"] == 1
    assert result["probability"]["brier"] == pytest.approx(0.04)
    assert all(p["summary"]["n_days"] == 60 for p in result["modes"].values())
    assert result["modes"]["OBSERVED_FILL"]["summary"]["total_return"] is None
    assert result["trade_import"]["import_basis"] == "EXPLICIT_IMPORT"


def test_execution_candidate_cannot_change_registered_evidence_mode(setup, monkeypatch, tmp_path):
    from autoresearch.research.execution_ledger import execution_plan_hash
    from autoresearch.research.probability_eval import PLANNED_OVERNIGHT_EVENT
    from tests.research.test_execution_ledger import plan

    kwargs, start = setup
    directory = fs.begin(**kwargs)  # registered EOD_PROXY, no observed mode authorization
    now = start.replace(hour=1)
    monkeypatch.setattr(fs, "_now", lambda: now)
    p = plan()
    p["analysis_session"] = "2026-09-01"
    for key in ("entry_window_start", "entry_window_end", "exit_window_start", "exit_window_end"):
        p[key] = (datetime.fromisoformat(p[key]) + timedelta(days=1)).isoformat()
    value = {
        "schema_version": 1,
        "status": "SELECTED",
        "candidates": [
            {
                "run_id": "synthetic",
                "plan": p,
                "declaration": {
                    "event_id": PLANNED_OVERNIGHT_EVENT,
                    "p": 0.8,
                    "declared_at": now.isoformat(),
                    "plan_hash": execution_plan_hash(p),
                },
                "qty": "100",
                "weight": ".1",
            }
        ],
    }
    path = tmp_path / "candidate.json"
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="registered mode"):
        fs.freeze_day(
            directory,
            "2026-09-01",
            [
                {
                    "artifact_id": "execution.candidates",
                    "path": str(path),
                    "available_at": now.isoformat(),
                }
            ],
        )
