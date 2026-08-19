"""毕业提名(2026-08-13 用户裁定 B 档):lesson 毕业不再直写 playbook,改起草提案等人批。

spec: docs/specs/2026-08-13-retro-skill-selfmodify-removal-design.md §4.1。
核心时序不变量(本文件第 2 组):**提名不退役 lesson**——提名期间该经验保持 active、
继续占 render_calibration_block 的注入名额,直到用户在开发会话固化落地后才 retire_lesson。
若"顺手优化"成提名即退役,等待人批期间这条经验既不在 playbook 也不在注入名单(两头落空)。

隔离:set_root(tmp)(照 test_proposals_kanban.py),不碰真实 context/knowledge。
"""
from __future__ import annotations

import pytest

import autoresearch.learning.feedback_store as fs


@pytest.fixture(autouse=True)
def _tmp_store(tmp_path):
    old = fs.KNOW
    fs.set_root(tmp_path / "knowledge")
    yield
    fs.set_root(old)


def _mk_graduated_lesson(slug="low_winner_reversal", rule="低获利盘+主力净流入=反转候选,别压在召回线外"):
    """造一条达毕业条件的经验:反复强化 + MTM support 明显压过 refute。"""
    lsn = fs.upsert_lesson(slug, ("global", "*"), rule, ["retro 2026-06-19 missed_l1 群体特征"],
                           confidence=0.7)
    for day in ("2026-08-01", "2026-08-04", "2026-08-06"):
        fs.mtm_update(slug, "support", day=day)
    return lsn


# ───────────────────── ① 提名落 kind=graduation 提案 ─────────────────────


def test_nomination_files_graduation_proposal():
    lsn = _mk_graduated_lesson()
    pr = fs.add_graduation_nomination("low_winner_reversal",
                                      target_hint=".claude/skills/scan-market/STAGES.md")

    assert pr["kind"] == "graduation" and pr["status"] == "open"
    assert lsn["id"] in pr["summary"]
    # rationale 带 MTM 读数 + 强化次数(人批时不必回头翻 lessons.jsonl)
    assert "support" in pr["rationale"] and "3" in pr["rationale"]
    # diff_sketch 带 rule 原文 + 建议目标(固化时照抄,不必重新蒸馏)
    assert "低获利盘+主力净流入=反转候选,别压在召回线外" in pr["diff_sketch"]
    assert ".claude/skills/scan-market/STAGES.md" in pr["diff_sketch"]


def test_nomination_shows_on_open_board():
    """提名进现有看板(零新建露出:retro_input 待裁决节 / prelude nag / L5 债务节都读它)。"""
    _mk_graduated_lesson()
    pr = fs.add_graduation_nomination("low_winner_reversal")
    assert pr["id"] in {p["id"] for p in fs.open_proposals()}


# ───────────────── ② 时序不变量:提名不退役,注入无空窗 ─────────────────


def test_nomination_does_not_retire_lesson():
    _mk_graduated_lesson()
    fs.add_graduation_nomination("low_winner_reversal")

    rec = next(r for r in fs._read_jsonl(fs._LESSONS) if r["id"] == "ls_low_winner_reversal")
    assert rec["status"] == "active"          # 提名 ≠ 退役
    assert "retired" not in rec and "invalid_at" not in rec


def test_lesson_still_injected_after_nomination():
    """提名期间经验继续注入 L3 校准块——人批+固化落地前不许出现知识空窗。"""
    _mk_graduated_lesson()
    fs.add_graduation_nomination("low_winner_reversal")

    block = fs.render_calibration_block([("global", "*")])
    assert "低获利盘+主力净流入=反转候选" in block


# ───────────────────── ③ 同 slug 去重(幂等) ─────────────────────


def test_same_slug_open_nomination_is_idempotent():
    """retro 日复日跑,同一条经验不该堆出 N 条毕业提名(对齐 mtm_update 退休提名的去重哲学)。"""
    _mk_graduated_lesson()
    first = fs.add_graduation_nomination("low_winner_reversal")
    again = fs.add_graduation_nomination("low_winner_reversal")

    assert again["id"] == first["id"]
    grads = [r for r in fs._read_jsonl(fs._PROPOSALS) if r["kind"] == "graduation"]
    assert len(grads) == 1


def test_resolved_nomination_frees_the_slot():
    """上一条毕业提名已裁决(rejected/applied)→ 允许再次提名(去重只挡 open 的)。"""
    _mk_graduated_lesson()
    first = fs.add_graduation_nomination("low_winner_reversal")
    fs.set_proposal_status(first["id"], "rejected")
    again = fs.add_graduation_nomination("low_winner_reversal")

    assert again["id"] != first["id"]


def test_dedup_is_per_slug():
    """不同经验各自提名,别被同族去重误挡。"""
    _mk_graduated_lesson()
    _mk_graduated_lesson(slug="gate_overkill", rule="次新成长别一刀切")
    a = fs.add_graduation_nomination("low_winner_reversal")
    b = fs.add_graduation_nomination("gate_overkill")
    assert a["id"] != b["id"]


# ───────────────────── ④ 前置校验 ─────────────────────


def test_unknown_slug_raises():
    with pytest.raises(ValueError):
        fs.add_graduation_nomination("从来没写过的经验")
    assert fs._read_jsonl(fs._PROPOSALS) == []        # raise 前不落盘


def test_retired_lesson_cannot_be_nominated():
    """已退休的经验不该被提名毕业(毕业是给活着的经验的出口)。"""
    _mk_graduated_lesson()
    fs.retire_lesson("low_winner_reversal")
    with pytest.raises(ValueError):
        fs.add_graduation_nomination("low_winner_reversal")
    assert fs._read_jsonl(fs._PROPOSALS) == []


def test_slug_accepts_both_bare_and_prefixed_form():
    """slug 与 ls_ 前缀两种写法都认(与 upsert_lesson/retire_lesson 同款容错)。"""
    _mk_graduated_lesson()
    pr = fs.add_graduation_nomination("ls_low_winner_reversal")
    assert pr["kind"] == "graduation"
