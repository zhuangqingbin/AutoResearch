"""Deterministic domain operations invoked through command capture."""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

from autoresearch.agents.utils.rating import parse_rating
from autoresearch.analyze import assemble as stock_assemble
from autoresearch.common import workspace as ws
from autoresearch.common.atomic import atomic_write_bytes, atomic_write_json, sha256_bytes
from autoresearch.contracts.agent_output import L4_CARD
from autoresearch.dossier import (
    builder as dossier_builder,
    prefetch as dossier_prefetch,
    schema as dossier_schema,
)
from autoresearch.macro import assemble as macro_assemble, harvest as macro_harvest
from autoresearch.macro.state import load_macro_state, write_macro_state
from autoresearch.session_agent import artifacts
from autoresearch.session_agent.validation import validate_registered_contract
from autoresearch.session_agent.workflows.stock import (
    full_product_artifacts,
    required_full_products,
)

_PROPOSAL_RE = re.compile(L4_CARD.field("proposal").pattern, re.IGNORECASE)


def _active_handle():
    from autoresearch.common import workspace as ws
    from autoresearch.trace.capsule import require_active_run

    run_id = ws.active_run_id()
    if run_id is None:
        raise RuntimeError("domain operation requires AUTORESEARCH_RUN_ID")
    return require_active_run(run_id)


def _request(handle) -> dict:
    return json.loads(
        (Path(handle.workspace) / "session/request.json").read_text(encoding="utf-8")
    )


def _text(handle, artifact_id: str) -> str:
    with artifacts.open_artifact(handle, artifact_id) as stream:
        return stream.read().decode("utf-8")


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
    return value


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
    validation = json.loads(
        _text(current, "stock.card.validation")
    )
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
    if parse_rating(decision, strict=True) is None or _PROPOSAL_RE.search(decision) is None:
        raise RuntimeError("full decision lacks strict Rating or proposal")
    value = {
        "schema_version": 1,
        "contract": "stock.full.products.v1",
        "required_products": sorted(required_full_products()),
        "hashes": hashes,
    }
    atomic_write_json(
        Path(current.staging) / "session_outputs/full.validation.json", value
    )
    return value


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
        Path(current.staging)
        / "analyze"
        / f"{ticker}_{request['analysis_date'].replace('-', '')}"
    )
    scratch = Path(current.staging) / "session_outputs/assemble_scratch"
    shutil.rmtree(scratch, ignore_errors=True)
    original_reports_root = stock_assemble.ws.reports_root
    original_argv = sys.argv
    try:
        stock_assemble.ws.reports_root = lambda: scratch
        sys.argv = ["assemble.py", str(draft_root)]
        if request.get("name"):
            sys.argv.extend(["--name", request["name"]])
        if stock_assemble.main() != 0:
            raise RuntimeError("existing stock assembler rejected full products")
    finally:
        stock_assemble.ws.reports_root = original_reports_root
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


def macro_harvest_run(handle=None) -> dict:
    current = handle or _active_handle()
    request = _request(current)
    root = Path(current.staging) / "macro" / request["analysis_date"]
    if macro_harvest.main([request["analysis_date"], "--output-dir", str(root)]) != 0:
        raise RuntimeError("macro harvest failed")
    return {"data": str(root / "data.md"), "global_tape": str(root / "global_tape.json")}


def _macro_market_payload(current, macro_state_path: Path | str | None = None) -> dict:
    request = _request(current)
    target = Path(current.staging) / "session_outputs/market_pack.json"
    try:
        with artifacts.open_artifact(current, "macro.market_pack") as stream:
            payload = json.loads(stream.read().decode("utf-8"))
    except (KeyError, ValueError, RuntimeError):
        from autoresearch.scan.frame import build_market_frame
        from autoresearch.scan.market import market_pack_from_frame

        frame, _ = build_market_frame(
            request["analysis_date"], cap_floor_yi=30.0, include_bj=True, source="tushare"
        )
        payload = market_pack_from_frame(frame, date=request["analysis_date"])
    regime = payload.get("regime") or {}
    default_state = ws.context_root() / "macro/macro_state.json"
    state, note = load_macro_state(
        request["analysis_date"],
        regime_today=regime.get("label"),
        path=macro_state_path or default_state,
    )
    payload = {**payload, "macro_state": state, "macro_state_note": note}
    atomic_write_json(target, payload)
    return payload


