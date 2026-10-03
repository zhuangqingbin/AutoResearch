"""Deterministic domain operations invoked through command capture."""

from __future__ import annotations

import argparse
import base64
import json
import re
import shutil
import sys
from pathlib import Path

from autoresearch.agents.utils.rating import parse_rating
from autoresearch.analyze import assemble as stock_assemble
from autoresearch.common import workspace as ws
from autoresearch.common.atomic import (
    atomic_write_bytes,
    atomic_write_json,
    canonical_json,
    sha256_bytes,
)
from autoresearch.contracts.agent_output import L4_CARD
from autoresearch.dossier import (
    builder as dossier_builder,
    prefetch as dossier_prefetch,
    schema as dossier_schema,
)
from autoresearch.macro import assemble as macro_assemble, harvest as macro_harvest
from autoresearch.macro.state import load_macro_state
from autoresearch.session_agent import artifacts
from autoresearch.session_agent.validation import validate_registered_contract
from autoresearch.session_agent.workflows.stock import (
    full_product_artifacts,
    required_full_products,
)
from autoresearch.trace.operation_clock import operation_clock

_PROPOSAL_RE = re.compile(L4_CARD.field("proposal").pattern, re.IGNORECASE)


def _active_handle():
    from autoresearch.common import workspace as ws
    from autoresearch.trace.capsule import require_active_run

    run_id = ws.active_run_id()
    if run_id is None:
        raise RuntimeError("domain operation requires AUTORESEARCH_RUN_ID")
    return require_active_run(run_id)


def _request(handle) -> dict:
    return json.loads((Path(handle.workspace) / "session/request.json").read_text(encoding="utf-8"))


def _text(handle, artifact_id: str) -> str:
    with artifacts.open_artifact(handle, artifact_id) as stream:
        return stream.read().decode("utf-8")


def stock_evidence_bundle(handle=None) -> dict:
    current = handle or _active_handle()
    from autoresearch.session_agent.evidence_bundle import build_bundle
    from autoresearch.session_agent.service import _task

    task = _task(current, "stock.evidence_bundle")
    bundle = build_bundle(current, task["input_artifact_ids"])
    target = artifacts.declared_path(current, "stock.evidence_bundle")
    if target.exists():
        if json.loads(target.read_text(encoding="utf-8")) != bundle:
            raise artifacts.ArtifactConflict("stock evidence bundle changed")
    else:
        atomic_write_json(target, bundle)
    return bundle


def research_calculate(
    calculator_id: str,
    input_artifact_ids: list[str],
    parameters: dict,
    *,
    handle=None,
    task_id: str | None = None,
    attempt: int | None = None,
    raise_on_failure: bool = True,
) -> dict:
    """Run one registered calculation from frozen JSON artifacts only."""
    current = handle or _active_handle()
    from autoresearch.research.calculations import calculate, persist_calculation

    merged: dict = {}
    input_refs = []
    for artifact_id in input_artifact_ids:
        with artifacts.open_artifact(current, artifact_id) as stream:
            payload = json.loads(stream.read().decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"calculation input is not a JSON object: {artifact_id}")
        values = payload.get("inputs", payload)
        if not isinstance(values, dict):
            raise ValueError(f"calculation input values are not an object: {artifact_id}")
        conflicts = set(merged) & set(values)
        if conflicts:
            raise ValueError(f"calculation input keys conflict: {sorted(conflicts)}")
        merged.update(values)
        digest = artifacts.binding_sha256(current, artifact_id)
        if digest is None:
            raise RuntimeError(f"calculation input is not frozen: {artifact_id}")
        input_refs.append({"artifact_id": artifact_id, "sha256": digest})
    environment_task = str(__import__("os").environ.get("AUTORESEARCH_TASK_ID", "")).strip()
    environment_attempt = int(__import__("os").environ.get("AUTORESEARCH_ATTEMPT", "1"))
    result = calculate(
        calculator_id,
        merged,
        parameters,
        input_refs=input_refs,
        task_id=task_id or environment_task or "research.calculate",
        attempt=attempt or environment_attempt,
    )
    persist_calculation(current, result)
    if raise_on_failure and result["status"] != "SUCCEEDED":
        raise RuntimeError(
            f"registered calculation failed: {result['error']['category']}:"
            f"{result['error']['message']}"
        )
    return result


def stock_validate(handle=None) -> dict:
    current = handle or _active_handle()
    validate_registered_contract(
        current,
        {"outputs": []},
        {"expected_output_contract": "stock.lite.v1"},
    )
    text = _text(current, "stock.card.output")
    proposal = _PROPOSAL_RE.search(text)
    descriptor = artifacts.bind_artifact_hash(current, "stock.card.output")
    value = {
        "schema_version": 1,
        "contract": "stock.lite.v1",
        "rating": parse_rating(text, strict=True),
        "proposal": proposal.group(1).upper(),
        "card_sha256": descriptor["sha256"],
    }
    target = Path(current.staging) / "session_outputs/card.validation.json"
    atomic_write_json(target, value)
    _record_card_stage(current, artifacts.artifact_path(current, "stock.card.output"), value)
    return value


def _record_card_stage(handle, card: Path, value: dict) -> None:
    """Checkpoint the accepted card as stage `card`, like the legacy runctl step.

    The LITE evidence profile owes `stages/card/*/result.json`; in session_v1 the
    card is an inference task, so its validating operation records the stage. Live
    runs only: offline replay never writes the run's capsule.
    """
    import os

    from autoresearch.trace.replay import REPLAY_ENV
    from autoresearch.trace.write_guard import RunWriteViolation

    run_id = str(os.environ.get("AUTORESEARCH_RUN_ID", "")).strip()
    if run_id != handle.run_id or os.environ.get(REPLAY_ENV):
        return
    try:
        from autoresearch.common import workspace as ws
        from autoresearch.trace.capsule import checkpoint
        from autoresearch.trace.write_guard import assert_write_allowed, run_write_lock

        with run_write_lock(run_id):
            assert_write_allowed(run_id, "stock.validate", ws.ENGINE)
            checkpoint(run_id, "card", "SUCCEEDED", [Path(card)],
                       {"rating": value["rating"], "proposal": value["proposal"],
                        "card_sha256": value["card_sha256"], "origin": "stock.validate"})
    except RunWriteViolation:
        raise
    except Exception as exc:  # noqa: BLE001 - evidence failure stays visible, not a business result
        print(f"[stock.validate·capsule] card checkpoint failed: {exc}", file=sys.stderr)


def _safe_output_name(request: dict) -> str:
    subject = str(request["subject"])
    code = subject.split(".", maxsplit=1)[0]
    raw = request.get("name") if code.isdigit() else subject
    raw = str(raw or code)
    safe = re.sub(r'[/\\:*?"<>|\s]', "", raw).replace("*ST", "ST").strip()
    if not safe:
        raise ValueError("stock publication name is empty")
    return f"{safe}_lite.md"


def stock_prepare_publication(handle=None) -> dict:
    current = handle or _active_handle()
    request = _request(current)
    validation = json.loads(_text(current, "stock.card.validation"))
    card = artifacts.bind_artifact_hash(current, "stock.card.output")
    if validation.get("card_sha256") != card["sha256"]:
        raise RuntimeError("validated stock card changed before publication")
    value = {
        "schema_version": 1,
        "kind": "stock-research",
        "mode": "LITE",
        "run_id": current.run_id,
        "engine": current.engine,
        "analysis_date": request["analysis_date"],
        "ticker": request["subject"],
        "name": request.get("name"),
        "output_name": _safe_output_name(request),
        "rating": validation["rating"],
        "proposal": validation["proposal"],
        "card_sha256": card["sha256"],
    }
    from autoresearch.contracts.profiles import CURRENT_CARD_RULES
    from autoresearch.scan.l4.card_io import card_rules_version
    if card_rules_version(handle=current) == CURRENT_CARD_RULES:
        from autoresearch.news.card_claims import registered_card_semantics
        from autoresearch.session_agent.card_semantics_context import claim_handle, freeze
        from autoresearch.trace.completeness import card_rating_bands_from_capsule
        freeze(current)  # replay restores exactly this owner/profile/claim context
        semantics = registered_card_semantics(claim_handle(current), _text(current, "stock.card.output"), subject=request["subject"],
                                          artifact_id="stock.card.output",
                                          frame_hash=artifacts.snapshot_artifact(current, "research.frame")["sha256"],
                                          frame=json.loads(_text(current, "research.frame")),
                                          bands=card_rating_bands_from_capsule(current.capsule))
        value.update({key: semantics[key] for key in ("machine_suggestion", "machine_reason", "execution", "claim_usage")})
    target = Path(current.staging) / "session_outputs/publication.json"
    atomic_write_json(target, value)
    return value


def stock_full_validate(handle=None) -> dict:
    current = handle or _active_handle()
    mapping = full_product_artifacts()
    hashes = {}
    for relative in sorted(required_full_products()):
        artifact_id = mapping[relative]
        try:
            descriptor = artifacts.bind_artifact_hash(current, artifact_id)
            with artifacts.open_artifact(current, artifact_id) as stream:
                text = stream.read().decode("utf-8").strip()
        except (KeyError, ValueError, RuntimeError) as exc:
            raise RuntimeError(f"required full product missing: {relative}") from exc
        if not text:
            raise RuntimeError(f"required full product empty: {relative}")
        hashes[relative] = descriptor["sha256"]
    decision = _text(current, mapping[stock_assemble.DECISION_REL])
    from autoresearch.agents.utils.rating import validate_rating_and_proposal

    try:
        validate_rating_and_proposal(decision)
    except ValueError as exc:
        raise RuntimeError(f"full decision: {exc}") from exc
    from autoresearch.contracts.profiles import CURRENT_CARD_RULES
    from autoresearch.scan.l4.card_io import card_rules_version
    from autoresearch.session_agent.validation import _research_card_semantics

    if card_rules_version(handle=current) == CURRENT_CARD_RULES:
        _research_card_semantics(current, decision, {"subject": _request(current)["subject"],
            "task_id": "stock.pm", "output_artifact_ids": [mapping[stock_assemble.DECISION_REL]]})
    value = {
        "schema_version": 1,
        "contract": "stock.full.products.v1",
        "required_products": sorted(required_full_products()),
        "hashes": hashes,
    }
    atomic_write_json(Path(current.staging) / "session_outputs/full.validation.json", value)
    return value


#: The operation the FULL plan registers for its assemble task (`workflows/stock.py`); the
#: legacy assembler must guard and checkpoint under this identity inside a session run.
FULL_ASSEMBLE_OPERATION = "stock.full.assemble"


def stock_full_assemble(handle=None) -> dict:
    current = handle or _active_handle()
    request = _request(current)
    validation = json.loads(_text(current, "stock.full.validation"))
    expected = required_full_products()
    if set(validation.get("required_products") or []) != expected:
        raise RuntimeError("full validation does not match assembler requirements")
    ticker = str(request["subject"])
    from autoresearch.dataflows.symbol_utils import normalize_symbol

    ticker = normalize_symbol(ticker)
    draft_root = (
        Path(current.staging) / "analyze" / f"{ticker}_{request['analysis_date'].replace('-', '')}"
    )
    scratch = Path(current.staging) / "session_outputs/assemble_scratch"
    shutil.rmtree(scratch, ignore_errors=True)
    original_argv = sys.argv
    try:
        sys.argv = ["assemble.py", str(draft_root)]
        if request.get("name"):
            sys.argv.extend(["--name", request["name"]])
        from autoresearch.contracts.profiles import CURRENT_CARD_RULES
        from autoresearch.scan.l4.card_io import card_rules_version
        from autoresearch.trace.completeness import card_rating_bands_from_capsule
        version = card_rules_version(handle=current)
        from autoresearch.news.card_claims import bound_claim_context
        from autoresearch.session_agent.card_semantics_context import claim_handle, freeze
        if version == CURRENT_CARD_RULES:
            freeze(current)  # replay restores exactly this owner/profile/claim context
        decision_context = ({"rules_version": version, "subject": request["subject"],
                             "frame": json.loads(_text(current, "research.frame")),
                             "rating_bands": card_rating_bands_from_capsule(current.capsule),
                             "frame_hash": artifacts.snapshot_artifact(current, "research.frame")["sha256"],
                             "claim_context": bound_claim_context(claim_handle(current), artifact_id="stock.full.4_decision.decision")}
                            if version == CURRENT_CARD_RULES else None)
        if stock_assemble.main(
            clock=operation_clock(current),
            reports_root=scratch,
            context_root=Path(current.staging),
            decision_context=decision_context,
            write_operation=FULL_ASSEMBLE_OPERATION,
        ) != 0:
            raise RuntimeError("existing stock assembler rejected full products")
    finally:
        sys.argv = original_argv
    report_dirs = sorted((scratch / "analyze").glob("*"))
    if len(report_dirs) != 1:
        raise RuntimeError("stock assembler did not create one candidate report directory")
    source_dir = report_dirs[0]
    reports = sorted(source_dir.glob("*.md"))
    if len(reports) != 1:
        raise RuntimeError("stock assembler did not create one report")
    report_bytes = reports[0].read_bytes()
    manifest = json.loads((source_dir / "manifest.json").read_text(encoding="utf-8"))
    manifest["run_id"] = current.run_id
    manifest["context_file"] = "stock.context" if (
        Path(current.staging) / f"{ticker}_{request['analysis_date']}.md"
    ).is_file() else None
    output = Path(current.staging) / "session_outputs"
    atomic_write_bytes(output / "full_report.md", report_bytes)
    atomic_write_json(output / "full_manifest.json", manifest)
    value = {
        "schema_version": 1,
        "kind": "stock-research",
        "mode": "FULL",
        "run_id": current.run_id,
        "engine": current.engine,
        "analysis_date": request["analysis_date"],
        "ticker": ticker,
        "name": request.get("name"),
        "output_name": reports[0].name,
        "rating": manifest["rating"],
        "proposal": manifest["proposal"],
        "report_sha256": sha256_bytes(report_bytes),
    }
    atomic_write_json(output / "publication.json", value)
    return value



def macro_intel_prepare(handle=None) -> dict:
    """Freeze neutral entities/cutoff/calendar from registered immutable inputs."""
    from autoresearch.contracts.execution import validate_decision_frame

    current = handle or _active_handle()
    frame = validate_decision_frame(json.loads(_text(current, "research.frame")))
    tape = json.loads(_text(current, "macro.global_tape"))
    policy = json.loads(_text(current, "macro.intel.policy"))
    cap = policy["source_budget"]["max_queries"]
    if type(cap) is not int or cap < 1 or policy["source_budget"]["unit"] != "SEARCH_AND_FETCH":
        raise ValueError("invalid frozen global intel source budget")
    events = tape.get("known_events") if isinstance(tape, dict) else None
    # Missing structured calendar remains explicit; do not infer dates from opinions.
    events = [{key: event[key] for key in (
        "entity", "event", "scheduled_at", "published_at", "source_url", "time_quality") if key in event}
        for event in events if isinstance(event, dict)] if isinstance(events, list) else []
    value = {
        "schema_version": 1, "analysis_date": current.analysis_date,
        "knowledge_cutoff": frame["knowledge_cutoff"],
        "entities": {"central_banks": ["Fed", "PBoC", "ECB", "BOJ"],
                     "economies": ["US", "China", "Eurozone", "Japan"]},
        "known_events": events,
        "calendar_status": "BOUND" if events else "UNAVAILABLE",
        "source_budget": {"max_queries": cap, "unit": "SEARCH_AND_FETCH"},
    }
    atomic_write_json(artifacts.declared_path(current, "macro.intel.request"), value)
    return value


