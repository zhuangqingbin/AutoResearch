"""learning 测试共享隔离(C4 修复,final-review 2026-08-08 sweep)。

`tests/scan/conftest.py` 已有 `_isolate_learning_stores`(t1 快环账本/候选账本重定向到
tmp)——但那份 autouse fixture 只覆盖 `tests/scan/**`,不覆盖 `tests/learning/**`。而
`t1_review._LEDGER`/`_CAND_LEDGER` 真正的调用大户恰恰在 `tests/learning/`(`test_t1_review.py`
自己的 module 级 fixture 已修 C4 本体事故;这里补的是**目录级**结构性护栏,防下一个加进
`tests/learning/` 的测试文件重蹈同一个坑——「记得传 ledger_path」这件事已经漏过一次)。

sweep 结论(2026-08-08):当前 `tests/learning/` 下除 `test_t1_review.py` 外没有第二个文件
真的踩了这个坑(`test_gap_backfill.py`/`test_t1_early_stop_bucket.py` 显式传路径或只调纯函数;
`test_nightly_close.py` 整个 mock 掉 `t1_review.*`),但目录本身此前没有这层护栏,纯属侥幸。
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_t1_review_ledgers(monkeypatch, tmp_path):
    """镜像 `tests/scan/conftest.py::_isolate_learning_stores` 的账本半条:t1 快环账本/候选
    账本重定向到 tmp。

    `test_t1_review.py` 自己也有一份同款 module 级 fixture(C4 本体修复,故意不删——两层
    防御,`tests/learning/` 目录级这层是本次 sweep 才补的,晚于本体修复几分钟,不该反过来
    因为"目录级已经够了"就撤掉更具体那层)。两份 fixture 谁先跑都不影响最终重定向生效
    (都指向 tmp 路径,同一属性反复 `monkeypatch.setattr` 幂等);但 `test_t1_review.py` 的
    C4 回归锁读的是**字面量** `Path("context/learning/t1_review.jsonl")`,不是 `t1._LEDGER`
    当前值——这样它的「真账本没被碰」断言不受两层 fixture 谁先执行影响,细节见那边的注释。
    """
    import autoresearch.learning.t1_review as t1

    monkeypatch.setattr(t1, "_LEDGER", tmp_path / "_iso" / "t1_review.jsonl")
    monkeypatch.setattr(t1, "_CAND_LEDGER", tmp_path / "_iso" / "t1_candidates.jsonl")
