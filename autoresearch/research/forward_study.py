"""Two exclusive freezes for a prospective 60-session observational study.

The calendar is an explicit sourced export, never inferred from weekdays. The local
clock records actual registration and input capture; only tests replace that clock.
No experiment is started merely by importing this module.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import subprocess
from datetime import date, datetime, timezone
from pathlib import Path

from autoresearch.common.atomic import canonical_json, sha256_bytes, sha256_file
from autoresearch.contracts.research_experiment import REQUIRED_SPEC, validate_spec
from autoresearch.research.experiment_io import create_experiment_dir, freeze_validated_spec
from autoresearch.research.registration import (
    file_manifest,
    manifest_digest,
    parse_maturity_policy,
    registered_date_slice,
    verify_code_provenance,
    verify_engine,
    verify_modes,
)

DAYS = 60
_DYNAMIC = {"code_sha", "prompt_hashes", "input_manifest_hash", "created_at"}


def _now():
    return datetime.now(timezone.utc)


def _timestamp(value):
    try:
        stamp = datetime.fromisoformat(value)
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            raise ValueError("timezone required")
        return stamp
    except (ValueError, TypeError) as exc:
        raise ValueError("valid timezone-aware timestamp required") from exc


def _day(value):
    try:
        if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
            raise ValueError("noncanonical date")
    except (ValueError, TypeError) as exc:
        raise ValueError("valid ISO date required") from exc
    return value


def _safe(path):
    path = Path(path).absolute()
    from autoresearch.common import workspace as ws

    other = "claude" if ws.ENGINE == "codex" else "codex"
    if any(part in {f"context_{other}", f"reports_{other}"} for part in path.parts):
        raise ValueError("another engine output path forbidden")
    if path.resolve() != path:
        raise ValueError("symlink or noncanonical path forbidden")
    return path


def _write(path, value):
    with _safe(path).open("xb") as stream:
        stream.write(canonical_json(value).encode())
        stream.flush()
        os.fsync(stream.fileno())


def _event(directory, event, **fields):
    path = _safe(Path(directory) / "events.jsonl")
    with path.open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        stream.write(canonical_json({"event": event, "at": _now().isoformat(), **fields}) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def protocol_digest(directory):
    return sha256_file(_safe(Path(directory) / "protocol.json"))


def read_protocol(directory):
    root = _safe(directory)
    value = json.loads((root / "protocol.json").read_text())
    receipt = json.loads((root / "registration.json").read_text())
    if protocol_digest(root) != receipt["protocol_sha256"]:
        raise ValueError("frozen protocol changed")
    return value


def _active(directory):
    value = read_protocol(directory)
    verify_code_provenance(
        {"code_sha": value["code_provenance"]["declared"]}, ["."], repo_root=value["repo_root"]
    )
    for sources in (value["prompt_files"], value["config_files"]):
        if any(sha256_file(_safe(row["path"])) != row["sha256"] for row in sources.values()):
            raise ValueError("frozen prompt/config changed")
    events = [
        json.loads(row) for row in _safe(Path(directory) / "events.jsonl").read_text().splitlines()
    ]
    if not any(row["event"] == "REGISTERED" for row in events):
        raise ValueError("registration never completed before cutoff")
    if any(row["event"] == "ABORTED" for row in events):
        raise ValueError("study aborted; start a new protocol without extending this window")
    if (Path(directory) / "final").exists():
        raise FileExistsError("final freeze already attempted")
    return value


def _source_files(paths, repo_root):
    if not isinstance(paths, dict) or not paths:
        raise ValueError("actual prompt/config files required")
    rows = {}
    for name, path in paths.items():
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
            raise ValueError("invalid source name")
        source = _safe(path)
        if not source.is_relative_to(repo_root) or not source.is_file():
            raise ValueError("prompt/config must belong to clean code repository")
        rows[name] = {"path": str(source), "sha256": sha256_file(source)}
    return rows


def begin(*, parent, template, calendar, start_date, prompt_paths, config_paths, repo_root="."):
    """Freeze protocol before the first decision; excludes unknown future input hashes."""
    if not isinstance(template, dict) or set(template) != REQUIRED_SPEC - _DYNAMIC:
        raise ValueError("template must omit dynamic code/prompt/input/time fields")
    now = _now()
    repo = _safe(repo_root)
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    prompts, configs = _source_files(prompt_paths, repo), _source_files(config_paths, repo)
    validation = dict(
        template,
        code_sha=sha,
        prompt_hashes={k: v["sha256"] for k, v in prompts.items()},
        input_manifest_hash="",
        created_at=now.isoformat(),
    )
    validate_spec(validation)
    verify_engine(validation)
    if template["evidence_mode"] in {"SNAPSHOT_SIMULATED", "OBSERVED_FILL"}:
        from autoresearch.research.execution_ledger import validate_portfolio_policy
        if "execution_portfolio" not in configs:
            raise ValueError("execution mode requires preregistered portfolio policy")
        portfolio = validate_portfolio_policy(json.loads(_safe(configs["execution_portfolio"]["path"]).read_bytes()))
        from autoresearch.research.execution_import import load_policy
        if "execution_cost" not in configs:
            raise ValueError("execution mode requires full frozen cost policy")
        frozen_cost = load_policy(_safe(configs["execution_cost"]["path"]))
        if frozen_cost["cost_model_version"] != portfolio["cost_model_version"]:
            raise ValueError("portfolio and full cost policy differ")
        verify_modes(validation,evidence_modes={"SNAPSHOT_SIMULATED","OBSERVED_FILL"},cost_models={portfolio["cost_model_version"]})
    else:
        verify_modes(validation, evidence_modes={"EOD_PROXY", "RETRO_REPLAY"}, cost_models={"none"})
    if parse_maturity_policy(template["maturity_policy"]) != DAYS:
        raise ValueError("forward maturity must require exactly 60 analysis sessions")
    provenance = verify_code_provenance(validation, ["."], repo_root=repo)
    if (
        not isinstance(calendar, dict)
        or set(calendar) != {"source", "sessions"}
        or not calendar["source"]
    ):
        raise ValueError("sourced calendar required")
    sessions = calendar["sessions"]
    dates = []
    for session in sessions:
        dates.append(_day(session["date"]))
        opened, cutoff, available = (
            _timestamp(session[k]) for k in ("open_at", "decision_cutoff", "label_available_at")
        )
        if (
            any(t.date().isoformat() != session["date"] for t in (opened, cutoff, available))
            or not opened <= available <= cutoff
        ):
            raise ValueError("invalid calendar session timestamps")
    if dates != sorted(set(dates)) or _day(start_date) not in dates:
        raise ValueError("invalid or unknown calendar start date")
    index = dates.index(start_date)
    if index + DAYS + 2 > len(dates):
        raise ValueError("calendar does not cover 60 analysis sessions and D2 maturity")
    days = [
        {
            "date": dates[i],
            "decision_cutoff": sessions[i]["decision_cutoff"],
            "entry_date": dates[i + 1],
            "exit_date": dates[i + 2],
            "label_due_at": sessions[i + 2]["label_available_at"],
        }
        for i in range(index, index + DAYS)
    ]
    if now >= _timestamp(days[0]["decision_cutoff"]):
        raise ValueError("protocol must precede first decision cutoff")
    if (
        not template["split"]["test"][0]
        <= days[0]["date"]
        <= days[-1]["date"]
        < template["split"]["test"][1]
    ):
        raise ValueError("forward window outside registered test split")
    now = _now()
    if now >= _timestamp(days[0]["decision_cutoff"]):
        raise ValueError("protocol capture crossed first decision cutoff")
    value = {
        "schema_version": 1,
        "repo_root": str(repo),
        "registered_at": now.isoformat(),
        "template": template,
        "code_provenance": provenance,
        "prompt_files": prompts,
        "config_files": configs,
        "calendar": calendar,
        "calendar_sha256": manifest_digest(calendar),
        "days": days,
    }
    root = create_experiment_dir(_safe(parent), template["experiment_id"])
    _write(root / "protocol.json", value)
    digest = protocol_digest(root)
    _write(
        root / "registration.json", {"protocol_sha256": digest, "registered_at": now.isoformat()}
    )
    (root / "days").mkdir()
    if _now() >= _timestamp(days[0]["decision_cutoff"]):
        _event(root, "REGISTRATION_FAILED", reason="CAPTURE_CROSSED_CUTOFF")
        raise ValueError("protocol persistence crossed first decision cutoff")
    _event(root, "REGISTERED", protocol_sha256=digest)
    return root


def _capture_inputs(rows, *, now, cutoff, ids_key="artifact_id"):
    if not isinstance(rows, list) or not rows:
        raise ValueError("nonempty input manifest required")
    captured, ids, paths = [], set(), set()
    for row in rows:
        key = row[ids_key]
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]+", key) or key in ids:
            raise ValueError("invalid or duplicate input identity")
        source = _safe(row["path"])
        if source in paths or not source.is_file():
            raise ValueError("duplicate or missing input path")
        available = _timestamp(row["available_at"])
        if available > now or available > cutoff:
            raise ValueError("future input availability")
        content = source.read_bytes()
        ids.add(key)
        paths.add(source)
        captured.append(
            (
                {
                    "artifact_id": key,
                    "source_path": str(source),
                    "available_at": row["available_at"],
                    "sha256": sha256_bytes(content),
                    "size": len(content),
                    "filename": f"{len(captured):04d}{source.suffix or '.bin'}",
                },
                content,
            )
        )
    return captured


def freeze_day(directory, analysis_date, inputs):
    """Archive actual input bytes before that day's frozen cutoff, once only."""
    root = _safe(directory)
    try:
        protocol = _active(root)
        day = next((d for d in protocol["days"] if d["date"] == _day(analysis_date)), None)
        now = _now()
        if day is None:
            raise ValueError("analysis date not in frozen calendar window")
        cutoff = _timestamp(day["decision_cutoff"])
        if now >= cutoff or now.astimezone(cutoff.tzinfo).date().isoformat() != analysis_date:
            raise ValueError("daily input freeze must occur on its date before decision cutoff")
        captured = _capture_inputs(inputs, now=now, cutoff=cutoff)
        populations = [
            (row, content) for row, content in captured if row["artifact_id"] == "population"
        ]
        population = None
        if populations:
            row, content = populations[0]
            population = _frozen_population(
                content, suffix=Path(row["filename"]).suffix, analysis_date=analysis_date
            )
        for row, content in captured:
            if row["artifact_id"] == "execution.candidates":
                validate_execution_candidates(json.loads(content), analysis_date=analysis_date, captured_at=_now(), registered=protocol["template"])
        now = _now()
        if now >= cutoff:
            raise ValueError("input capture crossed decision cutoff")
        target = root / "days" / analysis_date
        target.mkdir(exist_ok=False)
        for row, content in captured:
            with (target / row["filename"]).open("xb") as stream:
                stream.write(content)
        now = _now()
        if now >= cutoff:
            raise ValueError("input persistence crossed decision cutoff")
        value = {
            "date": analysis_date,
            "captured_at": now.isoformat(),
            "decision_cutoff": day["decision_cutoff"],
            "protocol_sha256": protocol_digest(root),
            "inputs": [row for row, _ in captured],
            "population": population,
        }
        _write(target / "manifest.json", value)
        digest = sha256_file(target / "manifest.json")
        if _now() >= cutoff:
            raise ValueError("input manifest persistence crossed decision cutoff")
        _event(root, "DAY_FROZEN", date=analysis_date, manifest_sha256=digest)
        return target / "manifest.json"
    except Exception as exc:
        _event(root, "DAY_FAILED", date=analysis_date, error=type(exc).__name__)
        raise



