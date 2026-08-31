"""回归锁:进程级降级账本必须在**每个**测试之间清空。

被锁的是 `tests/conftest.py::_isolate_degradation_ledger`(那里记了完整病历)。

## 为什么用「两条互相弄脏」而不是一条

`autoresearch.data.contracts._DEGRADED` 是进程级 list。要证明隔离生效,就得有一条
测试**在另一条已经弄脏之后**仍然看见干净账本。单条测试做不到这件事:它自己弄的脏
自己看不见。

两条都「先断言干净、再故意弄脏」,于是**无论哪条先跑**,后跑的那条都会在
autouse fixture 被拿掉时当场红 —— 与 `pytest-randomly` 的随机序无关,
也不依赖同文件内的书写顺序。

变异探针(2026-08-31 实测):把 `_isolate_degradation_ledger` 注释掉 →
本文件 2 条里必红 1 条(先跑的那条把账本弄脏,后跑的那条读到 1 行)。
"""

from __future__ import annotations

from autoresearch.data import contracts

_REASON = "回归锁:本用例故意弄脏进程级账本,验证下一条仍看见干净的"


def test_degradation_ledger_starts_clean_first_probe():
    assert contracts.degradations() == [], (
        "进程级降级账本进入本用例时就是脏的 —— "
        "tests/conftest.py::_isolate_degradation_ledger 没生效"
    )
    contracts.record_degradation("probe.isolation", _REASON)
    assert len(contracts.degradations()) == 1


def test_degradation_ledger_starts_clean_second_probe():
    assert contracts.degradations() == [], (
        "进程级降级账本进入本用例时就是脏的 —— "
        "tests/conftest.py::_isolate_degradation_ledger 没生效"
    )
    contracts.record_degradation("probe.isolation", _REASON)
    assert len(contracts.degradations()) == 1
