#!/usr/bin/env python3
"""情报分层的证据读数(2026-10-03 复盘稿 B6 / Q6;离线、只读)。

候选方案:T0+24h 必查面给所有票;六面全量情报只给硬门后幸存者与 📌。要回答的是「不给其余票全量情报
会不会丢决策」—— 证据来自 two-stage-v1 卡(`card_research_profile=two-stage-v1`)的
`session_attempts/<code>/a<n>/changes.json`:不读情报的初评 → 读了情报与先验的终评,改了哪些字段
(`changed_fields`)、新证据引用里有没有情报(`new_evidence_refs` 以 `.intel*` 结尾)。按 E6 硬门
幸存 / 被拒分两组汇总。没有 two-stage 记录 → 空读数(不是「情报没用」)。

  uv run --no-sync python -m autoresearch.research.intel_tiering --out <dir>
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

_INTEL_SUFFIXES = (".intel", ".intel_doc", ".intel_bundle", ".intel_status")


def _json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _stagings(root: Path):
    yield from sorted(Path(root).glob("*/trace/staging"))
    yield from sorted(Path(root).glob("runs/*/p*/report/trace/staging"))


def readout(reports_scan_root: Path) -> list[dict]:
    rows: list[dict] = []
    for staging in _stagings(reports_scan_root):
        attempts = sorted(staging.glob("session_attempts/*/a*/changes.json"))
        if not attempts:
            continue
        decision = _json(staging / "_relative_buy_decision.json") or {}
        eligible = {str(c.get("code")).zfill(6): bool(c.get("eligible"))
                    for c in decision.get("candidates") or [] if isinstance(c, dict)}
        run = staging.parents[1].name
        for path in attempts:
            code = path.parents[1].name
            changes = _json(path) or {}
            fields = list(changes.get("changed_fields") or [])
            refs = [str(r) for r in changes.get("new_evidence_refs") or []]
            rows.append({"run": run, "code": code, "survivor": eligible.get(code),
                         "changed": bool(fields), "rating_changed": "rating" in fields,
                         "intel_cited": any(r.endswith(_INTEL_SUFFIXES) for r in refs),
                         "n_changed": len(fields)})
    return rows


def summarize(rows: list[dict]) -> dict:
    def group(subset: list[dict]) -> dict:
        return {"n": len(subset), "changed": sum(r["changed"] for r in subset),
                "rating_changed": sum(r["rating_changed"] for r in subset),
                "intel_cited": sum(r["intel_cited"] for r in subset)}

    return {"survivor": group([r for r in rows if r["survivor"] is True]),
            "rejected": group([r for r in rows if r["survivor"] is False]),
            "unknown": group([r for r in rows if r["survivor"] is None])}


def main(argv: list[str] | None = None) -> int:
    from autoresearch.common import workspace as ws

    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--reports-root", default=None)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    root = Path(args.reports_root) if args.reports_root else ws.reports_root() / "scan"
    rows = readout(root)
    summary = summarize(rows)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(out / "intel_tiering.csv", index=False)
    (out / "intel_tiering.md").write_text(
        "# 情报分层证据(two-stage-v1 初评 → 终评,只记账)\n\n"
        + json.dumps(summary, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