def validate_execution_candidates(value, *, analysis_date, captured_at, registered=None):
    """Validate p/plan/sizing while actual prospective bytes are being frozen."""
    import math
    from decimal import Decimal

    from autoresearch.research.execution_ledger import execution_plan_hash
    from autoresearch.research.probability_eval import (
        PLANNED_OVERNIGHT_EVENT,
        execution_sizing_hash,
    )

    if set(value) != {"schema_version", "status", "candidates"} or value["schema_version"] != 1:
        raise ValueError("invalid execution candidate artifact")
    if value["status"] not in {"SELECTED", "ABSTAIN", "TASK_FAILED", "DATA_UNKNOWN"}:
        raise ValueError("invalid research status")
    if bool(value["candidates"]) != (value["status"] == "SELECTED"):
        raise ValueError("selection status disagrees with candidates")
    codes = set()
    for item in value["candidates"]:
        plan, declaration = item["plan"], item["declaration"]
        if plan["analysis_session"] != analysis_date:
            raise ValueError("candidate analysis session mismatch")
        entry = _timestamp(plan["entry_window_start"])
        if captured_at >= entry:
            raise ValueError("candidate capture must precede actual entry window")
        if registered is not None and (
            plan["execution_mode"] != registered["evidence_mode"]
            or plan["cost_model_version"] != registered["cost_model_version"]
        ):
            raise ValueError("candidate differs from registered mode/cost model")
        if _timestamp(declaration["declared_at"]) > captured_at:
            raise ValueError("future probability declaration")
        if (
            declaration["plan_hash"] != execution_plan_hash(plan)
            or declaration["event_id"] != PLANNED_OVERNIGHT_EVENT
        ):
            raise ValueError("probability plan/event mismatch")
        if declaration.get("sizing_hash") != execution_sizing_hash(
            plan, qty=item["qty"], weight=item["weight"]
        ):
            raise ValueError("probability sizing hash mismatch")
        p = declaration.get("p")
        if p is None:
            if not declaration.get("abstention_reason"):
                raise ValueError("missing probability needs information-insufficiency reason")
        elif type(p) not in (int, float) or not math.isfinite(p) or not 0 <= p <= 1:
            raise ValueError("invalid probability")
        if plan["code"] in codes or not item["run_id"]:
            raise ValueError("duplicate candidate code or missing run identity")
        codes.add(plan["code"])
        for key in ("qty", "weight"):
            number = Decimal(item[key])
            if not number.is_finite() or number <= 0 or key == "weight" and number > 1:
                raise ValueError("invalid frozen sizing")
    return value


