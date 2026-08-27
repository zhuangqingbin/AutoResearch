"""护栏验证(final-review 2026-08-08/09 I-3):`conftest.py::_forbid_production_report_writes`
必须真的拦住任何写入真实 `reports/` 或 `context/scan/` 的尝试——这是「故意让一个测试尝试写
生产路径,断言被拦」那条红测试(review-16-22-findings.md I-3 第 3 条要求)。

安全设计:目标路径一律用不会被任何真实产物占用的哨兵名(`_guardrail_canary_*`),且每个
用例前后都断言哨兵路径不存在。护栏正常工作时这层断言恒真(写入从未真正发生,拦截在
mkdir/open 调用本身那一步);哨兵路径若因护栏被改坏而真的建出来,teardown 立刻清掉,
不留仓库垃圾——反过来说,这份 teardown 本身也是一层独立于护栏的安全网。
"""
from __future__ import annotations

import pytest

from autoresearch.common import workspace as ws
from tests.scan.conftest import _REPO_ROOT, PRODUCTION_PATH_GUARD_MARKER

_CANARY_NAME = "_guardrail_canary_should_never_persist"
_CANARY_REPORTS_DIR = _REPO_ROOT / ws.reports_root() / _CANARY_NAME
_CANARY_SCAN_DIR = _REPO_ROOT / ws.scan_root() / _CANARY_NAME
_CANARY_REPORTS_FILE = _REPO_ROOT / ws.reports_root() / f"{_CANARY_NAME}.txt"
_CANARY_SCAN_FILE = _REPO_ROOT / ws.scan_root() / f"{_CANARY_NAME}.txt"
_ALL_CANARIES = (_CANARY_REPORTS_DIR, _CANARY_SCAN_DIR, _CANARY_REPORTS_FILE, _CANARY_SCAN_FILE)


@pytest.fixture(autouse=True)
def _cleanup_canaries():
    """独立于护栏本身的第二层安全网:不管护栏是否生效,用例前后都确保哨兵路径不存在。"""
    for p in _ALL_CANARIES:
        assert not p.exists(), f"canary {p} 在测试开始前就已存在,清理逻辑或此前某次运行出了问题"
    yield
    for p in _ALL_CANARIES:
        if p.is_dir():
            p.rmdir()
        elif p.exists():
            p.unlink()


# ───────────────────────── 正向:护栏必须拦住 ─────────────────────────


def test_guardrail_blocks_mkdir_into_real_reports_tree():
    with pytest.raises(PermissionError, match=PRODUCTION_PATH_GUARD_MARKER):
        _CANARY_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    assert not _CANARY_REPORTS_DIR.exists()


def test_guardrail_blocks_mkdir_into_real_context_scan_tree():
    with pytest.raises(PermissionError, match=PRODUCTION_PATH_GUARD_MARKER):
        _CANARY_SCAN_DIR.mkdir(parents=True, exist_ok=True)
    assert not _CANARY_SCAN_DIR.exists()


def test_guardrail_blocks_write_text_into_real_reports_tree():
    """父目录(仓库真实 `reports/`)本来就存在,不经过 mkdir 那一关——单独验证
    `Path.write_text`/`io.open` 这条路径,不依赖 mkdir 守卫。"""
    with pytest.raises(PermissionError, match=PRODUCTION_PATH_GUARD_MARKER):
        _CANARY_REPORTS_FILE.write_text("canary", encoding="utf-8")
    assert not _CANARY_REPORTS_FILE.exists()


def test_guardrail_blocks_write_text_into_real_context_scan_tree():
    with pytest.raises(PermissionError, match=PRODUCTION_PATH_GUARD_MARKER):
        _CANARY_SCAN_FILE.write_text("canary", encoding="utf-8")
    assert not _CANARY_SCAN_FILE.exists()


def test_guardrail_blocks_builtin_open_write_mode():
    """`builtins.open` 与 `io.open`(pathlib 走的是后者)是两个独立绑定,分别验证——
    这条测的是 pandas `to_csv`/`shutil.copy2` 实际落的那一个符号。"""
    with pytest.raises(PermissionError, match=PRODUCTION_PATH_GUARD_MARKER), \
         open(_CANARY_REPORTS_FILE, "w", encoding="utf-8"):
        pass
    assert not _CANARY_REPORTS_FILE.exists()


def test_guardrail_reproduces_the_original_incident_shape_via_shutil_copy2(tmp_path):
    """复现事故的真实机制:`_publish_retro_control_state` 用 `shutil.copy2` 把 scan 侧文件
    拷进 report 侧 `trace/`。`shutil.copyfile` 内部对目的路径 `open(dst, 'wb')`——这条测试
    直接验证 `shutil.copy2` 这一实际调用路径,而不仅仅是 mkdir/write_text 这两个更底层原语。
    """
    import shutil

    src = tmp_path / "source.csv"
    src.write_text("code\n000001\n", encoding="utf-8")
    with pytest.raises(PermissionError, match=PRODUCTION_PATH_GUARD_MARKER):
        shutil.copy2(src, _CANARY_REPORTS_FILE)
    assert not _CANARY_REPORTS_FILE.exists()


# ───────────────────────── 反向:护栏不能误伤 ─────────────────────────


def test_guardrail_does_not_block_read_attempts_in_real_production_tree():
    """护栏只挡写:真实生产树里对不存在哨兵的读应是 FileNotFound,不能变 PermissionError。

    用不存在的哨兵避免依赖开发机遗留的 gitignored 历史报告,同时仍让 read_text/Path.open
    穿过护栏对真实生产根的路径判定。
    """
    with pytest.raises(FileNotFoundError):
        _CANARY_REPORTS_FILE.read_text(encoding="utf-8")
    with pytest.raises(FileNotFoundError):
        with _CANARY_REPORTS_FILE.open(encoding="utf-8"):
            pass


def test_guardrail_allows_writes_under_tmp_path(tmp_path):
    """反向校验:护栏认的是"解析后是否落在仓库真实 reports//context/scan 根下",不是
    路径里出现 "reports"/"scan" 字样就拦——否则会把 tmp_path 下同名子目录也误伤,等于
    拦坏了本仓库几乎全部 scan/learning 测试。"""
    d = tmp_path / "reports" / "scan" / "20990101_9999"
    d.mkdir(parents=True, exist_ok=True)
    target = d / "manifest.json"
    target.write_text('{"analysis_date": "2099-01-01"}', encoding="utf-8")
    assert target.read_text(encoding="utf-8") == '{"analysis_date": "2099-01-01"}'

    also_scan = tmp_path / "context" / "scan" / "2099-01-01"
    also_scan.mkdir(parents=True, exist_ok=True)
    (also_scan / "ok.txt").write_text("fine", encoding="utf-8")
    assert (also_scan / "ok.txt").exists()
