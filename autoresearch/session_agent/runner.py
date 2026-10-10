"""session_v1 runner — drive one frozen task graph to ``DONE``, then ``finish``.

Scheduling only, no judgment: the task graph, attempts, receipts, validation and
publication stay in ``service`` / ``store`` / ``publication``.  Each round:

1. **harvest** finished work — inference: hash every output file itself (the driver
   trusts files, never an executor's word), bind transcript evidence, ``submit``;
   failures go through ``service.fail`` and ``contracts.retry.TASK_ATTEMPT`` classes
   (TIMEOUT …) get exactly one new attempt (L4 intel/card: one new taskbook attempt
   via ``service.retry_l4``; review2/review3 retry only their SESSION attempt);
2. read ``service.next``;
3. **launch** READY tasks — L4 taskbook tickets are *claimed* (the taskbook preflight is
   the ticket's execution), deterministic tasks run through ``service.execute`` on one
   deterministic lane (``trace.exec_capture`` evidence, same as the CLI path), inference
   tasks go to the executor, at most ``max_parallel`` in flight.

Threads: only the loop thread calls the service. Executor threads only run
``executor.dispatch``. Pure facts capture may yield to inference on the same owner thread.

A crash between ``claim`` and ``submit`` leaves the attempt RUNNING.  On restart the
runner never claims it again: an executor with ``supports_reattach`` re-attaches to the
same attempt (mailbox: same request file), otherwise the attempt is reported as an
orphan (with the exact ``fail --error-class STALE_TASK`` command as its hint) and the run
stops ``STALLED`` for an operator decision.

Restart safety (review I3): retry intent is never held only in memory — every round the
runner derives it from durable state (store: SESSION task FAILED below
``SESSION_MAX_ATTEMPTS``; taskbook: ticket FAILED with a ``TASK_ATTEMPT`` class below
``l4_tasks.MAX_ATTEMPTS``), so a runner restarted after a crash spends exactly the retries
the dead one would have.  One runner per run: ``_dispatch/runner.lock`` is held (flock)
for the process lifetime; a second runner refuses with :class:`RunnerAlreadyRunning`.
A heartbeat thread refreshes ``runner.json`` independently of the loop thread, so a long
service call never looks like a dead runner to ``mailbox wait``.
"""
from __future__ import annotations

import concurrent.futures as cf
import fcntl
import json
import os
import re
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from autoresearch.common.atomic import atomic_write_json, canonical_json, sha256_bytes
from autoresearch.contracts.retry import TASK_ATTEMPT, VALIDATION_REPAIR
from autoresearch.session_agent import artifacts, service, store
from autoresearch.session_agent.dispatch import build_request
from autoresearch.session_agent.executors.base import (
    DISPATCH_DIR,
    DispatchRequest,
    DispatchResult,
    ExecutorTimeout,
    classify_error,
    supports_reattach,
)

#: One retry for TASK_ATTEMPT-class failures of a SESSION task (spec §4 A1-1).
SESSION_MAX_ATTEMPTS = 2


def _session_max_attempts() -> int:
    """`scan_config.session.max_attempts`(缺省 = SESSION_MAX_ATTEMPTS)。"""
    from autoresearch.session_agent.config import session_cfg
    return int(session_cfg()["max_attempts"])
_NO_PARAMS = {"type": "object", "required": [], "additionalProperties": False}
_HEARTBEAT_SECONDS = 5.0


class RunnerUnsupported(RuntimeError):
    """The runner has no parameter source for a deterministic operation."""


class RunnerAlreadyRunning(RuntimeError):
    """Another live runner holds ``_dispatch/runner.lock`` for this run."""


def submit_error_class(exc: BaseException) -> str:
    """``submit`` 拒绝的分类:领域校验拒绝(DomainValidationError,含其 cause 链)= DOMAIN_VALIDATION,
    带校验原话重做一次(contracts.retry.VALIDATION_REPAIR);其余身份/契约拒绝 = CONTRACT_ERROR,不重试。"""
    from autoresearch.session_agent.validation import DomainValidationError

    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        if isinstance(current, DomainValidationError):
            return "DOMAIN_VALIDATION"
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return "CONTRACT_ERROR"


def orphan_hint(run_id: str, task_id: str, attempt: int) -> str:
    """The operator action that makes a restarted runner progress past an orphan."""
    return (
        f"uv run --no-sync python -m autoresearch.session_agent fail --run-id {run_id} "
        f"--task-id {task_id} --attempt {attempt} --error-class STALE_TASK "
        "--message 'orphaned by a dead runner'; then restart the runner under a NEW "
        "trace.detach key (e.g. session-runner-2): it retries the task once as "
        f"a{attempt + 1} (an L4 card/intel child: one retry-l4; a review: its SESSION attempt)"
    )


@dataclass(frozen=True)
class ServiceHooks:
    """Injection points forwarded to ``service`` (all ``None`` = production defaults)."""

    handle_loader: Callable | None = None
    operation_runner: Callable | None = None     # service.execute(runner=)
    event_recorder: Callable | None = None       # service.claim/submit(event_recorder=)
    validator: Callable | None = None            # service.submit(validator=)
    publisher: Callable | None = None            # service.finish(publisher=)
    finalizer: Callable | None = None            # service.finish(finalizer=)


@dataclass
class _Flight:
    kind: str
    task: dict
    attempt: int
    future: cf.Future
    request: DispatchRequest | None = None
    claim: dict | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _is_review(task_id: str) -> bool:
    return task_id.endswith((".review2", ".review3"))


