#!/usr/bin/env python3
"""agent 定义的 `model` / effort 与 `scan_config.jsonc` 同源检查(Claude frontmatter + Codex toml)。

为什么要有这一层:两种执行器从**不同的地方**取模型与 effort ——

- headless(`claude -p --agent <x> --model … --effort …`)取 `scan_config` 的解析值;
- mailbox(宿主会话用 Agent 工具派发)取 `.claude/agents/<x>.md` 的 frontmatter,
  因为 Agent 工具的 `model` 入参只接受别名,传不了全 ID。

唯一事实源是 `scan_config.jsonc` 的 `agent_engines.claude`;frontmatter 是它的镜像。本模块
只做两件事:`check()` 报出两边不一致的地方,`write()` 把 frontmatter 的这两行改成配置的值。
别名(`opus` / `sonnet`)由 Claude Code 客户端解析、随自动升级移动,所以生产配置里推理角色
必须是价表认识的全 ID(`trace.pricing.KNOWN_MODEL_IDS`)。

Codex 一侧同理:宿主 `spawn_agent` 按名字派发,生效的是 `.codex/agents/<role>.toml` 的
`model` / `model_reasoning_effort`,唯一事实源是 `agent_engines.codex`。

运行期的同一条约束由 `session_agent.preflight` 对 mailbox 执行器强制(取数前拦下)。

CLI(缺省两个引擎都查):
  uv run --no-sync python -m autoresearch.scan.agent_frontmatter --check
  uv run --no-sync python -m autoresearch.scan.agent_frontmatter --write
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

import tomllib

from autoresearch.contracts.agent_roles import configured_agents
from autoresearch.scan import user_config

AGENTS_DIR = Path(".claude") / "agents"
CODEX_AGENTS_DIR = Path(".codex") / "agents"
_FIELDS = ("model", "effort", "maxTurns", "omitClaudeMd")
#: 研究 agent 一律不加载 CLAUDE.md(B8,2026-10-03):那份讲的是编排与入口,研究 agent 用不上。
_STATIC = {"omitClaudeMd": "true"}
#: 解析后的 Codex spec 键 → toml 键。
_CODEX_FIELDS = {"model": "model", "reasoning_effort": "model_reasoning_effort"}


def expected(cfg: dict) -> dict[str, dict]:
    """`{agent 文件名(无扩展名): {"model", "effort", "roles": [...]}}`,取共用该文件的首个 role。

    没有物理 agent 的 role(命令壳)不在表里。`model` / `effort` 缺席时对应键不出现,
    由 `check()` 报成违规,而不是在这里补缺省。
    """
    resolved = user_config.resolve_agent_config(cfg, engine="claude")
    turns = _max_turns(cfg)
    out: dict[str, dict] = {}
    for role, spec in configured_agents().items():
        agent = spec["claude_agent"]
        if agent is None or role not in resolved:
            continue
        entry = out.setdefault(agent, {"roles": []})
        if not entry["roles"]:
            entry.update({key: resolved[role][key] for key in ("model", "effort") if key in resolved[role]})
            if agent in turns:
                entry["maxTurns"] = str(turns[agent])
            entry.update(_STATIC)
        entry["roles"].append(role)
    return {agent: {**{k: v for k, v in entry.items() if k != "roles"}, "roles": entry["roles"]}
            for agent, entry in out.items()}


def _max_turns(cfg: dict) -> dict[str, int]:
    """每个 agent 定义的 `maxTurns` = 它服务的 session 角色里最大的那个轮数上限。

    mailbox 派发此前没有任何轮数上限(headless 有 `--max-turns`);两条路径同一个数,出自
    `headless_claude.role_max_turns`(`session.max_turns` 配置 → 内建表 → 档位 → 缺省)。
    """
    from autoresearch.contracts.agent_roles import (
        DEFAULT_MAX_TURNS,
        dispatch_mapping,
        role_max_turns,
    )

    knob = user_config.knob
    overrides = knob("session", "max_turns", None, {}, cfg) or {}
    tier_overrides = knob("session", "tier_max_turns", None, {}, cfg) or {}
    default = int(knob("session", "default_max_turns", None, DEFAULT_MAX_TURNS, cfg))
    physical = configured_agents()
    out: dict[str, int] = {}
    for role_id, (agent, config_role) in dispatch_mapping().items():
        if agent is None:
            continue
        tier = (((cfg.get("agents") or {}).get(config_role) or {}).get("tier")
                or physical[config_role]["tier"])
        out[agent] = max(out.get(agent, 0), role_max_turns(
            role_id, tier, overrides=overrides, tier_overrides=tier_overrides, default=default))
    return out


def _split(text: str) -> tuple[str, str, str] | None:
    """`---\\n<frontmatter>\\n---<rest>` → (开头, frontmatter, 其余);不是这个形状 → None。"""
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end == -1:
        return None
    return text[:3], text[3:end], text[end:]


def read(path: Path) -> dict[str, str]:
    """读 frontmatter 的 `model:` / `effort:`;缺文件或没有 frontmatter → `{}`。"""
    try:
        parts = _split(path.read_text(encoding="utf-8"))
    except OSError:
        return {}
    if parts is None:
        return {}
    found: dict[str, str] = {}
    for line in parts[1].splitlines():
        for key in _FIELDS:
            if line.startswith(f"{key}:"):
                found[key] = line[len(key) + 1:].strip()
    return found


def check(cfg: dict, *, agents_dir: Path | str = AGENTS_DIR) -> list[str]:
    """返回违规清单(空 = 同源)。只报事实,不改文件。"""
    agents = Path(agents_dir)
    want = expected(cfg)
    resolved = user_config.resolve_agent_config(cfg, engine="claude")
    violations: list[str] = []
    for agent in sorted(want):
        entry = want[agent]
        name = f"{agent}.md"
        primary = entry["roles"][0]
        if not (agents / name).is_file():
            violations.append(f"{name}: agent 定义缺失")
            continue
        if "model" not in entry:
            violations.append(
                f"{name}: scan_config 没有给 role {primary} 钉模型(agent_engines.claude)")
            continue
        specs = {role: {key: resolved[role].get(key) for key in ("model", "effort")}
                 for role in entry["roles"]}
        if len({tuple(spec.items()) for spec in specs.values()}) > 1:
            shown = " / ".join(f"{role}={spec}" for role, spec in specs.items())
            violations.append(
                f"{name}: 共用该定义的 role 解析结果不同({shown});"
                "mailbox 执行器只读 frontmatter,分不开")
            continue
        have = read(agents / name)
        for key in _FIELDS:
            if key in entry and have.get(key) != entry[key]:
                violations.append(
                    f"{name}: {key} 是 {have.get(key)!r},scan_config 解析为 "
                    f"{entry[key]!r}(role {primary})")
    for path in sorted(agents.glob("*.md")):
        if path.stem not in want:
            violations.append(
                f"{path.name}: 没有任何已登记 role 使用该 agent 定义"
                "(contracts.agent_roles.PHYSICAL_AGENTS)")
    return violations


def write(cfg: dict, *, agents_dir: Path | str = AGENTS_DIR) -> list[str]:
    """把 frontmatter 的 `model:` / `effort:` 改成配置解析值;返回改动过的文件名(幂等)。

    只动 frontmatter 块里的这两行;正文里即使有 `model:` 开头的行也不碰。缺文件、缺 frontmatter、
    共用 role 不一致等结构性问题留给 `check()` 报,本函数不猜。
    """
    agents = Path(agents_dir)
    changed: list[str] = []
    for agent, entry in sorted(expected(cfg).items()):
        path = agents / f"{agent}.md"
        try:
            parts = _split(path.read_text(encoding="utf-8"))
        except OSError:
            continue
        if parts is None:
            continue
        head, front, rest = parts
        updated = front
        for key in _FIELDS:
            if key not in entry:
                continue
            if re.search(rf"(?m)^{key}:", updated):
                updated = re.sub(rf"(?m)^{key}:.*$", f"{key}: {entry[key]}", updated, count=1)
            else:
                updated = updated.rstrip("\n") + f"\n{key}: {entry[key]}"
        if updated != front:
            path.write_text(head + updated + rest, encoding="utf-8")
            changed.append(path.name)
    return changed


def codex_expected(cfg: dict) -> dict[str, dict]:
    """`{config role: {"model", "reasoning_effort"}}` —— Codex 一个 role 一份 toml,不共用。"""
    resolved = user_config.resolve_agent_config(cfg, engine="codex")
    return {role: {key: resolved[role][key] for key in _CODEX_FIELDS if key in resolved[role]}
            for role in configured_agents() if role in resolved}


def _toml_header(text: str) -> tuple[str, str]:
    """(第一个多行字符串开始之前的部分, 其余);只在前者里认 / 改 `model` 两行。"""
    start = text.find('"""')
    if start == -1:
        return text, ""
    cut = text.rfind("\n", 0, start) + 1
    return text[:cut], text[cut:]


