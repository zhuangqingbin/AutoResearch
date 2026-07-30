#!/usr/bin/env python3
"""intel 稿件硬顶守卫(确定性,零 LLM)—— Wave8 W8-13 起 / Wave9 W9-B2 改判。

**为什么需要**:`l4-intel` 的查询上限一直是**指令级**约束(prompt 里写 `≤N 条`),
agent 想超就超,`pr_20260714_007` 挂了 15 天没人裁。2026-07-28 实测 11 稿自报
16–29 条(cap 15),**全体超限** —— 探针天天报警、天天无视,狼来了效应把 P0 探针的
公信力磨没了。

修法两腿:
1. **cap 15 → 20**(`scan_config.jsonc`):对齐实测中位 ~18,消掉常态化警报;
2. **硬顶 30**(本模块):超硬顶必有后果 —— 内容照样丢,只是丢弃顺序按时效价值排。

**Wave9 W9-B2:拒稿 → 裁稿**。硬顶原本的后果是"整稿拒"(改名 `.rejected.md`,
card 侧 presence-gate 找不到 intel 就自动回退卡内网查)。问题是整稿拒把 **T0 增量**
(收盘后到跑报这段时间的新信息 = 明天开盘唯一还没被定价的东西)一并扔了 ——
2026-07-29 实测 002546/603893 两票中招(自报 36/34 条超硬顶 30,但事件段本身只有
5/1 行、结构完好),整稿拒把这种精简却新鲜的稿也一并否了,新闻面无谓变薄。现在
超硬顶触发的是 `trim_by_recency`:按 `T0 → 24h → 催化挂 → 背景 → >1周` 的时效
价值排序,只砍最不新鲜的事件行直到 ≤keep;T0/24h 永远最后被砍。`REJECTED` 收窄
为只留给**事件段一行都解析不出**的稿(结构不可信,裁无可裁);能解析的超顶稿——
不管裁后是否真的丢了行——一律 `TRIMMED`。

红线不变:**只拒稿不拒票**。情报是辅助面,不得反噬决策主链 —— 无论 TRIMMED 还是
REJECTED,进程退出码都是 0,本票照常出卡。自报缺失只 warn 不拒(无法对账 ≠ 违规,
弱证据不当强证据用)。
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

HARD_CAP_DEFAULT = 30
_CLAIM_RE = re.compile(r"网查\s*(\d+)\s*条")

# 事件行时效窗优先级(数值越小越新鲜,裁剪时最后被砍)。认不出的窗按"背景"降权 ——
# 不把不明来源的行伪装成最新增量,是有意的保守选择。
_WINDOW_RANK = {"T0": 0, "24h": 1, "催化挂": 2, "背景": 3, ">1周": 4}
_EVENT_ROW = re.compile(r"^\|\s*\d{4}-\d{2}-\d{2}\s*\|")


def intel_path(scan_dir: Path | str, code: str) -> Path:
    return Path(scan_dir) / f"_l4_intel_{code}.md"


def claimed_queries(text: str) -> int | None:
    """声明行自报的网查条数;没写 → None(不猜)。"""
    m = _CLAIM_RE.search(text)
    return int(m.group(1)) if m else None


def _window_of(row: str) -> int:
    cells = [c.strip() for c in row.strip().strip("|").split("|")]
    win = cells[1] if len(cells) > 1 else ""
    return _WINDOW_RANK.get(win, 3)          # 认不出的窗按"背景"降权,不当 T0


def _event_rows(text: str) -> list[int]:
    """事件段表格行的行号(0-based)。空列表 = 一行事件都解析不出(稿件结构不可信)。"""
    return [i for i, ln in enumerate(text.splitlines()) if _EVENT_ROW.match(ln)]


def trim_by_recency(text: str, *, keep: int = 10) -> tuple[str, int]:
    """按时效窗优先级把事件段裁到 ≤keep 行 → (新全文, 被砍行数)。

    Wave9 W9-B2:超硬顶从"整稿拒"改"按时效裁"——整稿拒会把 **T0 增量**(明天开盘
    唯一还没被定价的东西)一并扔掉,2026-07-29 实测 002546/603893 两票新闻面因此
    变薄。硬顶的牙还在(超帽内容照样丢),只是丢弃顺序按时效价值排。

    行数已在 keep 以内(含事件行数为 0,即无法识别事件表)时是 no-op,原文原样
    返回、cut=0 —— "裁不动"和"没什么可裁"用同一个信号面(cut),`guard_intel` 侧
    另用 `_event_rows` 区分"结构不可信"与"稿子本来就精简"两种 cut=0。
    """
    lines = text.splitlines()
    idx = _event_rows(text)
    if len(idx) <= keep:
        return text, 0
    ranked = sorted(idx, key=lambda i: (_window_of(lines[i]), i))
    drop = set(ranked[keep:])
    out = [ln for i, ln in enumerate(lines) if i not in drop]
    return "\n".join(out) + ("\n" if text.endswith("\n") else ""), len(drop)


def guard_intel(scan_dir: Path | str, code: str, *,
                hard_cap: int = HARD_CAP_DEFAULT) -> dict:
    """检查一份 intel 稿;超硬顶则按时效裁剪,裁无可裁才整拒。返回可直接 JSON 序列化的裁决。

    `action` ∈ `ABSENT`(无稿,presence-gated 安静通过)/ `KEPT`(未超顶)/
    `TRIMMED`(超顶但事件段可解析,已按时效窗裁到 ≤10 行,T0/24h 增量保留)/
    `REJECTED`(超顶且事件段一行都解析不出,结构不可信,裁无可裁,照旧整稿拒)。
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
        trimmed, cut = trim_by_recency(text)
        if not _event_rows(text):
            # 事件段一行都解析不出 → 稿件结构不可信,裁无可裁,照旧整拒
            # (REJECTED 的新语义)。注意:不能用 `cut == 0` 判"解析不出" ——
            # 精简但结构完好的稿(事件行数本就 ≤keep)也会 cut=0,那不是不可信,
            # 是没什么可裁(002546/603893 实测两票正是这种:36/34 条自报超顶,
            # 事件段却只有 5/1 行)。误把它当 REJECTED 会重犯本任务要修的老毛病。
            dst = src.with_name(f"_l4_intel_{code}.rejected.md")
            src.replace(dst)          # 改名不删除:证据留档,便于事后对账
            return {"ok": False, "code": code, "action": "REJECTED",
                    "claimed": claimed, "hard_cap": hard_cap, "kept_as": dst.name,
                    "note": "事件段不可解析,整稿拒;card 回退卡内网查"}
        stamp = (f"〔已裁剪·自报 {claimed} 超硬顶 {hard_cap}·"
                 f"按时效窗保留 T0/24h/催化挂,砍 {cut} 行〕\n")
        src.write_text(stamp + trimmed, encoding="utf-8")
        return {"ok": True, "code": code, "action": "TRIMMED",
                "claimed": claimed, "hard_cap": hard_cap, "dropped_rows": cut,
                "note": "T0/24h 增量保留;card 照常读 intel"}
    return {"ok": True, "code": code, "action": "KEPT", "claimed": claimed,
            "hard_cap": hard_cap}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="intel 稿件硬顶守卫:自报网查数超硬顶则按时效裁稿,裁无可裁才整拒(只拒稿不拒票)")
    ap.add_argument("date", help="分析日 YYYY-MM-DD")
    ap.add_argument("code", help="6 位股票代码")
    ap.add_argument("--hard-cap", type=int, default=HARD_CAP_DEFAULT,
                    help=f"自报网查条数硬顶,超过即按时效裁剪(默认 {HARD_CAP_DEFAULT})")
    ap.add_argument("--scan-dir", default=None, help="覆盖 context/scan/<date>")
    args = ap.parse_args(argv)

    scan_dir = Path(args.scan_dir) if args.scan_dir else Path("context/scan") / args.date
    print(json.dumps(guard_intel(scan_dir, args.code, hard_cap=args.hard_cap),
                     ensure_ascii=False))
    return 0          # 拒稿不是进程失败 —— 只拒稿不拒票


if __name__ == "__main__":
    raise SystemExit(main())