def macro_lite_prepare(
    handle=None, *, macro_state_path: Path | str | None = None
) -> dict:
    current = handle or _active_handle()
    from autoresearch.scan.strategist_pack import project

    payload = _macro_market_payload(current, macro_state_path)
    projection = project(payload)
    atomic_write_json(
        Path(current.staging) / "session_outputs/strategist_pack.json", projection
    )
    return {
        "pack": projection["pack"],
        "macro_state": projection["pack"].get("macro_state"),
        "macro_state_note": projection["pack"].get("macro_state_note"),
    }


_MARKET_VIEW_SECTION_RE = re.compile(r"(?m)^\s*([1-6])[.、]\s*\*\*")


def macro_lite_validate(handle=None) -> dict:
    current = handle or _active_handle()
    text = _text(current, "macro.market_view")
    sections = {match.group(1) for match in _MARKET_VIEW_SECTION_RE.finditer(text)}
    if sections != {"1", "2", "3", "4", "5", "6"}:
        raise RuntimeError("macro market view requires all six sections")
    descriptor = artifacts.bind_artifact_hash(current, "macro.market_view")
    value = {
        "schema_version": 1,
        "contract": "macro.brief.v1",
        "sections": sorted(sections),
        "report_sha256": descriptor["sha256"],
    }
    atomic_write_json(
        Path(current.staging) / "session_outputs/macro.lite.validation.json", value
    )
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
    atomic_write_json(
        Path(current.staging) / "session_outputs/macro.publication.json", value
    )
    return value


def macro_full_validate(handle=None) -> dict:
    current = handle or _active_handle()
    from autoresearch.session_agent.workflows.macro import (
        macro_product_artifacts,
        required_macro_products,
    )

    mapping = macro_product_artifacts()
    hashes = {}
    for relative in sorted(required_macro_products()):
        artifact_id = mapping[relative]
        try:
            descriptor = artifacts.bind_artifact_hash(current, artifact_id)
            text = _text(current, artifact_id).strip()
        except (KeyError, ValueError, RuntimeError) as exc:
            raise RuntimeError(f"required macro product missing: {relative}") from exc
        if not text:
            raise RuntimeError(f"required macro product empty: {relative}")
        hashes[relative] = descriptor["sha256"]
    for relative in (macro_assemble.DECISION_REL, macro_assemble.SECTOR_MAP_REL):
        allocation = macro_assemble.parse_allocation(_text(current, mapping[relative]))
        if not allocation or any(value is None for value in allocation.values()):
            raise RuntimeError(f"macro allocation is not parseable: {relative}")
    value = {
        "schema_version": 1,
        "contract": "macro.full.products.v1",
        "required_products": sorted(required_macro_products()),
        "hashes": hashes,
    }
    atomic_write_json(
        Path(current.staging) / "session_outputs/macro.full.validation.json", value
    )
    return value


def macro_full_assemble(handle=None) -> dict:
    current = handle or _active_handle()
    request = _request(current)
    validation = json.loads(_text(current, "macro.full.validation"))
    from autoresearch.session_agent.workflows.macro import required_macro_products

    if set(validation.get("required_products") or []) != required_macro_products():
        raise RuntimeError("macro validation does not match assembler requirements")
    root = Path(current.staging) / "macro" / request["analysis_date"]
    output = Path(current.staging) / "session_outputs"
    scratch = output / "macro_assembled"
    shutil.rmtree(scratch, ignore_errors=True)
    if macro_assemble.main(
        [
            str(root),
            "--output-dir",
            str(scratch),
            "--state-out-dir",
            str(output),
        ]
    ) != 0:
        raise RuntimeError("existing macro assembler rejected full products")
    reports = sorted(scratch.glob("*_summary.md"))
    if len(reports) != 1:
        raise RuntimeError("macro assembler did not create one report")
    report_bytes = reports[0].read_bytes()
    atomic_write_bytes(output / "macro.full.report.md", report_bytes)
    state = write_macro_state(root, report_path=reports[0], out_dir=output)
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


