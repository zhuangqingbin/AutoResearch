#!/usr/bin/env python3
"""阶段与模式的**唯一词汇表**(声明,零业务逻辑、零 IO)。

design: `docs/specs/2026-08-29-full-coverage-research-system-brainstorm.md` §2.4 A1。

## 病灶

仓里此前有**五份**「这趟该有什么」的登记,互不派生:

1. `scan/prelude.STEP_NAMES`(12 步)
2. `scan/artifacts.CRITICAL_ARTIFACTS`(21 项)
3. `scan/run_profile.SCAN_STAGES` / `ROLE_STAGES` / `_BASE_RULES` → 被
   `trace/completeness.build_expected` 展开成**第三套**词汇
4. `scan/health._ARTIFACTS` / `scan/brief` 白名单 / `scan/publisher` 两张 trace 映射表 /
   `trace/replay.default_stage_specs`(**第四套**阶段词汇:只有 l0/l1/l2/l5)
5. `.claude/workflows/*.js` 里的阶段字符串(`'l4-prep'`、`'finalize'` 两个
   `SCAN_STAGES` 里根本没有)

具体打架的两处(2026-08-29 审计实测):`run_profile.MODES` 只有 3 个而
`run_mode.MODES` 有 4 个(多一个 `SENTINEL_PINNED`);`capsule.finalize` 调
`scan_profile()` 时**从不传 mode**,于是哨兵趟的 `l4-card` 被标 REQUIRED。
「完整性」这个结论的分母自己有四个版本。

## 定位

本模块只**声明**。它不知道任何阶段怎么跑、产物怎么写;上层(`run_profile` /
`completeness` / `health` / `replay` / JS 生成物)从这里派生,而不是各写一份。

`STAGES` 是所有已知阶段词汇的**超集**:既含 `run_profile.SCAN_STAGES` 的九个,
也含 JS 用而 python 侧没登记的 `l4_prep` / `finalize`,以及 `sector` 旁路。

## 两套「阶段」不是同一件事(2026-08-29 与现场对账后的裁定)

本模块初稿把 `REPLAYABLE_STAGES` 写成 `("frame","prelude","l5")`(照设计稿),而
`run_profile.REPLAYABLE_STAGES` 与 `capsule replay --stage` 用的是 `("l0","l1","l2","l5")`。
逐条核对代码后确认**两者都真,量的是不同的东西**,不能压成一份:

- **流水线阶段**(`PIPELINE_STAGES`):capsule 按它建 `stages/<stage>/` 与 `logs/<stage>/`,
  完整性用它算「跑到没跑到」。
- **重放单元**(`REPLAY_UNITS`):`replay()` 的 `stages=` 参数域,是**漏斗层**的名字。
  `l0` 执行 `scan.frame`(= `frame` 阶段),`l1`/`l2` 执行 `scan.universe`(在 `prelude`
  阶段内),`l5` 执行 `scan.assemble`(= `l5` 阶段)。

所以这里**两个都登记**,并用 `REPLAY_UNIT_STAGES` 把它们桥起来:`REPLAY_UNITS` 保持与今天
的代码逐字相同(reality wins —— 派生结果与今天不同就是回归),`REPLAYABLE_STAGES` 改为
**从桥表派生**,于是它仍等于设计稿写的 `("frame","prelude","l5")`,但不再是第二份手写清单。
"""
from __future__ import annotations

#: 全部阶段,**按执行序**。序是有载荷的:失败 run 里最后到达阶段之后的一切
#: 是 NOT_REACHED 而不是 missing(`run_profile` 靠这条区分「没跑到」与「该有却没有」)。
#:
#: 与既有两套词汇的关系:
#: - `run_profile.SCAN_STAGES` 九个全在这里(frame prelude gate1 l3 gate2 l4 l5 observe gate4);
#: - JS 侧另用 `l4-prep` 与 `finalize`(`scan-market.js`),在这里登记为 `l4_prep` / `finalize`;
#: - `sector` 是 L2 之后、L3 之前的旁路(行业 pack + brief),python 侧此前没有阶段名。
STAGES: tuple[str, ...] = (
    "frame",
    "prelude",
    "gate1",
    "sector",
    "l3",
    "gate2",
    "l4_prep",
    "l4",
    "l5",
    "observe",
    "gate4",
    "finalize",
)

#: `STAGES` 里**不**按流水线阶段记账的名字,以及为什么:
#: - `sector`:行业 pack / reuse / brief 由 `scan-market.js:414-415` 以 `PY('l3', …)` 派发,
#:   capsule 把它们记在 `l3` 名下;它在这里存在只是为了让产物登记表能按 skill 阶段说话。
#: - `l4_prep` / `finalize`:JS 侧的壳阶段,python 侧没有 `stages/<name>/` 记账。
#: 这三个名字进 `STAGES`(超集)但不进 `PIPELINE_STAGES`。把它们加进流水线会让完整性
#: 凭空多要三份从来没人写过的 `stages/<name>/*/result.json`。
NON_PIPELINE_STAGES: frozenset[str] = frozenset({"sector", "l4_prep", "finalize"})

