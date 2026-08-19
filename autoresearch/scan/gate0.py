#!/usr/bin/env python3
"""GATE0 preflight —— 债务闸的**挂载点**,默认 advisory(确定性,零 LLM)。

design: docs/specs/2026-08-03-scan-next-wave-brainstorm-design.md §4.2-4

## 为什么需要一个新的挂载点

设计稿的原话:

> 若确需「启动前阻断」,必须在 frame/universe/LLM 之前增 GATE0/preflight;
> **当前 GATE1 在 L2 之后,不能声称「不开始扫描」**。

这一句是勘误:此前的说法是「GATE1 会挡住带病开扫」,但 GATE1 跑在 L2 **之后** ——
那时候取数、打分、召回都已经花掉了。真要「不开始」,闸必须在这三件事之前。

## 默认 advisory,不是保守,是分类

硬闸属于 **B 类可用性变更**(§4.2 末段):它能让扫描完全跑不起来,所以需要
availability SLO、故障演练与回滚,**不进零风险 P0**。本模块因此:

- 默认 `ADVISORY` —— 只产 preflight 报告,不阻断;
- `BLOCKING` 模式的**治理闸**(`assert_may_block`)与 CLI `--mode BLOCKING` 分支已于
  2026-08-19(用户裁决 A3,随 experiment_registry 家族)整删——该模式此前需 registry
  ACTIVE 实验才能开启,而 registry 永远不存在 ACTIVE 实验,是结构性不可达的死路径。
  `preflight(mode=BLOCKING)` 本身作为纯函数仍保留(供测试/编排代码直调模拟硬闸场景),
  只是不再有 CLI/治理入口能触发它;
- override 记 `actor / reason / expiry`,**过期即失效**(不是永久豁免)。

  uv run --no-sync python -m autoresearch.scan.gate0 check 2026-08-04
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from datetime import date as _date
from pathlib import Path

SCHEMA_VERSION = 1
REGISTRY_FAMILY = "scan_preflight_gate"

ADVISORY = "ADVISORY"      # 默认:只告警
BLOCKING = "BLOCKING"      # B 类:需显式开启 + registry
MODES = (ADVISORY, BLOCKING)

PASS, WARN, BLOCK = "PASS", "WARN", "BLOCK"

# GATE1 在 L2 之后 —— 这句勘误必须跟着报告走,免得又有人以为它能挡住开扫。
GATE1_CORRECTION = (
    "GATE1 跑在 **L2 之后**:那时取数/打分/召回已经花掉了,它挡不住「开始扫描」。"
    "真正的启动前阻断只能挂在本 GATE0(frame/universe/LLM 之前)")

AVAILABILITY_REQUIREMENT = (
    "硬闸是 **B 类可用性变更** —— 需 availability SLO、故障演练与回滚,"
    "并有 registry ACTIVE 实验;不进零风险 P0")


class Gate0Error(RuntimeError):
    """在没有治理授权的情况下想开硬闸。"""


@dataclass
class Override:
    actor: str
    reason: str
    expires: str          # YYYY-MM-DD

    def active_on(self, day: str) -> bool:
        """**过期即失效** —— 永久豁免等于没有闸。"""
        try:
            return _date.fromisoformat(day) <= _date.fromisoformat(self.expires)
        except ValueError:
            return False

    def as_dict(self) -> dict:
        return asdict(self)


def preflight(debts: dict, *, day: str | None = None, mode: str = ADVISORY,
              override: Override | None = None) -> dict:
    """启动前体检 → `{verdict, mode, blocking, override_applied, ...}`。

    `verdict`:`PASS` | `WARN`(advisory 下的应阻断项)| `BLOCK`。
    """
    from autoresearch.learning.nightly_runner import classify_debts

    if mode not in MODES:
        raise Gate0Error(f"未知模式 {mode!r};合法:{list(MODES)}")
    today = day or _date.today().isoformat()
    tiers = classify_debts(debts)
    would_block = tiers["would_hard_block"]

    override_applied = bool(override and override.active_on(today) and would_block)
    if not would_block:
        verdict = PASS
    elif mode == ADVISORY or override_applied:
        verdict = WARN
    else:
        verdict = BLOCK

    return {
        "schema_version": SCHEMA_VERSION,
        "day": today, "mode": mode, "verdict": verdict,
        "blocking_tiers": tiers["blocking_tiers"],
        "by_tier": tiers["by_tier"],
        "override": (override.as_dict() if override else None),
        "override_applied": override_applied,
        "override_expired": bool(override and not override.active_on(today)),
        "gate1_correction": GATE1_CORRECTION,
        "availability_requirement": AVAILABILITY_REQUIREMENT,
        "policy": tiers["policy"],
        "mount_point": "frame/universe/LLM 之前",
    }


def render(report: dict) -> str:
    mark = {PASS: "✅", WARN: "⚠️", BLOCK: "🚨"}[report["verdict"]]
    lines = [f"# GATE0 preflight —— {mark} **{report['verdict']}**", "",
             f"- 日期 `{report['day']}` · 模式 `{report['mode']}` · "
             f"挂载点 {report['mount_point']}",
             f"- 应阻断层级:{report['blocking_tiers'] or '无'}", ""]
    if report["override"]:
        state = ("已生效" if report["override_applied"]
                 else ("**已过期**" if report["override_expired"] else "未触发"))
        ov = report["override"]
        lines.append(f"- override({state}):{ov['actor']} · {ov['reason']} · "
                     f"到期 {ov['expires']}")
    lines += ["", "| 层级 | 债务 |", "|---|---|"]
    for tier, items in sorted(report["by_tier"].items()):
        detail = "、".join(f"{k}={v}" for k, v in sorted(items.items())) or "—"
        lines.append(f"| `{tier}` | {detail} |")
    lines += ["", f"> 勘误:{report['gate1_correction']}", "",
              f"> {report['availability_requirement']}", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="GATE0 preflight(§4.2-4;默认 advisory)")
    ap.add_argument("cmd", choices=["check"])
    ap.add_argument("date", nargs="?", default=None)
    # D1(2026-08-19,用户裁决 A3):BLOCKING 的治理闸(assert_may_block)已随
    # experiment_registry 整删,CLI 不再暴露该模式(结构性不可达即一并退役);
    # `preflight(mode=gate0.BLOCKING)` 仍可编程直调(见 tests/learning/test_nightly_runner.py)。
    ap.add_argument("--mode", default=ADVISORY, choices=[ADVISORY])
    ap.add_argument("--json-out", default=None)
    a = ap.parse_args(argv)

    from autoresearch.learning.nightly_runner import collect_debts

    day = a.date or _date.today().isoformat()
    report = preflight(collect_debts(day), day=day, mode=a.mode)
    if a.json_out:
        out = Path(a.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
    print(render(report))
    # advisory 恒 0;BLOCK 在 CLI 侧结构性不可达(--mode 只接受 ADVISORY)
    return 1 if report["verdict"] == BLOCK else 0


if __name__ == "__main__":
    raise SystemExit(main())
