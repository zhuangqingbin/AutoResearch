#!/usr/bin/env python3
"""兼容 re-export —— 真身已搬到 `autoresearch/common/run_identity.py`(2026-08-31 D6.2)。

搬家的理由与落点见新家的模块 docstring:契约本身与 scan 的业务无关,而把它留在 `scan/`
逼得 `trace/capsule.py` 为了读写契约必须 import `scan`(`KNOWN_UPWARD` 里那条成环边)。

本文件只做 re-export,一行逻辑都没有。`import subprocess` 是**故意**留的:
`tests/scan/test_run_contract.py` 通过 `monkeypatch.setattr(rc.subprocess, "run", …)`
桩掉 git 探针,而 `subprocess` 是进程内单例模块 —— 打在这里等于打在真身用的那一个上。
"""
from __future__ import annotations

import subprocess  # noqa: F401 —— 见 docstring:测试通过本模块的属性桩 git 探针

from autoresearch.common.atomic import canonical_json, sha256_bytes  # noqa: F401
from autoresearch.common.run_identity import (  # noqa: F401
    DIRTY_PATHS_CAP,
    RUN_CONTRACT_SCHEMA_VERSION,
    SUPPORTED_SCHEMA_VERSIONS,
    _V2_FIELDS,
    _V3_FIELDS,
    RunContract,
    load_run_contract,
    resolve_git_dirty,
    resolve_git_sha,
    sha256_json,
    write_run_contract,
)

__all__ = [
    "DIRTY_PATHS_CAP",
    "RUN_CONTRACT_SCHEMA_VERSION",
    "SUPPORTED_SCHEMA_VERSIONS",
    "RunContract",
    "canonical_json",
    "load_run_contract",
    "resolve_git_dirty",
    "resolve_git_sha",
    "sha256_bytes",
    "sha256_json",
    "write_run_contract",
]
