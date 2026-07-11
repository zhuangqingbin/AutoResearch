"""prompt_patch 提案(Plan B T1):经验 → 提示词补丁,`add_prompt_patch` 三重校验。

spec: docs/plans/2026-07-11-hermes-selfimprove-plan.md Task 1。
三重校验(核心安全,任一不过直接 raise,不静默降级):
① target_file 必须存在;② proposed_text 不得让 `_CONTRACT_ANCHORS` 任何一个契约锚从
target_file 消失;③ open 状态、kind=prompt_patch 的提案数已达上限(5)时拒绝新起草。
"""
from __future__ import annotations

import json

import pytest

import autoresearch.learning.feedback_store as fs


@pytest.fixture(autouse=True)
def _tmp_store(tmp_path):
    old = fs.KNOW
    fs.set_root(tmp_path / "knowledge")
    yield
    fs.set_root(old)


def _mk_target(tmp_path, name="fake_playbook.md", text="普通文案,无契约锚。"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


# ───────────────────────── ① target_file 必须存在 ─────────────────────────


def test_missing_target_file_raises(tmp_path):
    missing = tmp_path / "does_not_exist.md"
    with pytest.raises(FileNotFoundError):
        fs.add_prompt_patch(str(missing), "锚", "现状", "改写", ["证据"])
    assert fs._read_jsonl(fs._PROPOSALS) == []          # 未写入任何提案


# ───────────────────────── 合法起草 → 入 proposals.jsonl ─────────────────────────


def test_legal_draft_succeeds_and_persists(tmp_path):
    target = _mk_target(tmp_path)
    rec = fs.add_prompt_patch(
        str(target), "起草小节锚点", "普通文案", "改写后的文案",
        ["同型失误1:07-05 diagnosis X", "同型失误2:07-09 diagnosis X 再现", "gate_ledger ex>0 n=6"],
    )
    assert rec["kind"] == "prompt_patch" and rec["status"] == "open"
    recs = fs._read_jsonl(fs._PROPOSALS)
    assert len(recs) == 1 and recs[0]["id"] == rec["id"]

    payload = json.loads(rec["diff_sketch"])
    assert payload["target_file"] == str(target)
    assert payload["anchor_text"] == "起草小节锚点"
    assert payload["current_text"] == "普通文案"
    assert payload["proposed_text"] == "改写后的文案"
    assert "gate_ledger ex>0 n=6" in rec["rationale"]     # evidence 拼进 rationale


# ───────────────────────── ② 锚禁区:proposed_text 删锚 → raise ─────────────────────────


def test_anchor_deletion_raises(tmp_path):
    target = _mk_target(
        tmp_path, text="正文说明:卡契约 v3 相关规则在此,勿删。\n其它无关内容。")
    with pytest.raises(ValueError):
        fs.add_prompt_patch(
            str(target), "锚点", "正文说明:卡契约 v3 相关规则在此,勿删。",
            "正文说明:相关规则已改写。",               # 丢了「卡契约 v3」
            ["证据"],
        )
    assert fs._read_jsonl(fs._PROPOSALS) == []          # raise 前不落盘(原子性)


def test_anchor_deletion_raises_even_if_other_anchor_kept(tmp_path):
    """两锚同在时,proposed_text 只删其一也要拒——不是"全删光才算删锚"。"""
    target = _mk_target(
        tmp_path,
        text="卡契约 v3 与 机构面网查 两条锚都在这段。")
    with pytest.raises(ValueError):
        fs.add_prompt_patch(
            str(target), "锚点", "卡契约 v3 与 机构面网查 两条锚都在这段。",
            "卡契约 v3 与 网络查证 两条锚都在这段。",   # 「机构面网查」被改写掉了
            ["证据"],
        )
    assert fs._read_jsonl(fs._PROPOSALS) == []


def test_proposed_text_keeping_all_anchors_is_legal(tmp_path):
    """只要契约锚字符串本身原样保留,文案其它部分怎么改写都合法(不是文本级只读锁)。"""
    target = _mk_target(tmp_path, text="卡契约 v3:旧的啰嗦说明,读起来很绕。")
    rec = fs.add_prompt_patch(
        str(target), "锚点", "卡契约 v3:旧的啰嗦说明,读起来很绕。",
        "卡契约 v3:新的精简说明。",
        ["证据"],
    )
    assert rec["status"] == "open"


# ───────────────────────── ③ open prompt_patch 计数 ≤5 ─────────────────────────


def test_open_cap_blocks_sixth_draft(tmp_path):
    target = _mk_target(tmp_path)
    for i in range(5):
        fs.add_prompt_patch(str(target), f"锚{i}", "普通文案", f"改写{i}", ["证据"])
    with pytest.raises(RuntimeError):
        fs.add_prompt_patch(str(target), "锚6", "普通文案", "改写6", ["证据"])
    open_pp = [r for r in fs._read_jsonl(fs._PROPOSALS)
               if r["kind"] == "prompt_patch" and r["status"] == "open"]
    assert len(open_pp) == 5                             # 第 6 条未落盘


def test_open_cap_only_counts_prompt_patch_kind(tmp_path):
    """cap 只数 kind=prompt_patch 的 open 条,不被其它 kind(factor/gate)的 open 提案误挡。"""
    for i in range(5):
        fs.add_proposal("gate", f"不相关的门槛提案{i}")
    target = _mk_target(tmp_path)
    rec = fs.add_prompt_patch(str(target), "锚", "普通文案", "改写", ["证据"])
    assert rec["status"] == "open"                        # 未被 5 条 gate 提案误挡


def test_open_cap_ignores_non_open_status(tmp_path):
    """已批准/拒绝的 prompt_patch 不占 cap 名额。"""
    target = _mk_target(tmp_path)
    ids = []
    for i in range(5):
        rec = fs.add_prompt_patch(str(target), f"锚{i}", "普通文案", f"改写{i}", ["证据"])
        ids.append(rec["id"])
    fs.set_proposal_status(ids[0], "approved")            # 腾出一个名额
    rec = fs.add_prompt_patch(str(target), "锚新", "普通文案", "改写新", ["证据"])
    assert rec["status"] == "open"


# ───────────────────────── 锚集常量本身 ─────────────────────────


def test_contract_anchors_constant_matches_plan_spec():
    """_CONTRACT_ANCHORS 来自 grep 指定的 6 个契约锚,不多不少(防未来悄悄改小/改大集合)。"""
    expected = {
        "卡契约 v3", "超短口径", "机构面网查",
        "FINAL TRANSACTION PROPOSAL", "Rubric建议", "进入P4倾向",
    }
    assert set(fs._CONTRACT_ANCHORS) == expected
