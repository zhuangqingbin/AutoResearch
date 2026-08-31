"""D-5 两个 materializer 的**接线**,以及 finalize 传不传 run mode。

spec: `docs/specs/2026-08-29-full-coverage-research-system-brainstorm.md` §1.3(建成未接线)
      · §2.2 K3(五份「该有什么」登记表互不派生)

两件事在这里一起锁,因为它们是同一个病的两半 ——「模块建成了但生产零调用者」与
「参数写好了但调用点从不传」:

* `trace/web_budget.py` / `trace/evidence_index.py` 有模块、有测试,`autoresearch/`
  内**零调用者** → 真跑既没有 `web_budget.json` 也没有 `external_evidence_index.json`。
  它俩的单测全绿,因为测试自己调 materializer;没有一条测试问过「finalize 会不会调它」。
* `capsule.finalize` 调 `scan_profile(business_status=…, last_stage=…)` **从不传 mode**
  → 哨兵趟(没有 L4 腿)的 `l4-card` / `l4-intel` 被标 REQUIRED,完整性对哨兵趟恒假。

零网络:沿用 `tests/trace/conftest.py` 的 `codex_run` 夹具(临时根 + 假 identity 快照)。
"""

from __future__ import annotations

import json

import pytest

from autoresearch.common import workspace as ws
from autoresearch.data import contracts as data_contracts
from autoresearch.trace import evidence_index as ei, web_budget as wb
from autoresearch.trace.capsule import (
    BusinessStatus,
    checkpoint,
    finalize,
    read_manifest,
    verify_manifest,
)

USAGE_BUDGET = "usage/web_budget.json"
LINEAGE_BUDGET = f"lineage/{wb.BUDGET_NAME}"
EVIDENCE_INDEX = f"lineage/{ei.INDEX_NAME}"


@pytest.fixture
def finalizable(codex_run, tmp_path):
    """一个可 finalize 的 run —— 与 `test_finalization.finalizable` 同款,不另造夹具栈。"""
    handle, source = codex_run
    report_dir = ws.reports_root() / "scan" / handle.run_id
    report_dir.mkdir(parents=True)
    (report_dir / "summary.md").write_text("# synthetic summary\n", encoding="utf-8")
    checkpoint(handle.run_id, "l3", "SUCCEEDED", [], {"finalists": 3})
    # 降级账本是**进程级**累积的:两头都清,既不读到别人的记录,也不把自己的留给别人。
    data_contracts.clear_degradations()
    yield handle, report_dir, source
    data_contracts.clear_degradations()


def _expected_items(capsule) -> dict:
    payload = json.loads((capsule / "verification/expected.json").read_text(encoding="utf-8"))
    return {item["selector"]: item for item in payload["items"]}


def _endpoints() -> set[str]:
    return {row["endpoint"] for row in data_contracts.degradations()}


def test_finalize_materializes_web_budget_and_evidence_index(finalizable):
    """真跑之后这两份产物必须在 capsule 里 —— 此前一次真跑都没有过。

    第三条断言是位置:两步必须跑在 MANIFEST **之前**,清单才盖得住它俩;
    「产出了但清单不认」= 事后塞进去的证据,与没有一样。
    """
    handle, report_dir, _ = finalizable

    result = finalize(handle.run_id, BusinessStatus.SUCCEEDED, report_dir)

    listed = read_manifest(result.final_path)
    for relative in (USAGE_BUDGET, LINEAGE_BUDGET, EVIDENCE_INDEX):
        assert (handle.capsule / relative).is_file(), f"finalize 没有产出 {relative}"
        assert (report_dir / "capsule" / relative).is_file(), f"发布的 capsule 缺 {relative}"
        assert f"capsule/{relative}" in listed, f"MANIFEST 没盖住 {relative}"
    assert verify_manifest(result.final_path)["ok"] is True


def test_the_two_web_budget_copies_are_the_same_bytes(finalizable):
    """`usage/` 是 T4 生产 lint 的读点,`lineage/` 是设计稿 §6.1 的原址。

    两处必须是同一份 canonical 字节 —— 否则「真值路」读到的是第二个真身,
    而两侧各自的测试会双双全绿(FN-1 家训)。
    """
    handle, report_dir, _ = finalizable

    finalize(handle.run_id, BusinessStatus.SUCCEEDED, report_dir)

    assert (handle.capsule / USAGE_BUDGET).read_bytes() == (
        handle.capsule / LINEAGE_BUDGET
    ).read_bytes()


