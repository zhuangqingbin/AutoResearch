"""scan 测试共享隔离。

**DEFAULT_PINNED_PATH 隔离(autouse)**:`universe.run`/`assemble`/`l3_select` 在不传 `pinned_path`
时读默认 `.claude/skills/scan-market/pinned.jsonc`(FN-1 修复后生产入口真读它)。开发者本地可能有
**真实保送票**(如 300033),会被非隔离测试注入 L1 → 污染 parity/召回断言。此处把默认路径指向一个
保证不存在的路径,让所有 scan 测试默认"无保送"(=parity);要测 pinned 的用例照旧传显式 `pinned_path=`
(显式参数优先,不受本 fixture 影响)。
"""
from __future__ import annotations

import builtins
import io as _io
import os
import pathlib
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _no_trade_cal_network(monkeypatch):
    """交易日历不得在单测里走网络(`exec_anchor.trading_sessions` 第 1 级是 tushare)。

    **只掐网络那一级,不替换 `trading_sessions` 本体** —— 换成桩的话,测"日历回退"
    的用例就会测到桩而不是被测函数(「绿灯不等于有灯」)。掐掉之后它自动落到
    lake 分区 / 工作日启发,并如实标 `calendar_quality`。
    """
    def _offline(*_a, **_k):
        raise RuntimeError("单测禁止 trade_cal 网络调用")

    monkeypatch.setattr("autoresearch.data.tushare_source._pro", _offline)


@pytest.fixture(autouse=True)
def _isolate_default_pinned(monkeypatch):
    monkeypatch.setattr("autoresearch.scan.user_config.DEFAULT_PINNED_PATH",
                        Path("/nonexistent/tests-no-real-pinned.jsonc"))
    # 同族隔离:universe.run 的 _funnel_overlay 兜底会读默认 scan_config.jsonc(FN-1 第三修)——
    # 真配置(9 路+advisory 配额)不该渗进 tmp_path 测试的 parity 断言;要测 overlay 的用例自行 monkeypatch。
    monkeypatch.setattr("autoresearch.scan.user_config.DEFAULT_PATH",
                        Path("/nonexistent/tests-no-real-scan-config.jsonc"))


# ─────────────────── I-3(final-review 2026-08-08/09):production report/scan 写保护 ───────────────────
#
# 事故(2026-08-08):一个 tmp_path 测试漏传 `report_root`,回落到内联字面量默认值
# `Path("reports/scan")`,把 100 码合成 fixture `shutil.copy2` 进真实已发布报告的
# `trace/retro/`、`trace/outbox/`,覆写掉 7 个真实文件(`attribution.csv` 1,786,137B →
# 12,472B 等)。控制方已逐字节恢复(7/7 校验通过)。护栏保留:肇事模块虽已随 2026-08-21
# learning 层退役删除,但"忘记传 root、默认值落进真实工作区"这个形状与那个模块无关。
#
# `report_root`/`scan_root` 这类参数的默认值是散落在各模块里的内联字面量,没有单一可
# monkeypatch 的挂钩点——所以改在**文件系统写原语**这一层拦截:任何测试(不论现在还是以后新增、不论经由哪条调用链)一旦真的对仓库
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
    return (f"[{PRODUCTION_PATH_GUARD_MARKER}] tests/scan/ 不许写入真实生产路径 {root} "
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
def _isolate_temperature_csv(monkeypatch):
    """镜像 _isolate_default_pinned:context/learning/temperature.csv 由真实 prelude 增量落盘
    (gitignored、按日期键),tmp_path 隔离测试不该读到——否则硬编码 analysis_date 的既有断言会被
    开发机真数据污染(如 2026-07-02 行使 assemble 多出 🌡 行)。要测温度的用例自行 monkeypatch
    CSV_PATH(测试体内的 setattr 后执行,优先生效)。"""
    monkeypatch.setattr("autoresearch.scan.temperature.CSV_PATH",
                        Path("/nonexistent/tests-no-real-temperature.csv"))


@pytest.fixture(autouse=True)
def _isolate_swing_seat_stage_rulers(monkeypatch):
    """同上一条的理由:§12 观察席读数行缺省读真实 `_ledger/views/stage_rulers.csv`(gitignored,
    夜间重建)。测试里一律指向不存在的路径 → 读数行恒为「暂无」,不随开发机真账本变。要测读数
    的用例显式传 `stage_rulers_path`。"""
    monkeypatch.setattr("autoresearch.scan.swing_seat._default_stage_rulers_path",
                        lambda: Path("/nonexistent/tests-no-real-stage-rulers.csv"))


@pytest.fixture
def codex_run(tmp_path, monkeypatch):
    """One active Codex run plus a writable copy of the rollout fixture."""
    from tests.forensic_fixtures import begin_fixture_run, copy_fixture

    handle = begin_fixture_run(tmp_path, monkeypatch)
    source = copy_fixture("codex/rollout.jsonl", tmp_path / "harness")
    return handle, source
