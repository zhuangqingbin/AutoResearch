#!/usr/bin/env python3
"""把契约层**生成进 workflow 的行内块** —— 让 JS 不再各拼各的字面量。

design: `docs/specs/2026-08-29-full-coverage-research-system-brainstorm.md` §2.4 A1 / §2.2 K7。

## 病灶

`.claude/workflows/*.js` 与 python 各持一份同一个概念,而 JS 侧**没有测试**
(`node --check` 对 ESM + 顶层 return 零鉴别力 —— 写坏了仍 exit 0,是本仓记过的假绿灯):

| 概念 | JS | python |
|---|---|---|
| 评级名次 | `RANK = {sell:0…buy:4}`(`l4-stock.js:430`) | `RATING_ORDER` Buy=0…Sell=4 —— **方向相反** |
| 瞬时错误 | `TRANSIENT = [...]`(`l4-stock.js:306`) | `contracts/retry.INTEL_RESEARCH` |

两份真身 = 两个可以各自漂移的地方,而「方向相反」这件事只活在人的记忆里。

## 做法:**行内代码生成**,不是旁边放一个文件

初版(2026-08-29)生成了一份独立的 `.claude/workflows/_contracts.generated.js`,
指望两个 workflow `import` 它。**2026-08-30 复核发现那条路走不通,而且它本身就是
设计稿在骂的那种病**:

- 三个 workflow **没有任何一个 import 过任何东西**(grep 实证),Workflow 运行时也明说
  「No filesystem or Node.js API access」—— 本地文件 import 大概率根本不成立;
- 于是那份生成物**零消费者**,只有它自己的测试在读它 —— 正是 spec §1.3 列的
  「建成未接线」(FN-1)家族。**修别人这个病的同一天造了一个新的**,所以删掉重做。

改成把值直接生成进 workflow 文件里一段带标记的块:不需要 import、静态可校验、
漂移当场红。

    uv run --no-sync python -m autoresearch.contracts.emit          # 打印将要写入的块
    uv run --no-sync python -m autoresearch.contracts.emit --write  # 同步进 workflow
    uv run --no-sync python -m autoresearch.contracts.emit --check  # CI:不一致则 exit 1
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from autoresearch.contracts import agent_output as ao, retry

#: 行内块的起止标记。两行之间的一切由本模块拥有 —— 手编会被 `--write` 覆盖、被 `--check` 判红。
BEGIN = "// ── <contracts:begin> ── 由 `python -m autoresearch.contracts.emit --write` 生成,勿手编"
END = "// ── <contracts:end> ──"

#: 哪个 workflow 要哪些常量。**只放确实有两份真身的东西**,不是把 python 全搬过去 ——
#: 生成物越大,它自己就越像下一个没人读的文件。
WORKFLOW_BLOCKS: dict[str, tuple[str, ...]] = {
    ".claude/workflows/l4-stock.js": ("rank", "transient"),
}


def render_block(keys: tuple[str, ...]) -> str:
    """一个 workflow 的行内块正文(含起止标记)。"""
    lines = [
        BEGIN,
        "// 真身:autoresearch/contracts/agent_output.py(评级序)"
        " · autoresearch/contracts/retry.py(瞬时错误)",
    ]
    if "rank" in keys:
        body = ", ".join(f"{k}: {v}" for k, v in ao.js_rank_view().items())
        lines += [
            "// 评级名次:**JS 与 python 方向相反**(这里 sell=0…buy=4,而 python 的",
            "// RATING_ORDER 是 Buy=0…Sell=4)。此前两边各写一份字面量、谁也没有测试锁,",
            "// 方向只活在人的记忆里。",
            f"const RANK = {{ {body} }}",
        ]
    if "transient" in keys:
        arr = ", ".join(f"'{e}'" for e in retry.INTEL_RESEARCH)
        lines += [
            "// 可重试的瞬时错误(**情报再搜**口径 = contracts/retry.INTEL_RESEARCH)。",
            "// 注意 `retry.TASK_ATTEMPT` 是**另一套**(含 STALE_TASK 而非 ENOTFOUND):",
            "// 任务簿重试与网查重试是两条不同策略,名字像但不是一回事 —— 别顺手合并。",
            f"const TRANSIENT = [{arr}]",
        ]
    lines.append(END)
    return "\n".join(lines)


def _splice(text: str, block: str) -> str:
    """把 `text` 里 BEGIN..END 之间换成 `block`。

    没有标记 → 报错。标记要人**放一次**(块落在文件哪个位置是人的判断,
    不该由生成器擅自插入)。
    """
    if BEGIN not in text or END not in text:
        raise ValueError("目标文件里没有 <contracts:begin>/<contracts:end> 标记")
    head = text.split(BEGIN)[0]
    tail = text.split(END, 1)[1]
    return head + block + tail


def check(root: Path | None = None) -> tuple[bool, str]:
    """每个 workflow 的行内块是否与现在重新生成的一致。"""
    base = root or Path.cwd()
    stale: list[str] = []
    for rel, keys in WORKFLOW_BLOCKS.items():
        target = base / rel
        if not target.is_file():
            return False, f"workflow 不存在:{target}"
        text = target.read_text(encoding="utf-8")
        try:
            want = _splice(text, render_block(keys))
        except ValueError as exc:
            return False, f"{rel}: {exc}"
        if text != want:
            stale.append(rel)
    if stale:
        return False, (
            "以下 workflow 的契约块已过期:" + ", ".join(stale) + "\n"
            "改了 autoresearch/contracts/ 就要重新同步:\n"
            "  uv run --no-sync python -m autoresearch.contracts.emit --write"
        )
    return True, "in sync"


def write(root: Path | None = None) -> list[Path]:
    """把行内块同步进每个 workflow;返回被改动的文件。"""
    base = root or Path.cwd()
    out: list[Path] = []
    for rel, keys in WORKFLOW_BLOCKS.items():
        target = base / rel
        text = target.read_text(encoding="utf-8")
        target.write_text(_splice(text, render_block(keys)), encoding="utf-8")
        out.append(target)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="把契约层生成进 workflow 的行内块")
    ap.add_argument("--write", action="store_true", help="同步进 workflow")
    ap.add_argument("--check", action="store_true", help="不一致则 exit 1")
    args = ap.parse_args(argv)
    if args.check:
        ok, msg = check()
        print(msg)
        return 0 if ok else 1
    if args.write:
        for path in write():
            print(f"synced {path}")
        return 0
    for rel, keys in WORKFLOW_BLOCKS.items():
        print(f"# {rel}\n{render_block(keys)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
