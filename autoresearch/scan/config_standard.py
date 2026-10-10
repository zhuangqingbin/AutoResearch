#!/usr/bin/env python3
"""scan_config 标准 lint —— 九条规则的机器判定(2026-09-27)。

事实源是注册表 `autoresearch/contracts/scan_config.py`;本模块拿它对账三样现场:
`.claude/skills/scan-market/scan_config.jsonc` 的文本、仓库里的消费代码、四份 skill 文档。

规则(编号与 SKILL.md「配置」节一致):
- R1 白名单 = 注册表派生,野键 / 错型 load 即 raise —— 由 `user_config.load_user_config` 自己执行,本模块不重复。
- R2 每键至少一个消费者:符号存在,且该函数源码里出现键名字面量(group 的每个子键亦然)。
- R3 单侧宿主:hosts=js/sa 的键须有对应宿主的消费者,且尾注写「仅 legacy workflow / 仅 session_agent」;
  hosts=js+sa 须 JS 与 session_agent 各有消费者。
- R4 文件 ↔ 注册表键集相等:不许暗键(注册表有、文件无)、不许野键(文件有、注册表无);group 子键同理。
- R5 注释格式:叶子键尾注恰一行、≤80 字符、无日期、无历史词;块头注 = 注册表渲染;文件头 ≤8 行;
  块内不许段落注释;structured 块尾注可选但同样守文本规则。
- R6 分区与顺序:顶层块顺序 = 注册表 BLOCK_ORDER;两行分区标题各恰一次且位置正确。
- R7 缺省统一:消费代码里的缺省字面量(knob 第四参数 / .get 缺省 / JS ??)== 注册表 default。
- R8 散落常量 ratchet:扫描范围内的模块级数值常量与公开函数的数值缺省必须登记在 CODE_CONSTANTS。
- R9 文档不复述键值:四份 skill 文档里键名后不得直接跟数字。
- R10–R14 token 防膨胀(2026-10-10,实现在 `scan.token_rules`):agent 文件字符预算 / 静态成本清单不超
  `budgets.declared` / 档位锁(改 model·effort 须附等价读数)/ 预算线只降不升(对 git HEAD)/ 角色须声明消费者。

CLI:`python -m autoresearch.scan.config_standard [--path P] [--rules R4,R5] [--fix-headers] [--dump-constants]`
退出码:0 零违规 / 1 有违规。PostToolUse hook(`scripts/hooks/scan_config_standard.sh`)把 1 映射成 2 交回编辑者。
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from autoresearch.contracts import scan_config as reg

REPO = Path(__file__).resolve().parents[2]
MAX_COMMENT_CHARS = 80
MAX_FILE_HEADER_LINES = 8
RULES = ("R2", "R3", "R4", "R5", "R6", "R7", "R8", "R9", "R10", "R11", "R12", "R13", "R14")

_DATE_RE = re.compile(r"20\d\d\s*[-年/.]\s*\d{1,2}")
_HISTORY_RE = re.compile(r"沿革|终审|回滚杆|裁定|design|spec|§|⚖️|🚨|幽灵|补记|读数|实测|Wave\s*\d|批\s*\d|Task\s*\d", re.I)
_KEY_LINE_RE = re.compile(r'^(\s*)"([^"]+)"\s*:\s*(.*)$')
_UPPER_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_NON_TUNABLE_NAME_RE = re.compile(r"VERSION|SCHEMA|^EXIT_")
_CLOCK_RE = re.compile(r"^\d{1,2}:\d{2}$")
_JS_CONST_RE = re.compile(r"^\s*const\s+([A-Z][A-Z0-9_]+)\s*=\s*(-?\d+(?:\.\d+)?)\b")
_SKIP_PARAM_DEFAULTS = {0, 1, -1}
_R9_GENERIC_KEYS = {"enabled", "source", "channel", "floors", "floor", "base", "flags", "mode", "pool", "cap", "m", "tiers", "sections", "delta"}


@dataclass(frozen=True)
class Violation:
    rule: str
    where: str
    message: str

    def __str__(self) -> str:
        return f"[{self.rule}] {self.where}: {self.message}"


# ═══════════════════════════ 文本层:R4 / R5 / R6 (+R3 宿主标签) ═══════════════════════════


def _split_comment(line: str) -> tuple[str, str | None]:
    """(去掉尾注的代码部分, 尾注文本或 None)。字符串内的 `//` 不算注释。"""
    in_str = False
    i = 0
    while i < len(line):
        c = line[i]
        if in_str:
            if c == "\\":
                i += 2
                continue
            if c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "/" and line[i:i + 2] == "//":
            return line[:i].rstrip(), line[i + 2:].strip()
        i += 1
    return line.rstrip(), None


def _brace_delta(code: str) -> int:
    in_str = False
    delta = 0
    i = 0
    while i < len(code):
        c = code[i]
        if in_str:
            if c == "\\":
                i += 2
                continue
            if c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c in "{[":
            delta += 1
        elif c in "}]":
            delta -= 1
        i += 1
    return delta


def _comment_text_violations(comment: str, where: str) -> list[Violation]:
    out: list[Violation] = []
    if len(comment) > MAX_COMMENT_CHARS:
        out.append(Violation("R5", where, f"尾注 {len(comment)} 字符 > {MAX_COMMENT_CHARS}"))
    if _DATE_RE.search(comment):
        out.append(Violation("R5", where, f"尾注含日期(历史不进 config):{comment!r}"))
    m = _HISTORY_RE.search(comment)
    if m:
        out.append(Violation("R5", where, f"尾注含历史词「{m.group(0)}」:{comment!r}"))
    return out


def lint_text(text: str, *, registry=reg) -> list[Violation]:
    """R4 / R5 / R6 + R3 的宿主标签:只看文件文本与注册表,不碰仓库其它文件。"""
    out: list[Violation] = []
    lines = text.split("\n")
    zone_headers = {registry.render_zone_header(z).strip(): z for z in registry.ZONES}
    block_headers = {registry.render_block_header(b.name).strip(): b.name for b in registry.BLOCKS}
    structured = {k.block for k in registry.KEYS if k.kind == registry.KIND_STRUCTURED}
    groups = {(k.block, k.key): k for k in registry.KEYS if k.kind == registry.KIND_GROUP}
    data_keys = {(k.block, k.key) for k in registry.KEYS if k.kind == registry.KIND_DATA}

    depth = 0
    seen_blocks: list[tuple[str, int]] = []          # (block, line_no)
    seen_keys: dict[str, list[str]] = {}
    seen_children: dict[tuple[str, str], list[str]] = {}
    zone_positions: dict[str, list[int]] = {z: [] for z in registry.ZONES}
    header_lines = 0
    first_zone_line: int | None = None
    current_block: str | None = None
    current_group: str | None = None
    data_value_until: int | None = None              # 多行 data 值:深度回到该值前免检
    last_nonblank: tuple[int, str] | None = None     # (line_no, stripped)

    for no, raw in enumerate(lines, 1):
        stripped = raw.strip()
        code, comment = _split_comment(raw)
        code_stripped = code.strip()
        where = f"line {no}"
        depth_before = depth

        if stripped == "":
            continue
        if data_value_until is not None:
            depth += _brace_delta(code)
            if depth <= data_value_until:
                data_value_until = None
            last_nonblank = (no, stripped)
            continue

        if stripped.startswith("//"):
            if stripped in zone_headers:
                zone_positions[zone_headers[stripped]].append(no)
                if first_zone_line is None:
                    first_zone_line = no
            elif stripped in block_headers:
                pass                                  # 归属在遇到块键行时核对
            elif first_zone_line is None and depth_before == 1:
                header_lines += 1
            else:
                out.append(Violation("R5", where, f"不允许段落注释(只许每键尾注):{stripped[:40]!r}"))
            last_nonblank = (no, stripped)
            continue

        m = _KEY_LINE_RE.match(code)
        if m and depth_before == 1:                   # 顶层块键行
            block_name = m.group(2)
            seen_blocks.append((block_name, no))
            current_block = block_name
            current_group = None
            expected = registry.render_block_header(block_name) if block_name in {b.name for b in registry.BLOCKS} else None
            prev = last_nonblank[1] if last_nonblank else ""
            if expected is None:
                out.append(Violation("R4", block_name, "野块:注册表没有这个块"))
            elif prev != expected.strip():
                out.append(Violation("R5", block_name, "块头注缺失或不等于注册表渲染(可用 --fix-headers 重生成)"))
            if comment:
                out.extend(_comment_text_violations(comment, block_name))
        elif m and depth_before == 2 and current_block is not None:
            key = m.group(2)
            rest = m.group(3)
            if current_block in structured:
                if comment:
                    out.extend(_comment_text_violations(comment, f"{current_block}.{key}"))
            else:
                seen_keys.setdefault(current_block, []).append(key)
                kw = f"{current_block}.{key}"
                if comment is None:
                    out.append(Violation("R5", kw, "叶子键缺尾注(每个参数一行「作用」)"))
                else:
                    out.extend(_comment_text_violations(comment, kw))
                    entry = registry.find(current_block, key)
                    tag = registry.host_tag(entry.hosts) if entry else ""
                    if tag and tag not in comment:
                        out.append(Violation("R3", kw, f"单侧宿主的键尾注须含「{tag}」"))
                opens = _brace_delta(code) > 0
                if (current_block, key) in groups and opens:
                    current_group = key
                    seen_children.setdefault((current_block, key), [])
                elif opens and (current_block, key) in data_keys:
                    data_value_until = depth_before
                elif opens:
                    data_value_until = depth_before   # 未登记的多行值:免检其内部,野键由 R4 报
        elif m and depth_before >= 3 and current_block is not None:
            key = m.group(2)
            if current_block in structured:
                if comment:
                    out.extend(_comment_text_violations(comment, f"{current_block}.{key}"))
            elif current_group is not None and depth_before == 3:
                seen_children[(current_block, current_group)].append(key)
                kw = f"{current_block}.{current_group}.{key}"
                if comment is None:
                    out.append(Violation("R5", kw, "参数组子键缺尾注"))
                else:
                    out.extend(_comment_text_violations(comment, kw))
            else:
                out.append(Violation("R5", f"{current_block}.{key}", "意外的嵌套键(参数组须在注册表登记为 group)"))
        elif comment and not m:
            out.extend(_comment_text_violations(comment, where))

        depth += _brace_delta(code)
        if depth <= 1:
            current_block = None
            current_group = None
        elif depth <= 2:
            current_group = None
        last_nonblank = (no, stripped)

    # ── R5:文件头行数 ──
    if header_lines > MAX_FILE_HEADER_LINES:
        out.append(Violation("R5", "file header", f"文件头注释 {header_lines} 行 > {MAX_FILE_HEADER_LINES}"))

    # ── R4:键集相等 ──
    file_blocks = [b for b, _ in seen_blocks]
    for b in registry.BLOCKS:
        if b.name not in file_blocks:
            out.append(Violation("R4", b.name, "文件缺块"))
    for name in file_blocks:
        if name not in {b.name for b in registry.BLOCKS}:
            continue                                  # 野块已报
        if name in structured:
            continue
        expected_keys = {k.key for k in registry.keys_of(name)}
        got = seen_keys.get(name, [])
        for k in sorted(expected_keys - set(got)):
            out.append(Violation("R4", f"{name}.{k}", "文件缺键(注册表有、文件无 = 暗键)"))
        for k in got:
            if k not in expected_keys:
                out.append(Violation("R4", f"{name}.{k}", "野键(文件有、注册表无)"))
        if len(got) != len(set(got)):
            out.append(Violation("R4", name, "键重复"))
    for (blk, grp), key in groups.items():
        if blk not in file_blocks or grp not in seen_keys.get(blk, []):
            continue
        got = seen_children.get((blk, grp), [])
        for c in key.children:
            if c not in got:
                out.append(Violation("R4", f"{blk}.{grp}.{c}", "参数组缺子键"))
        for c in got:
            if c not in key.children:
                out.append(Violation("R4", f"{blk}.{grp}.{c}", "参数组野子键"))

    # ── R6:顺序与分区 ──
    order = [b for b in file_blocks if b in {x.name for x in registry.BLOCKS}]
    expected_order = [b for b in registry.block_order() if b in order]
    if order != expected_order:
        out.append(Violation("R6", "block order", f"块顺序 {order} ≠ 注册表 {expected_order}"))
    line_of = {b: no for b, no in seen_blocks}
    for i, zone in enumerate(registry.ZONES):
        positions = zone_positions[zone]
        if len(positions) != 1:
            out.append(Violation("R6", zone, f"分区标题须恰出现一次(现 {len(positions)} 次)"))
            continue
        zpos = positions[0]
        zone_blocks = [b.name for b in registry.BLOCKS if b.zone == zone and b.name in line_of]
        prev_blocks = [b.name for b in registry.BLOCKS if b.zone in registry.ZONES[:i] and b.name in line_of]
        if zone_blocks and zpos > min(line_of[b] for b in zone_blocks):
            out.append(Violation("R6", zone, "分区标题必须在本分区第一个块之前"))
        if prev_blocks and zpos < max(line_of[b] for b in prev_blocks):
            out.append(Violation("R6", zone, "分区标题必须在上一分区最后一个块之后"))
    return out


def fix_headers(text: str, *, registry=reg) -> str:
    """把块头注与分区标题重生成为注册表渲染(其它行原样)。"""
    names = {b.name for b in registry.BLOCKS}
    zone_prefix = {registry.ZONE_TITLES[z].split(" · ")[0]: z for z in registry.ZONES}
    out = []
    for line in text.split("\n"):
        s = line.strip()
        m = re.match(r"^// ── (\w+) · ", s)
        if m and m.group(1) in names:
            out.append(registry.render_block_header(m.group(1)))
            continue
        if s.startswith("// ═══"):
            for prefix, zone in zone_prefix.items():
                if prefix in s:
                    line = registry.render_zone_header(zone)
                    break
        out.append(line)
    return "\n".join(out)


# ═══════════════════════════ 消费者层:R2 / R3 ═══════════════════════════


def _module_path(sym: str, repo_root: Path) -> Path:
    mod = sym.split(":", 1)[0]
    return repo_root / (mod.replace(".", "/") + ".py")


def _symbol_source(sym: str, repo_root: Path) -> str | None:
    """`pkg.module:function` → 该函数源码段;`pkg.module` → 整文件;`x.js` → 整文件。找不到 → None。"""
    if sym.endswith(".js"):
        p = repo_root / sym
        return p.read_text(encoding="utf-8") if p.is_file() else None
    p = _module_path(sym, repo_root)
    if not p.is_file():
        return None
    src = p.read_text(encoding="utf-8")
    if ":" not in sym:
        return src
    fn = sym.split(":", 1)[1]
    tree = ast.parse(src)
    cls, _, method = fn.partition(".")
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == fn:
            return ast.get_source_segment(src, node) or ""
        if method and isinstance(node, ast.ClassDef) and node.name == cls:
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and sub.name == method:
                    return ast.get_source_segment(src, sub) or ""
    return None


def _mentions(src: str, literal: str, *, js: bool, identifier_ok: bool = False) -> bool:
    if js or identifier_ok:
        return re.search(rf"\b{re.escape(literal)}\b", src) is not None
    return f'"{literal}"' in src or f"'{literal}'" in src


def lint_consumers(*, registry=reg, repo_root: Path = REPO) -> list[Violation]:
    out: list[Violation] = []
    for k in registry.KEYS:
        kw = f"{k.block}.{k.key}" if k.key else k.block
        literal = k.key or k.block
        sources: list[tuple[str, str]] = []
        any_semantics = k.kind in (reg.KIND_GROUP, reg.KIND_STRUCTURED)
        for sym in k.consumers:
            src = _symbol_source(sym, repo_root)
            if src is None:
                out.append(Violation("R2", kw, f"消费者不存在:{sym}"))
                continue
            sources.append((sym, src))
            if not any_semantics and not _mentions(src, literal, js=sym.endswith(".js")):
                out.append(Violation("R2", kw, f"消费者 {sym} 源码里没有 {literal!r} 字面量"))
        if any_semantics and sources and not any(_mentions(src, literal, js=sym.endswith(".js"))
                                                 for sym, src in sources):
            out.append(Violation("R2", kw, f"没有任何消费者提到 {literal!r}"))
        if getattr(k, "children", ()):
            for child in k.children:
                if not any(_mentions(src, child, js=sym.endswith(".js"), identifier_ok=True) for sym, src in sources):
                    out.append(Violation("R2", f"{kw}.{child}", "参数组子键在所有消费者里都没出现"))
        hosts = k.hosts
        has_js = any(s.endswith(".js") for s in k.consumers)
        has_sa = any(s.startswith("autoresearch.session_agent.") for s in k.consumers)
        if hosts == reg.HOST_JS and not has_js:
            out.append(Violation("R3", kw, "hosts=js 却没有 .js 消费者"))
        if hosts == reg.HOST_SA and not has_sa:
            out.append(Violation("R3", kw, "hosts=sa 却没有 session_agent 消费者"))
        if hosts == reg.HOST_BOTH and not (has_js and has_sa):
            out.append(Violation("R3", kw, "hosts=js+sa 须 JS 与 session_agent 各登记一个消费者"))
    return out


# ═══════════════════════════ 缺省层:R7 ═══════════════════════════

_NOT_LITERAL = object()


def _parse_literal(s: str):
    s = s.strip().rstrip(",")
    if re.fullmatch(r"-?\d+(?:\.\d+)?|-?\d[\d_]*", s):
        return float(s.replace("_", ""))
    if s in ("True", "true"):
        return True
    if s in ("False", "false"):
        return False
    if s in ("None", "null"):
        return None
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        return s[1:-1]
    return _NOT_LITERAL


def _default_literals(line: str, block: str, key: str) -> list[str]:
    pats = [rf'knob\(\s*"{re.escape(block)}"\s*,\s*"{re.escape(key)}"\s*,\s*[^,]+,\s*(?P<lit>[^,)]+)']
    if key not in _R9_GENERIC_KEYS:          # 泛用键名(source/mode/…)只认带块名的 knob() 形态,防误伤
        pats += [
            rf'\.get\(\s*"{re.escape(key)}"\s*,\s*(?P<lit>[^)]+)\)',
            rf'\.get\(\s*"{re.escape(key)}"\s*\)\s*or\s+(?P<lit>[\w."\']+)',
            rf'(?P<lit>\d+(?:\.\d+)?)\s+if\s+\w*{re.escape(key)}\w*\s+is\s+None',
            rf'{re.escape(key)}\)?\s*\?\?\s*(?P<lit>[\w."\']+)',
        ]
    return [m.group("lit") for p in pats for m in re.finditer(p, line)]


def _same(default, lit) -> bool:
    if isinstance(default, bool) or isinstance(lit, bool):
        return default is lit
    if isinstance(default, (int, float)) and isinstance(lit, float):
        return float(default) == lit
    return default == lit


def lint_defaults(*, registry=reg, repo_root: Path = REPO) -> list[Violation]:
    out: list[Violation] = []
    for k in registry.KEYS:
        if k.default is None or not k.key or isinstance(k.default, dict):
            continue
        files: list[Path] = []
        for sym in tuple(k.consumers) + tuple(getattr(k, "default_sites", ())):
            p = repo_root / sym if sym.endswith((".js", ".py")) else _module_path(sym, repo_root)
            if p.is_file() and p not in files:
                files.append(p)
        for p in files:
            for no, line in enumerate(p.read_text(encoding="utf-8").split("\n"), 1):
                if k.key not in line:
                    continue
                for raw_lit in _default_literals(line, k.block, k.key):
                    lit = _parse_literal(raw_lit)
                    if lit is _NOT_LITERAL:
                        continue
                    if not _same(k.default, lit):
                        out.append(Violation("R7", f"{k.block}.{k.key}",
                                             f"{p.relative_to(repo_root)}:{no} 缺省 {raw_lit.strip()!r} ≠ 注册表 {k.default!r}"))
    return out


# ═══════════════════════════ 常量层:R8 ═══════════════════════════


def _is_numeric_node(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant):
        return isinstance(node.value, (int, float)) and not isinstance(node.value, bool)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return _is_numeric_node(node.operand)
    return False


def _is_tunable_value(node: ast.AST) -> bool:
    if _is_numeric_node(node):
        return True
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return _CLOCK_RE.match(node.value) is not None
    if isinstance(node, ast.Call):
        name = getattr(node.func, "id", getattr(node.func, "attr", ""))
        if name in {"time", "timedelta"}:
            return True
        if name in {"frozenset", "set", "tuple", "list"} and node.args:
            return _is_tunable_value(node.args[0])
        return False
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return bool(node.elts) and all(_is_numeric_node(e) for e in node.elts)
    if isinstance(node, ast.Dict):
        return bool(node.values) and all(_is_numeric_node(v) for v in node.values)
    return False


def scan_constants(repo_root: Path, scope: tuple[str, ...]) -> dict[str, str]:
    """{位置: 值文本}。位置 = `pkg.module:NAME` / `pkg.module:func.param` / `path.js:NAME`。"""
    found: dict[str, str] = {}
    files: list[Path] = []
    for pattern in scope:
        files.extend(sorted(repo_root.glob(pattern)))
    for p in files:
        rel = p.relative_to(repo_root).as_posix()
        if "/tests/" in f"/{rel}" or p.name == "__init__.py":
            continue
        text = p.read_text(encoding="utf-8")
        if p.suffix == ".js":
            for line in text.split("\n"):
                m = _JS_CONST_RE.match(line)
                if m:
                    found[f"{rel}:{m.group(1)}"] = m.group(2)
            continue
        module = rel[:-3].replace("/", ".")
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in tree.body:
            targets: list[ast.expr] = []
            value = None
            if isinstance(node, ast.Assign):
                targets, value = node.targets, node.value
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                targets, value = [node.target], node.value
            if value is not None:
                for t in targets:
                    if (isinstance(t, ast.Name) and _UPPER_RE.match(t.id)
                            and not _NON_TUNABLE_NAME_RE.search(t.id) and _is_tunable_value(value)):
                        found[f"{module}:{t.id}"] = ast.get_source_segment(text, value) or ""
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
                args = node.args
                pos = args.posonlyargs + args.args
                for arg, default in zip(pos[len(pos) - len(args.defaults):], args.defaults, strict=True):
                    if _is_numeric_node(default) and ast.literal_eval(default) not in _SKIP_PARAM_DEFAULTS:
                        found[f"{module}:{node.name}.{arg.arg}"] = ast.get_source_segment(text, default) or ""
                for arg, default in zip(args.kwonlyargs, args.kw_defaults, strict=True):
                    if default is not None and _is_numeric_node(default) \
                            and ast.literal_eval(default) not in _SKIP_PARAM_DEFAULTS:
                        found[f"{module}:{node.name}.{arg.arg}"] = ast.get_source_segment(text, default) or ""
    return found


def lint_constants(*, registry=reg, repo_root: Path = REPO) -> list[Violation]:
    found = scan_constants(repo_root, tuple(registry.R8_SCOPE))
    allow = {loc: reason for loc, reason in registry.CODE_CONSTANTS}
    out: list[Violation] = []
    for loc, value in found.items():
        if loc not in allow:
            out.append(Violation("R8", loc, f"未登记的可调常量 = {value}:进 scan_config,或登记到 CODE_CONSTANTS 并写理由"))
    for loc in allow:
        if loc not in found:
            out.append(Violation("R8", loc, "allowlist 条目已不存在于代码(删掉这一行)"))
    return out


# ═══════════════════════════ 文档层:R9 ═══════════════════════════


def lint_docs(*, registry=reg, repo_root: Path = REPO) -> list[Violation]:
    names = sorted({k.key for k in registry.KEYS if k.key and len(k.key) >= 5 and k.key not in _R9_GENERIC_KEYS}
                   | {c for k in registry.KEYS for c in getattr(k, "children", ())
                      if len(c) >= 5 and c not in _R9_GENERIC_KEYS})
    pat = re.compile(r"(?<![\w.])(" + "|".join(re.escape(n) for n in names) + r")`?[\s=:：]{1,3}[-+]?\d")
    out: list[Violation] = []
    for rel in registry.DOC_FILES:
        p = repo_root / rel
        if not p.is_file():
            continue
        for no, line in enumerate(p.read_text(encoding="utf-8").split("\n"), 1):
            m = pat.search(line)
            if m:
                out.append(Violation("R9", f"{rel}:{no}", f"文档复述了键值「{m.group(0).strip()}」:值只住 scan_config.jsonc"))
    return out


# ═══════════════════════════ 总入口 / CLI ═══════════════════════════


def lint_all(path: Path | str = REPO / reg.CONFIG_PATH, *, repo_root: Path = REPO, registry=reg,
             rules: tuple[str, ...] | None = None) -> list[Violation]:
    wanted = set(rules or RULES)
    out: list[Violation] = []
    text = Path(path).read_text(encoding="utf-8")
    if wanted & {"R3", "R4", "R5", "R6"}:
        out.extend(lint_text(text, registry=registry))
    if wanted & {"R2", "R3"}:
        out.extend(lint_consumers(registry=registry, repo_root=repo_root))
    if "R7" in wanted:
        out.extend(lint_defaults(registry=registry, repo_root=repo_root))
    if "R8" in wanted:
        out.extend(lint_constants(registry=registry, repo_root=repo_root))
    if "R9" in wanted:
        out.extend(lint_docs(registry=registry, repo_root=repo_root))
    if wanted & {"R10", "R11", "R12", "R13", "R14"}:
        # token 防膨胀(2026-10-10):agent 文件预算 / 静态成本清单 / 档位锁 / 预算线只降不升。
        from autoresearch.scan import token_rules

        out.extend(Violation(rule, where, message)
                   for rule, where, message in token_rules.lint(Path(path), repo_root=repo_root, wanted=wanted))
    return [v for v in out if v.rule in wanted]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="scan_config 标准 lint(九条规则;标准原文见 SKILL.md「配置」节)")
    ap.add_argument("--path", default=str(REPO / reg.CONFIG_PATH))
    ap.add_argument("--rules", default=None, help="逗号分隔,如 R4,R5;缺省全部")
    ap.add_argument("--fix-headers", action="store_true", help="按注册表重生成块头注与分区标题后再 lint")
    ap.add_argument("--dump-constants", action="store_true", help="只列出 R8 扫描到的常量(供登记 allowlist)")
    ap.add_argument("--hook", action="store_true", help="PostToolUse hook 模式:输出加提示行")
    args = ap.parse_args(argv)
    if args.dump_constants:
        for loc, value in scan_constants(REPO, tuple(reg.R8_SCOPE)).items():
            print(f"{loc} = {value}")
        return 0
    path = Path(args.path)
    if args.fix_headers:
        path.write_text(fix_headers(path.read_text(encoding="utf-8")), encoding="utf-8")
    rules = tuple(r.strip() for r in args.rules.split(",")) if args.rules else None
    violations = lint_all(path, rules=rules)
    for v in violations:
        print(str(v))
    print(f"scan_config 标准 lint:{len(violations)} 条违规")
    if args.hook:
        try:
            from autoresearch.scan import token_bom

            print(token_bom.summary_line())
        except Exception as exc:  # noqa: BLE001 - the summary is a courtesy line, never a gate
            print(f"token BOM(估):不可用 {type(exc).__name__}: {exc}")
    if violations and args.hook:
        print("→ 改到零违规为止;规则原文见 .claude/skills/scan-market/SKILL.md「配置」节,键的事实源是 autoresearch/contracts/scan_config.py")
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
