#!/usr/bin/env python3
"""把契约层**生成**给 JS 侧 —— 让 workflow 不再各拼各的字面量。

design: `docs/specs/2026-08-29-full-coverage-research-system-brainstorm.md` §2.4 A1 / §2.2 K7。

## 病灶

`.claude/workflows/*.js` 与 python 各持一份同一个概念:

| 概念 | JS | python |
|---|---|---|
| 评级序 | `RANK = {sell:0…buy:4}`(`l4-stock.js:430`) | `RATINGS_5_TIER` Buy=0…Sell=4 —— **方向相反** |
| 任务动作 | `'SKIP'/'RUN'/'BLOCKED'/'WAIT'/'LEGACY'` 字面量 | `scan/l4_tasks.py` 同名字符串 |
| 瞬时错误 | `l4-stock.js:249-256` | `intel_status.TRANSIENT_ERRORS` |
| staging 路径 | `context_${ENGINE}/scan_runs/${RUN_ID}/staging/${date}` | `ws.scan_root()` |
| 阶段串 | `'l4-prep'`、`'finalize'` | `run_profile.SCAN_STAGES` 里没有这两个 |

两份真身意味着两个可以各自漂移的地方,而 JS 侧**没有测试**
(`node --check` 对 ESM + 顶层 return 零鉴别力 —— 写坏了仍 exit 0,
是本仓记过的「永不变红的绿灯」)。

## 做法

python 是唯一真身,JS 侧消费**生成物** `.claude/workflows/_contracts.generated.js`。
生成物带 `contracts_hash`;测试断言「磁盘上的生成物 == 现在重新生成的内容」,
所以改了 python 而忘了重新生成会当场红。

    uv run --no-sync python -m autoresearch.contracts.emit          # 打印到 stdout
    uv run --no-sync python -m autoresearch.contracts.emit --write  # 写入生成物
    uv run --no-sync python -m autoresearch.contracts.emit --check  # CI:不一致则 exit 1
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from autoresearch.contracts import agent_output as ao, artifacts as ca, stages as cs

#: 生成物落点。**不要**手编它 —— 它是 python 的投影。
GENERATED_JS = Path(".claude/workflows/_contracts.generated.js")

_HEADER = """\
// 自动生成,请勿手编 —— 真身是 `autoresearch/contracts/`。
// 重新生成:`uv run --no-sync python -m autoresearch.contracts.emit --write`
//
// 为什么有这个文件:JS 与 python 此前各持一份评级序(方向还相反)、任务动作枚举、
// 阶段串与 staging 路径。两份真身 = 两个可以各自漂移的地方,而 JS 侧没有测试
// (`node --check` 对 ESM 顶层 return 零鉴别力)。现在 python 是唯一真身。
"""


def build_payload() -> dict:
    """生成物的结构化内容(JS 与测试共用)。"""
    return {
        "schema_version": ca.ARTIFACT_REGISTRY_SCHEMA_VERSION,
        "stages": list(cs.STAGES),
        "js_stage_aliases": dict(cs.JS_STAGE_ALIASES),
        "modes": list(cs.MODES),
        "l4_skipping_modes": sorted(cs.L4_SKIPPING_MODES),
        "role_stages": dict(cs.ROLE_STAGES),
        "conditional_roles": sorted(cs.CONDITIONAL_ROLES),
        # 评级:两个方向都由 RATING_ORDER 派生,不再靠人记住哪边是哪边。
        "rating_order": list(ao.RATING_ORDER),
        "rating_rank_js": ao.js_rank_view(),
        "proposals": list(ao.PROPOSALS),
        "stop_reasons": list(ao.STOP_REASONS),
        "ow_gates": list(ao.OW_GATES),
        "artifacts": {
            a.name: {
                "path": a.path,
                "root": a.root,
                "stage": a.stage,
                "presence": a.presence,
            }
            for a in ca.ARTIFACTS
        },
    }


def payload_hash(payload: dict) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def render_js(payload: dict | None = None) -> str:
    payload = payload if payload is not None else build_payload()
    body = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    digest = payload_hash(payload)
    return (
        f"{_HEADER}\n"
        f"export const CONTRACTS_HASH = '{digest}'\n\n"
        f"export const CONTRACTS = {body}\n\n"
        "export const STAGES = CONTRACTS.stages\n"
        "export const MODES = CONTRACTS.modes\n"
        "export const RATING_RANK = CONTRACTS.rating_rank_js\n"
        "export const ARTIFACTS = CONTRACTS.artifacts\n\n"
        "// 阶段串折叠:JS 用连字符,python 用下划线。\n"
        "export const normalizeStage = (name) =>\n"
        "  CONTRACTS.js_stage_aliases[name] ?? name\n\n"
        "// 产物相对路径:JS 侧唯一该拼路径的地方。\n"
        "export const artifactPath = (name) => {\n"
        "  const spec = CONTRACTS.artifacts[name]\n"
        "  if (!spec) throw new Error(`未登记的产物:${name}`)\n"
        "  return spec.path\n"
        "}\n"
    )


def check(root: Path | None = None) -> tuple[bool, str]:
    """磁盘上的生成物是否与现在重新生成的一致。"""
    target = (root or Path.cwd()) / GENERATED_JS
    want = render_js()
    if not target.is_file():
        return False, f"生成物不存在:{target}"
    got = target.read_text(encoding="utf-8")
    if got != want:
        return False, (
            f"生成物已过期:{target}\n"
            "改了 autoresearch/contracts/ 就要重新生成:\n"
            "  uv run --no-sync python -m autoresearch.contracts.emit --write"
        )
    return True, "in sync"


def write(root: Path | None = None) -> Path:
    target = (root or Path.cwd()) / GENERATED_JS
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_js(), encoding="utf-8")
    return target


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="把契约层生成给 JS 侧")
    ap.add_argument("--write", action="store_true", help="写入生成物")
    ap.add_argument("--check", action="store_true", help="不一致则 exit 1")
    args = ap.parse_args(argv)
    if args.check:
        ok, msg = check()
        print(msg)
        return 0 if ok else 1
    if args.write:
        print(f"wrote {write()}")
        return 0
    print(render_js())
    return 0


if __name__ == "__main__":
    sys.exit(main())
