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

## Codex 逃逸口(D6.5)

`codex exec` 驱动本模块时**禁止 `--ephemeral`**:该开关完全不落 rollout 文件
(`~/.codex/sessions/**/rollout-*.jsonl`),`bind()` / `usage_harvest.collect_run`
从此无源可读 —— 这趟 run 的全部 LLM 证据永久 UNMEASURED,且是「证据从未存在过」,
不是「丢了能补」。`begin()` 因此做两件尽力而为的 B 级记账(**只读**用户的
`~/.codex/config.toml`,从不改它):①把 `web_search` 配置值(`cached`/`live`/…,
探测失败写 `"UNKNOWN"`)写进 `identity/environment.json` 的附加键
`codex_web_search_mode`;②`CODEX_*` 环境变量在场却当日 rollout 目录空/缺席时
打一条 stderr warn(典型病因正是 `--ephemeral`)。两者都不阻断业务 run。
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
    operation: str | None = None,
) -> dict | None:
    """把一个阶段的事实记进当前 run 的现场;没有 run 就什么都不做。

    `operation` = 以哪个操作的身份过写守卫;缺省是旧 CLI 的 `stock.<stage>`。session_v1 的
    计划只登记自己的操作名(FULL 的装配是 `stock.full.assemble`),由调用方显式传入。

    返回 checkpoint 的 dict(便于测试断言),no-op / 取证失败时返回 None ——
    **调用方不看返回值**,业务返回码永远不受取证影响。
    """
    run_id = str(os.environ.get("AUTORESEARCH_RUN_ID", "")).strip()
    if not run_id:
        return None
    try:
        from autoresearch.trace.capsule import checkpoint
        from autoresearch.trace.write_guard import (
            RunWriteViolation,
            assert_write_allowed,
            run_write_lock,
        )

        with run_write_lock(run_id):
            handle = assert_write_allowed(run_id, operation or f"stock.{stage}", ws.ENGINE)
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
    except RunWriteViolation:
        raise
    except Exception as exc:  # noqa: BLE001 — 普通取证故障仍不改业务返回值
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


def _warn_if_codex_rollout_missing(
    today: calendar_date | None = None,
    *,
    sessions_root: Path | None = None,
) -> None:
    """`CODEX_*` 在场却当日 rollout 目录空/缺席 → 打 warn(尽力而为,B 级;D6.5)。

    典型病因:`codex exec --ephemeral` 完全不落 rollout(见本模块顶部文档字符串)。
    这是唯一能在 run 一开始就打旗的地方 —— 之后 `bind()` 只会看到「无候选文件」,
    分不清是真没跑还是被 `--ephemeral` 吃了。
    """
    if not any(key.startswith("CODEX_") for key in os.environ):
        return
    stamp = today or calendar_date.today()
    root = sessions_root if sessions_root is not None else Path.home() / ".codex" / "sessions"
    rollout_dir = root / f"{stamp.year:04d}" / f"{stamp.month:02d}" / f"{stamp.day:02d}"
    if rollout_dir.is_dir() and any(rollout_dir.iterdir()):
        return
    print(
        f"[analyze·capsule] CODEX_* 在场但今日 rollout 目录空/缺席({rollout_dir})"
        " —— 若用了 `codex exec --ephemeral`,本趟 run 的 LLM 证据会永久 UNMEASURED",
        file=sys.stderr,
    )


def _record_codex_escape_hatch(handle) -> None:
    """把 `codex_web_search_mode` 追加进这趟 run 的 `identity/environment.json`(D6.5)。

    只读 `~/.codex/config.toml`,从不改它;`environment.json` 是**这趟 run 自己的**
    身份快照,追加一个键不涉及用户配置。身份快照缺失是既有已知的独立容错路径
    (`capsule._record_identity_snapshot` 本就吞异常并单独记 `EVIDENCE_MISSING`
    事件),这里不为同一件事再吵一遍;只在文件**在场却读不动/不是合法 JSON**这种
    新增的失败模式上才打 warn。
    """
    env_path = handle.capsule / "identity" / "environment.json"
    if not env_path.is_file():
        return
    try:
        payload = json.loads(env_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 — 逃逸口记账失败不阻断业务 run
        print(
            f"[analyze·capsule] codex_web_search_mode 记录失败: {exc}",
            file=sys.stderr,
        )
        return
    from autoresearch.trace.atomic import atomic_write_json
    from autoresearch.trace.identity import detect_codex_web_search_mode

    payload["codex_web_search_mode"] = detect_codex_web_search_mode()
    atomic_write_json(env_path, payload)


def begin(
    ticker: str,
    analysis_date: str,
    *,
    mode: str,
    session_ref: str | None = None,
    peers: str | None = None,
    asset_type: str = "stock",
    name: str | None = None,
    legacy_reason: str | None = None,
) -> dict:
    from autoresearch.contracts.research_access import require_legacy_access
    from autoresearch.trace.capsule import begin_run, freeze_legacy_execution_origin
    require_legacy_access()
    reason = str(legacy_reason or "").strip()
    if not reason:
        raise ValueError("legacy_reason is required")

    if ws.ENGINE == "codex":
        _warn_if_codex_rollout_missing()

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
    if ws.ENGINE == "codex":
        _record_codex_escape_hatch(handle)
    freeze_legacy_execution_origin(
        handle,
        entrypoint="autoresearch.analyze.runctl.begin",
        legacy_reason=reason,
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
    from autoresearch.trace.write_guard import assert_write_allowed, run_write_lock

    with run_write_lock(run_id):
        assert_write_allowed(run_id, "stock.finalize", ws.ENGINE)
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
    start.add_argument("--legacy-reason", required=True)

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
            legacy_reason=args.legacy_reason,
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
