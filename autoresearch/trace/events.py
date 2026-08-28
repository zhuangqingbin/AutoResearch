"""Append-only, hash-chained execution facts for a forensic run capsule."""
from __future__ import annotations

import fcntl
import json
import math
import os
import re
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from autoresearch.common import workspace as ws
from autoresearch.trace.atomic import canonical_json, sha256_bytes

EVENT_SCHEMA_VERSION = 1
GENESIS_HASH = "0" * 64
MAX_JSON_DEPTH = 64

_RESERVED_FIELDS = frozenset(
    {"schema_version", "seq", "ts", "prev_hash", "event_hash"}
)
_SEMANTIC_FIELDS = frozenset(
    {
        "run_id",
        "engine",
        "stage",
        "invocation_id",
        "attempt",
        "subject",
        "event_type",
        "payload",
    }
)
_EVENT_FIELDS = _RESERVED_FIELDS | _SEMANTIC_FIELDS
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_UTC_TIMESTAMP_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z$"
)


class _DuplicateKeyError(ValueError):
    pass


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _event_hash(event: dict) -> str:
    payload = {key: value for key, value in event.items() if key != "event_hash"}
    return sha256_bytes(canonical_json(payload).encode("utf-8"))


def _require_nonempty_string(field: str, value: object) -> None:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    _validate_utf8(value, path=field)


def _validate_utf8(value: str, *, path: str) -> None:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{path} contains invalid Unicode") from exc


def _validate_json_value(value: object, *, path: str) -> None:
    active_containers: set[int] = set()
    stack = [("visit", value, path, 0)]
    while stack:
        action, current, current_path, depth = stack.pop()
        if action == "leave":
            active_containers.remove(id(current))
            continue
        if current is None or type(current) in (bool, int):
            continue
        if type(current) is str:
            _validate_utf8(current, path=current_path)
            continue
        if type(current) is float:
            if not math.isfinite(current):
                raise ValueError(f"{current_path} contains a non-finite float")
            continue
        if type(current) not in (list, dict):
            raise TypeError(
                f"{current_path} contains a non-JSON value of type "
                f"{type(current).__name__}"
            )

        identity = id(current)
        if identity in active_containers:
            raise ValueError(f"{current_path} contains a cyclic JSON value")
        if depth >= MAX_JSON_DEPTH:
            raise ValueError(
                f"{current_path} nesting exceeds maximum depth {MAX_JSON_DEPTH}"
            )
        active_containers.add(identity)
        stack.append(("leave", current, current_path, depth))
        if type(current) is list:
            for index in range(len(current) - 1, -1, -1):
                stack.append(
                    ("visit", current[index], f"{current_path}[{index}]", depth + 1)
                )
            continue
        for key, item in reversed(tuple(current.items())):
            if type(key) is not str:
                raise TypeError(f"{current_path} contains a non-string object key")
            _validate_utf8(key, path=f"{current_path} key")
            stack.append(("visit", item, f"{current_path}.{key}", depth + 1))


def _validate_semantic_fields(fields: dict) -> dict:
    reserved = sorted(_RESERVED_FIELDS.intersection(fields))
    if reserved:
        raise ValueError(f"reserved event field(s): {', '.join(reserved)}")
    missing = sorted(_SEMANTIC_FIELDS.difference(fields))
    unexpected = sorted(set(fields).difference(_SEMANTIC_FIELDS))
    if missing:
        raise ValueError(f"missing event field(s): {', '.join(missing)}")
    if unexpected:
        raise ValueError(f"unexpected event field(s): {', '.join(unexpected)}")

    try:
        ws.validate_run_id(fields["run_id"])
    except ValueError as exc:
        raise ValueError(f"invalid run_id: {fields['run_id']!r}") from exc
    engine = fields["engine"]
    if type(engine) is not str or engine not in ws.ENGINES:
        raise ValueError(f"engine must be one of {ws.ENGINES!r}")
    for field in ("stage", "invocation_id", "event_type"):
        _require_nonempty_string(field, fields[field])
    attempt = fields["attempt"]
    if type(attempt) is not int or attempt < 1:
        raise ValueError("attempt must be a positive integer")
    subject = fields["subject"]
    if subject is not None:
        _require_nonempty_string("subject", subject)
    payload = fields["payload"]
    if type(payload) is not dict:
        raise TypeError("payload must be a JSON object")
    _validate_json_value(payload, path="payload")

    normalized = dict(fields)
    normalized["payload"] = json.loads(canonical_json(payload))
    return normalized


def _validate_timestamp(value: object) -> None:
    if type(value) is not str or not _UTC_TIMESTAMP_RE.fullmatch(value):
        raise ValueError("ts must be a UTC timestamp with six fractional digits")
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ")
    except ValueError as exc:
        raise ValueError(f"ts is not a valid UTC timestamp: {value!r}") from exc


