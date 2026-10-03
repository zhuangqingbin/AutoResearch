"""Exclusive casebook freeze with event/issuer grouping and explicit exclusions."""
from __future__ import annotations

import copy
from datetime import date
from pathlib import Path

from autoresearch.common import workspace as ws
from autoresearch.contracts.research_case import REF_FIELDS, validate_case
from autoresearch.research.evidence_refs import read_ref, write_derived


def freeze_casebook(cases, *, split_spec, output_dir):
    if not isinstance(split_spec, dict) or set(split_spec) != {"train", "validation", "test"}:
        raise ValueError("three chronological split windows required")
    previous = None
    for name in ("train", "validation", "test"):
        window = split_spec[name]
        if not isinstance(window, list) or len(window) != 2:
            raise ValueError("split window must be [start,end)")
        start, end = [date.fromisoformat(v) for v in window]
        if start >= end or (previous and start < previous):
            raise ValueError("split windows overlap or are unordered")
        previous = end
    rows, seen = copy.deepcopy(list(cases)), set()
    for row in rows:
        validate_case(row)
        if row["case_id"] in seen or row["engine"] != ws.ENGINE:
            raise ValueError("duplicate case or engine mismatch")
        seen.add(row["case_id"])
        window = split_spec[row["split"]]
        if not window[0] <= row["analysis_date"] < window[1]:
            raise ValueError("case outside its declared time split")
        for key in REF_FIELDS:
            for ref in row[key]:
                read_ref(ref)
    # Connected components prevent aliases of event groups from crossing splits.
    groups = [{i} for i in range(len(rows))]
    changed = True
    while changed:
        changed = False
        for i in range(len(groups)):
            if not groups[i]:
                continue
            keys = {(rows[n]["security"], rows[n]["event_family"]) for n in groups[i]}
            ids = {rows[n]["group_id"] for n in groups[i]}
            for j in range(i + 1, len(groups)):
                if any((rows[n]["security"], rows[n]["event_family"]) in keys or rows[n]["group_id"] in ids
                       for n in groups[j]):
                    groups[i].update(groups[j])
                    groups[j].clear()
                    changed = True
    for group in groups:
        if len({rows[i]["split"] for i in group}) > 1:
            for i in group:
                rows[i].update(eligibility="EXCLUDED", exclusion_reason="RELATED_EVENT_CROSSES_SPLIT")
    directory = Path(output_dir)
    if not directory.resolve().is_relative_to(ws.context_root().resolve()):
        raise ValueError("casebook outside engine scope")
    directory.mkdir(parents=True, exist_ok=False)
    return write_derived(directory / "casebook.json", {
        "schema_version": 1, "engine": ws.ENGINE, "split_spec": split_spec, "cases": rows,
        "sampling": {"rule": "ALL_SUBMITTED_CASES", "seed": None},
        "split_policy": "CONNECTED_EVENT_ISSUER_GROUPS_CROSSING_WINDOWS_EXCLUDED",
        "human_identity_assurance": "EXTERNAL_REVIEW_ATTESTATION_NOT_AUTHENTICATION"})


_STARTERS = {
    "FACT_ERROR": ("计划回购误写完成", "减持数量单位误读", "订单意向当确单", "利润累计值当单季"),
    "TIME_ERROR": ("FRED未来修订", "次日公告混入当日", "缺交易日顺延标签", "当前行业回填历史"),
    "METRIC_ERROR": ("主动净额称机构吸筹", "大单规模称机构身份", "百分数与小数混用", "万元与亿元混用"),
    "MATERIAL_OMISSION": ("未声明关键催化", "负面列背景", "摘要漏偿债反证", "配置表漏汇率前提"),
    "INFERENCE_OVERREACH": ("政策计划当收入确定", "来源PASS当上涨概率", "市场健康当个股评级", "支持前提当推断证明"),
    "DUPLICATE_EVIDENCE": ("转载视独立来源", "同公告多URL", "同日重跑当独立样本", "根子会话重复用量"),
    "ACCESS_PUBLICATION_FAILURE": ("过期attempt提交", "跨引擎读入", "未绑定报告", "取消后晚到提交"),
    "EXECUTION_MISMATCH": ("未退出记零收益", "缺成交称未成交", "收盘价冒充盘中可成交", "部分成交虚构全平"),
    "ABSTENTION": ("零买日保留", "缺来源暂缓", "概率不足拒绝声明", "失败不能称持币"),
    "SUCCESS": ("真实单成员行业", "精确交易日腿", "原始字节可回放", "同key并发只取一次"),
}


def starter_cases(*, engine):
    """Forty synthetic regression proposals; no fabricated historical runs/gold."""
    rows = []
    for category, scenarios in _STARTERS.items():
        for index, scenario in enumerate(scenarios, 1):
            case_id = f"synthetic-{category.lower()}-{index}"
            row = {"schema_version": 1, "case_id": case_id, "engine": engine,
                "event_family": case_id, "security": "SYNTHETIC_FIXTURE", "analysis_date": "2026-01-01",
                "workflow": "offline-regression", "profile": "research-process-starter-v1",
                "knowledge_cutoff": "2026-01-01T00:00:00+00:00", "question": scenario,
                "expected_behavior": f"复现并人工核对：{scenario}；未知保持未知，证据与收益分开。",
                **{key: [] for key in REF_FIELDS}, "label_state": "PROPOSED", "gold_label": None,
                "reviewer": [], "reviewed_at": None, "label_version": "proposal-v1", "disagreements": [],
                "failure_type": category, "severity": "HIGH", "suspected_stage": "TO_BE_REVIEWED",
                "confirmed_cause": None, "counterexample": f"合成场景候选：{scenario}", "split": "train",
                "group_id": case_id, "eligibility": "CANDIDATE",
                "exclusion_reason": "SYNTHETIC_PROPOSAL_NEEDS_INPUTS_AND_HUMAN_REVIEW"}
            rows.append(validate_case(row))
    return rows


def main():
    import argparse
    import json

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["starter", "freeze"])
    parser.add_argument("--request", help="JSON containing cases and split_spec (freeze only)")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if args.command == "starter":
        result = write_derived(Path(args.output), {"schema_version": 1,
            "origin": "SYNTHETIC_REGRESSION_PROPOSALS", "target_quotas": dict.fromkeys(_STARTERS, 4),
            "cases": starter_cases(engine=ws.ENGINE), "human_gold_count": 0})
    else:
        if not args.request:
            parser.error("freeze requires --request")
        from autoresearch.research.evidence_refs import read_request

        request = read_request(args.request)
        result = freeze_casebook(request["cases"], split_spec=request["split_spec"], output_dir=args.output)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
