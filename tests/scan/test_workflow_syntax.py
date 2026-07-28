"""workflow JS 语法探针接进 pytest(Wave3.5 教训:`node --check` 对本仓 workflow 零鉴别力)。

2026-07-25 复验:往 scan-market.js 追加 `const broken = {{{` 后,`node --check` 仍 exit 0,
而本探针 exit 1。ESM(顶层 export + 顶层 await/return)会让 --check 跳过函数体解析 ——
那是一盏永远不会变红的绿灯,所以守卫必须走 AsyncFunction 构造器。
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="需要 node")

WF = Path(".claude/workflows")


def _check(p: Path):
    from scripts.check_workflow_js import check
    return check(p)


@pytest.mark.parametrize("name", sorted(p.name for p in WF.glob("*.js")))
def test_workflow_js_parses(name):
    ok, err = _check(WF / name)
    assert ok, f"{name} 语法错误:\n{err}"


def test_probe_actually_catches_broken_js(tmp_path):
    """探针自检:坏语法必须真的被逮到(否则这些绿灯全是装饰)。"""
    bad = tmp_path / "broken.js"
    bad.write_text("export const meta = { name: 'x' }\nconst broken = {{{\n", encoding="utf-8")
    ok, _ = _check(bad)
    assert not ok, "探针对明显的坏语法都不报错 —— 它没有鉴别力"


# ══ Wave7 B′-e / B′-g:两道「0 字节 / 断连」守卫的存在性契约 ═══════════════════
#
# 语法探针只证明文件能解析,证明不了守卫还在。这两条锁的是**调用点存在性** ——
# 2026-07-27 的两起事故都不是语法问题,而是「本该有的守卫压根没写」和「写了没人接住」。
# 无 node 也应能跑(纯文本断言),故不受本文件 pytestmark 的 node 门影响时也无妨。


def test_frame_has_pack_check_guard():
    """B′-g:frame 与 universe 同样会 ChunkedEncodingError 半途而废,必须有产物非空探测。

    2026-07-27:frame 在 11/12 端点断线 → 退出码 1 → `>` 重定向留下 **0 字节**
    market_pack.json;bash() 壳不看退出码,空 pack 一路流到 market_view。
    删掉这道门,本测试必须变红。
    """
    src = (WF / "scan-market.js").read_text(encoding="utf-8")
    assert "pack-check" in src, "frame 后没有 market_pack 非空探测(0 字节 pack 会静默流下去)"
    assert "test -s" in src, "探测判据不是 `test -s`(非零字节)—— 体积判据才拦得住 0 字节文件"
    assert "frame-retry" in src, "探测失败后没有重试腿"


def test_l3_lint_fix_failure_is_caught_not_silent():
    """B′-e:自修 agent 是可选增益,断连不该被当成「跑过了」。

    2026-07-27 该 agent 死于 `Connection closed mid-response`,journal 只留 started、
    没有 result,workflow 若无其事继续 —— 56.9k 加权白烧且无人察觉。
    """
    src = (WF / "scan-market.js").read_text(encoding="utf-8")
    head, _, tail = src.partition("label: 'L3-lint-fix'")
    assert tail, "L3-lint-fix 调用点不见了(本测试定位假设失效,请重写)"
    assert ".catch(" in tail[:400], "L3 自修 agent 调用没有 .catch —— 断连会静默"
    assert "未完成" in tail[:600] or "异常" in tail[:600], "失败路径没有可见日志 = 降级不留痕"


def test_scan_gate_branches_read_verified_stage_results():
    """GATE1/2 的唯一分支事实来自 StageResult，不再重复解析 gate stdout。"""
    src = (WF / "scan-market.js").read_text(encoding="utf-8")
    assert "autoresearch.scan.stage_result show" in src
    assert "STAGE_RESULT" in src
    assert "g1.status === 'SUCCEEDED'" in src
    assert "g2.status === 'SUCCEEDED'" in src
    assert "g1.metrics.sentinel_level" in src
    assert "g2.metrics.finalists" in src
    assert "g1.ok" not in src
    assert "g2.ok" not in src


def test_l4_workflow_records_success_and_failure_stage_results():
    src = (WF / "l4-stock.js").read_text(encoding="utf-8")
    assert "autoresearch.scan.stock_stage l4" in src
    assert "card_agent_exception" in src
    assert "card_no_return" in src
    assert "catch (error)" in src
    assert "throw error" in src


def test_earlystop_shadow_workflow_is_separate_and_shadow_only():
    src = (WF / "earlystop-shadow.js").read_text(encoding="utf-8")
    assert "shadow/earlystop_queue.json" in src
    assert "shadow/earlystop_details/${code}.md" in src
    assert "不得修改 production" in src
    assert "details/${code}.md" not in src.replace(
        "shadow/earlystop_details/${code}.md",
        "",
    )
    production = (WF / "scan-market.js").read_text(encoding="utf-8")
    assert "earlystop-shadow" not in production