def macro_harvest_run(handle=None) -> dict:
    current = handle or _active_handle()
    request = _request(current)
    root = Path(current.staging) / "macro" / request["analysis_date"]
    if macro_harvest.main([request["analysis_date"], "--output-dir", str(root)]) != 0:
        raise RuntimeError("macro harvest failed")
    return {
        "data": str(root / "data.md"),
        "global_tape": str(root / "global_tape.json"),
        "scan_meta": str(root / "scan_meta.json"),
    }


def _macro_state_source_text(path: Path | str | None) -> tuple[str | None, str]:
    if path is not None:
        candidate = Path(path)
        try:
            return candidate.read_text(encoding="utf-8"), "PATH"
        except FileNotFoundError:
            return None, "MISSING"
        except OSError:
            return "{", "UNREADABLE"
    from autoresearch.common import published_state

    committed = published_state.read_committed_state(
        "macro.latest_state",
        state_root=ws.context_root() / "_published_state",
        reports_root=ws.run_reports_root("macro-research"),
    )
    if committed is not None:
        return json.dumps(committed, ensure_ascii=False), "COMMITTED"
    candidate = ws.context_root() / "macro/macro_state.json"
    try:
        return candidate.read_text(encoding="utf-8"), "LEGACY_PATH"
    except FileNotFoundError:
        return None, "MISSING"
    except OSError:
        return "{", "UNREADABLE"


def collect_macro_lite_snapshot(current, macro_state_path: Path | str | None = None) -> dict:
    """Collect the frame supplier and exact state bytes before freshness processing."""
    request = _request(current)
    try:
        with artifacts.open_artifact(current, "macro.market_pack") as stream:
            market = json.loads(stream.read().decode("utf-8"))
    except (KeyError, ValueError, RuntimeError):
        from autoresearch.scan.frame import build_market_frame
        from autoresearch.scan.market import market_pack_from_frame

        frame, _ = build_market_frame(
            request["analysis_date"], cap_floor_yi=30.0, include_bj=True, source="tushare"
        )
        market = market_pack_from_frame(frame, date=request["analysis_date"])
    state_text, source = _macro_state_source_text(macro_state_path)
    return {
        "schema_version": 1,
        "analysis_date": request["analysis_date"],
        "market_payload": market,
        "macro_state_text": state_text,
        "macro_state_source": source,
    }


def render_macro_lite_snapshot(snapshot: dict, *, output_dir: Path | str) -> dict:
    """Apply freshness and strategist projection using frozen state bytes only."""
    from autoresearch.scan.strategist_pack import project

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    state_text = snapshot.get("macro_state_text")
    state_path = output / "_replay_inputs/macro_state.json"
    if state_text is not None:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(str(state_text), encoding="utf-8")
    market = dict(snapshot["market_payload"])
    regime = market.get("regime") or {}
    state, note = load_macro_state(
        str(snapshot["analysis_date"]),
        regime_today=regime.get("label"),
        path=state_path,
    )
    payload = {**market, "macro_state": state, "macro_state_note": note}
    projection = project(payload)
    atomic_write_json(output / "market_pack.json", payload)
    atomic_write_json(output / "strategist_pack.json", projection)
    return {
        "pack": projection["pack"],
        "macro_state": projection["pack"].get("macro_state"),
        "macro_state_note": projection["pack"].get("macro_state_note"),
    }


def _macro_market_payload(current, macro_state_path: Path | str | None = None) -> dict:
    snapshot = collect_macro_lite_snapshot(current, macro_state_path)
    try:
        from autoresearch.trace.source_receipts import record_active_response

        record_active_response(
            provider="macro_lite_frame",
            endpoint="macro.lite.frame.snapshot.v1",
            params={"analysis_date": snapshot["analysis_date"]},
            outcome=snapshot,
            consumer_artifact_ids=["macro.market_pack", "macro.strategist_pack"],
        )
    except Exception:  # noqa: BLE001
        print("macro lite source snapshot evidence incomplete", file=sys.stderr)
    render_macro_lite_snapshot(snapshot, output_dir=Path(current.staging) / "session_outputs")
    return json.loads(
        (Path(current.staging) / "session_outputs/market_pack.json").read_text(encoding="utf-8")
    )


def macro_lite_prepare(handle=None, *, macro_state_path: Path | str | None = None) -> dict:
    current = handle or _active_handle()
    from autoresearch.scan.strategist_pack import project

    payload = _macro_market_payload(current, macro_state_path)
    projection = project(payload)
    atomic_write_json(Path(current.staging) / "session_outputs/strategist_pack.json", projection)
    return {
        "pack": projection["pack"],
        "macro_state": projection["pack"].get("macro_state"),
        "macro_state_note": projection["pack"].get("macro_state_note"),
    }


def macro_lite_validate(handle=None) -> dict:
    current = handle or _active_handle()
    text = _text(current, "macro.market_view")
    from autoresearch.session_agent.validation import market_view_complete

    # One shape rule, shared with the scan market view: sections 1–5 carry a bold title and
    # section 6 is the plain disclaimer line the macro-brief template writes.
    if not market_view_complete(text):
        raise RuntimeError("macro market view requires all six sections")
    sections = {"1", "2", "3", "4", "5", "6"}
    descriptor = artifacts.bind_artifact_hash(current, "macro.market_view")
    value = {
        "schema_version": 1,
        "contract": "macro.brief.v1",
        "sections": sorted(sections),
        "report_sha256": descriptor["sha256"],
    }
    atomic_write_json(Path(current.staging) / "session_outputs/macro.lite.validation.json", value)
    return value


def macro_prepare_publication(handle=None) -> dict:
    current = handle or _active_handle()
    request = _request(current)
    validation = json.loads(_text(current, "macro.lite.validation"))
    report = artifacts.bind_artifact_hash(current, "macro.market_view")
    if validation.get("report_sha256") != report["sha256"]:
        raise RuntimeError("validated macro brief changed before publication")
    value = {
        "schema_version": 1,
        "kind": "macro-research",
        "mode": "LITE",
        "run_id": current.run_id,
        "engine": current.engine,
        "analysis_date": request["analysis_date"],
        "output_name": f"{current.run_id}_market_view.md",
        "report_sha256": report["sha256"],
    }
    atomic_write_json(Path(current.staging) / "session_outputs/macro.publication.json", value)
    return value


def macro_full_validate(handle=None) -> dict:
    current = handle or _active_handle()
    from autoresearch.session_agent.workflows.macro import (
        macro_product_artifacts,
        required_macro_products,
    )

    mapping = macro_product_artifacts()
    hashes = {}
    request = _request(current)
    selected = set(request.get('macro_optional_products', [])) if request.get('schema_version', 1) >= 4 else set()
    for relative in sorted(required_macro_products() | selected):
        artifact_id = mapping[relative]
        try:
            descriptor = artifacts.bind_artifact_hash(current, artifact_id)
            text = _text(current, artifact_id).strip()
        except (KeyError, ValueError, RuntimeError) as exc:
            raise RuntimeError(f"required macro product missing: {relative}") from exc
        if not text:
            raise RuntimeError(f"required macro product empty: {relative}")
        hashes[relative] = descriptor["sha256"]
    try:
        scope = macro_assemble.allocation_scope(_text(current, "macro.data"))
    except (KeyError, ValueError, RuntimeError) as exc:
        raise RuntimeError(f"macro allocation scope: {exc}") from exc
    for relative in (macro_assemble.DECISION_REL, macro_assemble.SECTOR_MAP_REL):
        try:
            allocation = macro_assemble.parse_allocation(
                _text(current, mapping[relative]), expected_keys=scope[relative],
            )
        except ValueError as exc:
            raise RuntimeError(f"macro allocation {relative}: {exc}") from exc
        if not allocation:
            raise RuntimeError(f"macro allocation is not parseable: {relative}")
    value = {
        "schema_version": 1,
        "contract": "macro.full.products.v1",
        "required_products": sorted(required_macro_products()),
        "hashes": hashes,
        "allocation_keys": {relative: list(keys) for relative, keys in scope.items()},
    }
    atomic_write_json(Path(current.staging) / "session_outputs/macro.full.validation.json", value)
    return value


def macro_full_assemble(handle=None) -> dict:
    current = handle or _active_handle()
    request = _request(current)
    validation = json.loads(_text(current, "macro.full.validation"))
    from autoresearch.session_agent.workflows.macro import required_macro_products

    if set(validation.get("required_products") or []) != required_macro_products():
        raise RuntimeError("macro validation does not match assembler requirements")
    scope = macro_assemble.allocation_scope(_text(current, "macro.data"))
    if validation.get("allocation_keys") != {relative: list(keys) for relative, keys in scope.items()}:
        raise RuntimeError("macro allocation scope changed after validation")
    root = Path(current.staging) / "macro" / request["analysis_date"]
    output = Path(current.staging) / "session_outputs"
    scratch = output / "macro_assembled"
    shutil.rmtree(scratch, ignore_errors=True)
    scan_root = output / "macro_state_scan"
    scan_meta = root / "scan_meta.json"
    if scan_meta.is_file():
        target = scan_root / request["analysis_date"] / "meta.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(target, scan_meta.read_bytes())
    if (
        macro_assemble.main(
            [
                str(root),
                "--output-dir",
                str(scratch),
                "--state-out-dir",
                str(output),
            ],
            clock=operation_clock(current),
            scan_root=scan_root,
            expected_keys=scope,
        )
        != 0
    ):
        raise RuntimeError("existing macro assembler rejected full products")
    reports = sorted(scratch.glob("*_summary.md"))
    if len(reports) != 1:
        raise RuntimeError("macro assembler did not create one report")
    report_bytes = reports[0].read_bytes()
    atomic_write_bytes(output / "macro.full.report.md", report_bytes)
    state = json.loads((output / "macro_state.json").read_text(encoding="utf-8"))
    state["run_report"] = "macro.full.report"
    state["session_run_id"] = current.run_id
    atomic_write_json(output / "macro_state.json", state)
    value = {
        "schema_version": 1,
        "kind": "macro-research",
        "mode": "FULL",
        "run_id": current.run_id,
        "engine": current.engine,
        "analysis_date": request["analysis_date"],
        "output_name": f"{current.run_id}_summary.md",
        "report_sha256": sha256_bytes(report_bytes),
        "state_as_of": state["as_of"],
    }
    atomic_write_json(output / "macro.publication.json", value)
    return value


_SECTOR_INPUT_NAMES = frozenset(
    {"L1_scored_full.csv", "L2_gbdt_top200.csv", "sectors.csv", "calendar.csv", "meta.json", "sector_evidence.json"}
)


def _encode_payload(payload: bytes) -> str:
    return base64.b64encode(payload).decode("ascii")


def _decode_payload(payload: object) -> bytes:
    if not isinstance(payload, str):
        raise TypeError("frozen file payload must be base64 text")
    return base64.b64decode(payload.encode("ascii"), validate=True)


def collect_sector_snapshot(handle=None, *, scan_root: Path | str | None = None) -> dict:
    """Collect the exact scan/brief/vendor inputs consumed by sector preparation."""
    current = handle or _active_handle()
    request = _request(current)
    analysis_date = request["analysis_date"]
    industry = request["subject"]
    source_root = Path(scan_root) if scan_root is not None else ws.scan_root()
    source = source_root / analysis_date
    source_kind = "existing_scan"
    input_files: dict[str, str] = {}
    l1_source = source / "L1_scored_full.csv"
    if l1_source.is_file():
        input_files[l1_source.name] = _encode_payload(l1_source.read_bytes())
        for name in sorted(_SECTOR_INPUT_NAMES - {l1_source.name}):
            candidate = source / name
            if candidate.is_file():
                input_files[name] = _encode_payload(candidate.read_bytes())
    else:
        source_kind = "generated_frame"
        from autoresearch.scan.frame import build_market_frame

        frame, _ = build_market_frame(
            analysis_date, cap_floor_yi=30.0, include_bj=True, source="tushare"
        )
        input_files["L1_scored_full.csv"] = _encode_payload(
            frame.to_csv(index=False).encode("utf-8")
        )
    readthrough = None
    if request["requested_mode"] == "FULL":
        from autoresearch.sector.pack import readthrough_block

        readthrough = readthrough_block(industry, analysis_date)
    reuse = {
        "reused": False,
        "source": None,
        "previous_date": None,
        "shift_pp": None,
        "source_body": None,
    }
    candidate = request.get("sector_brief_profile", "legacy") == "deterministic-v1"
    if request["requested_mode"] == "LITE" and source_kind == "existing_scan" and not candidate:
        from autoresearch.scan.user_config import knob
        from autoresearch.sector.reuse import find_reusable

        found = find_reusable(analysis_date, [industry], root=source_root,
                              ttl_days=int(knob("sector", "reuse_ttl_days", None, 5)))
        if industry in found:
            item = found[industry]
            reuse = {
                "reused": True,
                "source": item["src"],
                "previous_date": item["prev"],
                "shift_pp": item["shift_pp"],
                "source_body": Path(item["src"]).read_text(encoding="utf-8"),
            }
    value = {
        "schema_version": 1,
        "engine": current.engine,
        "analysis_date": analysis_date,
        "industry": industry,
        "mode": request["requested_mode"],
        "source": source_kind,
        "input_files": input_files,
        "readthrough": readthrough,
        "reuse": reuse,
    }
    if candidate:
        from autoresearch.sector.reuse import find_stable_snapshot
        from autoresearch.session_agent.dispatch import sector_brief_web_searches
        frame = json.loads(_text(current, "research.frame"))
        config = getattr(current.contract, "user_config", {}) or {}
        ttl = int(config.get("sector", {}).get("reuse_ttl_days", 5))
        value["reuse"] = {"reused": False, "previous": find_stable_snapshot(
            analysis_date, industry, root=source_root, ttl_days=ttl), "ttl_days": ttl}
        value.update(schema_version=2, sector_brief_profile="deterministic-v1",
                     event_max_queries=sector_brief_web_searches(getattr(current.contract, "user_config", {})),
                     knowledge_cutoff=frame["knowledge_cutoff"])
    return value