def execution_inputs(directory):
    """Read every registered day; absent/failed freezes keep a denominator row."""
    root = _safe(directory)
    protocol = read_protocol(root)
    events = [json.loads(line) for line in (root / "events.jsonl").read_text().splitlines()]
    policy_ref = protocol["config_files"].get("execution_portfolio")
    if policy_ref is None:
        raise ValueError("preregistered execution_portfolio config required")
    policy_path = _safe(policy_ref["path"])
    if sha256_file(policy_path) != policy_ref["sha256"]:
        raise ValueError("portfolio policy changed after registration")
    from autoresearch.research.execution_ledger import validate_portfolio_policy

    policy = validate_portfolio_policy(json.loads(policy_path.read_bytes()))
    cost_ref = protocol["config_files"].get("execution_cost")
    if cost_ref is None:
        raise ValueError("full frozen cost policy required")
    from autoresearch.research.execution_import import load_policy

    cost_path = _safe(cost_ref["path"])
    if sha256_file(cost_path) != cost_ref["sha256"]:
        raise ValueError("frozen cost policy bytes changed")
    cost_model = load_policy(cost_path)
    if cost_model["cost_model_version"] != policy["cost_model_version"]:
        raise ValueError("portfolio and frozen cost model differ")
    days = []
    for day in protocol["days"]:
        path = root / "days" / day["date"] / "manifest.json"
        failed = any(e["event"] == "DAY_FAILED" and e.get("date") == day["date"] for e in events)
        value = {
            "date": day["date"],
            "status": "TASK_FAILED" if failed else "DATA_UNKNOWN",
            "candidates": [],
        }
        if path.is_file():
            entry = json.loads(path.read_bytes())
            digest = sha256_file(path)
            if entry["protocol_sha256"] != protocol_digest(root) or not any(
                e["event"] == "DAY_FROZEN"
                and e.get("date") == day["date"]
                and e["manifest_sha256"] == digest
                for e in events
            ):
                raise ValueError("execution daily input not bound")
            for ref in entry["inputs"]:
                source = _safe(path.parent / ref["filename"])
                if sha256_file(source) != ref["sha256"]:
                    raise ValueError("frozen execution input changed")
                if ref["artifact_id"] == "execution.candidates":
                    candidate = validate_execution_candidates(
                        json.loads(source.read_bytes()),
                        analysis_date=day["date"],
                        captured_at=_timestamp(entry["captured_at"]),
                        registered=protocol["template"],
                    )
                    value.update(candidate)
                    value["candidate_artifact_sha256"] = ref["sha256"]
            value["manifest_sha256"] = digest
        days.append(value)
    training = []
    train_ref = protocol["config_files"].get("probability_training")
    if train_ref is not None:
        from autoresearch.research.probability_eval import import_training_outcomes

        source = _safe(train_ref["path"])
        if sha256_file(source) != train_ref["sha256"]:
            raise ValueError("frozen training input changed")
        training = import_training_outcomes(
            json.loads(source.read_bytes()), registered_at=_timestamp(protocol["registered_at"])
        )
    return {
        "protocol_sha256": protocol_digest(root),
        "protocol": protocol,
        "policy": policy,
        "cost_model": cost_model,
        "days": days,
        "probability_training": training,
    }
