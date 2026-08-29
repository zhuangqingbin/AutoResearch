"""契约登记表 vs 今天的现场 —— parity + drift 守卫。

design: `docs/specs/2026-08-29-full-coverage-research-system-brainstorm.md` §2.4 A1/A2。

这些用例的职责是**让契约层不许说谎**:它声明的东西必须与今天真实运行的代码一致
(parity),而任何新出现的、没登记的产物名必须当场变红(drift)。

「绿灯不等于有灯」:每条用例都应该在把被测声明改坏之后变红 —— 写完自问一遍。
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from autoresearch.agents.utils.rating import RATINGS_5_TIER
from autoresearch.contracts import agent_output as ao, artifacts as ca, stages as cs

REPO = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------- 登记表卫生

def test_registry_is_internally_consistent():
    names = [a.name for a in ca.ARTIFACTS]
    assert len(names) == len(set(names)), "登记名重复"
    for a in ca.ARTIFACTS:
        assert a.root in ca.ROOTS, f"{a.name}: 未知 root {a.root}"
        assert a.kind in ca.KINDS, f"{a.name}: 未知 kind {a.kind}"
        assert a.presence in ca.PRESENCES, f"{a.name}: 未知 presence {a.presence}"
        assert a.stage in cs.STAGES, f"{a.name}: 阶段 {a.stage} 不在 STAGES"
        if a.presence != "always":
            assert a.required_when or a.presence == "conditional", (
                f"{a.name}: gated 产物必须说明 required_when —— 「缺席是事实还是洞」"
                "得让读者分得出"
            )


def test_allowlist_does_not_shadow_the_registry():
    """白名单与登记表不许重叠 —— 否则一个产物既「是产物」又「不是产物」。"""
    registered = ca.paths() | {Path(p).name for p in ca.paths()}
    overlap = registered & ca.NON_ARTIFACT_LITERALS
    assert not overlap, f"这些名字同时出现在登记表和白名单里:{sorted(overlap)}"


# ---------------------------------------------------------------- parity

def test_covers_the_legacy_critical_artifacts():
    """`scan/artifacts.CRITICAL_ARTIFACTS` 的每一项都必须在新登记表里,路径逐字相同。"""
    from autoresearch.scan.artifacts import CRITICAL_ARTIFACTS

    registered = ca.paths()
    for spec in CRITICAL_ARTIFACTS:
        assert spec.path in registered, (
            f"CRITICAL_ARTIFACTS 的 {spec.name} ({spec.path}) 没进 contracts 登记表"
        )


def test_modes_match_run_mode_vocabulary():
    """模式词汇分叉是 K3 的核心症状:run_profile 曾少一个 SENTINEL_PINNED。"""
    from autoresearch.scan import run_mode

    assert set(cs.MODES) == set(run_mode.MODES)


def test_sentinel_pinned_still_dispatches_l4():
    """持仓哨兵**跑** L4(持仓票必须出卡);把它并进「跳过 L4」会让证据义务算错。"""
    assert cs.skips_l4("SENTINEL_EMPTY") is True
    assert cs.skips_l4("SENTINEL_PINNED") is False


def test_stages_superset_of_run_profile():
    from autoresearch.scan.run_profile import SCAN_STAGES

    missing = set(SCAN_STAGES) - set(cs.STAGES)
    assert not missing, f"run_profile 有而契约层没有的阶段:{sorted(missing)}"


def test_js_stage_strings_are_all_known():
    """JS 用 'l4-prep' / 'finalize',而 run_profile.SCAN_STAGES 里根本没有它们。"""
    js = REPO / ".claude/workflows/scan-market.js"
    text = js.read_text(encoding="utf-8")
    found = set(re.findall(r"PY\(\s*'([a-z0-9-]+)'", text)) | set(
        re.findall(r"phase\(\s*'([A-Za-z0-9-]+)'", text)
    )
    unknown = {s for s in found if cs.normalize_stage(s.lower()) not in cs.STAGES}
    assert not unknown, f"JS 里出现了契约层不认识的阶段:{sorted(unknown)}"


def test_rating_order_is_the_single_source():
    assert ao.RATING_ORDER == RATINGS_5_TIER


def test_js_rank_view_is_the_reverse_of_python():
    """JS 的 RANK 是 sell=0…buy=4,python 是 Buy=0…Sell=4 —— 方向相反,靠人记住是病。"""
    view = ao.js_rank_view()
    assert view["sell"] == 0 and view["buy"] == len(ao.RATING_ORDER) - 1
    for i, r in enumerate(ao.RATING_ORDER):
        assert view[r.lower()] == len(ao.RATING_ORDER) - 1 - i


def test_stop_reasons_match_the_parser():
    from autoresearch.scan.l4.parsers import _STOP_REASONS

    assert ao.STOP_REASONS == _STOP_REASONS


def test_ow_gates_match_the_parser():
    from autoresearch.scan.l4.parsers import _GATES3

    assert ao.OW_GATES == _GATES3


def test_terrain_header_matches_the_extractor():
    from autoresearch.sector.brief import TERRAIN_HDR

    pattern = ao.contract("sector-brief").field("terrain_header").pattern
    assert re.match(pattern, TERRAIN_HDR), (
        f"地形段锚点漂了:契约 {pattern!r} 认不出抽取器的 {TERRAIN_HDR!r}"
    )


def test_market_view_section_pattern_matches_the_renderer():
    from autoresearch.scan.report_sections import _MV_HEAD_RE

    contract_pat = ao.contract("macro-brief").field("section_head").pattern
    sample = "1. **一句话定调**: 避险哑铃\n2. **市场结构**: 宽度 0.31\n"
    assert [m.group(1) for m in re.finditer(contract_pat, sample, re.M)] == ["1", "2"]
    assert [m.group(1) for m in _MV_HEAD_RE.finditer(sample)] == ["1", "2"]


# ---------------------------------------------------------------- 产出契约对拍

SAMPLE_CARD = """# 决策卡 — 600519 贵州茅台