def render_sector_snapshot(snapshot: dict, *, staging_root: Path | str) -> dict[str, Path]:
    """Rebuild sector pack/reuse outputs from one frozen source snapshot only."""
    required = {
        "schema_version",
        "engine",
        "analysis_date",
        "industry",
        "mode",
        "source",
        "input_files",
        "readthrough",
        "reuse",
    }
    candidate = snapshot.get("schema_version") == 2
    if candidate:
        required |= {"sector_brief_profile", "event_max_queries", "knowledge_cutoff"}
    if set(snapshot) != required or snapshot["schema_version"] not in {1, 2}:
        raise ValueError("invalid sector snapshot contract")
    if candidate and snapshot["sector_brief_profile"] != "deterministic-v1":
        raise ValueError("invalid sector snapshot profile")
    if snapshot["mode"] not in {"FULL", "LITE"}:
        raise ValueError("invalid sector snapshot mode")
    if snapshot["source"] not in {"existing_scan", "generated_frame"}:
        raise ValueError("invalid sector snapshot source")
    input_files = snapshot["input_files"]
    if (
        not isinstance(input_files, dict)
        or "L1_scored_full.csv" not in input_files
        or not set(input_files) <= _SECTOR_INPUT_NAMES
    ):
        raise ValueError("invalid sector input file set")
    root = Path(staging_root)
    input_dir = root / "sector_inputs" / snapshot["analysis_date"]
    input_dir.mkdir(parents=True, exist_ok=True)
    for name, payload in input_files.items():
        atomic_write_bytes(input_dir / name, _decode_payload(payload))

    import pandas as pd

    l1 = pd.read_csv(input_dir / "L1_scored_full.csv", dtype={"code": str})
    l2_path = input_dir / "L2_gbdt_top200.csv"
    if not l2_path.is_file():
        order = next(
            (column for column in ("composite", "pct_60d") if column in l1.columns),
            None,
        )
        l2 = l1.sort_values(order, ascending=False).head(200) if order else l1.head(200)
        keep = [column for column in ("code", "name", "industry") if column in l2]
        l2[keep].to_csv(l2_path, index=False)
    input_hashes = {
        path.name: sha256_bytes(path.read_bytes())
        for path in sorted(input_dir.iterdir())
        if path.is_file()
    }
    from autoresearch.sector import pack as sector_pack_module

    pack = sector_pack_module._sector_pack_staging(snapshot["industry"], input_dir,
        profile="deterministic-v1" if candidate else "legacy")
    if snapshot["mode"] == "FULL" and snapshot["readthrough"]:
        pack["readthrough"] = snapshot["readthrough"]
    if int(pack.get("n_market") or 0) < 1:
        raise ValueError(
            f"industry is absent from verified market inputs: {snapshot['industry']}"
        )
    output = root / "session_outputs"
    manifest = {
        "schema_version": 1,
        "engine": snapshot["engine"],
        "analysis_date": snapshot["analysis_date"],
        "industry": snapshot["industry"],
        "source": snapshot["source"],
        "input_hashes": input_hashes,
    }
    atomic_write_json(output / "sector.inputs.json", manifest)
    atomic_write_json(output / "sector.pack.json", pack)
    reuse_value = {
        "schema_version": 1,
        "reused": False,
        "source": None,
        "sha256": None,
        "body": None,
    }
    reuse = snapshot["reuse"]
    if candidate:
        reuse_value = {"schema_version": 2, "profile": "deterministic-v1", **reuse}
    if reuse.get("reused") and not candidate:
        from autoresearch.sector.reuse import render_reused_brief

        body = render_reused_brief(
            reuse["previous_date"], reuse["shift_pp"], reuse["source_body"]
        )
        reuse_value = {
            "schema_version": 1,
            "reused": True,
            "source": reuse["source"],
            "sha256": sha256_bytes(body.encode("utf-8")),
            "body": body,
        }
    atomic_write_json(output / "sector.reuse.json", reuse_value)
    rendered = {
        "manifest": output / "sector.inputs.json",
        "pack": output / "sector.pack.json",
        "reuse": output / "sector.reuse.json",
    }
    if candidate:
        from autoresearch.sector.terrain import event_request
        events = event_request(pack, max_queries=snapshot["event_max_queries"],
                               knowledge_cutoff=snapshot["knowledge_cutoff"])
        atomic_write_json(output / "sector.events.request.json", events)
        rendered["events_request"] = output / "sector.events.request.json"
    return rendered


def sector_prepare(handle=None, *, scan_root: Path | str | None = None) -> dict:
    current = handle or _active_handle()
    snapshot = collect_sector_snapshot(current, scan_root=scan_root)
    from autoresearch.trace.source_receipts import record_active_response

    record_active_response(
        provider="sector.inputs",
        endpoint="sector.prepare.snapshot.v1",
        params={
            "analysis_date": snapshot["analysis_date"],
            "industry": snapshot["industry"],
            "mode": snapshot["mode"],
        },
        outcome=snapshot,
        consumer_artifact_ids=["sector.input.manifest", "sector.pack", "sector.reuse"]
        + (["sector.events.request"] if snapshot["schema_version"] == 2 else []),
    )
    rendered = render_sector_snapshot(snapshot, staging_root=current.staging)
    return {
        **json.loads(rendered["manifest"].read_text(encoding="utf-8")),
        "pack": json.loads(rendered["pack"].read_text(encoding="utf-8")),
        "reuse": json.loads(rendered["reuse"].read_text(encoding="utf-8")),
    }


def sector_terrain_render(handle=None, *, task: dict | None = None) -> dict:
    """One frozen task owns one deterministic terrain product in either workflow."""
    current = handle or _active_handle()
    if task is None:
        import os

        from autoresearch.session_agent.service import _task
        task = _task(current, os.environ["AUTORESEARCH_TASK_ID"])
    pack_id = next(key for key in task["input_artifact_ids"] if key.endswith(".pack"))
    request_id = next(key for key in task["input_artifact_ids"] if key.endswith(".events.request"))
    event_id = next((key for key in task["input_artifact_ids"] if key.endswith(".events")), None)
    pack = json.loads(_text(current, pack_id))
    request = json.loads(_text(current, request_id))
    supplement = json.loads(_text(current, event_id)) if event_id else None
    from autoresearch.sector.terrain import render_terrain
    from autoresearch.session_agent.sector_terrain import source_binding
    reuse_id = next((key for key in task["input_artifact_ids"] if key.endswith(".reuse")), None)
    reuse = json.loads(_text(current, reuse_id)) if reuse_id else {}
    previous = reuse.get("previous")
    binding = source_binding(current) if (supplement and supplement.get("events")) or (previous and previous['snapshot'].get('stable_facts')) or pack['terrain']['stable_facts'] else None
    from autoresearch.sector.reuse import reuse_stable_facts, stable_fact_snapshot
    if previous is not None:
        from autoresearch.sector.terrain import digest
        if digest(previous["snapshot"]) != previous.get("snapshot_sha256"):
            raise ValueError("stable fact snapshot identity changed")
    stable = reuse_stable_facts(pack, previous["snapshot"] if previous else None,
        ttl_days=int(reuse.get("ttl_days", 5)), knowledge_cutoff=request["knowledge_cutoff"], bind_claim=binding)
    fresh = reuse_stable_facts(pack, stable_fact_snapshot(pack, pack['terrain']['stable_facts']),
        ttl_days=0, knowledge_cutoff=request["knowledge_cutoff"], bind_claim=binding)
    facts = {sha256_bytes(canonical_json(fact).encode()): fact for fact in [*stable['stable_facts'], *fresh['stable_facts']]}
    text = render_terrain(pack, request=request, supplement=supplement, bind_claim=binding, stable_facts=list(facts.values()))
    artifact_id = next(key for key in task["output_artifact_ids"] if key.endswith((".report", ".brief")))
    stable_id = next(key for key in task["output_artifact_ids"] if key.endswith(".stable.snapshot"))
    atomic_write_json(artifacts.declared_path(current, stable_id), stable_fact_snapshot(pack, list(facts.values())))
    atomic_write_bytes(artifacts.declared_path(current, artifact_id), text.encode("utf-8"))
    from autoresearch.trace.source_receipts import record_active_response
    record_active_response(provider="sector.terrain", endpoint="sector.terrain.snapshot.v1",
        params={"analysis_date": current.analysis_date, "industry": pack["industry"]},
        outcome={"schema_version": 2, "pack": pack, "request": request, "supplement": supplement,
                 "stable_facts": list(facts.values()), "bound_event_hashes": [
                     sha256_bytes(canonical_json(event).encode()) for event in (supplement or {}).get("events", [])],
                 "bound_event_timings": {sha256_bytes(canonical_json(event).encode()): binding(event, request)["source_timing"]
                                         for event in (supplement or {}).get("events", [])}},
        consumer_artifact_ids=task["output_artifact_ids"])
    return {"profile": "deterministic-v1", "artifact_id": artifact_id, "sha256": sha256_bytes(text.encode("utf-8"))}


# 「买卖单」是 sector-brief 模板强制的资金流事实标签(「主动买卖单净流入合计」),不是方向措辞;
# 与 validation 同名正则保持一致。
_SECTOR_DIRECTIONS = re.compile(r"超配|低配|回避|买入|卖出|买卖(?!单)|看多|看空")
_SECTOR_SECTIONS = re.compile(r"(?m)^\s*#{1,6}\s*([1-6])[.、]\s*")


def sector_lite_validate(handle=None) -> dict:
    current = handle or _active_handle()
    from autoresearch.sector.brief import extract_terrain

    text = _text(current, "sector.report")
    terrain = extract_terrain(text)
    if not terrain:
        raise RuntimeError("sector lite report lacks terrain section")
    if _SECTOR_DIRECTIONS.search(terrain):
        raise RuntimeError("sector lite terrain contains directional language")
    reuse = json.loads(_text(current, "sector.reuse"))
    if reuse.get("reused") and text != reuse.get("body"):
        raise RuntimeError("reused sector brief changed during handoff")
    descriptor = artifacts.bind_artifact_hash(current, "sector.report")
    value = {
        "schema_version": 1,
        "contract": "sector.terrain.v1",
        "report_sha256": descriptor["sha256"],
        "reused": bool(reuse.get("reused")),
    }
    atomic_write_json(Path(current.staging) / "session_outputs/sector.validation.json", value)
    return value


def sector_full_validate(handle=None) -> dict:
    current = handle or _active_handle()
    text = _text(current, "sector.report")
    sections = sorted({int(match.group(1)) for match in _SECTOR_SECTIONS.finditer(text)})
    if sections != [1, 2, 3, 4, 5, 6]:
        raise RuntimeError("sector full report requires six sections")
    pack = json.loads(_text(current, "sector.pack"))
    for item in pack.get("readthrough") or []:
        if item.get("kind") == "company":
            continue
        symbol = re.escape(str(item.get("symbol") or ""))
        if symbol and re.search(rf"(?im)^.*{symbol}.*(?:公司|财报|业绩指引).*$", text):
            raise RuntimeError("non-company readthrough described as a company")
    if not pack.get("historical_valuation_percentile") and not re.search(
        r"历史估值分位.{0,12}(?:缺失|无|—|-)", text
    ):
        raise RuntimeError("missing historical valuation must be disclosed")
    descriptor = artifacts.bind_artifact_hash(current, "sector.report")
    value = {
        "schema_version": 1,
        "contract": "sector.full.v1",
        "sections": sections,
        "report_sha256": descriptor["sha256"],
    }
    atomic_write_json(Path(current.staging) / "session_outputs/sector.validation.json", value)
    return value


def sector_validate(handle=None) -> dict:
    current = handle or _active_handle()
    request = _request(current)
    return (
        sector_lite_validate(current)
        if request["requested_mode"] == "LITE"
        else sector_full_validate(current)
    )


def sector_prepare_publication(handle=None) -> dict:
    current = handle or _active_handle()
    request = _request(current)
    validation = json.loads(_text(current, "sector.validation"))
    report = artifacts.bind_artifact_hash(current, "sector.report")
    if validation.get("report_sha256") != report["sha256"]:
        raise RuntimeError("validated sector report changed before publication")
    value = {
        "schema_version": 1,
        "kind": "sector-research",
        "mode": request["requested_mode"],
        "run_id": current.run_id,
        "engine": current.engine,
        "analysis_date": request["analysis_date"],
        "industry": request["subject"],
        "report_sha256": report["sha256"],
    }
    atomic_write_json(Path(current.staging) / "session_outputs/sector.publication.json", value)
    return value


def render_dossier_prefetch_snapshot(snapshot: dict, *, output_path: Path | str) -> Path:
    if not isinstance(snapshot, dict) or snapshot.get("schema_version") != 1:
        raise ValueError("invalid dossier prefetch snapshot")
    data = snapshot.get("data")
    if not isinstance(data, dict):
        raise TypeError("dossier prefetch data must be an object")
    payload = json.dumps(data, ensure_ascii=False, indent=1).encode("utf-8")
    return atomic_write_bytes(output_path, payload)


def dossier_prefetch_run(handle=None) -> dict:
    current = handle or _active_handle()
    request = _request(current)
    output = Path(current.staging) / "session_outputs"
    data = dossier_prefetch.prefetch_one(
        request["subject"], request["analysis_date"], out_dir=output / "prefetch"
    )
    source = output / "prefetch" / f"{request['subject']}.json"
    snapshot = {"schema_version": 1, "data": data}
    from autoresearch.trace.source_receipts import record_active_response

    record_active_response(
        provider="dossier.prefetch",
        endpoint="dossier.prefetch.snapshot.v1",
        params={
            "analysis_date": request["analysis_date"],
            "code": request["subject"],
        },
        outcome=snapshot,
        consumer_artifact_ids=["dossier.prefetch"],
        raw_bytes=source.read_bytes(),
    )
    render_dossier_prefetch_snapshot(snapshot, output_path=output / "dossier.prefetch.json")
    return {
        "code": request["subject"],
        "degraded": bool(data.get("notes")),
        "notes": data.get("notes") or [],
    }


def _summary_values(text: str) -> dict[str, str]:
    block = dossier_schema._summary_block(text)
    values = {}
    for anchor in dossier_schema.SUMMARY_ANCHORS:
        match = re.search(rf"(?m)^-\s*{re.escape(anchor)}\s*(.*)$", block)
        values[anchor] = match.group(1).strip() if match else ""
    return values


def _dossier_permissions(skeleton: str, *, target: Path, opening_hash: str | None) -> dict:
    from autoresearch.dossier.facts import parse_ledger
    ledger = parse_ledger(skeleton)
    deterministic = {}
    for index in (2, 3, 5, 6, 7):
        block = dossier_schema._section_block(skeleton, dossier_schema.SECTIONS[index])
        deterministic[str(index + 1)] = sha256_bytes(block.encode("utf-8"))
    prefixes = {}
    for index in (0, 1):
        block = dossier_schema._section_block(skeleton, dossier_schema.SECTIONS[index])
        prefix = block.partition(dossier_builder._LLM_ANCHOR)[0]
        prefixes[str(index + 1)] = {
            "sha256": sha256_bytes(prefix.encode("utf-8")),
            "text": prefix,
        }
    summary = _summary_values(skeleton)
    frontmatter = dossier_schema.parse_frontmatter(skeleton)
    return {
        "schema_version": 1,
        "target": str(target),
        "opening_target_sha256": opening_hash,
        "frontmatter": {key: value for key, value in frontmatter.items() if key != "initiated"},
        "deterministic_sections": deterministic,
        "protected_prefixes": prefixes,
        "summary_fixed": {anchor: summary[anchor] for anchor in ("带位:", "判例:")},
        "fact_ledger": ledger,
    }


