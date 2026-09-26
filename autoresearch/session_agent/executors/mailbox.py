"""Host-mode executor: request/result files that an interactive session answers.

The runner writes a request, the interactive session (Claude Code or Codex) reads it with
``session_agent mailbox wait``, dispatches the named project agent with the given prompt,
and answers with ``session_agent mailbox complete``.  Files, all under
``<staging>/_dispatch/`` (registered in ``contracts/artifacts.py``):

========================================  =========  =====================================
``<task_id>.a<attempt>.request.json``     runner     ``DispatchRequest.to_json()`` + ``issued_at``
``<task_id>.a<attempt>.taken``            host       created exclusively by ``wait``
``<task_id>.a<attempt>.result.json``      host       ``RESULT_FIELDS``
``runner.json``                           runner     RUNNING / EXITED (+ outcome)
``ledger.jsonl``                          runner     one line per settled attempt
========================================  =========  =====================================

Rules: every write is tmp + ``os.replace``; request and result files are write-once under
an ``fcntl`` lock (a second ``complete`` is a conflict, never an overwrite).  The runner
reads only the result file of the exact attempt it issued and only if the body names the
same task/attempt, so a late result of a timed-out attempt can never be accepted.
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


class MailboxConflict(RuntimeError):
    """A write-once mailbox file already exists with other content."""


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
    """Answer one issued request (write-once)."""
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
    return path


def read_result(staging, task_id: str, attempt: int) -> dict | None:
    """The result of exactly this attempt, or ``None`` (absent, torn, or misaddressed)."""
    value = _read_json(result_path(staging, task_id, attempt))
    if value is None or value.get("task_id") != task_id or value.get("attempt") != attempt:
        return None
    return value


def _runner_state(staging) -> dict | None:
    return _read_json(mailbox_dir(staging) / "runner.json")


def pending_requests(staging, *, include_taken: bool = False) -> list[dict]:
    """Issued requests without a result, oldest first (``taken`` ones only on request)."""
    root = mailbox_dir(staging)
    rows = []
    for path in sorted(root.glob("*.request.json"), key=lambda item: item.stat().st_mtime_ns):
        stem = path.name.removesuffix(".request.json")
        if (root / f"{stem}.result.json").exists():
            continue
        if not include_taken and (root / f"{stem}.taken").exists():
            continue
        doc = _read_json(path)
        if doc is not None:
            rows.append({**doc, "request_path": str(path)})
    return rows


def _take(staging, doc: dict) -> bool:
    path = _taken_path(staging, doc["task_id"], doc["attempt"])
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(json.dumps({"taken_at": _now(), "pid": os.getpid()}))
    return True


def wait_request(
    staging,
    *,
    timeout: float = 540.0,
    poll_seconds: float = 1.0,
    include_taken: bool = False,
    clock=time.monotonic,
    sleep=time.sleep,
) -> dict:
    """Host side: block until one request is handed out, the runner exits, or timeout.

    Returns ``{"kind": "REQUEST", **request, "request_path"}`` (each request is handed out
    once unless ``include_taken``), ``{"kind": "RUNNER_EXITED", "runner": ...}`` when the
    runner has exited and nothing is pending, or ``{"kind": "IDLE", ...}`` on timeout.
    """
    deadline = clock() + float(timeout)
    while True:
        for doc in pending_requests(staging, include_taken=include_taken):
            if include_taken or _take(staging, doc):
                return {"kind": "REQUEST", **doc}
        runner = _runner_state(staging)
        exited = bool(runner) and runner.get("state") == "EXITED"
        if exited and not pending_requests(staging, include_taken=True):
            return {"kind": "RUNNER_EXITED", "runner": runner}
        if clock() >= deadline:
            return {
                "kind": "IDLE",
                "waited_seconds": float(timeout),
                "runner_state": (runner or {}).get("state"),
                "taken_unanswered": [
                    Path(item["request_path"]).name.removesuffix(".request.json")
                    for item in pending_requests(staging, include_taken=True)
                ],
            }
        sleep(poll_seconds)


class MailboxExecutor:
    """Runner side of host mode.  Re-attaches to an already issued attempt."""

    name = "mailbox"
    supports_reattach = True

    def __init__(self, staging, *, poll_seconds: float = 2.0, clock=time.monotonic,
                 sleep=time.sleep):
        self.staging = Path(staging)
        self.poll_seconds = poll_seconds
        self._clock = clock
        self._sleep = sleep

    def dispatch(self, request: DispatchRequest) -> DispatchResult:
        issue_request(self.staging, request)
        path = result_path(self.staging, request.task_id, request.attempt)
        deadline = self._clock() + float(request.timeout_seconds)
        while True:
            doc = read_result(self.staging, request.task_id, request.attempt)
            if doc is not None:
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
            if self._clock() >= deadline:
                raise ExecutorTimeout(
                    f"等待 {request.task_id} 的结果文件超时({request.timeout_seconds:.0f}s):{path}"
                )
            self._sleep(self.poll_seconds)


__all__ = [
    "DISPATCH_DIR", "MailboxConflict", "MailboxExecutor", "RESULT_FIELDS", "issue_request",
    "mailbox_dir", "pending_requests", "read_result", "request_path", "result_path",
    "wait_request", "write_result",
]
