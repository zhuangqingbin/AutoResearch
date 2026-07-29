"""frame `--json-out` 原子落盘(Wave8 W8-1)。

**为什么要 writer 侧落盘**:2026-07-28 首航事故 —— workflow 里写的是
`frame <date> --json > market_pack.json`,执行壳(haiku)擅自改写成
`... > market_pack.json 2>&1`,stderr 的日志行与 tqdm 进度条被并进产物文件
→ 1,776B 无 JSON 的垃圾 pack。同族第三代(空 pickle → 0 字节 pack → 非空垃圾 pack)。

`> file` 这条通路的根本毛病是**产物文件由 shell 持有、由进程的 stdout 决定内容**:
谁在 stdout 上多打一个字节、谁在命令行上多加一个 `2>&1`,产物就脏了;进程中途崩,
文件就留半截。`--json-out` 把落盘收回 writer 自己手里 —— 先写 `.tmp` 再 `os.replace`
(POSIX 原子换名),于是:

- stdout/stderr 怎么污染都到不了产物文件;
- 崩在半途 = 目标文件**不存在或仍是旧内容**,不会出现半截/垃圾文件;
- 下游的 `test -s` / JSON 合法性门(W8-2)退居纵深防御,不再是唯一防线。
"""

from __future__ import annotations

import json

import pytest

from autoresearch.scan.frame import _atomic_write_json


def test_writes_valid_json_and_leaves_no_tmp(tmp_path):
    target = tmp_path / "market_pack.json"
    payload = {"regime": {"label": "risk_off"}, "breadth": {"above_ma60": 0.1422}}

    out = _atomic_write_json(target, payload)

    assert out == target
    assert json.loads(target.read_text(encoding="utf-8")) == payload
    assert list(tmp_path.glob("*.tmp")) == [], "临时文件必须已被 os.replace 消费掉"


def test_creates_parent_directory(tmp_path):
    target = tmp_path / "context" / "scan" / "2026-07-28" / "market_pack.json"

    _atomic_write_json(target, {"ok": True})

    assert json.loads(target.read_text(encoding="utf-8")) == {"ok": True}


def test_serialization_failure_leaves_old_content_intact(tmp_path):
    """崩在序列化途中:目标文件保持旧内容,不留 .tmp。

    ⚠️ 本例**对原子性没有鉴别力**(2026-07-29 变异探针实测):`json.dumps` 在任何
    I/O 之前就抛,直写版同样不会碰到目标文件。留着只为锁"失败不留残渣"这条,
    真正区分原子/直写的是下面那个写到一半失败的用例。
    """
    target = tmp_path / "market_pack.json"
    target.write_text('{"old": true}', encoding="utf-8")

    class Unserializable:
        pass

    with pytest.raises(TypeError):
        _atomic_write_json(target, {"bad": Unserializable()})

    assert json.loads(target.read_text(encoding="utf-8")) == {"old": True}
    assert list(tmp_path.glob("*.tmp")) == [], "失败路径也不得留下临时文件"


def test_crash_midwrite_never_corrupts_target(tmp_path, monkeypatch):
    """**本 task 的核心断言**:写到一半崩,目标文件仍是旧内容,绝不出现半截/垃圾。

    模拟磁盘写途中失败(写入截断内容后抛 OSError)——这正是 `>` 重定向在
    进程半途崩时的形状,也是 07-28 垃圾 pack「非空但无效」的成因。
    原子写把半截内容关在 `.tmp` 里并清掉,目标文件永远只在两个合法状态之间跳。

    鉴别力已验:把实现退化成 `path.write_text(...)` 直写 → 本例变红。
    """
    from pathlib import Path as _Path

    target = tmp_path / "market_pack.json"
    target.write_text('{"old": true}', encoding="utf-8")

    real_write_text = _Path.write_text

    def truncating_write(self, data, *a, **kw):
        # 写进去一半(非空!)然后崩 —— 复刻 07-28 的"非空但无效"态
        real_write_text(self, data[: len(data) // 2], *a, **kw)
        raise OSError("simulated disk failure mid-write")

    monkeypatch.setattr(_Path, "write_text", truncating_write)

    with pytest.raises(OSError):
        _atomic_write_json(target, {"gen": 2, "payload": "x" * 200})

    monkeypatch.undo()
    assert json.loads(target.read_text(encoding="utf-8")) == {"old": True}, (
        "目标文件被半截内容污染了 —— 落盘不是原子的"
    )
    assert list(tmp_path.glob("*.tmp")) == [], "崩溃路径不得留下临时文件"


def test_overwrite_is_atomic_swap(tmp_path):
    target = tmp_path / "market_pack.json"
    _atomic_write_json(target, {"gen": 1})
    _atomic_write_json(target, {"gen": 2})

    assert json.loads(target.read_text(encoding="utf-8")) == {"gen": 2}
    assert list(tmp_path.glob("*.tmp")) == []


def test_cli_exposes_json_out_flag():
    """`--json-out` 必须在 CLI 上真实可用(防"函数写了但没接线"型死码)。"""
    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, "-m", "autoresearch.scan.frame", "--help"],
        capture_output=True, timeout=120,
    )
    assert proc.returncode == 0
    assert "--json-out" in proc.stdout.decode(errors="replace")
