"""Static deterministic operation registry; no shell or dynamic imports."""
from __future__ import annotations

import re
import sys
from collections.abc import Callable
from copy import deepcopy
from datetime import date

from autoresearch.contracts.session_task import require_exact_fields

_TICKER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.-]{0,31}", re.ASCII)


def _noop(params: dict) -> list[str]:
    require_exact_fields(params, frozenset({"message"}))
    message = params["message"]
    if type(message) is not str or len(message) > 200:
        raise ValueError("invalid noop message")
    return [sys.executable, "-c", "print('ok')", message]


def _stock_harvest(params: dict) -> list[str]:
    require_exact_fields(
        params, frozenset({"ticker", "analysis_date", "asset_type", "peers", "slim"})
    )
    ticker = params["ticker"]
    if type(ticker) is not str or not _TICKER_RE.fullmatch(ticker):
        raise ValueError("invalid ticker")
    try:
        date.fromisoformat(params["analysis_date"])
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid analysis_date") from exc
    if params["asset_type"] not in {"stock", "crypto"}:
        raise ValueError("invalid asset_type")
    peers = params["peers"]
    if type(peers) is not list or any(
        type(peer) is not str or not _TICKER_RE.fullmatch(peer) for peer in peers
    ):
        raise ValueError("invalid peers")
    if type(params["slim"]) is not bool:
        raise ValueError("slim must be boolean")
    argv = [
        "uv", "run", "--no-sync", "python", "-m", "autoresearch.analyze.harvest",
        ticker, params["analysis_date"], params["asset_type"], ",".join(peers),
    ]
    if params["slim"]:
        argv.append("--slim")
    return argv


def _no_params(command: str, params: dict) -> list[str]:
    require_exact_fields(params, frozenset())
    return [
        sys.executable,
        "-m",
        "autoresearch.session_agent.domain_ops",
        command,
    ]


_OPERATIONS: dict[str, dict[str, object]] = {
    "test.noop": {"builder": _noop, "idempotent": True, "stage": "session"},
    "stock.harvest": {"builder": _stock_harvest, "idempotent": True, "stage": "harvest"},
    "stock.validate": {
        "builder": lambda params: _no_params("stock-validate", params),
        "idempotent": True,
        "stage": "card",
    },
    "stock.publish": {
        "builder": lambda params: _no_params("stock-publish", params),
        "idempotent": True,
        "stage": "publish",
    },
    "stock.full.validate": {
        "builder": lambda params: _no_params("stock-full-validate", params),
        "idempotent": True,
        "stage": "assemble",
    },
    "stock.full.assemble": {
        "builder": lambda params: _no_params("stock-full-assemble", params),
        "idempotent": True,
        "stage": "assemble",
    },
    "macro.harvest": {
        "builder": lambda params: _no_params("macro-harvest", params),
        "idempotent": True,
        "stage": "harvest",
    },
    "macro.lite.frame": {
        "builder": lambda params: _no_params("macro-lite-frame", params),
        "idempotent": True,
        "stage": "frame",
    },
    "macro.lite.validate": {
        "builder": lambda params: _no_params("macro-lite-validate", params),
        "idempotent": True,
        "stage": "write",
    },
    "macro.publish": {
        "builder": lambda params: _no_params("macro-publish", params),
        "idempotent": True,
        "stage": "publish",
    },
    "macro.full.validate": {
        "builder": lambda params: _no_params("macro-full-validate", params),
        "idempotent": True,
        "stage": "assemble",
    },
    "macro.full.assemble": {
        "builder": lambda params: _no_params("macro-full-assemble", params),
        "idempotent": True,
        "stage": "assemble",
    },
    "sector.prepare": {
        "builder": lambda params: _no_params("sector-prepare", params),
        "idempotent": True,
        "stage": "prepare",
    },
    "sector.validate": {
        "builder": lambda params: _no_params("sector-validate", params),
        "idempotent": True,
        "stage": "validate",
    },
    "sector.publish": {
        "builder": lambda params: _no_params("sector-publish", params),
        "idempotent": True,
        "stage": "publish",
    },
    "dossier.prefetch": {
        "builder": lambda params: _no_params("dossier-prefetch", params),
        "idempotent": True,
        "stage": "prefetch",
    },
    "dossier.skeleton": {
        "builder": lambda params: _no_params("dossier-skeleton", params),
        "idempotent": True,
        "stage": "skeleton",
    },
    "dossier.validate": {
        "builder": lambda params: _no_params("dossier-validate", params),
        "idempotent": True,
        "stage": "lint",
    },
    "dossier.publish": {
        "builder": lambda params: _no_params("dossier-publish", params),
        "idempotent": True,
        "stage": "publish",
    },
}

