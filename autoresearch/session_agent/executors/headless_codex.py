"""Headless Codex executor: one ``codex exec`` thread per inference attempt (2026-10-08).

Transport only (``executors.base``), the Codex twin of ``headless_claude``: agent, model,
effort, prompt and output paths come from the :class:`DispatchRequest` verbatim; the
executor's own checks are about the *process* (bounded wall clock with process-group
cancellation, a parsable ``--json`` event stream without ``turn.failed`` / ``error``, and
every declared output file present after a clean exit — an agent saying "I wrote it" is not
a file).

Why two ``codex exec`` calls per attempt.  The C4 hook (``scripts/hooks/agent_input_boundary.py``)
selects the frozen task manifest by the hook payload's ``session_id`` — for a top-level
thread that is the thread id — and by nothing else (never the environment, never the
prompt).  ``codex exec`` has no ``--session-id``, so the id only exists once the thread does:

1. **open**  ``codex exec --json … "<OPEN_PROMPT>"`` with the role's own model, reasoning
   effort and ``developer_instructions``: the first event ``thread.started`` carries
   ``thread_id`` (one trivial turn, no tools);
2. **bind**  :func:`task_access.bind_headless` freezes the session-scoped binding for that
   id (a top-level thread has no ``agent_id``), so the model's first tool call already
   meets a binding;
3. **work**  ``codex exec … resume <thread_id> "<prompt + broker commands>"`` with the same
   model / reasoning effort plus ``web_search`` as ``-c`` overrides.

The role's ``developer_instructions`` are read from ``.codex/agents/<config_role>.toml`` — the
same text ``spawn_agent`` injects in host mode — and passed with ``-c developer_instructions=``
**on the open turn**: the 2026-10-08 probes (``docs/research/2026-10-08-codex-exec-probes.md``)
found that ``codex exec resume -c developer_instructions=`` is silently dropped, while the value
given at thread creation survives into every later turn.  The same readout is why the open turn
does not use a cheap relay tier: changing the model between turns makes Codex inject a
17.9k-character ``<model_switch>`` developer message carrying its whole base prompt, which costs
far more than the open turn saves.  So both turns declare the identical model / effort — or
neither declares one and both fall through to the user config.

``--ephemeral`` is never used: it would leave no rollout, hence no transcript evidence and no
metering (``trace.usage_harvest`` meters headless runs from the call records).
"""
from __future__ import annotations

import glob
import json
import os
import subprocess
import time
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType

import tomllib

from autoresearch.common.atomic import atomic_write_json, sha256_bytes
from autoresearch.session_agent.executors.base import (
    DISPATCH_DIR,
    DispatchRequest,
    DispatchResult,
    ExecutorTimeout,
    classify_error,
)
from autoresearch.session_agent.executors.headless_claude import (
    _TRANSIENT_API,
    HEADLESS_DIR,
    HEADLESS_TIMEOUTS,
    HeadlessClaudeExecutor,
    _excerpt,
    _group_stopped,
    child_env as _claude_child_env,
)
from autoresearch.session_agent.task_access import bind_headless as bind_task_access
from autoresearch.trace import process_probe
from autoresearch.trace.process_probe import owns_group, stop_group

#: 开线程那一轮只为拿 thread id:档位与干活轮**必须相同**(换档会被注入 ``<model_switch>`` 基础提示词)。
OPEN_PROMPT = "这是一个即将接收冻结研究任务的线程。现在只回复 OK;不要调用任何工具,不要读任何文件。"
OPEN_TIMEOUT_S = 180.0
#: 研究线程前导瘦身(零推理渲染 ``codex debug prompt-input`` 实测):skills 目录 17k 字符 + 项目 AGENTS.md
#: 6k 字符躺在每个研究线程的每一次调用里,研究角色都用不到。两轮同声明,记录原样。
#: 配置键叫 ``codex_skills_catalog_budget``:法证层把含 ``token`` 的键名一律当密钥拒绝(``identity._SECRET_KEY_RE``)。
PROJECT_DOC_MAX_BYTES = 0
SKILLS_MAX_CONTEXT_TOKENS = 1000
#: Codex 侧每个角色的墙钟与 Claude headless 同一张表(``session.timeouts.headless`` 可逐 role 覆盖)。
HEADLESS_CODEX_TIMEOUTS: Mapping[str, float] = MappingProxyType(dict(HEADLESS_TIMEOUTS))
#: 订阅登录以外的任何计费 / 路由开关都不传给子进程(与 ``headless_claude.child_env`` 叠加)。
_CODEX_ENV_DROP_PREFIXES = ("OPENAI_",)
_CODEX_ENV_DROP_NAMES = frozenset({"CODEX_API_KEY"})
#: ``.codex/agents/<config_role>.toml``:``developer_instructions`` 的来源。
DEFAULT_AGENTS_DIR = Path(".codex") / "agents"