def sector_prepare(handle=None, *, scan_root: Path | str | None = None) -> dict:
    current = handle or _active_handle()
    request = _request(current)
    analysis_date = request["analysis_date"]
    industry = request["subject"]
    source_root = Path(scan_root) if scan_root is not None else ws.scan_root()
    source = source_root / analysis_date
    input_dir = Path(current.staging) / "sector_inputs" / analysis_date
    input_dir.mkdir(parents=True, exist_ok=True)
    source_kind = "existing_scan"
    l1_source = source / "L1_scored_full.csv"
    if l1_source.is_file():
        shutil.copyfile(l1_source, input_dir / "L1_scored_full.csv")
        for name in ("L2_gbdt_top200.csv", "sectors.csv", "calendar.csv", "meta.json"):
            candidate = source / name
            if candidate.is_file():
                shutil.copyfile(candidate, input_dir / name)
    else:
        source_kind = "generated_frame"
        from autoresearch.scan.frame import build_market_frame

        frame, _ = build_market_frame(
            analysis_date, cap_floor_yi=30.0, include_bj=True, source="tushare"
        )
        frame.to_csv(input_dir / "L1_scored_full.csv", index=False)
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

    pack = (
        sector_pack_module._sector_pack_staging(industry, input_dir)
        if request["requested_mode"] == "LITE"
        else sector_pack_module.sector_pack(industry, input_dir)
    )
    if int(pack.get("n_market") or 0) < 1:
        raise ValueError(f"industry is absent from verified market inputs: {industry}")
    output = Path(current.staging) / "session_outputs"
    manifest = {
        "schema_version": 1,
        "engine": current.engine,
        "analysis_date": analysis_date,
        "industry": industry,
        "source": source_kind,
        "input_hashes": input_hashes,
    }
    atomic_write_json(output / "sector.inputs.json", manifest)
    atomic_write_json(output / "sector.pack.json", pack)
    reuse_value = {"schema_version": 1, "reused": False, "source": None, "sha256": None, "body": None}
    if request["requested_mode"] == "LITE" and source_kind == "existing_scan":
        from autoresearch.sector.reuse import apply_reuse, find_reusable

        found = find_reusable(analysis_date, [industry], root=source_root)
        if industry in found:
            reuse_root = Path(current.staging) / "sector_reuse"
            apply_reuse(analysis_date, found, root=reuse_root)
            reused_path = reuse_root / analysis_date / "sector_briefs" / f"{sector_pack_module._safe(industry)}.md"
            body = reused_path.read_text(encoding="utf-8")
            reuse_value = {
                "schema_version": 1,
                "reused": True,
                "source": found[industry]["src"],
                "sha256": sha256_bytes(body.encode("utf-8")),
                "body": body,
            }
    atomic_write_json(output / "sector.reuse.json", reuse_value)
    return {**manifest, "pack": pack, "reuse": reuse_value}


_SECTOR_DIRECTIONS = re.compile(r"超配|低配|回避|买入|卖出|买卖|看多|看空")
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


def dossier_prefetch_run(handle=None) -> dict:
    current = handle or _active_handle()
    request = _request(current)
    output = Path(current.staging) / "session_outputs"
    data = dossier_prefetch.prefetch_one(
        request["subject"], request["analysis_date"], out_dir=output / "prefetch"
    )
    source = output / "prefetch" / f"{request['subject']}.json"
    atomic_write_bytes(output / "dossier.prefetch.json", source.read_bytes())
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


def _dossier_permissions(
    skeleton: str, *, target: Path, opening_hash: str | None
) -> dict:
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
        "frontmatter": {
            key: value for key, value in frontmatter.items() if key != "initiated"
        },
        "deterministic_sections": deterministic,
        "protected_prefixes": prefixes,
        "summary_fixed": {anchor: summary[anchor] for anchor in ("带位:", "判例:")},
    }