def abort(directory, reason):
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("abort reason required")
    _active(directory)
    _event(directory, "ABORTED", reason=reason)


SELECTION_FLAGS = (
    "baseline",
    "refined",
    "in_l2",
    "pass1_kept",
    "l3_judged",
    "is_finalist",
    "l4_rejected",
    "e6_candidate",
    "is_buy",
    "eligible",
    "in_pool",
    "pinned",
)


def _population_table(content, *, suffix, analysis_date):
    import io

    import pandas as pd

    if suffix == ".csv":
        frame = pd.read_csv(io.BytesIO(content), dtype={"code": str})
    elif suffix == ".parquet":
        frame = pd.read_parquet(io.BytesIO(content))
    else:
        raise ValueError("evaluation population must be CSV or parquet")
    column = "analysis_date" if "analysis_date" in frame else "date"
    if column not in frame or "code" not in frame:
        raise ValueError("population analysis date and security identity required")
    dates = frame[column].astype(str).str.replace("-", "", regex=False)
    if not dates.eq(analysis_date.replace("-", "")).all():
        raise ValueError("evaluation file analysis date differs from frozen day")
    if frame["code"].isna().any() or frame.duplicated([column, "code"]).any():
        raise ValueError("duplicate or missing evaluation security identity")
    return frame


