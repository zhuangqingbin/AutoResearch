"""研究 agent 输入边界 hook(`scripts/hooks/agent_input_boundary.py`)的行为契约。

2026-09-14 夜扫描:harness 2.1.270 下 l4-card / l3-rank / macro-brief 去翻 `autoresearch/`
解析器与 lint 源码自证产物能过机检,每卡轮次 6–7 → 24;同夜 Codex 的 L4 card / L4 intel 子
agent 用 `rg`/`sed` 做同样的事。hook 把两个引擎的研究角色的读盘限制在本引擎数据根与契约文档内。
脚本不在包里,按文件路径装载后直接调 `decide()` / `main()`;shell 前置过滤另起子进程验一次。
"""
from __future__ import annotations

import importlib.util
import io
import json
import subprocess
from pathlib import Path

import pytest
import tomllib

ROOT = Path(__file__).resolve().parents[1]
HOOK_DIR = ROOT / "scripts" / "hooks"
SCRIPT = HOOK_DIR / "agent_input_boundary.py"
WRAPPER = HOOK_DIR / "agent_input_boundary.sh"
CODEX_RELAY_ROLES = {"gp_shell", "gp_shell_json"}   # 确定性命令壳:不做研究判断,不受管


def _load():
    spec = importlib.util.spec_from_file_location("agent_input_boundary", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def hook():
    return _load()


def _payload(tool: str, tool_input: dict, agent_type: str | None = "l4-card") -> dict:
    payload = {
        "session_id": "sess-1",
        "transcript_path": "/tmp/t.jsonl",
        "cwd": str(ROOT),
        "hook_event_name": "PreToolUse",
        "tool_name": tool,
        "tool_input": tool_input,
    }
    if agent_type is not None:
        payload["agent_id"] = "a0123456789abcdef"
        payload["agent_type"] = agent_type
    return payload


def _bash(command: str, agent_type: str | None = "L4 card") -> dict:
    return _payload("Bash", {"command": command}, agent_type)


def _denied(verdict) -> bool:
    if verdict is None:
        return False
    out = verdict["hookSpecificOutput"]
    assert out["hookEventName"] == "PreToolUse"
    assert "AGENT_INPUT_BOUNDARY" in out["permissionDecisionReason"]
    return out["permissionDecision"] == "deny"


SELF_REVIEW = str(ROOT / "autoresearch" / "scan" / "self_review.py")
TASK_PACK = str(ROOT / "context_claude" / "scan_runs" / "R" / "staging" / "2026-09-14"
                / "_l4_prompt_688981.md")


# ── Claude Code:Read / Grep / Glob ──────────────────────────────────────────────

@pytest.mark.parametrize("agent_type", [None, "general-purpose", "Explore", "Plan", "claude"])
def test_main_thread_and_dev_agents_read_source_freely(hook, agent_type):
    assert hook.decide(_payload("Read", {"file_path": SELF_REVIEW}, agent_type)) is None
    assert hook.decide(_payload("Grep", {"pattern": "def "}, agent_type)) is None


@pytest.mark.parametrize("path", [
    SELF_REVIEW,
    str(ROOT / "autoresearch" / "scan" / "l4" / "parsers.py"),
    str(ROOT / ".claude" / "workflows" / "l4-stock.js"),
    str(ROOT / ".codex" / "agents" / "l4_card.toml"),
    str(ROOT / "tests" / "test_agent_defs.py"),
    str(ROOT / "scripts" / "hooks" / "agent_input_boundary.py"),
    str(ROOT / "docs" / "specs" / "x.md"),
    str(ROOT / "lake" / "daily" / "20260914.parquet"),
    str(ROOT / "context_codex" / "scan_runs" / "R" / "x.md"),
    str(ROOT / ".worktrees" / "wt" / "autoresearch" / "scan" / "self_review.py"),
    str(ROOT / "context_claude" / ".." / "autoresearch" / "scan" / "brief.py"),
    "autoresearch/scan/price_claims.py",
])
def test_research_agent_reads_outside_data_roots_are_denied(hook, path):
    assert _denied(hook.decide(_payload("Read", {"file_path": path})))


@pytest.mark.parametrize("path", [
    TASK_PACK,
    str(ROOT / "context_claude" / "knowledge" / "dossiers" / "688981.md"),
    str(ROOT / "reports_claude" / "scan" / "run" / "brief.md"),
    str(ROOT / ".claude" / "skills" / "stock-research" / "lite-playbook.md"),
    str(ROOT / ".claude" / "agents" / "l4-card.md"),
    str(ROOT / "CLAUDE.md"),
    "/tmp/elsewhere/notes.md",
])
def test_research_agent_reads_inside_data_and_contract_roots_pass(hook, path):
    assert hook.decide(_payload("Read", {"file_path": path})) is None


def test_every_claude_research_role_is_guarded(hook):
    for role in ("l3-rank", "macro-brief", "sector-brief", "dossier-init"):
        assert _denied(hook.decide(_payload("Read", {"file_path": SELF_REVIEW}, role)))


def test_grep_search_base_decides(hook):
    staging = str(ROOT / "context_claude" / "scan_runs" / "R" / "staging")
    assert _denied(hook.decide(_payload("Grep", {"pattern": "citation_density"})))
    assert _denied(hook.decide(_payload("Grep", {"pattern": "x", "path": str(ROOT)})))
    assert _denied(hook.decide(_payload("Grep", {"pattern": "x", "path": str(ROOT / ".claude")})))
    assert _denied(hook.decide(_payload("Grep", {"pattern": "x", "path": SELF_REVIEW})))
    assert hook.decide(_payload("Grep", {"pattern": "触板", "path": staging})) is None


def test_glob_uses_literal_prefix_of_pattern(hook):
    assert hook.decide(_payload("Glob", {
        "pattern": "context_claude/scan_runs/R/staging/2026-09-14/*688981*",
        "path": str(ROOT)})) is None
    assert hook.decide(_payload("Glob", {"pattern": "context_claude/scan*/**/market_view.md"})) is None
    assert _denied(hook.decide(_payload("Glob", {"pattern": "**/*.py", "path": str(ROOT)})))
    assert _denied(hook.decide(_payload("Glob", {"pattern": "*.py",
                                                  "path": str(ROOT / "autoresearch")})))
    assert _denied(hook.decide(_payload("Glob", {"pattern": str(ROOT / "autoresearch") + "/**/*.py"})))


def test_symlink_into_source_is_denied(tmp_path, monkeypatch):
    """放行子树里的符号链接指回源码树 → 按 realpath 视图拒(假仓库,不碰真 context_claude)。"""
    fake = tmp_path / "repo"
    (fake / "autoresearch" / "scan").mkdir(parents=True)
    (fake / "autoresearch" / "scan" / "brief.py").write_text("x = 1\n", encoding="utf-8")
    (fake / "context_claude").mkdir()
    try:
        (fake / "context_claude" / "link").symlink_to(fake / "autoresearch")
    except OSError:
        pytest.skip("无法建符号链接")
    module = _load()
    monkeypatch.setattr(module, "REPO_ROOT", fake)
    via_link = str(fake / "context_claude" / "link" / "scan" / "brief.py")
    payload = _payload("Read", {"file_path": via_link})
    payload["cwd"] = str(fake)
    assert _denied(module.decide(payload))
    plain = _payload("Read", {"file_path": str(fake / "context_claude" / "pack.json")})
    plain["cwd"] = str(fake)
    assert module.decide(plain) is None


# ── Codex:Bash 命令 ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("command", [
    "export AUTORESEARCH_ENGINE=codex\nsed -n '270,370p' autoresearch/scan/self_review.py",
    'rg -n "chk_blind_pass|citation_density|FINAL TRANSACTION" autoresearch/',
    "rg --files .claude autoresearch docs | rg 'l4.*intel'",
    "cd autoresearch && sed -n 1,50p scan/self_review.py",
    f"cat {SELF_REVIEW}",
    "sed -n '1,120p' .claude/workflows/l4-stock.js",
    "rg -n foo .",
])
def test_codex_research_role_shell_reads_outside_roots_are_denied(hook, command):
    assert _denied(hook.decide(_bash(command), "codex"))


