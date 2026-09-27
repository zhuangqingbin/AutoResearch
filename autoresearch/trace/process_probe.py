"""Is the process that owns this run still the one that started it?

A stale-run recovery that only checks "does this PID exist" is wrong: PIDs are
reused, so a fresh unrelated process can make a dead run look alive forever.
Identity here is the pair (pid, start time); the start time is read as an opaque
token so no locale or clock-format assumption can silently break the check.
"""

from __future__ import annotations

import contextlib
import os
import socket
import subprocess
from collections.abc import Mapping


def pid_exists(pid: int) -> bool:
    """True when a process with this pid exists and we may signal it."""
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # It exists; it just belongs to someone else.
        return True
    except (TypeError, ValueError, OverflowError):
        return False
    return True


def started_at(pid: int) -> str | None:
    """An opaque per-process start token, or ``None`` when it cannot be read."""
    try:
        completed = subprocess.run(  # noqa: S603,S607 - fixed argv, no shell
            ["ps", "-p", str(int(pid)), "-o", "lstart="],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError, TypeError, ValueError):
        return None
    token = completed.stdout.strip()
    return token or None


def current_lease(*, invocation_id: str | None = None, heartbeat: str) -> dict:
    pid = os.getpid()
    return {
        "hostname": socket.gethostname(),
        "pid": pid,
        "process_started_at": started_at(pid),
        "heartbeat": heartbeat,
        "invocation_id": invocation_id,
    }


def matches(lease: Mapping | None) -> bool:
    """True only when this host still runs *that* process, not a pid twin."""
    if not isinstance(lease, Mapping):
        return False
    if str(lease.get("hostname") or "") != socket.gethostname():
        # Another host's lease cannot be probed from here; never claim it is dead.
        return True
    pid = lease.get("pid")
    if not isinstance(pid, int) or not pid_exists(pid):
        return False
    recorded = lease.get("process_started_at")
    if recorded is None:
        # No recorded identity: a live pid is the strongest signal available.
        return True
    return started_at(pid) == recorded


def group_alive(pgid: int) -> bool:
    """True while any process is left in process group ``pgid``."""
    try:
        os.killpg(int(pgid), 0)
    except (ProcessLookupError, PermissionError, TypeError, ValueError, OverflowError):
        return False
    return True


def stop_group(pgid: int, grace: float = 5.0) -> bool:
    """SIGTERM a process group, wait up to ``grace`` s, SIGKILL what is left.

    Returns whether the group existed.  Never raises for a vanished group.
    """
    import signal
    import time

    try:
        os.killpg(int(pgid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return False
    deadline = time.monotonic() + max(0.0, float(grace))
    while time.monotonic() < deadline:
        if not group_alive(pgid):
            return True
        time.sleep(0.05)
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(int(pgid), signal.SIGKILL)
    return True


def owns_group(record: Mapping) -> bool:
    """Is the process group a call record names (``pid`` = pgid, own session) still *that*
    launch?  Leader alive → its start token must match ``process_started_at`` (a pid twin is
    never signalled; a record without a token trusts the live pid); leader gone but group
    alive → the launch's leftovers (a pgid is not recycled while its group exists)."""
    if not isinstance(record, Mapping):
        return False
    pid = record.get("pid")
    if type(pid) is not int or pid <= 1:
        return False
    if pid_exists(pid):
        recorded = record.get("process_started_at")
        return recorded is None or started_at(pid) == recorded
    return group_alive(pid)


__all__ = ["current_lease", "group_alive", "matches", "owns_group", "pid_exists", "started_at",
           "stop_group"]
