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


def test_l3_lint_fix_reads_narrow_pack_not_full_table():
    src = (WF / "scan-market.js").read_text(encoding="utf-8")
    head, _, tail = src.partition("label: 'L3-lint-fix'")
    assert tail, "L3-lint-fix 调用点不见了"
    # Task 11 起业务 agent 走 tracedAgent 包装,定位取两种调用形式里**最后**出现的那个。
    call_at = max(head.rfind("await agent("), head.rfind("tracedAgent("))
    assert call_at >= 0, "L3-lint-fix 的派发调用点不见了"
    prompt = head[call_at:] + tail[:500]
    assert "_l3_repair_prompt.md" in prompt
    assert "_l3_repair_patch.json" in prompt
    assert "_l3_table.md" not in prompt
    assert "_l3_judged.json 有 thesis" not in prompt
    assert "apply-repair" in tail[:1200]


def test_scan_gate_branches_read_verified_stage_results():
    """GATE1/2 的唯一分支事实来自 StageResult，不再重复解析 gate stdout。

    2026-07-30 事故后 metrics 改经 `stageMetrics()` 解包(haiku 壳会把整条 StageResult
    再包一层塞进 metrics，外层三字段仍匹配 schema、校验照常放行，于是
    `g1.metrics.l4_budget` 静默变 undefined → `Math.min(10, undefined)=NaN` → L3 prompt
    写成「7~NaN 只」→ GATE2 `--budget NaN` 被 argparse 毙)。契约不变、读法变，断言同步。
    """
    src = (WF / "scan-market.js").read_text(encoding="utf-8")
    assert "autoresearch.scan.stage_result show" in src
    assert "STAGE_RESULT" in src
    assert "g1.status === 'SUCCEEDED'" in src
    assert "g2.status === 'SUCCEEDED'" in src
    assert "function stageMetrics(" in src          # schema 锁不住嵌套深度，得自己解包
    assert "g1m.sentinel_level" in src
    assert "g2m.finalists" in src
    assert "g1.ok" not in src
    assert "g2.ok" not in src


def test_gate1_and_run_mode_share_one_stage_gate_shell():
    src = (WF / "scan-market.js").read_text(encoding="utf-8")
    assert src.count("stageGate('GATE1'") == 1
    assert "--decide-run-mode" in src
    assert "run-mode-attempt-1" not in src
    assert "l2-check" in src and "prelude-retry" in src


def test_scan_refuses_to_run_on_an_unusable_run_mode():
    """GATE1 通过却没带回四态之一 → 硬停,不许 JS 侧默认值替它签字。

    合并前这里有兜底:run-mode 壳挂了被 `.catch(() => null)` 吞掉,再由 sentinel_level 推一个
    模式出来。推错的代价是**整天跑错方向**——哨兵日补成 FULL = 白跑一趟全市场;全扫日补成
    SENTINEL_EMPTY = 当天什么都不跑。合并后模式是 GATE1 事务的一部分,拿不到就是坏了。
    """
    src = (WF / "scan-market.js").read_text(encoding="utf-8")
    assert "RUN_MODES.includes(runMode)" in src
    assert "throw new Error" in src.split("RUN_MODES.includes(runMode)")[1][:400]
    # 四态白名单必须齐:少一态就是把那种日子判成"坏了"并硬停
    for mode in ("FULL", "FORCED_FULL", "SENTINEL_EMPTY", "SENTINEL_PINNED"):
        assert f"'{mode}'" in src.split("const RUN_MODES")[1][:200]
    # 旧兜底不得复活(它正是"默认值替失败签字")
    assert "? 'FORCED_FULL' : 'SENTINEL_EMPTY'" not in src


def test_force_full_reaches_the_merged_gate1_call():
    """人工 override 必须真的进到合并后的那条命令 —— 掉了它,哨兵日会静默变成"什么都不跑"。"""
    src = (WF / "scan-market.js").read_text(encoding="utf-8")
    gate1_call = src.split("stageGate('GATE1'")[1][:400]
    assert "--decide-run-mode" in gate1_call
    assert "forceFull ? ' --force-full' : ''" in gate1_call


def test_scan_refuses_to_run_on_an_unusable_l4_budget():
    """NaN/undefined 曾一路无声流进 L3 prompt 与 GATE2 —— 判断核心的指令被污染却没人喊。

    fail fast 优于带病继续:宁可整条停,也不让 L3 拿着「7~NaN 只」去判断。
    """
    src = (WF / "scan-market.js").read_text(encoding="utf-8")
    assert "Number.isInteger(l4Budget)" in src
    assert "throw new Error" in src.split("Number.isInteger(l4Budget)")[1][:400]


def test_l4_workflow_records_success_and_failure_stage_results():
    src = (WF / "l4-stock.js").read_text(encoding="utf-8")
    assert "autoresearch.scan.stock_stage l4" in src
    assert "card_agent_exception" in src
    assert "card_no_return" in src
    assert "catch (error)" in src
    assert "throw error" in src


# Wave10 B3:earlystop-shadow 整族退役(consumer/账本接了、producer 从未接线)——
# 原 `test_earlystop_shadow_workflow_is_separate_and_shadow_only` 随 workflow 一并删除。
# 它顺带锁的「scan-market.js 不得派发影子深审」在 workflow 文件消失后已恒真,无迁移对象。
