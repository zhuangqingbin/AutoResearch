#!/usr/bin/env python3
"""L4 派发下沉 —— capability/chaos probe 账本(确定性,零 LLM;**不改生产调度**)。

design: docs/specs/2026-08-03-scan-next-wave-brainstorm-design.md §4.3

## 当前裁定:未验证,不得写成既定工具契约

设计稿原话:「**嵌套 workflow 的基本调用、恢复和故障语义均未在本仓得到验证**,
不能先写成既定工具契约;主会话份额降至 15% 和每日节省 `$5–8` 也**只是待测假设**。」

而且它与 Wave9 的「L4 单工作流全链」**直接重叠** → 继承矩阵标 `需用户重裁`,
不得并行拥有两套权威方案(候选 `G43_nested_l4_dispatch`)。

## 这个账本存在的唯一理由

`UNTESTED` **不得被读成 `PASS`**。上一波的判例是「产物能证明跑过什么、不能证明没跑过什么」;
这里的对偶是:**没测过的能力不能因为"听起来应该没问题"就当成有**。
所以每一项都有三态,且 `summary()` 把 UNTESTED 单独计数、`ready_for_ab()` 要求
**全部 PASS** 才放行。

## 失败语义(§4.3 末段)

保持「每股一个可独立恢复单元」。单票失败先按 task book 重试;仍失败时**默认阻断
assemble**。若未来允许 degraded publication,必须另立策略并在报告显式列缺失票 ——
**不能 catch 后静默少一票**(OPEN-Q-8 的默认答案仍是「不允许」)。

  uv run --no-sync python -m autoresearch.research.nested_probe status
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from autoresearch.common import workspace as ws

SCHEMA_VERSION = 1
DEFAULT_LEDGER = ws.context_root() / "research/nested_probe.json"
REGISTRY_FAMILY = "l4_dispatch_topology"

PASS, FAIL, UNTESTED = "PASS", "FAIL", "UNTESTED"
VERDICTS = (PASS, FAIL, UNTESTED)

# §4.3 逐项列出的探针。**顺序即依赖**:基本调用不通,后面的都不必测。
PROBES: tuple[tuple[str, str], ...] = (
    ("basic_invocation", "玩具父 + 两个子:父能否调起子 workflow"),
    ("args_and_result", "args 传入与 result 传出的形状与大小上限"),
    ("concurrency_cap", "并发上限:父下挂 N 个子时的实际并行度"),
    ("parent_cancel", "父取消 → 子是否随之终止(还是变孤儿继续烧钱)"),
    ("child_failure", "子失败 → 父看到什么(异常/空结果/静默)"),
    ("timeout", "子超时的语义与可观测性"),
    ("parent_death", "父进程被 kill → 子的归宿"),
    ("resume_from_run_id", "resumeFromRunId 对嵌套结构是否有效"),
    ("task_book_lease", "task-book lease/heartbeat 在父子两层的语义"),
    ("idempotent_rerun", "重复执行的幂等性(同一票不得出两张卡)"),
)
_PROBE_KEYS = tuple(k for k, _ in PROBES)

UNTESTED_DISCIPLINE = (
    "`UNTESTED` **不得读成 `PASS`** —— 没测过的能力不能因为「听起来应该没问题」就当成有")

FAILURE_SEMANTICS = (
    "保持「每股一个可独立恢复单元」。单票失败先按 task book 重试;仍失败时**默认阻断 "
    "assemble**。degraded publication 若要允许,必须另立策略并在报告显式列缺失票 —— "
    "不能 catch 后静默少一票")

UNTESTED_ASSUMPTIONS = {
    "main_session_share_15pct": "主会话份额降至 15% —— **待测假设**(当前实测 32.7%)",
    "daily_saving_5_to_8_usd": "每日节省 $5–8 —— **待测假设**,须 A/B end-to-end 实测",
    "nested_workflow_contract": "嵌套 workflow 的调用/恢复/故障语义 —— **本仓未验证**",
}

AB_REQUIREMENT = (
    "A/B telemetry 必须测 **end-to-end** prompt/cache/token/墙钟,"
    "而不是只看父会话账单;子返回须有紧凑 result contract,防止结果重新灌满父上下文")


@dataclass
class ProbeResult:
    key: str
    description: str
    verdict: str = UNTESTED
    evidence: str = ""
    observed_at: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class ProbeLedger:
    results: dict = field(default_factory=dict)
    schema_version: int = SCHEMA_VERSION

    def as_dict(self) -> dict:
        return {"schema_version": self.schema_version,
                "results": {k: v if isinstance(v, dict) else v.as_dict()
                            for k, v in self.results.items()}}


def fresh_ledger() -> ProbeLedger:
    return ProbeLedger(results={k: ProbeResult(k, desc).as_dict()
                                for k, desc in PROBES})


def load(path: Path | str | None = None) -> ProbeLedger:
    target = Path(path or DEFAULT_LEDGER)
    if not target.exists():
        return fresh_ledger()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fresh_ledger()
    base = fresh_ledger()
    for key, row in (payload.get("results") or {}).items():
        if key in base.results and isinstance(row, dict):
            base.results[key].update({k: v for k, v in row.items()
                                      if k in ("verdict", "evidence", "observed_at")})
    return base


def save(ledger: ProbeLedger, path: Path | str | None = None) -> Path:
    target = Path(path or DEFAULT_LEDGER)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(json.dumps(ledger.as_dict(), ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    tmp.replace(target)
    return target


def record(key: str, verdict: str, *, evidence: str = "",
           path: Path | str | None = None, at: str | None = None) -> dict:
    if key not in _PROBE_KEYS:
        raise ValueError(f"未知探针 {key!r};合法:{list(_PROBE_KEYS)}")
    if verdict not in VERDICTS:
        raise ValueError(f"未知裁决 {verdict!r};合法:{list(VERDICTS)}")
    ledger = load(path)
    ledger.results[key].update({
        "verdict": verdict, "evidence": evidence,
        "observed_at": at or datetime.now(timezone.utc).isoformat()})
    save(ledger, path)
    return summary(ledger)


def summary(ledger: ProbeLedger | None = None, *,
            path: Path | str | None = None) -> dict:
    led = ledger or load(path)
    counts = dict.fromkeys(VERDICTS, 0)
    for row in led.results.values():
        counts[row.get("verdict", UNTESTED)] = counts.get(
            row.get("verdict", UNTESTED), 0) + 1
    untested = sorted(k for k, r in led.results.items()
                      if r.get("verdict") == UNTESTED)
    failed = sorted(k for k, r in led.results.items() if r.get("verdict") == FAIL)
    return {
        "schema_version": SCHEMA_VERSION,
        "counts": counts,
        # UNTESTED **单独**计数 —— 它不属于 PASS,也不属于 FAIL
        "untested": untested, "failed": failed,
        "n_probes": len(led.results),
        "capability_verified": not untested and not failed,
        "untested_discipline": UNTESTED_DISCIPLINE,
        "failure_semantics": FAILURE_SEMANTICS,
        "untested_assumptions": UNTESTED_ASSUMPTIONS,
        "ab_requirement": AB_REQUIREMENT,
        "inheritance": {"Wave9": "需用户重裁 —— 与 L4 单工作流全链直接重叠,"
                                 "不得并行拥有两套权威方案"},
        "results": led.as_dict()["results"],
    }


def ready_for_ab(path: Path | str | None = None) -> dict:
    """能不能进 A/B —— **三门全过**:capability、恢复性不劣、实测成本(§4.3 末段)。

    capability 这一门在这里判;另两门要真跑 A/B 才有数据,所以此处只能给 `PENDING`,
    **不能**因为 capability 过了就说「可以换调度」。
    """
    info = summary(path=path)
    return {
        "capability": "PASS" if info["capability_verified"] else "BLOCKED",
        "capability_blockers": {"untested": info["untested"], "failed": info["failed"]},
        "recoverability_not_worse": "PENDING —— 需 A/B 实测,不能由 capability 推出",
        "measured_cost": "PENDING —— 需 end-to-end telemetry,不能只看父会话账单",
        "verdict": "NOT_READY" if not info["capability_verified"] else "CAPABILITY_ONLY",
        "note": ("只有 capability、恢复性不劣、实测成本三门都过,才可进 registry "
                 "challenger;否则**保留现行主会话滑窗**"),
        "registry_family": REGISTRY_FAMILY,
    }


def render(info: dict) -> str:
    lines = ["# L4 派发下沉 · capability/chaos probe", "",
             f"> ⚠️ {info['untested_discipline']}", "",
             f"- 探针 {info['n_probes']} 项:PASS {info['counts'][PASS]} · "
             f"FAIL {info['counts'][FAIL]} · **UNTESTED {info['counts'][UNTESTED]}**",
             f"- capability 已验证:{'是' if info['capability_verified'] else '**否**'}",
             "",
             "| 探针 | 裁决 | 证据 |", "|---|---|---|"]
    mark = {PASS: "✅", FAIL: "🚨", UNTESTED: "⏳"}
    for key, desc in PROBES:
        row = info["results"].get(key, {})
        verdict = row.get("verdict", UNTESTED)
        lines.append(f"| {desc} | {mark[verdict]} {verdict} "
                     f"| {row.get('evidence') or '—'} |")
    lines += ["", "## 待测假设(**不是**已知事实)", ""]
    lines += [f"- `{k}`:{v}" for k, v in info["untested_assumptions"].items()]
    lines += ["", "## 失败语义", "", f"- {info['failure_semantics']}",
              "", "## A/B 要求", "", f"- {info['ab_requirement']}",
              "", "## 权威冲突", "",
              f"- Wave9:{info['inheritance']['Wave9']}", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="L4 嵌套派发 probe(§4.3;不改生产调度)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("status", help="账本 + 是否可进 A/B")
    s.add_argument("--ledger", default=None)
    s.add_argument("--md-out", default=None)
    r = sub.add_parser("record", help="记一项探针结果")
    r.add_argument("key", choices=list(_PROBE_KEYS))
    r.add_argument("verdict", choices=list(VERDICTS))
    r.add_argument("--evidence", default="")
    r.add_argument("--ledger", default=None)

    a = ap.parse_args(argv)
    if a.cmd == "record":
        info = record(a.key, a.verdict, evidence=a.evidence, path=a.ledger)
        print(json.dumps({k: info[k] for k in ("counts", "untested", "failed")},
                         ensure_ascii=False))
        return 0
    info = summary(path=a.ledger)
    text = render(info)
    if a.md_out:
        out = Path(a.md_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
    print(text)
    print(json.dumps(ready_for_ab(a.ledger), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
