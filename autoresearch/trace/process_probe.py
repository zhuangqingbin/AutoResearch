"""Is the process that owns this run still the one that started it?

A stale-run recovery that only checks "does this PID exist" is wrong: PIDs are
reused, so a fresh unrelated process can make a dead run look alive forever.
Identity here is the pair (pid, start time); the start time is read as an opaque
token so no locale or clock-format assumption can silently break the check.
"""

from __future__ import annotations

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


__all__ = ["current_lease", "matches", "pid_exists", "started_at"]
