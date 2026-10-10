"""token 防膨胀的改动时规则(R10–R13),挂在 ``config_standard`` 的同一个 lint 与 PostToolUse hook 上。零 LLM。

- **R10 agent 文件字符预算**:``.claude/agents/*.md`` 每个文件都要在 ``budgets.agent_chars`` 登记上限且不超;
  两个引擎的研究角色读的都是它(claude 当系统提示,codex 经 broker),每次调用都付这份钱。新 agent 不登记 = 违规。
- **R11 静态成本清单**:``token_bom.check`` —— 当前配置下一场的估算超过 ``budgets.declared`` = 违规。
- **R12 档位锁**:``contracts/tier_lock.json`` 记每个 ``<engine>:<role>`` 与每个 agent 文件 frontmatter 的
  model / effort 历史。配置与锁的最后一条必须一致;第一条之后的每一条必须带 ``equivalence_ref``
  (仓内存在的等价读数文件)—— 档位只能带着证据改(10-03:别名静默换代,同 effort 输出 1.9 万 → 5.1 万)。
- **R13 预算线只降不升**:``budgets.declared`` 任一条线高于 git HEAD 里的值 = 违规。agent 不能自己抬线;
  人改这一行并提交即可。

CLI:``python -m autoresearch.scan.token_rules lock --add-missing``(把锁里缺的键按现状补第一条)。
"""
from __future__ import annotations

import argparse
import json
import subprocess
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
LOCK_PATH = REPO / "autoresearch" / "contracts" / "tier_lock.json"
AGENTS_DIR = REPO / ".claude" / "agents"
RULES = ("R10", "R11", "R12", "R13")


def _load_cfg(path: Path) -> dict | None:
    from autoresearch.scan.user_config import load_user_config

    try:
        return load_user_config(path)
    except Exception:  # noqa: BLE001 - a broken file is reported by R4/R5; these rules need a parsed one
        return None


# ── R10 ──────────────────────────────────────────────────────────────────────────

def lint_agent_chars(cfg: dict, *, agents_dir: Path = AGENTS_DIR) -> list[tuple[str, str, str]]:
    budget = (cfg.get("budgets") or {}).get("agent_chars") or {}
    present = {path.stem: len(path.read_text(encoding="utf-8")) for path in sorted(agents_dir.glob("*.md"))}
    out = []
    for name, chars in present.items():
        cap = budget.get(name)
        if cap is None:
            out.append(("R10", f"budgets.agent_chars.{name}",
                        f"agent 文件 {name}.md({chars} 字)没有登记字符预算:新增角色先登记预算与消费者"))
        elif chars > int(cap):
            out.append(("R10", f"budgets.agent_chars.{name}",
                        f"{name}.md {chars} 字 > 预算 {cap}:每个研究线程每次调用都付这段;先删再加,或由人上调预算"))
    for name in sorted(set(budget) - set(present)):
        out.append(("R10", f"budgets.agent_chars.{name}", f"登记了预算但 .claude/agents/{name}.md 不存在(死键)"))
    return out


# ── R11 ──────────────────────────────────────────────────────────────────────────

def lint_bom(cfg: dict) -> list[tuple[str, str, str]]:
    from autoresearch.scan import token_bom

    return [("R11", where, message) for where, message in token_bom.check(cfg)]


# ── R12 ──────────────────────────────────────────────────────────────────────────

