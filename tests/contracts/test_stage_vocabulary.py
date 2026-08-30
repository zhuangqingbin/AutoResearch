"""阶段 / 模式 / 角色词汇只有一份 —— 派生结果必须与今天的硬编码逐项相等。

spec: `docs/specs/2026-08-29-full-coverage-research-system-brainstorm.md` §2.2 K3 / §2.4 A1
plan: `docs/superpowers/plans/2026-08-29-full-coverage-p0-p1.md` Task 9a

K3 的病不是「多打了几个字符串」,而是**「该有什么」的分母有四个版本**:
`run_profile` 一份、`completeness` 展开一份、`replay` 又一份、JS 再一份。
本文件的职责有两条,缺一条这层就白做:

1. **parity** —— 下面 `TODAY_*` 是 2026-08-29 收工时四份表的**逐字副本**。派生结果与它
   不同就是**回归**,应当去查派生逻辑,而不是改这里的期望值(计划 Task 9 原话)。
2. **鉴别力** —— 光断言「值相等」是永不变红的绿灯:任何人重新写一份等值字面量都能过。
   所以关键几条断的是**同一个对象**(`is`),再配几条「把契约层改一个字,下游必须跟着变」
   的变异探针。删掉派生、改回硬编码 → 这些条会红。
"""

from __future__ import annotations

import inspect

import pytest

from autoresearch.contracts import stages as cs
from autoresearch.scan import run_profile as rp
from autoresearch.trace import completeness as comp, replay as R

# ---------------------------------------------------------------- 今天的值(2026-08-29 实测)

TODAY_SCAN_STAGES = (
    "frame", "prelude", "gate1", "l3", "gate2", "l4", "l5", "observe", "gate4",
)
TODAY_SCAN_AGENT_ROLES = (
    "strategist", "sector-brief", "l3-rank", "l4-card", "l4-intel",
)
TODAY_ROLE_STAGES = {
    "strategist": "prelude",
    "sector-brief": "l3",
    "l3-rank": "l3",
    "l3-repair": "l3",
    "l4-card": "l4",
    "l4-intel": "l4",
    "l4-ensemble": "l4",
}
TODAY_CONDITIONAL_AGENT_ROLES = frozenset({"l3-repair", "l4-ensemble"})
TODAY_MODES = ("FULL", "FORCED_FULL", "SENTINEL_EMPTY", "SENTINEL_PINNED")
TODAY_REPLAYABLE_STAGES = ("l0", "l1", "l2", "l5")
TODAY_SENTINEL_SKIPPED_STAGES = frozenset({"l4"})
TODAY_SENTINEL_SKIPPED_ROLES = frozenset({"l4-card", "l4-intel", "l4-ensemble"})
TODAY_STAGE_ALIASES = {
    "l1": ("l1l2", ("L1_scored_full.csv", "L1_recall_top1000.csv")),
    "l2": ("l1l2", ("L2_gbdt_top200.csv",)),
}
TODAY_SPECS = (
    ("l0",
     ("autoresearch.scan.frame", "2026-08-26", "--json-out", "market_pack.json"),
     ("market_pack.json",)),
    ("l1l2",
     ("autoresearch.scan.universe", "2026-08-26"),
     ("L1_scored_full.csv", "L1_recall_top1000.csv", "L2_gbdt_top200.csv")),
    ("l5",
     ("autoresearch.scan.assemble", "2026-08-26"),
     ("summary.md", "appendix.md")),
)


# ---------------------------------------------------------------- ① SCAN_STAGES

def test_scan_stages_parity():
    assert rp.SCAN_STAGES == TODAY_SCAN_STAGES
    assert rp.SCAN_STAGES is cs.PIPELINE_STAGES, "SCAN_STAGES 必须**就是**契约层那个对象,不是等值副本"


def test_pipeline_stages_are_a_view_of_the_superset():
    """`STAGES` 是超集(含 JS 的 l4_prep/finalize 与 skill 的 sector 旁路),
    `PIPELINE_STAGES` 是 capsule 真按阶段记账的那九个 —— 顺序必须来自超集。"""
    assert set(cs.PIPELINE_STAGES) <= set(cs.STAGES)
    assert list(cs.PIPELINE_STAGES) == [s for s in cs.STAGES if s in set(cs.PIPELINE_STAGES)]
    assert set(cs.STAGES) - set(cs.PIPELINE_STAGES) == set(cs.NON_PIPELINE_STAGES)


# ---------------------------------------------------------------- ② ROLE_STAGES

