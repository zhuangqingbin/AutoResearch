#!/usr/bin/env python3
"""PreToolUse hook(Claude Code 与 Codex 共用)—— 项目研究 agent 的输入边界(2026-09-15)。

事故现场:09-14 夜扫描,Claude Code 2.1.270 下 l4-card 每卡中位轮次 6–7 → 24、cache 读
266k → 1,337k;l3-rank 4–5 → 27 轮;macro-brief 5 → 21 轮。它们去 Grep/Read `autoresearch/`
里的解析器与 lint(price_claims / self_review / parsers / rating / rubric …)、
`.claude/workflows/*.js` 和其它 run 的旧产物,想「先验证产物能过机检」。同一夜 Codex 侧的
L4 card / L4 intel 子 agent 用 `rg`/`sed` 做了同样的事。agent 定义、派发 prompt、模型与
effort 都没变。本仓「指令级约束无强制力」的前科很多,所以边界做成确定性 hook,agent 定义
里的文字只负责把「为什么不用去读」讲清楚。

调用:`agent_input_boundary.sh <engine>`(`claude` / `codex`)。两边 harness 的 hook 输入
同构:主线程不带 `agent_type`;子 agent 带 `agent_id` + `agent_type`。
- Claude Code:`agent_type` = `.claude/agents/<name>.md` 的 name(如 `l4-card`),工具是
  Read / Grep / Glob。2.1.271 的 hook 输入构造器取 `toolUseContext.agentType`,Workflow 派发的
  agent 同样带(8 月 `SubagentStart:l4-card` matcher 在 workflow agent 上命中过)。
- Codex 0.154:`agent_type` = `.codex/agents/*.toml` 的 `name`(如 `L4 card`,2026-09-15 真仓
  探针实测 `L3 repair`),读盘走 `Bash` 工具,`tool_input.command` 是整条 shell 命令。

判定(只管 `GUARDED_AGENT_TYPES` 里的研究角色,名字按小写 + 空格/连字符→下划线归一;主线程、
Claude 的 general-purpose / Explore / Plan、Codex 的确定性命令壳一律放行):
- 仓库外路径:放行,不归本 hook 管。
- 仓库内:只放行本引擎的数据根 `context_<engine>/`、`reports_<engine>/`,以及
  `.claude/skills/`、`.claude/agents/`、根目录 `CLAUDE.md` / `AGENTS.md`;其余(`autoresearch/`、
  `tests/`、`scripts/`、`.claude/workflows/`、`.codex/`、`docs/`、`lake/`、`.worktrees/`、另一引擎
  的数据根 …)一律拒。放行名单而不是拒绝名单:`.worktrees/<x>/autoresearch/` 这种整仓副本,
  拒绝名单永远列不全。
- Grep / Glob 按「搜索基底」判:Grep 看 `path`(缺省 = cwd),Glob 看 `path` 加 `pattern` 的字面
  前缀。基底是仓库根或放行子树的祖先(如 `.claude/`)→ 拒,整棵搜下去必然扫进源码。
- Bash 命令:先抹掉 heredoc 正文与引号内文字(那是写进产物的内容或正则,不是读盘目标),
  再逐个检查剩下的词里**磁盘上真实存在**的路径。已知漏网:藏在引号或 heredoc 里的读
  (如 `python3 - <<'PY' open('autoresearch/…')`)。这是有意的取舍:误拒一条写卡命令会让卡
  出不来,代价远大于漏掉一次偷看。
- 词法路径与 realpath 两个视图都查,任一落进禁区即拒(防 `..` 与符号链接绕行)。

拒绝 = 标准 PreToolUse JSON(`permissionDecision: "deny"`),两边 harness 同格式;理由里带
`MARKER`,事后可以在 transcript / rollout 里 grep 这个串计数。

**任何异常都放行、exit 0**:hook 自己出错绝不能打断工具调用(同 webtrace_posttool.py)。
同目录 `agent_input_boundary.sh` 是零成本前置过滤:主线程调用不带 `agent_type`,不起 Python。
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

#: 受管的研究角色(归一化名)。测试锁两份清单:`.claude/agents/*.md` 的全部 name,以及
#: `.codex/agents/*.toml` 里除确定性命令壳之外的全部 name —— 新增角色必须显式决定是否受管。
#: 没有读盘工具的角色(Claude 侧各 intel)在这里只是占位,以后谁给它们加了读盘工具,边界自动生效。
GUARDED_AGENT_TYPES = frozenset({
    # Claude Code(.claude/agents/*.md)
    "company_intel",
    "dossier_init",
    "global_intel",
    "l3_rank",
    "l4_card",
    "l4_intel",
    "macro_brief",
    "sector_brief",
    "sector_intel",
    "us_intel",
    # Codex(.codex/agents/*.toml 的 name;与上面重名的不重复列)
    "ensemble_review",
    "l3_repair",
    "scan_strategist",
})

ENGINES = ("claude", "codex")

MARKER = "AGENT_INPUT_BOUNDARY"

REPO_ROOT = Path(__file__).resolve().parents[2]

_GLOB_CHARS = frozenset("*?[{")
_HEREDOC = re.compile(r"<<-?[ \t]*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1[^\n]*\n.*?^[ \t]*\2[ \t]*$",
                      re.S | re.M)
_QUOTED = re.compile(r"'[^']*'|\"(?:\\.|[^\"\\])*\"")
_SEGMENT = re.compile(r"[\n;|&]+")
_WORD = re.compile(r"[^\s<>()`]+")
#: 只有这些程序的 `.` / `..` 参数才是「搜这个目录」;`jq .`、`awk '…' .` 这类 `.` 是表达式。
_DOT_PATH_PROGRAMS = frozenset({"rg", "grep", "egrep", "fgrep", "find", "fd", "ag", "ls", "tree",
                                "du", "cd"})
#: 第一个位置参数是模式不是路径的程序(`rg -q .` 的 `.` 是正则);带这些旗标时模式在旗标里。
_PATTERN_FIRST_PROGRAMS = frozenset({"rg", "grep", "egrep", "fgrep", "ag", "fd"})
_PATTERN_IN_FLAG = frozenset({"--files", "-e", "--regexp", "-f", "--file"})
#: 引号内文字替换成占位词而不是删掉:位置参数的计数才不会错位(`rg -n "x" autoresearch/`
#: 里被引号包住的是模式,后面的 `autoresearch/` 仍是路径)。
_QUOTE_PLACEHOLDER = " \x00q\x00 "
_COMMAND_PREFIXES = frozenset({"export", "env", "command", "time", "nice", "exec", "then", "do",
                               "if", "while", "else", "!"})


def normalize_agent_type(value: object) -> str:
    return re.sub(r"[\s\-]+", "_", str(value).strip().lower())


def allowed_prefixes(engine: str) -> tuple[tuple[str, ...], ...]:
    return (
        (f"context_{engine}",),
        (f"reports_{engine}",),
        (".claude", "skills"),
        (".claude", "agents"),
        ("CLAUDE.md",),
        ("AGENTS.md",),
    )


def _literal_prefix(pattern: str) -> str:
    """glob 模式里第一个通配段之前的字面路径(`a/b/*x*` → `a/b`;`**/*.py` → 空串)。"""
    literal: list[str] = []
    for part in pattern.replace("\\", "/").split("/"):
        if any(ch in part for ch in _GLOB_CHARS):
            break
        literal.append(part)
    return "/".join(literal)


def _resolve(path: str, cwd: str) -> str:
    expanded = os.path.expanduser(path)
    if not os.path.isabs(expanded):
        expanded = os.path.join(cwd or os.getcwd(), expanded)
    return os.path.normpath(expanded)


def _command_paths(command: str, cwd: str) -> list[str]:
    """shell 命令里真实存在的路径词(heredoc 正文与引号内文字不算)。"""
    text = _QUOTED.sub(_QUOTE_PLACEHOLDER, _HEREDOC.sub(" ", command))
    found: list[str] = []
    for segment in _SEGMENT.split(text):
        words = _WORD.findall(segment)
        program: str | None = None
        skip_pattern = False
        for word in words:
            if program is None and word in _COMMAND_PREFIXES:
                continue
            is_assignment = "=" in word and not word.startswith("-")
            if "=" in word:
                word = word.split("=", 1)[1]
            if program is None and not is_assignment:
                program = os.path.basename(word)
                skip_pattern = (program in _PATTERN_FIRST_PROGRAMS
                                and not _PATTERN_IN_FLAG.intersection(words))
                if program in _DOT_PATH_PROGRAMS or program in _PATTERN_FIRST_PROGRAMS:
                    continue
            word = word.strip(",:")
            if not word or word.startswith("-"):
                continue
            if skip_pattern:
                skip_pattern = False
                continue
            if "\x00" in word:
                continue
            if word in (".", "..") and program not in _DOT_PATH_PROGRAMS:
                continue
            candidate = _literal_prefix(word) if any(ch in word for ch in _GLOB_CHARS) else word
            if candidate and os.path.lexists(_resolve(candidate, cwd)):
                found.append(candidate)
    return found


def _search_bases(tool: object, tool_input: dict, cwd: str) -> list[str]:
    """这次调用会触达的路径基底;认不出的工具或缺字段 → 空列表(放行)。"""
    if tool == "Read":
        path = tool_input.get("file_path")
        return [path] if isinstance(path, str) and path else []
    if tool == "Grep":
        path = tool_input.get("path")
        return [path if isinstance(path, str) and path else cwd]
    if tool == "Glob":
        base = tool_input.get("path")
        base = base if isinstance(base, str) and base else cwd
        pattern = tool_input.get("pattern")
        prefix = _literal_prefix(pattern) if isinstance(pattern, str) else ""
        if os.path.isabs(prefix):
            return [prefix]
        return [os.path.join(base, prefix) if prefix else base]
    if tool == "Bash":
        command = tool_input.get("command")
        return _command_paths(command, cwd) if isinstance(command, str) else []
    return []


def denied_part(path: str, cwd: str, engine: str = "claude") -> str | None:
    """路径落进仓库禁区 → 返回相对仓库根的展示串(仓库根本身为空串);否则 None。"""
    if not path:
        return None
    lexical = _resolve(path, cwd)
    roots = {str(REPO_ROOT), os.path.realpath(str(REPO_ROOT))}
    prefixes = allowed_prefixes(engine)
    for candidate in (lexical, os.path.realpath(lexical)):
        for root in roots:
            if candidate == root:
                parts: tuple[str, ...] = ()
            elif candidate.startswith(root + os.sep):
                parts = tuple(Path(candidate[len(root) + 1:]).parts)
            else:
                continue
            if not any(parts[: len(prefix)] == prefix for prefix in prefixes):
                return "/".join(parts)
    return None


def _deny(agent_type: str, tool: str, where: str, engine: str) -> dict:
    target = where or "仓库根(整仓搜索)"
    reason = (
        f"{MARKER}: {agent_type} 的 {tool} 越出研究输入边界({target})。"
        f"研究 agent 只读派发 prompt / 任务包点名的数据文件(context_{engine}/、reports_{engine}/)"
        "和 .claude/skills、.claude/agents 下的契约文档;项目源码、测试、脚本、workflow、docs、"
        "lake、另一引擎的目录都不在输入范围。产物的机读格式已完整写在你的角色契约里,"
        "解析、lint、对账在你交付之后由确定性层执行 —— 不要去核对解析器,按契约直接完成产物。"
        f"找文件请直接用任务包给的完整路径;确实要搜,把搜索范围限定在 context_{engine}/ 之下。"
    )
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def decide(payload: dict, engine: str = "claude") -> dict | None:
    """hook 输入 → 拒绝 JSON;放行返回 None。"""
    if engine not in ENGINES:
        return None
    agent_type = payload.get("agent_type")
    if agent_type is None or normalize_agent_type(agent_type) not in GUARDED_AGENT_TYPES:
        return None
    tool = payload.get("tool_name")
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    cwd = payload.get("cwd")
    cwd = cwd if isinstance(cwd, str) else ""
    for base in _search_bases(tool, tool_input, cwd):
        where = denied_part(base, cwd, engine)
        if where is not None:
            return _deny(str(agent_type), str(tool), where, engine)
    return None


def main(stdin=None, stdout=None, argv: list[str] | None = None) -> int:
    stdin = stdin if stdin is not None else sys.stdin
    stdout = stdout if stdout is not None else sys.stdout
    argv = list(sys.argv[1:] if argv is None else argv)
    engine = argv[0] if argv else "claude"
    try:
        payload = json.loads(stdin.read() or "{}")
        if not isinstance(payload, dict):
            return 0
        verdict = decide(payload, engine)
        if verdict is not None:
            stdout.write(json.dumps(verdict, ensure_ascii=True))
    except Exception:  # noqa: BLE001 —— hook 失败必须放行
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