#: capsule 真正按阶段记账的那几个(= 今天的 `run_profile.SCAN_STAGES`,逐字相同)。
#: 从 `STAGES` 过滤而来,所以往超集里加一个阶段时必须当场决定它算不算流水线阶段。
PIPELINE_STAGES: tuple[str, ...] = tuple(
    stage for stage in STAGES if stage not in NON_PIPELINE_STAGES
)

#: JS 阶段串 → 本表阶段名。JS 用连字符,python 用下划线;两边都得认。
#: `.claude/workflows/scan-market.js` 的 `phase()` 与 `PY(stage, …)` 用的就是左边这些。
JS_STAGE_ALIASES: dict[str, str] = {
    "frame": "frame",
    "prelude": "prelude",
    "gate1": "gate1",
    "sector": "sector",
    "l3": "l3",
    "gate2": "gate2",
    "l4-prep": "l4_prep",
    "l4": "l4",
    "l5": "l5",
    "observe": "observe",
    "gate4": "gate4",
    "finalize": "finalize",
}

#: 运行模式。**与 `scan/run_mode.MODES` 同源**——两份词汇分叉正是本模块要治的病:
#: `SENTINEL_PINNED` 是「跑 L4 的哨兵」(持仓票必须出卡,见 STAGES.md『哨兵 vs 持仓』),
#: 它与 `SENTINEL_EMPTY` 的证据义务**完全不同**,少登记一个就等于把它归进未知分支。
MODES: tuple[str, ...] = ("FULL", "FORCED_FULL", "SENTINEL_EMPTY", "SENTINEL_PINNED")

#: 只有这些模式**不派发** L4(因此不欠 l4-card / l4-intel 的证据)。
#: `SENTINEL_PINNED` 故意不在里面。
L4_SKIPPING_MODES: frozenset[str] = frozenset({"SENTINEL_EMPTY"})

#: 只在某些模式下才派发的阶段族。`SENTINEL_EMPTY` 跳过的就是它(`SENTINEL_PINNED` 不跳)。
L4_STAGES: frozenset[str] = frozenset({"l4"})

#: **重放单元** —— `trace.replay.replay(stages=…)` 的参数域,也是
#: `run_profile.REPLAYABLE_STAGES` 与 `capsule replay --stage` 的默认值。
#: 名字是漏斗层的(l0/l1/l2),不是流水线阶段名 —— 见模块 docstring「两套阶段」。
REPLAY_UNITS: tuple[str, ...] = ("l0", "l1", "l2", "l5")

#: 重放单元 → 它落在哪个流水线阶段。这张表就是两套词汇之间**唯一**的桥。
REPLAY_UNIT_STAGES: dict[str, str] = {
    "l0": "frame",      # `python -m autoresearch.scan.frame`
    "l1": "prelude",    # `python -m autoresearch.scan.universe`(prelude 内跑 L0→L2)
    "l2": "prelude",    # 同上:L1 与 L2 是同一条命令的两半
    "l5": "l5",         # `python -m autoresearch.scan.assemble`
}

#: L1 与 L2 是**同一次执行**(`scan.universe` 一次写出三份 CSV),所以它们合成一个执行单元;
#: 对外仍是两个名字、各报自己名下的产物(`trace/replay.py` 模块 docstring 记了为什么)。
L1L2_UNIT = "l1l2"

#: 旧单元名 → 真正被执行的单元名。没有别名的单元执行的就是自己。
REPLAY_UNIT_ALIASES: dict[str, str] = {"l1": L1L2_UNIT, "l2": L1L2_UNIT}

#: 真正会被执行一次的单元(去重、保序)。`replay` 用它建 spec 表。
REPLAY_EXEC_UNITS: tuple[str, ...] = tuple(
    dict.fromkeys(REPLAY_UNIT_ALIASES.get(unit, unit) for unit in REPLAY_UNITS)
)

#: 确定性到可以按冻结输入重放出同样字节的**流水线阶段**(由桥表派生,不是第二份手写清单)。
#: LLM 阶段永不进这里(「产物能证明跑过什么、不能证明没跑过什么」)。
REPLAYABLE_STAGES: tuple[str, ...] = tuple(
    dict.fromkeys(REPLAY_UNIT_STAGES[unit] for unit in REPLAY_UNITS)
)

