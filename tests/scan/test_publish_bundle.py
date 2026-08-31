"""发布包(summary + appendix)的**完整性语义**(§6.2 / §6.3 / §6.4 / Q8)。

design: docs/specs/2026-08-28-summary-slimdown-design.md

L5 的产物从「一份 summary」变成「一个发布包」。这带来一个新的失败模式:**半个包**
—— summary 是这次的、appendix 是上一次的(或干脆没有)。本文件把三件事钉死:

1. **两份都非空才动盘**(各写 `.tmp` 再 `os.replace`);任一份渲染/写入失败 → 不留半成品、
   **不回写评级/BUY**(展示故障不许伪装成研究结论变化),保留 staging 供只重跑 L5;
2. **旧 run 不追责**:appendix 是 contract 门控产物,`run_contract.artifact_schema_versions`
   里没记它的 run(legacy)不计 missing;记了的 run 缺席必红;
3. **失败后重跑不重算评级** —— 决策落盘在 prepare 阶段已完成,补发布不动它。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoresearch.scan import artifacts as A
from autoresearch.scan.publisher import ReportBundleError, _write_report_bundle


# ── 1. 双写与失败语义 ────────────────────────────────────────────────────

def test_bundle_writes_both_halves(tmp_path):
    s, a = _write_report_bundle(tmp_path, "决策层\n", "现场层\n")
    assert s.read_text(encoding="utf-8") == "决策层\n"
    assert a.read_text(encoding="utf-8") == "现场层\n"
    assert not list(tmp_path.glob("*.tmp")), "临时文件没清干净"


@pytest.mark.parametrize("summary,appendix", [
    ("决策层\n", ""),          # appendix 渲染成空
    ("决策层\n", "   \n"),     # 只有空白
    ("", "现场层\n"),          # summary 渲染成空
    ("决策层\n", None),        # 渲染器返回了 None
])
def test_empty_half_refuses_to_publish(tmp_path, summary, appendix):
    """「appendix 缺席但 summary 成功」不算发布完成(Q8)—— 拒绝,而不是发半个包。"""
    with pytest.raises(ReportBundleError):
        _write_report_bundle(tmp_path, summary, appendix)


def test_a_failed_bundle_never_replaces_the_previous_publish(tmp_path):
    """第二份写不出来时,第一份也还没顶替旧文件 —— 不留「新 summary + 旧 appendix」。"""
    (tmp_path / "summary.md").write_text("上一次的决策层\n", encoding="utf-8")
    (tmp_path / "appendix.md").write_text("上一次的现场层\n", encoding="utf-8")
    with pytest.raises(ReportBundleError):
        _write_report_bundle(tmp_path, "这一次的决策层\n", "")
    assert (tmp_path / "summary.md").read_text(encoding="utf-8") == "上一次的决策层\n"
    assert (tmp_path / "appendix.md").read_text(encoding="utf-8") == "上一次的现场层\n"
    assert not list(tmp_path.glob("*.tmp"))


def test_failed_publish_does_not_touch_decision_products(tmp_path):
    """发布失败 = **展示层**失败:评级/决策产物一个字节不动,重跑 L5 即可补齐。"""
    ratings = tmp_path / "_final_ratings.json"
    ratings.write_text(json.dumps({"000001": "Hold"}, ensure_ascii=False), encoding="utf-8")
    before = ratings.read_bytes()
    with pytest.raises(ReportBundleError):
        _write_report_bundle(tmp_path, "决策层\n", "")
    assert ratings.read_bytes() == before


# ── 2. 产物登记与旧 run 兼容 ─────────────────────────────────────────────

def test_appendix_is_registered_as_an_assemble_artifact():
    spec = next((s for s in A.CRITICAL_ARTIFACTS if s.name == "appendix"), None)
    assert spec is not None, "appendix 没进 ArtifactSpec —— 发布包只登记了一半"
    assert (spec.producer, spec.path, spec.root) == ("assemble", "appendix.md", "report")


def test_appendix_is_contract_gated_not_globally_required():
    """§6.4:appendix 的义务来自 **run 自己的 contract**,不是当前全局 registry。"""
    assert "appendix" in A.CONTRACT_GATED_ARTIFACTS
    assert "appendix" in A.artifact_schema_versions()


@pytest.mark.parametrize("contract,expect_required", [
    (None, False),                                            # legacy:契约读不到
    ({}, False),                                              # legacy:空 map
    ({"artifact_schema_versions": {}}, False),                # legacy:记了表但没这一项
    ({"artifact_schema_versions": {"summary": 1}}, False),    # 同上
    ({"artifact_schema_versions": {"summary": 1, "appendix": 1}}, True),
])
def test_legacy_runs_are_not_retroactively_in_breach(contract, expect_required):
    """旧 run 不因今天新增产物而变红;新 run 缺 appendix 必红。

    「历史现场按当时契约解释,不能倒灌今天的义务」—— 否则每加一个产物,所有历史 run 的
    完整性结论都会集体翻车,而它们当时明明是完整的。
    """
    names = {spec.name for spec in A.expected_specs(contract)}
    assert ("appendix" in names) is expect_required
    # 反面锚:非门控产物任何情况下都必须在,否则这条用例会因为"全都不在"而假绿。
    assert "summary" in names and "manifest" in names


# ── 3. replay 把发布包当一个整体 ─────────────────────────────────────────

def test_l5_replay_spec_covers_the_whole_bundle():
    from autoresearch.trace import replay as R

    spec = {s.stage: s for s in R.default_stage_specs("2026-08-29")}["l5"]
    assert spec.outputs == ("summary.md", "appendix.md"), \
        "只比对 summary 会让「附录没重现」在 replay 结论里完全不可见"
