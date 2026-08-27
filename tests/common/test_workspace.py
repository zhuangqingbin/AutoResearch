"""workspace 引擎隔离契约(2026-08-11 用户裁定:除数据湖外两引擎不共享)。

三层锁:
① detect_engine 四级优先级 + 非法值 fail-fast;
② 根形状:context/reports 带引擎后缀,lake 恒为共享根;
③ **裸根字面量 grep 探针**(AST 级):除 workspace.py 外,autoresearch 生产代码不得再有
   以 context/reports 起头的路径字面量 —— 这是"新代码绕过唯一事实源"的防回归网。
   (.claude/workflows/*.js 同族检查:js 无法 import workspace,靠 `context_${engine}`
   模板串拼根,slashed 裸根同样禁止。)
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from autoresearch.common import workspace as ws

ROOT = Path(__file__).resolve().parents[2]


# ───────────────────────── ① 引擎判定 ─────────────────────────


def test_explicit_env_wins():
    assert ws.detect_engine({"AUTORESEARCH_ENGINE": "codex", "CLAUDECODE": "1"}) == "codex"
    assert ws.detect_engine({"AUTORESEARCH_ENGINE": "claude", "CODEX_SANDBOX": "x"}) == "claude"


def test_harness_detection_and_default():
    assert ws.detect_engine({"CLAUDECODE": "1"}) == "claude"
    assert ws.detect_engine({"CODEX_SANDBOX": "seatbelt"}) == "codex"
    assert ws.detect_engine({"CODEX_HOME": "/x"}) == "codex"
    # CLAUDECODE 优先于 CODEX_*(Claude 会话里可能残留 codex 安装的全局 env)
    assert ws.detect_engine({"CLAUDECODE": "1", "CODEX_SANDBOX": "x"}) == "claude"
    assert ws.detect_engine({}) == "claude"  # 普通终端/launchd/pytest = 在位者


def test_bad_explicit_engine_raises():
    with pytest.raises(ValueError, match="AUTORESEARCH_ENGINE"):
        ws.detect_engine({"AUTORESEARCH_ENGINE": "gpt"})


# ───────────────────────── ② 根形状 ─────────────────────────


def test_roots_follow_engine(monkeypatch):
    monkeypatch.delenv("AUTORESEARCH_RUN_ID", raising=False)
    monkeypatch.setattr(ws, "ENGINE", "claude")
    assert ws.context_root() == Path("context_claude")
    assert ws.reports_root() == Path("reports_claude")
    assert ws.scan_dir("2026-08-11") == Path("context_claude/scan/2026-08-11")
    monkeypatch.setattr(ws, "ENGINE", "codex")
    assert ws.context_root() == Path("context_codex")
    assert ws.reports_root() == Path("reports_codex")


def test_active_run_scopes_scan_workspace(monkeypatch):
    monkeypatch.setattr(ws, "ENGINE", "codex")
    monkeypatch.setenv("AUTORESEARCH_RUN_ID", "20260827T010203456789Z")

    assert ws.active_run_id() == "20260827T010203456789Z"
    assert ws.scan_run_root() == Path(
        "context_codex/scan_runs/20260827T010203456789Z")
    assert ws.scan_root() == Path(
        "context_codex/scan_runs/20260827T010203456789Z/staging")
    assert ws.scan_dir("2026-08-27") == Path(
        "context_codex/scan_runs/20260827T010203456789Z/staging/2026-08-27")
    assert ws.scan_input_dir("2026-08-27") == Path(
        "context_codex/scan_runs/20260827T010203456789Z/staging/2026-08-27/_external_inputs")
    assert "claude" not in str(ws.scan_input_dir("2026-08-27"))


@pytest.mark.parametrize("run_id", ["../x", "run/x", "latest", "20260827_0102"])
def test_active_run_id_rejects_malformed_values(run_id):
    with pytest.raises(ValueError, match="AUTORESEARCH_RUN_ID"):
        ws.active_run_id({"AUTORESEARCH_RUN_ID": run_id})


def test_scan_run_root_rejects_malformed_explicit_id():
    with pytest.raises(ValueError, match="AUTORESEARCH_RUN_ID"):
        ws.scan_run_root("../x")


def test_validate_run_id_is_the_public_ascii_validation_boundary():
    assert ws.validate_run_id("20260827T010203456789Z") == (
        "20260827T010203456789Z"
    )
    with pytest.raises(ValueError, match="run_id"):
        ws.validate_run_id("２０２６０８２７T０１０２０３４５６７８９Z")


def test_active_run_id_rejects_unicode_digits():
    unicode_id = "２０２６０８２７T０１０２０３４５６７８９Z"
    with pytest.raises(ValueError, match="AUTORESEARCH_RUN_ID"):
        ws.active_run_id({"AUTORESEARCH_RUN_ID": unicode_id})


def test_scan_run_root_rejects_explicit_empty_id_even_with_active_env(monkeypatch):
    monkeypatch.setenv("AUTORESEARCH_RUN_ID", "20260827T010203456789Z")
    with pytest.raises(ValueError, match="AUTORESEARCH_RUN_ID"):
        ws.scan_run_root("")


def test_empty_run_id_preserves_legacy_scan_workspace(monkeypatch):
    monkeypatch.setattr(ws, "ENGINE", "codex")
    monkeypatch.setenv("AUTORESEARCH_RUN_ID", "   ")

    assert ws.active_run_id() is None
    assert ws.scan_root() == Path("context_codex/scan")
    assert ws.scan_dir("2026-08-27") == Path("context_codex/scan/2026-08-27")
    assert ws.scan_input_dir("2026-08-27") == Path("context_codex")
    assert "claude" not in str(ws.scan_dir("2026-08-27"))


def test_scan_run_root_requires_a_run_id(monkeypatch):
    monkeypatch.delenv("AUTORESEARCH_RUN_ID", raising=False)
    with pytest.raises(ValueError, match="缺 AUTORESEARCH_RUN_ID"):
        ws.scan_run_root()


@pytest.mark.parametrize(
    "analysis_date",
    [
        "/tmp/x",
        "../../escape",
        "../2026-08-27",
        "2026/08/27",
        "２０２６-０８-２７",
        "2026-8-7",
        "2026-13-01",
        "2026-02-30",
    ],
)
def test_scan_dir_rejects_unsafe_or_invalid_dates(monkeypatch, analysis_date):
    monkeypatch.setenv("AUTORESEARCH_RUN_ID", "20260827T010203456789Z")
    with pytest.raises(ValueError, match="scan date"):
        ws.scan_dir(analysis_date)


def test_validate_scan_date_returns_exact_valid_ascii_date():
    assert ws.validate_scan_date("2026-08-27") == "2026-08-27"


def test_lake_is_engine_independent(monkeypatch):
    """数据湖是唯一共享根:换引擎不得改变 lake 路径。"""
    monkeypatch.setattr(ws, "ENGINE", "claude")
    lake_claude = ws.lake_root()
    monkeypatch.setattr(ws, "ENGINE", "codex")
    assert ws.lake_root() == lake_claude == Path("lake")
    assert "claude" not in str(lake_claude) and "codex" not in str(lake_claude)


# ───────────────────────── ③ 裸根字面量探针 ─────────────────────────

_BARE_ROOT = re.compile(r"^(context|reports)(_(claude|codex))?(/|$)")
# 语义无关的英文单词用法(prompt context 等):只拦"像路径"的值 —— 含 / 或与已知根全等
_EXACT_ROOTS = {"context", "reports", "context_claude", "context_codex",
                "reports_claude", "reports_codex"}


def _string_literals(tree: ast.AST):
    """产出 (lineno, value):普通串 + f-string 首段;跳过 docstring(说明文不算接线)。"""
    doc_nodes = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                doc_nodes.add(id(body[0].value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in doc_nodes:
            yield node.lineno, node.value
        elif isinstance(node, ast.JoinedStr) and node.values:
            first = node.values[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                yield node.lineno, first.value


def test_no_bare_root_literals_in_source():
    """生产代码取根只许走 workspace —— 裸 `context…`/`reports…` 路径字面量零容忍。

    变异校验:往任一模块写回 `Path("context/scan")`,本测试必须变红。
    """
    offenders: list[str] = []
    for p in sorted((ROOT / "autoresearch").rglob("*.py")):
        if p.name == "workspace.py":
            continue
        tree = ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
        for lineno, val in _string_literals(tree):
            if _BARE_ROOT.match(val) and ("/" in val or val in _EXACT_ROOTS):
                offenders.append(f"{p.relative_to(ROOT)}:{lineno}: {val!r}")
    assert not offenders, (
        f"裸根字面量 {len(offenders)} 处(应改走 autoresearch.common.workspace):\n"
        + "\n".join(offenders[:60]))


def test_no_bare_root_literals_in_workflow_js():
    """workflow js 无 env/文件系统,根由 `context_${ENGINE}` 模板串拼 —— slashed 裸根禁止。"""
    offenders: list[str] = []
    for p in sorted((ROOT / ".claude" / "workflows").glob("*.js")):
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"['\"`](context|reports)/", line):
                offenders.append(f"{p.relative_to(ROOT)}:{i}: {line.strip()[:90]}")
    assert not offenders, "workflow js 裸根字面量(应由 engine 模板串拼):\n" + "\n".join(offenders)
