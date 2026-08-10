"""Wave 3 工作流开关：流式 L4、稳定上下文和 finalist-only 行业 brief。"""
from __future__ import annotations

from pathlib import Path

WF = Path(".claude/workflows")


def test_scan_workflow_has_streaming_and_byte_compatible_legacy_branches():
    src = (WF / "scan-market.js").read_text(encoding="utf-8")
    assert "streaming_l4" in src
    assert "stable_context_blocks" in src
    assert "sector_brief_mode" in src
    assert "autoresearch.scan.l4_tasks init" in src
    assert "dispatch_batches" in src

    _, marker, legacy = src.partition("if (!streamingL4)")
    assert marker
    before_legacy = src[: src.index(marker)]
    assert "harvest-slim" not in before_legacy.rsplit("const plan =", 1)[-1]
    assert "harvest-slim" in legacy


def test_l4_tasks_init_gate_runs_prompts_before_init_in_same_shell():
    """I-3(2026-08-10 终审必修):C2 把 prompts 从 l4-prep 长壳迁入 l4-tasks-init 短壳——这是
    2026-08-09 $23 盲跑(prompts 缺失、init 仍认领任务、12 股全废)的根治手段,却在终审时
    发现**零测试锁定**:把 `l4_card prompts ${date} && ` 从这条命令里删掉,全量 3956 条
    测试照样全绿(见 final-review.md I-3)。`node --check` 对本仓 workflow js 形态零鉴别力
    (见 test_workflow_js_syntax.py 模块 docstring),所以不靠语法探针,直接钉字符串结构:
    prompts 必须与 init 同一条 `&&` 壳命令、且在 init **之前**(先生成任务包、init 才有
    东西可校验;C1a 的硬门吃的正是这个原子相邻)。长壳(`l4-prep`,C2 的迁出源头)里
    不得再出现 prompts —— 迁回去等于撤销 C2。
    """
    src = (WF / "scan-market.js").read_text(encoding="utf-8")
    assert "l4_card prompts ${date} && ${R} autoresearch.scan.l4_tasks init" in src, (
        "prompts 与 init 不再同壳相邻,或迁移顺序被打乱(C2 接线松脱)")

    l4prep = src[src.index("'l4-prep'") - 800: src.index("'l4-prep'")]
    assert "l4_card prompts" not in l4prep, "prompts 又被挪回了 l4-prep 长壳(C2 被撤销)"


def test_l4_stock_preflights_then_runs_slim_and_intel_in_parallel():
    src = (WF / "l4-stock.js").read_text(encoding="utf-8")
    # C1b(2026-08-10):preflight 从 taskGate(壳内 if-else)换 gpJson 直调 python CLI ——
    # 子命令不再是裸 `preflight ${code} ${date}`,而是完整命令行的一段。
    preflight_at = src.index("l4_tasks preflight ${code} ${date}")
    card_at = src.index("phase('Card')")
    success_at = src.index("`success ${code} ${date}`")

    assert preflight_at < card_at < success_at
    intel = src[src.index("phase('Intel')"):card_at]
    assert "parallel([" in intel
    assert "`prepare ${code} ${date}`" in intel
    assert "agent(" in intel
    assert "DATA_INTEGRITY" in intel


def test_task_success_is_presence_gated_for_legacy_direct_invocations():
    src = (WF / "l4-stock.js").read_text(encoding="utf-8")
    assert "test -s ${TASK_BOOK}" in src
    assert '"action":"LEGACY"' in src
    assert "`success ${code} ${date}`" in src


def test_stable_context_blocks_switch_is_gone_from_the_workflow_too():
    """Wave10 B4 的另一半 —— 它的孪生兄弟有守卫,它没有,于是它在 JS 里活了下来。

    B4 删净了 Python 侧(`context_blocks.py` + `prompts.py` 的 `--stable-context` 分支)
    与 config 键,却漏了 workflow 里的 `const stableContextBlocks = cfg.performance?...`
    与它拼出的 `promptMode`。当时无害(键已删 → false → 空串),但那是个**陷阱**:
    谁把这个键加回 config,流水线就会给一个不认识该 flag 的 CLI 传 `--stable-context`。

    退役没有守卫 = 删掉了还能悄悄回来。这条就是那个守卫。
    """
    src = (WF / "scan-market.js").read_text(encoding="utf-8")
    code = "\n".join(ln for ln in src.splitlines() if not ln.strip().startswith("//"))
    assert "stableContextBlocks" not in code and "promptMode" not in code
    assert "stable_context_blocks" not in code
    assert "--stable-context" not in code, "Python 侧已无此 flag,传了会直接报错"


def test_sector_briefs_have_exactly_one_path():
    """Wave10 B4:`sector_brief_mode` 退役 —— brief 只剩 GATE2 **之前**全量生成这一条路。

    原 `test_finalist_only_sector_briefs_run_after_gate2_before_prompts` 测的是那个
    A/B 开关的调度顺序;开关删了,顺序契约也随之消失。留下的契约是:**没有第二条路**
    (双路存在过就意味着 L3 可能看不到判断型 brief,而那会改变 finalists)。
    """
    src = (WF / "scan-market.js").read_text(encoding="utf-8")
    code = "\n".join(ln for ln in src.splitlines() if not ln.strip().startswith("//"))
    assert "sectorBriefMode" not in code and "finalistBriefSectors" not in code
    assert "sector_brief_mode" not in code
    briefs = src.index("preL3BriefSectors")
    gate2 = src.index("const g2 =")
    assert briefs < gate2, "行业 brief 必须在 GATE2 之前生成(L3 要读它)"