def test_codex_shell_reads_are_confined_to_the_codex_data_root(tmp_path, monkeypatch):
    """假仓库里两个引擎各一份真实文件:Codex 角色读 context_claude 拒、读 context_codex 放。"""
    fake = tmp_path / "repo"
    for engine in ("claude", "codex"):
        target = fake / f"context_{engine}" / "scan_runs" / "R" / "_l4_prompt_1.md"
        target.parent.mkdir(parents=True)
        target.write_text("# pack\n", encoding="utf-8")
    module = _load()
    monkeypatch.setattr(module, "REPO_ROOT", fake)

    def run(command: str):
        payload = _bash(command)
        payload["cwd"] = str(fake)
        return module.decide(payload, "codex")

    assert _denied(run("sed -n 1,40p context_claude/scan_runs/R/_l4_prompt_1.md"))
    assert run("sed -n 1,40p context_codex/scan_runs/R/_l4_prompt_1.md") is None
    assert run("sed -n 1,40p context_codex/scan_runs/R/not_written_yet.md") is None


def test_codex_reads_of_own_data_and_contracts_pass(hook):
    ctx = ROOT / "context_codex"
    if not ctx.is_dir():
        pytest.skip("context_codex 不存在")
    sample = next((p for p in ctx.rglob("*.md")), None)
    commands = [
        "export AUTORESEARCH_ENGINE=codex\nsed -n '1,240p' .claude/agents/l4-card.md",
        "wc -l CLAUDE.md .claude/skills/stock-research/lite-playbook.md",
        "uv run --no-sync python -m autoresearch.scan.l4_tasks --help",
        "ls -la ~/.codex/skills",
    ]
    if sample is not None:
        commands.append(f"sed -n 1,40p {sample.relative_to(ROOT)}")
    for command in commands:
        assert hook.decide(_bash(command), "codex") is None, command


