"""memory 写侧卫生 M3(失效记账)+ M2(写入四操作裁决 ADD/UPDATE/DELETE/NOOP)。

M3:lesson 加 valid_from/invalid_at/superseded_by,退休不删(留时点审计)。
M2:落 lesson 前 similar_lessons 结构化召回相似旧条 → Claude 判 op → adjudicate 确定性执行。

spec: docs/specs/2026-07-07-memory-astrategy-optimization-design.md §M2 §M3
"""
from __future__ import annotations

import pytest

import autoresearch.learning.feedback_store as fs

_G = [("global", "*")]


@pytest.fixture(autouse=True)
def _tmp_store(tmp_path):
    old = fs.KNOW
    fs.set_root(tmp_path)
    yield
    fs.set_root(old)


# ─────────────────────────── M3 · 失效记账 ───────────────────────────


def test_valid_from_set_on_create():
    fs.upsert_lesson("a", ("global", "*"), rule="r", evidence=[], day="2026-06-01")
    rec = fs._read_jsonl(fs._LESSONS)[0]
    assert rec["valid_from"] == "2026-06-01" == rec["created"]


def test_retire_sets_invalid_at():
    fs.upsert_lesson("a", ("global", "*"), rule="r", evidence=[], day="2026-06-01")
    fs.retire_lesson("a", day="2026-06-20")
    rec = fs._read_jsonl(fs._LESSONS)[0]
    assert rec["status"] == "retired" and rec["invalid_at"] == "2026-06-20"


def test_decay_autoretire_sets_invalid_at():
    fs.upsert_lesson("s", ("global", "*"), rule="r", evidence=[], confidence=0.35, day="2026-01-01")
    fs.decay_lessons(today="2026-06-20", stale_days=30)
    rec = fs._read_jsonl(fs._LESSONS)[0]
    assert rec["status"] == "retired" and rec["invalid_at"] == "2026-06-20"


# ─────────────────────────── M2 · 四操作裁决 ───────────────────────────


def test_similar_lessons_ranks_scope_and_text():
    fs.upsert_lesson("semi_overheat", ("industry", "半导体"),
                     rule="半导体板块过热,高位追涨次日回落", evidence=[])
    fs.upsert_lesson("value_weak", ("global", "*"), rule="低PE价值股T+1偏弱", evidence=[])
    hits = fs.similar_lessons("半导体高位追涨风险,过热回避", ("industry", "半导体"), k=2)
    assert hits[0]["id"] == "ls_semi_overheat"       # 同 scope + 高文本重合 → 排第一


def test_adjudicate_add_creates_new():
    r = fs.adjudicate("ADD", {"slug": "newrule", "scope": ("global", "*"),
                              "rule": "新规则", "evidence": ["e1"]}, day="2026-06-05")
    assert r["id"] == "ls_newrule" and r["status"] == "active"
    assert {x["id"] for x in fs.lessons_for(_G)} == {"ls_newrule"}


def test_adjudicate_noop_leaves_store_unchanged():
    fs.upsert_lesson("x", ("global", "*"), rule="r", evidence=["e"], day="2026-06-01")
    before = fs._read_jsonl(fs._LESSONS)
    fs.adjudicate("NOOP", {"slug": "dup", "scope": ("global", "*"),
                           "rule": "r2", "evidence": ["e2"]}, target_id="ls_x")
    assert fs._read_jsonl(fs._LESSONS) == before      # 重复 → 不动


def test_adjudicate_update_folds_into_target_preserving_account():
    fs.upsert_lesson("x", ("global", "*"), rule="旧文本", evidence=["e1"], confidence=0.6, day="2026-06-01")
    fs.mtm_update("x", "support", day="2026-06-02")   # 先建立 MTM 账
    r = fs.adjudicate("UPDATE", {"slug": "cand", "scope": ("global", "*"),
                                 "rule": "改写后的更准文本", "evidence": ["e2"]},
                      target_id="ls_x", day="2026-06-05")
    assert r["id"] == "ls_x"                            # 保 id
    assert r["rule"] == "改写后的更准文本"              # 文本被改写(非只动分)
    assert set(r["evidence"]) == {"e1", "e2"}          # evidence 并集
    assert r["reinforce_count"] == 2                    # 强化++
    assert r["mtm"]["support"] == 1                     # MTM 账保留
    assert {x["id"] for x in fs.lessons_for(_G)} == {"ls_x"}   # candidate 自身 slug 不入库


def test_adjudicate_delete_supersedes_old():
    fs.upsert_lesson("old", ("global", "*"), rule="旧的错误认知", evidence=["e1"], day="2026-06-01")
    fs.adjudicate("DELETE", {"slug": "corrected", "scope": ("global", "*"),
                             "rule": "纠正后的认知", "evidence": ["e2"]},
                  target_id="ls_old", day="2026-06-05")
    recs = {x["id"]: x for x in fs._read_jsonl(fs._LESSONS)}
    assert recs["ls_old"]["status"] == "retired"              # 旧条失效(不物理删)
    assert recs["ls_old"]["invalid_at"] == "2026-06-05"
    assert recs["ls_old"]["superseded_by"] == "ls_corrected"  # 指向替代条
    assert recs["ls_corrected"]["status"] == "active"         # 新条活跃
    assert {x["id"] for x in fs.lessons_for(_G)} == {"ls_corrected"}


def test_adjudicate_appends_changelog_audit():
    fs.adjudicate("ADD", {"slug": "n", "scope": ("global", "*"), "rule": "r", "evidence": []}, day="2026-06-05")
    cl = fs._read_jsonl(fs._CHANGELOG)
    assert any(c.get("kind") == "lesson_adjudicate" and c.get("op") == "ADD" for c in cl)


def test_adjudicate_rejects_unknown_op():
    with pytest.raises(ValueError):
        fs.adjudicate("MERGE", {"slug": "x", "scope": ("global", "*"), "rule": "r", "evidence": []})
