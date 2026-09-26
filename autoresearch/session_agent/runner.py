"""session_v1 runner — drive one frozen task graph to ``DONE``, then ``finish``.

Scheduling only, no judgment: the task graph, attempts, receipts, validation and
publication stay in ``service`` / ``store`` / ``publication``.  Each round:

1. **harvest** finished work — inference: hash every output file itself (the driver
   trusts files, never an executor's word), bind transcript evidence, ``submit``;
   failures go through ``service.fail`` and ``contracts.retry.TASK_ATTEMPT`` classes
   (TIMEOUT …) get exactly one new attempt (L4 intel/card: one new taskbook attempt
   via ``service.retry_l4``; a review2/review3 failure stops the run ``REVIEW_FAILED``
   because retry-l4 never rebuilds the review);
2. read ``service.next``;
3. **launch** READY tasks — L4 taskbook tickets are *claimed* (the taskbook preflight is
   the ticket's execution), deterministic tasks run through ``service.execute`` on one
   deterministic lane (``trace.exec_capture`` evidence, same as the CLI path), inference
   tasks go to the executor, at most ``max_parallel`` in flight.

Threads: only the loop thread and the deterministic lane call the service; executor
threads only run ``executor.dispatch``.  Store / artifact / event writes are flock-protected.

A crash between ``claim`` and ``submit`` leaves the attempt RUNNING.  On restart the
runner never claims it again: an executor with ``supports_reattach`` re-attaches to the
same attempt (mailbox: same request file), otherwise the attempt is reported as an
orphan and the run stops ``STALLED`` for an operator decision (``resume`` / ``fail``).
"""
from __future__ import annotations

import concurrent.futures as cf
import fcntl
import json
import os
import re
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from autoresearch.common.atomic import atomic_write_json, canonical_json, sha256_bytes
from autoresearch.contracts.retry import TASK_ATTEMPT
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
_NO_PARAMS = {"type": "object", "required": [], "additionalProperties": False}
_HEARTBEAT_SECONDS = 5.0


class RunnerUnsupported(RuntimeError):
    """The runner has no parameter source for a deterministic operation."""


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


