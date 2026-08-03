"""候选账本单测 —— §0.4 的两张强制表是不是真的会拦人。

每个 test 都对应设计稿里一句「必须」:如果把对应的校验删掉,这里必须变红。
"""
from __future__ import annotations

import json

import pytest

from autoresearch.research import candidates as cd


def _ok(**over) -> cd.Candidate:
    base = {
        "id": "x1", "title": "t", "section": "§9", "change_class": "M",
        "priority": "P0", "status": "OPEN",
        "inheritance": {"Wave9": "无关", "Wave10": "继承", "STAGES": "新增"},
        "falsification_step": "先跑一个不花钱的探针", "probe": "断言 n 会变",
        "rollback": "删文件",
        "cost": cd._cost("1 人日", "10MB", "无", "0", "秒级", "低"),
    }
    base.update(over)
    return cd.Candidate(**base)


# ── 继承矩阵(§0.4-1)───────────────────────────────────────────────


def test_clean_candidate_passes():
    assert cd.validate_one(_ok()) == []


def test_missing_authority_is_rejected():
    bad = cd.validate_one(_ok(inheritance={"Wave10": "继承", "STAGES": "新增"}))
    assert any("继承矩阵缺权威" in p for p in bad)


def test_unknown_relation_is_rejected():
    bad = cd.validate_one(_ok(inheritance={"Wave9": "大概吧", "Wave10": "继承",
                                           "STAGES": "新增"}))
    assert any("不在" in p for p in bad)


def test_unknown_authority_is_rejected():
    bad = cd.validate_one(_ok(inheritance={"Wave9": "无关", "Wave10": "继承",
                                           "STAGES": "新增", "Wave11": "新增"}))
    assert any("未知权威" in p for p in bad)


# ── 变更类别 / registry(§0.4-3 + §5-1)──────────────────────────────


def test_b_class_without_registry_family_is_rejected():
    bad = cd.validate_one(_ok(change_class="B"))
    assert any("registry_family" in p for p in bad)


def test_non_b_class_with_registry_family_is_rejected():
    """挂 family 的 M 类其实是伪装的 B 类 —— 这正是「展示列悄悄进 prompt」的形态。"""
    bad = cd.validate_one(_ok(change_class="M", registry_family="whatever"))
    assert any("其实是 B 类" in p for p in bad)


def test_b_class_implemented_without_registry_experiment_is_rejected(tmp_path):
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({
        "schema_version": 1, "stable_baseline": None, "baseline_history": [],
        "experiments": {}, "active_by_family": {}, "audit": [],
    }), encoding="utf-8")
    item = _ok(change_class="B", status="IMPLEMENTED", registry_family="ghost_family")
    result = cd.validate([item], registry_path=registry)
    assert result["checked_registry"] is True
    assert any("状态机没走过" in p for p in result["problems"]["x1"])


def test_registry_unreadable_skips_check_without_passing_silently(tmp_path):
    broken = tmp_path / "broken.json"
    broken.write_text("{ not json", encoding="utf-8")
    item = _ok(change_class="B", status="IMPLEMENTED", registry_family="ghost")
    result = cd.validate([item], registry_path=broken)
    assert result["checked_registry"] is False        # 明说跳过了,不是"通过"


# ── REJECTED / BLOCKED_BY_DATA 的额外义务 ───────────────────────────


def test_rejected_without_reopen_conditions_is_rejected():
    bad = cd.validate_one(_ok(status="REJECTED"))
    assert any("reopen_conditions" in p for p in bad)


def test_blocked_by_data_without_capability_gate_is_rejected():
    bad = cd.validate_one(_ok(status="BLOCKED_BY_DATA"))
    assert any("capability_gate" in p for p in bad)


# ── §0.3-1 / §5-3:证伪步与探针 ─────────────────────────────────────


@pytest.mark.parametrize("field_name", ["falsification_step", "probe", "rollback"])
def test_empty_required_prose_is_rejected(field_name):
    bad = cd.validate_one(_ok(**{field_name: "  "}))
    assert any(field_name in p for p in bad)


