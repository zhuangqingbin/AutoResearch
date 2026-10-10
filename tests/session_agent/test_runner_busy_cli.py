"""A competing runner is an ownership refusal, never a task/run failure."""
from types import SimpleNamespace

from autoresearch.session_agent import mailbox_cli, runner


def test_cli_reports_busy_without_entering_failure_cleanup(monkeypatch):
    monkeypatch.setattr(mailbox_cli, "_handle", lambda _: SimpleNamespace())
    monkeypatch.setattr(mailbox_cli, "_build_executor", lambda *a: (object(), None))
    def refuse(*args, **kwargs):
        raise runner.RunnerAlreadyRunning("a live runner already owns this run")
    monkeypatch.setattr(runner, "run_loop", refuse)
    args = SimpleNamespace(run_id="run", max_parallel=1, poll_seconds=.01,
                           max_rounds=1, timeout_multiplier=1, fanout_warmup_s=0)
    outcome, code = mailbox_cli.run_command(args)
    assert code == mailbox_cli.EXIT_NOT_FINISHED and outcome["stop_reason"] == "RUNNER_BUSY"
    assert not outcome["finished"]