def codex_child_env(parent: Mapping[str, str] | None = None) -> tuple[dict[str, str], list[str]]:
    """``(environment for codex exec, sorted NAMES of what was dropped)`` — never values."""
    env, dropped = _claude_child_env(parent)
    extra = sorted(key for key in env
                   if key.startswith(_CODEX_ENV_DROP_PREFIXES) or key in _CODEX_ENV_DROP_NAMES)
    for key in extra:
        env.pop(key)
    return env, sorted(set(dropped) | set(extra))


def toml_value(value: str | int | bool) -> str:
    """``-c key=<value>``:bool / int 裸写(Codex 按 TOML 类型校验,``"0"`` 会被拒),其余 TOML basic
    string(``json.dumps`` 的转义集是 TOML basic string 的子集)。"""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    return json.dumps(str(value), ensure_ascii=True)


def resolve_codex_bin(explicit: str | None = None) -> str:
    """``--codex-bin`` > ``$AUTORESEARCH_CODEX_BIN`` > ``PATH`` > ``/usr/local/bin/codex``."""
    if explicit:
        return explicit
    env = str(os.environ.get("AUTORESEARCH_CODEX_BIN", "")).strip()
    if env:
        return env
    import shutil

    found = shutil.which("codex")
    if found:
        return found
    local = Path("/usr/local/bin/codex")
    return str(local) if local.exists() else "codex"


def default_sessions_root() -> Path:
    """Codex rollouts: ``$CODEX_HOME/sessions/YYYY/MM/DD/rollout-<ts>-<thread_id>.jsonl``."""
    home = os.environ.get("CODEX_HOME")
    return (Path(home) if home else Path.home() / ".codex") / "sessions"


def find_rollout(thread_id: str, sessions_root: Path | str) -> str | None:
    """The rollout file of one thread; several (resumed days apart) → the newest."""
    if not thread_id:
        return None
    pattern = str(Path(sessions_root) / "**" / f"rollout-*-{glob.escape(thread_id)}.jsonl")
    matches = [Path(item) for item in glob.glob(pattern, recursive=True)]
    if not matches:
        return None
    return str(max(matches, key=lambda item: item.stat().st_mtime_ns))


def parse_events(text: str) -> dict:
    """``codex exec --json`` 事件流 → ``{thread_id, usage, failed, errors}``.

    ``usage`` sums every ``turn.completed.usage`` (``input_tokens`` / ``cached_input_tokens`` /
    ``output_tokens`` / ``reasoning_output_tokens``); non-JSON lines are ignored.
    """
    thread_id, usage, failed, errors = None, {}, [], []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        kind = event.get("type")
        if kind == "thread.started" and event.get("thread_id"):
            thread_id = str(event["thread_id"])
        elif kind == "turn.completed" and isinstance(event.get("usage"), dict):
            for key, value in event["usage"].items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    usage[key] = usage.get(key, 0) + value
        elif kind == "turn.failed":
            failed.append(_excerpt(json.dumps(event.get("error") or event, ensure_ascii=False)))
        elif kind == "error":
            errors.append(_excerpt(event.get("message") or json.dumps(event, ensure_ascii=False)))
    return {"thread_id": thread_id, "usage": usage or None, "failed": failed, "errors": errors}


