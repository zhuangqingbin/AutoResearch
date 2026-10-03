"""SIMULATED actual-runner comparison, using controlled futures and a monotonic clock."""
import concurrent.futures as cf
import json
from collections import Counter
from pathlib import Path

from autoresearch.common.atomic import canonical_json, sha256_bytes
from autoresearch.contracts.session_plan import plan_hash
from autoresearch.session_agent import artifacts, host_evidence, runner, service
from autoresearch.session_agent.executors.base import ExecutorTimeout
from autoresearch.session_agent.workflows import scan

from ._runner_support import RUN_ID
from .test_scan_runner_full import (
    CODE,
    _FakeScanModels,
    _plain_hooks,
    _scan_run,
    _ThreeStockOperations,
)


class Clock:
    def __init__(self):
        self.now = 0.0
        self.jobs = []

    def advance(self, seconds):
        target = self.now + seconds
        while self.jobs and min(job[0] for job in self.jobs) <= target:
            job = min(self.jobs, key=lambda row: row[0])
            self.jobs.remove(job)
            self.now = job[0]
            _, future, fn, args = job
            try:
                future.set_result(fn(*args))
            except Exception as exc:
                future.set_exception(exc)
        self.now = target

    def sleep(self, seconds):
        assert self.jobs, "runner is waiting without scheduled work"
        self.advance(max(0, min(job[0] for job in self.jobs) - self.now))

    def submit(self, fn, *args):
        request = args[0]
        delay = 1.0
        if request.task_id == f"l4.{CODE}.a1.card":
            delay = 40.0  # B's slow card must not block A's third review.
        future = cf.Future()
        self.jobs.append((self.now + delay, future, fn, args))
        return future

    def shutdown(self, **kwargs):
        assert not self.jobs


class Operations(_ThreeStockOperations):
    def _content(self, operation, artifact_id):
        value = super()._content(operation, artifact_id)
        if artifact_id == "scan.review.decision":
            next(row for row in value["decisions"] if row["code"] == "000002").update(
                same_tier=False, review3_required=True, review2_rating="Hold")
        return value


def test_same_inputs_new_graph_starts_review_early_and_preserves_business_outputs(tmp_path, monkeypatch):
    build = scan.build_scan_plan
    records = {}
    monkeypatch.setattr(host_evidence, "resolve_receipt_evidence", lambda *args: ["synthetic"])
    for version in ("legacy", "per_stock"):
        def planner(request, handle, version=version):
            plan = build(request, handle)
            if version == "legacy":
                plan["task_templates"] = [row for row in plan["task_templates"]
                                          if row["template_id"] != "scan.review.join"]
                for row in plan["task_templates"]:
                    if row["template_id"] in {"scan.reviews", "scan.review3"}:
                        row["expander"] = row["template_id"]
                plan["plan_hash"] = plan_hash(plan)
            return plan
        monkeypatch.setattr(scan, "build_scan_plan", planner)
        handle = _scan_run(tmp_path / version, monkeypatch)
        clock = Clock()
        monkeypatch.setattr(runner.cf, "ThreadPoolExecutor", lambda _clock=clock, **kwargs: _clock)
        operations = Operations(handle)
        calls, events = [], []
        class Models(_FakeScanModels):
            def dispatch(self, request, calls=calls, handle=handle, operations=operations):
                calls.append((request.task_id, request.attempt))
                if request.task_id == "l4.000001.a1.card":
                    assert not service.status(RUN_ID, handle_loader=lambda _: handle)["result"]["coverage"]["report_completeness"]["complete"]
                    raise ExecutorTimeout("SIMULATED local card timeout")
                worker = _FakeScanModels(branchy=request.subject == "000002")
                result = worker.dispatch(request)
                if request.subject in operations.codes:
                    for path in request.output_paths.values():
                        target = Path(path)
                        content = target.read_text().replace(CODE, request.subject)
                        if request.task_id.endswith(".review2"):
                            content = content.replace("Rating**: Sell", "Rating**: Hold").replace("**SELL**", "**HOLD**").replace(" 弱", " 中")
                        target.write_text(content)
                return result
        def operation_runner(*args, clock=clock, operations=operations, **kwargs):
            clock.advance(.1)  # include every deterministic call's simulated cost
            return operations(*args, **kwargs)
        hooks = _plain_hooks(handle, operation_runner)
        final = runner.run_loop(RUN_ID, Models(), hooks=hooks, max_parallel=3,
                                sleep=clock.sleep, monotonic=lambda clock=clock: clock.now,
                                wall_clock=lambda: "SIMULATED", log=lambda row, clock=clock, events=events: events.append({**row, "time": clock.now}))
        assert final["finished"], json.dumps(final["errors"], ensure_ascii=False)
        selected = ["scan.finalists", "scan.l4.source.bundle", "scan.review.plan", "scan.review.decision"]
        selected += [key for task in service._all_tasks(handle) for key in task["output_artifact_ids"]
                     if key.endswith((".card", ".review2", ".review3")) and artifacts.binding_sha256(handle, key)]
        hashes = {key: artifacts.snapshot_artifact(handle, key)["sha256"] for key in selected}
        times = {(row["task_id"], row["event"]): row["time"] for row in events if "task_id" in row}
        records[version] = {"classification": "SIMULATED", "business_version": "c5-test-rubric-v1",
                            "input_digest": sha256_bytes(canonical_json({key: hashes[key] for key in selected[:2]}).encode()),
                            "output_hashes": {key: value for key, value in hashes.items() if key not in selected[:2]},
                            "model_calls": sorted(calls), "model_call_count": len(calls),
                            "deterministic_calls": dict(Counter(operations.calls)),
                            "deterministic_call_count": len(operations.calls),
                            "elapsed_seconds": clock.now, "metrics": final["scheduling"]}
        if version == "per_stock":
            assert times[("l4.000002.a1.review2", "TASK_DISPATCHED")] < times[(f"l4.{CODE}.a1.card", "TASK_SUBMITTED")]
            assert times[("l4.000002.a1.review3", "TASK_DISPATCHED")] < times[(f"l4.{CODE}.a1.card", "TASK_SUBMITTED")]
            assert calls.count(("l4.000002.a1.card", 1)) == 1
            assert calls.count(("l4.000001.a2.card", 1)) == 1
    assert records["legacy"]["input_digest"] == records["per_stock"]["input_digest"]
    assert records["legacy"]["output_hashes"] == records["per_stock"]["output_hashes"]
    assert records["legacy"]["model_calls"] == records["per_stock"]["model_calls"]
    assert records["per_stock"]["deterministic_call_count"] > records["legacy"]["deterministic_call_count"]
    print("C5_COMPARISON=" + json.dumps(records, sort_keys=True))