def dossier_build_skeleton(handle=None, *, target_path: Path | str | None = None) -> dict:
    current = handle or _active_handle()
    request = _request(current)
    output = Path(current.staging) / "session_outputs"
    target = (
        Path(target_path)
        if target_path is not None
        else dossier_schema.dossier_path(request["subject"])
    )
    opening_hash = sha256_bytes(target.read_bytes()) if target.is_file() else None
    skeleton_path = output / "dossier.skeleton.md"
    if target.is_file():
        existing = target.read_text(encoding="utf-8")
        if dossier_schema.parse_frontmatter(existing).get("initiated"):
            raise RuntimeError("dossier is already initialized")
        atomic_write_bytes(skeleton_path, target.read_bytes())
        issues = dossier_schema.lint_dossier(existing)
    else:
        built = dossier_builder.build_skeleton(
            request["subject"],
            request["analysis_date"],
            name=request.get("name") or "",
            scan_root=ws.scan_root(),
            output_path=skeleton_path,
            prefetch_path=output / "dossier.prefetch.json",
        )
        issues = built["issues"]
    if issues:
        raise RuntimeError(f"dossier skeleton is invalid: {issues}")
    skeleton = skeleton_path.read_text(encoding="utf-8")
    permissions = _dossier_permissions(
        skeleton, target=target, opening_hash=opening_hash
    )
    atomic_write_json(output / "dossier.permissions.json", permissions)
    return permissions


def _validate_dossier_candidate(current) -> dict:
    request = _request(current)
    candidate = _text(current, "dossier.candidate")
    permissions = json.loads(_text(current, "dossier.permissions"))
    issues = dossier_schema.lint_dossier(candidate)
    if issues:
        raise RuntimeError(";".join(issues))
    meta = dossier_schema.parse_frontmatter(candidate)
    if meta.get("initiated") != request["analysis_date"]:
        raise RuntimeError("dossier initiated date is missing or incorrect")
    if {key: value for key, value in meta.items() if key != "initiated"} != permissions["frontmatter"]:
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
        if not block.startswith(prefix) or sha256_bytes(prefix.encode("utf-8")) != expected["sha256"]:
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
        "summary_tokens": dossier_schema.est_tokens(
            dossier_schema._summary_block(candidate)
        ),
    }


def dossier_validate(handle=None) -> dict:
    current = handle or _active_handle()
    value = _validate_dossier_candidate(current)
    atomic_write_json(Path(current.staging) / "session_outputs/dossier.validation.json", value)
    return value


def dossier_prepare_publication(handle=None) -> dict:
    current = handle or _active_handle()
    request = _request(current)
    validation = json.loads(_text(current, "dossier.validation"))
    candidate = artifacts.bind_artifact_hash(current, "dossier.candidate")
    if validation.get("candidate_sha256") != candidate["sha256"]:
        raise RuntimeError("validated dossier candidate changed before publication")
    value = {
        "schema_version": 1,
        "kind": "dossier-init",
        "mode": "INIT",
        "run_id": current.run_id,
        "engine": current.engine,
        "analysis_date": request["analysis_date"],
        "code": request["subject"],
        "candidate_sha256": candidate["sha256"],
    }
    atomic_write_json(Path(current.staging) / "session_outputs/dossier.publication.json", value)
    return value


def scan_frame(handle=None) -> dict:
    current = handle or _active_handle()
    from autoresearch.scan import frame, strategist_pack

    market = Path(current.staging) / "market_pack.json"
    projected = Path(current.staging) / "strategist_pack.json"
    if frame.main([current.analysis_date, "--json-out", str(market)]) != 0:
        raise RuntimeError("scan frame failed")
    if strategist_pack.main([str(market), "--out", str(projected)]) != 0:
        raise RuntimeError("strategist projection failed")
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
    return {"schema_version": 1, "steps": results, "summary": str(summary), "l2": str(l2)}


