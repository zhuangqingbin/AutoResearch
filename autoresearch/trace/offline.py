"""System-enforced filesystem and network isolation for forensic replay.

The helpers in this module are intentionally small.  They do not decide what a
workflow should replay; they only create the fixed scratch layout and launch a
child process with verifiable deny rules.  In-process monkeypatches are not an
isolation proof and therefore never produce ``ENFORCED``.
"""

from __future__ import annotations

import functools
import os
import platform
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path


class IsolationUnavailable(RuntimeError):
    """The current host has no supported system replay sandbox."""


@dataclass(frozen=True)
class OfflineLayout:
    root: Path
    code: Path
    inputs: Path
    expected: Path
    runtime: Path
    work: Path
    outputs: Path
    effects: Path
    audit: Path


@dataclass(frozen=True)
class IsolatedResult:
    exit_code: int
    stdout: bytes
    stderr: bytes
    isolation_status: str
    backend: str
    denied_read_roots: tuple[str, ...]
    denied_write_roots: tuple[str, ...]


def create_offline_layout(root: Path | str) -> OfflineLayout:
    """Create one fresh replay tree; never merge with prior audit bytes."""
    target = Path(root)
    if target.exists():
        if target.is_symlink() or not target.is_dir():
            raise ValueError("replay output_dir must be a real directory")
        if any(target.iterdir()):
            raise FileExistsError("replay output_dir is not empty")
    else:
        target.mkdir(parents=True)
    resolved = target.resolve(strict=True)
    paths = {
        name: resolved / name
        for name in ("code", "inputs", "expected", "runtime", "work", "outputs", "effects", "audit")
    }
    for path in paths.values():
        path.mkdir()
    return OfflineLayout(root=resolved, **paths)


def _sandbox_executable() -> str | None:
    if platform.system() != "Darwin":
        return None
    candidate = shutil.which("sandbox-exec")
    return candidate if candidate and Path(candidate).is_file() else None


@functools.lru_cache(maxsize=1)
def strict_isolation_available() -> bool:
    """Probe the backend, not merely its path, so CI skips are honest."""
    executable = _sandbox_executable()
    if executable is None:
        return False
    probe = subprocess.run(
        [executable, "-p", "(version 1) (allow default) (deny network*)", "/usr/bin/true"],
        capture_output=True,
        check=False,
    )
    return probe.returncode == 0


def _quoted(path: Path) -> str:
    return str(path.resolve()).replace("\\", "\\\\").replace('"', '\\"')


def _rule(action: str, roots: Sequence[Path | str]) -> str:
    unique = sorted({str(Path(root).resolve()) for root in roots})
    if not unique:
        return ""
    clauses = " ".join(f'(subpath "{_quoted(Path(root))}")' for root in unique)
    return f"({action} {clauses})"


def _macos_profile(
    layout: OfflineLayout,
    *,
    denied_read_roots: Sequence[Path | str],
    denied_write_roots: Sequence[Path | str],
) -> str:
    # Default allow keeps the selected local interpreter/runtime usable.  Explicit deny
    # rules remove every original evidence/state path and the comparator-only tree.
    read_denies = [layout.expected, *denied_read_roots]
    write_denies = [
        layout.code,
        layout.inputs,
        layout.expected,
        layout.runtime,
        *denied_write_roots,
    ]
    return " ".join(
        part
        for part in (
            "(version 1)",
            "(allow default)",
            "(deny network*)",
            _rule("deny file-read*", read_denies),
            _rule("deny file-write*", write_denies),
        )
        if part
    )


def _clean_environment(layout: OfflineLayout, values: Mapping[str, str]) -> dict[str, str]:
    safe = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8"),
        "TMPDIR": str(layout.work / "tmp"),
        "PYTHONNOUSERSITE": "1",
        "AUTORESEARCH_OFFLINE": "1",
    }
    (layout.work / "tmp").mkdir(exist_ok=True)
    for key, value in values.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ValueError("replay environment must contain strings")
        upper = key.upper()
        if any(marker in upper for marker in ("TOKEN", "SECRET", "PASSWORD", "COOKIE", "API_KEY")):
            continue
        if upper.startswith("AUTORESEARCH_") and upper not in {
            "AUTORESEARCH_ENGINE",
            "AUTORESEARCH_REPLAY_CAPSULE",
            "AUTORESEARCH_TASK_ID",
            "AUTORESEARCH_ATTEMPT",
            "AUTORESEARCH_OFFLINE",
        }:
            continue
        safe[key] = value
    return safe


def run_isolated(
    argv: Sequence[str],
    layout: OfflineLayout,
    *,
    env: Mapping[str, str],
    denied_read_roots: Sequence[Path | str] = (),
    denied_write_roots: Sequence[Path | str] = (),
    timeout: float | None = None,
) -> IsolatedResult:
    """Run ``argv`` under the host sandbox and return an attested launch result."""
    if not argv or any(not isinstance(item, str) or not item for item in argv):
        raise ValueError("isolated argv must be a non-empty string sequence")
    executable = _sandbox_executable()
    if executable is None or not strict_isolation_available():
        raise IsolationUnavailable("no supported system replay sandbox is available")
    profile = _macos_profile(
        layout,
        denied_read_roots=denied_read_roots,
        denied_write_roots=denied_write_roots,
    )
    completed = subprocess.run(
        [executable, "-p", profile, *argv],
        cwd=layout.work,
        env=_clean_environment(layout, env),
        capture_output=True,
        check=False,
        timeout=timeout,
    )
    return IsolatedResult(
        exit_code=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        isolation_status="ENFORCED",
        backend="macos-sandbox-exec",
        denied_read_roots=tuple(
            sorted(
                {
                    str(layout.expected.resolve()),
                    *(str(Path(root).resolve()) for root in denied_read_roots),
                }
            )
        ),
        denied_write_roots=tuple(
            sorted(
                {
                    str(layout.code.resolve()),
                    str(layout.inputs.resolve()),
                    str(layout.expected.resolve()),
                    str(layout.runtime.resolve()),
                    *(str(Path(root).resolve()) for root in denied_write_roots),
                }
            )
        ),
    )


__all__ = [
    "IsolatedResult",
    "IsolationUnavailable",
    "OfflineLayout",
    "create_offline_layout",
    "run_isolated",
    "strict_isolation_available",
]