_DOSSIER_STAGING_FILES = frozenset({"seats.csv", "pledge.csv", "calendar.csv"})


def _dossier_scan_sources(scan_root: Path, code: str, analysis_date: str) -> dict[str, str]:
    if not scan_root.is_dir():
        return {}
    days = sorted(
        (path for path in scan_root.iterdir() if path.is_dir() and path.name[:2] == "20"),
        key=lambda path: path.name,
        reverse=True,
    )
    selected: set[Path] = set()
    latest = next(
        (
            day
            for day in days
            if any((day / name).is_file() for name in _DOSSIER_STAGING_FILES)
        ),
        None,
    )
    if latest is not None:
        selected.update(
            latest / name for name in _DOSSIER_STAGING_FILES if (latest / name).is_file()
        )
    for day in [item for item in days if item.name != analysis_date][
        : dossier_builder._PRECEDENT_WINDOW
    ]:
        selected.update(
            path
            for path in (day / "finalists.csv", day / "verify.csv")
            if path.is_file()
        )
        details = day / "details"
        if details.is_dir():
            selected.update(path for path in details.glob(f"{code}*.md") if path.is_file())
    return {
        path.relative_to(scan_root).as_posix(): _encode_payload(path.read_bytes())
        for path in sorted(selected)
        if not path.is_symlink()
    }


def collect_dossier_skeleton_snapshot(
    handle=None,
    *,
    target_path: Path | str | None = None,
    scan_root: Path | str | None = None,
) -> dict:
    current = handle or _active_handle()
    request = _request(current)
    target = (
        Path(target_path)
        if target_path is not None
        else dossier_schema.dossier_path(request["subject"])
    )
    if target_path is not None:
        opening_bytes = target.read_bytes() if target.is_file() else None
        publication_base = sha256_bytes(opening_bytes) if opening_bytes is not None else None
    else:
        opening_bytes, publication_base = dossier_schema.read_dossier_snapshot(request["subject"])
    source_root = Path(scan_root) if scan_root is not None else ws.scan_root()
    return {
        "schema_version": 2,
        "analysis_date": request["analysis_date"],
        "code": request["subject"],
        "name": request.get("name") or "",
        "target": str(target),
        "publication_base_sha256": publication_base,
        "opening_target": (
            _encode_payload(opening_bytes) if opening_bytes is not None else None
        ),
        "scan_files": _dossier_scan_sources(
            source_root, request["subject"], request["analysis_date"]
        ),
    }


def _restore_dossier_scan(snapshot: dict, scratch_root: Path) -> Path:
    scan_root = scratch_root / "scan"
    scan_files = snapshot.get("scan_files")
    if not isinstance(scan_files, dict):
        raise TypeError("dossier scan_files must be an object")
    code = str(snapshot["code"])
    for relative, payload in scan_files.items():
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts or len(path.parts) not in {2, 3}:
            raise ValueError("unsafe dossier scan source path")
        allowed = (
            len(path.parts) == 2
            and path.name in {"finalists.csv", "verify.csv", *_DOSSIER_STAGING_FILES}
        ) or (
            len(path.parts) == 3
            and path.parts[1] == "details"
            and path.name.startswith(code)
            and path.suffix == ".md"
        )
        if not allowed or not re.fullmatch(r"20\d{2}-\d{2}-\d{2}", path.parts[0]):
            raise ValueError("unregistered dossier scan source path")
        atomic_write_bytes(scan_root / path, _decode_payload(payload))
    return scan_root


