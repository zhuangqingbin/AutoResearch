#!/usr/bin/env python3
"""Run one argv vector while preserving its exact process evidence."""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import gzip
import json
import os
import re
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TextIO

from autoresearch.trace.atomic import atomic_write_json
from autoresearch.trace.capsule import require_active_run
from autoresearch.trace.capsule_models import RunHandle
from autoresearch.trace.events import append_event

_STAGE_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$", re.ASCII)
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$", re.ASCII)
_RECORDED_ENV_KEYS = (
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TZ",
    "PATH",
    "PYTHONPATH",
    "PYTHONHOME",
    "VIRTUAL_ENV",
)
_KNOWN_SECRET_KEYS = frozenset(
    {
        "ANTHROPIC_API_KEY",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "FRED_API_KEY",
        "OPENAI_API_KEY",
        "TUSHARE_TOKEN",
    }
)
_SECRET_MARKERS = ("TOKEN", "SECRET", "PASSWORD", "API_KEY", "PRIVATE_KEY")


@dataclass(frozen=True)
class CaptureResult:
    """Terminal child outcome plus the persisted invocation metadata."""

    exit_code: int | None
    invocation: dict


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _validate_identifier(name: str, value: object, *, optional: bool = False):
    if optional and value is None:
        return None
    if type(value) is not str:
        raise TypeError(f"{name} must be a string")
    pattern = _STAGE_RE if name == "stage" else _IDENTIFIER_RE
    if not pattern.fullmatch(value):
        raise ValueError(f"unsafe {name}: {value!r}")
    return value


def _validate_argv(argv: object) -> list[str]:
    if type(argv) not in (list, tuple):
        raise TypeError("argv must be an argument vector, not a shell string")
    if not argv:
        raise ValueError("argv must not be empty")
    result = []
    for index, item in enumerate(argv):
        if type(item) is not str:
            raise TypeError(f"argv[{index}] must be a string")
        if "\x00" in item:
            raise ValueError(f"argv[{index}] contains NUL")
        if index == 0 and not item:
            raise ValueError("argv[0] must not be empty")
        result.append(item)
    return result


def _validate_attempt(attempt: object) -> int:
    if type(attempt) is not int or attempt < 1:
        raise ValueError("attempt must be a positive integer")
    return attempt


def _require_current_active_handle(handle: object) -> RunHandle:
    if not isinstance(handle, RunHandle):
        raise TypeError("handle must be a RunHandle")
    current = require_active_run(handle.run_id)
    if current != handle:
        raise RuntimeError("RunHandle is stale or does not match current run identity")
    return current


def _real_directory(root: Path, relative: Path, *, create: bool) -> Path:
    """Walk a relative directory without following a symlink component."""
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"directory escapes capsule: {relative}")
    root_info = root.lstat()
    if stat.S_ISLNK(root_info.st_mode) or not stat.S_ISDIR(root_info.st_mode):
        raise ValueError(f"capsule root is not a real directory: {root}")
    root_resolved = root.resolve(strict=True)
    current = root
    for part in relative.parts:
        if part in ("", "."):
            continue
        candidate = current / part
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            if not create:
                raise
            with contextlib.suppress(FileExistsError):
                candidate.mkdir(mode=0o700)
            info = candidate.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise ValueError(f"destination contains a symlink: {candidate}")
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError(f"destination component is not a directory: {candidate}")
        try:
            candidate.resolve(strict=True).relative_to(root_resolved)
        except ValueError as exc:
            raise ValueError(f"destination escapes capsule: {candidate}") from exc
        current = candidate
    return current


def _reject_symlink_file(path: Path) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(info.st_mode):
        raise ValueError(f"evidence path is a symlink: {path}")
    if not stat.S_ISREG(info.st_mode):
        raise ValueError(f"evidence path is not a regular file: {path}")


def _fsync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _recorded_environment(environ: dict[str, str]) -> dict:
    recorded: dict[str, object] = {
        key: environ[key] for key in _RECORDED_ENV_KEYS if key in environ
    }
    secret_keys = set(_KNOWN_SECRET_KEYS)
    secret_keys.update(
        key for key in environ if any(marker in key.upper() for marker in _SECRET_MARKERS)
    )
    for key in sorted(secret_keys):
        recorded[key] = {"present": bool(environ.get(key))}
    return recorded


def _secret_values(environ: dict[str, str]) -> tuple[str, ...]:
    values = {
        value
        for key, value in environ.items()
        if value
        and (key in _KNOWN_SECRET_KEYS or any(marker in key.upper() for marker in _SECRET_MARKERS))
    }
    return tuple(sorted(values, key=len, reverse=True))


