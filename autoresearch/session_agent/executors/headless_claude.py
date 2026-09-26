"""Headless executor: one ``claude -p --agent <role>`` process per inference attempt.

Each attempt is a separate top-level Claude Code session = its own context, so the
independent-context requirement of reviews is met by the process boundary.  Probes
(``docs/research/2026-09-26-headless-driver-probes.md``): ``--agent`` loads the project
agent definition and hooks, the result JSON carries ``usage`` / ``total_cost_usd`` /
``session_id``, and the transcript lands at ``~/.claude/projects/<slug>/<session-id>.jsonl``.

Transport only (``executors.base``): agent, model, effort, prompt and output paths come
from the :class:`DispatchRequest` verbatim.  The executor's own checks are about the
*process*: bounded wall clock (the whole process group is killed on timeout), the result
JSON must parse and must not be an error, and every declared output file must exist after
a clean exit — an agent saying "I wrote it" is not a file.

The child gets an explicit environment (:func:`child_env`): no ``ANTHROPIC_*`` / routing
switches / project secrets, so a key in ``.env`` or a proxy shell can never move the run off
the subscription login; the call record lists the dropped NAMES, never values.

Every call leaves ``<staging>/_dispatch/headless/<task_id>.a<attempt>.json`` (argv with the
prompt redacted, pid, exit code, usage, cost, session id, transcript path) plus the raw
``.stdout`` / ``.stderr`` streams; ``trace.usage_harvest`` meters headless runs from the
records.  ``_dispatch/`` is control traffic, excluded from scan staging bundles.
"""
from __future__ import annotations

import contextlib
import glob
import json
import os
import re
import shutil
import signal
import subprocess
import time
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType

from autoresearch.common.atomic import atomic_write_json, sha256_bytes
from autoresearch.session_agent.executors.base import (
    DISPATCH_DIR,
    DispatchRequest,
    DispatchResult,
    ExecutorTimeout,
)

#: Sub-directory of ``<staging>/_dispatch/`` holding one record per headless call.
HEADLESS_DIR = "headless"

#: Wall clock per role (spec §6 C1: intel 12 min / card 25 min / L3 30 min; the other
#: roles keep the host-mode values of ``executors.base.DEFAULT_TIMEOUTS``).
HEADLESS_TIMEOUTS: Mapping[str, float] = MappingProxyType({
    "macro.brief": 900.0,
    "sector.brief": 600.0,
    "scan.l3": 1800.0,
    "scan.l3.repair": 600.0,
    "scan.l4.intel": 720.0,
    "scan.l4.card": 1500.0,
    "scan.l4.review": 1500.0,
})

#: ``--max-turns`` per role.  Longest real subagent runs over the 30 days before
#: 2026-09-26 (unique assistant messages): macro-brief 25, sector-brief 14, l3-rank 32,
#: l4-intel 34, l4-card 35.  Caps sit ~2x above them: the wall clock is the real guard,
#: the turn cap only stops a runaway loop (``--max-budget-usd`` is unverified under a
#: subscription, probe doc "待办").
MAX_TURNS: Mapping[str, int] = MappingProxyType({
    "macro.brief": 50,
    "sector.brief": 30,
    "scan.l3": 64,
    "scan.l3.repair": 30,
    "scan.l4.intel": 64,
    "scan.l4.card": 80,
    "scan.l4.review": 80,
})
#: Fallback for roles outside ``MAX_TURNS``: scan_config ``agents.<role>.tier``.
TIER_MAX_TURNS: Mapping[str, int] = MappingProxyType({
    "critical": 80, "analytical": 40, "repair": 30, "relay": 20,
})
DEFAULT_MAX_TURNS = 60
EXCERPT_CHARS = 500

