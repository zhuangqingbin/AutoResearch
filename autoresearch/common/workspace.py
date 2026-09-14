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
import re
from datetime import date as calendar_date
from pathlib import Path

# 唯一的 kind 词汇表在契约层(最底层,workspace 在它之上 —— 这是**向下**的边)。
from autoresearch.contracts.stages import RUN_KINDS as _RUN_KINDS

ENGINES = ("claude", "codex")
_RUN_ID_RE = re.compile(r"^[0-9]{8}T[0-9]{12}Z$")
_SCAN_DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")

#: 2026-08-11 引擎隔离**之前**的 context 根名。历史产物(如 08-11 前的 `_l4_tasks.json`,
#: 实测 113 处)把路径记成裸 `context/…`,那个根今天不存在、文件却还在 —— 读侧要做前缀
#: 重映射(`scan/retention.resolve_recorded_path`)。名字放这里,是因为本模块是**根名的
#: 唯一事实源**,包括已经作废的那个;放消费点就是又一处裸根字面量。
LEGACY_CONTEXT_ROOT = "context"


def detect_engine(environ=None) -> str:
    """按 docstring 的四级优先返回 'claude' | 'codex';非法显式值直接 raise(不静默回落)。"""
    env = os.environ if environ is None else environ
    explicit = str(env.get("AUTORESEARCH_ENGINE", "")).strip().lower()
    if explicit:
        if explicit not in ENGINES:
            raise ValueError(
                f"AUTORESEARCH_ENGINE={explicit!r} 非法(可选 {'/'.join(ENGINES)});"
                "写错引擎名当场炸,不静默落回默认"
            )
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


def active_run_id(environ=None) -> str | None:
    env = os.environ if environ is None else environ
    value = str(env.get("AUTORESEARCH_RUN_ID", "")).strip()
    if not value:
        return None
    return validate_run_id(value)


def validate_run_id(run_id) -> str:
    """Validate the exact ASCII run identity used in path derivation."""
    value = str(run_id)
    if not _RUN_ID_RE.fullmatch(value):
        raise ValueError(f"run_id/AUTORESEARCH_RUN_ID={value!r} 非法")
    return value


#: run kind → 引擎根下的 run 池目录名。**键必须等于 `contracts.stages.RUN_KINDS`**
#: (import 时校验,见下)—— 「该有什么」的分母有四个版本正是本仓踩过的病,路径这层
#: 不再开第二份 kind 名单。
RUN_SPOOLS: dict[str, str] = {
    "scan-market": "scan_runs",
    "stock-research": "analyze_runs",
    "macro-research": "macro_runs",
    "sector-research": "sector_runs",
    "dossier-init": "dossier_runs",
}

#: run kind → 发布产物根下的技能目录名(`reports_<engine>/<这里>/…`)。
#: scan 的账本/失败区/归档/修补区都挂在 `reports_<engine>/scan/` 下,
#: stock-research 同构地挂在 `reports_<engine>/analyze/` 下。
RUN_REPORT_DIRS: dict[str, str] = {
    "scan-market": "scan",
    "stock-research": "analyze",
    "macro-research": "macro",
    "sector-research": "sector",
    "dossier-init": "dossiers",
}

if tuple(RUN_SPOOLS) != _RUN_KINDS or tuple(RUN_REPORT_DIRS) != _RUN_KINDS:
    raise RuntimeError(
        "workspace 的 run kind 表与 contracts.stages.RUN_KINDS 不一致:"
        f"{tuple(RUN_SPOOLS)} / {tuple(RUN_REPORT_DIRS)} vs {_RUN_KINDS}"
    )


def validate_run_kind(kind) -> str:
    value = str(kind)
    if value not in RUN_SPOOLS:
        raise ValueError(f"未知 run kind={kind!r}(可选 {'/'.join(RUN_SPOOLS)})")
    return value


def run_root(kind: str, run_id: str | None = None) -> Path:
    """某个 kind 的一趟 run 的工作区根(`context_<engine>/<spool>/<run_id>/`)。

    kind 化之前这里只有 `scan_run_root`,于是「run 有工作区」这件 kind 无关的事被写死
    成了 scan 专属(capsule 的七处 `scan` 字面量之一)。`scan_run_root` 保留为本函数
    在 `scan-market` 上的别名,签名一字未改,全仓既有调用点不断。
    """
    resolved_kind = validate_run_kind(kind)
    value = active_run_id() if run_id is None else str(run_id)
    if value is None:
        raise ValueError("缺 AUTORESEARCH_RUN_ID，无法解析 run-scoped workspace")
    return context_root() / RUN_SPOOLS[resolved_kind] / validate_run_id(value)


