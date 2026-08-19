"""learning 测试共享隔离(C4 修复,final-review 2026-08-08 sweep;I-3 补丁 2026-08-09)。

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

import builtins
import io as _io
import os
import pathlib
from pathlib import Path

import pytest

# ─────────────────── I-3(final-review 2026-08-08/09):production report/scan 写保护 ───────────────────
#
# 事故:`test_retro_rel_cols.py:172` 调 `retro.attribute("2026-07-24", scan_root=tmp_path)`
# 漏传 `report_root`,回落 `retro.py` 里散落的内联字面量默认值 `Path("reports/scan")`——
# `attribute()` → `_publish_retro_control_state()` → `_report_dir_for()` 真的在
# `reports/scan/*/manifest.json` 里找到 `analysis_date=="2026-07-24"` 的已发布报告
# `20260725_1316`,把该测试的 100 码合成 fixture `shutil.copy2` 进它的 `trace/retro/`、
# `trace/outbox/`,覆写掉 7 个真实文件(`attribution.csv` 1,786,137B → 12,472B 等)。
# 控制方已从 `context/scan/2026-07-24/` 逐字节恢复(7/7 校验通过)。
#
# 与上面 `_isolate_t1_review_ledgers`(C4)不同:那里的默认路径是模块级可变属性
# (`t1._LEDGER`),`monkeypatch.setattr` 一次就重定向了全部调用点。这里 `report_root`/
# `scan_root` 是函数参数,默认值 `Path("reports/scan")`/`Path("context/scan")` 是内联字面量,
# 散落在 `retro.py` 十余个函数里,没有单一可 monkeypatch 的挂钩点——所以改在**文件系统写
# 原语**这一层拦截:任何测试(不论现在还是以后新增、不论经由哪条调用链)一旦真的对仓库
# 真实 `reports/` 或 `context/scan/` 目录发起 mkdir / 写打开,直接抛 `PermissionError`,不
# 像 C4 那样静默重定向——静默重定向会让"忘记传 report_root/scan_root"这件事永远不可见,
# 这里要的是「直接失败」(review-16-22-findings.md I-3 的显式要求)。
#
# 覆盖点:`Path.mkdir`(目录创建,`_publish_retro_control_state`/`attribute()` 写文件前必经,
# 即便 `exist_ok=True` 也照样拦——不依赖目录是否已存在)+ `builtins.open` 与 `io.open`
# 两个**独立**绑定:前者是 pandas `to_csv`/`shutil.copy2`(经 `shutil.copyfile`)内部裸
# `open()` 落的实际符号;后者是 Python 3.11 `pathlib.Path.open()`/`write_text()`/
# `write_bytes()` 落的符号(源码显式 `return io.open(...)`,不经过 `builtins.open`)——
# 两个必须都补,漏一个就漏一整类调用方式。只挡**写意图**(mode 含 w/a/x/+)且目标解析后
# 落在 forbidden root 下的调用;读不受影响(历史真实产物允许被读,只是不能被测试写坏),
# tmp_path 下的写入也不受影响(见 `test_production_path_guard.py` 的反向校验)。
_REPO_ROOT = Path(__file__).resolve().parents[2]
# 引擎隔离后护两套真实工作区(claude+codex);湖不在此列(测试另有 LAKE monkeypatch)。
from autoresearch.common import workspace as ws  # noqa: E402

_FORBIDDEN_ROOTS = tuple(_REPO_ROOT / f"reports_{e}" for e in ws.ENGINES) + tuple(
    _REPO_ROOT / f"context_{e}" / "scan" for e in ws.ENGINES)
PRODUCTION_PATH_GUARD_MARKER = "PRODUCTION-PATH-GUARD"


def _forbidden_root_for(path_arg) -> Path | None:
    try:
        candidate = Path(os.fspath(path_arg))
    except TypeError:
        return None                       # fd(int)/其余非路径参数,不是本护栏管的对象
    resolved = candidate.resolve()
    for root in _FORBIDDEN_ROOTS:
        if resolved == root or root in resolved.parents:
            return root
    return None


def _is_write_mode(mode) -> bool:
    return isinstance(mode, str) and any(c in mode for c in "wax+")


def _guard_message(path_arg, root: Path) -> str:
    return (f"[{PRODUCTION_PATH_GUARD_MARKER}] tests/learning/ 不许写入真实生产路径 {root} "
            f"下的文件/目录(target={path_arg!r})。这正是 2026-08-08 覆写 "
            "reports/scan/20260725_1316/trace/ 7 个文件的事故根因(final-review I-3)——"
            "显式传 report_root=tmp_path / scan_root=tmp_path,不要依赖函数默认值。")


@pytest.fixture(autouse=True)
def _forbid_production_report_writes(monkeypatch):
    """结构性护栏(I-3):见上方模块 docstring。任何写打开/建目录只要目标落在真实
    `reports/` 或 `context/scan/` 下,一律 `PermissionError`——不静默重定向、不吞异常。
    """
    real_open = builtins.open
    real_io_open = _io.open
    real_mkdir = pathlib.Path.mkdir

    def guarded_open(file, mode="r", *args, **kwargs):
        if _is_write_mode(mode):
            root = _forbidden_root_for(file)
            if root is not None:
                raise PermissionError(_guard_message(file, root))
        return real_open(file, mode, *args, **kwargs)

    def guarded_io_open(file, mode="r", *args, **kwargs):
        if _is_write_mode(mode):
            root = _forbidden_root_for(file)
            if root is not None:
                raise PermissionError(_guard_message(file, root))
        return real_io_open(file, mode, *args, **kwargs)

    def guarded_mkdir(self, *args, **kwargs):
        root = _forbidden_root_for(self)
        if root is not None:
            raise PermissionError(_guard_message(self, root))
        return real_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded_open)
    monkeypatch.setattr(_io, "open", guarded_io_open)
    monkeypatch.setattr(pathlib.Path, "mkdir", guarded_mkdir)


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