def test_web_budget_is_readable_by_the_production_lint(finalizable):
    """`self_review.read_web_budget` 严格按 §6.2 的 11 个**顶层**键读它。

    接线接了、schema 不对,消费者仍会退回稿件自报路 —— 那正是要修的病本身。
    """
    from autoresearch.scan import self_review

    handle, report_dir, _ = finalizable
    finalize(handle.run_id, BusinessStatus.SUCCEEDED, report_dir)

    obj, why = self_review.read_web_budget(report_dir / "capsule" / USAGE_BUDGET)

    assert obj is not None, why


def test_finalize_passes_run_mode_to_profile(finalizable):
    """staging 有 `run_mode.json` → 完整性按**那个模式**展开,不再恒按 FULL。"""
    handle, report_dir, _ = finalizable
    (handle.staging / "run_mode.json").write_text(
        json.dumps({"schema_version": 1, "mode": "SENTINEL_EMPTY"}), encoding="utf-8"
    )

    finalize(handle.run_id, BusinessStatus.SUCCEEDED, report_dir)

    profile = json.loads(
        (handle.capsule / "verification/profile.json").read_text(encoding="utf-8")
    )
    assert profile["mode"] == "SENTINEL_EMPTY"
    items = _expected_items(handle.capsule)
    assert items["agents/l4-card/*"]["disposition"] == "NOT_EXPECTED"
    assert items["agents/l4-intel/*"]["disposition"] == "NOT_EXPECTED"


def test_full_run_still_owes_the_l4_roles(finalizable):
    """反向对照:没有 run_mode.json 就退回**最宽**的期望,L4 腿照样 REQUIRED。

    没有这一条,上面那条哨兵用例把 disposition 一律改成 NOT_EXPECTED 也会绿。
    """
    handle, report_dir, _ = finalizable

    finalize(handle.run_id, BusinessStatus.SUCCEEDED, report_dir)

    profile = json.loads(
        (handle.capsule / "verification/profile.json").read_text(encoding="utf-8")
    )
    assert profile["mode"] == "FULL"
    assert _expected_items(handle.capsule)["agents/l4-card/*"]["disposition"] == "REQUIRED"


@pytest.mark.parametrize(
    "payload",
    [None, "{ 不是 json", json.dumps({"mode": "SENTINEL_MAYBE"})],
    ids=["missing", "unparseable", "unknown-mode"],
)
def test_unreadable_run_mode_falls_back_to_full_and_is_recorded(finalizable, payload):
    """读不到模式 → 退回 FULL(最宽,不会造假绿灯),但**必须记账**。

    「降级不留痕」才是真病:静默退回 FULL 时,哨兵趟看起来只是「证据不全」。
    """
    handle, report_dir, _ = finalizable
    if payload is not None:
        (handle.staging / "run_mode.json").write_text(payload, encoding="utf-8")

    finalize(handle.run_id, BusinessStatus.SUCCEEDED, report_dir)

    profile = json.loads(
        (handle.capsule / "verification/profile.json").read_text(encoding="utf-8")
    )
    assert profile["mode"] == "FULL"
    assert "capsule.run_mode" in _endpoints()


def test_materializer_failure_does_not_break_finalize(finalizable, monkeypatch):
    """D-5 是 B 级证据:它炸了不许把整趟现场带走 —— 一个索引 bug 不该毙掉整个 run。"""

    def _boom(*args, **kwargs):
        raise RuntimeError("索引炸了")

    handle, report_dir, _ = finalizable
    monkeypatch.setattr(wb, "materialize_web_budget", _boom)
    monkeypatch.setattr(ei, "materialize_evidence_index", _boom)

    result = finalize(handle.run_id, BusinessStatus.SUCCEEDED, report_dir)

    assert (handle.capsule / "capsule.json").is_file()
    assert (result.final_path / "capsule/capsule.json").is_file()
    assert result.business_status == BusinessStatus.SUCCEEDED
    # 不阻断 ≠ 不记账。
    assert {"capsule.web_budget", "capsule.external_evidence_index"} <= _endpoints()
