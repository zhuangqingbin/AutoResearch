#!/usr/bin/env python3
"""温度 v2 输入守卫 —— 什么**不能**进市场温度(确定性,零 LLM)。

design: docs/specs/2026-08-03-scan-next-wave-brainstorm-design.md §4.5「温度 v2」

原文三句,三条守卫:

1. 「F1 **只能使用归一、换月调整且有领先性证据**的衍生品读数」
2. 「D1 **只有固定全局 feed、覆盖/freshness 归一后**的新闻量可作候选」
3. 「**选择性 L2 逐票抓取禁止进入市场温度**」

第三条最容易破:逐票抓来的新闻量在表里长得和市场新闻量一模一样,只是它的分母是
「我们今天选了哪 200 只票」。用它做温度,等于用自己的选股结果去衡量市场热度。

本模块**只裁决候选输入能不能用**,不改温度序列本身(温度 v2 接线是 B 类,须 registry)。

  uv run --no-sync python -m autoresearch.scan.temperature_v2_guard check
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path

SCHEMA_VERSION = 1
REGISTRY_FAMILY = "market_temperature"

ELIGIBLE = "ELIGIBLE"
REJECTED = "REJECTED"

DERIVATIVE_REQUIREMENTS = ("normalized", "roll_adjusted", "lead_evidence")
NEWS_REQUIREMENTS = ("fixed_global_feed", "coverage_normalized", "freshness_normalized")

SELECTIVE_BAN = (
    "选择性 L2 逐票抓取**禁止**进入市场温度 —— 它的分母是「我们今天选了哪些票」,"
    "拿它衡量市场热度等于用自己的选股结果衡量市场")


@dataclass
class Candidate:
    """一个想进温度的候选输入。"""
    name: str
    kind: str                      # "derivative" | "news"
    scope: str = "market_wide"     # news 专用;selective 一律拒
    normalized: bool = False       # 名义/delta 归一
    roll_adjusted: bool = False    # 换月调整
    lead_evidence: bool = False    # 领先性证据(同期相关不算)
    fixed_global_feed: bool = False
    coverage_normalized: bool = False
    freshness_normalized: bool = False

    def as_dict(self) -> dict:
        return asdict(self)


def judge(candidate: Candidate) -> dict:
    """单个候选的裁决。缺任一条件即 `REJECTED`,并**逐条列出缺什么**。"""
    missing: list[str] = []
    if candidate.kind == "derivative":
        for field_name in DERIVATIVE_REQUIREMENTS:
            if not getattr(candidate, field_name):
                missing.append(field_name)
    elif candidate.kind == "news":
        if candidate.scope != "market_wide":
            missing.append(f"scope={candidate.scope}(需 market_wide)")
        for field_name in NEWS_REQUIREMENTS:
            if not getattr(candidate, field_name):
                missing.append(field_name)
    else:
        missing.append(f"未知 kind {candidate.kind!r}(合法:derivative / news)")
    return {"name": candidate.name, "kind": candidate.kind,
            "verdict": REJECTED if missing else ELIGIBLE,
            "missing": missing,
            "reason": (SELECTIVE_BAN if candidate.kind == "news"
                       and candidate.scope != "market_wide" else "")}


def screen(candidates) -> dict:
    """批量裁决 + 汇总。**默认全拒**:没声明就是没满足。"""
    rows = [judge(c) for c in candidates]
    eligible = [r["name"] for r in rows if r["verdict"] == ELIGIBLE]
    return {
        "schema_version": SCHEMA_VERSION,
        "n_candidates": len(rows),
        "eligible": sorted(eligible),
        "rejected": sorted(r["name"] for r in rows if r["verdict"] == REJECTED),
        "rows": rows,
        "selective_ban": SELECTIVE_BAN,
        "wiring_class": ("温度 v2 接线是 **B 类**(改 regime/温度语义)—— "
                         f"须 registry `{REGISTRY_FAMILY}` ACTIVE 实验"),
    }


def assert_no_selective_news(candidates) -> None:
    """守卫:selective 新闻进温度 → 抛错。"""
    bad = [c.name for c in candidates
           if c.kind == "news" and c.scope != "market_wide"]
    if bad:
        raise ValueError(f"{bad} 是选择性采集 —— {SELECTIVE_BAN}")


def render(report: dict) -> str:
    lines = ["# 温度 v2 输入守卫", "",
             f"> {report['selective_ban']}", "",
             f"- 候选 {report['n_candidates']} 个 · 合格 {len(report['eligible'])} · "
             f"拒绝 {len(report['rejected'])}", "",
             "| 候选 | 类型 | 裁决 | 缺什么 |", "|---|---|---|---|"]
    for row in report["rows"]:
        mark = "✅" if row["verdict"] == ELIGIBLE else "⛔"
        lines.append(f"| `{row['name']}` | {row['kind']} | {mark} {row['verdict']} "
                     f"| {'、'.join(row['missing']) or '—'} |")
    if not report["rows"]:
        lines.append("| — | — | — | — |")
    lines += ["", f"> {report['wiring_class']}", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="温度 v2 输入守卫(§4.5)")
    ap.add_argument("cmd", choices=["check"])
    ap.add_argument("--json-out", default=None)
    a = ap.parse_args(argv)
    # 当前无候选接线 —— 空集裁决,产物本身是「边界还在」的证据
    report = screen([])
    if a.json_out:
        out = Path(a.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
    print(render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