def _validate_event(
    event: object,
    *,
    expected_seq: int,
    expected_prev_hash: str,
    expected_run_id: str | None,
    expected_engine: str | None,
) -> dict:
    if type(event) is not dict:
        raise ValueError("event must be an object")
    missing = sorted(_EVENT_FIELDS.difference(event))
    unexpected = sorted(set(event).difference(_EVENT_FIELDS))
    if missing or unexpected:
        details = []
        if missing:
            details.append(f"missing={','.join(missing)}")
        if unexpected:
            details.append(f"unexpected={','.join(unexpected)}")
        raise ValueError("event fields mismatch: " + "; ".join(details))

    schema_version = event["schema_version"]
    if type(schema_version) is not int or schema_version != EVENT_SCHEMA_VERSION:
        raise ValueError(
            f"schema_version must be exactly {EVENT_SCHEMA_VERSION}, got {schema_version!r}"
        )
    seq = event["seq"]
    if type(seq) is not int or seq != expected_seq:
        raise ValueError(f"seq must be {expected_seq}, got {seq!r}")
    _validate_timestamp(event["ts"])
    semantic = _validate_semantic_fields(
        {key: event[key] for key in _SEMANTIC_FIELDS}
    )
    if expected_run_id is not None and semantic["run_id"] != expected_run_id:
        raise ValueError(
            "run_id differs from first event: "
            f"expected {expected_run_id!r}, got {semantic['run_id']!r}"
        )
    if expected_engine is not None and semantic["engine"] != expected_engine:
        raise ValueError(
            "engine differs from first event: "
            f"expected {expected_engine!r}, got {semantic['engine']!r}"
        )

    prev_hash = event["prev_hash"]
    if type(prev_hash) is not str or not _HASH_RE.fullmatch(prev_hash):
        raise ValueError("prev_hash must be a full lowercase SHA-256 digest")
    if prev_hash != expected_prev_hash:
        raise ValueError(
            f"prev_hash mismatch: expected {expected_prev_hash}, got {prev_hash}"
        )
    event_hash = event["event_hash"]
    if type(event_hash) is not str or not _HASH_RE.fullmatch(event_hash):
        raise ValueError("event_hash must be a full lowercase SHA-256 digest")
    calculated_hash = _event_hash(event)
    if event_hash != calculated_hash:
        raise ValueError(
            f"event_hash mismatch: expected {calculated_hash}, got {event_hash}"
        )
    return event