def test_role_stages_parity():
    assert rp.ROLE_STAGES == TODAY_ROLE_STAGES
    assert rp.ROLE_STAGES is cs.ROLE_STAGES


def test_agent_roles_derive_from_the_role_table():
    assert rp.SCAN_AGENT_ROLES == TODAY_SCAN_AGENT_ROLES
    assert rp.CONDITIONAL_AGENT_ROLES == TODAY_CONDITIONAL_AGENT_ROLES
    assert rp.CONDITIONAL_AGENT_ROLES is cs.CONDITIONAL_ROLES
    # 非条件角色 = 角色表减去条件腿。两份清单各写一遍正是它们能分叉的原因。
    assert tuple(
        r for r in cs.ROLE_STAGES if r not in cs.CONDITIONAL_ROLES
    ) == rp.SCAN_AGENT_ROLES


def test_every_role_stage_is_a_pipeline_stage():
    """角色 → 阶段只用来回答「这个阶段跑到了吗」。映到一个**不在** `SCAN_STAGES` 里的
    阶段名(比如把 sector-brief 记成 `sector`),`stage_reached` 恒 False,
    于是那条腿被静默判成 NOT_EXPECTED —— 六个 opus brief 白跑而完整性说「本来就不该有」。"""
    for role, stage in cs.ROLE_STAGES.items():
        assert stage in cs.PIPELINE_STAGES, f"{role} 映到了非流水线阶段 {stage!r}"


# ---------------------------------------------------------------- ③ MODES

def test_modes_parity():
    from autoresearch.scan import run_mode

    assert rp.MODES == TODAY_MODES
    assert rp.MODES is cs.MODES
    assert set(cs.MODES) == set(run_mode.MODES)


def test_sentinel_skip_sets_derive_from_the_role_table():
    assert rp.SENTINEL_SKIPPED_STAGES == TODAY_SENTINEL_SKIPPED_STAGES
    assert rp.SENTINEL_SKIPPED_ROLES == TODAY_SENTINEL_SKIPPED_ROLES
    # 跳过的角色 = 属于被跳过阶段的角色,不是手抄的第二份名单。
    assert cs.roles_in_stages(cs.L4_STAGES) == rp.SENTINEL_SKIPPED_ROLES


def test_only_sentinel_empty_skips_l4_and_the_predicate_is_the_contract():
    """持仓哨兵**跑** L4(持仓票要出卡)。判据必须是 `contracts.skips_l4`,
    不是 `startswith("SENTINEL")` 那种会把持仓一起吞掉的前缀匹配。"""
    assert cs.skips_l4("SENTINEL_EMPTY") is True
    assert cs.skips_l4("SENTINEL_PINNED") is False
    empty = rp.scan_profile(mode="SENTINEL_EMPTY")
    pinned = rp.scan_profile(mode="SENTINEL_PINNED")
    assert "l4" not in empty.expected_stages
    assert "l4" in pinned.expected_stages
    assert empty.role_expected("l4-card") is False
    assert pinned.role_expected("l4-card") is True


def test_scan_profile_asks_the_contract_not_a_literal(monkeypatch):
    """变异探针:把契约层的「跳过 L4 的模式」搬到 FORCED_FULL 上,
    `scan_profile` 必须跟着变。若它自己判 `mode == "SENTINEL_EMPTY"`,这条永远绿。"""
    monkeypatch.setattr(cs, "L4_SKIPPING_MODES", frozenset({"FORCED_FULL"}))
    assert "l4" not in rp.scan_profile(mode="FORCED_FULL").expected_stages
    assert "l4" in rp.scan_profile(mode="SENTINEL_EMPTY").expected_stages


# ---------------------------------------------------------------- ④ REPLAYABLE_STAGES

def test_replayable_stages_parity():
    assert rp.REPLAYABLE_STAGES == TODAY_REPLAYABLE_STAGES
    assert rp.REPLAYABLE_STAGES is cs.REPLAY_UNITS


def test_replay_units_and_pipeline_stages_are_two_vocabularies_bridged_by_one_map():
    """`run_profile.REPLAYABLE_STAGES` 数的是**重放单元**(l0/l1/l2/l5 —— 漏斗层名),
    设计稿写的 `("frame","prelude","l5")` 数的是**流水线阶段**。两者都真,不能压成一份:
    l0 跑的是 `scan.frame`(= frame 阶段),l1/l2 跑的是 `scan.universe`(prelude 阶段内)。"""
    assert set(cs.REPLAY_UNIT_STAGES) == set(cs.REPLAY_UNITS)
    for unit, stage in cs.REPLAY_UNIT_STAGES.items():
        assert stage in cs.PIPELINE_STAGES, f"重放单元 {unit} 映到了非流水线阶段 {stage!r}"
    assert cs.REPLAYABLE_STAGES == ("frame", "prelude", "l5")
    assert tuple(
        dict.fromkeys(cs.REPLAY_UNIT_STAGES[u] for u in cs.REPLAY_UNITS)
    ) == cs.REPLAYABLE_STAGES