def render_dossier_skeleton_snapshot(
    snapshot: dict,
    *,
    prefetch_path: Path | str,
    output_dir: Path | str,
    scratch_root: Path | str,
) -> dict[str, Path]:
    required = {
        "schema_version",
        "analysis_date",
        "code",
        "name",
        "target",
        "opening_target",
        "scan_files",
    }
    version = snapshot.get("schema_version")
    if version == 2:
        required.add("publication_base_sha256")
    if set(snapshot) != required or type(version) is not int or version not in {1, 2}:
        raise ValueError("invalid dossier skeleton snapshot contract")
    output = Path(output_dir)
    skeleton_path = output / "dossier.skeleton.md"
    opening = snapshot["opening_target"]
    opening_bytes = _decode_payload(opening) if opening is not None else None
    opening_hash = sha256_bytes(opening_bytes) if opening_bytes is not None else None
    if opening_bytes is not None:
        existing = opening_bytes.decode("utf-8")
        if dossier_schema.parse_frontmatter(existing).get("initiated"):
            raise RuntimeError("dossier is already initialized")
        atomic_write_bytes(skeleton_path, opening_bytes)
        issues = dossier_schema.lint_dossier(existing)
    else:
        restored_scan = _restore_dossier_scan(snapshot, Path(scratch_root))
        built = dossier_builder._build_skeleton_unlocked(
            snapshot["code"],
            snapshot["analysis_date"],
            name=snapshot["name"],
            scan_root=restored_scan,
            output_path=skeleton_path,
            prefetch_path=prefetch_path,
        )
        issues = built["issues"]
    if issues:
        raise RuntimeError(f"dossier skeleton is invalid: {issues}")
    skeleton = skeleton_path.read_text(encoding="utf-8")
    permissions = _dossier_permissions(
        skeleton, target=Path(snapshot["target"]), opening_hash=opening_hash
    )
    if version == 2:
        base_hash = snapshot["publication_base_sha256"]
        if base_hash is not None and (not isinstance(base_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", base_hash)):
            raise ValueError("invalid dossier publication base hash")
        permissions["schema_version"] = 2
        permissions["publication_base_sha256"] = base_hash
    permissions_path = atomic_write_json(output / "dossier.permissions.json", permissions)
    return {"skeleton": skeleton_path, "permissions": permissions_path}


def dossier_build_skeleton(
    handle=None,
    *,
    target_path: Path | str | None = None,
    scan_root: Path | str | None = None,
) -> dict:
    current = handle or _active_handle()
    output = Path(current.staging) / "session_outputs"
    snapshot = collect_dossier_skeleton_snapshot(
        current, target_path=target_path, scan_root=scan_root
    )
    from autoresearch.trace.source_receipts import record_active_response

    record_active_response(
        provider="dossier.opening",
        endpoint="dossier.skeleton.snapshot.v1",
        params={
            "analysis_date": snapshot["analysis_date"],
            "code": snapshot["code"],
        },
        outcome=snapshot,
        consumer_artifact_ids=["dossier.skeleton", "dossier.permissions"],
    )
    rendered = render_dossier_skeleton_snapshot(
        snapshot,
        prefetch_path=output / "dossier.prefetch.json",
        output_dir=output,
        scratch_root=Path(current.staging) / "dossier_inputs",
    )
    permissions = json.loads(rendered["permissions"].read_text(encoding="utf-8"))
    return permissions


def _validate_dossier_candidate(current) -> dict:
    request = _request(current)
    candidate = _text(current, "dossier.candidate")
    permissions = json.loads(_text(current, "dossier.permissions"))
    issues = dossier_schema.lint_dossier(candidate)
    if issues:
        raise RuntimeError(";".join(issues))
    from autoresearch.dossier.facts import parse_ledger
    ledger = parse_ledger(candidate)
    opening = permissions.get('fact_ledger')
    if opening is not None:
        if ledger is None:
            raise RuntimeError('dossier fact ledger removed')
        if ledger['history'][:len(opening['history'])] != opening['history']:
            raise RuntimeError('dossier fact history changed')
        current_facts = {row['fact_id']: row for row in ledger['facts']}
        if any(current_facts.get(row['fact_id']) != row for row in opening['facts']):
            raise RuntimeError('existing dossier facts require sourced delta reconciliation')
    meta = dossier_schema.parse_frontmatter(candidate)
    if meta.get("initiated") != request["analysis_date"]:
        raise RuntimeError("dossier initiated date is missing or incorrect")
    if {key: value for key, value in meta.items() if key != "initiated"} != permissions[
        "frontmatter"
    ]:
        raise RuntimeError("dossier deterministic frontmatter changed")
    for raw_index, expected in permissions["deterministic_sections"].items():
        index = int(raw_index) - 1
        block = dossier_schema._section_block(candidate, dossier_schema.SECTIONS[index])
        if sha256_bytes(block.encode("utf-8")) != expected:
            raise RuntimeError(f"dossier deterministic section changed: {raw_index}")
    for raw_index, expected in permissions["protected_prefixes"].items():
        index = int(raw_index) - 1
        block = dossier_schema._section_block(candidate, dossier_schema.SECTIONS[index])
        prefix = expected["text"]
        if (
            not block.startswith(prefix)
            or sha256_bytes(prefix.encode("utf-8")) != expected["sha256"]
        ):
            raise RuntimeError(f"dossier deterministic section prefix changed: {raw_index}")
    if dossier_builder._LLM_ANCHOR in candidate:
        raise RuntimeError("dossier research anchors remain unfinished")
    summary = _summary_values(candidate)
    for anchor, expected in permissions["summary_fixed"].items():
        if summary.get(anchor) != expected:
            raise RuntimeError(f"dossier deterministic summary changed: {anchor}")
    for anchor in ("业务:", "驱动:", "风险:", "催化:"):
        if not summary.get(anchor) or summary[anchor] == dossier_builder._NARRATIVE_PENDING:
            raise RuntimeError(f"dossier summary remains unfinished: {anchor}")
    descriptor = artifacts.bind_artifact_hash(current, "dossier.candidate")
    return {
        "schema_version": 1,
        "contract": "dossier.v1",
        "code": request["subject"],
        "candidate_sha256": descriptor["sha256"],
        "summary_tokens": dossier_schema.est_tokens(dossier_schema._summary_block(candidate)),
        "fact_coverage": {'declared': len(ledger['facts']) if ledger else 0,
                          'semantic_coverage': 'UNKNOWN',
                          'reuse_requires_current_source_verification': True},
    }


def dossier_validate(handle=None) -> dict:
    current = handle or _active_handle()
    value = _validate_dossier_candidate(current)
    atomic_write_json(Path(current.staging) / "session_outputs/dossier.validation.json", value)
    return value


def collect_dossier_pool_snapshot() -> dict:
    """Freeze the exact pool version that publication intends to update."""
    from autoresearch.common.published_state import read_committed_bytes
    from autoresearch.dossier import pool as dossier_pool

    pool_path = Path(dossier_pool.POOL_PATH)
    committed_pool = read_committed_bytes(
        "dossier.coverage_pool",
        state_root=ws.context_root() / "_published_state",
        reports_root=ws.run_reports_root("dossier-init"),
    )
    pool_before = (
        committed_pool
        if committed_pool is not None
        else (pool_path.read_bytes() if pool_path.is_file() else None)
    )
    current_pool = (
        json.loads(committed_pool.decode("utf-8"))
        if committed_pool is not None
        else dossier_pool.load_pool(pool_path)
    )
    return {
        "schema_version": 1,
        "pool_before_text": (
            pool_before.decode("utf-8") if pool_before is not None else None
        ),
        "current_pool": current_pool,
    }


def render_dossier_publication_snapshot(current, snapshot: dict) -> list[dict]:
    """Build the candidate pool and a non-executable CAS mutation plan."""
    if set(snapshot) != {"schema_version", "pool_before_text", "current_pool"}:
        raise ValueError("invalid dossier pool snapshot contract")
    if snapshot["schema_version"] != 1 or not isinstance(snapshot["current_pool"], dict):
        raise ValueError("invalid dossier pool snapshot")
    request = _request(current)
    validation = json.loads(_text(current, "dossier.validation"))
    candidate = artifacts.bind_artifact_hash(current, "dossier.candidate")
    if validation.get("candidate_sha256") != candidate["sha256"]:
        raise RuntimeError("validated dossier candidate changed before publication")
    permissions = json.loads(_text(current, "dossier.permissions"))
    from autoresearch.session_agent.workflows.dossier import build_pool_candidate

    pool_before_text = snapshot["pool_before_text"]
    pool_before = (
        pool_before_text.encode("utf-8") if pool_before_text is not None else None
    )
    pool_before_sha256 = sha256_bytes(pool_before) if pool_before is not None else None
    pool_candidate = build_pool_candidate(
        request["subject"],
        request.get("name"),
        snapshot["current_pool"],
    )
    pool_candidate_path = Path(current.staging) / "session_outputs/dossier.pool.candidate.json"
    atomic_write_json(pool_candidate_path, pool_candidate)
    value = {
        "schema_version": 1,
        "kind": "dossier-init",
        "mode": "INIT",
        "run_id": current.run_id,
        "engine": current.engine,
        "analysis_date": request["analysis_date"],
        "code": request["subject"],
        "candidate_sha256": candidate["sha256"],
        "pool_before_sha256": pool_before_sha256,
        "pool_after_sha256": sha256_bytes(pool_candidate_path.read_bytes()),
    }
    atomic_write_json(Path(current.staging) / "session_outputs/dossier.publication.json", value)
    return [
        {
            "target_key": f"dossier.stock.{request['subject']}",
            "expected_before_hash": permissions.get("publication_base_sha256", permissions.get("opening_target_sha256")),
            "after_artifact_id": "dossier.candidate",
            "after_hash": candidate["sha256"],
            "apply_policy": "CAS_REPLACE",
        },
        {
            "target_key": "dossier.coverage_pool",
            "expected_before_hash": pool_before_sha256,
            "after_artifact_id": "dossier.pool.candidate",
            "after_hash": value["pool_after_sha256"],
            "apply_policy": "CAS_REPLACE",
        },
    ]


def dossier_prepare_publication(handle=None) -> dict:
    current = handle or _active_handle()
    snapshot = collect_dossier_pool_snapshot()
    request = _request(current)
    from autoresearch.trace.source_receipts import record_active_response

    record_active_response(
        provider="dossier.pool",
        endpoint="dossier.pool.snapshot.v1",
        params={
            "analysis_date": request["analysis_date"],
            "code": request["subject"],
        },
        outcome=snapshot,
        consumer_artifact_ids=[
            "dossier.pool.candidate",
            "dossier.publication.bundle",
        ],
    )
    render_dossier_publication_snapshot(current, snapshot)
    return json.loads(
        (Path(current.staging) / "session_outputs/dossier.publication.json").read_text(
            encoding="utf-8"
        )
    )


# `_dispatch/` is the runner↔host mailbox: control traffic that is written while
# deterministic bundles are collected, never scan state (contracts: dispatch_*).
_SCAN_BUNDLE_CONTROL_ROOTS = frozenset({"session_outputs", "_dispatch"})


def collect_scan_staging_bundle(root: Path | str, *, phase: str) -> dict:
    """Freeze a portable scan staging state without session control files.

    The bundle is an explicit workflow artifact, not an ambient directory
    escape hatch.  Report phases include only the run-scoped report build under
    ``session_outputs``; task state, claims and artifact registries stay out.
    """
    base = Path(root)
    files: dict[str, str] = {}
    if base.is_dir():
        for path in sorted(base.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            relative = path.relative_to(base)
            if relative.name.startswith(".session-agent") or relative.name.endswith(".lock"):
                continue
            if (
                relative.parts
                and relative.parts[0] in _SCAN_BUNDLE_CONTROL_ROOTS
                and not (
                    phase in {"report", "used_report", "observed_report"}
                    and relative.parts[:2] == ("session_outputs", "report_build")
                )
            ):
                continue
            files[relative.as_posix()] = base64.b64encode(path.read_bytes()).decode("ascii")
    return {"schema_version": 1, "phase": phase, "files": files}


def restore_scan_staging_bundle(
    bundle: dict,
    root: Path | str,
    *,
    expected_phase: str | None = None,
) -> Path:
    """Restore one declared scan state bundle into an isolated staging root."""
    if set(bundle) != {"schema_version", "phase", "files"}:
        raise ValueError("invalid scan staging bundle contract")
    if bundle["schema_version"] != 1 or not isinstance(bundle["files"], dict):
        raise ValueError("invalid scan staging bundle")
    if expected_phase is not None and bundle["phase"] != expected_phase:
        raise ValueError(
            f"scan staging bundle phase mismatch:{bundle['phase']} != {expected_phase}"
        )
    target_root = Path(root)
    for raw_relative, encoded in bundle["files"].items():
        relative = Path(raw_relative)
        if (
            relative.is_absolute()
            or not relative.parts
            or ".." in relative.parts
            or relative.parts[0] == "session"
        ):
            raise ValueError(f"unsafe scan bundle path:{raw_relative}")
        try:
            payload = base64.b64decode(str(encoded).encode("ascii"), validate=True)
        except Exception as exc:  # noqa: BLE001 - normalize corrupt bundle failures
            raise ValueError(f"invalid scan bundle payload:{raw_relative}") from exc
        atomic_write_bytes(target_root / relative, payload)
    return target_root


def _write_scan_bundle(current, phase: str, artifact_name: str) -> dict:
    bundle = collect_scan_staging_bundle(current.staging, phase=phase)
    atomic_write_json(Path(current.staging) / "session_outputs" / artifact_name, bundle)
    return bundle


def _freeze_scan_runtime_inputs(current) -> dict:
    """Copy mutable scan configuration/state inputs into run-local staging."""
    from autoresearch.scan import user_config

    target = Path(current.staging) / "_session_inputs"
    target.mkdir(parents=True, exist_ok=True)
    sources = {
        "scan_config.jsonc": Path(user_config.DEFAULT_PATH),
        "pinned.jsonc": Path(user_config.DEFAULT_PINNED_PATH),
        "L1_weights.json": ws.factor_lab_root() / "weights.json",
    }
    present = {}
    for name, source in sources.items():
        destination = target / name
        present[name] = source.is_file()
        if source.is_file():
            shutil.copyfile(source, destination)
        else:
            destination.unlink(missing_ok=True)
    # 2026-09-24 §2.1:preference 档时 L1_weights.json(校准文件)不再是当天用的真相——
    # 快照改记「实际生效的权重文档」(resolve_weights 会用到的那份),而不是继续复制一份
    # 谁都没读过的校准文件当"证据"。身份快照失败不挡 session,但必须留痕(不静默吞错)。
    value_error: str | None = None
    try:
        cfg = user_config.load_user_config() or {}
        funnel = cfg.get("funnel") or {}
        if funnel.get("weight_profile") == "preference":
            from autoresearch.common.scoring import preference_weights_doc

            atomic_write_json(target / "L1_weight_profile.json",
                              preference_weights_doc(funnel.get("preference_weights") or {}))
            present["L1_weight_profile.json"] = True
    except Exception as exc:  # noqa: BLE001 — 身份快照失败不挡 session,但要留痕
        present["L1_weight_profile.json"] = False
        value_error = repr(exc)
    # 2026-09-25 终审 M4:schema_version=1 的形状历史上恰是两键 {"schema_version", "present"}。
    # `weight_profile_error` 是新增的第三键(哪怕干净跑完也恒在、值 None)——identity 快照
    # 的形状变了就该挪版本号,不能靠"新键缺省 None"悄悄冒充旧形状;bump 到 2 让任何读者从
    # 这一个数字就能判断该按几个键去读,不用先探测键是否存在。
    value = {"schema_version": 2, "present": present, "weight_profile_error": value_error}
    atomic_write_json(target / "manifest.json", value)
    return value


def _use_frozen_scan_runtime_inputs(current) -> None:
    """Route mutable config readers to the prelude-frozen run-local copies."""
    frozen = Path(current.staging) / "_session_inputs"
    if not (frozen / "manifest.json").is_file():
        return
    from autoresearch.scan import user_config

    user_config.DEFAULT_PATH = frozen / "scan_config.jsonc"
    user_config.DEFAULT_PINNED_PATH = frozen / "pinned.jsonc"


def _record_scan_source(
    *,
    provider: str,
    endpoint: str,
    current,
    outcome: dict,
    consumers: list[str],
) -> None:
    if ws.active_run_id() is None:
        return
    from autoresearch.trace.source_receipts import record_active_response

    record_active_response(
        provider=provider,
        endpoint=endpoint,
        params={"analysis_date": current.analysis_date},
        outcome=outcome,
        consumer_artifact_ids=consumers,
    )


def render_scan_frame_snapshot(snapshot: dict, *, output_dir: Path | str) -> dict[str, Path]:
    if set(snapshot) != {"schema_version", "market_pack", "strategist_pack"}:
        raise ValueError("invalid scan frame snapshot contract")
    if snapshot["schema_version"] != 1:
        raise ValueError("invalid scan frame snapshot")
    output = Path(output_dir)
    market = atomic_write_json(output / "market_pack.json", snapshot["market_pack"])
    strategist = atomic_write_json(
        output / "strategist_pack.json", snapshot["strategist_pack"]
    )
    return {"market_pack": market, "strategist_pack": strategist}


def scan_frame(handle=None) -> dict:
    current = handle or _active_handle()
    from autoresearch.scan import frame, strategist_pack

    market = Path(current.staging) / "market_pack.json"
    projected = Path(current.staging) / "strategist_pack.json"
    if frame.main([current.analysis_date, "--json-out", str(market)]) != 0:
        raise RuntimeError("scan frame failed")
    if strategist_pack.main([str(market), "--out", str(projected)]) != 0:
        raise RuntimeError("strategist projection failed")
    snapshot = {
        "schema_version": 1,
        "market_pack": json.loads(market.read_text(encoding="utf-8")),
        "strategist_pack": json.loads(projected.read_text(encoding="utf-8")),
    }
    _record_scan_source(
        provider="scan.frame",
        endpoint="scan.frame.snapshot.v1",
        current=current,
        outcome=snapshot,
        consumers=["scan.market.pack", "scan.strategist.pack"],
    )
    return {"schema_version": 1, "market_pack": str(market), "strategist_pack": str(projected)}


def scan_prelude(handle=None) -> dict:
    current = handle or _active_handle()
    from autoresearch.scan.prelude import STEP_NAMES, run_prelude

    results = run_prelude(current.analysis_date)
    if [item["step"] for item in results] != list(STEP_NAMES):
        raise RuntimeError("prelude result steps differ from STEP_NAMES")
    summary = Path(current.staging) / "_prelude_summary.md"
    l2 = Path(current.staging) / "L2_gbdt_top200.csv"
    if not summary.is_file() or not l2.is_file():
        raise RuntimeError("prelude required outputs are missing")
    _freeze_scan_runtime_inputs(current)
    bundle = _write_scan_bundle(current, "prelude", "prelude.bundle.json")
    _record_scan_source(
        provider="scan.prelude",
        endpoint="scan.prelude.snapshot.v1",
        current=current,
        outcome={
            "schema_version": 1,
            "step_names": list(STEP_NAMES),
            "steps": results,
            "bundle": bundle,
        },
        consumers=["scan.prelude.summary", "scan.l2", "scan.prelude.bundle"],
    )
    return {
        "schema_version": 1,
        "steps": results,
        "summary": str(summary),
        "l2": str(l2),
        "bundle_files": len(bundle["files"]),
    }


def scan_gate1(handle=None) -> dict:
    current = handle or _active_handle()
    from autoresearch.scan.gates import gate1_decide, record_gate_stage_result

    result = gate1_decide(Path(current.staging), force_full=bool(_request(current)["force_full"]))
    record_gate_stage_result(Path(current.staging), result)
    budget = result.get("l4_budget")
    if result.get("ok") and (type(budget) is not int or budget < 1):
        result = {**result, "ok": False, "reason": "invalid GATE1 l4_budget"}
    atomic_write_json(Path(current.staging) / "session_outputs/gate1.json", result)
    if not result.get("ok"):
        raise RuntimeError(str(result.get("reason") or "GATE1 failed"))
    return result


def scan_sector_prepare(handle=None) -> dict:
    current = handle or _active_handle()
    _use_frozen_scan_runtime_inputs(current)
    from autoresearch.scan.user_config import knob
    from autoresearch.sector import pack as sector_pack, reuse as sector_reuse
    from autoresearch.session_agent.workflows.scan import _sector_key

    scan_dir = Path(current.staging)
    candidate = _request(current).get("sector_brief_profile", "legacy") == "deterministic-v1"
    cap = int(knob("sector", "max_briefs", None, 6))
    if candidate and not 1 <= cap <= 6:
        raise ValueError("deterministic sector coverage requires 1 <= K <= 6")
    sectors, provenance = sector_pack.select_briefing_sectors(
        scan_dir, k=cap, healthy_top3_extra=False if candidate else None,
    )
    found = {} if candidate else sector_reuse.find_reusable(current.analysis_date, sectors,
                                       ttl_days=int(knob("sector", "reuse_ttl_days", None, 5)))
    if found:
        sector_reuse.apply_reuse(
            current.analysis_date,
            found,
            root=Path(current.staging).parent,
        )
    pack_dir = scan_dir / "session_inputs/sectors"
    rows = []
    for industry in sectors:
        key = _sector_key(str(industry))
        payload = (sector_pack._sector_pack_staging(industry, scan_dir, profile="deterministic-v1")
                   if candidate else sector_pack.sector_pack(industry, scan_dir))
        atomic_write_json(pack_dir / f"{key}.json", payload)
        extra = {}
        if candidate:
            from autoresearch.sector.terrain import event_request
            from autoresearch.session_agent.dispatch import sector_brief_web_searches
            frame = json.loads(_text(current, "research.frame"))
            request = event_request(payload,
                max_queries=sector_brief_web_searches(getattr(current.contract, "user_config", {})),
                knowledge_cutoff=frame["knowledge_cutoff"])
            atomic_write_json(pack_dir / f"{key}.events.request.json", request)
            ttl = int(knob("sector", "reuse_ttl_days", None, 5))
            previous = sector_reuse.find_stable_snapshot(current.analysis_date, industry, ttl_days=ttl)
            atomic_write_json(pack_dir / f"{key}.reuse.json", {"schema_version": 2,
                "profile": "deterministic-v1", "reused": False, "previous": previous, "ttl_days": ttl})
            extra = {"profile": "deterministic-v1", "needs_events": request["dispatch"]}
        rows.append(
            {
                "industry": str(industry),
                "key": key,
                "reused": industry in found,
                "provenance": provenance.get(industry),
                **extra,
            }
        )
    value = {"schema_version": 1, "mode": "FULL", "sectors": rows}
    atomic_write_json(scan_dir / "session_outputs/sector.list.json", value)
    bundle = _write_scan_bundle(current, "sector", "sector.source.bundle.json")
    _record_scan_source(
        provider="scan.sector",
        endpoint="scan.sector.snapshot.v1",
        current=current,
        outcome={"schema_version": 1, "bundle": bundle, "sector_list": value},
        consumers=["scan.sector.list", "scan.sector.source.bundle"],
    )
    return value


def scan_sector_skip(handle=None) -> dict:
    current = handle or _active_handle()
    mode = json.loads(_text(current, "scan.run_mode"))
    if mode.get("mode") not in {"SENTINEL_EMPTY", "SENTINEL_PINNED"}:
        raise RuntimeError("sector skip only applies to sentinel modes")
    value = {
        "schema_version": 1,
        "mode": mode["mode"],
        "pinned_codes": list(mode.get("pinned_codes") or []),
        "sectors": [],
        "reason": "not applicable in sentinel mode",
    }
    atomic_write_json(Path(current.staging) / "session_outputs/sector.list.json", value)
    _write_scan_bundle(current, "sector", "sector.source.bundle.json")
    return value


def scan_l3_prepare(handle=None) -> dict:
    current = handle or _active_handle()
    _use_frozen_scan_runtime_inputs(current)
    from autoresearch.scan.l3.prompt import prepare_l3_table

    value = prepare_l3_table(
        current.analysis_date,
        root=Path(current.staging).parent,
    )
    bundle = _write_scan_bundle(current, "l3_source", "l3.source.bundle.json")
    _record_scan_source(
        provider="scan.l3.prepare",
        endpoint="scan.l3.prepare.snapshot.v1",
        current=current,
        outcome=bundle,
        consumers=["scan.l3.table", "scan.l3.source.bundle"],
    )
    return value


def scan_l3_lint(handle=None) -> dict:
    current = handle or _active_handle()
    _use_frozen_scan_runtime_inputs(current)
    from autoresearch.scan.l3.validation import build_repair_pack, lint_judged

    root = Path(current.staging).parent
    value = lint_judged(current.analysis_date, root=root)
    atomic_write_json(Path(current.staging) / "session_outputs/l3.validation.json", value)
    # Always materialize the bounded repair contract.  On a clean pass it is an
    # explicit empty pack, so later expansion evidence never depends on an
    # unowned ambient file.
    build_repair_pack(current.analysis_date, root=root)
    _write_scan_bundle(current, "l3_context", "l3.context.bundle.json")
    return value


def scan_l3_repair_skip(handle=None) -> dict:
    current = handle or _active_handle()
    value = {
        "schema_version": 1,
        "status": "NOT_REQUIRED",
        "patched": 0,
        "codes": [],
    }
    atomic_write_json(Path(current.staging) / "session_outputs/l3.repair.json", value)
    judged_path = Path(current.staging) / "_l3_judged.json"
    if artifacts.layout_version(current) >= 2:
        data = artifacts.read_bytes(current, 'scan.l3.judged')
        atomic_write_bytes(Path(current.staging) / '_l3_effective_judged.json', data)
    elif judged_path.is_file():
        atomic_write_bytes(
            Path(current.staging) / "_l3_effective_judged.json",
            judged_path.read_bytes(),
        )
    return value


def scan_l3_repair_apply(handle=None) -> dict:
    current = handle or _active_handle()
    _use_frozen_scan_runtime_inputs(current)
    from autoresearch.scan.l3.validation import apply_repair_patch

    judged = Path(current.staging) / "_l3_judged.json"
    opening = judged.read_bytes()
    try:
        applied = apply_repair_patch(
            current.analysis_date,
            root=Path(current.staging).parent,
        )
        effective = judged.read_bytes()
    finally:
        atomic_write_bytes(judged, opening)
    atomic_write_bytes(Path(current.staging) / "_l3_effective_judged.json", effective)
    value = {"schema_version": 1, "status": "APPLIED", **applied}
    atomic_write_json(Path(current.staging) / "session_outputs/l3.repair.json", value)
    return value


def scan_l3_repair_degraded(error: dict, handle=None) -> dict:
    """Record that the optional repair failed and the original judged set is retained."""
    current = handle or _active_handle()
    value = {
        "schema_version": 1,
        "status": "DEGRADED",
        "patched": 0,
        "codes": [],
        "preserved_original": True,
        "error": {
            "code": str(error.get("code") or "UNKNOWN"),
            "message": str(error.get("message") or "optional L3 repair failed"),
        },
    }
    atomic_write_json(Path(current.staging) / "session_outputs/l3.repair.json", value)
    judged_path = Path(current.staging) / "_l3_judged.json"
    if artifacts.layout_version(current) >= 2:
        atomic_write_bytes(Path(current.staging) / '_l3_effective_judged.json',
                           artifacts.read_bytes(current, 'scan.l3.judged'))
    elif judged_path.is_file():
        atomic_write_bytes(
            Path(current.staging) / "_l3_effective_judged.json",
            judged_path.read_bytes(),
        )
    return value


def scan_l3_merge(handle=None) -> dict:
    current = handle or _active_handle()
    _use_frozen_scan_runtime_inputs(current)
    from autoresearch.scan.gates import gate2, record_gate_stage_result
    from autoresearch.scan.l3.merge import write_finalists

    gate1 = json.loads(_text(current, "scan.gate1.result"))
    # L4 卡数(2026-09-26 l4.max_cards):只读 GATE1 回显 —— l3cap 进 write_finalists,max_cards 做
    # GATE2 预算(GATE2 数非豁免 lane 全部行,composite 席位也算)。老 run 的冻结 GATE1 没有这两键:
    # 回退旗后预算(= 改动前行为)并留痕。
    budget = gate1.get("l3cap", gate1.get("l4_budget"))
    gate2_budget = gate1.get("max_cards", gate1.get("l4_budget"))
    if "l3cap" not in gate1 or "max_cards" not in gate1:
        atomic_write_json(Path(current.staging) / "session_outputs/l3_merge_note.json",
                          {"fallback": "l4_budget", "budget": budget, "gate2_budget": gate2_budget,
                           "reason": "frozen GATE1 lacks l3cap/max_cards (pre-2026-09-26 run)"})
    if type(budget) is not int or budget < 1 or type(gate2_budget) is not int or gate2_budget < 1:
        raise RuntimeError("invalid frozen GATE1 l3cap/max_cards/l4_budget")
    write_finalists(
        current.analysis_date,
        budget=budget,
        root=Path(current.staging).parent,
        judged_path=Path(current.staging) / "_l3_effective_judged.json",
    )
    result = gate2(Path(current.staging), budget=gate2_budget)
    record_gate_stage_result(Path(current.staging), result, budget=gate2_budget)
    atomic_write_json(Path(current.staging) / "session_outputs/gate2.json", result)
    if not result.get("ok"):
        raise RuntimeError(str(result.get("reason") or "GATE2 failed"))
    _write_scan_bundle(current, "l3_final", "l3.final.bundle.json")
    return result


def scan_gate2_skip(handle=None) -> dict:
    current = handle or _active_handle()
    from autoresearch.scan import run_mode
    from autoresearch.scan.gates import gate2, record_gate_stage_result

    mode = run_mode.load(Path(current.staging))
    if mode is None or not mode.is_sentinel:
        raise RuntimeError("GATE2 skip requires a frozen sentinel mode")
    run_mode.write_pinned_finalists(Path(current.staging), mode.pinned_codes)
    reason = (
        run_mode.GATE2_SKIP_REASON
        if mode.mode == run_mode.SENTINEL_PINNED
        else "sentinel_empty_no_l3"
    )
    result = gate2(Path(current.staging), skip_reason=reason)
    record_gate_stage_result(Path(current.staging), result)
    atomic_write_json(Path(current.staging) / "session_outputs/gate2.json", result)
    _write_scan_bundle(current, "l3_final", "l3.final.bundle.json")
    return result


def _scan_codes(scan_dir: Path) -> list[str]:
    import pandas as pd

    frame = pd.read_csv(scan_dir / "finalists.csv", dtype={"code": str})
    if "code" not in frame.columns:
        raise RuntimeError("finalists.csv missing code")
    codes = [str(value).split(".")[0].zfill(6) for value in frame["code"].tolist()]
    if any(not code.isdigit() or len(code) != 6 for code in codes):
        raise RuntimeError("finalists.csv contains invalid code")
    if len(codes) != len(set(codes)):
        raise RuntimeError("finalists.csv contains duplicate code")
    return codes


def scan_l4_prepare(handle=None) -> dict:
    """Run the original L4 producers, freeze prompts, then create the sole ticket owner."""
    current = handle or _active_handle()
    _use_frozen_scan_runtime_inputs(current)
    scan_dir = Path(current.staging)
    from autoresearch.scan import calendar
    from autoresearch.scan.l4 import producers, prompts
    from autoresearch.scan.l4.dispatch import dispatch_plan
    from autoresearch.session_agent import legacy_scan

    prompts.write_shared_instructions(scan_dir)
    producer_status = {}
    for name, call in (
        ("pledge", lambda: producers.fetch_pledge(scan_dir)),
        ("seats", lambda: producers.fetch_seats(scan_dir)),
        ("calendar", lambda: calendar.main([current.analysis_date])),
        ("consensus", lambda: producers.fetch_consensus(scan_dir)),
        ("fund_hold", lambda: producers.fetch_fund_hold(scan_dir)),
    ):
        try:
            call()
            producer_status[name] = "SUCCEEDED"
        except Exception as exc:  # optional legacy producers degrade independently
            producer_status[name] = f"DEGRADED:{type(exc).__name__}"
    prompt_result = prompts.write_dispatch_pack(scan_dir)
    dispatch = dispatch_plan(current.analysis_date, root=scan_dir.parent)
    codes = list(dispatch.get("dispatch") or [])
    expected = _scan_codes(scan_dir)
    if codes != expected or prompt_result.get("n_prompts") != len(expected):
        raise RuntimeError("L4 dispatch/prompts differ from frozen finalists")
    meta = dispatch.get("meta") or {}
    config = getattr(current.contract, "user_config", {}) or {}
    initialized = legacy_scan.initialize_tickets(
        current, codes, meta=meta,
        caps=((config.get("budgets") or {}).get("concurrency") or None))
    value = {
        "schema_version": 1,
        "codes": codes,
        "meta": meta,
        "pinned_codes": [code for code in codes if bool((meta.get(code) or {}).get("pinned"))],
        "intel_enabled": bool((config.get("l4_intel") or {}).get("enabled")),
        "intel_max_queries": (config.get("l4_intel") or {}).get("max_queries"),
        "config_hash": getattr(current.contract, "config_hash", None),
        "producer_status": producer_status,
        "taskbook": initialized["path"],
        "effective_cap": initialized.get("effective_cap"),
    }
    atomic_write_json(scan_dir / "session_outputs/l4.plan.json", value)
    bundle = _write_scan_bundle(current, "l4_source", "l4.source.bundle.json")
    _record_scan_source(
        provider="scan.l4.prepare",
        endpoint="scan.l4.prepare.snapshot.v1",
        current=current,
        outcome={"schema_version": 1, "bundle": bundle, "plan": value},
        consumers=[
            "scan.l4.plan",
            "scan.l4.taskbook",
            "scan.l4.source.bundle",
            *[f"scan.l4.{code}.a1.prompt" for code in codes],
        ],
    )
    return value


def scan_l4_skip(handle=None) -> dict:
    current = handle or _active_handle()
    mode = json.loads(_text(current, "scan.run_mode"))
    if mode.get("mode") != "SENTINEL_EMPTY":
        raise RuntimeError("L4 skip only applies to SENTINEL_EMPTY")
    plan = {
        "schema_version": 1,
        "codes": [],
        "meta": {},
        "pinned_codes": [],
        "intel_enabled": False,
        "reason": "sentinel_empty_no_l4",
    }
    review = {"schema_version": 1, "reviews": [], "reason": "sentinel_empty_no_l4"}
    output = Path(current.staging) / "session_outputs"
    atomic_write_json(output / "l4.plan.json", plan)
    atomic_write_json(output / "review.plan.json", review)
    _write_scan_bundle(current, "l4_source", "l4.source.bundle.json")
    return plan


def _require_code(code: str | None) -> str:
    value = str(code or "").split(".")[0].zfill(6)
    if not value.isdigit() or len(value) != 6:
        raise ValueError("six-digit scan subject required")
    return value


def _l4_attempt(scan_dir: Path, code: str) -> int:
    path = scan_dir / "_l4_tasks.json"
    if not path.is_file():
        return 1
    payload = json.loads(path.read_text(encoding="utf-8"))
    return int(payload["tasks"][code].get("attempt") or 0)


def _retry_dir(scan_dir: Path, code: str, attempt: int) -> Path:
    return scan_dir / "session_attempts" / code / f"a{attempt}"


def _write_if_changed(path: Path, payload: bytes) -> bool:
    """Byte-compare before rewriting: identical content keeps the file (and its inode)."""
    if path.is_file() and not path.is_symlink() and path.read_bytes() == payload:
        return False
    atomic_write_bytes(path, payload)
    return True


def _copy_attempt_status(scan_dir: Path, code: str, attempt: int) -> None:
    """Every attempt (a1 included) registers its own intel_status copy (N2): a retry
    rewrites the canonical `_l4_intel_status_<code>.json` the report reads."""
    target = _retry_dir(scan_dir, code, attempt) / "intel_status.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    _write_if_changed(target, (scan_dir / f"_l4_intel_status_{code}.json").read_bytes())


def scan_l4_slim(handle=None, *, code: str | None = None) -> dict:
    current = handle or _active_handle()
    from autoresearch.session_agent import legacy_scan

    code6 = _require_code(code)
    result = legacy_scan.prepare_slim(current, code6)
    if not result.get("ok"):
        raise RuntimeError(str(result.get("reason") or "L4 slim failed"))
    scan_dir = Path(current.staging)
    attempt = _l4_attempt(scan_dir, code6)
    source = Path(legacy_scan._payload(current)["tasks"][code6]["artifacts"]["slim"]["path"])
    deep_source = source.with_name(source.stem + "_deep.md")
    target = artifacts.declared_path(current, f"scan.l4.{code6}.a{attempt}.slim")
    if target != source:
        target.parent.mkdir(parents=True, exist_ok=True)
        _write_if_changed(target, source.read_bytes())
        source = target
    snapshot = {
        "schema_version": 1,
        "code": code6,
        "attempt": attempt,
        "result": result,
        "slim": _encode_payload(source.read_bytes()),
    }
    consumers = [f"scan.l4.{code6}.a{attempt}.slim"]
    deep_id = f"scan.l4.{code6}.a{attempt}.deep"
    try:
        deep_target = artifacts.declared_path(current, deep_id)
    except KeyError:
        deep_target = None  # Historical frozen tasks declared only the slim file.
    if deep_target is not None:
        if deep_source.is_symlink():
            raise ValueError("deep evidence must not be a symlink")
        deep_payload = (deep_source.read_bytes() if deep_source.is_file()
                        else b"DEEP_EVIDENCE_UNAVAILABLE: deep source missing; unverified\n")
        _write_if_changed(deep_target, deep_payload)
        snapshot.update({"schema_version": 2, "deep": _encode_payload(deep_payload)})
        consumers.append(deep_id)
    _record_scan_source(
        provider="scan.l4.slim",
        endpoint=f"scan.l4.slim.snapshot.v{snapshot['schema_version']}",
        current=current,
        outcome=snapshot,
        consumers=consumers,
    )
    return result


def render_scan_l4_slim_snapshot(
    snapshot: dict, *, output_path: Path | str, deep_output_path: Path | str | None = None,
) -> dict:
    version = snapshot.get("schema_version")
    fields = {"schema_version", "code", "attempt", "result", "slim"}
    if version == 2:
        fields.add("deep")
    if version not in {1, 2} or set(snapshot) != fields:
        raise ValueError("invalid scan L4 slim snapshot contract")
    if version == 2 and deep_output_path is None:
        raise ValueError("v2 slim snapshot requires declared deep output")
    atomic_write_bytes(Path(output_path), _decode_payload(snapshot["slim"]))
    if version == 2:
        atomic_write_bytes(Path(deep_output_path), _decode_payload(snapshot["deep"]))
    return dict(snapshot["result"])


def _normalize_intel(scan_dir: Path, code: str) -> None:
    from autoresearch.scan.l4.intel_guard import intel_path
    from autoresearch.scan.l4.intel_status import normalize_stale_scores, write_normalization

    source = intel_path(scan_dir, code)
    if not source.is_file():
        return
    body = source.read_text(encoding="utf-8")
    fixed, normalization = normalize_stale_scores(body, scan_dir.name, code=code)
    if fixed != body:
        source.write_text(fixed, encoding="utf-8")
    write_normalization(scan_dir, normalization)


def scan_l4_intel_status(handle=None, *, code: str | None = None, claim_sources=None) -> dict:
    current = handle or _active_handle()
    code6 = _require_code(code)
    from autoresearch.scan.l4.intel_guard import configured_soft_cap, guard_intel
    from autoresearch.scan.l4.intel_status import from_guard, write_status

    scan_dir = Path(current.staging)
    attempt = _l4_attempt(scan_dir, code6)
    # N2: every attempt's bound intel (a1 included) is the agent's bytes in its attempt
    # dir and is never rewritten; the canonical file is this attempt's working copy, which
    # the guard trims/normalizes in place exactly as the legacy flow does.
    bound_intel = _retry_dir(scan_dir, code6, attempt) / "intel.md"
    if bound_intel.is_file():
        _write_if_changed(scan_dir / f"_l4_intel_{code6}.md", bound_intel.read_bytes())
    if claim_sources is None:
        from autoresearch.session_agent.source_fields import intel_source_context
        claim_sources = intel_source_context(scan_dir)
    result = guard_intel(scan_dir, code6, soft_cap=configured_soft_cap(), source_context=claim_sources)
    if result.get("action") in {"KEPT", "TRIMMED"}:
        _normalize_intel(scan_dir, code6)
    status = from_guard(result, code=code6, scan_dir=scan_dir, enabled=True, attempts=1)
    write_status(scan_dir, status)
    _copy_attempt_status(scan_dir, code6, attempt)
    intel_path = scan_dir / f"_l4_intel_{code6}.md"
    # A11(2026-10-03):守卫后的正文按 attempt 冻结一份给卡读(`intel_doc`)。canonical 工作副本会被
    # 后续 attempt 改写,卡的输入哈希不能跟着变;被拒(REJECTED)时工作副本已移走,写空文件说明「没有可读的情报」。
    _write_if_changed(_retry_dir(scan_dir, code6, attempt) / "intel_doc.md",
                      intel_path.read_bytes() if intel_path.is_file() else b"")
    bundle_path = (
        _retry_dir(scan_dir, code6, attempt) / "intel_bundle.json"
        if attempt > 1
        else scan_dir / "session_outputs/intel_bundles" / f"{code6}.a1.json"
    )
    atomic_write_json(
        bundle_path,
        {
            "schema_version": 1,
            "code": code6,
            "attempt": attempt,
            "enabled": True,
            "intel": _encode_payload(intel_path.read_bytes()) if intel_path.is_file() else None,
        },
    )
    return status.to_dict()


def scan_l4_intel_disabled(handle=None, *, code: str | None = None) -> dict:
    current = handle or _active_handle()
    code6 = _require_code(code)
    from autoresearch.scan.l4.intel_status import from_guard, write_status

    scan_dir = Path(current.staging)
    status = from_guard(None, code=code6, scan_dir=scan_dir, enabled=False, attempts=0)
    write_status(scan_dir, status)
    attempt = _l4_attempt(scan_dir, code6)
    _copy_attempt_status(scan_dir, code6, attempt)
    bundle_path = (
        _retry_dir(scan_dir, code6, attempt) / "intel_bundle.json"
        if attempt > 1
        else scan_dir / "session_outputs/intel_bundles" / f"{code6}.a1.json"
    )
    atomic_write_json(
        bundle_path,
        {
            "schema_version": 1,
            "code": code6,
            "attempt": attempt,
            "enabled": False,
            "intel": None,
        },
    )
    return status.to_dict()


def _card_rating(path: Path) -> str:
    rating = parse_rating(path.read_text(encoding="utf-8"), strict=True)
    if rating is None:
        raise RuntimeError(f"card lacks strict rating: {path.name}")
    return rating


def _review_path(scan_dir: Path, kind: str, code: str | None = None) -> Path:
    return (scan_dir / "session_outputs/reviews" / f"{code}.{kind}.json" if code
            else scan_dir / "session_outputs" / f"review.{kind}.json")


def _has_scoped_review(handle, code: str) -> bool:
    try:
        return artifacts.binding_sha256(handle, f"scan.review.decision.{code}") is not None
    except (KeyError, OSError, ValueError, RuntimeError):
        return False


def scan_review_plan(handle=None, *, code: str | None = None) -> dict:
    current = handle or _active_handle()
    import pandas as pd

    scan_dir = Path(current.staging)
    finalists = pd.read_csv(scan_dir / "finalists.csv", dtype={"code": str})
    scope = _require_code(code) if code is not None else None
    if scope is not None:
        finalists = finalists[finalists["code"] == scope]
        if len(finalists) != 1:
            raise ValueError("review plan requires one frozen finalist for its scope")
    rows = []
    for _, item in finalists.iterrows():
        code = _require_code(str(item["code"]))
        card_path = scan_dir / "details" / f"{code}.md"
        text = card_path.read_text(encoding="utf-8")
        rating = parse_rating(text, strict=True)
        proposal = _PROPOSAL_RE.search(text)
        if rating is None or proposal is None:
            raise RuntimeError(f"L4 card contract incomplete: {code}")
        pinned = str(item.get("lane") or "").strip() == "pinned"
        from autoresearch.scan.decision_finalize import review_trigger  # l4.review:与 JS 同一份规则
        trigger = review_trigger(rating, proposal.group(1), pinned=pinned)
        rows.append(
            {
                "code": code,
                "attempt": _l4_attempt(scan_dir, code),
                "rating": rating,
                "pinned": pinned,
                "trigger": trigger,
            }
        )
    value = {"schema_version": 1, "reviews": rows}
    atomic_write_json(_review_path(scan_dir, "plan", scope), value)
    return value


def scan_review_none(handle=None, *, code: str | None = None) -> dict:
    current = handle or _active_handle()
    code6 = _require_code(code)
    value = {"schema_version": 1, "code": code6, "trigger": None, "reason": "not_applicable"}
    scan_dir = Path(current.staging)
    attempt = _l4_attempt(scan_dir, code6)
    target = (
        _retry_dir(scan_dir, code6, attempt) / "review_none.json"
        if attempt > 1
        else scan_dir / "session_outputs/reviews" / f"{code6}.none.json"
    )
    atomic_write_json(target, value)
    return value


def scan_review_decide(handle=None, *, code: str | None = None) -> dict:
    current = handle or _active_handle()
    scan_dir = Path(current.staging)
    scope = _require_code(code) if code is not None else None
    plan = json.loads(_review_path(scan_dir, "plan", scope).read_text(encoding="utf-8"))
    if scope and [row["code"] for row in plan.get("reviews", [])] != [scope]:
        raise ValueError("review decision scope differs from frozen plan")
    decisions = []
    for row in plan.get("reviews") or []:
        code = _require_code(row.get("code"))
        trigger = row.get("trigger")
        review2 = None
        same_tier = None
        if trigger is not None:
            attempt = int(row.get("attempt") or 1)
            review2_path = (
                _retry_dir(scan_dir, code, attempt) / "review2.md"
                if attempt > 1
                else scan_dir / "ensemble" / f"{code}.run2.md"
            )
            review2 = _card_rating(review2_path)
            same_tier = review2 == row["rating"]
        decisions.append(
            {
                **row,
                "review2_rating": review2,
                "same_tier": same_tier,
                "review3_required": same_tier is False,
            }
        )
    value = {"schema_version": 1, "decisions": decisions}
    atomic_write_json(_review_path(scan_dir, "decision", scope), value)
    return value


def scan_review_skip(handle=None) -> dict:
    current = handle or _active_handle()
    value = {"schema_version": 1, "decisions": [], "reason": "sentinel_empty_no_l4"}
    atomic_write_json(Path(current.staging) / "session_outputs/review.decision.json", value)
    return value


def scan_review3_skip(handle=None) -> dict:
    current = handle or _active_handle()
    value = {"schema_version": 1, "status": "SUCCEEDED", "reason": "sentinel_empty_no_l4"}
    atomic_write_json(Path(current.staging) / "session_outputs/l4.complete.json", value)
    _write_scan_bundle(current, "l4_final", "l4.final.bundle.json")
    return value


def scan_l4_finalize(handle=None, *, code: str | None = None) -> dict:
    current = handle or _active_handle()
    code6 = _require_code(code)
    scan_dir = Path(current.staging)
    from autoresearch.scan.stock_stage import record_l4_result
    from autoresearch.session_agent import legacy_scan
    from autoresearch.session_agent.workflows.scan import ensemble_record

    decision = json.loads(
        _review_path(scan_dir, "decision", code6 if _has_scoped_review(current, code6) else None).read_text(encoding="utf-8")
    )
    matches = [row for row in decision.get("decisions") or [] if row.get("code") == code6]
    if len(matches) != 1:
        raise RuntimeError(f"review decision missing for {code6}")
    row = matches[0]
    attempt = int(row.get("attempt") or 1)
    intel_bundle_path = (
        _retry_dir(scan_dir, code6, attempt) / "intel_bundle.json"
        if attempt > 1
        else scan_dir / "session_outputs/intel_bundles" / f"{code6}.a1.json"
    )
    intel_bundle = json.loads(intel_bundle_path.read_text(encoding="utf-8"))
    if intel_bundle.get("intel") is not None:
        _write_if_changed(
            scan_dir / f"_l4_intel_{code6}.md",
            _decode_payload(intel_bundle["intel"]),
        )
    ensemble = None
    if row.get("trigger") is not None:
        review3_rating = (
            _card_rating(
                _retry_dir(scan_dir, code6, attempt) / "review3.md"
                if attempt > 1
                else scan_dir / "ensemble" / f"{code6}.run3.md"
            )
            if row.get("review3_required")
            else None
        )
        ensemble = ensemble_record(
            code6,
            row["rating"],
            row["review2_rating"],
            review3_rating,
            trigger=row["trigger"],
            review3_dispatched=bool(row.get("review3_required")),
        )
        atomic_write_json(scan_dir / f"_ensemble_{code6}.json", ensemble)
    stage = record_l4_result(scan_dir, code6)
    ticket = {
        "schema_version": 1,
        "code": code6,
        "attempt": attempt,
        "status": "SUCCEEDED",
        "source_rating": stage.metrics.get("source_rating"),
        "provisional_rating": stage.metrics.get("provisional_rating"),
        "ensemble": ensemble,
    }
    portable = [
        scan_dir / f"_l4_prompt_{code6}.md",
        scan_dir / f"_l4_intel_{code6}.md",
        scan_dir / f"_l4_intel_status_{code6}.json",
        scan_dir / f"_ensemble_{code6}.json",
        scan_dir / "details" / f"{code6}.md",
        scan_dir / "ensemble" / f"{code6}.run2.md",
        scan_dir / "ensemble" / f"{code6}.run3.md",
    ]
    slim_path = Path(
        legacy_scan._payload(current)["tasks"][code6]["artifacts"]["slim"]["path"]
    )
    try:
        slim_path.relative_to(scan_dir)
    except ValueError:
        pass
    else:
        portable.append(slim_path)
    retry_dir = _retry_dir(scan_dir, code6, attempt)
    if attempt > 1 and retry_dir.is_dir():
        portable.extend(path for path in retry_dir.rglob("*") if path.is_file())
    ticket["files"] = {
        path.relative_to(scan_dir).as_posix(): _encode_payload(path.read_bytes())
        for path in sorted(set(portable))
        if path.is_file() and not path.is_symlink()
    }
    ticket_path = scan_dir / "session_outputs/tickets" / f"{code6}.a{attempt}.json"
    atomic_write_json(ticket_path, ticket)
    legacy_scan.complete_ticket(current, code6, attempt)
    return ticket


def scan_l4_complete(handle=None) -> dict:
    current = handle or _active_handle()
    path = Path(current.staging) / "_l4_tasks.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    states = {
        str(code): str(task.get("status") or "PENDING")
        for code, task in (payload.get("tasks") or {}).items()
    }
    bad = {code: state for code, state in states.items() if state != "SUCCEEDED"}
    if bad:
        raise RuntimeError(f"L4 taskbook not all SUCCEEDED: {bad}")
    if states and _has_scoped_review(current, next(iter(states))):
        import csv
        with (Path(current.staging) / "finalists.csv").open() as stream:
            codes = [row["code"] for row in csv.DictReader(stream)]
        if set(codes) != set(states):
            raise RuntimeError("review join population differs from frozen finalists")
        for kind, key in (("plan", "reviews"), ("decision", "decisions")):
            rows = []
            for code in codes:
                with artifacts.open_artifact(current, f"scan.review.{kind}.{code}") as stream:
                    value = json.load(stream)
                if [row["code"] for row in value.get(key, [])] != [code]:
                    raise RuntimeError("review join scope identity mismatch")
                rows.extend(value[key])
            atomic_write_json(_review_path(Path(current.staging), kind), {"schema_version": 1, key: rows})
    value = {"schema_version": 1, "status": "SUCCEEDED", "states": states}
    atomic_write_json(Path(current.staging) / "session_outputs/l4.complete.json", value)
    _write_scan_bundle(current, "l4_final", "l4.final.bundle.json")
    return value


def scan_assemble(handle=None) -> dict:
    current = handle or _active_handle()
    _use_frozen_scan_runtime_inputs(current)
    from autoresearch.scan import publisher

    scan_dir = Path(current.staging)
    build_root = scan_dir / "session_outputs/report_build"
    summary = Path(
        publisher.run(
            current.analysis_date,
            scan_dir=scan_dir,
            out_root=build_root,
        )
    )
    report_dir = summary.parent
    from autoresearch.session_agent.research_provenance import collect_and_freeze

    collect_and_freeze(current)
    # The research exporter only reads immutable published bytes.
    mirror = report_dir / "trace/staging"
    for name in ("_research_provenance.json", "research_inputs", "research_reads", "_l4_force_full"):
        source = scan_dir / name
        target = mirror / name
        if source.is_dir():
            shutil.copytree(source, target, dirs_exist_ok=True)
        elif source.is_file():
            atomic_write_bytes(target, source.read_bytes())
    required = ["brief.md", "summary.md", "appendix.md", "manifest.json"]
    missing = [name for name in required if not (report_dir / name).is_file()]
    if missing:
        raise RuntimeError(f"scan assembler omitted required report files: {missing}")
    value = {
        "schema_version": 1,
        "run_id": current.run_id,
        "engine": current.engine,
        "analysis_date": current.analysis_date,
        "folder": report_dir.name,
        "candidate_relative": report_dir.relative_to(Path(current.workspace)).as_posix(),
    }
    atomic_write_json(scan_dir / "session_outputs/report.plan.json", value)
    _write_scan_bundle(current, "report", "report.build.bundle.json")
    return value


def scan_gate4(handle=None) -> dict:
    current = handle or _active_handle()
    _use_frozen_scan_runtime_inputs(current)
    from autoresearch.scan.gates import gate4, record_gate_stage_result

    scan_dir = Path(current.staging)
    value = gate4(scan_dir)
    record_gate_stage_result(scan_dir, value)
    atomic_write_json(scan_dir / "session_outputs/gate4.json", value)
    if not value.get("ok"):
        raise RuntimeError(str(value.get("reason") or "GATE4 failed"))
    return value


def _report_candidate(current) -> Path:
    plan = json.loads(
        (Path(current.staging) / "session_outputs/report.plan.json").read_text(encoding="utf-8")
    )
    workspace = Path(current.workspace).resolve(strict=True)
    candidate = (workspace / plan["candidate_relative"]).resolve(strict=True)
    try:
        candidate.relative_to(workspace)
    except ValueError as exc:
        raise RuntimeError("report candidate escaped its run") from exc
    return candidate


def scan_usage(handle=None) -> dict:
    current = handle or _active_handle()
    _use_frozen_scan_runtime_inputs(current)
    from autoresearch.trace import usage_harvest, usage_reconcile

    scan_dir = Path(current.staging)
    candidate = _report_candidate(current)
    source = f"run:{current.run_id}"
    warnings = []
    try:
        rows = usage_harvest.collect_run(current.run_id, engine=current.engine)
    except Exception as exc:  # missing host metering remains explicit, but does not erase research
        rows = []
        warnings.append(f"usage_harvest:{type(exc).__name__}:{exc}")
    ledger = usage_harvest.build_ledger(rows, source=source)
    if warnings:
        ledger["measurement_status"] = "UNMEASURED"
        ledger["warnings"] = warnings
    atomic_write_json(scan_dir / "_token_usage.json", ledger)
    atomic_write_bytes(
        candidate / "token_usage.md",
        (usage_harvest.render(rows, sub_dir=source) + "\n").encode("utf-8"),
    )
    try:
        reconcile = usage_reconcile.reconcile(current.analysis_date)
    except Exception as exc:
        reconcile = {
            "schema_version": 1,
            "ok": False,
            "status": "UNMEASURED",
            "warnings": [f"usage_reconcile:{type(exc).__name__}:{exc}"],
        }
    atomic_write_json(scan_dir / "_usage_reconcile.json", reconcile)
    bundle = _write_scan_bundle(current, "used_report", "report.used.bundle.json")
    snapshot = {
        "schema_version": 1,
        "usage": ledger,
        "reconcile": reconcile,
        "report_bundle": bundle,
    }
    _record_scan_source(
        provider="scan.usage",
        endpoint="scan.usage.snapshot.v1",
        current=current,
        outcome=snapshot,
        consumers=[
            "scan.token.usage",
            "scan.usage.reconcile",
            "scan.report.used.bundle",
        ],
    )
    return {"schema_version": 1, "usage": ledger, "reconcile": reconcile}


def render_scan_usage_snapshot(snapshot: dict, *, staging_root: Path | str) -> dict:
    if set(snapshot) != {"schema_version", "usage", "reconcile", "report_bundle"}:
        raise ValueError("invalid scan usage snapshot contract")
    if snapshot["schema_version"] != 1:
        raise ValueError("invalid scan usage snapshot")
    staging = Path(staging_root)
    atomic_write_json(staging / "_token_usage.json", snapshot["usage"])
    atomic_write_json(staging / "_usage_reconcile.json", snapshot["reconcile"])
    restore_scan_staging_bundle(
        snapshot["report_bundle"], staging, expected_phase="used_report"
    )
    atomic_write_json(
        staging / "session_outputs/report.used.bundle.json",
        snapshot["report_bundle"],
    )
    return {"schema_version": 1, "usage": snapshot["usage"], "reconcile": snapshot["reconcile"]}


def _scan_pool_snapshot() -> tuple[bytes | None, dict]:
    from autoresearch.common.published_state import read_committed_bytes
    from autoresearch.dossier import pool as dossier_pool

    committed = read_committed_bytes(
        "dossier.coverage_pool",
        state_root=ws.context_root() / "_published_state",
        reports_root=ws.run_reports_root("dossier-init"),
    )
    if committed is not None:
        return committed, json.loads(committed.decode("utf-8"))
    pool_path = Path(dossier_pool.POOL_PATH)
    before = pool_path.read_bytes() if pool_path.is_file() else None
    return before, dossier_pool.load_pool(pool_path)


def render_scan_observation_snapshot(current, snapshot: dict) -> list[dict]:
    """Recompute report/pool publication facts from one frozen observation source."""
    required = {
        "schema_version",
        "observation",
        "pool_before_text",
        "current_pool",
        "report_bundle",
        "progress_base",
    }
    if set(snapshot) != required or snapshot["schema_version"] != 1:
        raise ValueError("invalid scan observation snapshot contract")
    if not isinstance(snapshot["current_pool"], dict) or not isinstance(
        snapshot["progress_base"], dict
    ):
        raise ValueError("invalid scan observation snapshot")
    from autoresearch.scan.post_run import build_finalist_pool_candidate
    from autoresearch.session_agent.workflows.scan import directory_manifest

    scan_dir = Path(current.staging)
    restore_scan_staging_bundle(
        snapshot["report_bundle"], scan_dir, expected_phase="observed_report"
    )
    candidate = _report_candidate(current)
    fixed = scan_dir / "session_outputs/report_files"
    for name in ("brief.md", "summary.md", "appendix.md", "manifest.json"):
        source = candidate / name
        if not source.is_file():
            raise RuntimeError(f"observed report is missing: {name}")
        atomic_write_bytes(fixed / name, source.read_bytes())
    pool_before_text = snapshot["pool_before_text"]
    pool_before = pool_before_text.encode("utf-8") if pool_before_text is not None else None
    current_pool = snapshot["current_pool"]
    prepared_pool = build_finalist_pool_candidate(
        scan_dir,
        current.analysis_date,
        current_pool,
    )
    pool_candidate, _ = prepared_pool or (current_pool, [])
    pool_candidate_path = scan_dir / "session_outputs/scan.pool.candidate.json"
    atomic_write_json(pool_candidate_path, pool_candidate)
    pool_mutation = prepared_pool is not None and pool_candidate != current_pool
    plan = json.loads((scan_dir / "session_outputs/report.plan.json").read_text(encoding="utf-8"))
    bundle = {
        "schema_version": 1,
        "run_id": current.run_id,
        "engine": current.engine,
        "analysis_date": current.analysis_date,
        "folder": plan["folder"],
        "candidate_relative": plan["candidate_relative"],
        "files": directory_manifest(candidate),
        "pool_mutation": pool_mutation,
        "pool_before_sha256": sha256_bytes(pool_before) if pool_before is not None else None,
        "pool_after_sha256": sha256_bytes(pool_candidate_path.read_bytes()),
    }
    atomic_write_json(scan_dir / "session_outputs/scan.publication.json", bundle)
    progress = {
        **snapshot["progress_base"],
        "checkpoint": "CP7",
        "gate4": json.loads((scan_dir / "session_outputs/gate4.json").read_text(encoding="utf-8")),
        "observation": snapshot["observation"],
        "report_candidate": plan["candidate_relative"],
    }
    atomic_write_json(scan_dir / "session_outputs/progress.final.json", progress)
    if not pool_mutation:
        return []
    return [
        {
            "target_key": "dossier.coverage_pool",
            "expected_before_hash": bundle["pool_before_sha256"],
            "after_artifact_id": "scan.pool.candidate",
            "after_hash": bundle["pool_after_sha256"],
            "apply_policy": "CAS_REPLACE",
        }
    ]


def scan_observe(handle=None) -> dict:
    current = handle or _active_handle()
    _use_frozen_scan_runtime_inputs(current)
    from autoresearch.scan.post_run import publish_run_observation
    from autoresearch.session_agent.progress import scan_progress

    scan_dir = Path(current.staging)
    candidate = _report_candidate(current)
    observation = publish_run_observation(
        scan_dir,
        report_dir=candidate,
        usage_path=scan_dir / "_token_usage.json",
        decision_write="verify",
    )
    pool_before, current_pool = _scan_pool_snapshot()
    snapshot = {
        "schema_version": 1,
        "observation": observation,
        "pool_before_text": pool_before.decode("utf-8") if pool_before is not None else None,
        "current_pool": current_pool,
        "report_bundle": collect_scan_staging_bundle(
            scan_dir, phase="observed_report"
        ),
        "progress_base": scan_progress(current),
    }
    _record_scan_source(
        provider="scan.observe",
        endpoint="scan.observe.snapshot.v1",
        current=current,
        outcome=snapshot,
        consumers=[
            "scan.publication.bundle",
            "scan.pool.candidate",
            "scan.report.brief",
            "scan.report.summary",
            "scan.report.appendix",
            "scan.report.manifest",
            "scan.progress.final",
        ],
    )
    render_scan_observation_snapshot(current, snapshot)
    return json.loads(
        (scan_dir / "session_outputs/scan.publication.json").read_text(encoding="utf-8")
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="autoresearch.session_agent.domain_ops")
    parser.add_argument(
        "command",
        choices=(
            "stock-validate",
            "research-calculate",
            "research-card-facts",
            "stock-publish",
            "stock-evidence-bundle",
            "stock-full-validate",
            "stock-full-assemble",
            "macro-harvest",
            "macro-intel-prepare",
            "macro-lite-frame",
            "macro-lite-validate",
            "macro-publish",
            "macro-full-validate",
            "macro-full-assemble",
            "sector-prepare",
            "sector-terrain-render",
            "sector-validate",
            "sector-publish",
            "dossier-prefetch",
            "dossier-skeleton",
            "dossier-validate",
            "dossier-publish",
            "scan-frame",
            "scan-prelude",
            "scan-gate1",
            "scan-sector-prepare",
            "scan-sector-skip",
            "scan-l3-prepare",
            "scan-l3-lint",
            "scan-l3-repair-skip",
            "scan-l3-repair-apply",
            "scan-l3-merge",
            "scan-gate2-skip",
            "scan-l4-prepare",
            "scan-l4-skip",
            "scan-l4-ticket",
            "scan-l4-slim",
            "scan-l4-intel-status",
            "scan-l4-intel-disabled",
            "scan-review-plan",
            "scan-review-none",
            "scan-review-decide",
            "scan-review-skip",
            "scan-review3-skip",
            "scan-l4-finalize",
            "scan-l4-complete",
            "scan-assemble",
            "scan-gate4",
            "scan-usage",
            "scan-observe",
        ),
    )
    parser.add_argument("--subject")
    parser.add_argument("--calculator-id")
    parser.add_argument("--input-artifact-ids-json")
    parser.add_argument("--parameters-json")
    args = parser.parse_args(argv)
    if args.command == "research-card-facts":
        from autoresearch.session_agent.card_facts import execute_active
        value = execute_active()
    elif args.command == "research-calculate":
        input_artifact_ids = json.loads(args.input_artifact_ids_json)
        parameters = json.loads(args.parameters_json)
        if not isinstance(input_artifact_ids, list) or not isinstance(parameters, dict):
            raise ValueError("invalid calculation command parameters")
        value = research_calculate(
            args.calculator_id,
            input_artifact_ids,
            parameters,
        )
    elif args.command == "stock-validate":
        value = stock_validate()
    elif args.command == "stock-publish":
        value = stock_prepare_publication()
    elif args.command == "stock-evidence-bundle":
        value = stock_evidence_bundle()
    elif args.command == "stock-full-validate":
        value = stock_full_validate()
    elif args.command == "stock-full-assemble":
        value = stock_full_assemble()
    elif args.command == "macro-intel-prepare":
        value = macro_intel_prepare()
    elif args.command == "macro-harvest":
        value = macro_harvest_run()
    elif args.command == "macro-lite-frame":
        value = macro_lite_prepare()
    elif args.command == "macro-lite-validate":
        value = macro_lite_validate()
    elif args.command == "macro-publish":
        value = macro_prepare_publication()
    elif args.command == "macro-full-validate":
        value = macro_full_validate()
    elif args.command == "macro-full-assemble":
        value = macro_full_assemble()
    elif args.command == "sector-prepare":
        value = sector_prepare()
    elif args.command == "sector-terrain-render":
        value = sector_terrain_render()
    elif args.command == "sector-validate":
        value = sector_validate()
    elif args.command == "sector-publish":
        value = sector_prepare_publication()
    elif args.command == "dossier-prefetch":
        value = dossier_prefetch_run()
    elif args.command == "dossier-skeleton":
        value = dossier_build_skeleton()
    elif args.command == "dossier-validate":
        value = dossier_validate()
    elif args.command == "dossier-publish":
        value = dossier_prepare_publication()
    elif args.command == "scan-frame":
        value = scan_frame()
    elif args.command == "scan-prelude":
        value = scan_prelude()
    elif args.command == "scan-gate1":
        value = scan_gate1()
    elif args.command == "scan-sector-prepare":
        value = scan_sector_prepare()
    elif args.command == "scan-sector-skip":
        value = scan_sector_skip()
    elif args.command == "scan-l3-prepare":
        value = scan_l3_prepare()
    elif args.command == "scan-l3-lint":
        value = scan_l3_lint()
    elif args.command == "scan-l3-repair-skip":
        value = scan_l3_repair_skip()
    elif args.command == "scan-l3-repair-apply":
        value = scan_l3_repair_apply()
    elif args.command == "scan-l3-merge":
        value = scan_l3_merge()
    elif args.command == "scan-gate2-skip":
        value = scan_gate2_skip()
    elif args.command == "scan-l4-prepare":
        value = scan_l4_prepare()
    elif args.command == "scan-l4-skip":
        value = scan_l4_skip()
    elif args.command == "scan-l4-ticket":
        raise RuntimeError("L4 ticket is owned by l4_tasks and cannot be executed here")
    elif args.command == "scan-l4-slim":
        value = scan_l4_slim(code=args.subject)
    elif args.command == "scan-l4-intel-status":
        value = scan_l4_intel_status(code=args.subject)
    elif args.command == "scan-l4-intel-disabled":
        value = scan_l4_intel_disabled(code=args.subject)
    elif args.command == "scan-review-plan":
        value = scan_review_plan(code=args.subject)
    elif args.command == "scan-review-none":
        value = scan_review_none(code=args.subject)
    elif args.command == "scan-review-decide":
        value = scan_review_decide(code=args.subject)
    elif args.command == "scan-review-skip":
        value = scan_review_skip()
    elif args.command == "scan-review3-skip":
        value = scan_review3_skip()
    elif args.command == "scan-l4-finalize":
        value = scan_l4_finalize(code=args.subject)
    elif args.command == "scan-l4-complete":
        value = scan_l4_complete()
    elif args.command == "scan-assemble":
        value = scan_assemble()
    elif args.command == "scan-gate4":
        value = scan_gate4()
    elif args.command == "scan-usage":
        value = scan_usage()
    else:
        value = scan_observe()
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "research_calculate",
    "macro_full_assemble",
    "macro_full_validate",
    "macro_harvest_run",
    "macro_lite_prepare",
    "macro_lite_validate",
    "macro_prepare_publication",
    "sector_full_validate",
    "sector_lite_validate",
    "sector_prepare",
    "sector_prepare_publication",
    "sector_validate",
    "dossier_build_skeleton",
    "dossier_prefetch_run",
    "dossier_prepare_publication",
    "dossier_validate",
    "stock_full_assemble",
    "stock_full_validate",
    "stock_prepare_publication",
    "stock_validate",
    "scan_frame",
    "scan_gate1",
    "scan_gate2_skip",
    "scan_l3_lint",
    "scan_l3_repair_apply",
    "scan_l3_repair_degraded",
    "scan_l3_repair_skip",
    "scan_assemble",
    "scan_gate4",
    "scan_l3_merge",
    "scan_l3_prepare",
    "scan_l4_complete",
    "scan_l4_finalize",
    "scan_l4_intel_disabled",
    "scan_l4_intel_status",
    "scan_l4_prepare",
    "scan_l4_skip",
    "scan_l4_slim",
    "scan_observe",
    "scan_prelude",
    "scan_review_decide",
    "scan_review_none",
    "scan_review_plan",
    "scan_review_skip",
    "scan_review3_skip",
    "scan_sector_prepare",
    "scan_sector_skip",
    "scan_usage",
]