def _reject_duplicate_keys(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise _DuplicateKeyError(f"duplicate key {key!r}")
        value[key] = item
    return value


def _reject_nonstandard_constant(value: str):
    raise ValueError(f"non-standard JSON constant {value}")


def _failure(*, line: int, n: int, detail: str, last_hash: str) -> dict:
    return {
        "ok": False,
        "n": n,
        "error": f"line {line}: {detail}",
        "last_hash": last_hash,
    }


def _verify_text(
    text: str,
    *,
    expected_run_id: str | None = None,
    expected_engine: str | None = None,
) -> dict:
    if not text:
        return {"ok": True, "n": 0, "error": None, "last_hash": GENESIS_HASH}

    n = 0
    last_hash = GENESIS_HASH
    run_id = expected_run_id
    engine = expected_engine
    lines = text.split("\n")
    for line_number, raw_line in enumerate(lines, start=1):
        final_segment = line_number == len(lines)
        if final_segment and not raw_line:
            break
        if final_segment:
            return _failure(
                line=line_number,
                n=n,
                detail="event line is missing its trailing newline",
                last_hash=last_hash,
            )
        if not raw_line:
            return _failure(
                line=line_number,
                n=n,
                detail="blank event is not allowed",
                last_hash=last_hash,
            )
        try:
            event = json.loads(
                raw_line,
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=_reject_nonstandard_constant,
            )
        except _DuplicateKeyError as exc:
            return _failure(
                line=line_number,
                n=n,
                detail=str(exc),
                last_hash=last_hash,
            )
        except RecursionError:
            return _failure(
                line=line_number,
                n=n,
                detail="JSON nesting exceeds decoder limit",
                last_hash=last_hash,
            )
        except (json.JSONDecodeError, ValueError) as exc:
            return _failure(
                line=line_number,
                n=n,
                detail=f"invalid JSON: {exc}",
                last_hash=last_hash,
            )
        try:
            verified = _validate_event(
                event,
                expected_seq=n + 1,
                expected_prev_hash=last_hash,
                expected_run_id=run_id,
                expected_engine=engine,
            )
            if raw_line != canonical_json(verified):
                raise ValueError("event is not encoded as canonical JSON")
        except RecursionError:
            return _failure(
                line=line_number,
                n=n,
                detail="JSON nesting exceeds validation or canonicalization limit",
                last_hash=last_hash,
            )
        except (TypeError, ValueError) as exc:
            return _failure(
                line=line_number,
                n=n,
                detail=str(exc),
                last_hash=last_hash,
            )
        run_id = verified["run_id"] if run_id is None else run_id
        engine = verified["engine"] if engine is None else engine
        last_hash = verified["event_hash"]
        n += 1

    return {"ok": True, "n": n, "error": None, "last_hash": last_hash}


def _verify_bytes(
    data: bytes,
    *,
    expected_run_id: str | None = None,
    expected_engine: str | None = None,
) -> dict:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        prefix_end = data.rfind(b"\n", 0, exc.start) + 1
        reliable = _verify_text(
            data[:prefix_end].decode("utf-8"),
            expected_run_id=expected_run_id,
            expected_engine=expected_engine,
        )
        if not reliable["ok"]:
            return reliable
        return _failure(
            line=data[: exc.start].count(b"\n") + 1,
            n=reliable["n"],
            detail=f"invalid UTF-8: {exc}",
            last_hash=reliable["last_hash"],
        )
    return _verify_text(
        text,
        expected_run_id=expected_run_id,
        expected_engine=expected_engine,
    )


def _verify_handle(
    handle,
    *,
    expected_run_id: str | None = None,
    expected_engine: str | None = None,
) -> dict:
    handle.seek(0)
    content = handle.read()
    if isinstance(content, bytes):
        return _verify_bytes(
            content,
            expected_run_id=expected_run_id,
            expected_engine=expected_engine,
        )
    return _verify_text(
        content,
        expected_run_id=expected_run_id,
        expected_engine=expected_engine,
    )


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    fd = os.open(path, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def verify_event_chain(path: Path | str) -> dict:
    """Verify a complete log and report the first invalid line without raising."""
    target = Path(path)
    try:
        handle = target.open("rb")
    except FileNotFoundError:
        return {"ok": True, "n": 0, "error": None, "last_hash": GENESIS_HASH}

    with handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
        try:
            return _verify_handle(handle)
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def append_guarded_event(
    path: Path | str,
    *,
    guard: Callable[[tuple[dict, ...], dict], dict | None],
    **fields,
) -> dict:
    """Validate history and run ``guard`` under the same exclusive append lock.

    Returning an existing event makes a semantic retry idempotent. Returning
    ``None`` authorizes one append. Raising rejects without changing the log.
    """
    if not callable(guard):
        raise TypeError("guard must be callable")
    return _append_event(path, fields, guard=guard)


def append_event(path: Path | str, **fields) -> dict:
    """Durably append one event; every append also fsyncs the parent directory."""
    return _append_event(path, fields, guard=None)


def _append_event(
    path: Path | str,
    fields: dict,
    *,
    guard: Callable[[tuple[dict, ...], dict], dict | None] | None,
) -> dict:
    normalized_fields = _validate_semantic_fields(fields)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            existing = _verify_handle(
                handle,
                expected_run_id=normalized_fields["run_id"],
                expected_engine=normalized_fields["engine"],
            )
            if not existing["ok"]:
                raise ValueError(
                    f"invalid existing event chain: {existing['error']}"
                )
            if guard is not None:
                handle.seek(0)
                history = tuple(
                    json.loads(line)
                    for line in handle.read().decode("utf-8").splitlines()
                )
                guarded = guard(history, normalized_fields)
                if guarded is not None:
                    if not any(
                        item.get("event_hash") == guarded.get("event_hash")
                        and item == guarded
                        for item in history
                    ):
                        raise ValueError("guard returned an event outside locked history")
                    return guarded
            event = {
                "schema_version": EVENT_SCHEMA_VERSION,
                "seq": existing["n"] + 1,
                "run_id": normalized_fields["run_id"],
                "ts": _utc_now(),
                "engine": normalized_fields["engine"],
                "stage": normalized_fields["stage"],
                "invocation_id": normalized_fields["invocation_id"],
                "attempt": normalized_fields["attempt"],
                "subject": normalized_fields["subject"],
                "event_type": normalized_fields["event_type"],
                "payload": normalized_fields["payload"],
                "prev_hash": existing["last_hash"],
            }
            event["event_hash"] = _event_hash(event)
            _validate_event(
                event,
                expected_seq=event["seq"],
                expected_prev_hash=event["prev_hash"],
                expected_run_id=normalized_fields["run_id"],
                expected_engine=normalized_fields["engine"],
            )
            line = (canonical_json(event) + "\n").encode("utf-8")
            handle.seek(0, os.SEEK_END)
            written = handle.write(line)
            if written != len(line):
                raise OSError(
                    f"short write while appending event: {written}/{len(line)} bytes"
                )
            handle.flush()
            os.fsync(handle.fileno())
            # This extra syscall on every append is deliberate: a prior attempt may
            # have persisted event bytes but failed while syncing the new dir entry.
            _fsync_directory(target.parent)
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    return event
