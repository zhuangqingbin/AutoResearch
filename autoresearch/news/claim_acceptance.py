#!/usr/bin/env python3
"""B5:事件证据校验的功能验收 —— 四项**分母明确**的读数 + 案例契约。

实施计划:`docs/superpowers/plans/2026-09-06-claim-evidence-verification.md` Task B5。

**这是仪器,不是读数。** 80 条人工标注案例是外部依赖(人工复核 + 来源授权),本模块不生成、
不合成、不放宽:`load_cases` 只读别人标好的文件并校验形状,案例不够就如实报 `IMMATURE`。
用自动生成的文本冒充已标注真实样本,是这套验收唯一不能犯的错。

四项读数的分母各不相同,写清楚是为了让人没法混着读:

- `false_pass_rate` = 预测 PASS 里**实际不该 PASS** 的比例(分母 = 预测 PASS 数)。这是本包
  最该压低的那个数:错误 PASS 会让一条捏造的事实拿到「已核实」。
- `false_fail_rate` = 预测 FAIL 里**实际不该 FAIL** 的比例(分母 = 预测 FAIL 数)。错误 FAIL
  是**误指控**——把截断页、把另一个事件的材料读成「来源不支撑」。
- `unknown_rate` = UNKNOWN / 全部案例。它**不是**错误率:UNKNOWN 是「没核过」的诚实表达。
  但它高到接近 1,说明覆盖没长进,B1→B2 的增量是零。
- `missing_text_rate` = 缺原文 / 全部案例。缺原文时任何结论都只能是 UNKNOWN,这个数解释了
  上面那个数有多少来自「材料就没拿到」。

分母为零一律 `None`,不写 0 —— 「没有预测 PASS」与「预测 PASS 全对」是两件事。
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from autoresearch.contracts.claim_evidence import ENUMS, VERDICTS

#: 每条案例必须带的字段。缺一个就不是「可复核的案例」,只是一段印象。
CASE_FIELDS: frozenset[str] = frozenset({
    "case_id", "predicate", "difficulty", "expected", "predicted",
    "has_text", "source_ref", "source_hash", "reviewer", "reviewed_on", "note",
})
#: 每类谓语的三种难度配额(计划 B5 Step 1:每类 7 支持 / 7 反证 / 6 不足)。
DIFFICULTIES: tuple[str, ...] = ("support", "refute", "insufficient")
TARGET_PER_PREDICATE: dict[str, int] = {"support": 7, "refute": 7, "insufficient": 6}
#: 首批四个谓语 × 20 条 = 80。
TARGET_TOTAL = len(ENUMS["predicate"]) * sum(TARGET_PER_PREDICATE.values())


def validate_case(case: dict) -> dict:
    if not isinstance(case, dict) or set(case) != CASE_FIELDS:
        raise ValueError(f"case must carry exactly {sorted(CASE_FIELDS)}")
    if case["predicate"] not in ENUMS["predicate"]:
        raise ValueError(f"unregistered predicate: {case['predicate']!r}")
    if case["difficulty"] not in DIFFICULTIES:
        raise ValueError(f"unknown difficulty: {case['difficulty']!r}")
    for field in ("expected", "predicted"):
        if case[field] not in VERDICTS:
            raise ValueError(f"{field} must be one of {VERDICTS}")
    if type(case["has_text"]) is not bool:
        raise ValueError("has_text must be boolean")
    for field in ("case_id", "reviewer", "reviewed_on", "source_ref"):
        if not isinstance(case[field], str) or not case[field].strip():
            raise ValueError(f"{field} must be non-empty text — 没有复核人就不是人工标注")
    if case["has_text"] and not str(case["source_hash"] or "").strip():
        raise ValueError("case claims to have source text but carries no hash")
    if not case["has_text"] and case["expected"] != "UNKNOWN":
        raise ValueError("no source text can only be expected UNKNOWN")
    return case


def load_cases(path: Path | str) -> list[dict]:
    """读人工标注文件 → 案例列表。重复 case_id 一律抛错(同一条标两次会把配额算虚)。"""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    cases = payload["cases"] if isinstance(payload, dict) else payload
    seen: set[str] = set()
    for case in cases:
        validate_case(case)
        if case["case_id"] in seen:
            raise ValueError(f"duplicate case_id: {case['case_id']}")
        seen.add(case["case_id"])
    return list(cases)


def verification_metrics(rows) -> dict:
    """四项读数 + 分母。分母为零 → `None`,不写 0。"""
    rows = list(rows)
    n = len(rows)

    def wrong_rate(verdict: str):
        selected = [r for r in rows if r["predicted"] == verdict]
        if not selected:
            return None, 0
        wrong = sum(r["expected"] != verdict for r in selected)
        return wrong / len(selected), len(selected)

    false_pass, n_pass = wrong_rate("PASS")
    false_fail, n_fail = wrong_rate("FAIL")
    n_unknown = sum(r["predicted"] == "UNKNOWN" for r in rows)
    n_missing = sum(not r["has_text"] for r in rows)
    return {
        "n": n,
        "false_pass_rate": false_pass, "n_predicted_pass": n_pass,
        "false_fail_rate": false_fail, "n_predicted_fail": n_fail,
        "unknown_rate": n_unknown / n if n else None, "n_unknown": n_unknown,
        "missing_text_rate": n_missing / n if n else None, "n_missing_text": n_missing,
    }


def coverage_by_predicate(rows) -> dict:
    """每个谓语 × 难度的实到数 vs 配额 —— 「少标了哪一类」得看得见。"""
    counts = Counter((r["predicate"], r["difficulty"]) for r in rows)
    out = {}
    for predicate in sorted(ENUMS["predicate"]):
        out[predicate] = {d: {"n": counts.get((predicate, d), 0), "target": TARGET_PER_PREDICATE[d]}
                          for d in DIFFICULTIES}
    return out


def new_coverage(rows) -> dict:
    """B1→B2 的可验证覆盖增量:有多少案例**离开了** UNKNOWN。

    B1 之后每条都是 UNKNOWN(保守判定);B2/B3 的价值就是把其中一部分变成有根据的
    PASS/FAIL。增量为零 = 契约与比较器一条都没用上,那时「减少错误 PASS」不该被说成
    「增加了语义覆盖」。
    """
    decided = [r for r in rows if r["predicted"] in ("PASS", "FAIL")]
    correct = sum(r["predicted"] == r["expected"] for r in decided)
    return {"n_decided": len(decided), "n_correct": correct,
            "share_decided": len(decided) / len(rows) if rows else None}


def acceptance(rows) -> dict:
    """发布条件(计划 B5 Step 4)—— 全部满足才 `PASS`,否则逐条列出没满足哪一项。

    ① 硬边界零错误 PASS;② 每条结果可查(case_id + reviewer + source_ref,由 `validate_case`
    保证);③ 新增可验证覆盖不为零;④ 案例数达标。**80 条不是「总体准确率足够高」的证明**,
    它只是「这四条边界上没有明显破口」。
    """
    rows = list(rows)
    metrics = verification_metrics(rows)
    coverage = new_coverage(rows)
    failures = []
    if metrics["false_pass_rate"]:
        failures.append("FALSE_PASS_PRESENT")
    if not coverage["n_decided"]:
        failures.append("NO_NEW_VERIFIABLE_COVERAGE")
    if metrics["n"] < TARGET_TOTAL:
        failures.append(f"IMMATURE_SAMPLE:{metrics['n']}/{TARGET_TOTAL}")
    short = [f"{p}.{d}" for p, byd in coverage_by_predicate(rows).items()
             for d, got in byd.items() if got["n"] < got["target"]]
    if short:
        failures.append("QUOTA_SHORT:" + ",".join(sorted(short)))
    return {"status": "PASS" if not failures else "BLOCKED", "failures": failures,
            "metrics": metrics, "new_coverage": coverage,
            "quota": coverage_by_predicate(rows)}
