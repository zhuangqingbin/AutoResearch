"""提案看板自清洁(annotate_open_proposals + proposals_nag_lines):机器只整理,不裁决。

三件标注:age_days/stale(>14 天积压)、pair_with(summary/rationale 引用另一条 **open**
提案 id → 提示「一起收」,真实案例 pr_20260714_003 ↔ pr_20260624_001)、maybe_moot(命中
已退役机制词表,仅提示人判)。渲染:🚨/P0 → 有配对 → stale → 龄大 排序 + max_lines 截断。
隔离:set_root(tmp)(照 test_prompt_patch.py),不碰真实 context/knowledge。
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


def _rec(pid, ts, status="open", kind="gate", summary="s", rationale=""):
    return {"id": pid, "ts": ts, "kind": kind, "summary": summary,
            "rationale": rationale, "diff_sketch": "", "status": status}


# ───────────────────────── annotate:龄 + stale ─────────────────────────


def test_age_days_and_stale_threshold():
    recs = [_rec("pr_20260701_001", "2026-07-01T10:00:00"),   # 14 天 → 不 stale(边界)
            _rec("pr_20260630_001", "2026-06-30T10:00:00")]   # 15 天 → stale
    anns = {a["id"]: a for a in fs.annotate_open_proposals(recs, today="2026-07-15")}
    assert anns["pr_20260701_001"]["age_days"] == 14
    assert anns["pr_20260701_001"]["stale"] is False
    assert anns["pr_20260630_001"]["age_days"] == 15
    assert anns["pr_20260630_001"]["stale"] is True
    # 原字段保留(标注是叠加,不是重建)
    assert anns["pr_20260701_001"]["kind"] == "gate" and anns["pr_20260701_001"]["summary"] == "s"


def test_annotate_filters_to_open_only():
    recs = [_rec("pr_20260701_001", "2026-07-01T10:00:00", status="resolved"),
            _rec("pr_20260702_001", "2026-07-02T10:00:00", status="open")]
    anns = fs.annotate_open_proposals(recs, today="2026-07-10")
    assert [a["id"] for a in anns] == ["pr_20260702_001"]


# ───────────────────────── annotate:配对检测 ─────────────────────────


def test_pair_with_detects_reference_to_another_open_proposal():
    # 真实案例形状:pr_20260714_003 的 summary 引用 pr_20260624_001(裁决建议),两条都 open
    recs = [_rec("pr_20260624_001", "2026-06-24T09:00:00", summary="小盘漏判 cap_floor 软化"),
            _rec("pr_20260714_003", "2026-07-14T20:00:00",
                 summary="pr_20260624_001(cap_floor 30→20亿)裁决建议:拒绝")]
    anns = {a["id"]: a for a in fs.annotate_open_proposals(recs, today="2026-07-16")}
    assert anns["pr_20260714_003"]["pair_with"] == "pr_20260624_001"
    assert anns["pr_20260624_001"]["pair_with"] is None       # 检测按引用方向记,被引方不标


def test_pair_with_excludes_self_and_non_open():
    recs = [
        # rationale 里只出现自己的 id → 不算配对
        _rec("pr_20260710_001", "2026-07-10T09:00:00", rationale="见 pr_20260710_001 附录"),
        # 引用的 id 在账本里但已 rejected → 不算配对;引用不存在的 id → 也不算
        _rec("pr_20260711_001", "2026-07-11T09:00:00",
             summary="接 pr_20260601_001 与 pr_20260101_999 的读数"),
        _rec("pr_20260601_001", "2026-06-01T09:00:00", status="rejected"),
    ]
    anns = {a["id"]: a for a in fs.annotate_open_proposals(recs, today="2026-07-16")}
    assert anns["pr_20260710_001"]["pair_with"] is None
    assert anns["pr_20260711_001"]["pair_with"] is None


def test_pair_with_scans_rationale_too():
    recs = [_rec("pr_20260712_001", "2026-07-12T09:00:00"),
            _rec("pr_20260714_001", "2026-07-14T09:00:00",
                 rationale="承接 pr_20260712_001 的观测结论")]
    anns = {a["id"]: a for a in fs.annotate_open_proposals(recs, today="2026-07-16")}
    assert anns["pr_20260714_001"]["pair_with"] == "pr_20260712_001"


# ───────────────────────── annotate:moot 词表 ─────────────────────────


def test_maybe_moot_wordlist_hits():
    recs = [_rec("pr_20260714_001", "2026-07-14T09:00:00", summary="carryover 洗白保送身份"),
            _rec("pr_20260714_002", "2026-07-14T09:00:00", rationale="观察单退役后此路失效"),
            _rec("pr_20260714_003", "2026-07-14T09:00:00", summary="T+5 方向已作废,fwd_5_oc 同"),
            _rec("pr_20260714_004", "2026-07-14T09:00:00", summary="与退役机制无关的干净提案")]
    anns = {a["id"]: a for a in fs.annotate_open_proposals(recs, today="2026-07-16")}
    assert anns["pr_20260714_001"]["maybe_moot"] == ["carryover"]
    assert anns["pr_20260714_002"]["maybe_moot"] == ["观察单"]
    assert set(anns["pr_20260714_003"]["maybe_moot"]) == {"T+5", "fwd_5"}
    assert anns["pr_20260714_004"]["maybe_moot"] == []        # 无命中 → 空(falsy,不标旗)


def test_annotate_reads_ledger_and_skips_bad_lines():
    p = fs._f(fs._PROPOSALS)
    p.parent.mkdir(parents=True, exist_ok=True)
    import json
    p.write_text(json.dumps(_rec("pr_20260714_001", "2026-07-14T09:00:00")) + "\n"
                 + "not-json\n"
                 + json.dumps(_rec("pr_20260715_001", "2026-07-15T09:00:00", status="applied"))
                 + "\n", encoding="utf-8")
    anns = fs.annotate_open_proposals(today="2026-07-16")
    assert [a["id"] for a in anns] == ["pr_20260714_001"]     # 坏行跳过、非 open 不入


# ───────────────────────── nag 行:排序 + 截断 + 格式 ─────────────────────────


def _seed_ledger(recs):
    for r in recs:
        fs._append_jsonl(fs._PROPOSALS, r)


def test_nag_lines_sorted_urgent_pair_stale():
    _seed_ledger([
        _rec("pr_20260626_001", "2026-06-26T09:00:00",
             summary="旧积压提案甲(carryover 残留)"),                    # stale 20d + moot
        _rec("pr_20260715_001", "2026-07-15T09:00:00", kind="data",
             summary="🚨 情报编造事实"),                                  # urgent, 1d
        _rec("pr_20260714_001", "2026-07-14T09:00:00",
             summary="接 pr_20260713_001 的裁决建议:拒绝"),               # pair, 2d
        _rec("pr_20260713_001", "2026-07-13T09:00:00", summary="cap_floor 门"),   # 素票 3d
        _rec("pr_20260716_001", "2026-07-16T09:00:00", kind="data",
             summary="P0 权重重标定 NO-OP"),                              # urgent, 0d
    ])
    lines = fs.proposals_nag_lines(today="2026-07-16")
    order = [ln.split("`")[1] for ln in lines]                 # 行首 `id` 反解
    assert order == ["pr_20260715_001", "pr_20260716_001",    # 🚨/P0 在前(同组内龄大先)
                     "pr_20260714_001",                        # 有配对的
                     "pr_20260626_001",                        # stale 的
                     "pr_20260713_001"]                        # 其余
    pair_line = lines[2]
    assert "[gate·2d·↔pr_20260713_001]" in pair_line           # kind·龄·配对 标注格式
    assert "疑失效:carryover" in lines[3]                       # moot 旗进行内


def test_nag_lines_truncation_and_tail_count():
    _seed_ledger([_rec(f"pr_2026070{i}_001", f"2026-07-0{i}T09:00:00", summary=f"提案{i}")
                  for i in range(1, 6)])                       # 5 条 open
    lines = fs.proposals_nag_lines(max_lines=3, today="2026-07-16")
    assert len(lines) == 4                                     # 3 行 + 计数尾行
    assert lines[-1] == "- …共 5 条 open"
    assert all(ln.startswith("- `pr_") for ln in lines[:3])


def test_nag_lines_summary_cut_at_40():
    long = "四十个字符截断测试" * 6                              # 54 字
    _seed_ledger([_rec("pr_20260714_001", "2026-07-14T09:00:00", summary=long)])
    (line,) = fs.proposals_nag_lines(today="2026-07-16")
    assert long[:40] + "…" in line
    assert long not in line                                    # 全文不落行


def test_nag_lines_empty_or_missing_ledger():
    assert fs.proposals_nag_lines(today="2026-07-16") == []    # 账本缺 → []
    _seed_ledger([_rec("pr_20260714_001", "2026-07-14T09:00:00", status="resolved")])
    assert fs.proposals_nag_lines(today="2026-07-16") == []    # 无 open → []