# ── §5-4 成本分栏 ───────────────────────────────────────────────────


def test_missing_cost_column_is_rejected():
    cost = cd._cost("1", "2", "3", "4", "5", "6")
    cost.pop("wallclock")
    bad = cd.validate_one(_ok(cost=cost))
    assert any("成本分栏缺列" in p for p in bad)


@pytest.mark.parametrize("phrase", ["零成本", "可忽略", "negligible"])
def test_banned_cost_phrase_is_rejected(phrase):
    """§5-4 点名删除的措辞不得复活。"""
    cost = cd._cost(f"确定性脚本 {phrase}", "10MB", "无", "0", "秒级", "低")
    bad = cd.validate_one(_ok(cost=cost))
    assert any(phrase in p for p in bad)


def test_unknown_cost_column_is_rejected():
    cost = cd._cost("1", "2", "3", "4", "5", "6")
    cost["vibes"] = "good"
    bad = cd.validate_one(_ok(cost=cost))
    assert any("未知列" in p for p in bad)


# ── 真实候选池 ──────────────────────────────────────────────────────


def test_shipped_ledger_is_clean():
    result = cd.validate()
    assert result["ok"], result["problems"]


def test_duplicate_ids_are_caught():
    result = cd.validate([_ok(), _ok()])
    assert any("id 重复" in p for p in result["problems"]["x1"])


def test_every_doc_section_topic_is_covered():
    """设计稿深写的六块必须都有候选在册 —— 漏一块就是「没转 plan」。"""
    sections = {c.section.split("-")[0] for c in cd.CANDIDATES}
    for required in ("§1.1", "§1.2", "§1.3", "§1.4", "§2.2", "§2.3", "§2.4",
                     "§3.1", "§3.2", "§3.3", "§3.4", "§3.5",
                     "§4.1", "§4.2", "§4.3", "§4.4", "§4.5"):
        assert required in sections, f"设计稿 {required} 无候选在册"


def test_o2_stays_rejected_with_reopen_conditions():
    """§3.2:O2 的近期候选资格已删除,只保留可重开条件。"""
    o2 = next(c for c in cd.CANDIDATES if c.id == "O2_dist_high_252")
    assert o2.status == "REJECTED"
    assert "OOS" in o2.reopen_conditions and "no-harm" in o2.reopen_conditions


def test_f3_is_blocked_by_data_with_four_gates():
    f3 = next(c for c in cd.CANDIDATES if c.id == "F3_cb_factors")
    assert f3.status == "BLOCKED_BY_DATA" and len(f3.capability_gate) == 4


def test_nested_dispatch_declares_user_reruling_against_wave9():
    """§5-5:4.3 与 Wave9 的 L4 调度直接重叠,不得再声明「无重叠」。"""
    g43 = next(c for c in cd.CANDIDATES if c.id == "G43_nested_l4_dispatch")
    assert g43.inheritance["Wave9"] == "需用户重裁"


def test_blocked_by_points_at_real_candidates():
    ids = {c.id for c in cd.CANDIDATES}
    for c in cd.CANDIDATES:
        for dep in c.blocked_by:
            assert dep in ids, f"{c.id} 依赖了不存在的候选 {dep}"


def test_render_includes_every_candidate_and_flags_problems():
    md = cd.render()
    for c in cd.CANDIDATES:
        assert c.id in md
    bad_md = cd.render([_ok(change_class="B")])
    assert "⚠️" in bad_md and "registry_family" in bad_md


def test_cli_check_exits_nonzero_on_violation(monkeypatch, capsys):
    monkeypatch.setattr(cd, "CANDIDATES", (_ok(change_class="B"),))
    assert cd.main(["--check"]) == 1


def test_cli_writes_report_and_json(tmp_path):
    out, jp = tmp_path / "r.md", tmp_path / "r.json"
    assert cd.main(["--out", str(out), "--json", str(jp)]) == 0
    assert out.exists() and "继承矩阵" in out.read_text(encoding="utf-8")
    payload = json.loads(jp.read_text(encoding="utf-8"))
    assert len(payload["candidates"]) == len(cd.CANDIDATES)
