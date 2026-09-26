"""detach:长命令脱离中继壳进程树 + 有界等待 + 按 key 幂等。

2026-09-26 事故:scan-market 的 prelude 壳用 run_in_background 跑 prelude 后结束回合,harness
按**进程树**把后台任务连根杀掉(exec_capture 已把子进程放进独立进程组,子进程仍吃 SIGKILL),
两次 prelude 都在 ~7s 死,GATE1 毙全线。这里的核心回归:调用方整棵进程树被杀,命令照样跑完。
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
RUN_ID = "20260926T024905303694Z"


def _env() -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO)
    env["AUTORESEARCH_ENGINE"] = "claude"
    env.pop("AUTORESEARCH_RUN_ID", None)
    return env


def _argv(key: str, shell: str, wait: float, *extra: str) -> list[str]:
    return [sys.executable, "-m", "autoresearch.trace.detach", "--run-id", RUN_ID,
            "--key", key, "--wait-seconds", str(wait), *extra, "--shell", shell]


def _run(tmp_path: Path, key: str, shell: str, wait: float = 20, *extra: str) -> dict:
    proc = subprocess.run(_argv(key, shell, wait, *extra), cwd=tmp_path, env=_env(),
                          capture_output=True, text=True, timeout=wait + 30)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def _descendants(pid: int) -> list[int]:
    rows = subprocess.run(["ps", "-A", "-o", "pid=,ppid="], capture_output=True, text=True).stdout
    children: dict[int, list[int]] = {}
    for row in rows.splitlines():
        parts = row.split()
        if len(parts) == 2:
            children.setdefault(int(parts[1]), []).append(int(parts[0]))
    out, stack = [], [pid]
    while stack:
        for child in children.get(stack.pop(), []):
            out.append(child)
            stack.append(child)
    return out


def test_completes_and_returns_last_stdout_json_line(tmp_path):
    res = _run(tmp_path, "gate-a", 'echo noise; echo "err" >&2; echo \'{"ok": true, "n": 3}\'')
    assert res["state"] == "COMPLETED"
    assert res["exit_code"] == 0
    assert res["result"] == {"ok": True, "n": 3}
    assert "noise" in res["tail"]
    assert "err" not in res["tail"]          # stderr 不混进 stdout 尾(门判据读最后一行 JSON)
    assert "err" in res["stderr_tail"]


def test_nonzero_exit_is_failed_with_code(tmp_path):
    res = _run(tmp_path, "boom", "echo half; exit 3")
    assert res["state"] == "FAILED"
    assert res["exit_code"] == 3
    assert res["result"] is None


def test_job_survives_when_caller_process_tree_is_killed(tmp_path):
    """事故复现:壳(调用方)连同整棵后代树被 SIGKILL,命令必须照样跑完。"""
    marker = tmp_path / "finished.txt"
    shell = f"sleep 2; echo done > {marker}; echo '{{\"done\": 1}}'"
    caller = subprocess.Popen(_argv("prelude-attempt-1", shell, 30), cwd=tmp_path, env=_env(),
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              start_new_session=True)
    state_file = tmp_path / "context_claude" / "detached" / RUN_ID / "prelude-attempt-1.json"
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if state_file.exists() and json.loads(state_file.read_text()).get("status") == "RUNNING":
            break
        time.sleep(0.05)
    else:
        pytest.fail("detached job never reported RUNNING")
    for pid in _descendants(caller.pid) + [caller.pid]:          # harness 式树杀
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    caller.wait(timeout=5)

    res = _run(tmp_path, "prelude-attempt-1", shell, 20)          # 新壳续等,不重跑
    assert res["state"] == "COMPLETED", res
    assert res["result"] == {"done": 1}
    assert marker.read_text().strip() == "done"


def test_same_key_waits_instead_of_respawning(tmp_path):
    counter = tmp_path / "count.txt"
    shell = f"echo run >> {counter}; sleep 1.5; echo '{{\"ok\": true}}'"
    first = _run(tmp_path, "slim-600519", shell, 0)
    assert first["state"] == "RUNNING"
    second = _run(tmp_path, "slim-600519", shell, 20)
    assert second["state"] == "COMPLETED"
    third = _run(tmp_path, "slim-600519", shell, 5)             # 终态后再调:原样回报,不重跑
    assert third["state"] == "COMPLETED"
    assert counter.read_text().splitlines() == ["run"]


def test_key_reused_with_different_command_is_rejected(tmp_path):
    _run(tmp_path, "frame-attempt-1", "echo '{\"a\": 1}'")
    res = _run(tmp_path, "frame-attempt-1", "echo '{\"b\": 2}'")
    assert res["state"] == "FAILED"
    assert "different command" in res["reason"]


def test_dead_supervisor_without_terminal_state_is_lost(tmp_path):
    root = tmp_path / "context_claude" / "detached" / RUN_ID
    root.mkdir(parents=True)
    dead = subprocess.Popen(["true"])
    dead.wait()
    shell = "echo never"
    from autoresearch.trace.detach import command_sha256
    (root / "orphan.json").write_text(json.dumps({
        "status": "RUNNING", "supervisor_pid": dead.pid, "cmd_sha256": command_sha256(shell),
        "created_at": "2026-09-26T00:00:00Z"}))
    res = _run(tmp_path, "orphan", shell, 2)
    assert res["state"] == "LOST"


def test_expect_file_reported_on_completion(tmp_path):
    target = tmp_path / "summary.md"
    res = _run(tmp_path, "prelude-x", f"echo hi > {target}", 20, "--expect-file", str(target))
    assert res["state"] == "COMPLETED" and res["expect_file"] is True
    res2 = _run(tmp_path, "prelude-y", "true", 20, "--expect-file", str(tmp_path / "missing.md"))
    assert res2["expect_file"] is False


def test_rejects_unsafe_key(tmp_path):
    proc = subprocess.run(_argv("../escape", "true", 1), cwd=tmp_path, env=_env(),
                          capture_output=True, text=True, timeout=30)
    assert proc.returncode == 2
