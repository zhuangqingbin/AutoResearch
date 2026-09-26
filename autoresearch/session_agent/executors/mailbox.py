"""Host-mode executor: request/result files that an interactive session answers.

The runner writes a request, the interactive session (Claude Code or Codex) reads it with
``session_agent mailbox wait``, dispatches the named project agent with the given prompt,
and answers with ``session_agent mailbox complete``.  Files, all under
``<staging>/_dispatch/`` (registered in ``contracts/artifacts.py``):

========================================  =========  =====================================
``<task_id>.a<attempt>.request.json``     runner     ``DispatchRequest.to_json()`` + ``issued_at``
``<task_id>.a<attempt>.taken``            host       created exclusively by ``wait``
``<task_id>.a<attempt>.result.json``      host       ``RESULT_FIELDS``
``<task_id>.a<attempt>.abandoned``        runner     timed out: never handed out / accepted
``runner.json``                           runner     RUNNING / EXITED (+ outcome)
``ledger.jsonl``                          runner     one line per settled attempt
========================================  =========  =====================================

Rules: every write is tmp + ``os.replace``; request and result files are write-once under
an ``fcntl`` lock (a second ``complete`` is a conflict, never an overwrite).  The runner
reads only the result file of the exact attempt it issued and only if the body names the
same task/attempt, so a late result of a timed-out attempt can never be accepted.

Timing (review I1, 2026-09-26): the per-role budget ``request.timeout_seconds`` starts
when the host *takes* the request (``.taken``), not when the runner issues it — a request
queued behind other host work is not failed for waiting.  A request nobody takes times
out after ``NEVER_TAKEN_FACTOR`` × its budget.  The timeout decision and the
``.abandoned`` marker are one step under the mailbox lock, so a result either lands
before it (and is accepted) or after it (and is refused with :class:`MailboxAbandoned`,
kept only as late evidence of that attempt — see :func:`read_late_result`).
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import time
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

from autoresearch.session_agent.executors.base import (
    DISPATCH_DIR,
    DispatchRequest,
    DispatchResult,
    ExecutorTimeout,
)

RESULT_FIELDS = (
    "schema_version", "run_id", "task_id", "attempt", "ok", "session_ref", "context_ref",
    "parent_context_ref", "transcript_path", "evidence_refs", "usage", "error",
    "error_class", "written_at",
)
#: A request nobody takes times out after this many role budgets (from issue).
NEVER_TAKEN_FACTOR = 4.0
#: ``mailbox wait`` default: stays under the host shell's 120 s Bash timeout.
DEFAULT_WAIT_SECONDS = 90.0


class MailboxConflict(RuntimeError):
    """A write-once mailbox file already exists with other content."""


class MailboxAbandoned(RuntimeError):
    """The attempt timed out and was abandoned; its late result is evidence only."""

    def __init__(self, message: str, *, result_path: Path | None = None):
        super().__init__(message)
        self.result_path = result_path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def mailbox_dir(staging: Path | str) -> Path:
    path = Path(staging) / DISPATCH_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def _stem(task_id: str, attempt: int) -> str:
    if not task_id or "/" in task_id or task_id.startswith("."):
        raise ValueError(f"invalid task_id for the mailbox: {task_id!r}")
    if type(attempt) is not int or attempt < 1:
        raise ValueError("mailbox attempt must be a positive integer")
    return f"{task_id}.a{attempt}"


def request_path(staging, task_id: str, attempt: int) -> Path:
    return mailbox_dir(staging) / f"{_stem(task_id, attempt)}.request.json"


def result_path(staging, task_id: str, attempt: int) -> Path:
    return mailbox_dir(staging) / f"{_stem(task_id, attempt)}.result.json"


def _taken_path(staging, task_id: str, attempt: int) -> Path:
    return mailbox_dir(staging) / f"{_stem(task_id, attempt)}.taken"


def abandoned_path(staging, task_id: str, attempt: int) -> Path:
    return mailbox_dir(staging) / f"{_stem(task_id, attempt)}.abandoned"


@contextlib.contextmanager
def _locked(staging) -> Iterator[None]:
    with (mailbox_dir(staging) / ".mailbox.lock").open("a+", encoding="utf-8") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _atomic_write(path: Path, payload: dict) -> None:
    temp = path.with_name(f".{path.name}.{os.getpid()}.{time.monotonic_ns()}.tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True),
                    encoding="utf-8")
    os.replace(temp, path)


def _read_json(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def issue_request(staging, request: DispatchRequest) -> Path:
    """Write the request once; an existing request for the attempt is left untouched."""
    path = request_path(staging, request.task_id, request.attempt)
    with _locked(staging):
        if not path.exists():
            _atomic_write(path, {**request.to_json(), "issued_at": _now()})
    return path


def is_abandoned(staging, task_id: str, attempt: int) -> bool:
    return abandoned_path(staging, task_id, attempt).exists()


def abandon_request(staging, task_id: str, attempt: int, *, reason: str) -> bool:
    """Mark an unanswered attempt abandoned (runner timeout).  ``False`` = a result won."""
    marker = abandoned_path(staging, task_id, attempt)
    with _locked(staging):
        if read_result(staging, task_id, attempt) is not None:
            return False                   # a valid answer won; a torn/misaddressed one does not
        if not marker.exists():
            _atomic_write(marker, {
                "schema_version": 1, "task_id": task_id, "attempt": attempt,
                "abandoned_at": _now(), "reason": str(reason)[:2000], "pid": os.getpid(),
            })
    return True


def read_abandonment(staging, task_id: str, attempt: int) -> dict | None:
    """The ``.abandoned`` record (reason = the timeout event), or ``None``."""
    return _read_json(abandoned_path(staging, task_id, attempt))


def write_result(
    staging,
    task_id: str,
    attempt: int,
    *,
    ok: bool,
    session_ref: str | None = None,
    context_ref: str | None = None,
    parent_context_ref: str | None = None,
    transcript_path: str | None = None,
    evidence_refs: list[str] | tuple[str, ...] = (),
    usage: dict | None = None,
    error: str | None = None,
    error_class: str | None = None,
) -> Path:
    """Answer one issued request (write-once).

    Raises :class:`MailboxAbandoned` when the runner already abandoned the attempt: the
    result is still stored (write-once) so its transcript can be bound as evidence of
    the abandoned attempt, but the work itself is discarded.
    """
    issued = _read_json(request_path(staging, task_id, attempt))
    if issued is None:
        raise ValueError(f"no issued request for {_stem(task_id, attempt)}")
    path = result_path(staging, task_id, attempt)
    payload = {
        "schema_version": 1,
        "run_id": issued.get("run_id"),
        "task_id": task_id,
        "attempt": attempt,
        "ok": bool(ok),
        "session_ref": session_ref,
        "context_ref": context_ref,
        "parent_context_ref": parent_context_ref,
        "transcript_path": transcript_path,
        "evidence_refs": list(evidence_refs or ()),
        "usage": usage,
        "error": error,
        "error_class": error_class,
        "written_at": _now(),
    }
    with _locked(staging):
        if path.exists():
            raise MailboxConflict(f"result already written: {path}")
        _atomic_write(path, payload)
        abandoned = is_abandoned(staging, task_id, attempt)
    if abandoned:
        raise MailboxAbandoned(
            f"ABANDONED: {_stem(task_id, attempt)} timed out before this result arrived; "
            "the runner discarded this attempt (a new attempt replaces it). The result is "
            f"kept only as evidence of the abandoned attempt: {path}",
            result_path=path,
        )
    return path


def read_result(staging, task_id: str, attempt: int) -> dict | None:
    """The result of exactly this attempt, or ``None`` (absent, torn, or misaddressed)."""
    value = _read_json(result_path(staging, task_id, attempt))
    if value is None or value.get("task_id") != task_id or value.get("attempt") != attempt:
        return None
    return value


def read_late_result(staging, task_id: str, attempt: int) -> dict | None:
    """A result that arrived for an *abandoned* attempt (evidence only, never accepted)."""
    if not is_abandoned(staging, task_id, attempt):
        return None
    return read_result(staging, task_id, attempt)


def _runner_state(staging) -> dict | None:
    return _read_json(mailbox_dir(staging) / "runner.json")


def pending_requests(staging, *, include_taken: bool = False) -> list[dict]:
    """Issued, unanswered, not abandoned requests, oldest first (``taken`` on request)."""
    root = mailbox_dir(staging)
    rows = []
    for path in sorted(root.glob("*.request.json"), key=lambda item: item.stat().st_mtime_ns):
        stem = path.name.removesuffix(".request.json")
        if (root / f"{stem}.result.json").exists() or (root / f"{stem}.abandoned").exists():
            continue
        if not include_taken and (root / f"{stem}.taken").exists():
            continue
        doc = _read_json(path)
        if doc is not None:
            rows.append({**doc, "request_path": str(path)})
    return rows


def _unanswered(staging) -> list[str]:
    return [
        Path(item["request_path"]).name.removesuffix(".request.json")
        for item in pending_requests(staging, include_taken=True)
    ]


def _take(staging, doc: dict, *, wall=time.time) -> bool:
    path = _taken_path(staging, doc["task_id"], doc["attempt"])
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(json.dumps({"taken_at": _now(), "taken_at_epoch": float(wall()),
                                 "pid": os.getpid()}))
    return True


def taken_at_epoch(staging, task_id: str, attempt: int) -> float | None:
    """When the host took the request (epoch seconds), ``None`` while untaken."""
    path = _taken_path(staging, task_id, attempt)
    if not path.exists():
        return None
    doc = _read_json(path) or {}
    value = doc.get("taken_at_epoch")
    if isinstance(value, (int, float)):
        return float(value)
    try:
        stamp = datetime.fromisoformat(str(doc.get("taken_at")).replace("Z", "+00:00"))
    except ValueError:
        return path.stat().st_mtime        # torn/legacy marker: its own mtime
    return stamp.timestamp()


def wait_request(
    staging,
    *,
    timeout: float = DEFAULT_WAIT_SECONDS,
    poll_seconds: float = 1.0,
    include_taken: bool = False,
    clock=time.monotonic,
    sleep=time.sleep,
    wall=time.time,
) -> dict:
    """Host side: block until one request is handed out, the runner exits, or timeout.

    Returns ``{"kind": "RUNNER_EXITED", "runner", "unanswered"}`` as soon as the runner
    has exited (nothing it issued can be accepted any more), ``{"kind": "REQUEST",
    **request, "request_path"}`` (each request is handed out once unless
    ``include_taken``; abandoned attempts never), or ``{"kind": "IDLE", ...}`` on timeout.
    """
    deadline = clock() + float(timeout)
    while True:
        runner = _runner_state(staging)
        if runner and runner.get("state") == "EXITED":
            return {"kind": "RUNNER_EXITED", "runner": runner,
                    "unanswered": _unanswered(staging)}
        for doc in pending_requests(staging, include_taken=include_taken):
            if include_taken or _take(staging, doc, wall=wall):
                return {"kind": "REQUEST", **doc}
        if clock() >= deadline:
            return {
                "kind": "IDLE",
                "waited_seconds": float(timeout),
                "runner_state": (runner or {}).get("state"),
                "taken_unanswered": _unanswered(staging),
            }
        sleep(poll_seconds)


class MailboxExecutor:
    """Runner side of host mode.  Re-attaches to an already issued attempt."""

    name = "mailbox"
    supports_reattach = True

    def __init__(self, staging, *, poll_seconds: float = 2.0, clock=time.monotonic,
                 sleep=time.sleep, wall=time.time,
                 never_taken_factor: float = NEVER_TAKEN_FACTOR):
        self.staging = Path(staging)
        self.poll_seconds = poll_seconds
        self.never_taken_factor = never_taken_factor
        self._clock = clock
        self._sleep = sleep
        self._wall = wall

    def _result(self, doc: dict) -> DispatchResult:
        return DispatchResult(
            ok=bool(doc.get("ok")),
            session_ref=doc.get("session_ref"),
            context_ref=doc.get("context_ref"),
            parent_context_ref=doc.get("parent_context_ref"),
            transcript_path=doc.get("transcript_path"),
            evidence_refs=tuple(doc.get("evidence_refs") or ()),
            usage=doc.get("usage"),
            error=doc.get("error"),
            error_class=doc.get("error_class"),
        )

    def dispatch(self, request: DispatchRequest) -> DispatchResult:
        issue_request(self.staging, request)
        task_id, attempt = request.task_id, request.attempt
        path = result_path(self.staging, task_id, attempt)
        if is_abandoned(self.staging, task_id, attempt):
            raise ExecutorTimeout(f"{task_id} a{attempt} 已被放弃(abandoned),不再等待:{path}")
        budget = float(request.timeout_seconds)
        issued = self._clock()
        while True:
            doc = read_result(self.staging, task_id, attempt)
            if doc is not None:
                return self._result(doc)
            taken = taken_at_epoch(self.staging, task_id, attempt)
            if taken is not None:
                expired = self._wall() >= taken + budget
                why = f"taken 后 {budget:.0f}s 无结果"
            else:
                expired = self._clock() - issued >= budget * self.never_taken_factor
                why = f"never taken({budget * self.never_taken_factor:.0f}s 无人领取)"
            if expired:
                message = f"等待 {task_id} 的结果文件超时({why}):{path}"
                if abandon_request(self.staging, task_id, attempt, reason=f"TIMEOUT: {message}"):
                    raise ExecutorTimeout(message)
                continue                           # the result landed first: accept it
            self._sleep(self.poll_seconds)


__all__ = [
    "DEFAULT_WAIT_SECONDS", "DISPATCH_DIR", "MailboxAbandoned", "MailboxConflict",
    "MailboxExecutor", "NEVER_TAKEN_FACTOR", "RESULT_FIELDS", "abandon_request",
    "abandoned_path", "is_abandoned", "issue_request", "mailbox_dir", "pending_requests",
    "read_abandonment", "read_late_result", "read_result", "request_path", "result_path",
    "taken_at_epoch", "wait_request", "write_result",
]
