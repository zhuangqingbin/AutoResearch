#!/usr/bin/env python3
"""决策级噪声地板与等价检验(2026-10-03 复盘稿 B1 / §6.3;离线、只读、零 LLM)。

effort 降档、换模型、改 prompt 都要先回答「决策是不是一样」(用户 09-27 裁定:决策级一致)。
做法:同一批冻结任务包,基线配置重跑 k 次 → 自一致率(噪声地板);候选配置同样跑 k 次 → 与基线的
交叉一致率。判据预先写死:每个关键决策位 交叉 ≥ 自一致 − `margin`,且任务数 ≥ `min_tasks`。

决策位(读卡口径与生产同源):评级五档(`agents.utils.rating.validate_rating_and_proposal`)、
入场三态(`scan.l4.parsers.parse_card_context`)、是否早停、硬否决位(SELL 或入场禁止 ——
E6 `no_redflag` 的卡面部分)。

输入目录:`<dir>/<task_id>/<rep>.md`(每个任务一个子目录,每次重跑一张卡)。重跑怎么产生不归本模块
(见实施台账 B1 的运行说明);本模块只读卡、只算一致率。

  uv run --no-sync python -m autoresearch.research.noise_floor \
      --baseline <dir> --candidate <dir> --out <dir> [--margin 0.10] [--min-tasks 20]
"""
from __future__ import annotations

import argparse
import itertools
import json
from pathlib import Path

RATINGS = ("Sell", "Underweight", "Hold", "Overweight", "Buy")
KEY_BITS = ("rating_exact", "veto_exact", "entry_exact")


def decision_bits(text: str) -> dict:
    from autoresearch.agents.utils.rating import validate_rating_and_proposal
    from autoresearch.contracts.agent_output import _ENTRY_LINE_RE
    from autoresearch.scan.l4.parsers import _resolve_entry_line_matches, parse_early_stop

    try:
        rating, proposal = validate_rating_and_proposal(text)
    except Exception:  # noqa: BLE001 - 读不出锚点 = 没有决策位,不猜
        rating = proposal = None
    # 入场只读机读入场行(契约层同一个正则 + 解析器同一套保守取值);不走整卡解析 —— 重跑卡
    # 的仪表盘表格一旦写歪,整卡解析会把入场一起判成 UNKNOWN,把「表格格式」混进「决策是否一致」。
    matches = _ENTRY_LINE_RE.findall(text or "")
    entry = _resolve_entry_line_matches(matches)[0] if matches else "UNKNOWN"
    early = parse_early_stop(text) is not None
    veto = None if proposal is None else (proposal == "SELL" or entry == "PROHIBITED")
    return {"rating": rating, "proposal": proposal, "entry": entry, "early_stop": early,
            "veto": veto}


def _pair(a: dict, b: dict) -> dict:
    distance = (abs(RATINGS.index(a["rating"]) - RATINGS.index(b["rating"]))
                if a["rating"] in RATINGS and b["rating"] in RATINGS else None)
    return {"rating_exact": a["rating"] == b["rating"],
            "rating_within1": distance is not None and distance <= 1,
            "veto_exact": a["veto"] == b["veto"], "entry_exact": a["entry"] == b["entry"],
            "early_exact": a["early_stop"] == b["early_stop"]}


def _mean_by_task(pairs_by_task: dict[str, list[dict]]) -> dict:
    tasks = {task: pairs for task, pairs in pairs_by_task.items() if pairs}
    if not tasks:
        return {"n_tasks": 0, "n_pairs": 0}
    keys = ("rating_exact", "rating_within1", "veto_exact", "entry_exact", "early_exact")
    out = {key: sum(sum(p[key] for p in pairs) / len(pairs) for pairs in tasks.values()) / len(tasks)
           for key in keys}
    return {**out, "n_tasks": len(tasks), "n_pairs": sum(len(p) for p in tasks.values())}


def self_agreement(reps: dict[str, list[dict]]) -> dict:
    """同配置 k 次重跑两两比较,先在任务内平均、再跨任务等权(一个任务跑得多不该权重大)。"""
    return _mean_by_task({task: [_pair(a, b) for a, b in itertools.combinations(bits, 2)]
                          for task, bits in reps.items()})


def cross_agreement(base: dict[str, list[dict]], cand: dict[str, list[dict]]) -> dict:
    return _mean_by_task({task: [_pair(a, b) for a in base[task] for b in cand[task]]
                          for task in sorted(set(base) & set(cand))})


def equivalence(floor: dict, cross: dict, *, margin: float, min_tasks: int) -> dict:
    if min(floor.get("n_tasks", 0), cross.get("n_tasks", 0)) < min_tasks:
        return {"verdict": "INSUFFICIENT", "min_tasks": min_tasks,
                "n_tasks": min(floor.get("n_tasks", 0), cross.get("n_tasks", 0))}
    bits = {key: {"floor": floor[key], "cross": cross[key],
                  "ok": cross[key] >= floor[key] - margin} for key in KEY_BITS}
    return {"verdict": "EQUIVALENT" if all(b["ok"] for b in bits.values()) else "NOT_EQUIVALENT",
            "margin": margin, "bits": bits}


def load(root: Path) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for task_dir in sorted(p for p in Path(root).iterdir() if p.is_dir()):
        cards = sorted(task_dir.glob("*.md"))
        if cards:
            out[task_dir.name] = [decision_bits(p.read_text(encoding="utf-8")) for p in cards]
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--candidate", required=True)
    ap.add_argument("--margin", type=float, default=0.10)
    ap.add_argument("--min-tasks", type=int, default=20)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    base, cand = load(Path(args.baseline)), load(Path(args.candidate))
    floor = self_agreement(base)
    result = {"floor": floor, "candidate_self": self_agreement(cand),
              "cross": cross_agreement(base, cand)}
    result["equivalence"] = equivalence(floor, result["cross"], margin=args.margin,
                                        min_tasks=args.min_tasks)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "noise_floor.json").write_text(json.dumps(result, ensure_ascii=False, indent=1),
                                          encoding="utf-8")
    print(json.dumps(result["equivalence"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