def scan_gate1(handle=None) -> dict:
    current = handle or _active_handle()
    from autoresearch.scan.gates import gate1_decide, record_gate_stage_result

    result = gate1_decide(
        Path(current.staging), force_full=bool(_request(current)["force_full"])
    )
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
    from autoresearch.scan.user_config import knob
    from autoresearch.sector import pack as sector_pack, reuse as sector_reuse
    from autoresearch.session_agent.workflows.scan import _sector_key

    scan_dir = Path(current.staging)
    sectors, provenance = sector_pack.select_briefing_sectors(
        scan_dir, k=int(knob("sector", "max_briefs", None, 6))
    )
    found = sector_reuse.find_reusable(current.analysis_date, sectors)
    if found:
        sector_reuse.apply_reuse(
            current.analysis_date, found, root=ws.scan_root()
        )
    pack_dir = scan_dir / "session_inputs/sectors"
    rows = []
    for industry in sectors:
        key = _sector_key(str(industry))
        payload = sector_pack.sector_pack(industry, scan_dir)
        atomic_write_json(pack_dir / f"{key}.json", payload)
        rows.append(
            {
                "industry": str(industry),
                "key": key,
                "reused": industry in found,
                "provenance": provenance.get(industry),
            }
        )
    value = {"schema_version": 1, "mode": "FULL", "sectors": rows}
    atomic_write_json(scan_dir / "session_outputs/sector.list.json", value)
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
    return value


def scan_l3_prepare(handle=None) -> dict:
    current = handle or _active_handle()
    from autoresearch.scan.l3.prompt import prepare_l3_table

    return prepare_l3_table(current.analysis_date, root=ws.scan_root())


def scan_l3_lint(handle=None) -> dict:
    current = handle or _active_handle()
    from autoresearch.scan.l3.validation import build_repair_pack, lint_judged

    value = lint_judged(current.analysis_date, root=ws.scan_root())
    atomic_write_json(Path(current.staging) / "session_outputs/l3.validation.json", value)
    if not value.get("ok"):
        build_repair_pack(current.analysis_date, root=ws.scan_root())
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
    return value


def scan_l3_repair_apply(handle=None) -> dict:
    current = handle or _active_handle()
    from autoresearch.scan.l3.validation import apply_repair_patch

    applied = apply_repair_patch(current.analysis_date, root=ws.scan_root())
    value = {"schema_version": 1, "status": "APPLIED", **applied}
    atomic_write_json(Path(current.staging) / "session_outputs/l3.repair.json", value)
    return value


def scan_l3_merge(handle=None) -> dict:
    current = handle or _active_handle()
    from autoresearch.scan.gates import gate2, record_gate_stage_result
    from autoresearch.scan.l3.merge import write_finalists

    gate1 = json.loads(_text(current, "scan.gate1.result"))
    budget = gate1.get("l4_budget")
    if type(budget) is not int or budget < 1:
        raise RuntimeError("invalid frozen GATE1 l4_budget")
    write_finalists(current.analysis_date, budget=budget, root=ws.scan_root())
    result = gate2(Path(current.staging), budget=budget)
    record_gate_stage_result(Path(current.staging), result, budget=budget)
    atomic_write_json(Path(current.staging) / "session_outputs/gate2.json", result)
    if not result.get("ok"):
        raise RuntimeError(str(result.get("reason") or "GATE2 failed"))
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
    initialized = legacy_scan.initialize_tickets(current, codes, meta=meta)
    config = getattr(current.contract, "user_config", {}) or {}
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


def scan_l4_slim(handle=None, *, code: str | None = None) -> dict:
    current = handle or _active_handle()
    from autoresearch.session_agent import legacy_scan

    code6 = _require_code(code)
    result = legacy_scan.prepare_slim(current, code6)
    if not result.get("ok"):
        raise RuntimeError(str(result.get("reason") or "L4 slim failed"))
    scan_dir = Path(current.staging)
    attempt = _l4_attempt(scan_dir, code6)
    if attempt > 1:
        task = legacy_scan._payload(current)["tasks"][code6]
        source = Path(task["artifacts"]["slim"]["path"])
        target = _retry_dir(scan_dir, code6, attempt) / "slim.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    return result


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


