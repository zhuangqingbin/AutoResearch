"""Static deterministic operation registry; no shell or dynamic imports."""
from __future__ import annotations

import re
import sys
from collections.abc import Callable
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
}


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


__all__ = ["build_argv", "operation_spec"]