def _params_for(task: dict, request: dict | None = None) -> dict:
    """Parameters of a deterministic operation; the frozen request is the only source."""
    operation = task["operation"]
    if operation == "test.noop":
        return {"message": f"runner {task['task_id']}"[:200]}
    if operation == "stock.harvest" and request is not None:
        from autoresearch.session_agent.workflows.stock import harvest_params

        return harvest_params(request)
    from autoresearch.session_agent.operations import operation_catalog

    if operation_catalog()[operation]["params"] == _NO_PARAMS:
        return {}
    raise RunnerUnsupported(f"operation {operation} needs parameters; runner has no source")


def bind_transcript_evidence(run_id: str, request: DispatchRequest, result: DispatchResult,
                             *, handle_loader=None) -> tuple[str, ...]:
    """Default evidence hook: bind a whole subagent/headless transcript to the attempt."""
    if not result.transcript_path:
        return ()
    context = result.context_ref
    parent = result.parent_context_ref or request.host_session_ref
    if not context or parent == context:
        return ()
    from autoresearch.session_agent.host_evidence import bind_task_transcript
    from autoresearch.trace.transcripts.snapshot import capture_snapshot

    snapshot = capture_snapshot(Path(result.transcript_path), engine=request.engine)
    # Codex rollout rows carry an explicit ``ordinal``; Claude JSONL rows do not, so a
    # Claude transcript (subagent or headless ``claude -p`` session) is bound whole by
    # row index — without this fallback no Claude transcript was ever bound.
    end_ordinal = snapshot.last_ordinal
    if end_ordinal is None and snapshot.rows:
        end_ordinal = len(snapshot.rows) - 1
    if end_ordinal is None:
        return ()
    bound = bind_task_transcript(
        run_id,
        request.task_id,
        request.attempt,
        result.transcript_path,
        context_ref=context,
        parent_context_ref=parent,
        session_ref=result.session_ref or request.host_session_ref,
        start_ordinal=0,
        end_ordinal=end_ordinal,
        context_source="SUBAGENT",
        handle_loader=handle_loader,
    )
    return (bound["evidence_ref"],)