def scan_l4_intel_status(handle=None, *, code: str | None = None) -> dict:
    current = handle or _active_handle()
    code6 = _require_code(code)
    from autoresearch.scan.l4.intel_guard import guard_intel
    from autoresearch.scan.l4.intel_status import from_guard, write_status

    scan_dir = Path(current.staging)
    attempt = _l4_attempt(scan_dir, code6)
    if attempt > 1:
        retry_intel = _retry_dir(scan_dir, code6, attempt) / "intel.md"
        if retry_intel.is_file():
            shutil.copyfile(retry_intel, scan_dir / f"_l4_intel_{code6}.md")
    result = guard_intel(scan_dir, code6)
    if result.get("action") in {"KEPT", "TRIMMED"}:
        _normalize_intel(scan_dir, code6)
    status = from_guard(result, code=code6, scan_dir=scan_dir, enabled=True, attempts=1)
    write_status(scan_dir, status)
    if attempt > 1:
        target = _retry_dir(scan_dir, code6, attempt) / "intel_status.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(scan_dir / f"_l4_intel_status_{code6}.json", target)
    return status.to_dict()


def scan_l4_intel_disabled(handle=None, *, code: str | None = None) -> dict:
    current = handle or _active_handle()
    code6 = _require_code(code)
    from autoresearch.scan.l4.intel_status import from_guard, write_status

    scan_dir = Path(current.staging)
    status = from_guard(None, code=code6, scan_dir=scan_dir, enabled=False, attempts=0)
    write_status(scan_dir, status)
    attempt = _l4_attempt(scan_dir, code6)
    if attempt > 1:
        target = _retry_dir(scan_dir, code6, attempt) / "intel_status.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(scan_dir / f"_l4_intel_status_{code6}.json", target)
    return status.to_dict()


def _card_rating(path: Path) -> str:
    rating = parse_rating(path.read_text(encoding="utf-8"), strict=True)
    if rating is None:
        raise RuntimeError(f"card lacks strict rating: {path.name}")
    return rating


def scan_review_plan(handle=None) -> dict:
    current = handle or _active_handle()
    import pandas as pd

    scan_dir = Path(current.staging)
    finalists = pd.read_csv(scan_dir / "finalists.csv", dtype={"code": str})
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
        trigger = (
            "ow_review"
            if rating in {"Buy", "Overweight"}
            else ("sell_review" if pinned and proposal.group(1).upper() == "SELL" else None)
        )
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
    atomic_write_json(scan_dir / "session_outputs/review.plan.json", value)
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


def scan_review_decide(handle=None) -> dict:
    current = handle or _active_handle()
    scan_dir = Path(current.staging)
    plan = json.loads((scan_dir / "session_outputs/review.plan.json").read_text(encoding="utf-8"))
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
    atomic_write_json(scan_dir / "session_outputs/review.decision.json", value)
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
    return value


def scan_l4_finalize(handle=None, *, code: str | None = None) -> dict:
    current = handle or _active_handle()
    code6 = _require_code(code)
    scan_dir = Path(current.staging)
    from autoresearch.scan.stock_stage import record_l4_result
    from autoresearch.session_agent import legacy_scan
    from autoresearch.session_agent.workflows.scan import ensemble_record

    decision = json.loads(
        (scan_dir / "session_outputs/review.decision.json").read_text(encoding="utf-8")
    )
    matches = [row for row in decision.get("decisions") or [] if row.get("code") == code6]
    if len(matches) != 1:
        raise RuntimeError(f"review decision missing for {code6}")
    row = matches[0]
    attempt = int(row.get("attempt") or 1)
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
    value = {"schema_version": 1, "status": "SUCCEEDED", "states": states}
    atomic_write_json(Path(current.staging) / "session_outputs/l4.complete.json", value)
    return value


def scan_assemble(handle=None) -> dict:
    current = handle or _active_handle()
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
    return value


