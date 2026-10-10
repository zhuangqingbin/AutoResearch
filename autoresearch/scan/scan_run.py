"""无人值守的一场全 A 扫描(launchd 交易日 21:20 → ``scripts/scan_run.sh`` → 本模块)。

design: docs/superpowers/specs/2026-09-26-daily-engine-consolidation-design.md §6 C2/C3。

一条确定性流水,只有推理交给 ``claude -p``(headless 执行器,一个任务一个会话):

1. **锁**:``$CTX/.scan_run.lock``(``scan.run_lock``,fcntl;macOS 无 ``flock(1)``)。被占 →
   立刻退出、打印持锁 pid、推一句「未开」。
2. **窗口**(复审 I4):没带 ``--date`` 且此刻不在 [21:10, 截止] 内 = launchd 在本机睡眠/关机
   后补触发 → 推「扫描 <日> 错过」退出 0(该日已有摘要则静默),不在早上轮询 tushare 占锁一整天。
3. **交易日**:今天不是交易日 → 静默退出 0(launchd 只按星期触发,节假日靠这里挡)。
4. **人工场**:同引擎还有 ACTIVE 且心跳新鲜的 scan-market run(人工会话在扫)→ 不开,推送;
   就绪等待之后、begin 之前**再查一次**。
5. **湖灌齐**:``scan.readiness``(stk_factor_pro ≥5300 且连续两次不变,最多等到 22:30;
   就绪后把预热写下的半载湖分区隔离,扫描重取)。
6. **begin**:``session_agent begin --orchestration session_v1 --ignore-scan-lock``(本进程
   就是持锁者),请求由 :func:`build_headless_request` 生成(``session_ref=headless-<uuid>``)。
7. **runner**:``session_agent run --executor headless``(阶段级重试一次是 runner 的
   TIMEOUT 语义);墙钟 = min(``--run-timeout-minutes``, 距夜间硬截止 ``--hard-stop``
   (缺省 01:00,只管定时场))—— 硬截止前不足 10 分钟就不 begin。
8. 成功 → ``verify-report --level full`` → 送达 brief(``scan.delivery``),摘要
   ``result=FINISHED`` + 独立的 ``delivery.status``;未完成 → 停在飞 ``claude -p`` →
   可恢复容量/组装/发布失败保留 run，其他失败 ``capsule finalize FAILED``。
   **不自动改代码、不自动重跑第二场。**

收尾不留孤儿(复审 M1/M7):SIGTERM/SIGHUP/SIGINT(``launchctl bootout``、``kickstart -k``、
关终端)→ 杀 runner 进程组、按调用记录停在飞 ``claude -p``(TERM→5 s→KILL)、finalize
FAILED、推送;begin 之后任何意外异常同样收口。送达渠道是 ``none`` 时开头与结尾各打一行提醒
(复审 M8)。

日志 ``$RPT/_ops/scan_run_<日>.log``;每场一份摘要 ``$RPT/_ops/scan_run_<交易日>.json``。

  scripts/scan_run.sh                       # launchd / 手动触发(等湖灌齐)
  scripts/scan_run.sh --date 2026-09-28 --skip-readiness
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import signal
import subprocess
import sys
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, time as clock_time, timedelta, timezone
from pathlib import Path

from autoresearch.common import workspace as ws
from autoresearch.common.atomic import atomic_write_json
from autoresearch.scan import readiness, run_lock

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
#: 同引擎另有 ACTIVE 且心跳在此窗口内的 scan run = 人工会话还在扫。
LIVE_RUN_WINDOW = timedelta(minutes=90)
RUN_TIMEOUT_MINUTES = 180
#: 定时场的合法开跑窗口起点(launchd 21:20;稍早留给手动 kickstart)。终点 = readiness 截止。
WINDOW_START = "21:10"
#: 定时场的夜间硬截止(次日凌晨):runner 墙钟不越过它,不足 MIN_RUNNER_MINUTES 就不开。
HARD_STOP = "01:00"
MIN_RUNNER_MINUTES = 10
#: 停在飞 ``claude -p`` / runner 进程组:TERM 后等多久再 KILL。
KILL_GRACE_S = 5.0
SUBPROCESS_TIMEOUT_S = 1800


def runner_cfg(cfg: dict | None = None) -> dict:
    """`scan_config.runner`:无人值守场的全部时钟(缺键 = 模块常量)。"""
    from autoresearch.scan.user_config import knob
    return {"window_start": str(knob("runner", "window_start", None, WINDOW_START, cfg)),
            "hard_stop": str(knob("runner", "hard_stop", None, HARD_STOP, cfg)),
            "min_runner_minutes": int(knob("runner", "min_runner_minutes", None, MIN_RUNNER_MINUTES, cfg)),
            "run_timeout_minutes": float(knob("runner", "run_timeout_minutes", None, RUN_TIMEOUT_MINUTES, cfg)),
            "live_run_window": timedelta(minutes=int(knob("runner", "live_run_window_min", None, 90, cfg))),
            "kill_grace_s": float(knob("runner", "kill_grace_s", None, KILL_GRACE_S, cfg)),
            "subprocess_timeout_s": int(knob("runner", "subprocess_timeout_s", None, SUBPROCESS_TIMEOUT_S, cfg)),
            "force_full": bool(knob("runner", "force_full", None, False, cfg))}


NO_CHANNEL_WARNING = ("⚠ 送达渠道 = none(scan_config.jsonc delivery.channel):本场任何推送 —— "
                      "含 FAILED / 未开 / 错过 —— 都不会发出,只看日志与摘要")
VERIFY_KEYS = ("report_covered", "publication_ok", "orchestration_verified", "completeness_ok")
HEADLESS_EVIDENCE = (
    "headless-probe:docs/research/2026-09-26-headless-driver-probes.md",
    "headless-executor:autoresearch/session_agent/executors/headless_claude.py",
    "headless-executor:autoresearch/session_agent/executors/headless_codex.py",
)
HEADLESS_ENGINES = ("claude", "codex")


def ops_dir() -> Path:
    return ws.reports_root() / "_ops"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class OpsLog:
    """带时刻的日志行:同时写 ``$RPT/_ops/scan_run_<日>.log`` 与 stdout(launchd 的 /tmp 日志)。"""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = self.path.open("a", encoding="utf-8")

    def line(self, text: str) -> None:
        row = f"[scan-run] {datetime.now():%F %T} {text}"
        print(row, flush=True)
        self.stream.write(row + "\n")
        self.stream.flush()

    def close(self) -> None:
        self.stream.close()


def build_headless_request(date: str, *, session_ref: str | None = None,
                           engine: str | None = None) -> dict:
    """session_v1 scan 请求;宿主 = 本无人值守进程 + 每个推理任务一个 ``claude -p`` 会话。

    能力如实声明:确定性步骤在 runner 进程内跑(``deterministic_exec``),推理经 headless
    执行器交接(``inference_handoff``)且每个任务一个独立顶级会话(``independent_context``,
    进程边界即上下文边界),``--agent l4-intel`` 能联网(探针 ④)。执行器不能重挂在飞的
    ``claude -p``(``safe_resume=false``)。没有主会话 transcript,所以不给 ``transcript-file:``。
    """
    return {
        "schema_version": 4,
        "kind": "scan-market",
        "requested_mode": "AUTO",
        "analysis_date": date,
        "subject": None,
        "peers": [],
        "asset_type": None,
        "name": None,
        # 📌 持仓由确定性判据 SENTINEL_PINNED 兜住;无人值守不做人工 override。
        "force_full": runner_cfg()["force_full"],
        "host_profile": {
            "schema_version": 1,
            # 2026-10-08:引擎 = 本进程的工作区引擎(scan_run.sh 导出);codex 场每个推理任务一个
            # `codex exec` 线程(executors.headless_codex),证据链与 claude 场同形。
            "engine": engine or ws.ENGINE,
            "session_ref": session_ref or f"headless-{uuid.uuid4()}",
            "deterministic_exec": True,
            "capture_binding": True,
            "inference_handoff": True,
            "safe_resume": False,
            "independent_context": True,
            "native_dispatch": True,
            "web_search": True,
            "web_fetch": True,
            "observed_model": None,
            "observed_effort": None,
            "evidence_refs": list(HEADLESS_EVIDENCE),
        },
        "predecessor_run_id": None,
        # 与交互样例 docs/session-agent/examples/scan.request.json 同一份契约(测试锁同源):
        # v3 起 DecisionFrame 带日历来源证据,v4 起宏观/行业候选 profile 显式取缺省。
        "card_research_profile": "single-stage-v1",
        "research_context": {"venue": "XSHG", "usage": "scan", "calendar_source_path": None},
        "macro_research_profile": "serial21",
        "macro_optional_products": [],
        "sector_brief_profile": "legacy",
    }


def live_scan_runs(*, now: datetime | None = None,
                   window: timedelta | None = None) -> list[dict]:
    """同引擎 ACTIVE 的 scan-market run 中,持有者进程还活着或心跳在 ``window`` 内的那些。"""
    window = runner_cfg()["live_run_window"] if window is None else window
    from autoresearch.trace import process_probe

    stamp = now or datetime.now(timezone.utc)
    root = ws.context_root() / ws.RUN_SPOOLS["scan-market"]
    found = []
    if not root.is_dir():
        return found
    for workspace in sorted(root.iterdir()):
        state_path = workspace / "state.json"
        if workspace.is_symlink() or not state_path.is_file():
            continue
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if state.get("business_status") != "ACTIVE":
            continue
        lease = state.get("lease") if isinstance(state.get("lease"), dict) else {}
        beat = str(lease.get("heartbeat") or state.get("updated_at") or "")
        try:
            age = stamp - datetime.fromisoformat(beat.replace("Z", "+00:00"))
        except ValueError:
            age = None
        lease_live = isinstance(lease.get("pid"), int) and process_probe.matches(lease)
        if lease_live or (age is not None and age < window):
            found.append({
                "run_id": str(state.get("run_id") or workspace.name),
                "age_minutes": None if age is None else round(age.total_seconds() / 60, 1),
                "lease_live": lease_live,
            })
    return found


def locate_brief(run_id: str, canonical: str | None) -> Path | None:
    """兼容报告目录里的 brief(由发布器写的 ``delivery.json`` 认领本 run);没有则 canonical。"""
    base = ws.run_reports_root("scan-market")
    if base.is_dir():
        for folder in sorted(base.iterdir()):
            if (not folder.is_dir() or folder.name == "runs"
                    or folder.name.startswith(("_", "."))):
                continue
            sidecar = folder / "delivery.json"
            if not sidecar.is_file():
                continue
            try:
                owner = json.loads(sidecar.read_text(encoding="utf-8")).get("run_id")
            except (OSError, json.JSONDecodeError, AttributeError):
                continue
            if owner == run_id and (folder / "brief.md").is_file():
                return folder / "brief.md"
    return Path(canonical) / "brief.md" if canonical else None


def failure_point(outcome: dict | None) -> tuple[str, str]:
    """``(阶段, 一句原因)``:优先带 task_id 的错误,其次最后一次失败派发。"""
    if not outcome:
        return "runner", "runner 没有交回 outcome(进程崩溃或被杀)"
    errors = [item for item in outcome.get("errors") or [] if isinstance(item, dict)]
    for item in errors:
        if item.get("task_id"):
            code = item.get("code")
            text = f"{code}: {item.get('message')}" if code else str(item.get("message"))
            return str(item["task_id"]), text[:240]
    failed = [item for item in outcome.get("dispatches") or []
              if str(item.get("outcome", "")).startswith("FAILED")]
    if failed:
        return str(failed[-1].get("task_id")), str(failed[-1].get("outcome"))
    if errors:
        return "runner", str(errors[0].get("message") or errors[0])[:240]
    return "runner", str(outcome.get("stop_reason") or "unknown")


def _no_sweep(run_id: str) -> list[int]:
    return []


def _no_missed(now: datetime) -> str | None:
    return None


def _unknown_channel() -> str:
    return "unknown"


def _no_redline(run_id: str) -> dict:
    return {"verdict": "SKIPPED"}


def _no_breaker() -> dict | None:
    return None


def _no_ack(run_id: str):
    return None


def _no_replay_gate() -> dict:
    return {"verdict": "SKIPPED"}


def _resume_unavailable(run_id: str) -> str:
    raise RuntimeError("resume validation is unavailable")


def _recoverable_tasks(run_id: str) -> list[dict]:
    """Read durable failure facts after a killed runner; no task state is changed."""
    root = ws.find_run_root(run_id)
    if root is None:
        return []
    try:
        payload = json.loads((root / "session/tasks.json").read_text())
    except (OSError, ValueError):
        return []
    if payload.get("run_id") != run_id or payload.get("engine") != ws.ENGINE:
        return []
    rows = []
    for task_id, entry in payload.get("tasks", {}).items():
        code = (entry.get("error") or {}).get("code")
        if entry.get("state") == "FAILED" and (code == "USAGE_LIMIT" or
                (entry["spec"].get("operation") == "scan.assemble"
                 and code in {"OPERATION_ERROR", "OPERATION_FAILED"})):
            rows.append({"task_id": task_id, "attempt": entry["attempt"], "error_class": code})
    return rows


def _validate_resume(run_id: str):
    from autoresearch.trace.capsule import load_run
    handle = load_run(run_id)
    if handle.engine != ws.ENGINE or handle.contract.run_kind != "scan-market":
        raise ValueError("resume requires a same-engine scan-market run")
    state = json.loads((Path(handle.workspace) / "state.json").read_text())
    if state["business_status"] != "ACTIVE":
        from autoresearch.trace.publication import load_publication_journal
        if state["business_status"] != "SUCCEEDED" or load_publication_journal(
                handle.workspace)["state"] not in {"VIEWS_APPLIED", "COMMITTED"}:
            raise RuntimeError("resume requires ACTIVE run or resumable publication transaction")
    request = json.loads((Path(handle.workspace) / "session/request.json").read_text())
    host = request["host_profile"]
    if host["engine"] != ws.ENGINE or not host["session_ref"].startswith("headless-"):
        raise ValueError("resume requires the original headless host profile")
    lock = Path(handle.staging) / "_dispatch/runner.lock"
    if lock.is_file():
        with lock.open("r") as stream:
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError("resume refused: this run still has a live runner") from None
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
    return handle


@dataclass
class Steps:
    """每一个外部动作一个可注入的步骤(测试里全换成假的:不取数、不起 claude、不推送)。"""

    resolve_date: Callable[[str | None], str | None]
    live_runs: Callable[[], list[dict]]
    wait_ready: Callable[[str, str], bool]
    begin: Callable[[Path], str]
    run: Callable[[str, float], dict | None]       # (run_id, 墙钟秒)
    verify: Callable[[str, str], dict]
    locate_brief: Callable[[str, str | None], Path | None]
    deliver: Callable[..., dict]
    finalize_failed: Callable[[str, dict], None]
    notify: Callable[[str, str], dict]
    #: 停该 run 在飞的 ``claude -p``(按调用记录);返回被停的进程组。
    sweep: Callable[[str], list[int]] = _no_sweep
    now: Callable[[], datetime] = datetime.now
    #: 窗口外触发时「错过的是哪个交易日」(最近已结算交易日);不可知 → None。
    missed_date: Callable[[datetime], str | None] = _no_missed
    #: 当前送达渠道(``scan_config`` delivery.channel)。
    channel: Callable[[], str] = _unknown_channel
    #: Validate engine, headless owner, frozen request and resumable business state.
    resume: Callable[[str], str] = _resume_unavailable
    #: 场后真计量红线(``scan.redline.post_run``):本场判定,FAIL 写断路器;绝不改退出码。
    redline: Callable[[str], dict] = _no_redline
    #: 未确认的断路器(上一场 redline FAIL)→ 本场开场拒开。
    breaker: Callable[[], dict | None] = _no_breaker
    #: ``--ack-redline <run_id>``:确认断路器后放行。
    ack_breaker: Callable[[str], object] = _no_ack
    #: 推理前零推理回放门(``scan.replay_gate.gate``):上一场已接受产物过当前校验器。
    replay_gate: Callable[[], dict] = _no_replay_gate


class ScanRunInterrupted(BaseException):
    """SIGTERM / SIGHUP / SIGINT 打断了本场(launchctl bootout、kickstart -k、关终端、Ctrl-C)。

    继承 ``BaseException``:各步骤里「通知失败不改结果」一类的 ``except Exception`` 吞不掉它。
    """

    def __init__(self, signum: int):
        super().__init__(signum)
        self.signum = int(signum)
        self.name = signal.Signals(signum).name


INTERRUPT_SIGNALS = (signal.SIGTERM, signal.SIGHUP, signal.SIGINT)


def install_interrupt_handlers() -> dict:
    """第一个打断信号抛 :class:`ScanRunInterrupted`;收尾期间的后续信号忽略(让收尾做完)。"""
    fired: list[int] = []

    def handler(signum, frame):
        if fired:
            return
        fired.append(signum)
        raise ScanRunInterrupted(signum)

    return {sig: signal.signal(sig, handler) for sig in INTERRUPT_SIGNALS}


def restore_handlers(previous: dict) -> None:
    for sig, handler in previous.items():
        signal.signal(sig, handler)


def _at(hhmm: str) -> clock_time:
    hour, minute = (int(part) for part in hhmm.split(":"))
    return clock_time(hour, minute)


def in_window(now: datetime, deadline: str, start: str | None = None) -> bool:
    """定时场的合法开跑时段 [start, deadline](本地时刻;截止那一整分钟仍算在内)。"""
    start = runner_cfg()["window_start"] if start is None else start
    return _at(start) <= now.time() and now.replace(second=0, microsecond=0).time() <= _at(deadline)


def hard_stop_after(started: datetime, hhmm: str | None = None) -> datetime:
    """``started`` 之后第一次到达的 HH:MM(21:20 起跑 → 次日 01:00)。"""
    hhmm = runner_cfg()["hard_stop"] if hhmm is None else hhmm
    stop = datetime.combine(started.date(), _at(hhmm))
    return stop if stop > started else stop + timedelta(days=1)


# ── default steps (subprocess boundary) ──────────────────────────────────────────

def _stop_process_group(proc: subprocess.Popen, grace: float = 10.0) -> None:
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            break
        try:
            proc.wait(timeout=grace)
            break
        except subprocess.TimeoutExpired:
            continue


def _call(argv: list[str], *, env: dict, timeout: float | None,
          stderr=None) -> tuple[int | None, str]:
    """跑一个子进程(独立进程组);超时杀整组。返回 ``(returncode|None=超时, stdout)``。

    本进程被打断(:class:`ScanRunInterrupted` 等)时同样先杀整组再上抛 —— 子进程在自己的
    会话里,launchd 杀本进程不会连带它们。
    """
    proc = subprocess.Popen(  # noqa: S603 - argv list, no shell
        argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=stderr, text=True,
        env=env, start_new_session=True)
    try:
        out, _ = proc.communicate(timeout=timeout)
        return proc.returncode, out or ""
    except subprocess.TimeoutExpired:
        _stop_process_group(proc)
        try:
            out, _ = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            out = ""
        return None, out or ""
    except BaseException:
        _stop_process_group(proc, grace=runner_cfg()["kill_grace_s"])
        raise


def _last_json(text: str) -> dict | None:
    for line in reversed([row for row in text.splitlines() if row.strip()]):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        return value if isinstance(value, dict) else None
    return None


def _session_agent(*args: str) -> list[str]:
    return [sys.executable, "-m", "autoresearch.session_agent", *args]


def _env(run_id: str | None = None, *, engine: str | None = None) -> dict:
    env = {key: value for key, value in os.environ.items() if key != "AUTORESEARCH_RUN_ID"}
    env["AUTORESEARCH_ENGINE"] = engine or ws.ENGINE
    if run_id:
        env["AUTORESEARCH_RUN_ID"] = run_id
    return env


def terminate_inflight(records_dir: Path | str, *, grace: float | None = None) -> list[int]:
    """Stop every headless call still recorded as STARTING/RUNNING (its own process group).

    The runner's ``claude -p`` children live in their own sessions (``start_new_session``),
    so killing the runner does not stop them.  The executor
    (``session_agent.executors.headless_claude``) records ``state`` + ``pid`` (= pgid) +
    ``process_started_at`` before it waits; scan sits below session_agent in the layering,
    so the record is read as data through its registered path, never by importing the
    executor.  All groups get SIGTERM at once, then whatever is left after ``grace`` seconds
    gets SIGKILL (review M1b); a pid twin (same pid, other start token) is never signalled.
    """
    grace = runner_cfg()["kill_grace_s"] if grace is None else grace
    from autoresearch.trace import process_probe

    signalled = []
    for path in sorted(Path(records_dir).glob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(record, dict) or record.get("state") not in {"STARTING", "RUNNING"}:
            continue
        if not process_probe.owns_group(record):
            continue
        try:
            os.killpg(record["pid"], signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            continue
        signalled.append(record["pid"])
    deadline = time.monotonic() + max(0.0, float(grace))
    while signalled and time.monotonic() < deadline:
        if not any(process_probe.group_alive(pid) for pid in signalled):
            break
        time.sleep(0.05)
    for pid in signalled:
        if process_probe.group_alive(pid):
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(pid, signal.SIGKILL)
    return signalled


def _terminate_inflight_headless(run_id: str) -> list[int]:
    from autoresearch.contracts import artifacts as registry

    root = ws.find_run_root(run_id)
    if root is None:
        return []
    records = Path(registry.by_name("dispatch_headless_calls").path).parent
    killed = []
    for folder in sorted(root.glob(f"staging/*/{records.as_posix()}")):
        killed += terminate_inflight(folder)
    return killed


def default_steps(args, log: OpsLog | None) -> Steps:
    stream = log.stream if log is not None else None
    emit = log.line if log is not None else (lambda text: print(text, flush=True))
    claude_bin = getattr(args, "claude_bin", None)
    codex_bin = getattr(args, "codex_bin", None)
    max_parallel = getattr(args, "max_parallel", None)

    def missed_date(now: datetime) -> str | None:
        from autoresearch.scan.trade_date import resolve_scan_date

        return resolve_scan_date(None, now=now)         # 最近已结算交易日

    def channel() -> str:
        from autoresearch.scan import delivery

        return str(delivery.configured_delivery()["channel"])

    def resolve_date(explicit: str | None) -> str | None:
        from autoresearch.scan.trade_date import NotATradingDay, resolve_scan_date

        if explicit:
            return resolve_scan_date(explicit)          # 非交易日 → NotATradingDay(用法错)
        try:
            return resolve_scan_date(datetime.now().strftime("%Y-%m-%d"))
        except NotATradingDay:
            return None

    def wait_ready(date: str, deadline: str) -> bool:
        # tushare 灌齐 + 湖分区对账(预热半载分区隔离,扫描重取)—— 复审 I1
        return readiness.wait_and_guard(date, deadline=deadline, log=emit)

    def begin(request_path: Path) -> str:
        # 本进程就是扫描锁的持有者:begin 的锁检查(复审 I3)对它显式放行。
        code, out = _call(_session_agent("begin", "--orchestration", "session_v1",
                                         "--request-file", str(request_path),
                                         "--ignore-scan-lock", "--executor", "headless"),
                          env=_env(), timeout=runner_cfg()["subprocess_timeout_s"], stderr=stream)
        doc = _last_json(out) or {}
        if code != 0 or not doc.get("run_id"):
            detail = "; ".join(str(item.get("message")) for item in doc.get("errors") or []
                               if isinstance(item, dict)) or out.strip()[:300]
            raise RuntimeError(f"session_agent begin exit={code}: {detail}")
        return str(doc["run_id"])

    def resume(run_id: str) -> str:
        return _validate_resume(run_id).analysis_date

    def run(run_id: str, timeout_s: float) -> dict | None:
        if getattr(args, "resume_run_id", None):
            try:
                handle = _validate_resume(run_id)
            except RuntimeError as exc:
                if "live runner" in str(exc):
                    return {"finished": False, "stop_reason": "RUNNER_BUSY",
                            "errors": [{"message": str(exc)}]}
                raise
            state = json.loads((Path(handle.workspace) / "state.json").read_text())
            command = "finish" if state["business_status"] != "ACTIVE" else "resume"
            code, out = _call(_session_agent(command, "--run-id", run_id), env=_env(run_id),
                              timeout=timeout_s, stderr=stream)
            if command == "finish" or code != 0:
                result = _last_json(out) or {}
                publication = (result.get("result") or {}).get("publication") or {}
                ok = code == 0 and bool(publication.get("canonical_path"))
                return {"finished": ok, "stop_reason": "FINISHED" if ok else "FINISH_FAILED",
                        "recoverable": not ok, "errors": result.get("errors", []),
                        "finish": {"canonical_path": publication.get("canonical_path")}}
        argv = _session_agent("run", "--run-id", run_id, "--executor", "headless")
        if claude_bin and ws.ENGINE == "claude":
            argv += ["--claude-bin", str(claude_bin)]
        if codex_bin and ws.ENGINE == "codex":
            argv += ["--codex-bin", str(codex_bin)]
        if max_parallel:
            argv += ["--max-parallel", str(max_parallel)]
        code, out = _call(argv, env=_env(run_id), timeout=timeout_s, stderr=stream)
        if code is None:
            killed = _terminate_inflight_headless(run_id)
            recoveries = _recoverable_tasks(run_id)
            return {"finished": False, "stop_reason": "RUN_TIMEOUT", "errors": [{
                "message": f"runner 超过 {timeout_s / 60:.0f} 分钟未结束,已终止"
                           f"(连同 {len(killed)} 个在飞 claude -p)"}],
                    "recoverable": bool(recoveries), "recovery_tasks": recoveries}
        return _last_json(out)

    def verify(canonical: str, run_id: str) -> dict:
        code, out = _call(_session_agent("verify-report", "--report-path", canonical,
                                         "--expected-run-id", run_id, "--level", "full"),
                          env=_env(), timeout=runner_cfg()["subprocess_timeout_s"], stderr=stream)
        return _last_json(out) or {"error": f"verify-report exit={code}"}

    def deliver(brief: Path, *, run_id: str, title: str, report_path: str | None) -> dict:
        from autoresearch.scan import delivery

        sealed = ws.run_reports_root("scan-market") / "runs"
        try:
            inside_canonical = Path(brief).resolve().is_relative_to(sealed.resolve())
        except OSError:
            inside_canonical = False
        record = ops_dir() / f"scan_run_{run_id}.delivery.json" if inside_canonical else None
        return delivery.send(brief, run_id=run_id, title=title, report_path=report_path,
                             record_path=record)

    def finalize_failed(run_id: str, error: dict) -> None:
        code, out = _call([sys.executable, "-m", "autoresearch.trace.capsule", "finalize", run_id,
                           "--business-status", "FAILED",
                           "--error-json", json.dumps(error, ensure_ascii=False)],
                          env=_env(), timeout=runner_cfg()["subprocess_timeout_s"], stderr=stream)
        emit(f"capsule finalize FAILED exit={code} {out.strip()[:300]}")

    def notify(title: str, body: str) -> dict:
        from autoresearch.scan import delivery

        return delivery.notify(title, body)

    def redline_post(run_id: str) -> dict:
        # 会话层入口(先算冻结任务图的消费图);scan 不 import session_agent,与 begin / run 同走子进程。
        code, out = _call([sys.executable, "-m", "autoresearch.session_agent.redline_post", "--run-id", run_id],
                          env=_env(), timeout=runner_cfg()["subprocess_timeout_s"], stderr=stream)
        return _last_json(out) or {"verdict": "ERROR", "error": f"redline_post exit={code}"}

    def breaker() -> dict | None:
        from autoresearch.scan import redline

        return redline.active_breaker()

    def ack_breaker(run_id: str):
        from autoresearch.scan import redline

        return redline.acknowledge(run_id, reason="scan_run --ack-redline")

    def replay_gate_step() -> dict:
        # 回放门要读会话层的任务库与校验器:子进程调用;FAIL 时它退出 1,结论以 JSON 为准。
        code, out = _call([sys.executable, "-m", "autoresearch.session_agent.replay_gate", "--json-line"],
                          env=_env(), timeout=runner_cfg()["subprocess_timeout_s"], stderr=stream)
        return _last_json(out) or {"verdict": "ERROR", "error": f"replay_gate exit={code}"}

    return Steps(resolve_date=resolve_date, live_runs=live_scan_runs, wait_ready=wait_ready,
                 begin=begin, run=run, verify=verify, locate_brief=locate_brief,
                 deliver=deliver, finalize_failed=finalize_failed, notify=notify,
                 sweep=_terminate_inflight_headless, now=datetime.now,
                 missed_date=missed_date, channel=channel, resume=resume,
                 redline=redline_post, breaker=breaker, ack_breaker=ack_breaker,
                 replay_gate=replay_gate_step)


# ── orchestration ───────────────────────────────────────────────────────────────

def run_once(args, steps: Steps, *, log: OpsLog) -> int:
    started = _now_iso()
    summary: dict = {"schema_version": 1, "started_at": started, "log": str(log.path)}
    #: ``run_id`` 一旦 begin 成功就在这里;``stage`` = 正在做的那一步(意外时报给人看)。
    state: dict = {"run_id": None, "stage": "start"}
    try:
        channel = str(steps.channel())
    except Exception as exc:  # noqa: BLE001 - an unreadable config is itself worth a line
        channel = f"unknown({type(exc).__name__})"
    summary["delivery_channel"] = channel
    if channel == "none":
        summary["warnings"] = [NO_CHANNEL_WARNING]

    def finish(code: int, result: str, **fields) -> int:
        summary.update(fields, result=result, exit_code=code, ended_at=_now_iso())
        if summary.get("date"):
            atomic_write_json(ops_dir() / f"scan_run_{summary['date']}.json", summary)
        if channel == "none":
            log.line(NO_CHANNEL_WARNING)
        log.line(f"end · {result} · exit {code}")
        return code

    def tell(title: str, body: str) -> None:
        try:
            result = steps.notify(title, body)
        except Exception as exc:  # noqa: BLE001 - notification never changes the outcome
            result = {"status": "FAILED", "error": f"{type(exc).__name__}: {exc}"}
        log.line(f"notify · {title} · {result.get('status')}")

    def abort(stage: str, reason: str, result: str, stop: str, *,
              recoverable: bool = False, recovery_tasks: list | None = None) -> int:
        """Stop in-flight work; preserve recoverable runs, seal other failures."""
        run_id = state["run_id"]
        if run_id and not recoverable:
            recovery_tasks = _recoverable_tasks(run_id)
            recoverable = bool(recovery_tasks)
        if run_id:
            try:
                stopped = steps.sweep(run_id)
            except Exception as exc:  # noqa: BLE001 - cleanup must go on
                stopped = []
                log.line(f"停在飞 claude -p 失败:{type(exc).__name__}: {exc}")
            if stopped:
                log.line(f"已停 {len(stopped)} 个在飞 claude -p 进程组")
            if not recoverable:
                try:
                    steps.finalize_failed(run_id, {"stage": stage, "stop_reason": stop,
                                                   "message": reason, "log": str(log.path)})
                except Exception as exc:  # noqa: BLE001
                    log.line(f"capsule finalize FAILED 失败:{type(exc).__name__}: {exc}")
                record_redline(run_id)
        if recoverable:
            summary.update(recovery_tasks=recovery_tasks or [],
                           resume_command=f"scripts/scan_run.sh --engine {ws.ENGINE} --resume-run-id {run_id}")
            result = "RECOVERABLE"
            log.line("保留 ACTIVE run 与已接受产物;恢复前逐个 recover-task 授权失败 attempt")
        tell(f"扫描 {summary.get('date') or '?'} {'RECOVERABLE' if recoverable else 'FAILED'}",
             f"阶段 {stage} · {reason}" + (f" · run {run_id}" if run_id else "")
             + f" · 日志 {log.path}")
        return finish(EXIT_FAILED, result, stage=stage, reason=reason, stop_reason=stop)

    def record_redline(run_id: str) -> None:
        """场后真计量:失败的 run 研究也付过钱,同样要量;任何异常只记一行。"""
        try:
            result = steps.redline(run_id)
        except Exception as exc:  # noqa: BLE001 - the readout never changes the outcome
            result = {"verdict": "ERROR", "error": f"{type(exc).__name__}: {exc}"[:300]}
        summary["redline"] = result
        log.line(f"redline · {result.get('verdict')}"
                 + (f" · {', '.join(result.get('findings') or [])}" if result.get("findings") else "")
                 + (f" · {result.get('error')}" if result.get("error") else ""))

    log.line("start")
    if channel == "none":
        log.line(NO_CHANNEL_WARNING)
    try:
        return _flow(args, steps, log=log, summary=summary, state=state,
                     finish=finish, tell=tell, abort=abort, record_redline=record_redline)
    except ScanRunInterrupted as exc:
        return abort(state["stage"], f"收到 {exc.name}(launchctl bootout / kickstart -k / "
                     "关机 / 关终端),已停 runner 与在飞 claude -p", "INTERRUPTED", "SIGNAL")
    except Exception as exc:  # noqa: BLE001 - unattended failure must not be silent (R8)
        return abort(state["stage"], f"{type(exc).__name__}: {exc}"[:300], "FAILED",
                     "EXCEPTION")


def _flow(args, steps: Steps, *, log: OpsLog, summary: dict, state: dict,
          finish, tell, abort, record_redline=lambda run_id: None) -> int:
    explicit = getattr(args, "date", None)
    resume_id = getattr(args, "resume_run_id", None)
    if resume_id:
        state["stage"] = "resume_validation"
        resumed_date = steps.resume(resume_id)
        if explicit and explicit != resumed_date:
            raise ValueError("--date conflicts with resumed run")
        explicit = resumed_date
    deadline = getattr(args, "deadline", readiness.DEADLINE)
    fired = steps.now()
    if not explicit and not in_window(fired, deadline):
        return _out_of_window(steps, fired, deadline, log=log, summary=summary,
                              finish=finish, tell=tell)

    state["stage"] = "trade_date"
    try:
        date = steps.resolve_date(explicit)
    except Exception as exc:  # noqa: BLE001
        from autoresearch.scan.trade_date import NotATradingDay

        if isinstance(exc, NotATradingDay):
            log.line(f"显式日期不是交易日:{exc}")
            return finish(EXIT_USAGE, "BAD_DATE")
        log.line(f"交易日历不可用:{type(exc).__name__}: {exc}")
        tell("扫描未开", f"交易日历不可用:{exc} · 日志 {log.path}")
        return finish(EXIT_FAILED, "CALENDAR_FAILED")
    if date is None:
        log.line("今天不是交易日,静默退出")
        return finish(EXIT_OK, "NOT_TRADING_DAY")
    summary["date"] = date
    log.line(f"date={date}")

    def live_manual_run() -> int | None:
        live = [item for item in steps.live_runs() if item["run_id"] != resume_id]
        if not live:
            return None
        ids = ", ".join(item["run_id"] for item in live)
        log.line(f"人工会话的扫描 run 仍在跑:{ids}")
        tell(f"扫描 {date} 未开", f"人工会话的扫描 run {ids} 仍在跑 · 日志 {log.path}")
        return finish(run_lock.EXIT_HELD, "LIVE_RUN", live_runs=live)

    state["stage"] = "live_runs"
    held = live_manual_run()
    if held is not None:
        return held

    # 断路器(token 防膨胀 M4):上一场 redline FAIL 且未确认 → 不开新场(恢复原 run 不受限)。
    # 恢复一个被场中前导守卫停下的 run:同一个 run_id 的 --ack-redline 让守卫让路。
    if resume_id and getattr(args, "ack_redline", None) == resume_id:
        steps.ack_breaker(resume_id)
        log.line(f"恢复前确认红线:--ack-redline {resume_id}")
    if not resume_id:
        state["stage"] = "redline_breaker"
        breaker = steps.breaker()
        ack = getattr(args, "ack_redline", None)
        if breaker is not None and ack and ack == breaker.get("run_id"):
            steps.ack_breaker(ack)
            log.line(f"断路器已确认:--ack-redline {ack}")
            breaker = None
        if breaker is not None:
            reasons = ", ".join(str(item.get("code")) for item in breaker.get("findings") or []) or "?"
            log.line(f"上一场 {breaker.get('run_id')} redline FAIL({reasons}),断路器未确认,不开新场")
            tell(f"扫描 {date} 未开",
                 f"上一场 {breaker.get('run_id')} redline FAIL({reasons})· 看 {breaker.get('readout')} 后"
                 f" scripts/scan_run.sh --engine {ws.ENGINE} --ack-redline {breaker.get('run_id')} · 日志 {log.path}")
            return finish(EXIT_FAILED, "REFUSED_BUDGET", stage="redline_breaker",
                          breaker={key: breaker.get(key) for key in ("run_id", "readout", "findings")})

        # 推理前零推理回放(红线 R1):上一场已接受的研究产物过一遍当前校验器;回归 = 不花这场钱。
        state["stage"] = "replay_gate"
        try:
            gate = steps.replay_gate()
        except Exception as exc:  # noqa: BLE001 - a broken gate must not become a new way to lose a night
            gate = {"verdict": "ERROR", "error": f"{type(exc).__name__}: {exc}"[:300]}
        summary["replay_gate"] = {"verdict": gate.get("verdict"), "run_id": gate.get("run_id"),
                                  "checked": gate.get("checked"),
                                  "regressions": [row.get("task_id") for row in gate.get("regressions") or []]}
        log.line(f"回放门 · {gate.get('verdict')} · run {gate.get('run_id')} · 重验 {gate.get('checked')}"
                 + (f" · {gate.get('error')}" if gate.get("error") else ""))
        if gate.get("verdict") == "FAIL":
            replayed = gate.get("run_id")
            if ack and ack == replayed:
                try:
                    steps.ack_breaker(replayed)
                except Exception as exc:  # noqa: BLE001
                    log.line(f"确认回放门失败:{type(exc).__name__}: {exc}")
                log.line(f"回放门回归已确认:--ack-redline {replayed}")
            else:
                first = (gate.get("regressions") or [{}])[0]
                tell(f"扫描 {date} 未开",
                     f"回放门:当前代码拒绝了上一场 {replayed} 已接受的 {len(gate['regressions'])} 份产物"
                     f"(如 {first.get('task_id')}:{first.get('reason')})· 修好或看过后"
                     f" --ack-redline {replayed} · 日志 {log.path}")
                return finish(EXIT_FAILED, "REFUSED_REPLAY", stage="replay_gate")

    state["stage"] = "readiness"
    if not resume_id and not getattr(args, "skip_readiness", False) and not steps.wait_ready(date, deadline):
        tell(f"扫描 {date} 未开",
             f"tushare stk_factor_pro 截至 {deadline} 未灌齐 · 日志 {log.path}")
        return finish(EXIT_FAILED, "NOT_READY")
    # 就绪等待可长达 70 分钟:begin 前再问一次人工场(复审 I3)。
    state["stage"] = "live_runs"
    held = live_manual_run()
    if held is not None:
        return held

    # 夜间硬截止(复审 M2):只管定时场;显式 --date 的补跑只受 --run-timeout-minutes 约束。
    rc = runner_cfg()
    _cap_arg = getattr(args, "run_timeout_minutes", None)
    run_cap = float(rc["run_timeout_minutes"] if _cap_arg is None else _cap_arg) * 60
    stop_at = None if explicit else hard_stop_after(fired, getattr(args, "hard_stop", None) or rc["hard_stop"])
    if stop_at is not None:
        summary["hard_stop"] = stop_at.isoformat(timespec="minutes")
        if (stop_at - steps.now()).total_seconds() < rc["min_runner_minutes"] * 60:
            log.line(f"已过/逼近夜间硬截止 {stop_at:%m-%d %H:%M},不再 begin")
            tell(f"扫描 {date} 未开",
                 f"已过夜间硬截止 {stop_at:%H:%M}(就绪等待期间本机可能睡眠)· 日志 {log.path}")
            return finish(EXIT_FAILED, "PAST_HARD_STOP")

    if resume_id:
        run_id = resume_id
        summary["resumed"] = True
    else:
        state["stage"] = "begin"
        request_path = ops_dir() / f"scan_run_{date}.request.json"
        atomic_write_json(request_path, build_headless_request(date))
        try:
            run_id = steps.begin(request_path)
        except Exception as exc:  # noqa: BLE001
            reason = f"{type(exc).__name__}: {exc}"[:300]
            log.line(f"begin 失败:{reason}")
            tell(f"扫描 {date} FAILED", f"阶段 begin · {reason} · 日志 {log.path}")
            return finish(EXIT_FAILED, "BEGIN_FAILED", stage="begin", reason=reason)
    state["run_id"] = summary["run_id"] = run_id

    state["stage"] = "runner"
    budget = run_cap
    if stop_at is not None:
        budget = max(60.0, min(run_cap, (stop_at - steps.now()).total_seconds()))
    log.line(f"run_id={run_id} · runner 启动(--executor headless · 墙钟 {budget / 60:.0f} 分钟)")
    outcome = steps.run(run_id, budget)
    if (outcome or {}).get("stop_reason") == "RUNNER_BUSY":
        tell(f"扫描 {date} 未开", f"run {run_id} 仍有 runner;本次恢复未执行")
        return finish(run_lock.EXIT_HELD, "LIVE_RUN", stage="runner")
    if not (outcome or {}).get("finished"):
        stage, reason = failure_point(outcome)
        stop = (outcome or {}).get("stop_reason") or "NO_OUTCOME"
        log.line(f"runner 未完成 · {stop} · 阶段 {stage} · {reason}")
        return abort(stage, reason, "FAILED", stop,
                     recoverable=(outcome or {}).get("recoverable") is True,
                     recovery_tasks=(outcome or {}).get("recovery_tasks"))

    state["stage"] = "verify"
    canonical = ((outcome.get("finish") or {}).get("canonical_path"))
    verification = steps.verify(canonical, run_id) if canonical else {"error": "no canonical"}
    verified = all(verification.get(key) is True for key in VERIFY_KEYS)
    log.line(f"finished · canonical={canonical} · verify-report "
             + " ".join(f"{key}={verification.get(key)}" for key in VERIFY_KEYS))
    record_redline(run_id)
    state["stage"] = "deliver"
    brief = steps.locate_brief(run_id, canonical)
    report_dir = str(brief.parent) if brief is not None else canonical
    title = f"扫描 {date} ✓" + ("" if verified else " · verify ✗")
    if brief is None:
        record = {"status": "FAILED", "error": "brief.md 找不到"}
    else:
        record = steps.deliver(brief, run_id=run_id, title=title, report_path=report_dir)
    log.line(f"送达 · {record.get('channel')} · {record.get('status')}"
             + (f" · {record.get('error')}" if record.get("error") else ""))
    # 复审 M6:run 的结果与送达是两件事 —— 没发出去也不叫 DELIVERED。
    return finish(EXIT_OK, "FINISHED", canonical_path=canonical, verification=verification,
                  verified=verified, delivery={key: record.get(key) for key in (
                      "channel", "status", "error", "truncated", "record_path")})


def _out_of_window(steps: Steps, fired: datetime, deadline: str, *, log: OpsLog,
                   summary: dict, finish, tell) -> int:
    """定时触发落在 [WINDOW_START, 截止] 之外(复审 I4):本机睡眠/关机后 launchd 补触发。

    错过的那一夜 = 最近已结算交易日;它已有摘要(跑过/报过)→ 静默;窗口还没到(同日早于
    21:10,多半是手动)→ 静默并提示带 ``--date``;否则推一次「错过」并写该日摘要(之后的
    补触发就静默了)。
    """
    log.line(f"触发时刻 {fired:%m-%d %H:%M} 不在窗口 {runner_cfg()["window_start"]}–{deadline} 内(未带 --date)")
    try:
        day = steps.missed_date(fired)
    except Exception as exc:  # noqa: BLE001
        log.line(f"错过的交易日不可知:{type(exc).__name__}: {exc}")
        day = None
    if day is None:
        return finish(EXIT_OK, "OUT_OF_WINDOW")
    if day == fired.strftime("%Y-%m-%d") and fired.time() < _at(runner_cfg()["window_start"]):
        log.line(f"{day} 的窗口还没到;手动补跑请带 --date {day}")
        return finish(EXIT_OK, "OUT_OF_WINDOW")
    if (ops_dir() / f"scan_run_{day}.json").exists():
        log.line(f"{day} 已有摘要,静默退出")
        return finish(EXIT_OK, "OUT_OF_WINDOW")
    summary["date"] = day
    tell(f"扫描 {day} 错过",
         f"本机睡眠/关机:launchd {fired:%m-%d %H:%M} 才触发(窗口 {runner_cfg()["window_start"]}–{deadline});"
         f"补跑 scripts/scan_run.sh --date {day} --skip-readiness · 日志 {log.path}")
    return finish(EXIT_OK, "MISSED", fired_at=fired.isoformat(timespec="minutes"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m autoresearch.scan.scan_run",
                                 description="无人值守一场全 A 扫描(headless;launchd 21:20)")
    ap.add_argument("--date", help="显式数据日(须为交易日;缺省 = 今天,非交易日静默退出)")
    ap.add_argument("--resume-run-id", help="恢复原 ACTIVE headless run;不重新 begin 或取数")
    ap.add_argument("--ack-redline", metavar="RUN_ID",
                    help="确认上一场 redline 断路器(看过 $RPT/_ops/redline/<RUN_ID>.json 之后)并放行本场")
    ap.add_argument("--deadline", default=readiness.DEADLINE, help="湖就绪等待截止 HH:MM")
    ap.add_argument("--skip-readiness", action="store_true", help="不等 stk_factor_pro(补跑用)")
    ap.add_argument("--claude-bin", help="claude CLI 路径(缺省 PATH / ~/.local/bin/claude;claude 场)")
    ap.add_argument("--codex-bin", help="codex CLI 路径(缺省 PATH / /usr/local/bin/codex;codex 场)")
    ap.add_argument("--max-parallel", type=int, help="推理并发帽(缺省 budgets.concurrency.l4_stock)")
    ap.add_argument("--run-timeout-minutes", type=float, default=None,
                    help=f"runner 墙钟(缺省 = scan_config runner.run_timeout_minutes,内建 {RUN_TIMEOUT_MINUTES})")
    ap.add_argument("--hard-stop", default=None,
                    help=f"定时场夜间硬截止 HH:MM(缺省 = scan_config runner.hard_stop,内建 {HARD_STOP};显式 --date 补跑不受它约束)")
    args = ap.parse_args(argv)
    if ws.ENGINE not in HEADLESS_ENGINES:
        print(f"[scan-run] headless 执行器只跑 {'/'.join(HEADLESS_ENGINES)} 引擎;当前 {ws.ENGINE!r}"
              "(scripts/scan_run.sh --engine claude|codex 显式钉死)")
        return EXIT_USAGE
    held = run_lock.try_acquire(run_lock.lock_path(), note="scan_run")
    if held is None:
        info = run_lock.holder(run_lock.lock_path()) or {}
        message = run_lock.describe(info)
        print(f"[scan-run] {message} —— 另一场在跑,本次不开")
        try:
            from autoresearch.scan import delivery

            delivery.notify("扫描未开", f"{message}(另一场仍在跑)")
        except Exception:  # noqa: BLE001 - never mask the lock refusal
            pass
        return run_lock.EXIT_HELD
    log = OpsLog(ops_dir() / f"scan_run_{datetime.now():%Y-%m-%d}.log")
    # 复审 M1d:launchd bootout / kickstart -k / 关终端只打到本进程;runner 与 claude -p 在
    # 各自的会话里 —— 由处理器转成 ScanRunInterrupted,run_once 收口(杀组、finalize、推送)。
    previous = install_interrupt_handlers()
    try:
        return run_once(args, default_steps(args, log), log=log)
    finally:
        restore_handlers(previous)
        log.close()
        held.release()


__all__ = [
    "EXIT_FAILED", "EXIT_OK", "EXIT_USAGE", "HARD_STOP", "HEADLESS_ENGINES", "LIVE_RUN_WINDOW", "NO_CHANNEL_WARNING",
    "OpsLog", "RUN_TIMEOUT_MINUTES", "ScanRunInterrupted", "Steps", "WINDOW_START",
    "build_headless_request", "default_steps", "failure_point", "hard_stop_after", "in_window",
    "install_interrupt_handlers", "live_scan_runs", "locate_brief", "main", "ops_dir",
    "restore_handlers", "run_once", "terminate_inflight",
]


if __name__ == "__main__":
    sys.exit(main())
