#!/usr/bin/env python3
"""E1:当前编排效率的**只读**基线 + 宿主能力报告。

实施计划:`docs/superpowers/plans/2026-09-06-orchestration-and-layering.md` Task E1。

只读:不拥有预算红黄绿(那是 `scan.budget.observe_run`),不改任何生产参数。它回答的是
「现在一票 finalist 花多少」,好让后面任何「省了多少」的说法有个对得上的基线。

**计划写的字段有一半不存在**(2026-09-06 按 Step 0 逐个 rg 核过):`_budget_observation.json`
里只有 `run_id / measurement_status / weighted_input_proxy / interactive_wall_s /
estimated_usd / budget_band / real_scan`;`mature_finalists`、`metering_version`、
`failed_tasks`、`attempted_tasks` 在这份产物里**查无**——前者要问任务簿/人口账本,后者是
计量层的 schema。所以本模块:

- 按**真实键**读观测,不假设不存在的字段;
- finalist 数与任务成败由调用方显式传入(它们的 owner 是 taskbook / populations,不是预算观测);
- 任何缺失都在 `coverage` 里逐项点名。**缺计量不冒充零成本**:`proxy_per_finalist` 宁可
  是 `None`,也不拿输出字数去估「真实 token」(09-04 那波的教训:估出来的数会被当成实测引用)。

跨模式不合并:`real_scan=False` 的演练与真扫描分组报告 —— 把两者平均掉,单票成本会好看得
莫名其妙。
"""
from __future__ import annotations

#: 从 `_budget_observation.json` 直接读的键(名字以生产产物为准,不是计划草稿里的名字)。
OBSERVATION_KEYS: tuple[str, ...] = (
    "run_id", "measurement_status", "weighted_input_proxy", "interactive_wall_s",
    "estimated_usd", "budget_band", "real_scan",
)
#: 这四项来自**别的** owner,必须由调用方传;传不了就如实标缺。
CALLER_SUPPLIED: tuple[str, ...] = (
    "engine", "mature_finalists", "attempted_tasks", "failed_tasks",
)
CAPABILITIES: tuple[str, ...] = (
    "deterministic_exec", "capture_binding", "inference_handoff", "safe_resume",
)


def efficiency_row(observation: dict, *, engine=None, mature_finalists=None,
                   attempted_tasks=None, failed_tasks=None) -> dict:
    """一次 run 的效率读数 + 覆盖情况。缺什么就是什么,不补。"""
    supplied = {"engine": engine, "mature_finalists": mature_finalists,
                "attempted_tasks": attempted_tasks, "failed_tasks": failed_tasks}
    missing = [key for key in OBSERVATION_KEYS if key not in observation]
    missing += [key for key, value in supplied.items() if value is None]

    weighted = observation.get("weighted_input_proxy")
    measured = observation.get("measurement_status") == "MEASURED"
    count = supplied["mature_finalists"]
    attempted, failed = supplied["attempted_tasks"], supplied["failed_tasks"]
    if attempted is not None and failed is not None and failed > attempted:
        raise ValueError("failed_tasks cannot exceed attempted_tasks")

    return {
        "run_id": observation.get("run_id"),
        "engine": supplied["engine"],
        "real_scan": observation.get("real_scan"),
        "measurement_status": observation.get("measurement_status"),
        "budget_band": observation.get("budget_band"),
        "weighted_input_proxy": weighted if measured else None,
        "estimated_usd": observation.get("estimated_usd") if measured else None,
        "wall_seconds": observation.get("interactive_wall_s"),
        "mature_finalists": count,
        # 未计量 → None。这里给 0 或给一个估值,后面每一句「省了 x%」都建在沙上。
        "proxy_per_finalist": (weighted / count
                               if measured and weighted is not None and count else None),
        "failure_rate": (failed / attempted
                         if attempted and failed is not None else None),
        "coverage": "COMPLETE" if not missing else "MISSING:" + ",".join(sorted(set(missing))),
    }


def efficiency_rows(records) -> list[dict]:
    """一批 `{"observation": ..., 其余是调用方补的字段}` → 逐行读数。"""
    return [efficiency_row(record["observation"],
                           **{k: record.get(k) for k in CALLER_SUPPLIED})
            for record in records]


def cohort_summary(rows) -> dict:
    """按 `(engine, real_scan)` 分组的中位读数。

    只纳入 `coverage == "COMPLETE"` 且 `proxy_per_finalist` 有值的行;被排除的计入
    `excluded`。**跨引擎不合并**(I02:各自的账各自算),真扫描与演练也不合并。
    """
    groups: dict[tuple, list[dict]] = {}
    excluded = 0
    for row in rows:
        if row["coverage"] != "COMPLETE" or row["proxy_per_finalist"] is None:
            excluded += 1
            continue
        groups.setdefault((row["engine"], bool(row["real_scan"])), []).append(row)
    out = []
    for (engine, real_scan), members in sorted(groups.items(), key=lambda kv: str(kv[0])):
        values = sorted(row["proxy_per_finalist"] for row in members)
        walls = sorted(row["wall_seconds"] for row in members if row["wall_seconds"] is not None)
        out.append({"engine": engine, "real_scan": real_scan, "n": len(members),
                    "median_proxy_per_finalist": _median(values),
                    "median_wall_seconds": _median(walls) if walls else None})
    return {"groups": out, "excluded": excluded}


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    mid = len(values) // 2
    return values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2


def capability_report(evidence: dict) -> dict:
    """宿主能力四问 → `{name: {"available": bool, "evidence": str}}`。

    每一项**必须带证据字符串**才能是 `True`:「没试过」与「试过不行」在决策上完全不同,而
    只报一个布尔会把前者读成后者。缺证据一律 `False` + `NO_EVIDENCE`。

    四项的含义(计划 E1 Step 4):能不能直接起进程 / 能不能把执行留证绑到 run / 宿主能不能
    接手推理并回传 / 中断后能不能安全续跑。`inference_handoff` 为假时保留现有 Workflow 或
    人工接续,**不实现假的会话 API**(不变量 I12)。
    """
    unknown = set(evidence) - set(CAPABILITIES)
    if unknown:
        raise ValueError(f"unknown capability: {sorted(unknown)}")
    report = {}
    for name in CAPABILITIES:
        item = evidence.get(name)
        if isinstance(item, dict) and item.get("evidence"):
            report[name] = {"available": bool(item.get("available")),
                            "evidence": str(item["evidence"])}
        else:
            report[name] = {"available": False, "evidence": "NO_EVIDENCE"}
    return report


def runner_is_unblocked(report: dict) -> bool:
    """E3 的放行判据:四项**全部**为真才谈得上换路由。任何一项缺证据即为假。"""
    return all(report[name]["available"] for name in CAPABILITIES)
