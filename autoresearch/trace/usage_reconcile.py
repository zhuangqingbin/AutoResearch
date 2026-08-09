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

   **Wave12-T34 例外**:`ens_review`(复用 `l4-card`)与 `l3_repair`(复用 `l3-rank`)
   两个复用父 agentType 的 role **不再**走这条兜底。它们的派发次数由 `dispatch_census()`
   从产物普查出来(`_ensemble_<code>.json` 的 `role`/`n_dispatch`、`_l3_repair_prompt.md`
   在场),次级 role 认领自己的行、主 role 吸收其余 → mismatch **逐 role 分行报**。
   此前它们被一律按父 role 的期望判,能过关纯属巧合(生产里两者 effort 恰好同为 `max`),
   一旦分开调参就会既产生假阳(冤枉 l4_card)又产生假阴(掩盖 ens_review 自己的偏差)。
   只有普查也拿不到时,才退回并集断言(`role=None`)。
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
# 该 agentType 底下可能的 role **有序**元组:**第 0 位是主 role**(吸收剩余行),
# 其后是复用同一 agentType 派发的次级 role。`general-purpose` 故意不在这张表里——
# 它没有 role label,走下面「壳类集合断言」分支(见模块 docstring 边界 2)。
#
# Wave12-T34(2026-08-09):此前本表是「一个 agentType → 一个 role」,`ens_review`
# (l4-card 双复核 run2/3,`.claude/workflows/l4-stock.js` `rerun()`)与 `l3_repair`
# (L3 lint-fix 补跑,`.claude/workflows/scan-market.js` `L3-lint-fix`)复用父 agentType
# 派发,harvest 的 `attributionAgent` 只记 agentType,于是这两类行**一律被按父 role 的
# 期望值判**。它此前没显影纯属巧合:生产配置里 `ens_review.effort` 与 `l4_card.effort`
# 当时碰巧同为 `max` —— 靠「两者配置值恰好相同」蒙混。一旦分开调参,本表会把 ens_review
# 的合规行误判成 l4_card mismatch(假阳),或让 ens_review 自己的真实偏差被 l4_card 的
# 期望值掩盖(假阴)。
#
# 修法**不是**照抄 `gp_shell` 的并集豁免(那会把精确定位一起丢掉,ens_review 跑偏时
# 只要落在并集里就永远查不出来),而是引入**派发人口普查**(`dispatch_census`):
# 次级 role 派了几次由**产物**说了算,主 role 吸收其余行 → 每一行都能落到具体 role 上,
# mismatch 因此**逐 role 分行报**(带 `role` 字段)。拿不到 census 时才退回集合断言兜底。
AGENTTYPE_ROLES: dict[str, tuple[str, ...]] = {
    "l3-rank": ("l3_rank", "l3_repair"),
    "l4-card": ("l4_card", "ens_review"),
    "l4-intel": ("l4_intel",),
    "macro-brief": ("strategist",),
    "sector-brief": ("sector_brief",),
    "dossier-init": ("dossier_init",),
}

#: 兼容既有导入方(只给主 role)。新代码请用 `AGENTTYPE_ROLES`。
AGENTTYPE_TO_ROLE = {atype: roles[0] for atype, roles in AGENTTYPE_ROLES.items()}

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


#: 次级 role 的派发次数从哪些产物读出来(Wave12-T34)。**只数次级 role**——主 role
#: 吸收剩余行,所以不需要(也不该)去数它:数主 role 会引入第二个可能错的计数。
_SECONDARY_ROLES = ("ens_review", "l3_repair")


def dispatch_census(scan_dir: Path | str) -> dict[str, int]:
    """逐**次级** role 的实际派发次数 —— 产物说了算,不靠 agent 自报。

    两个来源,都**只读既有产物、零新增派发**:

    - `ens_review`:`_ensemble_<code>.json`。优先读显式子记录 `role`/`n_dispatch`
      (Wave12-T34 起 `l4-stock.js` 的 ens-dump 一并写进去);老产物无这两个键 →
      由 `n_runs - 1` 推断(主卡 1 次 + 复核 n-1 次,`rerun()` 的定义)。
    - `l3_repair`:`_l3_repair_prompt.md` 在场即 1 次。**故意用产物推断而不是让 agent
      自己写标记**:2026-07-27 实跑过 L3 自修 agent 死于 `Connection closed mid-response`
      ——它烧掉了 56.9k 加权却没留下任何自报记录。让"它自己承认跑过"当唯一事实源,
      恰好会在它死掉时丢掉那一行的归属,而那正是最需要看清成本的时刻。
      prompt 由 `repair-pack` 在派发**之前**写下,agent 死不死它都在。

    presence-gated:目录不存在 / 产物坏 → 该源计 0,**绝不抛异常**(本模块是报表,不毙人)。
    """
    scan = Path(scan_dir)
    out: dict[str, int] = {}
    for path in sorted(scan.glob("_ensemble*.json")):
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError):
            continue
        if not isinstance(rec, dict):
            continue
        if rec.get("role") == "ens_review" and rec.get("n_dispatch") is not None:
            try:
                n = max(int(rec["n_dispatch"]), 0)
            except (TypeError, ValueError):
                n = 0
        else:
            try:
                n = max(int(rec.get("n_runs") or 1) - 1, 0)
            except (TypeError, ValueError):
                n = 0
        if n:
            out["ens_review"] = out.get("ens_review", 0) + n
    if (scan / "_l3_repair_prompt.md").exists():
        out["l3_repair"] = out.get("l3_repair", 0) + 1
    return out