_NO_PARAMS = {"type": "object", "required": [], "additionalProperties": False}
_CATALOG_META: dict[str, dict[str, object]] = {
    "test.noop": {
        "params": {"message": "string<=200"},
        "side_effects": "none; test-only captured process",
        "outputs": ["test fixture output"],
        "callers": ["session-agent tests"],
        "errors": ["INVALID_PARAMS", "OPERATION_FAILED"],
        "limits": "test-only",
        "retained_cli": False,
    },
    "stock.harvest": {
        "params": {
            "ticker": "validated symbol",
            "analysis_date": "YYYY-MM-DD",
            "asset_type": "stock|crypto",
            "peers": "validated symbol[]",
            "slim": "boolean",
        },
        "side_effects": "reads vendors/lake; writes the active stock run staging",
        "outputs": ["stock.slim", "stock.deep", "stock.context", "stock.indicators"],
        "callers": ["stock.harvest"],
        "errors": ["INVALID_PARAMS", "DATA_CONTRACT", "OPERATION_FAILED"],
        "limits": "existing harvester vendor and retry caps",
        "retained_cli": True,
    },
    "stock.validate": {
        "params": _NO_PARAMS,
        "side_effects": "writes card validation in active run",
        "outputs": ["stock.card.validation"],
        "callers": ["stock.validate"],
        "errors": ["DOMAIN_VALIDATION", "ARTIFACT_CONFLICT"],
        "limits": "no network",
        "retained_cli": False,
    },
    "stock.publish": {
        "params": _NO_PARAMS,
        "side_effects": "prepares a run-scoped publication bundle",
        "outputs": ["stock.publication.bundle"],
        "callers": ["stock.publish"],
        "errors": ["DOMAIN_VALIDATION", "ARTIFACT_CONFLICT"],
        "limits": "no network",
        "retained_cli": False,
    },
    "stock.full.validate": {
        "params": _NO_PARAMS,
        "side_effects": "writes full-product validation in active run",
        "outputs": ["stock.full.validation"],
        "callers": ["stock.full.validate"],
        "errors": ["MISSING_PRODUCT", "DOMAIN_VALIDATION"],
        "limits": "no network",
        "retained_cli": False,
    },
    "stock.full.assemble": {
        "params": _NO_PARAMS,
        "side_effects": "runs the legacy assembler into run staging",
        "outputs": ["stock.full.report", "stock.full.manifest", "stock.publication.bundle"],
        "callers": ["stock.assemble"],
        "errors": ["MISSING_PRODUCT", "ASSEMBLY_FAILED"],
        "limits": "no network",
        "retained_cli": True,
    },
    "macro.harvest": {
        "params": _NO_PARAMS,
        "side_effects": "reads existing macro vendors; writes active run staging",
        "outputs": ["macro.data", "macro.global_tape"],
        "callers": ["macro.harvest"],
        "errors": ["DATA_CONTRACT", "OPERATION_FAILED"],
        "limits": "existing FRED/yfinance/akshare/tushare retry caps",
        "retained_cli": True,
    },
    "macro.lite.frame": {
        "params": _NO_PARAMS,
        "side_effects": "builds market and strategist projections in active run",
        "outputs": ["macro.market_pack", "macro.strategist_pack"],
        "callers": ["macro.frame"],
        "errors": ["DATA_CONTRACT", "PROJECTION_FAILED"],
        "limits": "one market-frame build; no L3/L4",
        "retained_cli": True,
    },
    "macro.lite.validate": {
        "params": _NO_PARAMS,
        "side_effects": "writes six-section brief validation",
        "outputs": ["macro.lite.validation"],
        "callers": ["macro.lite.validate"],
        "errors": ["DOMAIN_VALIDATION"],
        "limits": "no network",
        "retained_cli": False,
    },
    "macro.publish": {
        "params": _NO_PARAMS,
        "side_effects": "prepares a run-scoped macro publication bundle",
        "outputs": ["macro.publication.bundle"],
        "callers": ["macro.publish"],
        "errors": ["ARTIFACT_CONFLICT"],
        "limits": "no network",
        "retained_cli": False,
    },
    "macro.full.validate": {
        "params": _NO_PARAMS,
        "side_effects": "validates required sections and every allocation row",
        "outputs": ["macro.full.validation"],
        "callers": ["macro.full.validate"],
        "errors": ["MISSING_PRODUCT", "DOMAIN_VALIDATION"],
        "limits": "no network",
        "retained_cli": False,
    },
    "macro.full.assemble": {
        "params": _NO_PARAMS,
        "side_effects": "assembles report and candidate state inside active run",
        "outputs": ["macro.full.report", "macro.state.candidate", "macro.publication.bundle"],
        "callers": ["macro.assemble"],
        "errors": ["MISSING_PRODUCT", "ASSEMBLY_FAILED"],
        "limits": "no network",
        "retained_cli": True,
    },
    "sector.prepare": {
        "params": _NO_PARAMS,
        "side_effects": "copies verified same-engine scan inputs or builds one market frame",
        "outputs": ["sector.input.manifest", "sector.pack", "sector.reuse"],
        "callers": ["sector.*.prepare"],
        "errors": ["DATA_CONTRACT", "UNKNOWN_INDUSTRY"],
        "limits": "no market ranking, L3, or L4",
        "retained_cli": True,
    },
    "sector.validate": {
        "params": _NO_PARAMS,
        "side_effects": "writes terrain/full-report validation",
        "outputs": ["sector.validation"],
        "callers": ["sector.*.validate"],
        "errors": ["DOMAIN_VALIDATION"],
        "limits": "no network",
        "retained_cli": False,
    },
    "sector.publish": {
        "params": _NO_PARAMS,
        "side_effects": "prepares a run-scoped sector publication bundle",
        "outputs": ["sector.publication.bundle"],
        "callers": ["sector.*.publish"],
        "errors": ["ARTIFACT_CONFLICT"],
        "limits": "no network",
        "retained_cli": False,
    },
    "dossier.prefetch": {
        "params": _NO_PARAMS,
        "side_effects": "runs three existing data legs into active run staging",
        "outputs": ["dossier.prefetch"],
        "callers": ["dossier.*.prefetch"],
        "errors": ["OPERATION_FAILED"],
        "limits": "each prefetch leg degrades independently",
        "retained_cli": True,
    },
    "dossier.skeleton": {
        "params": _NO_PARAMS,
        "side_effects": "builds candidate skeleton and immutable edit permissions",
        "outputs": ["dossier.skeleton", "dossier.permissions"],
        "callers": ["dossier.*.skeleton"],
        "errors": ["ALREADY_INITIALIZED", "INVALID_SKELETON"],
        "limits": "no network",
        "retained_cli": True,
    },
    "dossier.validate": {
        "params": _NO_PARAMS,
        "side_effects": "writes candidate lint and byte-protection result",
        "outputs": ["dossier.validation"],
        "callers": ["dossier.*.lint"],
        "errors": ["DOMAIN_VALIDATION", "DETERMINISTIC_SECTION_CHANGED"],
        "limits": "no network",
        "retained_cli": False,
    },
    "dossier.publish": {
        "params": _NO_PARAMS,
        "side_effects": "prepares a run-scoped dossier publication bundle",
        "outputs": ["dossier.publication.bundle"],
        "callers": ["dossier.*.publish"],
        "errors": ["ARTIFACT_CONFLICT"],
        "limits": "no network",
        "retained_cli": False,
    },
}

if set(_CATALOG_META) != set(_OPERATIONS):
    raise RuntimeError("operation catalog and executable registry differ")


def operation_spec(operation: str) -> dict:
    try:
        return _OPERATIONS[operation]
    except KeyError as exc:
        raise KeyError(f"unknown operation: {operation}") from exc


def build_argv(operation: str, params: dict) -> list[str]:
    spec = operation_spec(operation)
    builder = spec["builder"]
    if not isinstance(params, dict) or not isinstance(builder, Callable):
        raise ValueError("operation params must be an object")
    return builder(params)


def operation_catalog() -> dict[str, dict[str, object]]:
    result = {}
    for operation, metadata in _CATALOG_META.items():
        spec = _OPERATIONS[operation]
        result[operation] = {
            **deepcopy(metadata),
            "idempotent": bool(spec["idempotent"]),
            "stage": str(spec["stage"]),
        }
    return result


__all__ = ["build_argv", "operation_catalog", "operation_spec"]
