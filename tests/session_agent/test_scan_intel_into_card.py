"""情报正文进卡片输入(2026-10-03 A11)。

10-02 session_v1 首跑:11 份情报员产出没有一份被决策卡读到 —— 卡只拿到 `intel_status`(断言 id +
哈希、全 UNKNOWN、无原文),two-stage 的 `intel_bundle` 里正文是 base64。legacy 卡读的是守卫后的
`_l4_intel_<code>.md`;这里把同一份守卫后正文按 attempt 冻结成 `intel_doc`,登记为卡的输入。
情报关着时不登记(不是空文件)。
"""
from __future__ import annotations

from pathlib import Path

from autoresearch.session_agent.workflows import scan

from .test_scan_prelude import context, request as scan_request
from .test_service import _handle

SNAP = [{"artifact_id": "scan.finalists", "sha256": "a" * 64}]


def _tasks(tmp_path, *, intel_enabled: bool) -> dict[str, dict]:
    plan = scan.build_scan_plan(scan_request(), context(tmp_path))
    result = scan.l4_expansion(plan, {"mode": "FULL"}, [{"code": "600519"}], SNAP,
                               intel_enabled=intel_enabled)
    return {t["task_id"]: t for t in result["tasks"]}


def test_card_reads_the_guarded_intel_body_when_intel_is_enabled(tmp_path):
    tasks = _tasks(tmp_path, intel_enabled=True)
    assert "scan.l4.600519.a1.intel_doc" in tasks["l4.600519.a1.intel_status"]["output_artifact_ids"]
    assert "scan.l4.600519.a1.intel_doc" in tasks["l4.600519.a1.card"]["input_artifact_ids"]


def test_no_intel_doc_is_registered_when_intel_is_disabled(tmp_path):
    tasks = _tasks(tmp_path, intel_enabled=False)
    assert not any(a.endswith(".intel_doc") for t in tasks.values()
                   for a in t["input_artifact_ids"] + t["output_artifact_ids"])


def test_a_retry_card_reads_its_own_attempts_intel_doc(tmp_path):
    plan = scan.build_scan_plan(scan_request(), context(tmp_path))
    result = scan.l4_retry_expansion(plan, "600519", 2, SNAP, intel_enabled=True)
    tasks = {t["task_id"]: t for t in result["tasks"]}
    status = next(t for name, t in tasks.items() if name.endswith(".intel_status"))
    card = next(t for name, t in tasks.items() if name.endswith(".card"))
    assert "scan.l4.600519.a2.intel_doc" in status["output_artifact_ids"]
    assert "scan.l4.600519.a2.intel_doc" in card["input_artifact_ids"]


def test_intel_doc_is_frozen_per_attempt(tmp_path):
    handle = _handle(tmp_path)
    staging = Path(handle.staging)
    plan = scan.build_scan_plan(scan_request(), handle)
    first_task = next(t for t in scan.l4_expansion(plan, {"mode": "FULL"}, [{"code": "600519"}], SNAP,
                                                   intel_enabled=True)["tasks"]
                      if t["task_id"].endswith(".intel_status"))
    retry_task = next(t for t in scan.l4_retry_expansion(plan, "600519", 2, SNAP,
                                                         intel_enabled=True)["tasks"]
                      if t["task_id"].endswith(".intel_status"))
    first, mode = scan._paths_for_artifact(handle, first_task, "scan.l4.600519.a1.intel_doc")
    second, _ = scan._paths_for_artifact(handle, retry_task, "scan.l4.600519.a2.intel_doc")
    assert first == staging / "session_attempts" / "600519" / "a1" / "intel_doc.md"
    assert second == staging / "session_attempts" / "600519" / "a2" / "intel_doc.md"
    assert mode == "WRITE"                                  # 情报状态任务写它,卡任务读它


def test_the_status_operation_writes_the_guarded_copy_into_the_attempt(tmp_path, monkeypatch):
    from autoresearch.session_agent import domain_ops

    handle = context(tmp_path)
    staging = Path(handle.staging)
    bound = staging / "session_attempts" / "600519" / "a1" / "intel.md"
    bound.parent.mkdir(parents=True, exist_ok=True)
    bound.write_text("raw agent bytes\n", encoding="utf-8")

    def fake_guard(scan_dir, code6, **_kw):
        (Path(scan_dir) / f"_l4_intel_{code6}.md").write_text("guarded body\n", encoding="utf-8")
        return {"action": "KEPT"}

    import autoresearch.scan.l4.intel_guard as guard_mod
    monkeypatch.setattr(guard_mod, "guard_intel", fake_guard)
    monkeypatch.setattr(domain_ops, "_normalize_intel", lambda *_a, **_k: None)
    monkeypatch.setattr("autoresearch.session_agent.source_fields.intel_source_context",
                        lambda _scan: {})
    monkeypatch.setattr("autoresearch.scan.l4.intel_status.from_guard",
                        lambda *a, **k: type("S", (), {"to_dict": lambda self: {}})())
    monkeypatch.setattr("autoresearch.scan.l4.intel_status.write_status", lambda *a, **k: None)
    monkeypatch.setattr(domain_ops, "_copy_attempt_status", lambda *a, **k: None)

    domain_ops.scan_l4_intel_status(handle, code="600519")

    doc = staging / "session_attempts" / "600519" / "a1" / "intel_doc.md"
    assert doc.read_text(encoding="utf-8") == "guarded body\n"       # 守卫后的,不是原始字节
    assert bound.read_text(encoding="utf-8") == "raw agent bytes\n"  # 绑定的原件不动
