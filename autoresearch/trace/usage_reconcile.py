#!/usr/bin/env python3
"""trace.usage_reconcile —— 配置期望 × usage_harvest 实测逐 role 对账。Wave11 B4。

design: docs/specs/2026-08-05-wave11-ruler-config-l4concurrency-skills-design.md §B4

**背景**:Wave11 B1/B2 把 scan-market 的 agent model/effort 收进 `scan_config.jsonc` 的
`agents` 闭集(`autoresearch.scan.user_config._AGENT_ROLES`)+ workflow 统一 `AGENT_DEFAULTS`
取值——但"配置写了就一定生效吗"此前**没有任何断言**。`autoresearch.trace.usage_harvest`
已经从 subagent transcript 里把每个 agent 真实用的 model/effort 实测出来(`_token_usage.json`
的逐行 `agent`/`model`/`effort`)。本模块把「期望(当日 `user_config_echo.json`)」×
「实测(当日 `_token_usage.json`)」逐 role 对上,让"配置生效"从口头变成可断言产物。

**必须如实的三条边界(报表头 `render()` 同步写,勿在别处美化/隐藏掉)**:

1. **时序**:GATE4/`self_review` 跑在 `usage_harvest`(进而本模块)**之前**——本模块要读
   的 `_token_usage.json` 在 GATE4 那一刻还没生成。**当日对账结论进不了当日 GATE4**;
   `self_review.usage_reconcile_lint` 的新 check 只能读**最近一份既有** `_usage_reconcile.json`
   (通常 = 上一次 run 的结论),当日结论由 SKILL.md CP7 第五条命令跑完直接打给人看,
   不经 self_review 转手、不假装能当日闭环。
2. **壳类只做集合断言**:`general-purpose` 这个 agentType 不带 role label——harvest 的
   `attributionAgent` 只记到 Agent 工具的 `subagent_type`,分不清某一行到底是 `gp_shell`
   还是 `gp_shell_json` 派的。本模块把两者期望 spec 的**并集**当合法集合,实测
   `(model, effort)` ∈ 并集即算过;**分不清具体是哪一个,不装作分得清**。
3. **effort 是请求参数,不是推理深度**:harvest 记录的是「发出的请求」携带的 model/effort
   参数——它证明「配置到达了调用点」,**不证明模型真的按这个深度推理**。这已经是
   本对账能做到的最深层。

CLI:
  uv run --no-sync python -m autoresearch.trace.usage_reconcile <date> [--json-out PATH]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

# agentType(usage_harvest 的 `row["agent"]`,即派发时 Agent 工具的 `subagent_type`)→
# scan_config.jsonc `agents.<role>` 闭集里的 role 名(见 `autoresearch.scan.user_config
# ._AGENT_ROLES`)。`general-purpose` 故意不在这张表里——它没有 role label,走下面
# 「壳类集合断言」分支(见模块 docstring 边界 2)。
#
# ⚠️ 已知局限(2026-08-06 核实,未在本轮修——见 task-9-report.md「已知局限」节):
# `ens_review`(l4-card 双复核 run2/3)与 `l3_repair`(L3 lint-fix 补跑)两个 role **复用**
# 父 role 同一个 agentType 派发(`.claude/workflows/l4-stock.js` 253 行 `rerun()`、
# `.claude/workflows/scan-market.js` 293 行 `L3-lint-fix` 均写 `agentType: 'l4-card'`/
# `'l3-rank'`,与主调用同名)——harvest 的 `attributionAgent` 只记 agentType,记不到
# 是主调用还是复核/补跑,与 `general-purpose` 分不清 `gp_shell`/`gp_shell_json` 是同一类
# 结构性盲。本表仍按「一个 agentType → 一个 role」处理(只映到父 role),**没有**对
# ens_review/l3_repair 做类似 `gp_shell` 的并集豁免——现状能蒙混过关纯属巧合:生产配置
# 里 `ens_review.effort` 与 `l4_card.effort` 当前碰巧同为 `max`(2026-08 起)。一旦两者
# 效果值被分开调参,本表会对着 ens_review 的行误判成"l4_card mismatch"(假阳)或反过来
# 让 ens_review 自己的真实偏差被 l4_card 的期望值掩盖(假阴)。修法应比照 `gp_shell` 的
# 并集断言,但改动会影响 mismatch 的精确定位语义,留作独立后续 task,不在本轮展开。
AGENTTYPE_TO_ROLE = {
    "l3-rank": "l3_rank",
    "l4-card": "l4_card",
    "l4-intel": "l4_intel",
    "macro-brief": "strategist",
    "sector-brief": "sector_brief",
    "dossier-init": "dossier_init",
}

# 全扫描日「应在场」的 role——t1_diag/t1_synth 属 t1-review 快环、dossier_init 属首覆
# workflow,均非每个 scan-market 全扫日必跑;放进来会对着正常运作的当日报假 wire_break。
_EXPECT_PRESENT = ("l3_rank", "l4_card", "l4_intel", "strategist", "sector_brief")

# gp_shell / gp_shell_json 未在 scan_config.jsonc 显式配置时的缺省 spec——与 workflow 侧
# AGENT_DEFAULTS 的意图一致(B2);本模块不解析 workflow JS,直接内联同一对缺省值。
_GP_SHELL_DEFAULT = {"model": "sonnet", "effort": "low"}

_AGENTS_DIR = Path(".claude/agents")
LEDGER_PATH = Path("context/learning/usage_reconcile.jsonl")


def _frontmatter(agent_type: str) -> dict:
    """读 `.claude/agents/<agent_type>.md` frontmatter 的 `model:`/`effort:`。

    运行时读,**不做镜像表**(防漂移——agent def 改了 effort,没人记得同步改第二处)。
    缺文件/无 frontmatter → `{}`(下游按"该字段无 frontmatter 缺省"处理,只剩 echo
    override 可比;`general-purpose` 走壳类分支,从不会调用到这里)。
    """
    p = _AGENTS_DIR / f"{agent_type}.md"
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return {}
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    out: dict = {}
    for line in text[3:end].splitlines():
        line = line.strip()
        for key in ("model", "effort"):
            prefix = f"{key}:"
            if line.startswith(prefix):
                out[key] = line[len(prefix):].strip()
    return out


def _norm_model(raw: str | None) -> str:
    """完整 model id(如 `claude-opus-5`)→ 家族名(haiku/sonnet/opus);认不出 → 原样。

    子串匹配,故意不复用 `usage_harvest.model_family`——那边的家族集合含 fable/mythos,
    本模块只关心 config 三家族,没必要拖那份映射进来。
    """
    m = (raw or "").lower()
    for fam in ("haiku", "sonnet", "opus"):
        if fam in m:
            return fam
    return raw or "?"


def _reconcile_core(echo: dict, rows: list[dict], *, date: str) -> dict:
    """纯函数核心:输入已经是内存里的 `echo`/`rows` dict,不碰文件系统。

    `reconcile()` 是它的文件 I/O 外壳(读两份产物后转手调用这里)。拆出这一层是为了让
    「拿真实 harvest 数据、只手动改一处字段」这种变异验证不需要每次都先落临时文件——
    这正是本模块验收(mutation testing)最常做的操作,值得有一个不用碰磁盘的入口。

    返回 `{date, ok, mismatches:[{agent,field,expected,actual}], wire_breaks:[role],
    unknown_agent_types:[agentType], checked}`。

    - `mismatches` 逐**实测行**记,不按 role 去重合并——同一 role 多行各自独立判(2026-08-05
      真实数据里 l3-rank 一次 `max` 一次 `medium`,就是两行各自的判断;合并会盖掉"这次跑
      到底哪几次跑偏"的信息)。
    - `wire_breaks` 逐 **role** 记:config 里配了该 role,但当日实测行里一次没见过对应
      agentType——像是"配置写了没人接"。
    - `unknown_agent_types` 逐**去重后的 agentType** 记(2026-08-06 review Minor 2):
      harvest 里出现的 agentType 若既不在 `AGENTTYPE_TO_ROLE`、也不是 `general-purpose`,
      此前会静默跳过、不判也不报——与 `wire_breaks`"配置写了没人接"的方向不对称(一边
      主动报断线,一边默默把看不懂的行扔掉)。现在这类行会被记进本字段并计入 `ok`——
      分不清、判不了本身就是一种"这份对账不完整"的信号,不该被 0 mismatches 悄悄冲平。
    - `checked` 只计 `role == "subagent"` 的行数(2026-08-06 review Minor 1 修正):此前
      用 `len(rows)` 会把 `role == "main"`(主会话自身)的那一行也算进去,但比对循环一开
      头就跳过了它——"实测行 N 条"这句话此前会让人以为 N 行都真的参与了对账,其实最多
      N-1 行。
    """
    agents_cfg = echo.get("agents") or {}

    gp_allowed: set[tuple[str, str]] = set()
    for shell in ("gp_shell", "gp_shell_json"):
        spec = {**_GP_SHELL_DEFAULT, **(agents_cfg.get(shell) or {})}
        gp_allowed.add((spec["model"], spec["effort"]))

    mismatches: list[dict] = []
    seen_types: set[str] = set()
    unknown_types: set[str] = set()
    checked = 0
    for r in rows:
        if r.get("role") != "subagent":
            continue
        checked += 1
        atype = r.get("agent") or ""
        seen_types.add(atype)
        got = (_norm_model(r.get("model")), r.get("effort") or "(unset)")
        if atype in AGENTTYPE_TO_ROLE:
            role = AGENTTYPE_TO_ROLE[atype]
            exp = {**_frontmatter(atype), **(agents_cfg.get(role) or {})}
            for field, got_val in (("model", got[0]), ("effort", got[1])):
                if field in exp and exp[field] != got_val:
                    mismatches.append({"agent": atype, "field": field,
                                       "expected": exp[field], "actual": got_val})
        elif atype == "general-purpose":
            if got not in gp_allowed:
                mismatches.append({"agent": atype, "field": "model+effort",
                                   "expected": sorted(gp_allowed), "actual": list(got)})
        else:
            unknown_types.add(atype)          # 既不在映射表也不是 general-purpose——不装懂

    wire_breaks = [role for role in _EXPECT_PRESENT if role in agents_cfg
                   and not any(AGENTTYPE_TO_ROLE.get(t) == role for t in seen_types)]
    unknown_agent_types = sorted(unknown_types)
    ok = not mismatches and not wire_breaks and not unknown_agent_types
    return {"date": str(date), "ok": ok, "mismatches": mismatches, "wire_breaks": wire_breaks,
            "unknown_agent_types": unknown_agent_types, "checked": checked}


def reconcile(date: str, root: str | Path | None = None) -> dict:
    """`context/scan/<date>/` 下 `user_config_echo.json` × `_token_usage.json` 逐行对账。

    薄 I/O 外壳,核心逻辑见 `_reconcile_core`。

    presence-gated **仅对"文件缺失"生效**(两份产物任一缺失 → `FileNotFoundError`
    原样抛出,不吞):CLI 层(`main`)负责兜底"exit 恒 0"(见模块 CLI 段);`reconcile`
    本身保持 fail-fast,好让测试与未来的直接调用方(如 `self_review` 的新 check)能看见
    真实缺失,而不是被静默吞成一个查不出因由的空结果。
    """
    base = Path(root) if root is not None else Path(".")
    scan = base / "context" / "scan" / str(date)
    echo = json.loads((scan / "user_config_echo.json").read_text(encoding="utf-8"))
    rows = json.loads((scan / "_token_usage.json").read_text(encoding="utf-8")).get("rows") or []
    return _reconcile_core(echo, rows, date=str(date))


def render(result: dict) -> str:
    """→ markdown(报表头带三条精度限制声明 + wire_break/mismatch 明细表)。"""
    lines = [f"# usage_reconcile — {result['date']}", ""]
    lines += [
        "> **读表前三条边界(勿脑补掉,写在这里是为了不用每次口头重复)**:",
        "> 1. **时序**:本文件由 CP7 跑完 `usage_harvest` 之后单独生成,不在当日 GATE4 内——"
        "`self_review` 的对应 check 读到的是**最近一份既有**结果(通常是上一次 run),"
        "不是「当日」结论。",
        "> 2. **壳类只做集合断言**:`general-purpose` 不带 role label,分不清是 `gp_shell` "
        "还是 `gp_shell_json`——下表这类行只断言实测 ∈ 两者期望的并集,不指认具体是哪一个。",
        "> 3. **effort 是请求参数**:harvest 证明「配置到达了调用点」(发出的请求带什么参数),"
        "不证明模型真的按这个深度推理——这是本对账能做到的最深层。",
        "",
        f"- **结论**:{'✓ ok' if result['ok'] else '✗ 有差异'}"
        f" · 实测行 {result['checked']} 条"
        f" · mismatch {len(result['mismatches'])} 条"
        f" · wire_break {len(result['wire_breaks'])} 个"
        f" · unknown_agent_type {len(result.get('unknown_agent_types') or [])} 个",
        "",
    ]
    if result["wire_breaks"]:
        lines.append("**wire_breaks**(config 写了该 role,当日实测行一次没见过——像是没接线):")
        lines += [f"- `{role}`" for role in result["wire_breaks"]]
        lines.append("")
    if result.get("unknown_agent_types"):
        lines.append("**unknown_agent_types**(harvest 出现但本表不认识的 agentType——"
                     "既不是 general-purpose、也不在 `AGENTTYPE_TO_ROLE` 映射里,判不了,"
                     "不能算 0 mismatches 就当没事):")
        lines += [f"- `{t}`" for t in result["unknown_agent_types"]]
        lines.append("")
    if result["mismatches"]:
        lines += ["| agent | field | expected | actual |", "|---|---|---|---|"]
        for m in result["mismatches"]:
            lines.append(f"| {m['agent']} | {m['field']} | {m['expected']} | {m['actual']} |")
        lines.append("")
    else:
        lines.append("_无 mismatch。_")
    return "\n".join(lines) + "\n"


def append_ledger(result: dict, path: str | Path = LEDGER_PATH) -> None:
    """追加一行同形 JSON 到 streak 账本(`self_review.usage_reconcile_lint` 的
    "连续两日 ok=false 升 fail" 判据读这份;每行一个独立 `reconcile()` 结果,append-only)。
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(result, ensure_ascii=False) + "\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="usage_reconcile:配置期望(user_config_echo)× usage_harvest 实测逐 role 对账")
    ap.add_argument("date", help="扫描日,YYYY-MM-DD(读 <root>/context/scan/<date>/ 下两份既有产物)")
    ap.add_argument("--root", default=None,
                    help="仓库根目录(默认 cwd;测试用于指向隔离的 tmp_path,生产不传)")
    ap.add_argument("--json-out", default=None,
                    help="落盘路径(缺省不落 _usage_reconcile.json,只打印 md 表)")
    ap.add_argument("--ledger", default=None,
                    help="streak 账本路径(缺省 = <root>/context/learning/usage_reconcile.jsonl)")
    ap.add_argument("--no-ledger", action="store_true", help="跳过账本追加(重跑/测试用)")
    a = ap.parse_args(argv)

    try:
        result = reconcile(a.date, root=a.root)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        # 报表不毙人:CP7 用 `&&` 串联 usage_harvest → 本命令 → post_run,本命令非零退出
        # 会连累 post_run 不跑。产物缺失/损坏本身就是一条能看的诊断,不该演变成管线中断。
        print(f"[usage_reconcile] 缺产物或产物损坏,跳过本次对账:{e}")
        return 0

    if a.json_out:
        jp = Path(a.json_out)
        jp.parent.mkdir(parents=True, exist_ok=True)
        jp.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"[usage_reconcile] JSON → {jp}")
    if not a.no_ledger:
        root_base = Path(a.root) if a.root else Path(".")
        ledger_path = Path(a.ledger) if a.ledger else root_base / LEDGER_PATH
        append_ledger(result, path=ledger_path)
        print(f"[usage_reconcile] 账本 → {ledger_path}")

    print(render(result))
    return 0                              # exit 恒 0 —— 报表不毙人,即便 ok=false


if __name__ == "__main__":
    raise SystemExit(main())