def _spec_of(atype: str, role: str, agents_cfg: dict) -> dict:
    """某 role 的期望 spec = agent def frontmatter 垫底 + config override 覆盖。"""
    return {**_frontmatter(atype), **(agents_cfg.get(role) or {})}


def _fits(exp: dict, got: tuple[str, str]) -> bool:
    """实测 `(model, effort)` 是否**逐字段**满足期望(期望里没有的字段不判)。"""
    return all(exp[f] == g for f, g in (("model", got[0]), ("effort", got[1])) if f in exp)


def _judge_multi_role(atype: str, roles: tuple[str, ...], got_rows: list[tuple[str, str]],
                      agents_cfg: dict, census: dict[str, int]) -> list[dict] | None:
    """同 agentType 多 role 的归行判定 —— 每一行落到具体 role 上,mismatch **分行报**。

    返回 mismatch 列表;`None` = 拿不到 census(次级 role 一次都没派 → 调用方按单 role
    老路走;这不是兜底失败,是"今天确实只有主 role 在跑")。

    算法(贪心两趟,小 N 下够用且可读):
      ① 先让每个次级 role 从实测行里**认领它认得出的行**(逐字段满足其 spec 的),
         认领上限 = census 说它派了几次;
      ② 还欠着的次级 role 名额,从剩余行里按序取一行来判 —— 判不过就记它自己的
         mismatch(**这一行归它,不再冤枉主 role**);
      ③ 剩下的行全部归主 role,按主 role 的 spec 判。

    为什么①要先"认领得上的":配置分开调参后,ens_review 的合规行长得和 l4_card 的
    不一样,先把长得对的挑走,剩下的才是真跑偏的 —— 反过来(先按顺序切前 k 行给
    ens_review)会随机制造假阳。
    """
    secondary = [r for r in roles[1:] if int(census.get(r, 0)) > 0]
    if not secondary:
        return None
    primary = roles[0]
    specs = {r: _spec_of(atype, r, agents_cfg) for r in roles}
    need = {r: int(census.get(r, 0)) for r in secondary}

    remaining = list(got_rows)
    for role in secondary:                       # ① 认领长得对的
        keep: list[tuple[str, str]] = []
        for got in remaining:
            if need[role] and _fits(specs[role], got):
                need[role] -= 1
            else:
                keep.append(got)
        remaining = keep

    mismatches: list[dict] = []
    for role in secondary:                       # ② 欠着的名额从剩余行里认，判不过记它自己
        while need[role] and remaining:
            got = remaining.pop(0)
            exp = specs[role]
            for field, got_val in (("model", got[0]), ("effort", got[1])):
                if field in exp and exp[field] != got_val:
                    mismatches.append({"agent": atype, "role": role, "field": field,
                                       "expected": exp[field], "actual": got_val})
            need[role] -= 1

    for got in remaining:                        # ③ 剩下的归主 role
        exp = specs[primary]
        for field, got_val in (("model", got[0]), ("effort", got[1])):
            if field in exp and exp[field] != got_val:
                mismatches.append({"agent": atype, "role": primary, "field": field,
                                   "expected": exp[field], "actual": got_val})

    # census 说派了、实测行却不够 → 不当没发生:少的那几次要么没被 harvest 到,
    # 要么根本没派成。两种都是"这份对账不完整",按 wire 语义单列一条。
    for role in secondary:
        if need[role]:
            mismatches.append({"agent": atype, "role": role, "field": "dispatch_count",
                               "expected": f"census {census.get(role)} 次",
                               "actual": f"实测行少 {need[role]} 次"})
    return mismatches


