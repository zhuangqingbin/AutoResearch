"""Process-segment scheduling diagnostics use monotonic elapsed time."""
from autoresearch.scan.observability import SchedulingMetrics


def test_metrics_distinguish_start_accept_and_observed_queue_lower_bound():
    now = [10.0]
    metrics = SchedulingMetrics(2, monotonic=lambda: now[0], wall_clock=lambda: "wall")
    a = {"task_id": "l4.600519.a1.card", "role": "stock.card"}
    b = {"task_id": "l4.600519.a1.review2", "role": "ensemble.review"}
    metrics.ready(a, 1, "DETERMINISTIC_LANE_BUSY")
    metrics.occupancy(0, True)
    now[0] += 3
    metrics.started(a, 1)
    metrics.occupancy(1, False)
    now[0] += 4
    metrics.accepted(a, 1)
    metrics.started(b, 1)
    value = metrics.snapshot()
    assert value["first_card"]["started"]["monotonic_seconds"] == 3
    assert value["last_card"]["accepted"]["monotonic_seconds"] == 7
    assert value["first_review"]["accepted"] is None
    assert value["deterministic_lane_busy_seconds"] == 3
    assert value["slot_idle_seconds"] == 10
    assert value["ready_queue_wait"]["l4.600519.a1.card@1"]["seconds"] == 3
    assert value["ready_queue_wait"]["l4.600519.a1.card@1"]["measurement"] == "OBSERVED_LOWER_BOUND"
    assert value["historical_measurements"] == "UNKNOWN"
    value["first_card"]["started"]["task_id"] = "mutated"
    assert metrics.snapshot()["first_card"]["started"]["task_id"] == a["task_id"]
    assert SchedulingMetrics(2).snapshot()["segment_id"] != value["segment_id"]


def test_capacity_queue_reason_updates_without_resetting_observed_wait():
    now = [0.0]
    metrics = SchedulingMetrics(1, monotonic=lambda: now[0])
    task = {"task_id": "l4.600519.a1.card", "role": "scan.l4.card"}
    metrics.ready(task, 1, "READY")
    now[0] = 2
    metrics.ready(task, 1, "CAPACITY")
    metrics.started(task, 1)
    row = metrics.snapshot()["ready_queue_wait"]["l4.600519.a1.card@1"]
    assert row["queued_reason"] == "CAPACITY"
    assert row["seconds"] == 2