def _params_for(task: dict) -> dict:
    """Parameters of a deterministic operation (scan operations take none)."""
    operation = task["operation"]
    if operation == "test.noop":
        return {"message": f"runner {task['task_id']}"[:200]}
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
    if snapshot.last_ordinal is None:
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
        end_ordinal=snapshot.last_ordinal,
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
    ):
        if type(max_parallel) is not int or max_parallel < 1:
            raise ValueError("max_parallel must be a positive integer")
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
        self._inflight: dict[str, _Flight] = {}
        self._retry_queue: list[tuple[dict, int]] = []
        self._l4_retry: dict[str, int] = {}
        self._skip: dict[str, str] = {}
        self._orphans: list[dict] = []
        self._dispatches: list[dict] = []
        self._errors: list[dict] = []
        self._started_at = _now()
        self._heartbeat = 0.0

    # ── service plumbing ────────────────────────────────────────────────────────
    def _handle(self):
        from autoresearch.trace.capsule import require_active_run

        return (self.hooks.handle_loader or require_active_run)(self.run_id)

    def _entry(self, task_id: str) -> dict:
        return store.read_entry(service._store_path(self.handle), task_id)

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
        atomic_write_json(self._dispatch_dir() / "runner.json", {
            "schema_version": 1,
            "run_id": self.run_id,
            "pid": os.getpid(),
            "executor": getattr(self.executor, "name", type(self.executor).__name__),
            "state": state,
            "started_at": self._started_at,
            "updated_at": _now(),
            "in_flight": sorted(self._inflight),
            "outcome": outcome,
        })

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
        self._ledger(row)
        self._event("TASK_" + outcome.split(":")[0], task_id=row["task_id"],
                    attempt=row["attempt"], error_class=error_class)

    # ── main loop ───────────────────────────────────────────────────────────────
    def run(self) -> dict:
        self.handle = self._handle()
        self.host_profile = json.loads(
            (service._session_dir(self.handle) / "host_profile.json").read_text("utf-8")
        )
        self._write_status("RUNNING")
        self._inference_pool = cf.ThreadPoolExecutor(
            max_workers=self.max_parallel, thread_name_prefix="session-inference")
        self._det_pool = cf.ThreadPoolExecutor(max_workers=1, thread_name_prefix="session-det")
        outcome = None
        state = None
        rounds = 0
        try:
            self._adopt_orphans()
            while rounds < self.max_rounds:
                rounds += 1
                progressed = self._harvest()
                if self._det_busy():
                    # A deterministic execute may be mid-expansion (expansion file on disk,
                    # store/artifacts not yet synced): read the graph only between executes.
                    self._beat()
                    if not progressed:
                        self._sleep(self.poll_seconds)
                    continue
                progressed |= self._run_l4_retries()
                state = service.next(self.run_id, handle_loader=self.hooks.handle_loader)
                status = state["state"]
                if not self._inflight and not self._retry_queue:
                    if status == "DONE":
                        outcome = self._finish(state, rounds)
                        break
                    if status == "BLOCKED" and not self._l4_retry:
                        outcome = self._outcome(state, rounds, "BLOCKED")
                        break
                    if status == "WAITING":
                        outcome = self._outcome(state, rounds, "STALLED")
                        break
                launched = self._launch(state["tasks"] if status == "READY" else [])
                progressed |= launched
                if status == "READY" and not launched and not self._inflight:
                    outcome = self._outcome(state, rounds, "STALLED")
                    break
                self._beat()
                if not progressed:
                    self._sleep(self.poll_seconds)
            else:
                outcome = self._outcome(state, rounds, "MAX_ROUNDS")
        finally:
            self._inference_pool.shutdown(wait=False, cancel_futures=True)
            self._det_pool.shutdown(wait=False, cancel_futures=True)
            self._write_status("EXITED", outcome)
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
            "orphans": self._orphans,
            "skipped": [{"task_id": key, "reason": value} for key, value in self._skip.items()],
            "errors": [*(state or {}).get("errors", []), *self._errors],
            "finish": finish,
        }
        self._event("RUNNER_STOPPED", stop_reason=stop_reason, status=value["status"],
                    finished=finished)
        return json.loads(json.dumps(value, default=str))

    def _finish(self, state: dict, rounds: int) -> dict:
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

    def _launch(self, ready: list[dict]) -> bool:
        launched = False
        pending_retries, self._retry_queue = self._retry_queue, []
        for task, attempt in pending_retries:
            if self._can_start(task):
                launched |= self._start(task, attempt)
            else:
                self._retry_queue.append((task, attempt))
        for task in ready:
            task_id = task["task_id"]
            if task_id in self._inflight or task_id in self._skip:
                continue
            if task["owner"] == "L4_TASKBOOK":
                launched |= self._claim_ticket(task)
            elif self._can_start(task):
                try:
                    attempt = self._entry(task_id)["attempt"] + 1
                except KeyError:          # expansion visible before its store sync
                    continue
                launched |= self._start(task, attempt)
        return launched

    def _can_start(self, task: dict) -> bool:
        if task["kind"] == "DETERMINISTIC":
            return not self._det_busy()
        return self._inference_count() < self.max_parallel

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

    def _start(self, task: dict, attempt: int) -> bool:
        task_id = task["task_id"]
        if task["kind"] == "DETERMINISTIC":
            try:
                params = _params_for(task)
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
            future = self._det_pool.submit(
                service.execute, self.run_id, task_id, attempt, params,
                handle_loader=self.hooks.handle_loader, runner=self.hooks.operation_runner)
            self._inflight[task_id] = _Flight("DETERMINISTIC", task, attempt, future)
            self._event("TASK_STARTED", task_id=task_id, attempt=attempt, kind="DETERMINISTIC")
            return True
        try:
            request = build_request(
                self.handle, task, attempt, host_profile=self.host_profile,
                timeouts=self.timeouts, timeout_multiplier=self.timeout_multiplier)
        except Exception as exc:  # noqa: BLE001 - nothing claimed yet
            self._skip[task_id] = f"{type(exc).__name__}: {exc}"
            self._error(task_id, f"cannot render dispatch request: {exc}")
            return False
        try:
            claimed = service.claim(self.run_id, task_id, attempt,
                                    handle_loader=self.hooks.handle_loader,
                                    event_recorder=self.hooks.event_recorder)["result"]
        except Exception as exc:  # noqa: BLE001
            self._release_failed_claim(task, attempt, exc)
            return False
        self._dispatch(task, attempt, request,
                       {"envelope": claimed["envelope"], "plan_hash": claimed["plan_hash"]})
        return True

    def _dispatch(self, task: dict, attempt: int, request: DispatchRequest, claim: dict) -> None:
        future = self._inference_pool.submit(self.executor.dispatch, request)
        self._inflight[task["task_id"]] = _Flight(
            "INFERENCE", task, attempt, future, request=request, claim=claim)
        self._event("TASK_DISPATCHED", task_id=task["task_id"], attempt=attempt,
                    agent_type=request.agent_type, timeout_seconds=request.timeout_seconds)

    def _adopt_orphans(self) -> None:
        states = store.read_states(service._store_path(self.handle))
        for task in service._all_tasks(self.handle):
            if task["owner"] != "SESSION" or states.get(task["task_id"]) != "RUNNING":
                continue
            attempt = self._entry(task["task_id"])["attempt"]
            orphan = {"task_id": task["task_id"], "attempt": attempt, "kind": task["kind"]}
            if task["kind"] == "INFERENCE" and supports_reattach(self.executor):
                handoff = json.loads((
                    Path(self.handle.capsule) / "agents/session/requests"
                    / f"session-{task['task_id']}-a{attempt}.json"
                ).read_text(encoding="utf-8"))
                request = build_request(
                    self.handle, task, attempt, host_profile=self.host_profile,
                    timeouts=self.timeouts, timeout_multiplier=self.timeout_multiplier)
                self._dispatch(task, attempt, request, {
                    "envelope": handoff["envelope"], "plan_hash": handoff["plan_hash"]})
                self._event("ORPHAN_REATTACHED", **orphan)
            else:
                self._orphans.append(orphan)
                self._event("ORPHAN_LEFT_RUNNING", **orphan,
                            hint="session_agent resume; then fail --error-class STALE_TASK")

    # ── harvesting ──────────────────────────────────────────────────────────────
    def _harvest(self) -> bool:
        progressed = False
        for task_id, flight in list(self._inflight.items()):
            if not flight.future.done():
                continue
            del self._inflight[task_id]
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
        if (
            entry["state"] == "FAILED"
            and flight.task["parent_task"] is None
            and flight.attempt < SESSION_MAX_ATTEMPTS
        ):
            self._retry_queue.append((flight.task, flight.attempt + 1))

    def _settle_inference(self, flight: _Flight) -> None:
        task, attempt, request = flight.task, flight.attempt, flight.request
        try:
            result = flight.future.result()
        except ExecutorTimeout as exc:
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
            self._fail(flight, "CONTRACT_ERROR", f"{type(exc).__name__}: {exc}", result)
            return
        self._record(flight, "SUBMITTED", result)

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
        try:
            service.fail(self.run_id, task["task_id"], attempt, error_class, message[:2000],
                         handle_loader=self.hooks.handle_loader)
        except Exception as exc:  # noqa: BLE001 - keep the root cause, not just the refusal
            self._error(task["task_id"],
                        f"{error_class}: {message[:1000]} (not recorded as a task failure: {exc})")
            return
        self._record(flight, f"FAILED:{error_class}", result, error_class)
        if error_class not in TASK_ATTEMPT:
            return
        parent = task.get("parent_task")
        if parent is None:
            if (self._entry(task["task_id"])["state"] == "FAILED"
                    and attempt < SESSION_MAX_ATTEMPTS):
                self._retry_queue.append((task, attempt + 1))
            return
        if _is_review(task["task_id"]):
            self._review_failed(task, error_class, message)
            return
        from autoresearch.scan.l4_tasks import MAX_ATTEMPTS

        next_attempt = int(parent["attempt"]) + 1
        if next_attempt <= MAX_ATTEMPTS:
            code = str(parent["subject"])
            self._l4_retry[code] = max(self._l4_retry.get(code, 0), next_attempt)

    def _review_failed(self, task: dict, error_class: str, message: str) -> None:
        """retry-l4 rebuilds ticket/slim/intel/card only — never the review — so a
        review TASK_ATTEMPT failure stops the run instead of re-spending intel + card."""
        subject = str((task.get("parent_task") or {}).get("subject") or task.get("subject"))
        entry = {"code": f"REVIEW_FAILED:{error_class}", "task_id": task["task_id"],
                 "subject": subject, "message": message[:1000]}
        if entry not in self._errors:
            self._errors.append(entry)
            self._event("REVIEW_FAILED", task_id=task["task_id"], subject=subject,
                        error_class=error_class)

    def _run_l4_retries(self) -> bool:
        progressed = False
        for code, attempt in list(self._l4_retry.items()):
            busy = any(
                (flight.task.get("parent_task") or {}).get("subject") == code
                for flight in self._inflight.values()
            )
            if busy:
                continue
            try:
                service.retry_l4(self.run_id, code, attempt,
                                 handle_loader=self.hooks.handle_loader)
            except (RuntimeError, KeyError, ValueError) as exc:
                # No child of this ticket is in flight here (checked above), so a
                # "not quiescent" refusal means a child is RUNNING without a live owner.
                self._error(f"l4.{code}", f"L4 retry refused: {exc}")
            else:
                self._event("L4_RETRY_EXPANDED", code=code, attempt=attempt)
                progressed = True
            del self._l4_retry[code]
        return progressed


def run_loop(run_id: str, executor, **options) -> dict:
    """Run ``run_id`` to completion (or to a reported stop); see :class:`Runner`."""
    return Runner(run_id, executor, **options).run()


__all__ = [
    "DISPATCH_DIR", "SESSION_MAX_ATTEMPTS", "Runner", "RunnerUnsupported", "ServiceHooks",
    "bind_transcript_evidence", "run_loop",
]
