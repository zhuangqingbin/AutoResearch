#!/usr/bin/env python3
"""E6 召回面消融(2026-10-03 复盘稿 B3;离线、只读、零 LLM、零网络)。

评价协议 §1「E6 召回」实验:同一硬门、同一候选池、同一 max_buys,**只**把 `recall_strength` 移出
排序,看 BUY 会不会换人、换了之后主尺上是好是坏。动机:`recall_strength` = n_channels 分位与逐路
分位的平均,而 n_channels 在 L1 召回里已经加过一次分 —— 多路召回可能被重复奖励;账本上它对主尺
的截面 IC 为负(`stage_rulers` 的 `l1_n_channels_ic`)。

做法:逐份已发布 `_relative_buy_decision.json`(只取 `mode=active ∧ pool=finalists`;composite 池
本来就只按 `target_align` 排,消融无意义)用候选表里冻结的四面分重放排序 —— 先用四面重放核对能复现
当日真实 BUY(`fidelity`),再用三面(去掉 `recall_strength`)重放;两边的主尺读数取自账本 universe
层标签。另报每个面在当日 eligible 候选内对主尺的秩相关(复盘稿「target_align 方向复核」)。

只记账:结论交给人读,不改 E6 规则、不写任何生产产物。

  uv run --no-sync python -m autoresearch.research.e6_ablation --out <dir>
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

FACES_FULL = ("target_align", "recall_strength", "evidence", "risk_safety")
FACES_ABLATED = ("target_align", "evidence", "risk_safety")
#: 一天的 eligible 候选通常只有 5–12 只;少于这个数不算面 IC。
MIN_FACE_IC_NAMES = 5


def _rank(cands: list[dict], faces: tuple[str, ...], *, exclude_pinned: bool, max_buys: int,
          tiering: bool) -> list[str]:
    """与 `scan.relative_buy` finalists 池同一套排序:面均分 → target_align → 流动性 → 代码;分级时 A 桶优先。

    流动性用候选表里的 `amount_pctl`(决策时用的是成交额本身,二者同序)。
    """
    pool = [c for c in cands if c.get("eligible") and c.get("in_pool", True)
            and not (exclude_pinned and c.get("pinned"))]

    def score(c: dict) -> float:
        return sum(float(c["faces"][f]) for f in faces) / len(faces)

    pool.sort(key=lambda c: (-score(c), -float(c["faces"]["target_align"]),
                             -float(c.get("amount_pctl") or 0.0), str(c["code"])))
    if tiering:
        allowed = [c for c in pool if (c.get("card_context") or {}).get("entry_stance") == "ALLOWED"]
        pool = allowed or [c for c in pool if c not in allowed]
    return [str(c["code"]) for c in pool[:max_buys]]


def replay(decision: dict) -> dict | None:
    """一份决策 → 实际 / 四面重放 / 三面重放的 BUY;不是 active finalists 池 → None。"""
    if decision.get("mode") != "active" or decision.get("pool") != "finalists":
        return None
    cands = [c for c in decision.get("candidates") or []
             if isinstance(c, dict) and isinstance(c.get("faces"), dict)]
    kw = {"exclude_pinned": bool(decision.get("exclude_pinned", True)),
          "max_buys": int(decision.get("max_buys") or 1),
          "tiering": bool(decision.get("tiering"))}
    replayed = _rank(cands, FACES_FULL, **kw)
    ablated = _rank(cands, FACES_ABLATED, **kw)
    actual = [str(b.get("code")) for b in decision.get("buys") or [] if b.get("code")]
    return {"actual": actual, "replayed": replayed, "ablated": ablated,
            "fidelity": replayed == actual, "changed": replayed != ablated}


def _gaps(date: str, ledger_root: Path) -> dict[str, float]:
    path = Path(ledger_root) / "evaluations/outcome_labels.v2/universe" / f"{date}.parquet"
    if not path.is_file():
        return {}
    labels = pd.read_parquet(path, columns=["code", "gap_c1_o2", "status_gap_c1_o2", "buyable_c1"])
    labels["code"] = labels["code"].astype(str).str.zfill(6)
    ok = (labels["status_gap_c1_o2"] == "MATURE") & labels["buyable_c1"].fillna(False).astype(bool)
    labels = labels[ok & labels["gap_c1_o2"].notna()].drop_duplicates("code")
    return dict(zip(labels["code"], labels["gap_c1_o2"].astype(float), strict=True))


def face_ic(decision: dict, *, ledger_root: Path) -> dict[str, float | None]:
    """当日 eligible 候选内,每个面对主尺的秩相关;可算票不足或面无差异 → None。"""
    gaps = _gaps(str(decision.get("date") or ""), ledger_root)
    rows = [c for c in decision.get("candidates") or []
            if isinstance(c, dict) and c.get("eligible") and isinstance(c.get("faces"), dict)
            and str(c.get("code")) in gaps]
    out: dict[str, float | None] = {}
    for face in FACES_FULL:
        frame = pd.DataFrame({"x": [float(c["faces"][face]) for c in rows],
                              "y": [gaps[str(c["code"])] for c in rows]})
        if len(frame) < MIN_FACE_IC_NAMES or frame["x"].nunique() < 2 or frame["y"].nunique() < 2:
            out[face] = None
        else:
            out[face] = float(frame["x"].rank().corr(frame["y"].rank()))
    return out


def _published_decisions(reports_scan_root: Path):
    for path in sorted(Path(reports_scan_root).glob("*/trace/staging/_relative_buy_decision.json")):
        try:
            yield path.parents[2].name, json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue


def readout(reports_scan_root: Path, *, ledger_root: Path) -> list[dict]:
    """逐场:实际 BUY、去掉召回面后的 BUY、是否换人、重放保真、两只票的主尺读数(缺标签 = None)。"""
    rows: list[dict] = []
    for run, decision in _published_decisions(reports_scan_root):
        got = replay(decision)
        if got is None:
            continue
        date = str(decision.get("date") or "")
        gaps = _gaps(date, ledger_root)
        actual = got["actual"][0] if got["actual"] else None
        ablated = got["ablated"][0] if got["ablated"] else None
        rows.append({"run": run, "date": date, "actual": actual, "ablated": ablated,
                     "changed": got["changed"], "fidelity": got["fidelity"],
                     "gap_actual": gaps.get(actual) if actual else None,
                     "gap_ablated": gaps.get(ablated) if ablated else None})
    return rows


def summarize(rows: list[dict]) -> dict:
    changed = [r for r in rows if r["changed"]]
    diffs = [r["gap_ablated"] - r["gap_actual"] for r in changed
             if r["gap_actual"] is not None and r["gap_ablated"] is not None]
    return {"n_days": len(rows), "n_fidelity": sum(r["fidelity"] for r in rows),
            "n_changed": len(changed), "n_changed_with_labels": len(diffs),
            "mean_gap_diff_changed": float(np.mean(diffs)) if diffs else None}


def main(argv: list[str] | None = None) -> int:
    from autoresearch.common import workspace as ws
    from autoresearch.scan.outcome import ledger_root as _ledger_root

    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--reports-root", default=None, help="已发布 scan 报告根(缺省 = 当前引擎)")
    ap.add_argument("--ledger-root", default=None, help="结果账本根(缺省 = 当前引擎)")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    root = Path(args.reports_root) if args.reports_root else ws.reports_root() / "scan"
    ledger = Path(args.ledger_root) if args.ledger_root else _ledger_root()
    rows = readout(root, ledger_root=ledger)
    ics = [face_ic(decision, ledger_root=ledger) for _run, decision in _published_decisions(root)
           if replay(decision) is not None]
    face_means = {face: (float(np.mean(vals)) if (vals := [ic[face] for ic in ics
                                                           if ic[face] is not None]) else None)
                  for face in FACES_FULL}
    summary = {**summarize(rows), "face_ic_mean": face_means}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(out / "e6_ablation.csv", index=False)
    (out / "e6_ablation.md").write_text(
        "# E6 召回面消融(离线重放,只记账)\n\n"
        + (pd.DataFrame(rows).to_markdown(index=False) if rows else "(没有 active finalists 池的决策)")
        + "\n\n## 汇总\n\n" + json.dumps(summary, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
