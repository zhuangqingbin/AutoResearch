#!/usr/bin/env python3
"""intel 稿件硬顶守卫(确定性,零 LLM)—— Wave8 W8-13。

**为什么需要**:`l4-intel` 的查询上限一直是**指令级**约束(prompt 里写 `≤N 条`),
agent 想超就超,`pr_20260714_007` 挂了 15 天没人裁。2026-07-28 实测 11 稿自报
16–29 条(cap 15),**全体超限** —— 探针天天报警、天天无视,狼来了效应把 P0 探针的
公信力磨没了。

修法两腿:
1. **cap 15 → 20**(`scan_config.jsonc`):对齐实测中位 ~18,消掉常态化警报;
2. **硬顶 30 = 拒稿**(本模块):超硬顶把稿件改名 `.rejected.md`,card 侧的
   presence-gate 找不到 intel 就自动回退卡内网查 —— **现有机制零改动**。

红线:**只拒稿不拒票**。情报是辅助面,不得反噬决策主链 —— 所以拒稿走的是"文件改名 +
退出码 0",不是让本票失败。自报缺失只 warn 不拒(无法对账 ≠ 违规,弱证据不当强证据用)。
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

HARD_CAP_DEFAULT = 30
_CLAIM_RE = re.compile(r"网查\s*(\d+)\s*条")


def intel_path(scan_dir: Path | str, code: str) -> Path:
    return Path(scan_dir) / f"_l4_intel_{code}.md"


def claimed_queries(text: str) -> int | None:
    """声明行自报的网查条数;没写 → None(不猜)。"""
    m = _CLAIM_RE.search(text)
    return int(m.group(1)) if m else None


def guard_intel(scan_dir: Path | str, code: str, *,
                hard_cap: int = HARD_CAP_DEFAULT) -> dict:
    """检查一份 intel 稿;超硬顶则改名拒稿。返回可直接 JSON 序列化的裁决。

    `action` ∈ `ABSENT`(无稿,presence-gated 安静通过)/ `KEPT` / `REJECTED`。
    """
    src = intel_path(scan_dir, code)
    if not src.exists():
        return {"ok": True, "code": code, "action": "ABSENT", "claimed": None}
    try:
        text = src.read_text(encoding="utf-8")
    except Exception as e:  # noqa: BLE001 — 读不动不等于违规,放行并留痕
        return {"ok": True, "code": code, "action": "KEPT", "claimed": None,
                "warn": f"unreadable: {e!r}"}

    claimed = claimed_queries(text)
    if claimed is None:
        # 缺自报 = 无法对账,照旧只 warn。以"缺"推断"违规"是把弱证据当强证据。
        return {"ok": True, "code": code, "action": "KEPT", "claimed": None,
                "warn": "unreported"}
    if claimed > hard_cap:
        dst = src.with_name(f"_l4_intel_{code}.rejected.md")
        src.replace(dst)          # 改名不删除:证据留档,便于事后对账
        return {"ok": False, "code": code, "action": "REJECTED",
                "claimed": claimed, "hard_cap": hard_cap, "kept_as": dst.name,
                "note": "card 将回退卡内网查(presence-gate);本票照常出卡"}
    return {"ok": True, "code": code, "action": "KEPT", "claimed": claimed,
            "hard_cap": hard_cap}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="intel 稿件硬顶守卫:自报网查数超硬顶则拒稿(只拒稿不拒票)")
    ap.add_argument("date", help="分析日 YYYY-MM-DD")
    ap.add_argument("code", help="6 位股票代码")
    ap.add_argument("--hard-cap", type=int, default=HARD_CAP_DEFAULT,
                    help=f"自报网查条数硬顶,超过即拒稿(默认 {HARD_CAP_DEFAULT})")
    ap.add_argument("--scan-dir", default=None, help="覆盖 context/scan/<date>")
    args = ap.parse_args(argv)

    scan_dir = Path(args.scan_dir) if args.scan_dir else Path("context/scan") / args.date
    print(json.dumps(guard_intel(scan_dir, args.code, hard_cap=args.hard_cap),
                     ensure_ascii=False))
    return 0          # 拒稿不是进程失败 —— 只拒稿不拒票


if __name__ == "__main__":
    raise SystemExit(main())
