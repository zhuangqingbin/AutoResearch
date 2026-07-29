"""前奏汇总屏落盘失败必须响亮(Wave8 W8-5)。

2026-07-28 首航:CP1「转播前奏汇总屏全文」落空 —— `_prelude_summary.md` 不存在,
而 workflow 里的 prelude agent 回报的是「Prelude execution complete. Exit code: 0.
Summary file generated at context/scan/2026-07-28/_prelude_summary.md」。

两处合谋制造了这句谎话:

1. `run_prelude` 用 `contextlib.suppress(Exception)` 包住 `write_summary` ——
   写盘异常被整个吞掉,**根因至今无从考证**(这正是本 task 要解决的:下次自证);
2. workflow 的 `prelude ... && echo "SUMMARY_FILE=..."` 在 prelude 退出码 0 时
   **无条件**回显路径,不管文件在不在(该腿在 scan-market.js 侧同批修)。

修法保持「不阻断前奏」(汇总屏是 B 级产物,stdout 仍有全文),但失败必须留痕。
同族教训:空 pickle 永不重拉 / 空 slim 默认 Hold / 非空垃圾 pack —— 静默降级
比响亮失败危险得多。
"""

from __future__ import annotations

import pytest

from autoresearch.scan import prelude as prelude_mod
from autoresearch.scan import stage_result as stage_result_mod


@pytest.fixture
def isolated_prelude(tmp_path, monkeypatch):
    """把 run_prelude 剥到只剩「汇总屏落盘」这一段:业务步骤与 StageResult 都短路。"""
    monkeypatch.chdir(tmp_path)                       # context/scan/... 落进 tmp
    monkeypatch.setattr(prelude_mod, "_run_steps", lambda steps: [])
    monkeypatch.setattr(prelude_mod, "render_summary", lambda *_a, **_kw: "SUMMARY-BODY")
    monkeypatch.setattr(stage_result_mod, "safe_record_stage_result",
                        lambda *_a, **_kw: None)
    return tmp_path


def test_summary_write_failure_is_loud_and_non_blocking(isolated_prelude, monkeypatch, capsys):
    """写盘炸了:stderr 必须出现失败行(带真实异常),且 run_prelude 不得抛。"""
    def boom(*_a, **_kw):
        raise OSError("simulated: read-only filesystem")

    monkeypatch.setattr(prelude_mod, "write_summary", boom)

    results = prelude_mod.run_prelude("2099-01-01")     # 不抛 = 不阻断前奏

    assert results == []
    err = capsys.readouterr().err
    assert "汇总屏落盘失败" in err, (
        "写盘失败被静默吞掉了 —— 这正是 07-28 那句谎话的成因(根因无从考证)"
    )
    assert "simulated: read-only filesystem" in err, (
        "必须带上真实异常文本,否则下次照样查不出根因"
    )


def test_success_path_still_reports_the_path(isolated_prelude, monkeypatch, capsys):
    """成功路不受影响:仍在 stdout 报出落盘路径。"""
    target = isolated_prelude / "_prelude_summary.md"
    monkeypatch.setattr(prelude_mod, "write_summary", lambda *_a, **_kw: target)

    prelude_mod.run_prelude("2099-01-01")

    out = capsys.readouterr().out
    assert "汇总屏已落盘" in out
    assert str(target) in out