def read_codex(path: Path) -> dict[str, str]:
    """读 toml 的 `model` / `model_reasoning_effort`(键名按解析后的 spec);缺文件 → `{}`。"""
    try:
        doc = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    return {key: doc[toml_key] for key, toml_key in _CODEX_FIELDS.items() if toml_key in doc}


def check_codex(cfg: dict, *, agents_dir: Path | str = CODEX_AGENTS_DIR) -> list[str]:
    """Codex 角色定义与 `agent_engines.codex` 的违规清单(空 = 同源)。"""
    agents = Path(agents_dir)
    want = codex_expected(cfg)
    violations: list[str] = []
    for role in sorted(want):
        name = f"{role}.toml"
        if not (agents / name).is_file():
            violations.append(f"{name}: agent 定义缺失")
            continue
        if "model" not in want[role]:
            violations.append(f"{name}: scan_config 没有给 role {role} 钉模型(agent_engines.codex)")
            continue
        have = read_codex(agents / name)
        for key, toml_key in _CODEX_FIELDS.items():
            if key in want[role] and have.get(key) != want[role][key]:
                violations.append(f"{name}: {toml_key} 是 {have.get(key)!r},scan_config 解析为 "
                                  f"{want[role][key]!r}")
    for path in sorted(agents.glob("*.toml")):
        if path.stem not in want:
            violations.append(f"{path.name}: 没有任何已登记 role 使用该 agent 定义"
                              "(contracts.agent_roles.PHYSICAL_AGENTS)")
    return violations


