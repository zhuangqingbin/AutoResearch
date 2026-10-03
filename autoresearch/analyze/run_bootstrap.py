#!/usr/bin/env python3
"""一趟 `stock-research` 的运行身份 —— 只认配置,不碰行情/湖/网络(D6.3)。

design: `docs/specs/2026-08-29-full-coverage-research-system-brainstorm.md`;
task: `.superpowers/sdd/2026-08-31-stock-research-p0-p1/task-13-brief.md`。

镜像 `scan/run_bootstrap.prepare_scan_run` 的位置与纪律:它在**分配法证现场之前**跑完,
所以一份非法配置不会留下一个匿名的半截 run 目录。

与 scan 的差别只有「配置是什么」:scan 读 `scan_config.jsonc`(几十个 knob),
单票研究的全部可变量就是**档 + 标的**(外加同业/资产类型/中文简称三个取数参数),
所以这里的 config echo 是一个小白名单 dict,直接冻进 `RunContract.user_config` ——
`capsule.resolve_run_mode` 就是从这个 echo 读 `mode` 的(它没有 `run_mode.json`)。
session_v1 另传已校验的 orchestration_config,嵌套冻结调度配置,不与上述业务键混用。
"""
from __future__ import annotations

import sys
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

from autoresearch.common import workspace as ws
from autoresearch.common.atomic import canonical_json, sha256_bytes
from autoresearch.common.run_identity import RunContract
from autoresearch.contracts.stages import ANALYZE_MODES

#: prompt 本体的**目录**,不是文件名清单。单票研究的 playbook / agent def 会随波次
#: 增删(engine-playbook、lite-playbook、SKILL 已经三份了),写死文件名等于又一份
#: 要人肉同步的登记表 —— 本仓「五份互不派生的登记表」正是这么长出来的。
PROMPT_SKILL_DIR = ".claude/skills/stock-research"
PROMPT_AGENT_DIR = ".claude/agents"
#: 单票研究真正会派发的 agent(full 两个情报员 + lite 决策卡写手)。
PROMPT_AGENTS: tuple[str, ...] = ("company-intel", "l4-card", "us-intel")

#: config echo 的闭集。多一个键当场炸 —— 一个没人消费的键冻进契约,就是一条
#: 「看起来被记下来了」的假证据。
_CONFIG_KEYS = frozenset({"mode", "ticker", "peers", "asset_type", "name"})


def _sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def prompt_hashes() -> dict[str, str]:
    """`{仓内相对路径: sha256}` —— 供 `RunContract.prompt_hashes` 记账。

    为什么 hash 而不是只靠 `git_sha`:agent def 与 playbook **未提交也会生效**
    (会话启动装载的是工作树里那一份)。git 干净、prompt 却被改过的那一趟,
    只有这份 hash 看得出来。缺文件不进表(诚实缺席,不写占位)。
    """
    out: dict[str, str] = {}
    skill_dir = Path(PROMPT_SKILL_DIR)
    if skill_dir.is_dir():
        for path in sorted(skill_dir.glob("*.md")):
            if path.is_file():
                out[path.as_posix()] = _sha256_file(path)
    for name in PROMPT_AGENTS:
        path = Path(PROMPT_AGENT_DIR) / f"{name}.md"
        if path.is_file():
            out[path.as_posix()] = _sha256_file(path)
    return out


def _resolved_config(config: Mapping | None, *, engine: str) -> dict:
    if config is None:
        raise ValueError("stock-research run 必须说明它研究哪只票、跑哪一档")
    if not isinstance(config, Mapping):
        raise TypeError("stock-research config must be a mapping")
    unknown = sorted(set(config) - _CONFIG_KEYS)
    if unknown:
        raise ValueError(f"stock-research config 含未知键: {unknown}")
    mode = str(config.get("mode") or "")
    if mode not in ANALYZE_MODES:
        raise ValueError(f"stock-research mode={mode!r} 非法(可选 {ANALYZE_MODES})")
    ticker = str(config.get("ticker") or "").strip()
    if not ticker:
        raise ValueError("stock-research config 缺 ticker")
    peers = config.get("peers") or []
    if isinstance(peers, str):
        peers = [item.strip() for item in peers.split(",") if item.strip()]
    echo = {
        "mode": mode,
        "ticker": ticker,
        "peers": [str(item) for item in peers],
        "asset_type": str(config.get("asset_type") or "stock"),
        "engine": engine,
    }
    name = config.get("name")
    if name:
        echo["name"] = str(name)
    # canonical 一遍:任何自定义 Mapping / 非 JSON 值都不许漏进不可变契约。
    import json

    return json.loads(canonical_json(echo))


def prepare_analyze_run(
    analysis_date: str,
    *,
    config: Mapping | None = None,
    run_id: str | None = None,
    engine: str | None = None,
    workspace_path: Path | str | None = None,
    session_ref: str | None = None,
    now: datetime | None = None,
    repo_root: Path | str = ".",
    git_sha: str | None = None,
    orchestration_config: Mapping | None = None,
) -> RunContract:
    """Build one complete v3 `stock-research` contract.

    关键字形状与 `prepare_scan_run` 逐字一致 —— `capsule.begin_run` 用同一副调用
    形状调两个 bootstrap(`bootstrap=` 参数),签名分叉就得在 capsule 里写 kind 分支。
    """
    resolved_date = ws.validate_scan_date(analysis_date)
    if run_id is None or workspace_path is None:
        raise ValueError(
            "stock-research 只有 run-scoped 身份:run_id 与 workspace_path 必须成对给"
        )
    resolved_engine = engine or ws.ENGINE
    user_config = _resolved_config(config, engine=resolved_engine)
    if orchestration_config is not None:
        import json

        user_config["orchestration_config"] = json.loads(canonical_json(dict(orchestration_config)))
    return RunContract.build(
        analysis_date=resolved_date,
        user_config=user_config,
        pinned={},
        data_policy={},
        stage_budgets={},
        # 单票研究没有 `CRITICAL_ARTIFACTS` 那样的 schema 版本表(它的产物登记在
        # `contracts/artifacts.py`,还没有版本号);空表是事实,不是占位。
        artifact_schema_versions={},
        prompt_hashes=_safe_prompt_hashes(),
        run_kind="stock-research",
        engine=resolved_engine,
        workspace_path=workspace_path,
        session_ref=session_ref,
        run_id=run_id,
        now=now,
        repo_root=repo_root,
        git_sha=git_sha,
    )


def _safe_prompt_hashes() -> dict[str, str]:
    try:
        return prompt_hashes()
    except Exception as exc:  # noqa: BLE001 - 缺证据要留痕,不能拖垮开跑
        print(
            f"[analyze·bootstrap] prompt_hashes skipped: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return {}


__all__ = ["PROMPT_AGENTS", "prepare_analyze_run", "prompt_hashes"]