def _redact_text(value: str | None, secrets: tuple[str, ...]) -> str | None:
    if value is None:
        return None
    for secret in secrets:
        value = value.replace(secret, "[REDACTED]")
    return value


def _redact_error(error: dict, secrets: tuple[str, ...]) -> dict:
    result = dict(error)
    for field in ("summary", "message", "traceback"):
        result[field] = _redact_text(result[field], secrets)
    return result


def _redacted_errors(errors: list[dict], secrets: tuple[str, ...]) -> list[dict]:
    return [_redact_error(error, secrets) for error in errors]


def _exception_error(
    exc: BaseException,
    *,
    classification: str,
    category: str,
    phase: str,
    traceback_text: str | None = None,
) -> dict:
    """Describe a wrapper exception without discarding its Python traceback."""
    message = str(exc) or type(exc).__name__
    return {
        "classification": classification,
        "category": category,
        "summary": f"{type(exc).__name__}: {message}",
        "message": message,
        "exception_type": type(exc).__name__,
        "type": type(exc).__name__,
        "phase": phase,
        "traceback": traceback_text if traceback_text is not None else traceback.format_exc(),
    }


def _process_error(*, classification: str, exception_type: str, summary: str, phase: str) -> dict:
    return {
        "classification": classification,
        "category": "PROCESS",
        "summary": summary,
        "message": summary,
        "exception_type": exception_type,
        "type": exception_type,
        "phase": phase,
        "traceback": None,
    }


def _index_paths(handle: RunHandle) -> tuple[Path, Path]:
    events_dir = _real_directory(handle.capsule, Path("events"), create=False)
    index = events_dir / "invocations.json"
    lock = events_dir / "invocations.lock"
    _reject_symlink_file(index)
    _reject_symlink_file(lock)
    return index, lock


def _with_index_lock(handle: RunHandle, update) -> dict:
    index_path, lock_path = _index_paths(handle)
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(lock_path, flags, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        _reject_symlink_file(index_path)
        if index_path.exists():
            try:
                value = json.loads(index_path.read_text(encoding="utf-8"))
            except Exception as exc:
                raise RuntimeError(f"invalid invocation index: {exc}") from exc
            if type(value) is not dict:
                raise RuntimeError("invalid invocation index: root must be an object")
        else:
            value = {}
        result = update(value)
        atomic_write_json(index_path, value)
        return result
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _reserve_invocation(handle: RunHandle, invocation_id: str, metadata: dict) -> None:
    def reserve(index: dict):
        if invocation_id in index:
            raise ValueError(f"duplicate invocation_id: {invocation_id}")
        index[invocation_id] = metadata

    _with_index_lock(handle, reserve)


def _finish_invocation(handle: RunHandle, invocation_id: str, metadata: dict) -> dict:
    def finish(index: dict):
        if invocation_id not in index:
            raise RuntimeError(f"invocation reservation is missing: {invocation_id}")
        if index[invocation_id].get("status") != "STARTING":
            raise RuntimeError(f"invocation is already terminal: {invocation_id}")
        index[invocation_id] = metadata
        return metadata

    return _with_index_lock(handle, finish)


def _replace_invocation(handle: RunHandle, invocation_id: str, metadata: dict) -> dict:
    """Replace this invocation's terminal row after a later evidence failure."""

    def replace(index: dict):
        current = index.get(invocation_id)
        if current is None:
            raise RuntimeError(f"invocation reservation is missing: {invocation_id}")
        if current.get("run_id") != handle.run_id:
            raise RuntimeError(f"invocation identity does not match run: {invocation_id}")
        index[invocation_id] = metadata
        return metadata

    return _with_index_lock(handle, replace)


def _discard_reservation(handle: RunHandle, invocation_id: str) -> None:
    def discard(index: dict):
        if index.get(invocation_id, {}).get("status") == "STARTING":
            del index[invocation_id]

    _with_index_lock(handle, discard)


def _make_raw_temp(directory: Path, stream_name: str) -> tuple[int, Path]:
    fd, name = tempfile.mkstemp(prefix=f".{stream_name}.", suffix=".raw.tmp", dir=directory)
    os.chmod(name, 0o600)
    return fd, Path(name)


def _echo(stream: TextIO, block: bytes) -> None:
    target = getattr(stream, "buffer", None)
    if target is not None:
        target.write(block)
        target.flush()
        return
    stream.write(block.decode("utf-8", errors="replace"))
    stream.flush()


def _reader(pipe, raw_fd: int, parent_stream: TextIO, errors: list[dict], name: str):
    try:
        while True:
            block = pipe.read(64 * 1024)
            if not block:
                break
            view = memoryview(block)
            while view:
                written = os.write(raw_fd, view)
                if written == 0:
                    raise OSError(f"short write while capturing {name}")
                view = view[written:]
            # A closed parent stream must not stop evidence capture.
            with contextlib.suppress(Exception):
                _echo(parent_stream, block)
    except BaseException as exc:
        errors.append(
            _exception_error(
                exc,
                classification="STREAM_CAPTURE_FAILURE",
                category="CAPTURE",
                phase=f"{name}-reader",
            )
        )
    finally:
        with contextlib.suppress(Exception):
            pipe.close()


def _gzip_raw(raw_path: Path, destination: Path) -> None:
    _reject_symlink_file(destination)
    if destination.exists():
        raise FileExistsError(f"log evidence already exists: {destination}")
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as target:
            fd = -1
            with (
                gzip.GzipFile(
                    filename="", mode="wb", fileobj=target, compresslevel=9, mtime=0
                ) as archive,
                raw_path.open("rb") as source,
            ):
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    archive.write(block)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temp, destination)
        os.chmod(destination, 0o600)
        _fsync_directory(destination.parent)
    finally:
        if fd >= 0:
            os.close(fd)
        temp.unlink(missing_ok=True)


