"""L4 卡数的唯一算法(2026-09-26 用户需求:一个键决定最终进 L4 卡的票数,且真实生效)。

四处曾各管一段:menu.l4_budget(五面旗)/ scan-market.js 写死的 min(10, budget)/
l3.finalist_max / composite_seat.m(不占名额)。现在只有这里算,GATE1 回显,消费方只读。
"""
from __future__ import annotations

DEFAULT_MAX_CARDS = 13      # = 退役前 finalist_max 10 + composite m 3(逐字 parity)


def effective_caps(cfg: dict | None, l4_budget: int) -> dict:
    """返回 {max_cards, budget_flags, seat_m, finalist_cap, l3cap}。

    - `max_cards`:非 📌 卡上限(含 composite 席位);📌 持仓恒出卡、不占额。
    - `finalist_cap` = max(1, max_cards − seat_m):留给 L3 finalist tier 的名额。
    - `l3cap`:传给 l3-rank / `write_finalists` 的 finalist tier 上限;`budget_flags=true` 时再与
      `menu.l4_budget`(五面旗只降不升)取小,`false` 时忽略旗。永不为 0。
    """
    from autoresearch.scan.l3.merge import composite_seat_cfg

    l4 = (cfg or {}).get("l4") or {}
    max_cards = int(l4.get("max_cards", DEFAULT_MAX_CARDS))
    budget_flags = bool(l4.get("budget_flags", True))
    seat_on, seat_m = composite_seat_cfg(cfg)
    seat_m = int(seat_m) if seat_on else 0
    finalist_cap = max(1, max_cards - seat_m)
    l3cap = min(finalist_cap, int(l4_budget)) if budget_flags else finalist_cap
    return {"max_cards": max_cards, "budget_flags": budget_flags, "seat_m": seat_m,
            "finalist_cap": finalist_cap, "l3cap": max(1, int(l3cap))}


def _gate1_metrics(staging) -> dict:
    """源 staging 冻结的 GATE1 STAGE_RESULT.metrics;缺/坏 → {}。"""
    from autoresearch.scan.stage_result import load_stage_result, stage_result_path

    try:
        return dict(load_stage_result(stage_result_path(staging, "gate1")).metrics or {})
    except Exception:  # noqa: BLE001 — 回放的补充输入,缺了就走下一级兜底
        return {}


def _day_pinned(staging) -> list[dict]:
    """回放用的当日 📌:源 finalists.csv 的 lane=pinned 行(码 + 当日 pinned_note);缺 → GATE1 冻结的
    run_mode.pinned_codes。不读今天的 pinned.jsonc(那是另一天的持仓)。"""
    import csv
    from pathlib import Path

    fin = Path(staging) / "finalists.csv"
    if fin.is_file():
        with fin.open(encoding="utf-8") as fh:
            return [{"code": str(r["code"]).zfill(6), "note": r.get("pinned_note") or ""}
                    for r in csv.DictReader(fh) if (r.get("lane") or "") == "pinned"]
    run_mode = _gate1_metrics(staging).get("run_mode") or {}
    return [{"code": str(c).zfill(6), "note": ""} for c in (run_mode.get("pinned_codes") or [])]


def _day_l4_budget(staging) -> int:
    budget = _gate1_metrics(staging).get("l4_budget")
    return budget if isinstance(budget, int) and budget > 0 else 30


def main(argv: list[str] | None = None) -> int:
    """`replay <staging> --max-cards N [--budget B] --out <dir>`:把一场真 staging 拷到 scratch,按给定
    max_cards 重跑 write_finalists,打印一行 JSON。只读源目录(验「改小立刻生效 / 默认逐字 parity」)。"""
    import argparse
    import json
    import shutil
    from pathlib import Path
    from unittest import mock

    from autoresearch.scan.l3.merge import write_finalists
    from autoresearch.scan.user_config import load_user_config

    ap = argparse.ArgumentParser(prog="python -m autoresearch.scan.l4.card_count")
    sub = ap.add_subparsers(dest="cmd", required=True)
    rp = sub.add_parser("replay", help="离线回放 max_cards(只写 --out)")
    rp.add_argument("staging")
    rp.add_argument("--max-cards", type=int, required=True)
    rp.add_argument("--budget", type=int, default=None, help="旗后 l4_budget;缺省读源 GATE1 冻结值,再缺省 30")
    rp.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    src, out = Path(a.staging).resolve(), Path(a.out).resolve()
    if a.max_cards < 1:
        ap.error("--max-cards 须为正整数")
    if out == src or src in out.parents or out in src.parents:
        ap.error(f"--out 不得与源 staging 重叠(回放只写 scratch):{out}")
    dst = out / src.name
    if dst.exists():
        ap.error(f"回放目标已存在(不覆盖):{dst}")
    shutil.copytree(src, dst)
    pin_path = out / f"_replay_pinned_{src.name}.json"
    pin_path.write_text(json.dumps([{**p, "expires": "2099-12-31"} for p in _day_pinned(src)],
                                   ensure_ascii=False), encoding="utf-8")
    base = load_user_config()
    cfg = {**base, "l4": {**(base.get("l4") or {}), "max_cards": int(a.max_cards)}}
    budget = a.budget if a.budget is not None else _day_l4_budget(src)
    caps = effective_caps(cfg, budget)
    with mock.patch("autoresearch.scan.user_config.load_user_config", lambda path=None: cfg):
        res = write_finalists(src.name, budget=caps["l3cap"], root=out, pinned_path=pin_path)
    import pandas as pd

    fin = pd.read_csv(dst / "finalists.csv", dtype={"code": str})
    pinned_n = int((fin["lane"].fillna("") == "pinned").sum()) if "lane" in fin.columns else 0
    print(json.dumps({"out": str(dst), "max_cards": caps["max_cards"], "l3cap": caps["l3cap"],
                      "l4_budget": budget, "finalists_non_pinned": int(len(fin)) - pinned_n,
                      "finalists_pinned": pinned_n, "max_cards_cut_n": res["max_cards_cut_n"]},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