def developer_instructions(agents_dir: Path | str, config_role: str | None) -> str | None:
    """The role's ``developer_instructions`` from ``.codex/agents/<config_role>.toml`` (None = no role file)."""
    if not config_role:
        return None
    path = Path(agents_dir) / f"{config_role}.toml"
    if not path.is_file():
        return None
    text = tomllib.loads(path.read_text(encoding="utf-8")).get("developer_instructions")
    return str(text) if text else None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class HeadlessCodexExecutor:
    """Run each inference attempt as its own ``codex exec`` thread (blocking)."""

    name = "headless"
    #: A dead runner cannot re-attach to a ``codex exec`` it did not start.
    supports_reattach = False

    def __init__(
        self,
        staging: Path | str,
        *,
        codex_bin: str | None = None,
        cwd: Path | str | None = None,
        sessions_root: Path | str | None = None,
        agents_dir: Path | str | None = None,
        kill_grace_seconds: float = 5.0,
        open_timeout_seconds: float | None = None,
        context: Mapping[str, int] | None = None,
    ):
        self.staging = Path(staging)
        self.codex_bin = resolve_codex_bin(codex_bin)
        self.cwd = Path(cwd) if cwd is not None else Path.cwd()
        self.sessions_root = Path(sessions_root) if sessions_root is not None else default_sessions_root()
        self.agents_dir = Path(agents_dir) if agents_dir is not None else self.cwd / DEFAULT_AGENTS_DIR
        self.kill_grace_seconds = float(kill_grace_seconds)
        if open_timeout_seconds is None or context is None:
            from autoresearch.session_agent.config import session_cfg

            _sc = session_cfg()
            if open_timeout_seconds is None:
                open_timeout_seconds = _sc["timeouts"]["codex_open_s"]
            if context is None:
                context = _sc.get("context") or {}
        self.open_timeout_seconds = float(open_timeout_seconds)
        self.project_doc_max_bytes = int(context.get("codex_project_doc_max_bytes", PROJECT_DOC_MAX_BYTES))
        self.skills_max_context_tokens = int(context.get("codex_skills_catalog_budget", SKILLS_MAX_CONTEXT_TOKENS))

    # ── helpers ─────────────────────────────────────────────────────────────────
    def _record_dir(self) -> Path:
        path = self.staging / DISPATCH_DIR / HEADLESS_DIR
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _base_argv(self) -> list[str]:
        # ``-C`` = the trusted project root (project ``.codex/`` hooks + agents load from it);
        # ``--skip-git-repo-check`` lets a drill root that is not a git checkout run too.
        return [self.codex_bin, "exec", "--json", "-C", str(self.cwd), "--skip-git-repo-check"]

    def _tier(self, request: DispatchRequest) -> dict[str, str]:
        """Model / effort of the attempt — declared identically on both turns, or on neither."""
        values = {}
        if request.model:
            values["model"] = str(request.model)
        if request.effort:
            values["model_reasoning_effort"] = str(request.effort)
        return values

    def _context_overrides(self) -> dict[str, int]:
        """前导瘦身,两轮同声明:研究线程不装 skills 目录、不读项目 AGENTS.md(角色契约走 developer_instructions)。"""
        return {"project_doc_max_bytes": self.project_doc_max_bytes,
                "skills.max_context_tokens": self.skills_max_context_tokens}

    def open_overrides(self, request: DispatchRequest) -> dict[str, str | int]:
        """``-c`` overrides of the open turn: the role's tier, its developer instructions, the context trims."""
        values = {"approval_policy": "never", **self._tier(request), **self._context_overrides()}
        instructions = developer_instructions(self.agents_dir, request.config_role)
        if instructions:
            values["developer_instructions"] = instructions
        return values

    def argv_open(self, request: DispatchRequest) -> list[str]:
        argv = [*self._base_argv(), "--sandbox", "read-only"]
        for key, value in self.open_overrides(request).items():
            argv += ["-c", f"{key}={toml_value(value)}"]
        return [*argv, OPEN_PROMPT]

    def overrides(self, request: DispatchRequest) -> dict[str, str | int]:
        """``-c`` overrides of the work turn, in a fixed order (recorded verbatim)."""
        values = {"approval_policy": "never", **self._tier(request), **self._context_overrides()}
        web = request.agent_spec.get("web_search")
        if web:
            values["web_search"] = str(web)
        return values

    def argv_work(self, request: DispatchRequest, thread_id: str, prompt: str,
                  last_message: Path) -> list[str]:
        argv = [*self._base_argv(), "--sandbox", "workspace-write", "-o", str(last_message), "resume"]
        for key, value in self.overrides(request).items():
            argv += ["-c", f"{key}={toml_value(value)}"]
        argv += [thread_id, prompt]
        return argv

    def _spawn(self, argv: list[str], *, stdout: Path, stderr: Path, env: dict) -> subprocess.Popen:
        with stdout.open("wb") as out, stderr.open("wb") as err:
            return subprocess.Popen(  # noqa: S603 - argv list, no shell
                argv, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                cwd=str(self.cwd), env=env, start_new_session=True)

    def _kill_group(self, proc: subprocess.Popen) -> bool | None:
        return HeadlessClaudeExecutor._kill_group(self, proc)

    def _stop_orphans(self, priors: list[tuple[Path, dict]], thread_hint: str) -> list[int]:
        stopped = []
        for path, record in priors:
            if record.get("state") not in {"STARTING", "OPENING", "RUNNING"} or not owns_group(record):
                continue
            stop_group(int(record["pid"]), self.kill_grace_seconds)
            stopped.append(int(record["pid"]))
            confirmed = _group_stopped(int(record["pid"]))
            record.update(state="KILLED" if confirmed is True else "CANCEL_UNCONFIRMED",
                          cancel_capability="PROCESS_GROUP", cancel_requested=True,
                          cancel_confirmed=confirmed, superseded_by=thread_hint, ended_at=_now(),
                          error=f"superseded by call {thread_hint} (runner re-dispatched)")
            atomic_write_json(path, record)
        return stopped

    @staticmethod
    def _failure(exit_code: int, events: dict, stdout: str, stderr: str) -> tuple[str | None, str | None]:
        """``(error, declared_class)``; transient API errors (overloaded / 5xx) = CONNECTION = one retry."""
        if exit_code != 0:
            detail = (" | ".join(events["failed"] + events["errors"])
                      or _excerpt(stderr) or _excerpt(stdout))
            error = f"codex exec 失败 exit={exit_code}: {detail}"
        elif events["failed"]:
            error = f"codex exec turn.failed: {' | '.join(events['failed'])}"
        elif events["errors"]:
            error = f"codex exec error: {' | '.join(events['errors'])}"
        else:
            return None, None
        if classify_error(error) == "USAGE_LIMIT":
            return error, "USAGE_LIMIT"
        return error, ("CONNECTION" if _TRANSIENT_API.search(error) else None)

    # ── dispatch ────────────────────────────────────────────────────────────────
    def dispatch(self, request: DispatchRequest) -> DispatchResult:
        if request.engine != "codex":
            return DispatchResult(
                ok=False, error_class="AGENT_ERROR",
                error=f"headless codex executor runs `codex exec` only; run engine is {request.engine}")
        folder = self._record_dir()
        call_id = uuid.uuid4().hex[:8]
        priors = HeadlessClaudeExecutor._prior_records(folder, request.task_id)
        superseded = self._stop_orphans(priors, call_id)
        moved = HeadlessClaudeExecutor._move_aside_stale_outputs(request, folder, call_id, bool(priors))
        stem = f"{request.task_id}.a{request.attempt}"
        if (folder / f"{stem}.json").exists():
            stem = f"{stem}.{call_id}"          # a restarted runner: never overwrite the orphan's record
        env, env_stripped = codex_child_env()
        last_message = folder / f"{stem}.last_message.md"
        record = {
            "schema_version": 1,
            "engine": "codex",
            "transport": "codex exec",
            "run_id": request.run_id,
            "task_id": request.task_id,
            "attempt": request.attempt,
            "role": request.role,
            "agent_type": request.agent_type,
            "config_role": request.config_role,
            "call_id": call_id,
            "argv": None,
            "open": None,
            "thread_id": None,
            "session_id": None,
            "host_session_ref": request.host_session_ref,
            "access_binding": None,
            "cwd": str(self.cwd),
            "codex_bin": self.codex_bin,
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
            "cancel_capability": "PROCESS_GROUP",
            "cancel_requested": False,
            "cancel_confirmed": None,
            "usage": None,
            "last_message_path": str(last_message),
            "transcript_path": None,
            "outputs_missing": [],
            "leftovers_swept": False,
            "error": None,
        }
        started = time.monotonic()

        def save() -> None:
            atomic_write_json(folder / f"{stem}.json", record)

        # ── 1. open: one trivial turn gives us the thread id ──────────────────────
        open_argv = self.argv_open(request)
        record["open"] = {"argv": open_argv, "exit_code": None, "elapsed_s": None, "usage": None,
                          "timed_out": False}
        open_out, open_err = folder / f"{stem}.open.stdout", folder / f"{stem}.open.stderr"
        try:
            proc = self._spawn(open_argv, stdout=open_out, stderr=open_err, env=env)
        except OSError as exc:
            record.update(state="SPAWN_FAILED", ended_at=_now(), error=f"{type(exc).__name__}: {exc}")
            save()
            return DispatchResult(ok=False, error_class="AGENT_ERROR",
                                  error=f"cannot start {self.codex_bin}: {exc}")
        record.update(state="OPENING", pid=proc.pid, process_started_at=process_probe.started_at(proc.pid))
        save()
        try:
            open_exit = proc.wait(timeout=self.open_timeout_seconds)
        except subprocess.TimeoutExpired:
            confirmed = self._kill_group(proc)
            record["open"].update(exit_code=proc.returncode, timed_out=True,
                                  elapsed_s=round(time.monotonic() - started, 1))
            record.update(state="OPEN_FAILED", timed_out=True, cancel_requested=True,
                          cancel_confirmed=confirmed, ended_at=_now(),
                          elapsed_s=round(time.monotonic() - started, 1),
                          error=f"codex exec open timed out after {self.open_timeout_seconds:.0f}s")
            save()
            # Not the role's budget: the thread never opened → transient, one retry.
            return DispatchResult(ok=False, error_class="CONNECTION", error=record["error"])
        open_events = parse_events(open_out.read_text(encoding="utf-8", errors="replace"))
        record["open"].update(exit_code=open_exit, elapsed_s=round(time.monotonic() - started, 1),
                              usage=open_events["usage"])
        thread_id = open_events["thread_id"]
        if open_exit != 0 or not thread_id:
            error, declared = self._failure(open_exit, open_events,
                                            open_out.read_text(encoding="utf-8", errors="replace"),
                                            open_err.read_text(encoding="utf-8", errors="replace"))
            error = error or "codex exec open: no thread.started event in the --json stream"
            transcript = find_rollout(thread_id, self.sessions_root) if thread_id else None
            record.update(state="OPEN_FAILED", exit_code=open_exit, ended_at=_now(),
                          elapsed_s=round(time.monotonic() - started, 1), error=error,
                          thread_id=thread_id, session_id=thread_id, transcript_path=transcript)
            save()
            return DispatchResult(ok=False, error=error, error_class=declared,
                                  session_ref=thread_id, context_ref=thread_id,
                                  parent_context_ref=request.host_session_ref,
                                  transcript_path=transcript)
        record.update(thread_id=thread_id, session_id=thread_id)
        save()

        # ── 2. bind: session-scoped C4 binding BEFORE the first tool call ─────────
        try:
            access_binding = bind_task_access(request, thread_id, repo_root=self.cwd)
        except (ValueError, OSError, KeyError) as exc:
            record.update(state="BIND_FAILED", ended_at=_now(),
                          elapsed_s=round(time.monotonic() - started, 1),
                          error=f"C4 access binding unavailable: {exc}")
            save()
            return DispatchResult(ok=False, error_class="CONTRACT_ERROR", error=record["error"],
                                  session_ref=thread_id, context_ref=thread_id,
                                  parent_context_ref=request.host_session_ref)
        record["access_binding"] = access_binding
        transport_prompt = request.prompt
        if "read_commands" in access_binding:
            transport_prompt += ("\nC4 已绑定本 headless 线程。以下为精确文件 broker 命令;Write 正文替换占位符,"
                                 "保持单引号 JSON 参数,正文单引号写 Unicode 转义。\n"
                                 + json.dumps({key: access_binding[key] for key in
                                               ("read_commands", "write_commands")}, ensure_ascii=False))

        # ── 3. work: resume the bound thread with the frozen task ─────────────────
        argv = self.argv_work(request, thread_id, transport_prompt, last_message)
        record["argv"] = argv[:-1] + [
            f"<prompt {len(transport_prompt)} chars sha256:"
            f"{sha256_bytes(transport_prompt.encode('utf-8'))[:16]}>"]
        stdout_path, stderr_path = folder / f"{stem}.stdout", folder / f"{stem}.stderr"
        try:
            proc = self._spawn(argv, stdout=stdout_path, stderr=stderr_path, env=env)
        except OSError as exc:
            record.update(state="SPAWN_FAILED", ended_at=_now(), error=f"{type(exc).__name__}: {exc}")
            save()
            return DispatchResult(ok=False, error_class="AGENT_ERROR",
                                  error=f"cannot start {self.codex_bin}: {exc}")
        record.update(state="RUNNING", pid=proc.pid, process_started_at=process_probe.started_at(proc.pid))
        save()
        try:
            exit_code = proc.wait(timeout=float(request.timeout_seconds))
            record["leftovers_swept"] = stop_group(proc.pid, self.kill_grace_seconds)
            if record["leftovers_swept"]:
                record.update(cancel_requested=True, cancel_confirmed=_group_stopped(proc.pid))
        except subprocess.TimeoutExpired:
            confirmed = self._kill_group(proc)
            record.update(state="KILLED" if confirmed is True else "CANCEL_UNCONFIRMED",
                          timed_out=True, exit_code=proc.returncode,
                          cancel_requested=True, cancel_confirmed=confirmed,
                          ended_at=_now(), elapsed_s=round(time.monotonic() - started, 1),
                          transcript_path=find_rollout(thread_id, self.sessions_root),
                          error=f"timeout after {request.timeout_seconds:.0f}s")
            save()
            raise ExecutorTimeout(
                f"{request.task_id} a{request.attempt}: codex exec 超时 "
                f"{request.timeout_seconds:.0f}s,"
                f"{'已确认进程组停止' if confirmed is True else '进程组停止未确认'}"
                f"(thread {thread_id})"
            ) from None
        stdout = stdout_path.read_text(encoding="utf-8", errors="replace")
        stderr = stderr_path.read_text(encoding="utf-8", errors="replace")
        events = parse_events(stdout)
        transcript = find_rollout(thread_id, self.sessions_root)
        record.update(state="EXITED", exit_code=exit_code, ended_at=_now(),
                      elapsed_s=round(time.monotonic() - started, 1),
                      usage=events["usage"], transcript_path=transcript)
        identity = {
            "session_ref": thread_id,
            "context_ref": thread_id,
            "parent_context_ref": request.host_session_ref,
            "transcript_path": transcript,
            "usage": events["usage"],
        }
        error, error_class = self._failure(exit_code, events, stdout, stderr)
        if error is None:
            missing = [path for path in request.output_paths.values() if not Path(path).is_file()]
            if missing:
                record["outputs_missing"] = missing
                error = (f"{request.task_id}: 产物未落盘 {missing}"
                         "(codex exec 退出 0,agent 自称完成不算数)")
                error_class = "CONTRACT_ERROR"
        record["error"] = error
        save()
        if error is not None:
            return DispatchResult(ok=False, error=error, error_class=error_class, **identity)
        return DispatchResult(ok=True, **identity)


__all__ = [
    "DEFAULT_AGENTS_DIR", "HEADLESS_CODEX_TIMEOUTS", "HeadlessCodexExecutor",
    "OPEN_PROMPT", "OPEN_TIMEOUT_S", "codex_child_env", "default_sessions_root",
    "developer_instructions", "find_rollout", "parse_events", "resolve_codex_bin", "toml_value",
]
