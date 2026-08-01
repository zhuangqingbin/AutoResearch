"""Gate 0 事实门:设计稿 §1.1 的每个手抄数字都必须能由冻结的 evidence manifest 再生。

design: docs/specs/2026-08-01-wave10-report-ops-slimdown-zerobuy-design.md §1.3 / §6「事实门」

本文件**直接从设计稿正文里抠数字**,再和冻结快照对账。所以它有两种红法,两种都是对的:

  · 快照重新冻结、数字变了而设计稿没跟着改 → 红(证据漂移了,文档在说旧话);
  · 设计稿被改写、抠不到那句话了       → 红(证据契约的措辞是有人依赖的,不能随手改)。

不是"再抄一遍数字",而是让**抄袭这件事本身**变成会失败的操作。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from autoresearch.learning.evidence_manifest import validate

DESIGN = Path("docs/specs/2026-08-01-wave10-report-ops-slimdown-zerobuy-design.md")
FROZEN = Path("docs/research/2026-08-01-wave10-gate0-evidence.json")


@pytest.fixture(scope="module")
def design_text() -> str:
    if not DESIGN.exists():
        pytest.skip(f"设计稿不在:{DESIGN}")
    return DESIGN.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def frozen() -> dict:
    if not FROZEN.exists():
        pytest.skip(f"冻结快照不在:{FROZEN}")
    return json.loads(FROZEN.read_text(encoding="utf-8"))


def _num(text: str, pattern: str) -> float:
    """从设计稿抠一个数。抠不到就是红 —— 说明那句话被改写了,证据契约需要重新确认。"""
    hit = re.search(pattern, text)
    assert hit, f"设计稿里找不到:{pattern}"
    # 稿里用的是 U+2212 减号,不是 ASCII 连字符
    return float(hit.group(1).replace("−", "-").replace("–", "-"))


def _value(frozen: dict, metric_id: str) -> float:
    metrics = frozen["metrics"]
    assert metric_id in metrics, f"快照缺指标 {metric_id}"
    record = metrics[metric_id]
    assert record["status"] == "OK", f"{metric_id} 状态为 {record['status']},不得引用"
    return float(record["value"])


def _denominator(frozen: dict, denominator_id: str) -> int:
    denominators = frozen["denominators"]
    assert denominator_id in denominators, f"快照缺分母 {denominator_id}"
    return int(denominators[denominator_id]["value"])


# ────────────────────────── §1.1 Cohort 账 ──────────────────────────

def test_cohort_day_counts_match(design_text, frozen):
    assert _num(design_text, r"raw journal rows \| (\d+) 日") == _denominator(
        frozen, "journal_scan_days")
    assert _num(design_text, r"T\+2 mature \(`zero_buy_ledger`\) \| (\d+) 日") == (
        _denominator(frozen, "zero_buy_mature_days"))
    assert _num(design_text, r"abstention v2 mature \| (\d+) 日") == _denominator(
        frozen, "abstention_v2_judged_days")


def test_raw_and_mature_are_not_the_same_denominator(frozen):
    """§1.1 撤回了首稿「30 日 23 个 0买日」的写法 —— 结构上保证两者不可混用。"""
    raw = frozen["denominators"]["journal_scan_days"]
    mature = frozen["denominators"]["zero_buy_mature_days"]
    assert raw["cohort"] == "raw_run"
    assert mature["cohort"] == "t2_mature"
    assert raw["value"] != mature["value"]


def test_zero_buy_split_and_market_fwd2(design_text, frozen):
    assert _num(design_text, r"(\d+) 个 0买日 \+ \d+ 个买日") == _denominator(
        frozen, "zero_buy_mature_zero_days")
    assert _num(design_text, r"\d+ 个 0买日 \+ (\d+) 个买日") == _denominator(
        frozen, "zero_buy_mature_bought_days")
    stated = _num(design_text, r"0买日市场 fwd_2 均值 (−?[\d.]+)%") / 100
    assert abs(stated - _value(frozen, "zero_buy.zero_day_market_fwd2_mean")) < 5e-5


def test_abstention_v2_verdict_counts(design_text, frozen):
    verdicts = r"CORRECT (\d+) · FALSE (\d+) · NEUTRAL (\d+)"
    hit = re.search(verdicts, design_text)
    assert hit, f"设计稿里找不到:{verdicts}"
    for group, metric_id in enumerate((
        "abstention_v2.correct_days",
        "abstention_v2.false_days",
        "abstention_v2.neutral_days",
    ), start=1):
        assert float(hit.group(group)) == _value(frozen, metric_id), metric_id
    judged = _denominator(frozen, "abstention_v2_judged_days")
    assert _num(design_text, rf"(\d+)/{judged} 均 DEGRADED") == _value(
        frozen, "abstention_v2.degraded_days")


def test_paper_nav_four_lanes(design_text, frozen):
    lanes = {
        r"真实 (−?[\d.]+)% vs 影子": "paper_nav.real_return",
        r"vs 影子 (−?[\d.]+)% vs sized": "paper_nav.shadow_return",
        r"vs sized (−?[\d.]+)% vs 市场等权": "paper_nav.shadow_sized_return",
        r"vs 市场等权 (−?[\d.]+)%": "paper_nav.market_equal_weight_return",
    }
    for pattern, metric_id in lanes.items():
        stated = _num(design_text, pattern) / 100
        assert abs(stated - _value(frozen, metric_id)) < 5e-5, metric_id
    assert _num(design_text, r"真实 (\d+) 笔") == _value(frozen, "paper_nav.real_trades")
    assert _num(design_text, r"影子 (\d+) 笔") == _value(
        frozen, "paper_nav.shadow_trades")


# ────────────────────── §1.1 主力门迁移基线(legacy) ──────────────────────

def test_legacy_main_gate_migration_baseline(design_text, frozen):
    """36/7/20(总63)—— A11 验收要求 legacy cohort 精确复现这一组。"""
    assert _num(design_text, r"主力真在门 \| (\d+) 日 / \d+ 次") == 15
    assert _num(design_text, r"主力真在门 \| \d+ 日 / (\d+) 次") == _denominator(
        frozen, "gate_legacy.主力真在.measured_n")

    mean_stated = _num(design_text, r"mean excess2 (−?[\d.]+)%") / 100
    assert abs(mean_stated - _value(frozen, "gate_legacy.主力真在.mean_excess_2")) < 5e-5

    correct = _num(design_text, r"`excess2<0` (\d+)/63")
    neutral = _num(design_text, r"`0≤excess2<\+2pp` (\d+)/63")
    false_kill = _num(design_text, r"`excess2≥\+2pp` (\d+)/63")
    assert (correct, neutral, false_kill) == (36, 7, 20)
    assert correct + neutral + false_kill == _denominator(
        frozen, "gate_legacy.主力真在.measured_n")

    frozen_correct = _value(frozen, "gate_legacy.主力真在.correct_rate")
    frozen_false = _value(frozen, "gate_legacy.主力真在.false_kill_rate")
    assert abs(frozen_correct - correct / 63) < 5e-5
    assert abs(frozen_false - false_kill / 63) < 5e-5


def test_legacy_and_v3_main_gate_are_separate_series(frozen):
    """§A11:两者不得冒充同一序列。v3 去重后单门分母必然小于 legacy。"""
    legacy_n = _denominator(frozen, "gate_legacy.主力真在.measured_n")
    v3_n = _denominator(frozen, "gate_v3.主力真在.measured_n")
    assert v3_n < legacy_n
    assert frozen["denominators"][
        "gate_legacy.主力真在.measured_n"]["cohort"] == "legacy_migration"
    assert frozen["denominators"][
        "gate_v3.主力真在.measured_n"]["cohort"] == "experiment_eligible"


def test_left_tail_rate_is_not_filed_as_false_kill(design_text, frozen):
    """§1.1 明确 39% 是左尾保护率而非错杀率;快照必须按这个语义存。"""
    assert "不是错杀率" in design_text
    tail = frozen["metrics"]["gate_legacy.主力真在.left_tail_protection_rate"]
    assert tail["semantic"] == "left_tail_protection_rate"
    assert tail["source_field"] == "gate_ledger.tail_rate"
    assert abs(tail["value"] - 0.39) < 5e-3
    # 同一道门的错杀率是另一条指标、另一个来源,两者不得同值同源
    false_kill = frozen["metrics"]["gate_legacy.主力真在.false_kill_rate"]
    assert false_kill["source_field"] != tail["source_field"]


# ────────────────────────── 快照自身的完整性 ──────────────────────────

def test_frozen_snapshot_passes_its_own_validator(frozen):
    assert validate(frozen) == []


def test_frozen_snapshot_has_no_conflicts(frozen):
    assert frozen["conflicts"] == [], frozen["conflicts"]


def test_frozen_snapshot_carries_source_fingerprints(frozen):
    """没有指纹的证据 = 说不清读的是哪一版文件。"""
    fingerprinted = [
        m for m in frozen["metrics"].values()
        if m.get("semantic") and m.get("source_hashes")
    ]
    assert len(fingerprinted) >= 20


def test_registry_inventory_is_recorded(frozen):
    """§1.3-5:已在册的 L3 实验必须出现在 inventory,后续不得绕开它另开冲突 family。"""
    inventory = frozen["registry_inventory"]
    assert "exp_20260729_l3_hard_constraint_f" in inventory["experiments"]
    assert inventory["experiments"][
        "exp_20260729_l3_hard_constraint_f"]["status"] == "PREREGISTERED"
