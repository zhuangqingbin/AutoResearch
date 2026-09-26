"""无人值守的一场全 A 扫描(launchd 交易日 21:20 → ``scripts/scan_run.sh`` → 本模块)。

design: docs/superpowers/specs/2026-09-26-daily-engine-consolidation-design.md §6 C2/C3。

一条确定性流水,只有推理交给 ``claude -p``(headless 执行器,一个任务一个会话):

1. **锁**:``$CTX/.scan_run.lock``(``scan.run_lock``,fcntl;macOS 无 ``flock(1)``)。被占 →
   立刻退出、打印持锁 pid、推一句「未开」。
2. **交易日**:今天不是交易日 → 静默退出 0(launchd 只按星期触发,节假日靠这里挡)。
3. **人工场**:同引擎还有 ACTIVE 且心跳新鲜的 scan-market run(人工会话在扫)→ 不开,推送。
4. **湖灌齐**:``scan.readiness``(stk_factor_pro ≥5300 且连续两次不变,最多等到 22:30)。
5. **begin**:``session_agent begin --orchestration session_v1``,请求由
   :func:`build_headless_request` 生成(宿主 = 本进程,``session_ref=headless-<uuid>``)。
6. **runner**:``session_agent run --executor headless``(阶段级重试一次是 runner 的
   TIMEOUT 语义);整场墙钟上限 ``--run-timeout-minutes``,超时杀 runner 进程组与在飞的
   ``claude -p``。
7. 成功 → ``verify-report --level full`` → 送达 brief(``scan.delivery``);
   未完成 → ``capsule finalize FAILED`` + 推「FAILED · 阶段 · 一句原因 · 日志路径」。
   **不自动改代码、不自动重跑第二场。**

日志 ``$RPT/_ops/scan_run_<日>.log``;每场一份摘要 ``$RPT/_ops/scan_run_<交易日>.json``。

  scripts/scan_run.sh                       # launchd / 手动触发(等湖灌齐)
  scripts/scan_run.sh --date 2026-09-28 --skip-readiness
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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
VERIFY_KEYS = ("report_covered", "publication_ok", "orchestration_verified", "completeness_ok")
HEADLESS_EVIDENCE = (
    "headless-probe:docs/research/2026-09-26-headless-driver-probes.md",
    "headless-executor:autoresearch/session_agent/executors/headless_claude.py",
)


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


def build_headless_request(date: str, *, session_ref: str | None = None) -> dict:
    """session_v1 scan 请求;宿主 = 本无人值守进程 + 每个推理任务一个 ``claude -p`` 会话。

    能力如实声明:确定性步骤在 runner 进程内跑(``deterministic_exec``),推理经 headless
    执行器交接(``inference_handoff``)且每个任务一个独立顶级会话(``independent_context``,
    进程边界即上下文边界),``--agent l4-intel`` 能联网(探针 ④)。执行器不能重挂在飞的
    ``claude -p``(``safe_resume=false``)。没有主会话 transcript,所以不给 ``transcript-file:``。
    """
    return {
        "schema_version": 1,
        "kind": "scan-market",
        "requested_mode": "AUTO",
        "analysis_date": date,
        "subject": None,
        "peers": [],
        "asset_type": None,
        "name": None,
        # 📌 持仓由确定性判据 SENTINEL_PINNED 兜住;无人值守不做人工 override。
        "force_full": False,
        "host_profile": {
            "schema_version": 1,
            "engine": "claude",
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
    }


def live_scan_runs(*, now: datetime | None = None,
                   window: timedelta = LIVE_RUN_WINDOW) -> list[dict]:
    """同引擎 ACTIVE 的 scan-market run 中,持有者进程还活着或心跳在 ``window`` 内的那些。"""
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


@dataclass
class Steps:
    """每一个外部动作一个可注入的步骤(测试里全换成假的:不取数、不起 claude、不推送)。"""

    resolve_date: Callable[[str | None], str | None]
    live_runs: Callable[[], list[dict]]
    wait_ready: Callable[[str, str], bool]
    begin: Callable[[Path], str]
    run: Callable[[str], dict | None]
    verify: Callable[[str, str], dict]
    locate_brief: Callable[[str, str | None], Path | None]
    deliver: Callable[..., dict]
    finalize_failed: Callable[[str, dict], None]
    notify: Callable[[str, str], dict]


# ── default steps (subprocess boundary) ──────────────────────────────────────────

def _call(argv: list[str], *, env: dict, timeout: float | None,
          stderr=None) -> tuple[int | None, str]:
    """跑一个子进程(独立进程组);超时杀整组。返回 ``(returncode|None=超时, stdout)``。"""
    proc = subprocess.Popen(  # noqa: S603 - argv list, no shell
        argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=stderr, text=True,
        env=env, start_new_session=True)
    try:
        out, _ = proc.communicate(timeout=timeout)
        return proc.returncode, out or ""
    except subprocess.TimeoutExpired:
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(proc.pid, sig)
            except ProcessLookupError:
                break
            try:
                proc.wait(timeout=10)
                break
            except subprocess.TimeoutExpired:
                continue
        try:
            out, _ = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            out = ""
        return None, out or ""


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


def _env(run_id: str | None = None) -> dict:
    env = {key: value for key, value in os.environ.items() if key != "AUTORESEARCH_RUN_ID"}
    env["AUTORESEARCH_ENGINE"] = "claude"
    if run_id:
        env["AUTORESEARCH_RUN_ID"] = run_id
    return env


def terminate_inflight(records_dir: Path | str, sig: int = signal.SIGTERM) -> list[int]:
    """Signal every headless call still recorded as STARTING/RUNNING (its own process group).

    The runner's ``claude -p`` children live in their own sessions (``start_new_session``),
    so killing the runner does not stop them.  The executor
    (``session_agent.executors.headless_claude``) records ``state`` + ``pid`` (= pgid)
    before it waits; scan sits below session_agent in the layering, so the record is read
    as data through its registered path, never by importing the executor.
    """
    killed = []
    for path in sorted(Path(records_dir).glob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(record, dict):
            continue
        pid = record.get("pid")
        if record.get("state") not in {"STARTING", "RUNNING"} or type(pid) is not int or pid <= 1:
            continue
        try:
            os.killpg(pid, sig)
        except (ProcessLookupError, PermissionError):
            continue
        killed.append(pid)
    return killed


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
    max_parallel = getattr(args, "max_parallel", None)
    run_timeout = float(getattr(args, "run_timeout_minutes", RUN_TIMEOUT_MINUTES)) * 60

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
                                         "--ignore-scan-lock"),
                          env=_env(), timeout=1800, stderr=stream)
        doc = _last_json(out) or {}
        if code != 0 or not doc.get("run_id"):
            detail = "; ".join(str(item.get("message")) for item in doc.get("errors") or []
                               if isinstance(item, dict)) or out.strip()[:300]
            raise RuntimeError(f"session_agent begin exit={code}: {detail}")
        return str(doc["run_id"])

    def run(run_id: str) -> dict | None:
        argv = _session_agent("run", "--run-id", run_id, "--executor", "headless")
        if claude_bin:
            argv += ["--claude-bin", str(claude_bin)]
        if max_parallel:
            argv += ["--max-parallel", str(max_parallel)]
        code, out = _call(argv, env=_env(run_id), timeout=run_timeout, stderr=stream)
        if code is None:
            killed = _terminate_inflight_headless(run_id)
            return {"finished": False, "stop_reason": "RUN_TIMEOUT", "errors": [{
                "message": f"runner 超过 {run_timeout / 60:.0f} 分钟未结束,已终止"
                           f"(连同 {len(killed)} 个在飞 claude -p)"}]}
        return _last_json(out)

    def verify(canonical: str, run_id: str) -> dict:
        code, out = _call(_session_agent("verify-report", "--report-path", canonical,
                                         "--expected-run-id", run_id, "--level", "full"),
                          env=_env(), timeout=1800, stderr=stream)
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
                          env=_env(), timeout=1800, stderr=stream)
        emit(f"capsule finalize FAILED exit={code} {out.strip()[:300]}")

    def notify(title: str, body: str) -> dict:
        from autoresearch.scan import delivery

        return delivery.notify(title, body)

    return Steps(resolve_date=resolve_date, live_runs=live_scan_runs, wait_ready=wait_ready,
                 begin=begin, run=run, verify=verify, locate_brief=locate_brief,
                 deliver=deliver, finalize_failed=finalize_failed, notify=notify)


# ── orchestration ───────────────────────────────────────────────────────────────

def run_once(args, steps: Steps, *, log: OpsLog) -> int:
    started = _now_iso()
    summary: dict = {"schema_version": 1, "started_at": started, "log": str(log.path)}

    def finish(code: int, result: str, **fields) -> int:
        summary.update(fields, result=result, exit_code=code, ended_at=_now_iso())
        if summary.get("date"):
            atomic_write_json(ops_dir() / f"scan_run_{summary['date']}.json", summary)
        log.line(f"end · {result} · exit {code}")
        return code

    def tell(title: str, body: str) -> None:
        try:
            result = steps.notify(title, body)
        except Exception as exc:  # noqa: BLE001 - notification never changes the outcome
            result = {"status": "FAILED", "error": f"{type(exc).__name__}: {exc}"}
        log.line(f"notify · {title} · {result.get('status')}")

    log.line("start")
    try:
        date = steps.resolve_date(getattr(args, "date", None))
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
        live = steps.live_runs()
        if not live:
            return None
        ids = ", ".join(item["run_id"] for item in live)
        log.line(f"人工会话的扫描 run 仍在跑:{ids}")
        tell(f"扫描 {date} 未开", f"人工会话的扫描 run {ids} 仍在跑 · 日志 {log.path}")
        return finish(run_lock.EXIT_HELD, "LIVE_RUN", live_runs=live)

    held = live_manual_run()
    if held is not None:
        return held

    deadline = getattr(args, "deadline", readiness.DEADLINE)
    if not getattr(args, "skip_readiness", False) and not steps.wait_ready(date, deadline):
        tell(f"扫描 {date} 未开",
             f"tushare stk_factor_pro 截至 {deadline} 未灌齐 · 日志 {log.path}")
        return finish(EXIT_FAILED, "NOT_READY")
    # 就绪等待可长达 70 分钟:begin 前再问一次人工场(复审 I3)。
    held = live_manual_run()
    if held is not None:
        return held

    request_path = ops_dir() / f"scan_run_{date}.request.json"
    atomic_write_json(request_path, build_headless_request(date))
    try:
        run_id = steps.begin(request_path)
    except Exception as exc:  # noqa: BLE001
        reason = f"{type(exc).__name__}: {exc}"[:300]
        log.line(f"begin 失败:{reason}")
        tell(f"扫描 {date} FAILED", f"阶段 begin · {reason} · 日志 {log.path}")
        return finish(EXIT_FAILED, "BEGIN_FAILED", stage="begin", reason=reason)
    summary["run_id"] = run_id
    log.line(f"run_id={run_id} · runner 启动(--executor headless)")

    outcome = steps.run(run_id)
    if not (outcome or {}).get("finished"):
        stage, reason = failure_point(outcome)
        stop = (outcome or {}).get("stop_reason") or "NO_OUTCOME"
        log.line(f"runner 未完成 · {stop} · 阶段 {stage} · {reason}")
        steps.finalize_failed(run_id, {"stage": stage, "stop_reason": stop,
                                       "message": reason, "log": str(log.path)})
        tell(f"扫描 {date} FAILED",
             f"阶段 {stage} · {reason} · run {run_id} · 日志 {log.path}")
        return finish(EXIT_FAILED, "FAILED", stage=stage, reason=reason, stop_reason=stop)

    canonical = ((outcome.get("finish") or {}).get("canonical_path"))
    verification = steps.verify(canonical, run_id) if canonical else {"error": "no canonical"}
    verified = all(verification.get(key) is True for key in VERIFY_KEYS)
    log.line(f"finished · canonical={canonical} · verify-report "
             + " ".join(f"{key}={verification.get(key)}" for key in VERIFY_KEYS))
    brief = steps.locate_brief(run_id, canonical)
    report_dir = str(brief.parent) if brief is not None else canonical
    title = f"扫描 {date} ✓" + ("" if verified else " · verify ✗")
    if brief is None:
        record = {"status": "FAILED", "error": "brief.md 找不到"}
    else:
        record = steps.deliver(brief, run_id=run_id, title=title, report_path=report_dir)
    log.line(f"送达 · {record.get('channel')} · {record.get('status')}"
             + (f" · {record.get('error')}" if record.get("error") else ""))
    return finish(EXIT_OK, "DELIVERED", canonical_path=canonical, verification=verification,
                  verified=verified, delivery={key: record.get(key) for key in (
                      "channel", "status", "error", "truncated", "record_path")})


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m autoresearch.scan.scan_run",
                                 description="无人值守一场全 A 扫描(headless;launchd 21:20)")
    ap.add_argument("--date", help="显式数据日(须为交易日;缺省 = 今天,非交易日静默退出)")
    ap.add_argument("--deadline", default=readiness.DEADLINE, help="湖就绪等待截止 HH:MM")
    ap.add_argument("--skip-readiness", action="store_true", help="不等 stk_factor_pro(补跑用)")
    ap.add_argument("--claude-bin", help="claude CLI 路径(缺省 PATH / ~/.local/bin/claude)")
    ap.add_argument("--max-parallel", type=int, help="推理并发帽(缺省 budgets.concurrency.l4_stock)")
    ap.add_argument("--run-timeout-minutes", type=float, default=RUN_TIMEOUT_MINUTES)
    args = ap.parse_args(argv)
    if ws.ENGINE != "claude":
        print(f"[scan-run] headless 执行器只跑 claude 引擎;当前 {ws.ENGINE}(Codex headless 不在范围)")
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
    try:
        return run_once(args, default_steps(args, log), log=log)
    finally:
        log.close()
        held.release()


__all__ = [
    "EXIT_FAILED", "EXIT_OK", "EXIT_USAGE", "LIVE_RUN_WINDOW", "OpsLog", "RUN_TIMEOUT_MINUTES",
    "Steps", "build_headless_request", "default_steps", "failure_point", "live_scan_runs",
    "locate_brief", "main", "ops_dir", "run_once", "terminate_inflight",
]


if __name__ == "__main__":
    sys.exit(main())