def scan_gate4(handle=None) -> dict:
    current = handle or _active_handle()
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
    return {"schema_version": 1, "usage": ledger, "reconcile": reconcile}


def scan_observe(handle=None) -> dict:
    current = handle or _active_handle()
    from autoresearch.scan.post_run import publish_run_observation
    from autoresearch.session_agent.progress import scan_progress
    from autoresearch.session_agent.workflows.scan import directory_manifest

    scan_dir = Path(current.staging)
    candidate = _report_candidate(current)
    observation = publish_run_observation(
        scan_dir,
        report_dir=candidate,
        usage_path=scan_dir / "_token_usage.json",
        decision_write="verify",
    )
    fixed = scan_dir / "session_outputs/report_files"
    for name in ("brief.md", "summary.md", "appendix.md", "manifest.json"):
        source = candidate / name
        if not source.is_file():
            raise RuntimeError(f"observed report is missing: {name}")
        atomic_write_bytes(fixed / name, source.read_bytes())
    plan = json.loads((scan_dir / "session_outputs/report.plan.json").read_text(encoding="utf-8"))
    bundle = {
        "schema_version": 1,
        "run_id": current.run_id,
        "engine": current.engine,
        "analysis_date": current.analysis_date,
        "folder": plan["folder"],
        "candidate_relative": plan["candidate_relative"],
        "files": directory_manifest(candidate),
    }
    atomic_write_json(scan_dir / "session_outputs/scan.publication.json", bundle)
    progress = {
        **scan_progress(current),
        "checkpoint": "CP7",
        "gate4": json.loads((scan_dir / "session_outputs/gate4.json").read_text(encoding="utf-8")),
        "observation": observation,
        "report_candidate": plan["candidate_relative"],
    }
    atomic_write_json(scan_dir / "session_outputs/progress.final.json", progress)
    return bundle


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="autoresearch.session_agent.domain_ops")
    parser.add_argument(
        "command",
        choices=(
            "stock-validate",
            "stock-publish",
            "stock-full-validate",
            "stock-full-assemble",
            "macro-harvest",
            "macro-lite-frame",
            "macro-lite-validate",
            "macro-publish",
            "macro-full-validate",
            "macro-full-assemble",
            "sector-prepare",
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
    args = parser.parse_args(argv)
    if args.command == "stock-validate":
        value = stock_validate()
    elif args.command == "stock-publish":
        value = stock_prepare_publication()
    elif args.command == "stock-full-validate":
        value = stock_full_validate()
    elif args.command == "stock-full-assemble":
        value = stock_full_assemble()
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
        value = scan_review_plan()
    elif args.command == "scan-review-none":
        value = scan_review_none(code=args.subject)
    elif args.command == "scan-review-decide":
        value = scan_review_decide()
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
    "macro_full_assemble", "macro_full_validate", "macro_harvest_run",
    "macro_lite_prepare", "macro_lite_validate", "macro_prepare_publication",
    "sector_full_validate", "sector_lite_validate", "sector_prepare",
    "sector_prepare_publication", "sector_validate",
    "dossier_build_skeleton", "dossier_prefetch_run", "dossier_prepare_publication",
    "dossier_validate",
    "stock_full_assemble", "stock_full_validate", "stock_prepare_publication",
    "stock_validate",
    "scan_frame", "scan_gate1", "scan_gate2_skip", "scan_l3_lint",
    "scan_l3_repair_apply", "scan_l3_repair_skip",
    "scan_assemble", "scan_gate4", "scan_l3_merge", "scan_l3_prepare", "scan_l4_complete",
    "scan_l4_finalize", "scan_l4_intel_disabled",
    "scan_l4_intel_status", "scan_l4_prepare", "scan_l4_skip", "scan_l4_slim",
    "scan_observe", "scan_prelude", "scan_review_decide", "scan_review_none", "scan_review_plan",
    "scan_review_skip", "scan_review3_skip", "scan_sector_prepare", "scan_sector_skip",
    "scan_usage",
]
