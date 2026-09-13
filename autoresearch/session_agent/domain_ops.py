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
from autoresearch.common.atomic import atomic_write_bytes, atomic_write_json, sha256_bytes
from autoresearch.contracts.agent_output import L4_CARD
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="autoresearch.session_agent.domain_ops")
    parser.add_argument(
        "command",
        choices=(
            "stock-validate",
            "stock-publish",
            "stock-full-validate",
            "stock-full-assemble",
        ),
    )
    args = parser.parse_args(argv)
    if args.command == "stock-validate":
        value = stock_validate()
    elif args.command == "stock-publish":
        value = stock_prepare_publication()
    elif args.command == "stock-full-validate":
        value = stock_full_validate()
    else:
        value = stock_full_assemble()
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "stock_full_assemble", "stock_full_validate", "stock_prepare_publication",
    "stock_validate",
]