**一行多空**: 多 = 提价预期;空 = 动销偏弱
Rubric建议: Overweight
**Rating**: Hold
置信度: 中
OW三门: 主力真在 ✓ ｜ 业绩真兑现 ✗ ｜ 估值不透支 ✓
进入P4倾向: Hold
[执行线] pct_chg <= 3.0
[执行线] pos_in_range < 0.7

FINAL TRANSACTION PROPOSAL: **HOLD**
"""


def test_card_contract_agrees_with_the_production_parsers():
    """契约 pattern 与 `l4/parsers` 的现成正则在同一张卡上必须得出同样的结果。"""
    from autoresearch.scan.l4 import parsers

    c = ao.contract("l4-card")
    assert re.search(c.field("proposal").pattern, SAMPLE_CARD, re.I).group(1).upper() == "HOLD"
    assert parsers._PROPOSAL_RE.search(SAMPLE_CARD).group(1).upper() == "HOLD"

    assert re.search(c.field("confidence").pattern, SAMPLE_CARD).group(1) == "中"
    assert parsers._CONF_RE.search(SAMPLE_CARD).group(1) == "中"

    assert re.search(c.field("bull_bear").pattern, SAMPLE_CARD).group(1).startswith("多 =")
    assert parsers._BULLBEAR_RE.search(SAMPLE_CARD).group(1).startswith("多 =")


def test_strict_rating_pattern_does_not_take_the_first_word_anywhere():
    """现行 `parse_rating` 兜底取全文第一个评级词 —— 这张卡的正确答案是 Hold,
    而兜底路会在 `Rubric建议: Overweight` 上先命中。严格档必须只认行首标签。"""
    from autoresearch.agents.utils.rating import parse_rating

    strict = ao.contract("l4-card").field("rating").pattern
    hits = [m.group(1) for m in re.finditer(strict, SAMPLE_CARD)]
    assert hits == ["Hold"], f"严格档读出 {hits},应当只有 Hold"
    # 现行宽松解析器在这张卡上恰好也对(它先扫到 **Rating** 那一行);
    # 记录下来,等 A2 把严格档接进生产时这条会变成两边都必须是 Hold 的回归锁。
    assert parse_rating(SAMPLE_CARD) == "Hold"


def test_rank_of_rejects_unknown_rating():
    with pytest.raises(ValueError):
        ao.rank_of("Strong Buy")


# ---------------------------------------------------------------- drift 守卫

_LITERAL_RE = re.compile(r'"[A-Za-z0-9_./*-]+\.(?:csv|json|md|txt)"')
_SCAN_ROOTS = ("autoresearch/scan", "autoresearch/trace", ".claude/workflows")


def _repo_literals() -> set[str]:
    out = subprocess.run(  # noqa: S603
        ["grep", "-rhoE", _LITERAL_RE.pattern, *_SCAN_ROOTS,
         "--include=*.py", "--include=*.js"],
        cwd=REPO, capture_output=True, text=True, check=False,
    )
    return {line.strip('"') for line in out.stdout.splitlines() if line.strip()}


def test_no_unregistered_artifact_literals():
    """产物名此前是跨三种语言的字面量(finalists.csv 在 40 个文件里)。

    这条守卫的作用不是消灭字面量,而是**让新出现的、没登记的产物名当场变红**:
    白名单只许减不许增,新产物一律先进 `ARTIFACTS` 再写代码。
    """
    literals = _repo_literals()
    assert literals, "grep 没抓到任何字面量 —— 守卫自身坏了(永不变红的绿灯)"

    registered = ca.paths() | {Path(p).name for p in ca.paths()}
    unknown = sorted(
        lit for lit in literals
        if lit not in registered
        and Path(lit).name not in registered
        and lit not in ca.NON_ARTIFACT_LITERALS
        and Path(lit).name not in ca.NON_ARTIFACT_LITERALS
    )
    assert not unknown, (
        "这些产物名没在 contracts.artifacts 登记(是产物就登记;不是就进 "
        f"NON_ARTIFACT_LITERALS 并说明理由):{unknown}"
    )


def test_drift_guard_would_catch_a_new_name(monkeypatch):
    """守卫的鉴别力自证:塞一个没登记的名字进去,它必须红。"""
    monkeypatch.setattr(
        ca, "NON_ARTIFACT_LITERALS", frozenset(ca.NON_ARTIFACT_LITERALS - {"verify.csv"})
    )
    literals = {"verify.csv"}
    registered = ca.paths() | {Path(p).name for p in ca.paths()}
    unknown = [
        lit for lit in literals
        if lit not in registered and lit not in ca.NON_ARTIFACT_LITERALS
    ]
    assert unknown == ["verify.csv"]