def test_codex_dot_is_a_path_only_for_search_programs(hook):
    """`jq .` 的 `.` 是表达式(09-14 夜 sector brief 8 条真命令);`rg … .` / `find .` 才是整仓搜索。"""
    assert hook.decide(_bash("export AUTORESEARCH_ENGINE=codex\njq . README.md.missing"), "codex") is None
    assert hook.decide(_bash("jq . CLAUDE.md | head -3"), "codex") is None
    assert _denied(hook.decide(_bash("rg -n citation_density ."), "codex"))
    assert _denied(hook.decide(_bash("export AUTORESEARCH_ENGINE=codex && find . -name '*l4*'"), "codex"))
    # 09-14 夜 sector brief 真命令:管道尾的 `rg -q .` 里 `.` 是正则
    assert hook.decide(_bash("rg -n '^## ' CLAUDE.md | tail -n +2 | rg -q . && echo EXTRA"), "codex") is None
    # 模式被引号包住时,后面的位置参数仍按路径判
    assert _denied(hook.decide(_bash('rg -n "def x|citation" autoresearch/scan'), "codex"))
    assert _denied(hook.decide(_bash("rg --files .claude autoresearch | rg 'l4.*intel'"), "codex"))
    assert _denied(hook.decide(_bash("grep -rn -e citation_density autoresearch"), "codex"))


def test_codex_assignment_values_are_checked_as_paths(hook):
    assert _denied(hook.decide(_bash("SRC=autoresearch/scan\nrg -n def \"$SRC\""), "codex"))
    assert hook.decide(_bash("OUT=CLAUDE.md\nwc -c \"$OUT\""), "codex") is None


def test_codex_heredoc_and_quoted_text_are_not_read_targets(hook):
    """写卡命令的正文里提到源码路径不是读盘:误拒它会让卡写不出来。"""
    card = ("cat > context_codex/scan_runs/R/staging/2026-09-14/details/600000.md <<'EOF'\n"
            "证据见 docs/research/2026-08-26-buy-owner-spikes/ 与 autoresearch/scan/self_review.py\n"
            "EOF")
    assert hook.decide(_bash(card), "codex") is None
    assert hook.decide(_bash('printf "%s" "对照 autoresearch/scan/brief.py 的口径"'), "codex") is None