def write_codex(cfg: dict, *, agents_dir: Path | str = CODEX_AGENTS_DIR) -> list[str]:
    """把 toml 头部的 `model` / `model_reasoning_effort` 改成配置解析值;返回改动过的文件名(幂等)。

    只改第一个多行字符串(`developer_instructions`)之前的那两行 —— 指令正文里出现同样的字样也不碰。
    """
    agents = Path(agents_dir)
    changed: list[str] = []
    for role, want in sorted(codex_expected(cfg).items()):
        path = agents / f"{role}.toml"
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        head, rest = _toml_header(text)
        updated = head
        for key, toml_key in _CODEX_FIELDS.items():
            if key in want:
                updated = re.sub(rf'(?m)^{toml_key} = ".*"$', f'{toml_key} = "{want[key]}"',
                                 updated, count=1)
        if updated != head:
            path.write_text(updated + rest, encoding="utf-8")
            changed.append(path.name)
    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="只检查;有违规退出码 1")
    mode.add_argument("--write", action="store_true", help="按 scan_config 改写 agent 定义")
    parser.add_argument("--config", default=None, help="scan_config.jsonc 路径(缺省 = 生产文件)")
    parser.add_argument("--engine", choices=("claude", "codex", "all"), default="all")
    parser.add_argument("--agents-dir", default=str(AGENTS_DIR))
    parser.add_argument("--codex-agents-dir", default=str(CODEX_AGENTS_DIR))
    args = parser.parse_args(argv)
    cfg = user_config.load_user_config(args.config or user_config._PRODUCTION_DEFAULT_PATH)
    engines = ("claude", "codex") if args.engine == "all" else (args.engine,)
    violations: list[str] = []
    for engine in engines:
        if engine == "claude":
            changed = write(cfg, agents_dir=args.agents_dir) if args.write else []
            found = check(cfg, agents_dir=args.agents_dir)
        else:
            changed = write_codex(cfg, agents_dir=args.codex_agents_dir) if args.write else []
            found = check_codex(cfg, agents_dir=args.codex_agents_dir)
        for name in changed:
            print(f"[agent_frontmatter] 已改写 {name}")
        violations += found
    for line in violations:
        print(f"[agent_frontmatter] {line}")
    if not violations:
        print(f"[agent_frontmatter] agent 定义与 scan_config 同源({'+'.join(engines)})")
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