def frontmatter(path: Path) -> dict[str, str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    out = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" in line:
            key, _, value = line.partition(":")
            out[key.strip()] = value.strip()
    return out


def current_tiers(cfg: dict, *, agents_dir: Path = AGENTS_DIR) -> dict[str, dict]:
    """``<engine>:<role>`` 与 ``agent:<文件名>`` → ``{"model", "effort"}``(现状)。"""
    from autoresearch.scan.user_config import resolve_agent_config

    out = {}
    for engine in ("claude", "codex"):
        for role, spec in resolve_agent_config(cfg, engine=engine, require_all=False).items():
            out[f"{engine}:{role}"] = {"model": spec.get("model"),
                                       "effort": spec.get("effort") or spec.get("reasoning_effort")}
    for path in sorted(agents_dir.glob("*.md")):
        meta = frontmatter(path)
        if meta.get("model") or meta.get("effort"):
            out[f"agent:{path.stem}"] = {"model": meta.get("model"), "effort": meta.get("effort")}
    return out


def read_lock(path: Path = LOCK_PATH) -> dict:
    if not path.is_file():
        return {"schema_version": 1, "entries": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def lint_tier_lock(cfg: dict, *, lock_path: Path = LOCK_PATH, repo_root: Path = REPO,
                   agents_dir: Path = AGENTS_DIR) -> list[tuple[str, str, str]]:
    lock = read_lock(lock_path).get("entries") or {}
    now = current_tiers(cfg, agents_dir=agents_dir)
    out = []
    for key in sorted(set(now) - set(lock)):
        out.append(("R12", key, "档位锁里没有这个键:先 `python -m autoresearch.scan.token_rules lock --add-missing`"))
    for key in sorted(set(lock) - set(now)):
        out.append(("R12", key, "锁里有、配置里没有:删角色时把锁里这一键一起删"))
    for key in sorted(set(lock) & set(now)):
        history = lock[key]
        last = history[-1] if history else {}
        if (last.get("model"), last.get("effort")) != (now[key]["model"], now[key]["effort"]):
            out.append(("R12", key,
                        f"配置 {now[key]['model']}/{now[key]['effort']} ≠ 锁 {last.get('model')}/{last.get('effort')}:"
                        "改档位要在 tier_lock.json 追加一条带 equivalence_ref(等价读数)的历史"))
        for item in history[1:]:
            ref = item.get("equivalence_ref")
            if not ref or not (repo_root / ref).is_file():
                out.append(("R12", key, f"档位变更 {item.get('model')}/{item.get('effort')} 缺等价读数"
                                        f"(equivalence_ref={ref!r} 不存在)"))
    return out


def add_missing(cfg: dict, *, lock_path: Path = LOCK_PATH, agents_dir: Path = AGENTS_DIR) -> list[str]:
    lock = read_lock(lock_path)
    entries = lock.setdefault("entries", {})
    added = []
    for key, tier in current_tiers(cfg, agents_dir=agents_dir).items():
        if key not in entries:
            entries[key] = [{**tier, "since": date.today().isoformat(), "equivalence_ref": None}]
            added.append(key)
    lock["entries"] = dict(sorted(entries.items()))
    lock_path.write_text(json.dumps(lock, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return added


# ── R13 ──────────────────────────────────────────────────────────────────────────

def _head_declared(repo_root: Path, rel: str) -> dict | None:
    from autoresearch.scan.user_config import _strip_jsonc

    try:
        text = subprocess.run(["git", "show", f"HEAD:{rel}"], cwd=repo_root, capture_output=True,
                              text=True, check=True, timeout=10).stdout
        return (json.loads(_strip_jsonc(text)).get("budgets") or {}).get("declared") or {}
    except Exception:  # noqa: BLE001 - no git / no HEAD copy = nothing to ratchet against
        return None


def lint_declared_ratchet(cfg: dict, path: Path, *, repo_root: Path = REPO) -> list[tuple[str, str, str]]:
    from autoresearch.contracts import scan_config as reg

    try:
        canonical = (repo_root / reg.CONFIG_PATH).resolve()
        if Path(path).resolve() != canonical:
            return []
    except OSError:
        return []
    before = _head_declared(repo_root, str(reg.CONFIG_PATH))
    if not before:
        return []
    now = (cfg.get("budgets") or {}).get("declared") or {}
    out = []
    for key, value in now.items():
        old = before.get(key)
        if isinstance(old, (int, float)) and float(value) > float(old):
            out.append(("R13", f"budgets.declared.{key}",
                        f"预算线只许下调:HEAD {old} → 现在 {value}。上调由人改这一行并提交"))
    return out


# ── 汇总 ─────────────────────────────────────────────────────────────────────────

def lint(path: Path, *, repo_root: Path = REPO, wanted: set[str] | None = None) -> list[tuple[str, str, str]]:
    wanted = set(wanted or RULES)
    cfg = _load_cfg(Path(path))
    if cfg is None:
        return []
    out: list[tuple[str, str, str]] = []
    if "R10" in wanted:
        out.extend(lint_agent_chars(cfg))
    if "R11" in wanted:
        out.extend(lint_bom(cfg))
    if "R12" in wanted:
        out.extend(lint_tier_lock(cfg, repo_root=repo_root))
    if "R13" in wanted:
        out.extend(lint_declared_ratchet(cfg, Path(path), repo_root=repo_root))
    return out


def main(argv: list[str] | None = None) -> int:
    from autoresearch.scan.user_config import load_user_config

    ap = argparse.ArgumentParser(prog="python -m autoresearch.scan.token_rules")
    sub = ap.add_subparsers(dest="command", required=True)
    lock = sub.add_parser("lock", help="档位锁")
    lock.add_argument("--add-missing", action="store_true", help="锁里缺的键按现状补第一条(不改已有历史)")
    args = ap.parse_args(argv)
    if args.command == "lock" and args.add_missing:
        print(json.dumps({"added": add_missing(load_user_config())}, ensure_ascii=False))
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