def test_replay_default_stages_and_aliases_come_from_the_vocabulary():
    assert inspect.signature(R.replay).parameters["stages"].default is cs.REPLAY_UNITS
    assert R.STAGE_ALIASES == TODAY_STAGE_ALIASES
    assert R.L1L2 == cs.L1L2_UNIT
    assert set(R.STAGE_ALIASES) == set(cs.REPLAY_UNIT_ALIASES)
    assert cs.REPLAY_EXEC_UNITS == ("l0", "l1l2", "l5")


def test_default_stage_specs_parity():
    got = tuple((s.stage, s.argv, s.outputs) for s in R.default_stage_specs("2026-08-26"))
    assert got == TODAY_SPECS


def test_default_stage_specs_follow_the_vocabulary(monkeypatch):
    """变异探针:改执行单元清单的顺序,spec 顺序必须跟着变(证明它读的是词汇表)。"""
    monkeypatch.setattr(cs, "REPLAY_EXEC_UNITS", ("l5", "l0", "l1l2"))
    assert [s.stage for s in R.default_stage_specs("2026-08-26")] == ["l5", "l0", "l1l2"]


def test_unplanned_replay_unit_is_a_loud_error(monkeypatch):
    """词汇表加了一个没人会跑的重放单元 → 必须当场炸,而不是安静少跑一段。"""
    monkeypatch.setattr(cs, "REPLAY_EXEC_UNITS", (*cs.REPLAY_EXEC_UNITS, "l9"))
    with pytest.raises(ValueError, match="l9"):
        R.default_stage_specs("2026-08-26")


# ---------------------------------------------------------------- completeness 侧

def test_completeness_reads_the_shared_role_table():
    assert comp.ROLE_STAGES is cs.ROLE_STAGES


def test_build_expected_follows_the_shared_role_table(monkeypatch):
    """变异探针:把 sector-brief 挪到一个**没跑到**的阶段,expected 必须从
    REQUIRED 变成 NOT_REACHED。若 completeness 自带一份角色表,这条永远绿。"""
    profile = rp.scan_profile(business_status="FAILED", last_stage="prelude")

    def disposition(prof, key):
        return next(i.disposition for i in comp.build_expected(prof).items if i.key == key)

    assert disposition(profile, "agent:strategist") == comp.REQUIRED   # prelude 跑到了
    assert disposition(profile, "agent:sector-brief") == comp.NOT_REACHED  # l3 没跑到
    monkeypatch.setitem(cs.ROLE_STAGES, "sector-brief", "prelude")
    assert disposition(profile, "agent:sector-brief") == comp.REQUIRED


def test_expected_stage_rows_use_the_contract_stage_names():
    profile = rp.scan_profile()
    keys = {i.key for i in comp.build_expected(profile).items if i.key.startswith("stage:")}
    assert keys == {f"stage:{s}" for s in cs.PIPELINE_STAGES}


# ---------------------------------------------------------------- _BASE_RULES 词汇守卫

def test_base_rules_only_mention_known_stages_and_roles():
    rp.check_rule_vocabulary(rp._BASE_RULES)   # 今天不该抛


def test_rule_vocabulary_guard_catches_an_unknown_name():
    """守卫的鉴别力自证 —— 没有这条,上面那条是「永不变红的绿灯」。"""
    bogus_stage = rp.ArtifactRule("x", "stages/l4b/*/result.json", "capsule", "always")
    bogus_role = rp.ArtifactRule("y", "agents/l4-oracle/*", "agent_index", "always")
    for rule in (bogus_stage, bogus_role):
        with pytest.raises(ValueError):
            rp.check_rule_vocabulary((rule,))
    # 合法形状不许误伤:`agents/index.json` 是文件不是角色,glob 段也不是名字。
    rp.check_rule_vocabulary((
        rp.ArtifactRule("agent_index", "agents/index.json", "capsule", "llm_run"),
        rp.ArtifactRule("stage_any", "stages/*/result.json", "capsule", "always"),
        rp.ArtifactRule("card", "agents/l4-card/*", "agent_index", "llm_run"),
    ))
