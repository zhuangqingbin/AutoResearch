"""C5 owner-thread commits and conservative deterministic lane overlap."""
import threading
from pathlib import Path

from autoresearch.session_agent import runner, store
from autoresearch.session_agent.executors.base import DispatchResult

from ._runner_support import begin_synthetic_run, det, inf


def test_deterministic_and_inference_state_commits_share_the_loop_thread(tmp_path, monkeypatch):
    run = begin_synthetic_run(tmp_path, monkeypatch, [det("prepare"), inf("card")])
    owner = threading.get_ident()
    observed = []
    for name in ("claim", "complete_deterministic", "accept"):
        original = getattr(store, name)
        def record(*args, _original=original, _name=name, **kwargs):
            observed.append((_name, threading.get_ident()))
            return _original(*args, **kwargs)
        monkeypatch.setattr(store, name, record)

    class Model:
        name = "synthetic"
        from autoresearch.session_agent.roles import EXECUTOR_CAPABILITIES
        capabilities = EXECUTOR_CAPABILITIES["mailbox"]
        def dispatch(self, request):
            for path in request.output_paths.values():
                target = Path(path)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("**Rating**: Hold\nFINAL TRANSACTION PROPOSAL: **HOLD**")
            return DispatchResult(ok=True, session_ref="session-main", context_ref="agent",
                                  parent_context_ref="session-main")

    result = runner.run_loop(run.run_id, Model(), hooks=run.hooks(), poll_seconds=.001)
    assert result["finished"] is True, result["errors"]
    assert {name for name, _ in observed} == {"claim", "complete_deterministic", "accept"}
    assert all(thread == owner for _, thread in observed)


def test_pure_lane_callback_dispatches_inference_before_deterministic_commit(tmp_path, monkeypatch):
    from autoresearch.session_agent import service
    tasks = [det("facts", operation="research.card.facts", subject="600519"), inf("card")]
    run = begin_synthetic_run(tmp_path, monkeypatch, tasks)
    original = service.execute
    observed = []
    def execute(*args, owner_callback=None, **kwargs):
        assert owner_callback is not None
        owner_callback(lambda: True)
        observed.append(store.read_entry(service._store_path(run.handle), "card")["state"])
        return original(*args, **kwargs)
    monkeypatch.setattr(service, "execute", execute)
    class Model:
        name = "synthetic"
        from autoresearch.session_agent.roles import EXECUTOR_CAPABILITIES
        capabilities = EXECUTOR_CAPABILITIES["mailbox"]
        def dispatch(self, request):
            for path in request.output_paths.values():
                Path(path).parent.mkdir(parents=True, exist_ok=True)
                Path(path).write_text("**Rating**: Hold\nFINAL TRANSACTION PROPOSAL: **HOLD**")
            return DispatchResult(ok=True, session_ref="session-main", context_ref="agent",
                                  parent_context_ref="session-main")
    result = runner.run_loop(run.run_id, Model(), hooks=run.hooks(), poll_seconds=.001)
    assert result["finished"]
    assert observed == ["RUNNING"]


def test_completed_callback_task_is_not_claimed_again_from_outer_ready_list(tmp_path, monkeypatch):
    from autoresearch.session_agent import service

    from ._runner_support import CARD_TEXT

    run = begin_synthetic_run(tmp_path, monkeypatch, [
        det("facts", operation="research.card.facts", subject="600519"), inf("card")])
    original = service.execute
    def execute(*args, owner_callback=None, **kwargs):
        owner_callback(lambda: True)
        current = owner_callback.__self__
        current._inflight["card"].future.result(timeout=2)
        owner_callback(lambda: True)
        assert store.read_entry(service._store_path(run.handle), "card")["state"] == "SUCCEEDED"
        return original(*args, **kwargs)
    monkeypatch.setattr(service, "execute", execute)
    class Model:
        name = "synthetic"
        from autoresearch.session_agent.roles import EXECUTOR_CAPABILITIES
        capabilities = EXECUTOR_CAPABILITIES["mailbox"]
        def dispatch(self, request):
            for path in request.output_paths.values():
                Path(path).parent.mkdir(parents=True, exist_ok=True)
                Path(path).write_text(CARD_TEXT)
            return DispatchResult(ok=True, session_ref="session-main", context_ref="agent",
                                  parent_context_ref="session-main")
    result = runner.run_loop(run.run_id, Model(), hooks=run.hooks(), poll_seconds=.001)
    assert result["finished"]
    assert result["skipped"] == []
    assert result["errors"] == []