def run_reports_root(kind: str) -> Path:
    """某个 kind 的发布产物根 —— `reports_<engine>/scan` 或 `reports_<engine>/analyze`。"""
    return reports_root() / RUN_REPORT_DIRS[validate_run_kind(kind)]


def canonical_publication_root(kind: str, run_id: str, publication_id: str = "p1") -> Path:
    """不可变 session_v1 发布根；兼容日期路径不再承担提交真值。"""
    if not re.fullmatch(r"p[1-9][0-9]*", str(publication_id)):
        raise ValueError(f"publication_id={publication_id!r} 非法")
    return run_reports_root(kind) / "runs" / validate_run_id(run_id) / str(publication_id)


def find_run_root(run_id) -> Path | None:
    """真实存在的那个 spool 里的 run 目录;哪个 kind 都找不到 → None。

    run_id 自身不带 kind(它只是时间戳),所以「这趟是谁的」只能问文件系统。
    """
    value = validate_run_id(run_id)
    for kind in RUN_SPOOLS:
        candidate = context_root() / RUN_SPOOLS[kind] / value
        if candidate.is_dir():
            return candidate
    return None


def active_run_kind(environ=None) -> str | None:
    """环境里那趟 run 的 kind;没有 run → None。

    **落回 `scan-market`** 是有意的:`AUTORESEARCH_RUN_ID` 在场而 spool 目录还没建
    (begin 之前、或测试夹具只设 env 不建目录)时,历史行为就是「run-scoped scan 路径」,
    这里必须逐字保持,否则 scan 侧一大票夹具会在解析路径时静默改道。
    """
    run_id = active_run_id(environ)
    if run_id is None:
        return None
    for kind in RUN_SPOOLS:
        if (context_root() / RUN_SPOOLS[kind] / run_id).is_dir():
            return kind
    return "scan-market"


def scan_run_root(run_id: str | None = None) -> Path:
    """`run_root("scan-market", …)` 的别名(签名不变)。"""
    return run_root("scan-market", run_id)


def scan_root() -> Path:
    run_id = active_run_id()
    if run_id and active_run_kind() == "scan-market":
        return scan_run_root(run_id) / "staging"
    # 非 scan 的 run(如 stock-research)在场时,scan 的 staging 根**照旧**是历史根:
    # 一趟单票研究的 run_id 不该把 `L1_scored_full.csv` 的读点改道到一个不存在的
    # `scan_runs/<那个 id>/` 里(harvest 的 L1 复用会因此静默落空)。
    return context_root() / "scan"


def validate_scan_date(date) -> str:
    value = str(date)
    if not _SCAN_DATE_RE.fullmatch(value):
        raise ValueError(f"scan date={value!r} 非法")
    try:
        calendar_date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"scan date={value!r} 非法") from exc
    return value


def scan_dir(date) -> Path:
    return scan_root() / validate_scan_date(date)


def scan_input_dir(date, *, scan_dir=None) -> Path:
    value = validate_scan_date(date)
    # 同 `scan_root()`:只有 **scan** 的 run 才把外源输入收进 run 目录。
    if active_run_kind() != "scan-market":
        return context_root()
    resolved_scan_dir = Path(scan_dir) if scan_dir is not None else scan_root() / value
    return resolved_scan_dir / "_external_inputs"


def learning_root() -> Path:
    """`context_<engine>/learning/` —— **目录名是历史遗留**,不再有闭环学习。

    2026-08-21「整个 learning 层退役」后这里只剩三类东西:① 仍在写的
    `usage_reconcile.jsonl`(token 计量 streak,不是学习)与 `temperature.csv`
    (S1 情绪温度计,prelude 增量落盘);② 历史账本 jsonl/csv(按用户裁定原样保留,
    无生产者);③ 若干离线审计报告的落点。**不改名**:改了历史文件就跟路径失联。
    """
    return context_root() / "learning"


def knowledge_root() -> Path:
    return context_root() / "knowledge"


def factor_lab_root() -> Path:
    return context_root() / "factor_lab"


def broker_root() -> Path:
    """`context_<engine>/broker/` —— 券商成交取数层产物根(2026-08-27 设计稿 §5)。

    个人财务数据:**不进 lake/**(lake 是跨引擎共享的行情湖),按引擎分根、gitignore。
    """
    return context_root() / "broker"