def _event_fields(
    handle: RunHandle,
    *,
    stage: str,
    invocation_id: str,
    attempt: int,
    subject: str | None,
    event_type: str,
    payload: dict,
) -> dict:
    return {
        "run_id": handle.run_id,
        "engine": handle.engine,
        "stage": stage,
        "invocation_id": invocation_id,
        "attempt": attempt,
        "subject": subject,
        "event_type": event_type,
        "payload": payload,
    }


def _terminate_child(process: subprocess.Popen) -> int:
    if process.poll() is None:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
    try:
        return process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        return process.wait()


def _terminal_event_exists(handle: RunHandle, invocation_id: str) -> bool:
    path = handle.capsule / "events/events.jsonl"
    for line in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row.get("invocation_id") == invocation_id and row.get("event_type") in {
            "COMMAND_COMPLETED",
            "COMMAND_FAILED",
        }:
            return True
    return False


def _capture_reserved(
    handle: RunHandle,
    *,
    stage: str,
    invocation_id: str,
    attempt: int,
    subject: str | None,
    command: list[str],
    child_env: dict[str, str],
    base: dict,
    stdout_fd: int,
    stderr_fd: int,
    stdout_raw: Path,
    stderr_raw: Path,
    stdout_path: Path,
    stderr_path: Path,
    stdout_ref: str,
    stderr_ref: str,
    started_monotonic: float,
) -> CaptureResult:
    """Run a reserved invocation; restore signals only after terminal evidence."""
    process: subprocess.Popen | None = None
    threads: list[threading.Thread] = []
    started_threads: list[threading.Thread] = []
    reader_errors: list[dict] = []
    capture_errors: list[dict] = []
    previous_handlers: dict[int, object] = {}
    signal_handling = {
        "installed": False,
        "reason": (
            "not-main-thread"
            if threading.current_thread() is not threading.main_thread()
            else "child-not-started"
        ),
    }
    signal_forwarded: list[int] = []
    exit_code: int | None = None
    pending_exception: BaseException | None = None
    secrets = _secret_values(child_env)

    def exception_error(
        exc: BaseException, *, classification: str, category: str, phase: str
    ) -> dict:
        return _redact_error(
            _exception_error(
                exc,
                classification=classification,
                category=category,
                phase=phase,
            ),
            secrets,
        )

    def build_metadata(errors: list[dict]) -> tuple[dict, int | None]:
        child_signal = -exit_code if exit_code is not None and exit_code < 0 else None
        observed_signal = child_signal or (signal_forwarded[-1] if signal_forwarded else None)
        failed = (
            exit_code != 0
            or observed_signal is not None
            or bool(errors)
            or pending_exception is not None
        )
        return (
            {
                **base,
                "status": "FAILED" if failed else "COMPLETED",
                "ended_at": _utc_now(),
                "duration_seconds": round(time.monotonic() - started_monotonic, 9),
                "exit_code": exit_code,
                "signal": observed_signal,
                "signal_handling": signal_handling,
                "forwarded_signals": signal_forwarded,
                "error": errors[0] if errors else None,
                "capture_errors": errors,
            },
            observed_signal,
        )

    def terminal_payload(metadata: dict) -> dict:
        return {
            "exit_code": metadata["exit_code"],
            "signal": metadata["signal"],
            "duration_seconds": metadata["duration_seconds"],
            "stdout_log": stdout_ref,
            "stderr_log": stderr_ref,
            "error": metadata["error"],
        }

    try:
        try:
            try:
                process = subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=child_env,
                    start_new_session=True,
                )
            except OSError as exc:
                signal_handling = {"installed": False, "reason": "spawn-failed"}
                capture_errors.append(
                    exception_error(
                        exc,
                        classification="SPAWN_FAILURE",
                        category="WRAPPER",
                        phase="spawn",
                    )
                )
            else:
                if threading.current_thread() is threading.main_thread():

                    def forward(signum, frame):
                        signal_forwarded.append(signum)
                        if process is not None and process.poll() is None:
                            with contextlib.suppress(ProcessLookupError):
                                os.killpg(process.pid, signum)

                    try:
                        for signum in (signal.SIGINT, signal.SIGTERM):
                            previous_handlers[signum] = signal.getsignal(signum)
                            signal.signal(signum, forward)
                    except (OSError, RuntimeError, ValueError) as exc:
                        for signum, previous in previous_handlers.items():
                            with contextlib.suppress(Exception):
                                signal.signal(signum, previous)
                        previous_handlers.clear()
                        signal_handling = {
                            "installed": False,
                            "reason": f"signal-api-unavailable: {type(exc).__name__}",
                        }
                    else:
                        signal_handling = {"installed": True, "reason": None}

                assert process.stdout is not None and process.stderr is not None
                threads = [
                    threading.Thread(
                        target=_reader,
                        args=(process.stdout, stdout_fd, sys.stdout, reader_errors, "stdout"),
                        daemon=False,
                    ),
                    threading.Thread(
                        target=_reader,
                        args=(process.stderr, stderr_fd, sys.stderr, reader_errors, "stderr"),
                        daemon=False,
                    ),
                ]
                try:
                    for thread in threads:
                        thread.start()
                        started_threads.append(thread)
                except Exception as exc:
                    capture_errors.append(
                        exception_error(
                            exc,
                            classification="READER_START_FAILURE",
                            category="CAPTURE",
                            phase="reader-start",
                        )
                    )
                    exit_code = _terminate_child(process)
                except BaseException as exc:
                    pending_exception = exc
                    capture_errors.append(
                        exception_error(
                            exc,
                            classification="READER_START_FAILURE",
                            category="CAPTURE",
                            phase="reader-start",
                        )
                    )
                    exit_code = _terminate_child(process)
                else:
                    try:
                        exit_code = process.wait()
                    except BaseException as exc:
                        pending_exception = exc
                        capture_errors.append(
                            exception_error(
                                exc,
                                classification="WRAPPER_EXCEPTION",
                                category="WRAPPER",
                                phase="wait",
                            )
                        )
                        exit_code = _terminate_child(process)
        finally:
            for thread in started_threads:
                try:
                    thread.join()
                except BaseException as exc:
                    if pending_exception is None:
                        pending_exception = exc
                    capture_errors.append(
                        exception_error(
                            exc,
                            classification="STREAM_CAPTURE_FAILURE",
                            category="CAPTURE",
                            phase="reader-drain",
                        )
                    )
            if process is not None:
                for name, pipe in (("stdout", process.stdout), ("stderr", process.stderr)):
                    if pipe is not None:
                        try:
                            pipe.close()
                        except Exception as exc:
                            capture_errors.append(
                                exception_error(
                                    exc,
                                    classification="STREAM_CAPTURE_FAILURE",
                                    category="CAPTURE",
                                    phase=f"{name}-close",
                                )
                            )
            for name, fd in (("stdout", stdout_fd), ("stderr", stderr_fd)):
                try:
                    os.fsync(fd)
                except OSError as exc:
                    capture_errors.append(
                        exception_error(
                            exc,
                            classification="LOG_FLUSH_FAILURE",
                            category="CAPTURE",
                            phase=f"{name}-flush",
                        )
                    )
                try:
                    os.close(fd)
                except OSError as exc:
                    capture_errors.append(
                        exception_error(
                            exc,
                            classification="LOG_CLOSE_FAILURE",
                            category="CAPTURE",
                            phase=f"{name}-close",
                        )
                    )

        for name, raw_path, destination in (
            ("stdout", stdout_raw, stdout_path),
            ("stderr", stderr_raw, stderr_path),
        ):
            try:
                _gzip_raw(raw_path, destination)
            except BaseException as exc:
                capture_errors.append(
                    exception_error(
                        exc,
                        classification="LOG_COMPRESSION_FAILURE",
                        category="CAPTURE",
                        phase=f"{name}-gzip",
                    )
                )
            try:
                raw_path.unlink(missing_ok=True)
            except BaseException as exc:
                capture_errors.append(
                    exception_error(
                        exc,
                        classification="LOG_CLEANUP_FAILURE",
                        category="CAPTURE",
                        phase=f"{name}-cleanup",
                    )
                )

        errors = [*_redacted_errors(reader_errors, secrets), *capture_errors]
        child_signal = -exit_code if exit_code is not None and exit_code < 0 else None
        observed_signal = child_signal or (signal_forwarded[-1] if signal_forwarded else None)
        if observed_signal is not None:
            errors.append(
                _process_error(
                    classification="PROCESS_SIGNAL",
                    exception_type="ProcessSignal",
                    summary=f"child terminated after signal {observed_signal}",
                    phase="signal",
                )
            )
        elif exit_code is not None and exit_code != 0:
            errors.append(
                _process_error(
                    classification="PROCESS_NONZERO_EXIT",
                    exception_type="ProcessExit",
                    summary=f"child exited with code {exit_code}",
                    phase="wait",
                )
            )

        metadata, _ = build_metadata(errors)
        try:
            persisted = _finish_invocation(handle, invocation_id, metadata)
        except BaseException as exc:
            errors.append(
                exception_error(
                    exc,
                    classification="INDEX_WRITE_FAILURE",
                    category="PERSISTENCE",
                    phase="invocation-index",
                )
            )
            metadata, _ = build_metadata(errors)
            persisted = _replace_invocation(handle, invocation_id, metadata)

        event_type = "COMMAND_FAILED" if metadata["status"] == "FAILED" else "COMMAND_COMPLETED"
        event_fields = _event_fields(
            handle,
            stage=stage,
            invocation_id=invocation_id,
            attempt=attempt,
            subject=subject,
            event_type=event_type,
            payload=terminal_payload(metadata),
        )
        try:
            append_event(handle.capsule / "events/events.jsonl", **event_fields)
        except BaseException as exc:
            if not _terminal_event_exists(handle, invocation_id):
                errors.append(
                    exception_error(
                        exc,
                        classification="EVENT_WRITE_FAILURE",
                        category="PERSISTENCE",
                        phase="terminal-event",
                    )
                )
                metadata, _ = build_metadata(errors)
                persisted = _replace_invocation(handle, invocation_id, metadata)
                append_event(
                    handle.capsule / "events/events.jsonl",
                    **_event_fields(
                        handle,
                        stage=stage,
                        invocation_id=invocation_id,
                        attempt=attempt,
                        subject=subject,
                        event_type="COMMAND_FAILED",
                        payload=terminal_payload(metadata),
                    ),
                )

        if pending_exception is not None:
            raise pending_exception
        return CaptureResult(exit_code=exit_code, invocation=persisted)
    finally:
        restoration_errors = []
        for signum, previous in previous_handlers.items():
            try:
                signal.signal(signum, previous)
            except Exception as exc:
                restoration_errors.append(exc)
        if restoration_errors and sys.exc_info()[0] is None:
            raise restoration_errors[0]