def test_codex_role_names_are_normalized_and_relays_pass(hook):
    for role in ("L4 card", "L3 rank", "L4 intel", "sector brief", "scan strategist",
                 "dossier init", "ensemble review", "L3 repair"):
        assert _denied(hook.decide(_bash(f"cat {SELF_REVIEW}", role), "codex")), role
    for relay in ("deterministic command relay", "deterministic JSON relay", "default", None):
        assert hook.decide(_bash(f"cat {SELF_REVIEW}", relay), "codex") is None


def test_engine_roots_are_isolated(hook):
    claude_pack = _payload("Read", {"file_path": TASK_PACK})
    assert hook.decide(claude_pack, "claude") is None
    assert _denied(hook.decide(claude_pack, "codex"))


# ── 共用 ────────────────────────────────────────────────────────────────────────

def test_malformed_input_never_blocks(hook):
    for raw in ("", "not json", "[]", json.dumps({"agent_type": "l4-card"}),
                json.dumps({"agent_type": "l4-card", "tool_name": "Read", "tool_input": "x"}),
                json.dumps({"agent_type": "L4 card", "tool_name": "Bash", "tool_input": {"command": 7}})):
        out = io.StringIO()
        assert hook.main(io.StringIO(raw), out, ["codex"]) == 0
        assert out.getvalue() == ""


def test_unknown_engine_never_blocks(hook):
    assert hook.decide(_payload("Read", {"file_path": SELF_REVIEW}), "gemini") is None


def test_main_emits_deny_json(hook):
    out = io.StringIO()
    raw = json.dumps(_payload("Read", {"file_path": SELF_REVIEW}))
    assert hook.main(io.StringIO(raw), out, ["claude"]) == 0
    assert _denied(json.loads(out.getvalue()))


def test_guarded_set_covers_every_research_role_of_both_engines(hook):
    claude = {hook.normalize_agent_type(p.stem) for p in (ROOT / ".claude" / "agents").glob("*.md")}
    codex = {
        hook.normalize_agent_type(tomllib.loads(p.read_text(encoding="utf-8"))["name"])
        for p in (ROOT / ".codex" / "agents").glob("*.toml") if p.stem not in CODEX_RELAY_ROLES
    }
    assert set(hook.GUARDED_AGENT_TYPES) == claude | codex, (
        "研究角色清单变了:显式决定新角色是否受输入边界约束,再同步 GUARDED_AGENT_TYPES")


def test_claude_settings_wire_the_wrapper_for_read_grep_glob():
    settings = json.loads((ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
    wired = [e for e in settings["hooks"]["PreToolUse"]
             if any("agent_input_boundary.sh" in h.get("command", "") for h in e["hooks"])]
    assert wired, ".claude/settings.json 未接 agent_input_boundary.sh"
    assert set(wired[0]["matcher"].split("|")) >= {"Read", "Grep", "Glob"}
    assert wired[0]["hooks"][0]["command"].rstrip().endswith(" claude")
    assert WRAPPER.is_file() and SCRIPT.is_file()


def test_codex_hooks_wire_the_wrapper_for_bash():
    hooks = json.loads((ROOT / ".codex" / "hooks.json").read_text(encoding="utf-8"))
    wired = [e for e in hooks["hooks"]["PreToolUse"]
             if any("agent_input_boundary.sh" in h.get("command", "") for h in e["hooks"])]
    assert wired, ".codex/hooks.json 未接 agent_input_boundary.sh"
    assert "Bash" in wired[0]["matcher"].split("|")
    assert wired[0]["hooks"][0]["command"].rstrip().endswith(" codex")


def _run_wrapper(payload: dict, engine: str) -> str:
    proc = subprocess.run(["sh", str(WRAPPER), engine], input=json.dumps(payload),
                          capture_output=True, text=True, timeout=30, check=False)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def test_wrapper_denies_research_agents_and_skips_main_thread():
    assert _denied(json.loads(_run_wrapper(_payload("Read", {"file_path": SELF_REVIEW}), "claude")))
    assert _denied(json.loads(_run_wrapper(_bash(f"cat {SELF_REVIEW}"), "codex")))
    assert _run_wrapper(_payload("Read", {"file_path": SELF_REVIEW}, agent_type=None), "claude") == ""
    assert _run_wrapper(_bash(f"cat {SELF_REVIEW}", agent_type=None), "codex") == ""
    assert _run_wrapper(_payload("Read", {"file_path": TASK_PACK}), "claude") == ""
