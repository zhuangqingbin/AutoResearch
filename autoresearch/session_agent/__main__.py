"""Command-line boundary with engine selection before project imports."""

from __future__ import annotations

import argparse
import json
import os
import sys


def _parser():
    parser = argparse.ArgumentParser(prog="python -m autoresearch.session_agent")
    subparsers = parser.add_subparsers(dest="command", required=True)
    begin = subparsers.add_parser("begin")
    begin.add_argument("--request-file", required=True)
    begin.add_argument("--kind")
    begin.add_argument("--mode")
    begin.add_argument("--date")
    begin.add_argument("--subject")
    for command in ("status", "next", "resume", "finish"):
        child = subparsers.add_parser(command)
        child.add_argument("--run-id", required=True)
    claim = subparsers.add_parser("claim")
    claim.add_argument("--run-id", required=True)
    claim.add_argument("--task-id", required=True)
    claim.add_argument("--expected-attempt", required=True, type=int)
    execute = subparsers.add_parser("execute")
    execute.add_argument("--run-id", required=True)
    execute.add_argument("--task-id", required=True)
    execute.add_argument("--attempt", required=True, type=int)
    execute.add_argument("--params-file", required=True)
    submit = subparsers.add_parser("submit")
    submit.add_argument("--run-id", required=True)
    submit.add_argument("--submission-file", required=True)
    submit.add_argument("--host-receipt-file")
    return parser


def _load(path):
    with open(path, encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError("JSON object required")
    return value


def _emit(value):
    sys.stdout.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")


def _error(command, run_id, code, message):
    return {
        "schema_version": 1,
        "command": command,
        "run_id": run_id,
        "state": "BLOCKED",
        "tasks": [],
        "result": None,
        "errors": [{"code": code, "message": message}],
    }


def main(argv=None):
    args = _parser().parse_args(argv)
    engine = os.environ.get("AUTORESEARCH_ENGINE", "").strip().lower()
    run_id = getattr(args, "run_id", "")
    if engine not in {"claude", "codex"}:
        _emit(
            _error(
                args.command,
                run_id,
                "EXPLICIT_ENGINE_REQUIRED",
                "set AUTORESEARCH_ENGINE=claude|codex",
            )
        )
        return 2
    if run_id:
        existing = os.environ.get("AUTORESEARCH_RUN_ID")
        if existing and existing != run_id:
            _emit(
                _error(
                    args.command,
                    run_id,
                    "RUN_ID_CONFLICT",
                    "AUTORESEARCH_RUN_ID conflicts with --run-id",
                )
            )
            return 6
        os.environ["AUTORESEARCH_RUN_ID"] = run_id
    try:
        from autoresearch.session_agent import service

        if args.command == "begin":
            request = _load(args.request_file)
            assertions = {
                "kind": args.kind,
                "requested_mode": args.mode,
                "analysis_date": args.date,
                "subject": args.subject,
            }
            for field, expected in assertions.items():
                if expected is not None and request.get(field) != expected:
                    raise ValueError(f"--{field} conflicts with request file")
            value = service.begin(request)
        elif args.command in {"status", "next", "resume", "finish"}:
            value = getattr(service, args.command)(args.run_id)
        elif args.command == "claim":
            value = service.claim(args.run_id, args.task_id, args.expected_attempt)
        elif args.command == "execute":
            value = service.execute(
                args.run_id, args.task_id, args.attempt, _load(args.params_file)
            )
        else:
            host_receipt = _load(args.host_receipt_file) if args.host_receipt_file else None
            value = service.submit(
                args.run_id, _load(args.submission_file), host_receipt=host_receipt
            )
        _emit(value)
        return 0
    except (ValueError, TypeError, KeyError, FileNotFoundError) as exc:
        _emit(_error(args.command, run_id, "CONTRACT_ERROR", str(exc)))
        return 2
    except RuntimeError as exc:
        _emit(_error(args.command, run_id, "STATE_CONFLICT", str(exc)))
        return 6


if __name__ == "__main__":
    raise SystemExit(main())
