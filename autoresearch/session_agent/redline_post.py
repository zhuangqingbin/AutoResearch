"""场后 redline 的会话层入口(token 防膨胀 M3 / M6)。零 LLM。

``scan.redline`` 在分层上低于 ``session_agent``,读不了冻结任务库;这里先从任务库算出每个推理角色的
消费图,再交给 ``scan.redline.post_run``。``scan_run`` 以子进程调用本模块(与它调用 begin / run /
verify-report 同一个方式),最后一行打印 JSON 摘要。
"""
from __future__ import annotations

import argparse
import json
import sys


def consumption_graph(run_id: str) -> dict[str, list[str]] | None:
    """冻结任务图里:每个推理角色已接受的产物被哪些下游任务(操作名 / 角色)声明为输入。

    这是「生产者有没有接线」(FN-1 家族)的机械证据,不是决策影响:下游拿到 ≠ 改变了结论。
    读不到工作区 = ``None``(没量到),不是「没人消费」。
    """
    try:
        from autoresearch.session_agent import service, store
        from autoresearch.trace.capsule import load_run

        entries = store.read_entries(service._store_path(load_run(run_id)))
    except Exception:  # noqa: BLE001 - an archived run without a readable workspace
        return None
    produced: dict[str, str] = {}
    for entry in entries.values():
        spec = entry.get("spec") or {}
        if spec.get("kind") == "INFERENCE" and entry.get("state") == "SUCCEEDED":
            for artifact in entry.get("accepted_artifacts") or spec.get("output_artifact_ids") or []:
                produced[str(artifact)] = str(spec.get("role"))
    graph: dict[str, set] = {role: set() for role in produced.values()}
    for entry in entries.values():
        spec = entry.get("spec") or {}
        for artifact in spec.get("input_artifact_ids") or []:
            role = produced.get(str(artifact))
            if role is not None:
                graph[role].add(str(spec.get("operation") or spec.get("role")))
    return {role: sorted(consumers) for role, consumers in graph.items()}


def post_run(run_id: str) -> dict:
    from autoresearch.scan import redline

    return redline.post_run(run_id, consumption=consumption_graph(run_id))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m autoresearch.session_agent.redline_post",
                                 description="场后 redline(先算消费图,再判定落盘)")
    ap.add_argument("--run-id", required=True)
    args = ap.parse_args(argv)
    print(json.dumps(post_run(args.run_id), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
