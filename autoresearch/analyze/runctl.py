#!/usr/bin/env python3
"""`stock-research` 的现场控制器 —— begin / bind / finalize + 进程内 checkpoint(D6.3/6.4)。

task: `.superpowers/sdd/2026-08-31-stock-research-p0-p1/task-13-brief.md`。

## 为什么不照抄 scan 的 traced 壳

`scan-market` 的每条确定性命令都过 `trace/exec_capture` 的 `tracedAgent` 双壳,
换来 `logs/<stage>/*.log.gz`。设计稿 §1.4/Q2 明确不给单票研究套这层:那是**每条命令
3 次 agent spawn** 的成本病,而一趟单票研究总共才两三条命令。

所以这里的留痕是**进程内 checkpoint**:`record_stage()` 在 harvest / assemble 的
收尾直接调 `capsule.checkpoint`(形状照 `scan/stage_result.safe_record_stage_result`,
**不 import scan** —— `analyze` 排在 `scan` 之下,那会是一条新的向上边)。
对应地,`analyze/run_profile.analyze_profile` 声明 `captured_stages=()`:
没有捕获壳就不欠捕获日志,否则每一趟单票研究都会恒判「证据缺失」= 假警报。

## 不开 RUN_ID 时零留痕

`record_stage()` 在没有 `AUTORESEARCH_RUN_ID` 时是**真 no-op**(不建目录、不写盘、
不打印),与今天逐字相同。取证故障也一律吞成 stderr 一行:一个索引 bug 不该毙掉
用户的研究。

## 产物为什么要复制进 staging

harvest 的落点是 `$CTX/`(`analyze_ctx` 根),不在 run 目录里 —— 这是**故意**的:
开不开 RUN_ID,报告读的都是同一个路径,SKILL 的步骤不用分叉。于是 capsule 要自带
业务产物就只能复制一份进 `staging/<date>/`,`finalize` 再把整个 staging 冻进
`capsule/products/staging/`。原始绝对路径记在 checkpoint 的 metrics 里(`origin`),
所以「这份快照是从哪儿来的」没有丢。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import date as calendar_date
from pathlib import Path

from autoresearch.analyze.run_bootstrap import prepare_analyze_run
from autoresearch.common import workspace as ws
from autoresearch.contracts.stages import ANALYZE_MODES

#: 可以绑 transcript 的角色。`main` = 本会话主 agent(写报告的那个),两个情报员是
#: `contracts.stages.ANALYZE_ROLE_STAGES` 里登记过的腿。
BIND_ROLES: tuple[str, ...] = ("main", "company-intel", "us-intel")

#: `capsule.checkpoint` 认的终态。
_STATUSES: tuple[str, ...] = ("SUCCEEDED", "DEGRADED", "FAILED", "SKIPPED")


# ─────────────────────────────────────────────────── 进程内 checkpoint


def record_stage(
    stage: str,
    *,
    status: str = "SUCCEEDED",
    inputs=(),
    outputs=(),
    metrics: dict | None = None,
    error: str | None = None,
) -> dict | None:
    """把一个阶段的事实记进当前 run 的现场;没有 run 就什么都不做。

    返回 checkpoint 的 dict(便于测试断言),no-op / 取证失败时返回 None ——
    **调用方不看返回值**,业务返回码永远不受取证影响。
    """
    run_id = str(os.environ.get("AUTORESEARCH_RUN_ID", "")).strip()
    if not run_id:
        return None
    try:
        from autoresearch.trace.capsule import checkpoint, require_active_run

        handle = require_active_run(run_id)
        if handle.contract.run_kind != "stock-research":
            raise ValueError(
                f"ambient run {run_id} is a {handle.contract.run_kind!r} run; "
                "analyze 不往别人的现场里写"
            )
        names, origins = _stage_outputs(handle.staging, outputs)
        payload = {
            **(metrics or {}),
            "inputs": [str(item) for item in inputs],
            "origin": origins,
        }
        return checkpoint(
            run_id, stage, status, names, payload, error=error
        ).to_dict()
    except Exception as exc:  # noqa: BLE001 — 取证故障不能改业务返回值
        print(f"[analyze·capsule] {stage} checkpoint 失败: {exc}", file=sys.stderr)
        return None


def _stage_outputs(staging: Path, outputs) -> tuple[list[str], dict[str, str]]:
    """把阶段产物复制进 run staging,返回 (staging 内相对名, 名→原始路径)。

    已经在 staging 里的文件原样引用(不自我复制);不存在的文件仍然登记一行 ——
    `capsule.checkpoint` 会把它记成 MISSING,那是关于这趟 run 的事实,
    比「安静地少一行」诚实。
    """
    staging.mkdir(parents=True, exist_ok=True)
    names: list[str] = []
    origins: dict[str, str] = {}
    for item in outputs:
        source = Path(item)
        name = source.name
        if name in names:
            continue
        names.append(name)
        origins[name] = str(source)
        destination = staging / name
        try:
            if source.is_file() and source.resolve() != destination.resolve():
                shutil.copyfile(source, destination)
        except OSError as exc:  # noqa: PERF203 — 一份拷不动不该拖垮其余的
            print(f"[analyze·capsule] 产物快照失败 {source}: {exc}", file=sys.stderr)
    return names, origins


# ─────────────────────────────────────────────────── CLI verbs


def begin(
    ticker: str,
    analysis_date: str,
    *,
    mode: str,
    session_ref: str | None = None,
    peers: str | None = None,
    asset_type: str = "stock",
    name: str | None = None,
) -> dict:
    from autoresearch.trace.capsule import begin_run

    handle = begin_run(
        "stock-research",
        analysis_date,
        ws.ENGINE,
        {
            "mode": mode,
            "ticker": ticker,
            "peers": peers or [],
            "asset_type": asset_type,
            **({"name": name} if name else {}),
        },
        session_ref=session_ref,
        bootstrap=prepare_analyze_run,
    )
    return {
        "run_id": handle.run_id,
        "analysis_date": handle.analysis_date,
        "engine": handle.engine,
        "mode": mode,
        "ticker": ticker,
        "workspace": str(handle.workspace),
        "staging": str(handle.staging),
        "contract_hash": handle.contract.contract_hash,
    }


def bind(
    run_id: str,
    transcript: str,
    *,
    role: str,
    invocation_id: str | None = None,
    from_ordinal: int | None = None,
    to_ordinal: int | None = None,
) -> dict:
    from autoresearch.trace.capsule import bind_transcript

    if role not in BIND_ROLES:
        raise ValueError(f"unknown role: {role!r}(可选 {BIND_ROLES})")
    return bind_transcript(
        run_id,
        transcript,
        role=role,
        invocation_id=invocation_id or f"{role}-{run_id}",
        start_ordinal=from_ordinal,
        end_ordinal=to_ordinal,
    )


def backfill_manifest_run_id(report_dir: Path | str, run_id: str) -> Path | None:
    """把 manifest v2 的 `run_id: null` 补成真值。

    **必须在 finalize 之前**:finalize 会 `_freeze_tree(final_path)` 把发布目录冻成
    只读,并且 MANIFEST 的哈希是在那之后算的 —— 之后再改一个字节,`verify` 立刻
    报 integrity 失败。「发布目录冻结后不再变」这条不变量在这里没有被破,
    因为回填发生在冻结之前。
    """
    path = Path(report_dir) / "manifest.json"
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("run_id") == run_id:
        return path
    payload["run_id"] = run_id
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return path


def finalize(
    run_id: str,
    *,
    report_dir: str | None = None,
    status: str = "SUCCEEDED",
    reason: str | None = None,
) -> dict:
    from autoresearch.trace.capsule import finalize as capsule_finalize

    if report_dir is not None:
        backfill_manifest_run_id(report_dir, run_id)
    error = None
    if status != "SUCCEEDED":
        error = {
            "error_type": f"Run{status.title()}",
            "reason": reason or "调用方未说明原因",
        }
    outcome = capsule_finalize(run_id, status, report_dir, error=error)
    return {
        "run_id": outcome.run_id,
        "business_status": outcome.business_status.value,
        "evidence_status": outcome.evidence_status.value,
        "replayability": outcome.replayability.value,
        "final_path": str(outcome.final_path),
        "root_hash": outcome.root_hash,
        "durability": outcome.durability,
        "last_reliable_checkpoint": outcome.last_reliable_checkpoint,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="autoresearch.analyze.runctl")
    commands = parser.add_subparsers(dest="command", required=True)

    start = commands.add_parser("begin")
    start.add_argument("ticker")
    start.add_argument("analysis_date", nargs="?")
    start.add_argument("--mode", required=True, choices=list(ANALYZE_MODES))
    start.add_argument("--session-ref")
    start.add_argument("--peers")
    start.add_argument("--asset-type", default="stock")
    start.add_argument("--name")

    attach = commands.add_parser("bind")
    attach.add_argument("run_id")
    attach.add_argument("transcript")
    attach.add_argument("--role", required=True, choices=list(BIND_ROLES))
    attach.add_argument("--invocation-id")
    attach.add_argument("--from-ordinal", type=int)
    attach.add_argument("--to-ordinal", type=int)

    done = commands.add_parser("finalize")
    done.add_argument("run_id")
    done.add_argument("--report-dir")
    done.add_argument(
        "--status", default="SUCCEEDED",
        choices=["SUCCEEDED", "FAILED", "INTERRUPTED"],
    )
    done.add_argument("--reason")

    check = commands.add_parser("verify")
    check.add_argument("run_id")

    save = commands.add_parser("checkpoint")
    save.add_argument("stage")
    save.add_argument("--status", default="SUCCEEDED", choices=list(_STATUSES))
    save.add_argument("--output", action="append", default=[])
    save.add_argument("--metrics-json", default="{}")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "begin":
        result = begin(
            args.ticker,
            args.analysis_date or calendar_date.today().isoformat(),
            mode=args.mode,
            session_ref=args.session_ref,
            peers=args.peers,
            asset_type=args.asset_type,
            name=args.name,
        )
        # 第一行**必须**是可直接 `export` 的形状:SKILL 里写的就是
        # `export AUTORESEARCH_RUN_ID=<回显>`,人和 agent 都只读这一行。
        print(f"RUN_ID={result['run_id']}")
    elif args.command == "bind":
        result = bind(
            args.run_id,
            args.transcript,
            role=args.role,
            invocation_id=args.invocation_id,
            from_ordinal=args.from_ordinal,
            to_ordinal=args.to_ordinal,
        )
    elif args.command == "finalize":
        result = finalize(
            args.run_id,
            report_dir=args.report_dir,
            status=args.status,
            reason=args.reason,
        )
    elif args.command == "verify":
        from autoresearch.trace.capsule import verify

        result = verify(args.run_id)
    else:
        result = record_stage(
            args.stage,
            status=args.status,
            outputs=args.output,
            metrics=json.loads(args.metrics_json),
        )
        if result is None:
            print("[runctl] 没有 AUTORESEARCH_RUN_ID —— 零留痕(与不开 run 时相同)")
            return 0
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
