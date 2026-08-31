"""Stable serialization, hashing, and atomic JSON persistence primitives.

**搬家说明(2026-08-31 D6.2)**:真身已下沉到 `autoresearch/common/atomic.py`,
本文件只做 re-export。原因是 `common/run_identity.py`(RunContract 的新家)需要
`canonical_json`/`sha256_bytes`/`atomic_write_json`,而 `common` 排在 `trace`
**之下** —— `common` import `trace` 会造一条新的向上边,当场被
`tests/contracts/test_layering.py::test_no_new_upward_edges` 判红。

这几个函数本身零 autoresearch 依赖(只用 stdlib),不属于 `trace` 的私产。
全仓既有 `from autoresearch.trace.atomic import …`(21 处)一律不断。
"""
from __future__ import annotations

from autoresearch.common.atomic import (
    atomic_write_bytes,
    atomic_write_json,
    canonical_json,
    sha256_bytes,
    sha256_file,
)

__all__ = [
    "atomic_write_bytes",
    "atomic_write_json",
    "canonical_json",
    "sha256_bytes",
    "sha256_file",
]
