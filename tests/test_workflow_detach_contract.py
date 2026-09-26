"""Workflow 长命令契约(2026-09-26 prelude 被杀事故)。

- 长命令必须走 `detached()`(命令脱离中继壳进程树,壳只做有界等待),不许再裸交给 bash()/taskGate:
  壳一旦 run_in_background 后交卷,harness 按进程树杀掉后台任务。
- 任何壳 prompt 都不许再教壳「转后台就等完成通知」—— 对 subagent,等通知 = 交卷 = 任务被杀。
- Workflow 运行时禁用 `Date.now()` / 无参 `new Date()`(破坏 resume;09-26 探针实测直接抛错)。
"""
import re
from pathlib import Path

WF = Path(__file__).resolve().parents[1] / ".claude" / "workflows"
SCAN = (WF / "scan-market.js").read_text(encoding="utf-8")
L4 = (WF / "l4-stock.js").read_text(encoding="utf-8")


def test_no_wall_clock_in_production_workflows():
    # 生产 workflow = 根目录非递归(同 test_workflow_js_syntax 口径);probes/ 是历史探针。
    for path in sorted(WF.glob("*.js")):
        code = re.sub(r"//[^\n]*", "", path.read_text(encoding="utf-8"))
        assert "Date.now(" not in code, path
        assert not re.search(r"new Date\(\s*\)", code), path


def test_no_prompt_teaches_relays_to_wait_for_background_notifications():
    for path in sorted(WF.rglob("*.js")):
        src = path.read_text(encoding="utf-8")
        for phrase in ("转后台就", "安静等待完成通知", "安静等完成通知"):
            assert phrase not in src, (path, phrase)


def test_scan_market_long_commands_run_detached():
    for key in ("frame-attempt-1", "frame-attempt-2", "prelude-attempt-1", "prelude-attempt-2",
                "l3-prepare-attempt-1", "l4-prep-attempt-1"):
        assert f"detached('{key}'" in SCAN, key
    for module in ("autoresearch.scan.prelude", "autoresearch.scan.frame ",
                   "autoresearch.scan.agents.l3_select prepare"):
        assert not re.search(r"bash\(\s*`[^`]*" + re.escape(module), SCAN), module


def test_l4_slim_prepare_runs_detached_and_lost_is_retryable():
    assert "detached(`slim-${code}-attempt-${taskAttempt}`" in L4
    assert not re.search(r"taskGate\(\s*`[^`]*l4_tasks prepare", L4)
    lost = L4[L4.index("['LOST', 'TIMEOUT'].includes(slimRun.state)"):]
    assert lost.index("taskFailure('TIMEOUT')") < lost.index("DATA_INTEGRITY")
