"""`scan_config.observability`:结果账本 / 读数的样本门与 lint 阈(2026-09-27 P3)。只影响观测与展示,不改选股。"""
from __future__ import annotations


def observability_cfg(cfg: dict | None = None) -> dict:
    from autoresearch.scan.user_config import knob

    k = knob
    return {"min_ledger_n": int(k("observability", "min_ledger_n", None, 20, cfg)),
            "min_session_n": int(k("observability", "min_session_n", None, 20, cfg)),
            "realized_min_n": int(k("observability", "realized_min_n", None, 20, cfg)),
            "winner_decile": float(k("observability", "winner_decile", None, 0.9, cfg)),
            "swing_readout_min_days": int(k("observability", "swing_readout_min_days", None, 40, cfg)),
            "swing_readout_min_clusters": int(k("observability", "swing_readout_min_clusters", None, 10, cfg)),
            "menu_knife_tolerance": float(k("observability", "menu_knife_tolerance", None, 0.06, cfg)),
            "price_claim_tol_pp": float(k("observability", "price_claim_tol_pp", None, 1.5, cfg)),
            "unknown_rate_tolerance": float(k("observability", "unknown_rate_tolerance", None, 0.05, cfg)),
            "nan_warn": float(k("observability", "nan_warn", None, 0.30, cfg))}


class SchedulingMetrics:
    """Derived process-local measurements; never a source of task state."""

    def __init__(self, max_parallel, *, monotonic=None, wall_clock=None):
        import threading
        import time
        import uuid
        from datetime import datetime, timezone

        self._clock = monotonic or time.monotonic
        self._wall = wall_clock or (lambda: datetime.now(timezone.utc).isoformat())
        self._lock = threading.Lock()
        self._start = self._last = self._clock()
        self._slots = max_parallel
        self._active = 0
        self._busy = False
        self._ready = {}
        self._value = {
            "schema_version": 1, "segment_id": uuid.uuid4().hex,
            "source": "RUNNER_PROCESS_MONOTONIC", "started_at": self._wall(),
            "historical_measurements": "UNKNOWN", "max_parallel": max_parallel,
            "slot_idle_seconds": 0.0, "deterministic_lane_busy_seconds": 0.0,
            "ready_queue_wait": {},
            **{key: {"started": None, "accepted": None} for key in
               ("first_card", "last_card", "first_review", "last_review")},
        }

    def _elapsed(self, now):
        elapsed = max(0.0, now - self._last)
        return (elapsed * max(0, self._slots - self._active), elapsed if self._busy else 0.0)

    def occupancy(self, inference, deterministic):
        with self._lock:
            now = self._clock()
            idle, busy = self._elapsed(now)
            self._value["slot_idle_seconds"] += idle
            self._value["deterministic_lane_busy_seconds"] += busy
            self._last, self._active, self._busy = now, inference, deterministic
            if deterministic:
                for key in self._ready:
                    self._value["ready_queue_wait"][key]["queued_reason"] = "DETERMINISTIC_LANE_BUSY"

    def ready(self, task, attempt, reason):
        key = f"{task['task_id']}@{attempt}"
        with self._lock:
            self._ready.setdefault(key, self._clock())
            self._value["ready_queue_wait"].setdefault(key, {
                "seconds": None, "measurement": "OBSERVED_LOWER_BOUND",
                "queued_reason": reason, "first_observed_at": self._wall(),
            })
            if reason in {"CAPACITY", "RETRY_CAPACITY"}:
                self._value["ready_queue_wait"][key]["queued_reason"] = reason

    def _milestone(self, task, attempt, phase):
        key = f"{task['task_id']}@{attempt}"
        with self._lock:
            now = self._clock()
            if phase == "started" and key in self._ready:
                self._value["ready_queue_wait"][key]["seconds"] = max(0, now - self._ready.pop(key))
            kind = ("review" if task["task_id"].endswith((".review2", ".review3"))
                    else "card" if task.get("role") in {"stock.card", "scan.l4.card"}
                    and task.get("expected_output_contract") != "research.card.initial.v1" else None)
            if kind:
                event = {"task_id": task["task_id"], "attempt": attempt,
                         "monotonic_seconds": max(0, now - self._start), "wall_clock": self._wall()}
                if self._value[f"first_{kind}"][phase] is None:
                    self._value[f"first_{kind}"][phase] = event
                self._value[f"last_{kind}"][phase] = event

    def started(self, task, attempt):
        self._milestone(task, attempt, "started")

    def accepted(self, task, attempt):
        self._milestone(task, attempt, "accepted")

    def snapshot(self):
        from copy import deepcopy

        with self._lock:
            value = deepcopy(self._value)
            now = self._clock()
            idle, busy = self._elapsed(now)
            value["slot_idle_seconds"] += idle
            value["deterministic_lane_busy_seconds"] += busy
            value["elapsed_seconds"] = max(0, now - self._start)
            for key, observed in self._ready.items():
                value["ready_queue_wait"][key]["seconds"] = max(0, now - observed)
            return value