def _frozen_population(content, *, suffix, analysis_date):
    import numpy as np
    import pandas as pd

    frame = _population_table(content, suffix=suffix, analysis_date=analysis_date)
    flags = [name for name in SELECTION_FLAGS if name in frame]
    if not flags:
        raise ValueError("frozen population selection flags required")
    for name in flags:
        if not frame[name].dropna().map(lambda v: isinstance(v, (bool, np.bool_))).all():
            raise ValueError("population selection flags must be boolean or explicit unknown")
    rows = [
        {
            "code": row["code"],
            **{name: None if pd.isna(row[name]) else bool(row[name]) for name in flags},
        }
        for row in frame.to_dict("records")
    ]
    return {"selection_flags": flags, "rows": sorted(rows, key=lambda row: row["code"])}


def _validate_labels(content, *, suffix, analysis_date, population=None,
                     expected_sessions=None, price_inputs=None):
    frame = _population_table(content, suffix=suffix, analysis_date=analysis_date)
    if "gap_c1_o2" not in frame:
        raise ValueError("evaluation main ruler required")
    if population is not None:
        actual = _frozen_population(content, suffix=suffix, analysis_date=analysis_date)
        if actual != population:
            raise ValueError("evaluation population/selection differs from predecision freeze")
        import numpy as np
        import pandas as pd

        values = pd.to_numeric(frame["gap_c1_o2"], errors="coerce")
        if np.isinf(values.to_numpy(dtype=float)).any():
            raise ValueError("infinite evaluation label")
        missing = values.isna()
        if missing.any() and (
            "status_gap_c1_o2" not in frame
            or not frame.loc[missing, "status_gap_c1_o2"]
            .isin(["UNKNOWN", "PENDING", "MISSING", "UNAVAILABLE"])
            .all()
        ):
            raise ValueError("missing outcome must explicitly retain UNKNOWN population row")

    if expected_sessions is not None:
        import numpy as np
        import pandas as pd

        from autoresearch.common.outcome_sessions import LABEL_VERSION, resolve_sessions
        resolved = resolve_sessions(analysis_date, sessions=expected_sessions, quality='trade_cal',
                                    today=expected_sessions[-1])
        expected = {'label_version': LABEL_VERSION, 'venue': 'CN_A',
                    'calendar_quality': 'trade_cal', 'calendar_digest': resolved['calendar_digest'],
                    'entry_session_gap_c1_o2': resolved['t1'],
                    'exit_session_gap_c1_o2': resolved['t2']}
        for key, value in expected.items():
            if key not in frame or not frame[key].astype(str).eq(str(value)).all():
                raise ValueError('label calendar/price leg provenance mismatch: ' + key)
        legs = (resolved['t1'], resolved['t2'])
        price_inputs = price_inputs or {}
        hashes, prices, price_bytes = {}, {}, {}
        for day in legs:
            if day not in price_inputs:
                hashes[day] = None
                continue
            path = _safe(price_inputs[day])
            data = path.read_bytes()
            hashes[day] = sha256_bytes(data)
            price_bytes[day] = data
            import io
            price = pd.read_parquet(io.BytesIO(data))
            if 'trade_date' not in price or not price['trade_date'].astype(str).eq(day).all():
                raise ValueError('price input session mismatch')
            price['code'] = price['ts_code'].astype(str).str[:6]
            if price['code'].duplicated().any():
                raise ValueError('duplicate price input identity')
            prices[day] = price.set_index('code')
        if 'price_input_hashes_gap_c1_o2' not in frame:
            raise ValueError('label price input hashes required')
        for row in frame.to_dict('records'):
            if json.loads(row['price_input_hashes_gap_c1_o2']) != hashes:
                raise ValueError('label price input hash mismatch')
            try:
                actual = float(prices[legs[1]].loc[row['code'], 'open']) / float(prices[legs[0]].loc[row['code'], 'close']) - 1
            except (KeyError, ZeroDivisionError):
                actual = np.nan
            from autoresearch.common.ruler import GAP_CLIP
            if abs(actual) > GAP_CLIP:
                actual = np.nan
            value = row['gap_c1_o2']
            if not (pd.isna(value) and pd.isna(actual)) and not np.isclose(value, actual, atol=1e-8):
                raise ValueError('label value differs from exact price legs')
            state = row.get('status_gap_c1_o2')
            if (pd.notna(actual) and state != 'MATURE') or (pd.isna(actual) and state == 'MATURE'):
                raise ValueError('label maturity differs from exact price legs')
        return {'hashes': hashes, 'contents': price_bytes}