#: 业务 agent 角色 → 它属于哪个阶段。确定性壳(gp-shell / trace-control)**不在**这里:
#: 它们的证据是被捕获的命令,不是 transcript(`run_profile` 模块头同一条原则)。
ROLE_STAGES: dict[str, str] = {
    "strategist": "prelude",
    # `sector` 在 `STAGES` 里(产物登记表按 skill 阶段登记 `sector_briefs`),但它**不是**
    # 流水线阶段:行业 pack / reuse / brief 由 `scan-market.js:414-415` 以 `PY('l3', …)`
    # 派发,capsule 把它们记在 `l3` 名下。角色→阶段只回答「这个阶段跑到了吗」,映到一个不在
    # `PIPELINE_STAGES` 里的名字会让 `stage_reached` 恒 False —— 六个 opus brief 白跑,
    # 完整性还说「本来就不该有」。所以这里跟现场走(2026-08-29 与 `run_profile` 对账)。
    "sector-brief": "l3",
    "l3-rank": "l3",
    "l3-repair": "l3",
    "l4-card": "l4",
    "l4-intel": "l4",
    "l4-ensemble": "l4",
}

#: 只有被触发才存在的腿;它们缺席是关于这趟 run 的事实,不是证据的洞。
CONDITIONAL_ROLES: frozenset[str] = frozenset({"l3-repair", "l4-ensemble"})


def roles_in_stages(stages: frozenset[str] | set[str] | tuple[str, ...]) -> frozenset[str]:
    """属于这些阶段的全部业务角色。

    「跳过 L4 的模式不欠哪些角色的证据」是**派生**出来的,不是第二份手抄名单:
    新增一个 L4 角色而忘了同步跳过集,哨兵趟就会被要求交一份它根本没派发的 transcript。
    """
    wanted = set(stages)
    return frozenset(role for role, stage in ROLE_STAGES.items() if stage in wanted)


def normalize_stage(name: str) -> str:
    """把 JS 侧的阶段串(连字符)折成本表的阶段名;未知名原样返回。"""
    return JS_STAGE_ALIASES.get(name, name)


def stage_index(name: str) -> int:
    """阶段在执行序里的位置;未知阶段 → -1(调用方自行决定怎么处理)。"""
    normalized = normalize_stage(name)
    return STAGES.index(normalized) if normalized in STAGES else -1


def skips_l4(mode: str) -> bool:
    """这个模式是否**不**派发 L4。未知模式按「派发」处理(宁可多要证据)。"""
    return mode in L4_SKIPPING_MODES


# ── stock-research(analyze)词汇(2026-08-31 D6.1;scan 词汇在上,一个字未动) ──
#: 已注册的 run kind——目前只有两个技能落 capsule。新技能接线前先在这里报到。
RUN_KINDS: tuple[str, ...] = (
    "scan-market",
    "stock-research",
    "macro-research",
    "sector-research",
)
#: full 档阶段序:取数 → 情报 → 撰写 → 组装 → 发布。
ANALYZE_STAGES: tuple[str, ...] = ("harvest", "intel", "write", "assemble", "publish")
#: lite 档(决策卡)只有两步——不派情报员,见 `engine-playbook`「lite 一律不派」。
ANALYZE_LITE_STAGES: tuple[str, ...] = ("harvest", "card")
#: `stock-research` 的模式词汇,与 `scan.MODES` 互不相干(各自的技能各自的模式)。
ANALYZE_MODES: tuple[str, ...] = ("FULL", "LITE")
#: 角色 → 阶段。两个情报角色(A 股 / 美股)都挂在 `intel` 阶段下。
ANALYZE_ROLE_STAGES: dict[str, str] = {"company-intel": "intel", "us-intel": "intel"}
#: 只在标的市场匹配时才派发的腿——缺席是「这只票不是这个市场」的事实,不是证据的洞。
ANALYZE_CONDITIONAL_ROLES: frozenset[str] = frozenset({"company-intel", "us-intel"})

# ── macro-research 词汇 ───────────────────────────────────────────────
MACRO_STAGES: tuple[str, ...] = ("harvest", "intel", "write", "assemble", "publish")
MACRO_LITE_STAGES: tuple[str, ...] = ("frame", "write", "publish")
MACRO_MODES: tuple[str, ...] = ("FULL", "LITE")
MACRO_ROLE_STAGES: dict[str, str] = {
    "macro.research": "write",
    "macro.brief": "write",
}

# ── sector-research 词汇 ──────────────────────────────────────────────
SECTOR_STAGES: tuple[str, ...] = ("prepare", "intel", "write", "validate", "publish")
SECTOR_LITE_STAGES: tuple[str, ...] = ("prepare", "write", "validate", "publish")
SECTOR_MODES: tuple[str, ...] = ("FULL", "LITE")
SECTOR_ROLE_STAGES: dict[str, str] = {
    "sector.intel": "intel",
    "sector.research": "write",
    "sector.brief": "write",
}