#: Environment never handed to ``claude -p`` (review I2).  The project rule is zero paid
#: LLM API: every ``ANTHROPIC_*`` variable (API key, auth token, base URL, model overrides)
#: and the CLI's own routing switches would silently move the run off the subscription
#: login (``.env`` from the retired paid framework, a ``cc-ds`` shell).  Project secrets
#: (``*_API_KEY`` / ``*_TOKEN`` / ``*_SECRET``: tushare, Bark, FRED, …) are not the agents'
#: business either.  ``CLAUDECODE`` / ``CLAUDE_CODE_ENTRYPOINT`` belong to a parent Claude
#: session (a manual trigger from inside one); the child sets its own.
_ENV_DROP_PREFIXES = ("ANTHROPIC_",)
_ENV_DROP_SUFFIXES = ("_API_KEY", "_TOKEN", "_SECRET")
_ENV_DROP_NAMES = frozenset({
    "CLAUDE_CODE_SUBAGENT_MODEL", "CLAUDE_CODE_EFFORT_LEVEL", "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY", "DELIVERY_MAIL_TO",
    "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT",
})
#: ``claude setup-token`` subscription login for non-interactive use — the opposite of
#: moving billing, so it is kept.
_ENV_KEEP_NAMES = frozenset({"CLAUDE_CODE_OAUTH_TOKEN"})


def _drops(name: str) -> bool:
    if name in _ENV_KEEP_NAMES:
        return False
    return (name in _ENV_DROP_NAMES or name.startswith(_ENV_DROP_PREFIXES)
            or name.endswith(_ENV_DROP_SUFFIXES))


def child_env(parent: Mapping[str, str] | None = None) -> tuple[dict[str, str], list[str]]:
    """``(environment for claude -p, sorted NAMES of what was dropped)`` — never values."""
    source = os.environ if parent is None else parent
    env = {key: value for key, value in source.items() if not _drops(key)}
    return env, sorted(key for key in source if _drops(key))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def resolve_claude_bin(explicit: str | None = None) -> str:
    """``--claude-bin`` > ``$AUTORESEARCH_CLAUDE_BIN`` > ``PATH`` > ``~/.local/bin/claude``.

    launchd starts jobs with a minimal PATH; the last fallback is where the official
    installer puts the CLI.
    """
    if explicit:
        return explicit
    env = str(os.environ.get("AUTORESEARCH_CLAUDE_BIN", "")).strip()
    if env:
        return env
    found = shutil.which("claude")
    if found:
        return found
    local = Path.home() / ".local" / "bin" / "claude"
    return str(local) if local.exists() else "claude"


def _resolved(binary: str) -> str:
    """The real file behind the CLI (``~/.local/bin/claude`` is a symlink to a versioned
    build that auto-updates between nights)."""
    found = binary if os.sep in binary else (shutil.which(binary) or binary)
    try:
        return str(Path(found).resolve())
    except OSError:
        return found


