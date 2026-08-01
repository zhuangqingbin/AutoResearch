#!/usr/bin/env python3
"""策略师 pack —— 从 full market_pack 单向投影出「策略师能看的那部分」(确定性,零 LLM)。

design: docs/specs/2026-08-01-wave10-report-ops-slimdown-zerobuy-design.md §A4

**治的是防锚定连续两日复发**。此前的防线写在 workflow 的 prompt 里:

    「pack 里的 sector_healthy_top3 键是 L5 专用的确定性产物,**忽略它**,
      不得把"看多行业"及其排名写进任何小节」

—— 一句叮嘱管着一份就摆在眼前的数据。07-30/31 连续两日复发,根因不是 agent 不听话,
是**它本来就能读到**。指令级约束的失败率不是零,而数据级约束的失败率是零:看不见就写不出。

所以本模块做的是**投影**而不是过滤:allowlist 之外的键(现在的 `sector_healthy_top3` /
`run_contract` / `user_config`,以及**将来任何新增键**)默认进不来。默认拒绝是关键 ——
白名单式的默认拒绝在加新字段时会安静地少一块(可发现);黑名单式的默认放行在加新字段时
会安静地泄漏一块(不可发现)。

full pack 仍是 L5 / L3 数字 validator 的事实源,本投影不改它一个字节。

  uv run --no-sync python -m autoresearch.scan.strategist_pack <market_pack.json> [-o out.json]
"""
from __future__ import annotations

import json
from pathlib import Path

from autoresearch.scan.run_contract import sha256_json

SCHEMA_VERSION = 1

# 策略师写市场研判需要的地形与量纲。**只加不减需过 review**:每加一个键,
# 就是多给策略师一个可以据以锚定的东西。
ALLOWED_KEYS: tuple[str, ...] = (
    "regime",
    "breadth",
    "money",
    "valuation",
    "temperature",
    "cross_money",
    "index_val",
    "macro_state",
    "macro_state_note",
    "today_slice",
    "sectors",
)

# 明确拒绝并写进产物的键 —— 让"为什么少了它"可查,而不是让人以为投影漏了。
# 未列在这里的新键同样进不来(默认拒绝),只是不会被单独点名。
DENIED_KEYS: tuple[str, ...] = (
    "sector_healthy_top3",   # L5 专用的确定性看多行业排名 —— 策略师看到就会复述
    "run_contract",          # 运行契约:与市场地形无关,且含 pinned 等决策面事实
    "user_config",           # 用户配置:同上
)


class StrategistPackError(ValueError):
    """投影违反了自己的契约 —— 生成时就该失败。"""


def project(pack: dict) -> dict:
    """full market_pack → strategist_pack(单向;不改入参)。

    `source_hash` 锚的是**投影前的整份 pack**:据此可证明"策略师读的这份,确实是从当日
    那份 pack 投出来的",而不用把原始 pack 交给它。
    """
    if not isinstance(pack, dict):
        raise StrategistPackError("market_pack 必须是对象")
    projected = {key: pack[key] for key in ALLOWED_KEYS if key in pack}
    leaked = [key for key in DENIED_KEYS if key in projected]
    if leaked:      # allowlist 与 denylist 撞车 = 有人两边都加了,不能靠"顺序"侥幸
        raise StrategistPackError(f"allowlist 与 denylist 冲突:{leaked}")
    return {
        "schema_version": SCHEMA_VERSION,
        "source_hash": sha256_json(pack),
        "allowed_keys": list(ALLOWED_KEYS),
        "denied_keys": list(DENIED_KEYS),
        "dropped_keys": sorted(set(pack) - set(ALLOWED_KEYS)),
        "pack": projected,
    }


def validate(payload: dict) -> list[str]:
    """校验一份已落盘的投影。返回问题列表,空 = 通过。"""
    problems = []
    if payload.get("schema_version") != SCHEMA_VERSION:
        problems.append(f"schema_version={payload.get('schema_version')}")
    body = payload.get("pack")
    if not isinstance(body, dict):
        return [*problems, "pack 缺失或不是对象"]
    for key in body:
        if key not in ALLOWED_KEYS:
            problems.append(f"越权键 {key!r} 出现在投影里")
    for key in DENIED_KEYS:
        if key in body:
            problems.append(f"明令拒绝的键 {key!r} 泄漏进投影")
    return problems


def write(pack: dict, target: Path | str) -> Path:
    """原子落盘。返回路径。"""
    payload = project(pack)
    problems = validate(payload)
    if problems:                       # 自己校验不过就不该落盘,别让下游去发现
        raise StrategistPackError(";".join(problems))
    path = Path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.name}.tmp")
    temp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)
    return path


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="market_pack → strategist_pack 单向投影")
    ap.add_argument("market_pack", help="full market_pack.json 路径")
    ap.add_argument("-o", "--out", default=None, help="投影落盘路径(缺省打到 stdout)")
    args = ap.parse_args(argv)

    pack = json.loads(Path(args.market_pack).read_text(encoding="utf-8"))
    payload = project(pack)
    if args.out:
        path = write(pack, args.out)
        print(f"[strategist_pack] 保留 {len(payload['pack'])} 键 · "
              f"剔除 {len(payload['dropped_keys'])} 键 → {path}")
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
