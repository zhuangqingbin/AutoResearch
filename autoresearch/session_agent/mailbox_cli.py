"""Host-mode commands: ``run`` (the runner) and ``mailbox wait|complete|pending`` (session side).

The host loop itself is documented once, in ``docs/session-agent/README.md``.
"""
from __future__ import annotations

import json
from pathlib import Path

#: ``run`` exit code when the runner stopped without finishing (BLOCKED / STALLED / …).
EXIT_NOT_FINISHED = 8


def _handle(run_id: str):
    from autoresearch.trace.capsule import require_active_run

    return require_active_run(run_id)


def resolve_max_parallel(handle, explicit: int | None) -> int:
    """``--max-parallel`` else the frozen ``budgets.concurrency.l4_stock`` (else 4)."""
    if explicit is not None:
        if explicit < 1:
            raise ValueError("--max-parallel must be a positive integer")
        return explicit
    config = getattr(getattr(handle, "contract", None), "user_config", None) or {}
    value = ((config.get("budgets") or {}).get("concurrency") or {}).get("l4_stock")
    if value is None:
        from autoresearch.scan.user_config import knob

        value = (knob("budgets", "concurrency", None, {}) or {}).get("l4_stock")
    return value if type(value) is int and value > 0 else 4


def _build_executor(args, handle) -> tuple[object, dict | None]:
    """``(executor, runner timeouts)`` — ``None`` keeps ``executors.base.DEFAULT_TIMEOUTS``."""
    if args.executor == "mailbox":
        from autoresearch.session_agent.executors.mailbox import MailboxExecutor

        return MailboxExecutor(handle.staging, poll_seconds=min(args.poll_seconds, 2.0)), None
    if args.executor == "headless":
        from autoresearch.session_agent.executors import headless_claude

        if handle.engine != "claude":
            raise ValueError(
                f"--executor headless runs `claude -p` and needs a claude run; "
                f"{args.run_id} is a {handle.engine} run (Codex headless is out of scope)")
        executor = headless_claude.HeadlessClaudeExecutor(
            handle.staging, claude_bin=args.claude_bin)
        return executor, dict(headless_claude.HEADLESS_TIMEOUTS)
    raise ValueError(f"unknown executor: {args.executor}")  # pragma: no cover - argparse


def run_command(args) -> tuple[dict, int]:
    from autoresearch.session_agent import runner

    handle = _handle(args.run_id)
    executor, timeouts = _build_executor(args, handle)
    options = {"timeouts": timeouts} if timeouts is not None else {}
    outcome = runner.run_loop(
        args.run_id,
        executor,
        max_parallel=resolve_max_parallel(handle, args.max_parallel),
        poll_seconds=args.poll_seconds,
        max_rounds=args.max_rounds,
        timeout_multiplier=args.timeout_multiplier,
        **options,
    )
    return outcome, 0 if outcome.get("finished") else EXIT_NOT_FINISHED


def _subagent_transcript(session_ref: str, context_ref: str) -> str | None:
    """Claude Code keeps a subagent's transcript at ``<session>/subagents/agent-<id>.jsonl``."""
    from autoresearch.trace import usage_harvest

    folder = usage_harvest.find_session_dir(session_ref)
    if folder is None:
        return None
    name = context_ref if context_ref.startswith("agent-") else f"agent-{context_ref}"
    candidate = Path(folder) / f"{name}.jsonl"
    return str(candidate) if candidate.is_file() else None


def mailbox_command(args) -> dict:
    from autoresearch.session_agent import service
    from autoresearch.session_agent.executors import mailbox

    handle = _handle(args.run_id)
    if args.mailbox_command == "wait":
        return mailbox.wait_request(
            handle.staging, timeout=args.timeout, include_taken=args.include_taken)
    if args.mailbox_command == "pending":
        return {
            "kind": "PENDING",
            "requests": [
                {key: item.get(key) for key in (
                    "task_id", "attempt", "agent_type", "issued_at", "request_path")}
                for item in mailbox.pending_requests(handle.staging, include_taken=True)
            ],
        }
    host_profile = json.loads(
        (service._session_dir(handle) / "host_profile.json").read_text(encoding="utf-8"))
    session_ref = args.session_ref or host_profile["session_ref"]
    transcript = args.transcript_path or _subagent_transcript(session_ref, args.context_ref)
    try:
        path = mailbox.write_result(
            handle.staging,
            args.task_id,
            args.attempt,
            ok=args.error is None,
            session_ref=session_ref,
            context_ref=args.context_ref,
            parent_context_ref=args.parent_context_ref or session_ref,
            transcript_path=transcript,
            evidence_refs=args.evidence_ref or [],
            error=args.error,
            error_class=args.error_class,
        )
    except mailbox.MailboxAbandoned as exc:
        return {
            "kind": "ABANDONED",
            "result_path": str(exc.result_path),
            "transcript_path": transcript,
            "message": "the runner timed this attempt out before the result arrived; the "
                       "agent's work is discarded (kept only as late evidence of the "
                       "abandoned attempt). Do not retry it by hand: the runner decides.",
        }
    return {
        "kind": "RESULT_WRITTEN",
        "result_path": str(path),
        "ok": args.error is None,
        "transcript_path": transcript,
    }


def add_parsers(subparsers) -> None:
    run = subparsers.add_parser("run")
    run.add_argument("--run-id", required=True)
    run.add_argument("--executor", choices=("mailbox", "headless"), default="mailbox")
    run.add_argument("--claude-bin", help="headless only: claude CLI path "
                     "(default $AUTORESEARCH_CLAUDE_BIN, PATH, ~/.local/bin/claude)")
    run.add_argument("--max-parallel", type=int)
    run.add_argument("--poll-seconds", type=float, default=5.0)
    run.add_argument("--timeout-multiplier", type=float, default=1.0)
    run.add_argument("--max-rounds", type=int, default=20000)
    box = subparsers.add_parser("mailbox")
    commands = box.add_subparsers(dest="mailbox_command", required=True)
    wait = commands.add_parser("wait")
    wait.add_argument("--run-id", required=True)
    wait.add_argument("--timeout", type=float, default=90.0)   # < host Bash 120 s cap
    wait.add_argument("--include-taken", action="store_true")
    pending = commands.add_parser("pending")
    pending.add_argument("--run-id", required=True)
    complete = commands.add_parser("complete")
    complete.add_argument("--run-id", required=True)
    complete.add_argument("--task-id", required=True)
    complete.add_argument("--attempt", required=True, type=int)
    complete.add_argument("--context-ref", required=True)
    complete.add_argument("--session-ref")
    complete.add_argument("--parent-context-ref")
    complete.add_argument("--transcript-path")
    complete.add_argument("--evidence-ref", action="append")
    complete.add_argument("--error")
    complete.add_argument("--error-class")


__all__ = [
    "EXIT_NOT_FINISHED", "add_parsers", "mailbox_command", "resolve_max_parallel",
    "run_command",
]
