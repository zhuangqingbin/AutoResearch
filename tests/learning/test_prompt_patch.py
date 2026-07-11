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


# ───────────────────────── Task 2 · proposals show/apply 辅助流 ─────────────────────────
# plan: docs/plans/2026-07-11-hermes-selfimprove-plan.md Task 2;
# spec: docs/specs/2026-07-11-recall-gate-pinned-config-design.md §5.1「落地端」。
# show = 只读打印 diff+evidence;apply = 只读打印施工指引(契约文件命中→强制列必跑命令)+
# set_proposal_status 收尾 —— **两者都绝不 Edit/Write target_file**。


def test_show_prints_target_diff_and_evidence(tmp_path):
    target = _mk_target(tmp_path, text="正文:普通文案,无契约锚。")
    rec = fs.add_prompt_patch(
        str(target), "起草小节锚点", "普通文案", "改写后的文案",
        ["同型失误1:07-05 diagnosis X", "同型失误2:07-09 diagnosis X 再现", "gate_ledger ex>0 n=6"],
    )
    out = fs.show_proposal(rec["id"])
    assert str(target) in out                       # target_file 可见
    assert "普通文案" in out and "改写后的文案" in out  # current/proposed 都进了 diff
    assert "-普通文案" in out and "+改写后的文案" in out  # 真是 diff(+/- 标记),非只是原文罗列
    assert "同型失误1:07-05 diagnosis X" in out        # evidence 逐条可见
    assert "gate_ledger ex>0 n=6" in out


def test_show_unknown_pid_raises():
    with pytest.raises(KeyError):
        fs.show_proposal("pr_不存在_001")


def test_show_non_prompt_patch_kind_falls_back_to_raw_fields():
    """非 prompt_patch 提案(如 gate/factor)没有 target_file/diff 结构,show 退化打印原字段不崩。"""
    pr = fs.add_proposal("gate", "cap_floor 30→20 亿", rationale="14 个 missed_l0 卡在 20-30亿",
                         diff_sketch="screen_market 硬门 cap_floor 默认 30 → 20")
    out = fs.show_proposal(pr["id"])
    assert "cap_floor 30→20 亿" in out
    assert "screen_market 硬门 cap_floor 默认 30 → 20" in out
    assert "14 个 missed_l0 卡在 20-30亿" in out


def test_contract_file_set_matches_plan_spec():
    """契约文件集(按 basename 命中)= l4-card.md / lite-playbook.md / 任意 SKILL.md / STAGES.md。"""
    assert fs._is_contract_file(".claude/agents/l4-card.md")
    assert fs._is_contract_file(".claude/skills/stock-research/lite-playbook.md")
    assert fs._is_contract_file(".claude/skills/scan-market/SKILL.md")
    assert fs._is_contract_file(".claude/skills/scan-market/STAGES.md")
    assert not fs._is_contract_file(".claude/skills/scan-retro/retro-playbook.md")


def test_apply_contract_file_lists_mandatory_verification_commands(tmp_path):
    target = tmp_path / "l4-card.md"
    target.write_text("卡契约 v3 相关文案,勿删。", encoding="utf-8")
    rec = fs.add_prompt_patch(
        str(target), "锚点", "卡契约 v3 相关文案,勿删。",
        "卡契约 v3:改写后的更精简文案,勿删。", ["证据"],
    )
    before = target.read_text(encoding="utf-8")
    out = fs.apply_proposal(rec["id"])
    assert "tests/test_agent_defs.py" in out
    assert "tests/test_skill_docs_refs.py" in out
    assert "doc-lint" in out
    assert target.read_text(encoding="utf-8") == before        # 硬约束:文件内容逐字未变


def test_apply_non_contract_file_has_no_mandatory_commands(tmp_path):
    target = _mk_target(tmp_path, name="random_note.md", text="普通文案。")
    rec = fs.add_prompt_patch(str(target), "锚点", "普通文案。", "改写后。", ["证据"])
    before = target.read_text(encoding="utf-8")
    out = fs.apply_proposal(rec["id"])
    assert "test_agent_defs.py" not in out
    assert "test_skill_docs_refs.py" not in out
    assert target.read_text(encoding="utf-8") == before        # 硬约束:文件内容逐字未变


def test_apply_sets_status_applied_and_drops_off_open_board(tmp_path):
    target = _mk_target(tmp_path)
    rec = fs.add_prompt_patch(str(target), "锚", "普通文案", "改写", ["证据"])
    assert rec["id"] in {p["id"] for p in fs.open_proposals()}
    fs.apply_proposal(rec["id"])
    recs = {r["id"]: r for r in fs._read_jsonl(fs._PROPOSALS)}
    assert recs[rec["id"]]["status"] == "applied"
    assert rec["id"] not in {p["id"] for p in fs.open_proposals()}


def test_apply_unknown_pid_raises():
    with pytest.raises(KeyError):
        fs.apply_proposal("pr_不存在_002")


def test_apply_non_prompt_patch_kind_has_no_target_file_instructions():
    """非 prompt_patch 提案没有 target_file,apply 仍不崩、仍不列强制命令、仍收尾改 status。"""
    pr = fs.add_proposal("gate", "cap_floor 30→20 亿", rationale="证据",
                         diff_sketch="screen_market 硬门 cap_floor 默认 30 → 20")
    out = fs.apply_proposal(pr["id"])
    assert "test_agent_defs.py" not in out
    assert fs._read_jsonl(fs._PROPOSALS)[0]["status"] == "applied"
