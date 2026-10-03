"""Command-line boundary with engine selection before project imports."""

from __future__ import annotations

import argparse
import dataclasses
import enum
import json
import os
import sys
from pathlib import Path


def _parser(*, read_only=False):
    parser = argparse.ArgumentParser(prog="python -m autoresearch.session_agent")
    subparsers = parser.add_subparsers(dest="command", required=True)
    begin = subparsers.add_parser("begin")
    begin.add_argument("--request-file", required=True)
    begin.add_argument("--kind")
    begin.add_argument("--mode")
    begin.add_argument("--date")
    begin.add_argument("--subject")
    begin.add_argument(
        "--orchestration",
        choices=("session_v1", "legacy"),
        default="session_v1",
    )
    begin.add_argument("--legacy-reason")
    begin.add_argument("--ignore-scan-lock", action="store_true")
    for command in ("status", "next", "resume", "finish"):
        child = subparsers.add_parser(command)
        child.add_argument("--run-id", required=True)
    metering = subparsers.add_parser("metering")
    metering.add_argument("--run-id", required=True)
    acceptance = subparsers.add_parser("acceptance-status")
    acceptance.add_argument("--records-file")
    acceptance.add_argument("--evidence-root")
    accept = subparsers.add_parser("accept-run")
    accept.add_argument("--run-id", required=True)
    accept.add_argument("--scenario", required=True)
    accept.add_argument("--evidence-kind", choices=("REAL_SESSION", "REAL_SESSION_DRILL"),
                        default="REAL_SESSION")
    accept.add_argument("--notes", default="")
    accept.add_argument("--publication-id")
    accept.add_argument("--report")
    accept.add_argument("--replay-timeout", type=float)
    accept.add_argument("--write", action="store_true")
    imported = subparsers.add_parser("acceptance-import")
    imported.add_argument("--proof", required=True)
    claim = subparsers.add_parser("claim")
    claim.add_argument("--run-id", required=True)
    claim.add_argument("--task-id", required=True)
    claim.add_argument("--expected-attempt", required=True, type=int)
    bind = subparsers.add_parser("bind-host-evidence")
    bind.add_argument("--run-id", required=True)
    bind.add_argument("--task-id", required=True)
    bind.add_argument("--attempt", required=True, type=int)
    bind.add_argument("--transcript-file", required=True)
    bind.add_argument("--session-ref", required=True)
    bind.add_argument("--context-ref", required=True)
    bind.add_argument("--parent-context-ref")
    bind.add_argument("--start-ordinal", required=True, type=int)
    bind.add_argument("--end-ordinal", required=True, type=int)
    bind.add_argument(
        "--context-source",
        required=True,
        choices=("MAIN", "SUBAGENT", "SHARED"),
    )
    execute = subparsers.add_parser("execute")
    execute.add_argument("--run-id", required=True)
    execute.add_argument("--task-id", required=True)
    execute.add_argument("--attempt", required=True, type=int)
    execute.add_argument("--params-file", required=True)
    calculation = subparsers.add_parser("calculate")
    calculation.add_argument("--run-id", required=True)
    calculation.add_argument("--task-id", required=True)
    calculation.add_argument("--attempt", required=True, type=int)
    calculation.add_argument("--params-file", required=True)
    source_fields = subparsers.add_parser("source-fields")
    source_fields.add_argument("--run-id", required=True)
    source_fields.add_argument("--task-id", required=True)
    source_fields.add_argument("--attempt", required=True, type=int)
    source_fields.add_argument("--params-file", required=True)
    for command in ("submit", "precheck"):
        submission = subparsers.add_parser(command)
        submission.add_argument("--run-id", required=True)
        submission.add_argument("--submission-file", required=True)
        submission.add_argument("--host-receipt-file")
    fail = subparsers.add_parser("fail")
    fail.add_argument("--run-id", required=True)
    fail.add_argument("--task-id", required=True)
    fail.add_argument("--attempt", required=True, type=int)
    fail.add_argument("--error-class", required=True)
    fail.add_argument("--message", required=True)
    retry_l4 = subparsers.add_parser("retry-l4")
    retry_l4.add_argument("--run-id", required=True)
    retry_l4.add_argument("--code", required=True)
    retry_l4.add_argument("--expected-attempt", required=True, type=int)
    verify_report = subparsers.add_parser("verify-report")
    verify_report.add_argument("--report-path", required=True)
    verify_report.add_argument("--expected-run-id")
    verify_report.add_argument(
        "--level", choices=("integrity", "full"), default="full"
    )
    from autoresearch.session_agent.mailbox_cli import add_parsers

    if not read_only:
        add_parsers(subparsers)
    return parser


#: Commands that only read frozen evidence: they never adopt ``--run-id`` as the active run.
_OFFLINE_COMMANDS = frozenset({"metering", "acceptance-status", "accept-run", "acceptance-import"})


