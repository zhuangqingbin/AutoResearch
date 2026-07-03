"""prelude 编排骨架:单步失败不阻断、结果结构。整链为已测组件的编排,真跑验证。合成。

spec: docs/specs/2026-07-03-scan-run-reliability-design.md §2
"""
from __future__ import annotations

from autoresearch.scan.prelude import _run_steps


def test_run_steps_isolation():
    def ok():
        return "好"

    def boom():
        raise RuntimeError("炸")

    res = _run_steps([("a", ok), ("b", boom), ("c", ok)])
    assert [r["step"] for r in res] == ["a", "b", "c"]      # b 炸不阻 c
    assert [r["ok"] for r in res] == [True, False, True]
    assert res[0]["note"] == "好" and "炸" in res[1]["note"]
