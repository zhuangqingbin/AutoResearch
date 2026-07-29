"""执行壳契约:跑命令的 gp 壳必须被禁止改写重定向(Wave8 W8-3)。

**为什么要把这句话锁进测试**:2026-07-28 首航事故的第一因不是取数失败,是**执行壳**
自作主张 —— workflow 里写的是

    uv run --no-sync python -m autoresearch.scan.frame <date> --json > .../market_pack.json

haiku 壳实际跑的是 `... > market_pack.json 2>&1`(多了 `2>&1`,脚本从没要求过)。
stderr 的 `[L0·tushare]` 日志行与 tqdm 进度条被并进产物 → 1,776B 无 JSON 的垃圾 pack,
`test -s` 门放行,重试分支从未触发。

机器层的修法是 W8-1(writer 侧原子写)+ W8-2(门判 JSON 合法性);本文件锁的是**指令层**
(instruction-vs-check 排序:补指令 > 给合法情形标记 > 才是加严检查)。没有这条测试,
将来任何一次 prompt 编辑都能把这句约束静默删掉,而不会有任何东西变红。

判据只认语义关键点(`2>&1` / `原样` / `重定向`),不锁整句措辞 —— 措辞可改,约束不可失。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS_DIR = ROOT / ".claude" / "workflows"

# 各文件已知的 gp-haiku 壳(跑命令的封装函数/调用点)。数量用于自检切分逻辑没失效。
# ⚠️ scan-market.js 有三个而非一个 —— W8-3 首版只补了 bash(),被本测试逮到 gate()/
# stageGate() 同样在跑命令、同样会被壳加 `2>&1` 污染(它们读的是 stdout 上的 JSON,
# 混进 stderr 直接毁掉返回值)。「发现一处必查同族」。
_SHELL_LABELS = {
    "scan-market.js": ["bash()", "gate()", "stageGate()"],
    "l4-stock.js": ["recordL4", "taskGate", "ens-dump:"],
}

_REQUIRED = ("2>&1", "原样", "重定向")


def _shell_prompt_blocks(text: str) -> list[str]:
    """切出每个 `agentType: 'general-purpose'` 调用前的 prompt 文本块。

    做法:按 `agentType: 'general-purpose'` 切分,取每段**之前**的内容尾部
    (prompt 在 opts 之前),足够覆盖该次 agent() 调用的 prompt 串。
    """
    parts = re.split(r"agentType:\s*'general-purpose'", text)
    return parts[:-1]  # 最后一段是末次调用之后的尾巴,不含 prompt


@pytest.mark.parametrize("filename", sorted(_SHELL_LABELS))
def test_every_gp_shell_forbids_rewriting_redirects(filename: str) -> None:
    """每个跑命令的 gp 壳 prompt 都必须带「不得改写重定向」约束。"""
    path = WORKFLOWS_DIR / filename
    assert path.exists(), f"{filename} 不存在(workflow 被改名?同步更新本测试)"
    blocks = _shell_prompt_blocks(path.read_text(encoding="utf-8"))
    assert blocks, f"{filename} 里没找到 general-purpose 壳 —— 切分逻辑可能失效"

    for i, block in enumerate(blocks):
        tail = block[-1200:]           # prompt 紧邻 opts,取尾部即可
        missing = [kw for kw in _REQUIRED if kw not in tail]
        assert not missing, (
            f"{filename} 第 {i + 1} 个 general-purpose 壳的 prompt 缺少重定向约束关键点 "
            f"{missing}。\n2026-07-28 事故:壳擅自加 `2>&1` 把 stderr 灌进产物文件。\n"
            f"prompt 尾部:\n...{tail[-400:]}"
        )


def test_probe_would_notice_a_missing_constraint() -> None:
    """自检:切分逻辑至少要找到全部已知的壳,否则本测试会静默空跑。"""
    total = sum(
        len(_shell_prompt_blocks((WORKFLOWS_DIR / f).read_text(encoding="utf-8")))
        for f in _SHELL_LABELS
    )
    expected = sum(len(v) for v in _SHELL_LABELS.values())
    assert total >= expected, (
        f"只切出 {total} 个 gp 壳,已知有 {expected} 个 —— 切分逻辑失效,"
        f"本测试会变成永不变红的绿灯"
    )