def run_captured(
    handle: RunHandle,
    stage: str,
    argv,
    invocation_id: str,
    attempt: int = 1,
    subject: str | None = None,
) -> CaptureResult:
    """Execute exactly one argument vector and persist its streamed evidence."""
    stage = _validate_identifier("stage", stage)
    invocation_id = _validate_identifier("invocation_id", invocation_id)
    subject = _validate_identifier("subject", subject, optional=True)
    attempt = _validate_attempt(attempt)
    command = _validate_argv(argv)
    handle = _require_current_active_handle(handle)

    log_dir = _real_directory(handle.capsule, Path("logs") / stage, create=True)
    stdout_path = log_dir / f"{invocation_id}.stdout.log.gz"
    stderr_path = log_dir / f"{invocation_id}.stderr.log.gz"
    _reject_symlink_file(stdout_path)
    _reject_symlink_file(stderr_path)

    child_env = dict(os.environ)
    child_env.update(
        {
            "AUTORESEARCH_ENGINE": handle.engine,
            "AUTORESEARCH_RUN_ID": handle.run_id,
            "AUTORESEARCH_STAGE": stage,
            "AUTORESEARCH_INVOCATION_ID": invocation_id,
        }
    )
    started_at = _utc_now()
    started_monotonic = time.monotonic()
    stdout_ref = stdout_path.relative_to(handle.capsule).as_posix()
    stderr_ref = stderr_path.relative_to(handle.capsule).as_posix()
    environment = _recorded_environment(child_env)
    stdout_fd = -1
    stderr_fd = -1
    stdout_raw: Path | None = None
    stderr_raw: Path | None = None
    try:
        stdout_fd, stdout_raw = _make_raw_temp(log_dir, f"{invocation_id}.stdout")
        stderr_fd, stderr_raw = _make_raw_temp(log_dir, f"{invocation_id}.stderr")
    except BaseException:
        for fd in (stdout_fd, stderr_fd):
            with contextlib.suppress(OSError):
                os.close(fd)
        for path in (stdout_raw, stderr_raw):
            if path is not None:
                path.unlink(missing_ok=True)
        raise

    base = {
        "run_id": handle.run_id,
        "engine": handle.engine,
        "stage": stage,
        "invocation_id": invocation_id,
        "attempt": attempt,
        "subject": subject,
        "argv": command,
        "cwd": os.getcwd(),
        "started_at": started_at,
        "environment": environment,
        "stdout_log": stdout_ref,
        "stderr_log": stderr_ref,
        "status": "STARTING",
    }
    reserved = False
    try:
        _reserve_invocation(handle, invocation_id, dict(base))
        reserved = True
        append_event(
            handle.capsule / "events/events.jsonl",
            **_event_fields(
                handle,
                stage=stage,
                invocation_id=invocation_id,
                attempt=attempt,
                subject=subject,
                event_type="COMMAND_STARTED",
                payload={
                    "argv": command,
                    "cwd": base["cwd"],
                    "environment": environment,
                    "stdout_log": stdout_ref,
                    "stderr_log": stderr_ref,
                },
            ),
        )
    except BaseException:
        if reserved:
            with contextlib.suppress(Exception):
                _discard_reservation(handle, invocation_id)
        for fd in (stdout_fd, stderr_fd):
            with contextlib.suppress(OSError):
                os.close(fd)
        stdout_raw.unlink(missing_ok=True)
        stderr_raw.unlink(missing_ok=True)
        raise

    return _capture_reserved(
        handle,
        stage=stage,
        invocation_id=invocation_id,
        attempt=attempt,
        subject=subject,
        command=command,
        child_env=child_env,
        base=base,
        stdout_fd=stdout_fd,
        stderr_fd=stderr_fd,
        stdout_raw=stdout_raw,
        stderr_raw=stderr_raw,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        stdout_ref=stdout_ref,
        stderr_ref=stderr_ref,
        started_monotonic=started_monotonic,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m autoresearch.trace.exec_capture",
        description="capture one exact argv vector into an active run capsule",
    )
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--stage", required=True)
    parser.add_argument("--invocation-id", required=True)
    parser.add_argument("--attempt", type=int, default=1)
    parser.add_argument("--subject")
    return parser


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    parser = _parser()
    option_end = raw.index("--") if "--" in raw else len(raw)
    option_argv = raw[:option_end]
    if "-h" in option_argv or "--help" in option_argv:
        parser.parse_args(option_argv)
    if "--" not in raw:
        parser.error("missing required '--' before child argv")
    separator = option_end
    options = parser.parse_args(option_argv)
    command = raw[separator + 1 :]
    if not command:
        parser.error("child argv after '--' must not be empty")
    try:
        handle = require_active_run(options.run_id)
        result = run_captured(
            handle,
            stage=options.stage,
            argv=command,
            invocation_id=options.invocation_id,
            attempt=options.attempt,
            subject=options.subject,
        )
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        print(f"[exec-capture] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 125
    if result.exit_code is None:
        error = result.invocation.get("error") or {}
        detail = error.get("message", "child was not spawned")
        print(f"[exec-capture] spawn failed: {detail}", file=sys.stderr)
        return 127
    if result.invocation.get("signal"):
        return 128 + int(result.invocation["signal"])
    if result.exit_code < 0:
        return 128 + (-result.exit_code)
    if result.invocation.get("capture_errors") and result.exit_code == 0:
        print("[exec-capture] evidence capture failed", file=sys.stderr)
        return 125
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
