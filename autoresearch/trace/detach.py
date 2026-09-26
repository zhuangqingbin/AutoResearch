"""长命令脱离中继壳:双 fork 脱离调用方进程树 + 有界前台等待 + 按 ``(run_id, key)`` 幂等。

Workflow 里的确定性命令由 LLM 中继壳(general-purpose subagent)代跑。harness 在壳给出最终回复
时会终止它启动的全部后台任务,而且是按**进程树**杀 —— 2026-09-26 scan-market 的 prelude 壳用
``run_in_background`` 启动 prelude、挂个 Monitor 就结束回合,两次 prelude 都在 ~7s 吃 SIGKILL
(exec_capture 早已把子进程放进独立进程组,照样被杀),L2 不落,GATE1 毙全线。壳选不选后台是
模型行为,历史 135 次后台启动里 23 次没有阻塞等待就交了卷 —— 指令级叮嘱挡不住。

本模块把命令的存活从壳手里拿走:

- 命令跑在一个**双 fork 后被 launchd 收养**的 supervisor 下(新 session、不是调用方后代),
  壳怎么结束、被怎么杀都碰不到它;
- 调用方只做**有界**的前台等待(``--wait-seconds``,缺省 100s,低于 Bash 工具 2 分钟缺省超时);
- 同一 ``(run_id, key)`` 再调**只等不重跑**;编排侧(JS)循环调用直到终态。

stdout 与 stderr 分开落盘:门判据读「stdout 最后一行 JSON」,stderr 混进来会污染它
(2026-07-28 事故)。只打印一行 JSON::

    {"state": RUNNING|COMPLETED|FAILED|LOST, "key", "exit_code", "tail", "stderr_tail",
     "result", "expect_file", "reason"}

``result`` = 命令 stdout 最后一行解析出的 JSON(门判据)。**别叫 last_json**:09-26 真壳冒烟里,
壳把提示词「最后一行 JSON」对上了同名字段,把整行包进 last_json 再自编 state="done" —— 字段名
不能和提示词撞车。

状态文件在 ``context_<engine>/detached/<run_id>/<key>.{json,out,err}``,不进 run 目录与 staging。
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from autoresearch.common import workspace as ws

_KEY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,119}")
TERMINAL = frozenset({"COMPLETED", "FAILED"})
_TAIL_LINES = 15
_TAIL_CHARS = 4000
#: STARTING 态(已占 key、supervisor 尚未回报 RUNNING)的最长容忍;超过且无活进程 → LOST。
_START_GRACE_S = 30.0
_POLL_S = 0.25


def command_sha256(shell: str) -> str:
    return hashlib.sha256(shell.encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _root(run_id: str) -> Path:
    return ws.context_root() / "detached" / ws.validate_run_id(run_id)


def _paths(run_id: str, key: str) -> tuple[Path, Path, Path]:
    root = _root(run_id)
    return root / f"{key}.json", root / f"{key}.out", root / f"{key}.err"


def _read_state(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except json.JSONDecodeError:
        return None          # 写入途中的瞬态;原子替换下不应出现,出现就当还没写好


def _write_state(path: Path, state: dict) -> None:
    tmp = path.with_suffix(f".json.tmp{os.getpid()}")
    tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


@contextlib.contextmanager
def _locked(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    fd = os.open(root / ".lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _alive(pid: object) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _tail(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return ""
    return "\n".join(text.splitlines()[-_TAIL_LINES:])[-_TAIL_CHARS:]


def _last_json(path: Path):
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except FileNotFoundError:
        return None
    for line in reversed(lines):
        if line.strip():
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                return None
    return None


def _spawn_supervisor(run_id: str, key: str) -> None:
    """双 spawn:中间层进新 session 后立刻退出,supervisor 成孤儿被 launchd 收养。

    用 subprocess(fork+exec)而不是裸 os.fork:macOS 上多线程/ObjC 进程 fork 不 exec 不安全。
    """
    launcher = [sys.executable, "-m", "autoresearch.trace.detach", "_launch",
                "--run-id", run_id, "--key", key]
    subprocess.run(launcher, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, start_new_session=True, check=True, timeout=30)


def _launch(run_id: str, key: str) -> int:
    subprocess.Popen([sys.executable, "-m", "autoresearch.trace.detach", "_supervise",
                      "--run-id", run_id, "--key", key],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, close_fds=True)
    return 0


def _supervise(run_id: str, key: str) -> int:
    state_path, out_path, err_path = _paths(run_id, key)
    state = _read_state(state_path)
    if not state or state.get("status") != "STARTING":
        return 2
    with open(out_path, "ab") as out, open(err_path, "ab") as err:
        child = subprocess.Popen(["/bin/bash", "-c", state["shell"]], cwd=state["cwd"],
                                 stdin=subprocess.DEVNULL, stdout=out, stderr=err)
        _write_state(state_path, {**state, "status": "RUNNING", "supervisor_pid": os.getpid(),
                                  "child_pid": child.pid, "started_at": _now()})
        code = child.wait()
    final = _read_state(state_path) or state
    _write_state(state_path, {**final, "status": "COMPLETED" if code == 0 else "FAILED",
                              "exit_code": code, "ended_at": _now()})
    return 0


def _result(key: str, state: dict | None, out_path: Path, err_path: Path,
            expect_file: str | None, *, lost: bool = False, reason: str | None = None) -> dict:
    status = "LOST" if lost else ((state or {}).get("status") or "LOST")
    if status == "STARTING":
        status = "RUNNING"
    terminal = status in TERMINAL or status == "LOST"
    res = {"state": status, "key": key, "exit_code": (state or {}).get("exit_code"),
           "tail": _tail(out_path) if terminal else "",
           "stderr_tail": _tail(err_path) if terminal else "",
           "result": _last_json(out_path) if status in TERMINAL else None,
           "expect_file": (Path(expect_file).is_file() and Path(expect_file).stat().st_size > 0)
           if (expect_file and terminal) else None}
    if reason:
        res["reason"] = reason
    return res


def start_or_wait(run_id: str, key: str, shell: str, wait_seconds: float,
                  expect_file: str | None = None) -> dict:
    state_path, out_path, err_path = _paths(run_id, key)
    digest = command_sha256(shell)
    with _locked(state_path.parent):
        state = _read_state(state_path)
        if state is None:
            state = {"status": "STARTING", "key": key, "run_id": run_id, "shell": shell,
                     "cmd_sha256": digest, "cwd": os.getcwd(), "created_at": _now()}
            _write_state(state_path, state)
            spawn = True
        else:
            spawn = False
    if not spawn and state.get("cmd_sha256") != digest:
        return _result(key, {"status": "FAILED"}, out_path, err_path, None,
                       reason="key already used by a different command (改 key 或 attempt)")
    if spawn:
        _spawn_supervisor(run_id, key)
    deadline = time.monotonic() + max(0.0, wait_seconds)
    while True:
        state = _read_state(state_path) or state
        status = state.get("status")
        if status in TERMINAL:
            return _result(key, state, out_path, err_path, expect_file)
        if status == "RUNNING" and not _alive(state.get("supervisor_pid")):
            state = _read_state(state_path) or state          # 刚写完终态的竞态
            if state.get("status") not in TERMINAL:
                return _result(key, state, out_path, err_path, expect_file, lost=True,
                               reason="supervisor 已不在且未写终态")
            continue
        if status == "STARTING" and _age_s(state) > _START_GRACE_S:
            return _result(key, state, out_path, err_path, expect_file, lost=True,
                           reason=f"supervisor {int(_START_GRACE_S)}s 内未启动")
        if time.monotonic() >= deadline:
            return _result(key, state, out_path, err_path, expect_file)
        time.sleep(_POLL_S)


def _age_s(state: dict) -> float:
    try:
        created = datetime.strptime(state["created_at"], "%Y-%m-%dT%H:%M:%S.%fZ")
    except (KeyError, ValueError):
        try:
            created = datetime.strptime(state["created_at"], "%Y-%m-%dT%H:%M:%SZ")
        except (KeyError, ValueError):
            return float("inf")
    return (datetime.now(timezone.utc).replace(tzinfo=None) - created).total_seconds()


def _key(value: str) -> str:
    if not _KEY_RE.fullmatch(value):
        raise argparse.ArgumentTypeError(f"非法 key:{value!r}")
    return value


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("_launch", "_supervise"):
        ap = argparse.ArgumentParser(prog=f"detach {argv[0]}")
        ap.add_argument("--run-id", required=True)
        ap.add_argument("--key", required=True, type=_key)
        ns = ap.parse_args(argv[1:])
        return (_launch if argv[0] == "_launch" else _supervise)(ns.run_id, ns.key)
    ap = argparse.ArgumentParser(prog="python -m autoresearch.trace.detach",
                                 description="长命令脱离中继壳执行;有界等待,按 key 幂等")
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--key", required=True, type=_key)
    ap.add_argument("--wait-seconds", type=float, default=100.0)
    ap.add_argument("--expect-file", default=None)
    ap.add_argument("--shell", required=True, help="交给 /bin/bash -c 的整条命令")
    ns = ap.parse_args(argv)
    try:
        ws.validate_run_id(ns.run_id)
    except ValueError as exc:
        ap.error(str(exc))
    res = start_or_wait(ns.run_id, ns.key, ns.shell, ns.wait_seconds, ns.expect_file)
    print(json.dumps(res, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
