"""proposals show/apply 的只读语义(prompt_patch 退役后的存活契约)。

来历:本文件承接 `tests/learning/test_prompt_patch.py`(随 prompt_patch 管线于 2026-08-13
一同删除,spec `docs/specs/2026-08-13-retro-skill-selfmodify-removal-design.md` §4.2)顺带
锁着的**存活不变量**——删退役特性的 test 会静默孤立它兼职守着的 live 契约,所以这四项
逐字移植过来,只把造数据的入口从 `add_prompt_patch` 换成通用 `add_proposal`。

其中「apply 绝不写文件」在退役后**更加载重**:它是「python 侧永不写 .claude/」这条设计
不变量的唯一运行时测试(其余靠 §6 静态探针)。
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


# ───────────────── ① 硬约束:apply 绝不写 target 文件 ─────────────────


def test_apply_never_writes_the_file_it_names(tmp_path):
    """提案正文点名了某文件,apply 也不许碰它——磁盘内容逐字不变(安全边界,非实现细节)。"""
    target = tmp_path / "l4-card.md"
    target.write_text("卡契约 v3 相关文案,勿删。", encoding="utf-8")
    before = target.read_text(encoding="utf-8")

    pr = fs.add_proposal("prompt_rule", f"改写 {target} 的某段文案",
                         rationale="同型失误 2 次", diff_sketch=f"target_file: {target}")
    fs.apply_proposal(pr["id"])

    assert target.read_text(encoding="utf-8") == before


def test_apply_states_it_changes_no_files(tmp_path):
    """指引须自证「本命令不改任何文件」——这句话是给读指引的人的安全承诺,不能悄悄消失。"""
    pr = fs.add_proposal("gate", "cap_floor 30→20 亿", rationale="证据")
    out = fs.apply_proposal(pr["id"])
    assert "不改任何文件" in out


# ───────────────── ② apply 收尾:置 applied 并退出看板 ─────────────────


def test_apply_sets_status_applied_and_drops_off_open_board():
    pr = fs.add_proposal("gate", "cap_floor 30→20 亿", rationale="证据")
    assert pr["id"] in {p["id"] for p in fs.open_proposals()}

    fs.apply_proposal(pr["id"])

    recs = {r["id"]: r for r in fs._read_jsonl(fs._PROPOSALS)}
    assert recs[pr["id"]]["status"] == "applied"
    assert pr["id"] not in {p["id"] for p in fs.open_proposals()}


# ───────────────── ③ show:通用字段可读,不崩 ─────────────────


def test_show_prints_summary_rationale_and_diff_sketch():
    pr = fs.add_proposal("gate", "cap_floor 30→20 亿", rationale="14 个 missed_l0 卡在 20-30亿",
                         diff_sketch="screen_market 硬门 cap_floor 默认 30 → 20")
    out = fs.show_proposal(pr["id"])
    assert "cap_floor 30→20 亿" in out
    assert "screen_market 硬门 cap_floor 默认 30 → 20" in out
    assert "14 个 missed_l0 卡在 20-30亿" in out


def test_show_survives_legacy_prompt_patch_json_payload():
    """历史 prompt_patch 行(diff_sketch 是 JSON payload)不迁移不改写——show 仍须可读不崩。"""
    legacy = ('{"target_file": ".claude/agents/l3-rank.md", "anchor_text": "## 选股硬约束", '
              '"current_text": "旧文案", "proposed_text": "新文案"}')
    pr = fs.add_proposal("prompt_patch", "历史遗留提案", rationale="证据", diff_sketch=legacy)
    out = fs.show_proposal(pr["id"])
    assert "历史遗留提案" in out
    assert ".claude/agents/l3-rank.md" in out          # payload 原文仍可读


# ───────────────── ④ 未知 pid ─────────────────


def test_show_unknown_pid_raises():
    with pytest.raises(KeyError):
        fs.show_proposal("pr_不存在_001")


def test_apply_unknown_pid_raises():
    with pytest.raises(KeyError):
        fs.apply_proposal("pr_不存在_002")