def project_slug(path: Path | str) -> str:
    """Claude Code's projects-directory name for a working directory."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(Path(path).resolve()))


def _parse_result(text: str) -> dict | None:
    """The ``--output-format json`` result object (tolerates a stream array / log lines).

    A ``type == "result"`` object wins over any other trailing JSON line; an untyped dict
    is only the fallback.
    """
    body = text.strip()
    if not body:
        return None
    candidates = [body, *reversed(body.splitlines())]
    fallback = None
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, list):
            results = [item for item in value
                       if isinstance(item, dict) and item.get("type") == "result"]
            value = results[-1] if results else None
        if isinstance(value, dict):
            if value.get("type") == "result":
                return value
            fallback = fallback or value
    return fallback


#: Transient API failures the CLI reports as text (overloaded / 5xx): one retry, like a
#: dropped connection, instead of AGENT_ERROR → BLOCKED for the whole night (review M4).
_TRANSIENT_API = re.compile(
    r"overloaded|API Error:?\s*5\d\d|internal server error|service unavailable|bad gateway",
    re.I)


def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def stop_group(pgid: int, grace: float) -> bool:
    """SIGTERM a process group, wait up to ``grace`` s, SIGKILL what is left.

    Returns whether the group existed.  Never raises for a vanished group.
    """
    try:
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return False
    deadline = time.monotonic() + max(0.0, grace)
    while time.monotonic() < deadline:
        if not _group_alive(pgid):
            return True
        time.sleep(0.05)
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pgid, signal.SIGKILL)
    return True


def owns_group(record: Mapping) -> bool:
    """Is the process group named by a call record still *that* ``claude -p``?

    Leader alive → the recorded start token must match (a pid twin is never signalled);
    leader gone but group alive → our leftovers (a pgid is not recycled while its group
    exists).
    """
    from autoresearch.trace import process_probe

    pid = record.get("pid")
    if type(pid) is not int or pid <= 1:
        return False
    if process_probe.pid_exists(pid):
        recorded = record.get("process_started_at")
        return recorded is None or process_probe.started_at(pid) == recorded
    return _group_alive(pid)


def _excerpt(text: str | None) -> str:
    return str(text or "").strip()[:EXCERPT_CHARS]


class HeadlessClaudeExecutor:
    """Run each inference attempt as its own ``claude -p`` session (blocking)."""

    name = "headless"
    #: A dead runner cannot re-attach to a ``claude -p`` it did not start; the runner
    #: then reports the attempt as an orphan (STALLED) instead of launching a second one.
    supports_reattach = False

    def __init__(
        self,
        staging: Path | str,
        *,
        claude_bin: str | None = None,
        cwd: Path | str | None = None,
        transcript_root: Path | str | None = None,
        max_turns: Mapping[str, int] | None = None,
        kill_grace_seconds: float = 5.0,
    ):
        self.staging = Path(staging)
        self.claude_bin = resolve_claude_bin(claude_bin)
        self.cwd = Path(cwd) if cwd is not None else Path.cwd()
        self.transcript_root = Path(transcript_root) if transcript_root is not None else None
        self.max_turns = {**MAX_TURNS, **dict(max_turns or {})}
        self.kill_grace_seconds = float(kill_grace_seconds)

    # ── helpers ─────────────────────────────────────────────────────────────────
    def max_turns_for(self, request: DispatchRequest) -> int:
        if request.max_turns:
            return int(request.max_turns)
        if request.role in self.max_turns:
            return int(self.max_turns[request.role])
        return int(TIER_MAX_TURNS.get(request.tier or "", DEFAULT_MAX_TURNS))

    def _record_dir(self) -> Path:
        path = self.staging / DISPATCH_DIR / HEADLESS_DIR
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _transcript(self, session_id: str) -> str | None:
        if self.transcript_root is not None:
            candidate = self.transcript_root / f"{session_id}.jsonl"
            return str(candidate) if candidate.is_file() else None
        candidate = (Path.home() / ".claude" / "projects" / project_slug(self.cwd)
                     / f"{session_id}.jsonl")
        if candidate.is_file():
            return str(candidate)
        from autoresearch.trace.usage_harvest import find_session_files

        found = find_session_files(session_id)[0]
        return str(found) if found is not None else None

    def argv(self, request: DispatchRequest, session_id: str) -> list[str]:
        argv = [
            self.claude_bin, "-p",
            "--agent", request.agent_type,
            "--output-format", "json",
            "--permission-mode", "bypassPermissions",
            "--session-id", session_id,
            "--max-turns", str(self.max_turns_for(request)),
        ]
        if request.model:
            argv += ["--model", str(request.model)]
        if request.effort:
            argv += ["--effort", str(request.effort)]
        argv.append(request.prompt)
        return argv

    # ── dispatch ────────────────────────────────────────────────────────────────
    def dispatch(self, request: DispatchRequest) -> DispatchResult:
        if request.engine != "claude":
            return DispatchResult(
                ok=False, error_class="AGENT_ERROR",
                error=f"headless executor runs `claude -p` only; run engine is {request.engine}")
        session_id = str(uuid.uuid4())
        argv = self.argv(request, session_id)
        folder = self._record_dir()
        priors = self._prior_records(folder, request.task_id)
        superseded = self._stop_orphans(priors, session_id)
        moved = self._move_aside_stale_outputs(request, folder, session_id, bool(priors))
        stem = f"{request.task_id}.a{request.attempt}"
        if (folder / f"{stem}.json").exists():
            # A restarted runner re-dispatching this attempt: never overwrite the earlier
            # record (it names the orphan's pid and its cost) — key this call by session.
            stem = f"{stem}.{session_id[:8]}"
        stdout_path, stderr_path = folder / f"{stem}.stdout", folder / f"{stem}.stderr"
        env, env_stripped = child_env()
        record = {
            "schema_version": 1,
            "run_id": request.run_id,
            "task_id": request.task_id,
            "attempt": request.attempt,
            "role": request.role,
            "agent_type": request.agent_type,
            "argv": argv[:-1] + [
                f"<prompt {len(request.prompt)} chars sha256:"
                f"{sha256_bytes(request.prompt.encode('utf-8'))[:16]}>"],
            "requested_session_id": session_id,
            "session_id": None,
            "host_session_ref": request.host_session_ref,
            "cwd": str(self.cwd),
            "claude_bin_resolved": _resolved(self.claude_bin),
            "env_stripped": env_stripped,
            "superseded_pids": superseded,
            "outputs_moved_aside": moved,
            "timeout_seconds": request.timeout_seconds,
            "state": "STARTING",
            "pid": None,
            "process_started_at": None,
            "started_at": _now(),
            "ended_at": None,
            "elapsed_s": None,
            "exit_code": None,
            "timed_out": False,
            "is_error": None,
            "subtype": None,
            "num_turns": None,
            "total_cost_usd": None,
            "usage": None,
            "transcript_path": None,
            "outputs_missing": [],
            "leftovers_swept": False,
            "error": None,
        }
        started = time.monotonic()
        try:
            with stdout_path.open("wb") as out, stderr_path.open("wb") as err:
                proc = subprocess.Popen(  # noqa: S603 - argv list, no shell
                    argv, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                    cwd=str(self.cwd), env=env, start_new_session=True)
        except OSError as exc:
            record.update(state="SPAWN_FAILED", ended_at=_now(),
                          error=f"{type(exc).__name__}: {exc}")
            atomic_write_json(folder / f"{stem}.json", record)
            return DispatchResult(ok=False, error_class="AGENT_ERROR",
                                  error=f"cannot start {self.claude_bin}: {exc}")
        from autoresearch.trace import process_probe

        record.update(state="RUNNING", pid=proc.pid,
                      process_started_at=process_probe.started_at(proc.pid))
        atomic_write_json(folder / f"{stem}.json", record)
        try:
            exit_code = proc.wait(timeout=float(request.timeout_seconds))
            # A clean exit can still leave a grandchild in the group (review M1a).
            record["leftovers_swept"] = stop_group(proc.pid, self.kill_grace_seconds)
        except subprocess.TimeoutExpired:
            self._kill_group(proc)
            record.update(state="KILLED", timed_out=True, exit_code=proc.returncode,
                          ended_at=_now(), elapsed_s=round(time.monotonic() - started, 1),
                          session_id=session_id,
                          error=f"timeout after {request.timeout_seconds:.0f}s")
            atomic_write_json(folder / f"{stem}.json", record)
            raise ExecutorTimeout(
                f"{request.task_id} a{request.attempt}: claude -p 超时 "
                f"{request.timeout_seconds:.0f}s,已杀整个进程组(session {session_id})"
            ) from None
        stdout = stdout_path.read_text(encoding="utf-8", errors="replace")
        stderr = stderr_path.read_text(encoding="utf-8", errors="replace")
        doc = _parse_result(stdout)
        actual = str((doc or {}).get("session_id") or session_id)
        transcript = self._transcript(actual)
        usage = (doc or {}).get("usage")
        usage = dict(usage) if isinstance(usage, Mapping) else None
        record.update(
            state="EXITED", exit_code=exit_code, ended_at=_now(),
            elapsed_s=round(time.monotonic() - started, 1), session_id=actual,
            is_error=(doc or {}).get("is_error"), subtype=(doc or {}).get("subtype"),
            num_turns=(doc or {}).get("num_turns"),
            total_cost_usd=(doc or {}).get("total_cost_usd"), usage=usage,
            transcript_path=transcript,
        )
        identity = {
            "session_ref": actual,
            "context_ref": actual,
            "parent_context_ref": request.host_session_ref,
            "transcript_path": transcript,
            "usage": usage,
        }
        error, error_class = self._failure(exit_code, doc, stdout, stderr)
        if error is None:
            missing = [path for path in request.output_paths.values() if not Path(path).is_file()]
            if missing:
                record["outputs_missing"] = missing
                error = (f"{request.task_id}: 产物未落盘 {missing}"
                         "(claude -p 退出 0,agent 自称完成不算数)")
                error_class = "CONTRACT_ERROR"
        record["error"] = error
        atomic_write_json(folder / f"{stem}.json", record)
        if error is not None:
            return DispatchResult(ok=False, error=error, error_class=error_class, **identity)
        return DispatchResult(ok=True, **identity)

    @staticmethod
    def _failure(exit_code: int, doc: dict | None, stdout: str,
                 stderr: str) -> tuple[str | None, str | None]:
        """``(error, declared_class)``; the class is left to ``classify_error`` except for
        transient API errors (overloaded / 5xx), declared CONNECTION = one retry."""
        if exit_code != 0:
            detail = _excerpt((doc or {}).get("result")) or _excerpt(stderr) or _excerpt(stdout)
            error = f"claude -p 失败 exit={exit_code}: {detail}"
        elif doc is None:
            error = (f"claude -p 退出 0 但结果不可解析: "
                     f"{_excerpt(stdout) or _excerpt(stderr) or '(空输出)'}")
        elif doc.get("is_error") is True or str(doc.get("subtype") or "").startswith("error"):
            error = (f"claude -p is_error(subtype={doc.get('subtype') or '—'}): "
                     f"{_excerpt(doc.get('result')) or _excerpt(stderr)}")
        else:
            return None, None
        return error, ("CONNECTION" if _TRANSIENT_API.search(error) else None)

    # ── before launch: orphans of this task, stale outputs (review M9 / M5) ─────────
    @staticmethod
    def _prior_records(folder: Path, task_id: str) -> list[tuple[Path, dict]]:
        found = []
        for path in sorted(folder.glob(f"{glob.escape(task_id)}.a*.json")):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(record, dict) and record.get("task_id") == task_id:
                found.append((path, record))
        return found

    def _stop_orphans(self, priors: list[tuple[Path, dict]], session_id: str) -> list[int]:
        """A still-running earlier session of this task (runner restarted) would keep
        writing the same outputs while the new attempt runs: stop it first."""
        stopped = []
        for path, record in priors:
            if record.get("state") not in {"STARTING", "RUNNING"} or not owns_group(record):
                continue
            stop_group(int(record["pid"]), self.kill_grace_seconds)
            stopped.append(int(record["pid"]))
            record.update(state="KILLED", superseded_by=session_id, ended_at=_now(),
                          error=f"superseded by session {session_id} (runner re-dispatched)")
            atomic_write_json(path, record)
        return stopped

    @staticmethod
    def _move_aside_stale_outputs(request: DispatchRequest, folder: Path, session_id: str,
                                  has_prior: bool) -> list[dict]:
        """Before a retry, outputs left by an earlier attempt move to ``stale/``.

        Otherwise an attempt that exits 0 without writing passes the missing-output check
        on the old file, and ``l4-intel`` (no Read tool) cannot overwrite a file it never
        read.  ``_dispatch/`` is control traffic: never part of a staging bundle.
        """
        if request.attempt <= 1 and not has_prior:
            return []
        previous = request.attempt - 1 if request.attempt > 1 else request.attempt
        moved = []
        for path in request.output_paths.values():
            source = Path(path)
            if not source.is_file():
                continue
            stale = folder / "stale"
            stale.mkdir(exist_ok=True)
            target = stale / f"{request.task_id}.a{previous}.{source.name}.stale"
            if target.exists():
                target = stale / f"{request.task_id}.a{previous}.{session_id[:8]}.{source.name}.stale"
            shutil.move(str(source), str(target))
            moved.append({"from": str(source), "to": str(target)})
        return moved

    def _kill_group(self, proc: subprocess.Popen) -> None:
        """SIGTERM the whole process group, then SIGKILL whatever is left."""
        for sig, grace in ((signal.SIGTERM, self.kill_grace_seconds), (signal.SIGKILL, 5.0)):
            try:
                os.killpg(proc.pid, sig)
            except ProcessLookupError:
                return
            try:
                proc.wait(timeout=grace)
            except subprocess.TimeoutExpired:
                continue
            if sig == signal.SIGTERM:
                # The leader is gone; grandchildren in the group may not be.
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(proc.pid, signal.SIGKILL)
            return


__all__ = [
    "DEFAULT_MAX_TURNS", "HEADLESS_DIR", "HEADLESS_TIMEOUTS", "HeadlessClaudeExecutor",
    "MAX_TURNS", "TIER_MAX_TURNS", "child_env", "owns_group", "project_slug",
    "resolve_claude_bin", "stop_group",
]