def _reconcile_core(echo: dict, rows: list[dict], *, date: str,
                    census: dict[str, int] | None = None) -> dict:
    """纯函数核心:输入已经是内存里的 `echo`/`rows` dict,不碰文件系统。

    `reconcile()` 是它的文件 I/O 外壳(读两份产物后转手调用这里)。拆出这一层是为了让
    「拿真实 harvest 数据、只手动改一处字段」这种变异验证不需要每次都先落临时文件——
    这正是本模块验收(mutation testing)最常做的操作,值得有一个不用碰磁盘的入口。

    返回 `{date, ok, mismatches:[{agent,role,field,expected,actual}], wire_breaks:[role],
    unknown_agent_types:[agentType], checked}`。

    `census`(Wave12-T34)= 次级 role 的实际派发次数(`dispatch_census()` 的产物);
    给了它,同 agentType 的多 role 才分得开、mismatch 才带得上 `role` 字段。**不给**
    (或次级 role 今天一次没派)时:两 role 的期望 spec 若不同,退回**并集集合断言**
    (仿 `gp_shell`,`role=None`)——分不清就不装分得清,但也绝不再拿主 role 的期望去
    冤枉次级 role 的行(那是 Wave12-T34 之前的假阳/假阴根因)。

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
    census = dict(census or {})

    gp_allowed: set[tuple[str, str]] = set()
    for shell in ("gp_shell", "gp_shell_json"):
        spec = {**_GP_SHELL_DEFAULT, **(agents_cfg.get(shell) or {})}
        gp_allowed.add((spec["model"], spec["effort"]))

    mismatches: list[dict] = []
    seen_types: set[str] = set()
    unknown_types: set[str] = set()
    by_type: dict[str, list[tuple[str, str]]] = {}
    checked = 0
    for r in rows:
        if r.get("role") != "subagent":
            continue
        checked += 1
        atype = r.get("agent") or ""
        seen_types.add(atype)
        got = (_norm_model(r.get("model")), r.get("effort") or "(unset)")
        if atype in AGENTTYPE_ROLES:
            by_type.setdefault(atype, []).append(got)   # 同 agentType 攒齐再判(多 role 需要全局视野)
        elif atype == "general-purpose":
            if got not in gp_allowed:
                mismatches.append({"agent": atype, "role": None, "field": "model+effort",
                                   "expected": sorted(gp_allowed), "actual": list(got)})
        else:
            unknown_types.add(atype)          # 既不在映射表也不是 general-purpose——不装懂

    for atype, got_rows in by_type.items():
        roles = AGENTTYPE_ROLES[atype]
        judged = (_judge_multi_role(atype, roles, got_rows, agents_cfg, census)
                  if len(roles) > 1 else None)
        if judged is not None:
            mismatches.extend(judged)
            continue
        # 兜底只在次级 role **确实被配置了**时才启用。没配 = 没人在管这个档位,它的
        # "期望"不过是父 agent def 的 frontmatter,拿它去撑并集会把父 role 本来逮得住的
        # mismatch 一并放过(实测:那会让 test_effort_mutation_caught 这类探针集体失明)。
        live = [role for role in roles if role == roles[0] or role in agents_cfg]
        specs = {role: _spec_of(atype, role, agents_cfg) for role in live}
        distinct = {tuple(sorted(spec.items())) for spec in specs.values()}
        if len(live) > 1 and len(distinct) > 1:
            # 兜底(census 缺,但两 role 期望确实不同):只断言实测 ∈ 并集,**不指认**是哪个
            # role —— 分不清就不装分得清(同 gp_shell 边界 2)。想要精确定位,把 census 补上。
            for got in got_rows:
                if not any(_fits(specs[role], got) for role in live):
                    mismatches.append({
                        "agent": atype, "role": None, "field": "model+effort",
                        "expected": sorted(f"{role}:{specs[role]}" for role in live),
                        "actual": list(got)})
            continue
        role = roles[0]                     # 单 role,或多 role 但期望完全一致 → 逐行直判
        exp = specs[role]
        for got in got_rows:
            for field, got_val in (("model", got[0]), ("effort", got[1])):
                if field in exp and exp[field] != got_val:
                    mismatches.append({"agent": atype, "role": role, "field": field,
                                       "expected": exp[field], "actual": got_val})

    wire_breaks = [role for role in _EXPECT_PRESENT if role in agents_cfg
                   and not any(role in AGENTTYPE_ROLES.get(t, ()) for t in seen_types)]
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
    # census 是 presence-gated 的**增益**:有它 ens_review/l3_repair 才分得开;
    # 没它(老 run 目录)照常出表,只是多 role 那几个 agentType 退回集合断言。
    return _reconcile_core(echo, rows, date=str(date), census=dispatch_census(scan))


def render(result: dict) -> str:
    """→ markdown(报表头带三条精度限制声明 + wire_break/mismatch 明细表)。"""
    lines = [f"# usage_reconcile — {result['date']}", ""]
    lines += [
        "> **读表前三条边界(勿脑补掉,写在这里是为了不用每次口头重复)**:",
        "> 1. **时序**:本文件由 CP7 跑完 `usage_harvest` 之后单独生成,不在当日 GATE4 内——"
        "`self_review` 的对应 check 读到的是**最近一份既有**结果(通常是上一次 run),"
        "不是「当日」结论。",
        "> 2. **壳类只做集合断言**:`general-purpose` 不带 role label,分不清是 `gp_shell` "
        "还是 `gp_shell_json`——下表这类行只断言实测 ∈ 两者期望的并集,不指认具体是哪一个。"
        "复用父 agentType 的 `ens_review`/`l3_repair` 自 Wave12-T34 起**不再**走这条兜底:"
        "派发次数由产物普查(`dispatch_census`)给出,mismatch 逐 role 分行报;只有普查拿不到"
        "时才退回并集断言(那时 role 列显示 `—`)。",
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
        lines += ["| agent | role | field | expected | actual |", "|---|---|---|---|---|"]
        for m in result["mismatches"]:
            lines.append(f"| {m['agent']} | {m.get('role') or '—'} | {m['field']} "
                         f"| {m['expected']} | {m['actual']} |")
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