class Runner:
    def __init__(
        self,
        run_id: str,
        executor,
        *,
        max_parallel: int = 4,
        poll_seconds: float = 5.0,
        max_rounds: int = 20000,
        hooks: ServiceHooks | None = None,
        evidence_binder: Callable | None = None,
        timeouts: dict | None = None,
        timeout_multiplier: float = 1.0,
        log: Callable[[dict], None] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        heartbeat_seconds: float = _HEARTBEAT_SECONDS,
        monotonic: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], str] = _now,
        fanout_warmup_s: float = 0.0,
    ):
        if type(max_parallel) is not int or max_parallel < 1:
            raise ValueError("max_parallel must be a positive integer")
        if not fanout_warmup_s >= 0:
            raise ValueError("fanout_warmup_s must be >= 0")
        # B8(2026-10-03):并行扇出预热。同一角色第一份派发之后,其余等它领先这么多秒再发 ——
        # 并发请求读不到彼此的 prompt 缓存,同时发 N 份就是 N 次写同一个前缀。0 = 关。
        self.fanout_warmup_s = float(fanout_warmup_s)
        self._monotonic = monotonic
        self._role_first_start: dict[str, float] = {}
        if not heartbeat_seconds > 0:
            raise ValueError("heartbeat_seconds must be positive")
        self.heartbeat_seconds = float(heartbeat_seconds)
        self.run_id = run_id
        self.executor = executor
        self.max_parallel = max_parallel
        self.poll_seconds = poll_seconds
        self.max_rounds = max_rounds
        self.hooks = hooks or ServiceHooks()
        self.evidence_binder = evidence_binder or (
            lambda request, result: bind_transcript_evidence(
                run_id, request, result, handle_loader=self.hooks.handle_loader
            )
        )
        self.timeouts = timeouts
        self.timeout_multiplier = timeout_multiplier
        self._log = log or (lambda event: print(json.dumps(event, ensure_ascii=False),
                                                file=sys.stderr, flush=True))
        self._sleep = sleep
        from autoresearch.scan.observability import SchedulingMetrics

        self.metrics = SchedulingMetrics(max_parallel, monotonic=monotonic, wall_clock=wall_clock)
        self._inflight: dict[str, _Flight] = {}
        # Retry intent is derived from durable state every round (review I3); the only
        # in-memory memo is "this process already tried and was refused" (no spinning).
        self._l4_refused: set[str] = set()
        self._late_bound: set[tuple[str, int]] = set()
        self._skip: dict[str, str] = {}
        self._orphans: list[dict] = []
        self._dispatches: list[dict] = []
        self._errors: list[dict] = []
        self._started_at = _now()
        self._heartbeat = 0.0
        self._status_lock = threading.Lock()
        self._exited = False
        self._stop_beat = threading.Event()
        self._lock_stream = None

    # ── service plumbing ────────────────────────────────────────────────────────
    def _handle(self):
        from autoresearch.trace.capsule import require_active_run

        return (self.hooks.handle_loader or require_active_run)(self.run_id)

    def _entry(self, task_id: str) -> dict:
        return store.read_entry(service._store_path(self.handle), task_id)

    def _request(self) -> dict:
        path = service._session_dir(self.handle) / "request.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def _event(self, event_name: str, /, **fields) -> None:
        self._log({"ts": _now(), "event": event_name, "run_id": self.run_id, **fields})

    def _error(self, task_id: str | None, message: str) -> None:
        self._errors.append({"code": "RUNNER_ERROR", "task_id": task_id, "message": message})
        self._event("RUNNER_ERROR", task_id=task_id, message=message)

    # ── status files under <staging>/_dispatch ──────────────────────────────────
    def _dispatch_dir(self) -> Path:
        path = Path(self.handle.staging) / DISPATCH_DIR
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _write_status(self, state: str, outcome: dict | None = None) -> None:
        with self._status_lock:
            if self._exited:
                return                     # EXITED is final: a late beat never revives it
            self._exited = state == "EXITED"
            atomic_write_json(self._dispatch_dir() / "runner.json", {
                "schema_version": 1,
                "run_id": self.run_id,
                "pid": os.getpid(),
                "executor": getattr(self.executor, "name", type(self.executor).__name__),
                "state": state,
                "started_at": self._started_at,
                "updated_at": _now(),
                "heartbeat_epoch": time.time(),
                "heartbeat_seconds": self.heartbeat_seconds,
                "in_flight": sorted(self._inflight.copy()),   # dict.copy is atomic (GIL)
                "outcome": outcome,
                "scheduling": self.metrics.snapshot(),
            })

    def _heartbeat_loop(self) -> None:
        while not self._stop_beat.wait(self.heartbeat_seconds):
            try:
                self._write_status("RUNNING")
            except Exception as exc:  # noqa: BLE001 - a failed beat must not kill the run
                self._event("HEARTBEAT_FAILED", message=f"{type(exc).__name__}: {exc}")

    def _acquire_run_lock(self) -> None:
        """M2: exactly one runner per run (flock held for the process lifetime)."""
        path = self._dispatch_dir() / "runner.lock"
        stream = path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            stream.seek(0)
            holder = stream.read().strip() or "{}"
            stream.close()
            try:
                pid = json.loads(holder).get("pid")
            except (json.JSONDecodeError, AttributeError):
                pid = None
            raise RunnerAlreadyRunning(
                f"another runner is already running run {self.run_id}: pid {pid} holds {path}; "
                "do not start a second one (it would claim the same work). Stop that "
                "process first if it is stuck."
            ) from None
        stream.seek(0)
        stream.truncate()
        stream.write(json.dumps({"pid": os.getpid(), "started_at": self._started_at}))
        stream.flush()
        self._lock_stream = stream

    def _release_run_lock(self) -> None:
        stream, self._lock_stream = self._lock_stream, None
        if stream is not None:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            stream.close()

    def _ledger(self, row: dict) -> None:
        path = self._dispatch_dir() / "ledger.jsonl"
        with path.open("a", encoding="utf-8") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def _record(self, flight: _Flight, outcome: str, result: DispatchResult | None = None,
                error_class: str | None = None) -> None:
        row = {
            "ts": _now(),
            "task_id": flight.task["task_id"],
            "attempt": flight.attempt,
            "kind": flight.kind,
            "role": flight.task.get("role"),
            "agent_type": flight.request.agent_type if flight.request else None,
            "outcome": outcome,
            "error_class": error_class,
            "session_ref": result.session_ref if result else None,
            "context_ref": result.context_ref if result else None,
            "transcript_path": result.transcript_path if result else None,
            "usage": dict(result.usage) if result and result.usage else None,
        }
        self._dispatches.append({key: row[key] for key in (
            "task_id", "attempt", "kind", "outcome", "error_class")})
        if outcome in {"SUBMITTED", "SUCCEEDED"}:
            self.metrics.accepted(flight.task, flight.attempt)
        self._ledger(row)
        self._event("TASK_" + outcome.split(":")[0], task_id=row["task_id"],
                    attempt=row["attempt"], error_class=error_class)

    # ── main loop ───────────────────────────────────────────────────────────────
    def run(self) -> dict:
        self.handle = self._handle()
        self.host_profile = json.loads(
            (service._session_dir(self.handle) / "host_profile.json").read_text("utf-8")
        )
        self._acquire_run_lock()           # before runner.json: a refused runner writes nothing
        try:
            return self._run_locked()
        finally:
            self._release_run_lock()

    def _preflight(self) -> None:
        from autoresearch.session_agent.preflight import preflight_plan

        plan = service._load_plan(self.handle)
        tasks = service._all_tasks(self.handle, plan)
        signature = tuple(sorted(task["task_id"] for task in tasks))
        if signature != getattr(self, "_preflight_tasks", None):
            report = preflight_plan(self.handle, plan, self.host_profile,
                                    executor=self.executor, tasks=tasks)
            self._event("ROLE_PREFLIGHT", report=report)
            self._preflight_tasks = signature

    def _run_locked(self) -> dict:
        self._preflight()
        self._write_status("RUNNING")
        beat = threading.Thread(target=self._heartbeat_loop, name="session-heartbeat",
                                daemon=True)
        beat.start()
        self._inference_pool = cf.ThreadPoolExecutor(
            max_workers=self.max_parallel, thread_name_prefix="session-inference")
        outcome = None
        state = None
        rounds = 0
        try:
            self._adopt_orphans()
            while rounds < self.max_rounds:
                rounds += 1
                progressed = self._harvest()
                self._bind_late_evidence()
                capacity = self._capacity_pending()
                if capacity:
                    # Drain already-paid work, but never fan out or automatically retry
                    # while the subscription cannot serve new inference.
                    if not self._inflight:
                        state = service.next(self.run_id, handle_loader=self.hooks.handle_loader)
                        outcome = self._outcome(state, rounds, "USAGE_LIMIT")
                        break
                    self._beat()
                    if not progressed:
                        self._sleep(self.poll_seconds)
                    continue
                if self._det_busy():
                    # A deterministic execute may be mid-expansion (expansion file on disk,
                    # store/artifacts not yet synced): read the graph only between executes.
                    self._beat()
                    if not progressed:
                        self._sleep(self.poll_seconds)
                    continue
                progressed |= self._run_l4_retries()
                state = service.next(self.run_id, handle_loader=self.hooks.handle_loader)
                self._preflight()
                status = state["state"]
                retries = self._session_retries()
                if not self._inflight and not retries:
                    if status == "DONE":
                        outcome = self._finish(state, rounds)
                        break
                    if status == "BLOCKED":
                        outcome = self._outcome(state, rounds, "BLOCKED")
                        break
                    if status == "WAITING":
                        outcome = self._outcome(state, rounds, "STALLED")
                        break
                launched = self._launch(state["tasks"] if status == "READY" else [], retries)
                progressed |= launched
                if (status == "READY" and not launched and not self._inflight
                        and not self._warming(state["tasks"])):
                    outcome = self._outcome(state, rounds, "STALLED")
                    break
                self._beat()
                if not progressed:
                    self._sleep(self.poll_seconds)
            else:
                outcome = self._outcome(state, rounds, "MAX_ROUNDS")
        finally:
            self._inference_pool.shutdown(wait=False, cancel_futures=True)
            self._stop_beat.set()
            beat.join(timeout=max(1.0, 2 * self.heartbeat_seconds))
            self._write_status("EXITED", outcome)
            try:
                snapshot = self.metrics.snapshot()
                atomic_write_json(self._dispatch_dir() / f"scheduling-{snapshot['segment_id']}.json", snapshot)
            except Exception as exc:  # diagnostic persistence must not change task outcomes
                self._event("SCHEDULING_METRICS_FAILED", message=str(exc))
        return outcome

    def _beat(self) -> None:
        now = time.monotonic()
        if now - self._heartbeat >= _HEARTBEAT_SECONDS:
            self._heartbeat = now
            self._write_status("RUNNING")

    def _outcome(self, state: dict | None, rounds: int, stop_reason: str, *,
                 finished: bool = False, finish: dict | None = None) -> dict:
        value = {
            "schema_version": 1,
            "run_id": self.run_id,
            "status": state["state"] if state else None,
            "finished": finished,
            "stop_reason": stop_reason,
            "rounds": rounds,
            "dispatches": self._dispatches,
            "scheduling": self.metrics.snapshot(),
            "orphans": self._orphans,
            "skipped": [{"task_id": key, "reason": value} for key, value in self._skip.items()],
            "errors": [*(state or {}).get("errors", []), *self._errors],
            "finish": finish,
        }
        entries = store.read_entries(service._store_path(self.handle))
        recoveries = []
        for task_id, entry in entries.items():
            code = (entry.get("error") or {}).get("code")
            spec = entry["spec"]
            if (entry["state"] == "FAILED" and
                    (code == "USAGE_LIMIT" or
                     (spec.get("operation") == "scan.assemble"
                      and code in {"OPERATION_ERROR", "OPERATION_FAILED"}))):
                recoveries.append({"task_id": task_id, "attempt": entry["attempt"],
                                   "error_class": code})
        value["recoverable"] = (not finished and any(
            row["error_class"] == "USAGE_LIMIT" for row in recoveries)) or (
                                stop_reason in {"USAGE_LIMIT", "FINISH_FAILED"}
                                or (stop_reason == "BLOCKED" and bool(recoveries)))
        value["recovery_tasks"] = recoveries
        self._event("RUNNER_STOPPED", stop_reason=stop_reason, status=value["status"],
                    finished=finished)
        return json.loads(json.dumps(value, default=str))

    def _finish(self, state: dict, rounds: int) -> dict:
        self._bind_late_evidence()           # a late transcript that arrived before finish
        try:
            report = service.finish(
                self.run_id,
                handle_loader=self.hooks.handle_loader,
                publisher=self.hooks.publisher,
                finalizer=self.hooks.finalizer,
            )
        except Exception as exc:  # noqa: BLE001 - surfaced verbatim, never swallowed
            self._error(None, f"finish failed: {type(exc).__name__}: {exc}")
            return self._outcome(state, rounds, "FINISH_FAILED")
        result = report.get("result") or {}
        publication = result.get("publication")
        finish = {
            "canonical_path": publication.get("canonical_path")
            if isinstance(publication, dict) else None,
            "finalization": result.get("finalization"),
        }
        return self._outcome(report, rounds, "FINISHED", finished=True, finish=finish)

    # ── launching ───────────────────────────────────────────────────────────────
    def _inference_count(self) -> int:
        return sum(1 for flight in self._inflight.values() if flight.kind == "INFERENCE")

    def _det_busy(self) -> bool:
        return any(flight.kind == "DETERMINISTIC" for flight in self._inflight.values())

    def _session_retries(self) -> list[tuple[dict, int]]:
        """Durable retry intent: SESSION tasks (including ticket reviews) held FAILED
        (= recorded retryable: an inference TASK_ATTEMPT class, an idempotent operation,
        or an operator's STALE_TASK) below ``SESSION_MAX_ATTEMPTS``."""
        retries = []
        for task_id, entry in store.read_entries(service._store_path(self.handle)).items():
            spec = entry["spec"]
            authorized = store.recovery_authorized(entry)
            code = (entry.get("error") or {}).get("code")
            if (spec.get("role") == "scan.l4.review"
                    and code != "USAGE_LIMIT"
                    and entry["state"] in {"FAILED", "BLOCKED"}
                    and (entry["state"] == "BLOCKED" or int(entry["attempt"]) >= _session_max_attempts())):
                self._review_failed(spec, (entry.get("error") or {}).get("code", "UNKNOWN"),
                                    (entry.get("error") or {}).get("message", ""))
            if (
                entry["state"] != "FAILED"
                or (spec.get("parent_task") is not None
                    and spec.get("role") != "scan.l4.review" and not authorized)
                or task_id in self._inflight
                or task_id in self._skip
                or (int(entry["attempt"]) >= _session_max_attempts() and not authorized)
            ):
                continue
            if (spec["kind"] == "INFERENCE" and code not in TASK_ATTEMPT
                    and code not in VALIDATION_REPAIR and not authorized):
                continue
            if spec.get("parent_task") is not None:
                from autoresearch.session_agent import legacy_scan
                try:
                    legacy_scan.validate_parent(self.handle, spec["parent_task"])
                except ValueError as exc:
                    self._review_failed(spec, "PARENT_NOT_RUNNING", str(exc))
                    continue
            retries.append((spec, int(entry["attempt"]) + 1))
        return retries

    def _capacity_pending(self) -> list[dict]:
        return [entry for entry in store.read_entries(service._store_path(self.handle)).values()
                if entry["state"] == "FAILED"
                and (entry.get("error") or {}).get("code") == "USAGE_LIMIT"
                and not store.recovery_authorized(entry)]

    def _launch(self, ready: list[dict], retries: list[tuple[dict, int]] = ()) -> bool:
        launched = False
        for task in ready:
            if task["owner"] == "SESSION" and task["task_id"] not in self._inflight:
                try:
                    entry = self._entry(task["task_id"])
                except KeyError:  # expansion may not have synced its store yet
                    continue
                if entry["state"] == "PENDING":
                    self.metrics.ready(task, entry["attempt"] + 1,
                                       "CAPACITY" if not self._can_start(task) else "READY")
        for task, attempt in retries:
            entry = self._entry(task["task_id"])
            if entry["state"] != "FAILED" or entry["attempt"] + 1 != attempt:
                continue
            self.metrics.ready(task, attempt, "RETRY_CAPACITY" if not self._can_start(task) else "RETRY_READY")
            if self._can_start(task):
                launched |= self._start(task, attempt)
        for task in ready:
            task_id = task["task_id"]
            if task_id in self._inflight or task_id in self._skip:
                continue
            if task["owner"] == "L4_TASKBOOK":
                launched |= self._claim_ticket(task)
            elif self._can_start(task):
                try:
                    entry = self._entry(task_id)
                except KeyError:          # expansion visible before its store sync
                    continue
                # Pure-lane callbacks can already have accepted this old READY row.
                if entry["state"] != "PENDING":
                    continue
                launched |= self._start(task, entry["attempt"] + 1)
        return launched

    def _can_start(self, task: dict) -> bool:
        if task["kind"] == "DETERMINISTIC":
            return not self._det_busy()
        return self._inference_count() < self.max_parallel and self._warmed(task)

    def _warming(self, tasks: list[dict]) -> bool:
        """有推理任务只因扇出预热还没发 —— 这是在等,不是卡死(STALLED)。"""
        return any(task.get("kind") == "INFERENCE" and task.get("owner") == "SESSION"
                   and not self._warmed(task) for task in tasks)

    def _warmed(self, task: dict) -> bool:
        """预热关、或本角色还没人发、或第一份已领先 `fanout_warmup_s` → 可以发。"""
        if self.fanout_warmup_s <= 0:
            return True
        first = self._role_first_start.get(str(task.get("role")))
        return first is None or self._monotonic() - first >= self.fanout_warmup_s

    def _claim_ticket(self, task: dict) -> bool:
        match = re.fullmatch(r"l4\.\d{6}\.a(\d+)", task["task_id"])
        if match is None:
            self._skip[task["task_id"]] = "unrecognised taskbook ticket id"
            return False
        try:
            service.claim(self.run_id, task["task_id"], int(match.group(1)),
                          handle_loader=self.hooks.handle_loader)
        except (RuntimeError, ValueError, KeyError) as exc:
            if "WAIT" not in str(exc):
                self._skip[task["task_id"]] = f"{type(exc).__name__}: {exc}"
            self._event("TICKET_NOT_CLAIMED", task_id=task["task_id"], reason=str(exc))
            return False
        self._event("TICKET_CLAIMED", task_id=task["task_id"])
        return True

    def _release_failed_claim(self, task: dict, attempt: int, exc: Exception) -> None:
        """A claim that raised after taking ownership must not leave RUNNING behind."""
        try:
            entry = self._entry(task["task_id"])
        except KeyError:
            entry = None
        if entry and entry["state"] == "RUNNING" and entry["attempt"] == attempt:
            try:
                service.fail(self.run_id, task["task_id"], attempt, "CLAIM_ERROR",
                             f"{type(exc).__name__}: {exc}"[:2000],
                             handle_loader=self.hooks.handle_loader)
            except Exception as fail_exc:  # noqa: BLE001
                self._error(task["task_id"], f"could not release claim: {fail_exc}")
        self._skip[task["task_id"]] = f"{type(exc).__name__}: {exc}"

    def _overlap_inference(self, can_dispatch: Callable[[], bool]) -> None:
        """Bounded owner callback: no deterministic execute, retry-l4 or finish."""
        if not can_dispatch():
            return
        self._harvest()
        if self._capacity_pending():
            return
        state = service.next(self.run_id, handle_loader=self.hooks.handle_loader)
        self._preflight()
        candidates = list(self._session_retries())
        for task in state["tasks"] if state["state"] == "READY" else []:
            if task["owner"] == "SESSION" and task["kind"] == "INFERENCE":
                attempt = self._entry(task["task_id"])["attempt"] + 1
                self.metrics.ready(task, attempt, "CAPACITY" if not self._can_start(task) else "PURE_LANE_READY")
                candidates.append((task, attempt))
        for task, attempt in candidates:
            if not can_dispatch():
                return
            if (task["kind"] == "INFERENCE" and task["task_id"] not in self._inflight
                    and task["task_id"] not in self._skip and self._can_start(task)):
                self._start(task, attempt, can_dispatch=can_dispatch)

    def _start(self, task: dict, attempt: int, *, can_dispatch=None) -> bool:
        task_id = task["task_id"]
        if self._capacity_pending():
            return False
        if task["kind"] == "DETERMINISTIC":
            try:
                params = _params_for(task, self._request())
            except (RunnerUnsupported, KeyError) as exc:
                self._skip[task_id] = str(exc)
                self._error(task_id, str(exc))
                return False
            try:
                service.claim(self.run_id, task_id, attempt,
                              handle_loader=self.hooks.handle_loader,
                              event_recorder=self.hooks.event_recorder)
            except Exception as exc:  # noqa: BLE001
                self._release_failed_claim(task, attempt, exc)
                return False
            future = cf.Future()
            self._inflight[task_id] = _Flight("DETERMINISTIC", task, attempt, future)
            self.metrics.started(task, attempt)
            self.metrics.occupancy(self._inference_count(), True)
            self._event("TASK_STARTED", task_id=task_id, attempt=attempt, kind="DETERMINISTIC")
            try:
                future.set_result(service.execute(
                    self.run_id, task_id, attempt, params,
                    handle_loader=self.hooks.handle_loader, runner=self.hooks.operation_runner,
                    owner_callback=self._overlap_inference if task["operation"] == "research.card.facts" else None))
            except Exception as exc:
                future.set_exception(exc)
            except BaseException:
                self._inflight.pop(task_id, None)
                raise
            finally:
                self.metrics.occupancy(self._inference_count(), False)
            return True
        try:
            request = build_request(
                self.handle, task, attempt, host_profile=self.host_profile,
                timeouts=self.timeouts, timeout_multiplier=self.timeout_multiplier)
        except Exception as exc:  # noqa: BLE001 - nothing claimed yet
            self._skip[task_id] = f"{type(exc).__name__}: {exc}"
            self._error(task_id, f"cannot render dispatch request: {exc}")
            return False
        if can_dispatch is not None and not can_dispatch():
            return False
        try:
            claimed = service.claim(self.run_id, task_id, attempt,
                                    handle_loader=self.hooks.handle_loader,
                                    event_recorder=self.hooks.event_recorder)["result"]
        except Exception as exc:  # noqa: BLE001
            self._release_failed_claim(task, attempt, exc)
            return False
        if can_dispatch is not None and not can_dispatch():
            service.fail(self.run_id, task_id, attempt, "INTERRUPTED", "capture cancelled before dispatch",
                         handle_loader=self.hooks.handle_loader)
            return False
        self._dispatch(task, attempt, request,
                       {"envelope": claimed["envelope"], "plan_hash": claimed["plan_hash"]})
        return True

    def _dispatch(self, task: dict, attempt: int, request: DispatchRequest, claim: dict) -> None:
        self._role_first_start.setdefault(str(task.get("role")), self._monotonic())
        self.metrics.started(task, attempt)
        future = self._inference_pool.submit(self.executor.dispatch, request)
        self._inflight[task["task_id"]] = _Flight(
            "INFERENCE", task, attempt, future, request=request, claim=claim)
        self.metrics.occupancy(self._inference_count(), any(
            flight.kind == "DETERMINISTIC" and not flight.future.done() for flight in self._inflight.values()))
        self._event("TASK_DISPATCHED", task_id=task["task_id"], attempt=attempt,
                    agent_type=request.agent_type, timeout_seconds=request.timeout_seconds)

    def _adopt_orphans(self) -> None:
        states = store.read_states(service._store_path(self.handle))
        for task in service._all_tasks(self.handle):
            if task["owner"] != "SESSION" or states.get(task["task_id"]) != "RUNNING":
                continue
            attempt = self._entry(task["task_id"])["attempt"]
            orphan = {"task_id": task["task_id"], "attempt": attempt, "kind": task["kind"]}
            hint = orphan_hint(self.run_id, task["task_id"], attempt)
            if task["kind"] == "INFERENCE" and supports_reattach(self.executor):
                try:
                    handoff = json.loads((
                        Path(self.handle.capsule) / "agents/session/requests"
                        / f"session-{task['task_id']}-a{attempt}.json"
                    ).read_text(encoding="utf-8"))
                    if artifacts.layout_version(self.handle) >= 2:
                        if 'dispatch_request' not in handoff:
                            raise RuntimeError('frozen dispatch request missing; explicit recovery required')
                        request = DispatchRequest.from_json(handoff['dispatch_request'])
                        from autoresearch.session_agent.task_access import activate_claim_access
                        activate_claim_access(
                            Path(self.handle.workspace) / 'session/dispatch'
                            / f"{task['task_id']}-a{attempt}.json")
                    else:
                        request = build_request(
                            self.handle, task, attempt, host_profile=self.host_profile,
                            timeouts=self.timeouts, timeout_multiplier=self.timeout_multiplier)
                    claim = {"envelope": handoff["envelope"], "plan_hash": handoff["plan_hash"]}
                except Exception as exc:  # noqa: BLE001 - one bad orphan is reported, not fatal
                    # e.g. a crash between store.claim and the handoff freeze: nothing to
                    # re-attach to.
                    error = f"{type(exc).__name__}: {exc}"[:500]
                    self._orphans.append({**orphan, "error": error, "hint": hint})
                    self._event("ORPHAN_UNRECOVERABLE", **orphan, error=error, hint=hint)
                    continue
                self._dispatch(task, attempt, request, claim)
                self._event("ORPHAN_REATTACHED", **orphan)
            else:
                self._orphans.append({**orphan, "hint": hint})
                self._event("ORPHAN_LEFT_RUNNING", **orphan, hint=hint)

    # ── harvesting ──────────────────────────────────────────────────────────────
    def _harvest(self) -> bool:
        progressed = False
        for task_id, flight in list(self._inflight.items()):
            if not flight.future.done():
                continue
            del self._inflight[task_id]
            self.metrics.occupancy(self._inference_count(), any(
                row.kind == "DETERMINISTIC" and not row.future.done() for row in self._inflight.values()))
            progressed = True
            if flight.kind == "DETERMINISTIC":
                self._settle_deterministic(flight)
            else:
                self._settle_inference(flight)
        return progressed

    def _settle_deterministic(self, flight: _Flight) -> None:
        task_id = flight.task["task_id"]
        exc = flight.future.exception()
        if exc is not None:
            self._fail(flight, "OPERATION_ERROR", f"{type(exc).__name__}: {exc}")
            return
        entry = self._entry(task_id)
        if entry["state"] == "SUCCEEDED":
            self._record(flight, "SUCCEEDED")
            return
        self._record(flight, f"FAILED:{(entry.get('error') or {}).get('code')}",
                     error_class=(entry.get("error") or {}).get("code"))
        # A FAILED (retryable) entry is retried by _session_retries from the store.

    def _settle_inference(self, flight: _Flight) -> None:
        task, attempt, request = flight.task, flight.attempt, flight.request
        try:
            result = flight.future.result()
        except ExecutorTimeout as exc:
            # Nothing was reported for this attempt: record it ABANDONED (the timeout is
            # the reason) so a late transcript can still be bound to it, and so the
            # closure does not demand evidence that cannot exist (review I4).
            try:
                from autoresearch.session_agent.evidence import freeze_abandonment

                freeze_abandonment(self.handle, task, attempt, str(exc),
                                   executor=getattr(self.executor, "name", None))
            except Exception as record_exc:  # noqa: BLE001 - reported, the failure proceeds
                self._error(task["task_id"], f"abandonment record failed: {record_exc}")
            self._fail(flight, "TIMEOUT", str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - classified like l4-stock.js
            self._fail(flight, classify_error(str(exc)), f"{type(exc).__name__}: {exc}")
            return
        if not result.ok:
            self._fail(flight, classify_error(result.error, result.error_class),
                       result.error or "executor reported failure", result)
            return
        outputs = []
        for artifact_id in task["output_artifact_ids"]:
            try:
                if artifacts.layout_version(self.handle) >= 2:
                    from autoresearch.common.atomic import sha256_file
                    descriptor = {'sha256': sha256_file(Path(request.output_paths[artifact_id]))}
                else:
                    descriptor = artifacts.bind_artifact_hash(self.handle, artifact_id)
            except (KeyError, ValueError, RuntimeError, OSError) as exc:
                self._fail(flight, "CONTRACT_ERROR",
                           f"output {artifact_id} missing or changed at "
                           f"{request.output_paths.get(artifact_id)}: {exc}", result)
                return
            outputs.append({"artifact_id": artifact_id, "sha256": descriptor["sha256"]})
        evidence_refs = self._bind_evidence(request, result)
        receipt = None
        if task["independent_context"]:
            if not evidence_refs:
                self._fail(flight, "EVIDENCE_MISSING",
                           f"independent task {task['task_id']} a{attempt} needs bound "
                           "transcript evidence (host-binding); none was supplied", result)
                return
            receipt = {
                "schema_version": 1,
                "engine": self.handle.engine,
                "session_ref": result.session_ref or request.host_session_ref,
                "context_ref": result.context_ref,
                "parent_context_ref": result.parent_context_ref or request.host_session_ref,
                "task_id": task["task_id"],
                "attempt": attempt,
                "completed": True,
                "evidence_refs": list(evidence_refs),
            }
        submission = {
            "schema_version": 1,
            "envelope": flight.claim["envelope"],
            "plan_hash": flight.claim["plan_hash"],
            "outputs": outputs,
            "host_receipt_id": (
                sha256_bytes(canonical_json(receipt).encode("utf-8")) if receipt else None
            ),
        }
        try:
            service.submit(
                self.run_id, submission, handle_loader=self.hooks.handle_loader,
                validator=self.hooks.validator, host_receipt=receipt,
                event_recorder=self.hooks.event_recorder)
        except Exception as exc:  # noqa: BLE001 - contract/identity rejection of the output
            self._fail(flight, submit_error_class(exc), f"{type(exc).__name__}: {exc}", result)
            return
        self._record(flight, "SUBMITTED", result)

    def _bind_late_evidence(self) -> None:
        """Bind late results of abandoned attempts (executor ``late_results()``) as
        evidence of *that* attempt — never submitted, never accepted (review I4)."""
        source = getattr(self.executor, "late_results", None)
        if not callable(source):
            return
        from autoresearch.session_agent.evidence import read_abandonment

        try:
            late = list(source())
        except Exception as exc:  # noqa: BLE001 - evidence gaps are reported, not fatal
            self._error(None, f"late results unavailable: {type(exc).__name__}: {exc}")
            return
        for request, result in late:
            key = (request.task_id, request.attempt)
            if (
                key in self._late_bound
                or request.task_id in self._inflight          # not harvested yet
                or read_abandonment(self.handle, request.task_id, request.attempt) is None
            ):
                continue
            self._late_bound.add(key)
            refs = self._bind_evidence(request, result)
            self._ledger({
                "ts": _now(), "task_id": request.task_id, "attempt": request.attempt,
                "kind": "INFERENCE", "role": request.role, "agent_type": request.agent_type,
                "outcome": "LATE_RESULT", "error_class": None,
                "session_ref": result.session_ref, "context_ref": result.context_ref,
                "transcript_path": result.transcript_path,
                "usage": dict(result.usage) if result.usage else None,
            })
            self._event("LATE_EVIDENCE_BOUND", task_id=request.task_id,
                        attempt=request.attempt, bound=bool(refs))

    def _bind_evidence(self, request: DispatchRequest, result: DispatchResult) -> tuple:
        if result.evidence_refs:
            return tuple(result.evidence_refs)
        try:
            return tuple(self.evidence_binder(request, result) or ())
        except Exception as exc:  # noqa: BLE001 - evidence gaps are reported, not fatal
            self._error(request.task_id, f"transcript binding failed: {exc}")
            return ()

    def _fail(self, flight: _Flight, error_class: str, message: str,
              result: DispatchResult | None = None) -> None:
        task, attempt = flight.task, flight.attempt
        self._event("TASK_FAILING", task_id=task["task_id"], attempt=attempt,
                    error_class=error_class, message=message[:500])
        if result is not None and flight.request is not None:
            # The agent reported back: bind its transcript while the attempt still runs,
            # so the failed attempt is evidenced like any other (review I4; idempotent).
            self._bind_evidence(flight.request, result)
        try:
            service.fail(self.run_id, task["task_id"], attempt, error_class, message[:2000],
                         handle_loader=self.hooks.handle_loader)
        except Exception as exc:  # noqa: BLE001 - keep the root cause, not just the refusal
            self._error(task["task_id"],
                        f"{error_class}: {message[:1000]} (not recorded as a task failure: {exc})")
            return
        self._record(flight, f"FAILED:{error_class}", result, error_class)
        # Retries (SESSION: _session_retries; L4 intel/card: _l4_retries) are derived
        # from the durable store / taskbook each round, never queued here (review I3).

    def _review_failed(self, task: dict, error_class: str, message: str) -> None:
        """Expose an unrecoverable review without re-spending the successful card."""
        subject = str((task.get("parent_task") or {}).get("subject") or task.get("subject"))
        if any(item.get("task_id") == task["task_id"]
               and str(item.get("code", "")).startswith("REVIEW_UNAVAILABLE") for item in self._errors):
            return
        self._errors.append({"code": "REVIEW_UNAVAILABLE", "task_id": task["task_id"],
                             "subject": subject, "reason": error_class, "message": message[:1000]})
        self._event("REVIEW_UNAVAILABLE", task_id=task["task_id"], subject=subject,
                    error_class=error_class)

    def _l4_retries(self) -> dict[str, int]:
        """Durable retry-l4 intent from the taskbook: a ticket FAILED with a TASK_ATTEMPT
        class below ``MAX_ATTEMPTS`` whose failed child is not a review and whose retry
        subtree does not exist yet; tickets with a child still in flight wait."""
        from autoresearch.session_agent import legacy_scan

        if not legacy_scan.taskbook_path(self.handle).is_file():
            return {}
        from autoresearch.scan.l4_tasks import MAX_ATTEMPTS

        entries = store.read_entries(service._store_path(self.handle))
        in_flight = {
            (flight.task.get("parent_task") or {}).get("subject")
            for flight in self._inflight.values()
        }
        wanted = {}
        for code, ticket in (legacy_scan._payload(self.handle).get("tasks") or {}).items():
            attempt = int(ticket.get("attempt") or 0)
            last_class = str(ticket.get("last_error_class") or "")
            if (
                ticket.get("status") != "FAILED"
                or (last_class not in TASK_ATTEMPT and last_class not in VALIDATION_REPAIR)
                or attempt < 1
                or attempt + 1 > MAX_ATTEMPTS
                or code in in_flight
                or code in self._l4_refused
                or f"l4.{code}.a{attempt + 1}.card" in entries      # already expanded
            ):
                continue
            children = {
                task_id: entry for task_id, entry in entries.items()
                if (entry["spec"].get("parent_task") or {}).get("subject") == code
                and (entry["spec"].get("parent_task") or {}).get("attempt") == attempt
            }
            if any(entry["state"] == "RUNNING" for entry in children.values()):
                continue                   # an orphaned child: STALLED path reports it
            reviews = [
                (task_id, entry) for task_id, entry in children.items()
                if _is_review(task_id) and entry["state"] in {"FAILED", "BLOCKED"}
            ]
            if reviews:
                task_id, entry = reviews[0]
                self._review_failed(entry["spec"], "PARENT_NOT_RUNNING",
                                    "Historical review failure left the parent ticket FAILED; "
                                    "explicit recovery is required, the accepted card is retained.")
                continue
            wanted[code] = attempt + 1
        return wanted

    def _run_l4_retries(self) -> bool:
        progressed = False
        for code, attempt in self._l4_retries().items():
            try:
                service.retry_l4(self.run_id, code, attempt,
                                 handle_loader=self.hooks.handle_loader)
            except (RuntimeError, KeyError, ValueError) as exc:
                # No child of this ticket is in flight here (checked above), so a
                # "not quiescent" refusal means a child is RUNNING without a live owner.
                self._l4_refused.add(code)
                self._error(f"l4.{code}", f"L4 retry refused: {exc}")
            else:
                self._event("L4_RETRY_EXPANDED", code=code, attempt=attempt)
                progressed = True
        return progressed


def run_loop(run_id: str, executor, **options) -> dict:
    """Run ``run_id`` to completion (or to a reported stop); see :class:`Runner`."""
    return Runner(run_id, executor, **options).run()


__all__ = [
    "DISPATCH_DIR", "SESSION_MAX_ATTEMPTS", "Runner", "RunnerUnsupported", "ServiceHooks",
    "bind_transcript_evidence", "run_loop", "submit_error_class",
]
