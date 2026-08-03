#!/usr/bin/env python3
"""F3 可转债 capability gate —— 当前状态 `BLOCKED_BY_DATA`(确定性,零 LLM)。

design: docs/specs/2026-08-03-scan-next-wave-brainstorm-design.md §2.4 F3

## 原结论已撤销

设计稿原话:「**『数据全通、一晚跑三因子』的原结论撤销**,F3 先标 `BLOCKED_BY_DATA`。」

撤销的理由很具体:

- `cb_basic.conv_price` 是**当前截面**。拿它重算历史转股价值 = **泄漏未来调整**
  (转股价会因分红、下修而变动,今天的值不是当时的值);
- `cb_basic` **也不能判断实际强赎状态** —— 强赎要走 `cb_call` 或公告;
- `cb_price_chg` 端点存在但**当前 token 无权限** → 历史溢价因子被数据权限阻断。

## 四道门(全过才解锁)

1. 历史转股价调整序列或公告解析可得,并能按 `first_seen_ts` 回放;
2. `cb_call`/公告能标强赎、到期、上市/退市与暂停窗口;
3. 多债映射一只正股有确定聚合规则,成交额单位/合约口径完成对账;
4. 新债、低流动性与妖债过滤预注册;**缺失不与无转债股票直接横比**。

## 过门之后才研究的三条信号(现在不排产)

CB return / underlying return / premium change。**`premium_delta` 的符号并不天然代表
「转债资金抢跑」** —— 它可能只是正股上涨的机械结果。优先研究**控制 underlying return、
期限/转股价值与债底后的 residual**。`CB mom − stock mom` 与 premium change 高度重叠,
**不得同时以两个独立发现计数**。

  uv run --no-sync python -m autoresearch.derivatives.cb_gate status
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

SCHEMA_VERSION = 1
RULE_VERSION = "cb_gate.v1"

BLOCKED = "BLOCKED_BY_DATA"
OPEN = "OPEN"

PASS, FAIL, UNKNOWN = "PASS", "FAIL", "UNKNOWN"


@dataclass
class Gate:
    key: str
    requirement: str
    status: str = UNKNOWN
    evidence: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def default_gates() -> list[Gate]:
    """四道门的当前实测状态(2026-08-04 复核)。"""
    return [
        Gate("historical_conv_price",
             "历史转股价调整序列或公告解析可得,并能按 first_seen_ts 回放",
             FAIL,
             "`cb_basic.conv_price` 是当前截面 —— 拿它重算历史转股价值会泄漏未来调整;"
             "`cb_price_chg` 端点存在但当前 token 无权限"),
        Gate("lifecycle_windows",
             "cb_call/公告能标强赎、到期、上市/退市与暂停窗口",
             UNKNOWN,
             "`cb_call` 20260731 可返回强赎相关记录 —— 端点可达,但覆盖完整性未验;"
             "`cb_basic` 判不了实际强赎状态"),
        Gate("mapping_and_units",
             "多债映射一只正股有确定聚合规则,成交额单位/合约口径完成对账",
             UNKNOWN,
             "`cb_basic` 1,156 只基础表 / `cb_daily` 20260731 308 行行情可达;"
             "聚合规则与单位对账尚未做"),
        Gate("universe_filters",
             "新债、低流动性与妖债过滤预注册;缺失不与无转债股票直接横比",
             UNKNOWN,
             "尚未预注册"),
    ]


@dataclass
class GateReport:
    status: str
    gates: list
    blocking: list
    schema_version: int = SCHEMA_VERSION
    rule_version: str = RULE_VERSION
    withdrawn_conclusion: str = field(default=(
        "「数据全通、一晚跑三因子」的原结论**已撤销**(§2.4)"))
    signal_discipline: str = field(default=(
        "premium_delta 的符号**不天然代表**转债资金抢跑,它可能只是正股上涨的机械结果;"
        "优先研究控制 underlying return / 期限 / 转股价值 / 债底后的 residual。"
        "`CB mom − stock mom` 与 premium change 高度重叠,**不得计为两个独立发现**"))
    routing: str = field(default=(
        "统一走 factor_lab → 锁定 OOS → replay → registry;"
        "能力门不过则整线停在数据可行性,**不回填伪 PIT 因子**"))

    def as_dict(self) -> dict:
        return {**asdict(self), "gates": [g if isinstance(g, dict) else g.as_dict()
                                          for g in self.gates]}


def evaluate(gates: list[Gate] | None = None) -> GateReport:
    """四门结算。**任一门非 PASS → 整线 BLOCKED_BY_DATA**(不是「部分可用」)。

    为什么不给部分解锁:三条信号全都依赖历史转股价。第一道门不过,后面研究的东西
    就是伪 PIT 因子 —— 那正是本门要防的。
    """
    items = list(gates if gates is not None else default_gates())
    blocking = [g.key for g in items if g.status != PASS]
    return GateReport(status=BLOCKED if blocking else OPEN,
                      gates=[g.as_dict() for g in items], blocking=blocking)


def assert_not_scheduled(report: GateReport | None = None) -> None:
    """守卫:门没过就想排产 → 抛错(§2.4「能力门不过则整线停在数据可行性」)。"""
    rep = report or evaluate()
    if rep.status != OPEN:
        raise RuntimeError(
            f"F3 仍是 {rep.status},阻断门:{rep.blocking} —— {rep.routing}。"
            f"另注:{rep.withdrawn_conclusion}")


def assert_not_double_counted(findings) -> None:
    """`CB mom − stock mom` 与 premium change 高度重叠,不得计为两个独立发现(§2.4)。"""
    names = {str(f) for f in (findings or ())}
    overlap = {"cb_minus_stock_mom", "premium_change"} & names
    if len(overlap) > 1:
        raise RuntimeError(
            f"{sorted(overlap)} 高度重叠 —— 不得同时以两个独立发现计数(§2.4)")


def render(report: GateReport) -> str:
    payload = report.as_dict()
    lines = [f"# F3 可转债 capability gate —— **{payload['status']}**", "",
             f"> {payload['withdrawn_conclusion']}", "",
             "| # | 门 | 状态 | 证据 |", "|---:|---|---|---|"]
    for i, gate in enumerate(payload["gates"], 1):
        mark = {"PASS": "✅", "FAIL": "🚨", "UNKNOWN": "⏳"}.get(gate["status"], "?")
        lines.append(f"| {i} | {gate['requirement']} | {mark} {gate['status']} "
                     f"| {gate['evidence'] or '—'} |")
    lines += ["", f"- 阻断门:{payload['blocking'] or '无'}",
              "", "## 过门之后才研究的三条信号(现在不排产)", "",
              f"- {payload['signal_discipline']}",
              f"- 路由:{payload['routing']}", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="F3 可转债 capability gate(§2.4)")
    ap.add_argument("cmd", choices=["status"])
    ap.add_argument("--json-out", default=None)
    a = ap.parse_args(argv)
    report = evaluate()
    if a.json_out:
        out = Path(a.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report.as_dict(), ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
    print(render(report))
    # BLOCKED 是**当前的正确状态**,不是失败 → 退出码 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
