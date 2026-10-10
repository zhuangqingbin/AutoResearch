"""推理前零推理回放门(token 防膨胀 M9 · 红线 R1)。零 LLM。

开一场新扫描之前,用**当前代码**的领域校验器重验最近一场里每个已被接受的研究产物(决策卡、复核、
情报、L3、行业 brief、策略师)—— 喂的就是那场冻结下来的 ``accepted_submission``。

- 原来接受、现在拒绝 / 校验器自己崩了 = 推理之后的确定性代码被改坏了。10-03 第 4 场(6 张卡契约缺陷)、
  10-07 第 5 场(复核的情景精度契约)都是这一类:研究的钱花完才在校验处死。这里在花钱之前就拦住。
- 原来拒绝(被新尝试取代)的不重验:它的产物没有绑定可读的版本。

判 FAIL 时 ``scan_run`` 不开场;看过之后 ``--ack-redline <被回放的 run_id>`` 放行(与断路器同一个确认)。
这道门只能抓「校验 / 契约层回归」,抓不到新输入形状,也不替代组装阶段的测试。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

from autoresearch.common import workspace as ws

SCHEMA_VERSION = 1


def candidate_runs() -> list[str]:
    """本引擎 scan 工作区里的 run,新的在前。"""
    root = ws.context_root() / "scan_runs"
    if not root.is_dir():
        return []
    return sorted((path.name for path in root.iterdir() if path.is_dir()), reverse=True)


def replay(run_id: str) -> dict:
    """``run_id`` 里每个已接受的推理任务过一遍当前校验器。"""
    from autoresearch.session_agent import service, store
    from autoresearch.session_agent.validation import (
        DomainValidationError,
        validate_registered_domain_contract,
    )
    from autoresearch.trace.capsule import load_run

    handle = load_run(run_id)
    entries = store.read_entries(service._store_path(handle))
    results = []
    for task_id, entry in sorted(entries.items()):
        spec = entry.get("spec") or {}
        submission = entry.get("accepted_submission")
        if spec.get("kind") != "INFERENCE" or entry.get("state") != "SUCCEEDED" or not submission:
            continue
        row = {"task_id": task_id, "role": spec.get("role"), "attempt": entry.get("attempt")}
        try:
            validate_registered_domain_contract(handle, submission, spec)
            row["now"] = "ACCEPT"
        except DomainValidationError as exc:
            row.update(now="REJECT", reason=str(exc)[:300])
        except Exception as exc:  # noqa: BLE001 - a crashing validator is the regression we look for
            row.update(now="CRASH", reason=f"{type(exc).__name__}: {exc}"[:300])
        results.append(row)
    regressions = [row for row in results if row["now"] != "ACCEPT"]
    return {"run_id": run_id, "checked": len(results), "regressions": regressions}


def gate(*, acknowledged=None) -> dict:
    """最近一场有已接受推理产物的 run 做回放;``acknowledged(run_id) -> bool`` 已确认则放行。"""
    from autoresearch.scan import redline

    acknowledged = acknowledged or (lambda run_id: redline.ack_path(run_id).is_file())
    skipped = []
    for run_id in candidate_runs():
        try:
            result = replay(run_id)
        except Exception as exc:  # noqa: BLE001 - an unreadable old workspace is not a regression
            skipped.append({"run_id": run_id, "reason": f"{type(exc).__name__}: {exc}"[:200]})
            continue
        if result["checked"] == 0:
            skipped.append({"run_id": run_id, "reason": "没有已接受的推理产物"})
            continue
        verdict = "FAIL" if result["regressions"] else "PASS"
        if verdict == "FAIL" and acknowledged(run_id):
            verdict = "ACKNOWLEDGED"
        return {"schema_version": SCHEMA_VERSION, "verdict": verdict, **result,
                "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "skipped": skipped[:5]}
    return {"schema_version": SCHEMA_VERSION, "verdict": "SKIPPED", "run_id": None, "checked": 0,
            "regressions": [], "skipped": skipped[:5]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m autoresearch.scan.replay_gate", description=__doc__.splitlines()[0])
    ap.add_argument("--run-id", help="只回放这一场(缺省:最近一场有已接受产物的)")
    args = ap.parse_args(argv)
    result = replay(args.run_id) if args.run_id else gate()
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 1 if result.get("regressions") and result.get("verdict", "FAIL") == "FAIL" else 0


if __name__ == "__main__":
    sys.exit(main())