def finalize(directory, labels):
    """Freeze actual matured evaluation files and a legacy-compatible evaluation spec."""
    root = _safe(directory)
    try:
        protocol = _active(root)
        now = _now()
        events = [json.loads(row) for row in (root / "events.jsonl").read_text().splitlines()]
        daily = []
        populations = {}
        for day in protocol["days"]:
            if now < _timestamp(day["label_due_at"]):
                raise ValueError("D2 labels not mature")
            path = _safe(root / "days" / day["date"] / "manifest.json")
            digest = sha256_file(path)
            if not any(
                e["event"] == "DAY_FROZEN"
                and e["date"] == day["date"]
                and e["manifest_sha256"] == digest
                for e in events
            ):
                raise ValueError("daily inputs not bound")
            entry = json.loads(path.read_text())
            if entry["protocol_sha256"] != protocol_digest(root):
                raise ValueError("daily protocol binding mismatch")
            for row in entry["inputs"]:
                if sha256_file(_safe(path.parent / row["filename"])) != row["sha256"]:
                    raise ValueError("daily input archive changed")
            if entry.get("population") is None:
                raise ValueError("daily frozen candidate population missing")
            populations[day["date"]] = entry["population"]
            daily.append({"date": day["date"], "manifest_sha256": digest})
        if sorted(row["date"] for row in labels) != [d["date"] for d in protocol["days"]]:
            raise ValueError("exactly one evaluation file per frozen analysis day required")
        due = {d["date"]: _timestamp(d["label_due_at"]) for d in protocol["days"]}
        if any(_timestamp(row["available_at"]) < due[row["date"]] for row in labels):
            raise ValueError("label claimed available before D2 maturity")
        captured = _capture_inputs(labels, now=now, cutoff=now, ids_key="date")
        validated_prices = {}
        for row, content in captured:
            validated_prices[row["artifact_id"]] = _validate_labels(
                content,
                suffix=Path(row["filename"]).suffix,
                analysis_date=row["artifact_id"],
                population=populations[row["artifact_id"]],
                expected_sessions=[s["date"] for s in protocol["calendar"]["sessions"]],
                price_inputs=next(item.get("price_inputs", {}) for item in labels if item["date"] == row["artifact_id"]),
            )
        target = root / "final"
        target.mkdir(exist_ok=False)
        price_refs = []
        for analysis_day, source in validated_prices.items():
            for session, content in source['contents'].items():
                relative = Path('prices') / analysis_day / f'{session}.parquet'
                destination = target / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                with destination.open('xb') as stream:
                    stream.write(content)
                price_refs.append({'analysis_date': analysis_day, 'session': session,
                    'path': str(relative), 'sha256': source['hashes'][session]})
        files = []
        for row, content in captured:
            path = target / row["filename"]
            with path.open("xb") as stream:
                stream.write(content)
            files.append(path)
        spec = dict(
            protocol["template"],
            created_at=now.isoformat(),
            code_sha=protocol["code_provenance"]["declared"],
            prompt_hashes={k: v["sha256"] for k, v in protocol["prompt_files"].items()},
            input_manifest_hash="",
        )
        manifest = file_manifest(files, registered_date_slice(spec))
        spec["input_manifest_hash"] = manifest_digest(manifest)
        _write(target / "input_manifest.json", manifest)
        _write(
            target / "manifest.json",
            {
                "protocol_sha256": protocol_digest(root),
                "registered_at": protocol["registered_at"],
                "finalized_at": now.isoformat(),
                "code_provenance": protocol["code_provenance"],
                "daily_inputs": daily,
                "price_inputs": price_refs,
                "labels": [row for row, _ in captured],
                "input_manifest_sha256": spec["input_manifest_hash"],
            },
        )
        path, digest = freeze_validated_spec(target, spec)
        _event(root, "FINALIZED", spec_sha256=digest)
        return path
    except Exception as exc:
        _event(root, "FINALIZE_FAILED", error=type(exc).__name__)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    begin_parser = sub.add_parser("begin")
    for name in ("parent", "template", "calendar", "start-date", "sources", "repo-root"):
        begin_parser.add_argument("--" + name, required=True)
    for name in ("day", "finalize", "abort"):
        child = sub.add_parser(name)
        child.add_argument("--directory", required=True)
        if name == "day":
            child.add_argument("--date", required=True)
            child.add_argument("--inputs", required=True)
        elif name == "finalize":
            child.add_argument("--labels", required=True)
        else:
            child.add_argument("--reason", required=True)
    args = parser.parse_args(argv)

    def read(path):
        return json.loads(_safe(path).read_text())

    if args.command == "begin":
        sources = read(args.sources)
        result = begin(
            parent=args.parent,
            template=read(args.template),
            calendar=read(args.calendar),
            start_date=args.start_date,
            repo_root=args.repo_root,
            prompt_paths=sources["prompts"],
            config_paths=sources["configs"],
        )
    elif args.command == "day":
        result = freeze_day(args.directory, args.date, read(args.inputs))
    elif args.command == "finalize":
        result = finalize(args.directory, read(args.labels))
    else:
        result = abort(args.directory, args.reason)
    print(json.dumps({"result": str(result) if result else None}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
