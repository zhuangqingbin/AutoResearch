"""引擎隔离工作区 —— context/reports 根路径的唯一事实源(2026-08-11 用户裁定)。

裁定:Claude 与 Codex 两个引擎跑同一套代码/skill(skill 经软链共享),但**除数据湖外
互不共享任何可变状态**——各自的 staging(context)、发布产物(reports)、闭环账本、
知识库、档案、权重全部按引擎分根:

    context_claude/   reports_claude/     ← Claude Code 会话(含 launchd 夜间预热)
    context_codex/    reports_codex/      ← Codex 会话
    lake/                                 ← 数据湖,唯一共享根(确定性行情数据,与引擎无关)

引擎判定(进程级一次定死,全部相对 CWD——本仓路径哲学不变):
    1. env ``AUTORESEARCH_ENGINE``(claude|codex)显式指定,恒优先;
    2. ``CLAUDECODE`` 在(Claude Code 的 Bash 自带)→ claude;
    3. 任一 ``CODEX_*`` env 在(Codex 沙箱模式自带)→ codex;
    4. 都没有(普通终端 / launchd / pytest)→ claude(在位者)。

⚠️ 引擎判定**不得**写进 ``.env``:`autoresearch/__init__` 会把 .env 灌进两个引擎的
进程,一份共享文件写死 engine 等于两边同根,隔离整个失效。Codex 侧的保险丝是
AGENTS.md 要求的 ``export AUTORESEARCH_ENGINE=codex``(沙箱外 CODEX_* 检测不可靠)。

消费方式:模块级根常量一律 ``XXX = ws.context_root() / "..."``(import 时按进程引擎
固化,测试照旧 monkeypatch 模块常量);调用点默认参数用 ``or ws.scan_root()`` 等
惰性形式。**除本模块外,生产代码不得再出现 ``"context…"`` / ``"reports…"`` 裸根
字面量**(tests/common/test_workspace.py 的 grep 探针锁此契约)。
"""
from __future__ import annotations

import os
from pathlib import Path

ENGINES = ("claude", "codex")


def detect_engine(environ=None) -> str:
    """按 docstring 的四级优先返回 'claude' | 'codex';非法显式值直接 raise(不静默回落)。"""
    env = os.environ if environ is None else environ
    explicit = str(env.get("AUTORESEARCH_ENGINE", "")).strip().lower()
    if explicit:
        if explicit not in ENGINES:
            raise ValueError(
                f"AUTORESEARCH_ENGINE={explicit!r} 非法(可选 {'/'.join(ENGINES)});"
                "写错引擎名当场炸,不静默落回默认")
        return explicit
    if env.get("CLAUDECODE"):
        return "claude"
    if any(k.startswith("CODEX_") for k in env):
        return "codex"
    return "claude"


#: 进程级引擎(import 时定死;测试想换引擎 monkeypatch 本属性,勿改 os.environ 后重读)
ENGINE: str = detect_engine()


def context_root() -> Path:
    """staging/状态根(引擎隔离):context_claude/ 或 context_codex/。"""
    return Path(f"context_{ENGINE}")


def reports_root() -> Path:
    """发布产物根(引擎隔离):reports_claude/ 或 reports_codex/。"""
    return Path(f"reports_{ENGINE}")


def lake_root() -> Path:
    """数据湖根 —— **唯一**跨引擎共享的可变目录(lake/<endpoint>/<key>.parquet)。"""
    return Path("lake")


# ── 高频组合根(纯便捷,无独立语义)─────────────────────────────────────────────

def scan_root() -> Path:
    return context_root() / "scan"


def scan_dir(date) -> Path:
    return scan_root() / str(date)


def learning_root() -> Path:
    return context_root() / "learning"


def knowledge_root() -> Path:
    return context_root() / "knowledge"


def factor_lab_root() -> Path:
    return context_root() / "factor_lab"