def _load(path):
    with open(path, encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError("JSON object required")
    return value


def _emit(value):
    def encode(item):
        if dataclasses.is_dataclass(item):
            return dataclasses.asdict(item)
        if isinstance(item, (Path, enum.Enum)):
            return str(item.value if isinstance(item, enum.Enum) else item)
        raise TypeError(f"not JSON serializable: {type(item).__name__}")

    sys.stdout.write(
        json.dumps(value, ensure_ascii=False, sort_keys=True, default=encode) + "\n"
    )


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
    argv = list(sys.argv[1:] if argv is None else argv)
    args = _parser(read_only=bool(argv and argv[0] in _OFFLINE_COMMANDS)).parse_args(argv)
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
        if args.command not in _OFFLINE_COMMANDS:
            os.environ["AUTORESEARCH_RUN_ID"] = run_id
    if args.command in _OFFLINE_COMMANDS:
        try:
            if args.command == "acceptance-status":
                from autoresearch.session_agent.evaluation import acceptance_status_from_paths
                value = acceptance_status_from_paths(records_file=args.records_file, evidence_root=args.evidence_root)
            elif args.command == "accept-run":
                from autoresearch.session_agent.acceptance_cli import collect
                value = collect(args.run_id, args.scenario, evidence_kind=args.evidence_kind,
                                notes=args.notes, publication_id=args.publication_id,
                                report=args.report, replay_timeout=args.replay_timeout,
                                write=args.write)
            elif args.command == "acceptance-import":
                from autoresearch.session_agent.acceptance_cli import import_proof
                value = import_proof(args.proof)
            else:
                from autoresearch.session_agent.metering import build_metering
                from autoresearch.trace.capsule import load_run
                value = build_metering(load_run(args.run_id))
            _emit(value)
            return 0
        except (ValueError, TypeError, KeyError, OSError, RuntimeError) as exc:
            _emit(_error(args.command, run_id, "CONTRACT_ERROR", str(exc)))
            return 2
    try:
        from autoresearch.session_agent import service
        from autoresearch.session_agent.executor import OperationRunning
        from autoresearch.session_agent.hosts.base import HostCapabilityError
        from autoresearch.session_agent.origin import EntrypointSelectionError
        from autoresearch.session_agent.store import TaskConflict
        from autoresearch.session_agent.validation import DomainValidationError

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
            from autoresearch.common.scan_lock import EXIT_HELD, begin_refusal

            refusal = begin_refusal(request.get("kind"), ignore=args.ignore_scan_lock)
            if refusal:  # unattended scan_run holds the lock (review I3)
                _emit(_error(args.command, run_id, "SCAN_LOCK_HELD", refusal))
                return EXIT_HELD
            from autoresearch.session_agent.origin import begin_via_entry

            value = begin_via_entry(
                request,
                orchestration=args.orchestration,
                legacy_reason=args.legacy_reason,
            )
        elif args.command in {"status", "next", "resume", "finish"}:
            value = getattr(service, args.command)(args.run_id)
        elif args.command == "claim":
            value = service.claim(args.run_id, args.task_id, args.expected_attempt)
        elif args.command == "bind-host-evidence":
            from autoresearch.session_agent.host_evidence import bind_task_transcript

            value = bind_task_transcript(
                args.run_id,
                args.task_id,
                args.attempt,
                args.transcript_file,
                session_ref=args.session_ref,
                context_ref=args.context_ref,
                parent_context_ref=args.parent_context_ref,
                start_ordinal=args.start_ordinal,
                end_ordinal=args.end_ordinal,
                context_source=args.context_source,
            )
        elif args.command == "execute":
            value = service.execute(
                args.run_id, args.task_id, args.attempt, _load(args.params_file)
            )
        elif args.command == "calculate":
            value = service.calculate(
                args.run_id, args.task_id, args.attempt, _load(args.params_file)
            )
        elif args.command == "source-fields":
            value = service.source_fields(
                args.run_id, args.task_id, args.attempt, _load(args.params_file)
            )
        elif args.command == "fail":
            value = service.fail(
                args.run_id,
                args.task_id,
                args.attempt,
                args.error_class,
                args.message,
            )
        elif args.command == "retry-l4":
            value = service.retry_l4(args.run_id, args.code, args.expected_attempt)
        elif args.command == "verify-report":
            from autoresearch.trace.verification import verify_report

            value = verify_report(
                args.report_path,
                expected_run_id=args.expected_run_id,
                level=args.level,
            )
        elif args.command == "run":
            from autoresearch.session_agent.mailbox_cli import run_command

            value, code = run_command(args)
            _emit(value)
            return code
        elif args.command == "mailbox":
            from autoresearch.session_agent.mailbox_cli import mailbox_command

            value = mailbox_command(args)
        else:
            host_receipt = _load(args.host_receipt_file) if args.host_receipt_file else None
            value = getattr(service, args.command)(
                args.run_id, _load(args.submission_file), host_receipt=host_receipt
            )
        _emit(value)
        return 0
    except DomainValidationError as exc:
        _emit(_error(args.command, run_id, "DOMAIN_VALIDATION_FAILED", str(exc)))
        return 3
    except HostCapabilityError as exc:
        _emit(_error(args.command, run_id, "HOST_CAPABILITY_REQUIRED", str(exc)))
        return 4
    except EntrypointSelectionError as exc:
        _emit(_error(args.command, run_id, exc.code, str(exc)))
        return 7
    except (OperationRunning, OSError) as exc:
        _emit(_error(args.command, run_id, "RETRYABLE_TOOL_FAILURE", str(exc)))
        return 5
    except TaskConflict as exc:
        _emit(_error(args.command, run_id, "IDENTITY_CONFLICT", str(exc)))
        return 6
    except (ValueError, TypeError, KeyError, FileNotFoundError) as exc:
        _emit(_error(args.command, run_id, "CONTRACT_ERROR", str(exc)))
        return 2
    except RuntimeError as exc:
        _emit(_error(args.command, run_id, "STATE_CONFLICT", str(exc)))
        return 6


if __name__ == "__main__":
    raise SystemExit(main())
