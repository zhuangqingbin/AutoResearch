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

#: 确定性到可以按冻结输入重放出同样字节的阶段。LLM 阶段永不进这里
#: (「产物能证明跑过什么、不能证明没跑过什么」)。
REPLAYABLE_STAGES: tuple[str, ...] = ("frame", "prelude", "l5")

#: 业务 agent 角色 → 它属于哪个阶段。确定性壳(gp-shell / trace-control)**不在**这里:
#: 它们的证据是被捕获的命令,不是 transcript(`run_profile` 模块头同一条原则)。
ROLE_STAGES: dict[str, str] = {
    "strategist": "prelude",
    "sector-brief": "sector",
    "l3-rank": "l3",
    "l3-repair": "l3",
    "l4-card": "l4",
    "l4-intel": "l4",
    "l4-ensemble": "l4",
}

#: 只有被触发才存在的腿;它们缺席是关于这趟 run 的事实,不是证据的洞。
CONDITIONAL_ROLES: frozenset[str] = frozenset({"l3-repair", "l4-ensemble"})


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
