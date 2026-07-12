"""scan-market workflow 校验门(确定性,零 LLM)。workflow 经 Bash-agent 调,读 JSON 分支。

GATE1 = prelude 后数据体检(L2 非空 + 代码 6 位)+ 返回 sentinel/budget;
GATE2 = finalists 定稿后(代码 6 位 + count≤budget;exempt lane 不占名额)+ 返回名单——
L3.5 可插拔闸已**完全移除**(2026-07-12 用户裁定"直接 L3 输出",design
2026-07-12-funnel-replay-l35-removal-design.md §1;L3 finalist tier 即 L4 入选集);
GATE4 = assemble 后 self_review 硬门(gate_fires.csv 无 severity=fail)。
GATE3(slim>8KB(surface) / 无 .SH)由 l4_card harvest-slim 自身承担。
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

import pandas as pd

_CODE_RE = re.compile(r"^\d{6}$")

# GATE2 exempt 记账契约(终审 C-1;原 L3.5 闸 exempt 契约收编,闸删记账留):保送(pinned)/
# 菜单滞回(carryover)/观察单直通车(watchlist_trigger)三个 lane 不占 finalist tier 预算名额。
# pinned 由 l3_select._inject_pinned_finalists 写在 GATE2 之前(GATE2 时已在场);
# carryover/watchlist_trigger 目前在 workflow 里于 L4 phase 才追加,晚于 GATE2——此处仍
# 一并识别是防呆:万一未来追加顺序提前,契约不因此漂移。
_EXEMPT_LANES = {"pinned", "carryover", "watchlist_trigger"}


def _codes_ok(codes) -> bool:
    return len(codes) > 0 and all(bool(_CODE_RE.match(str(c))) for c in codes)


def gate1(scan_dir: Path) -> dict:
    scan_dir = Path(scan_dir)
    l2 = scan_dir / "L2_gbdt_top200.csv"
    if not l2.exists():
        return {"ok": False, "gate": "gate1", "reason": "L2_gbdt_top200.csv 缺失(universe 未跑?)"}
    df = pd.read_csv(l2, dtype={"code": str})
    if df.empty:
        return {"ok": False, "gate": "gate1", "reason": "L2 为空"}
    if not _codes_ok(df["code"].astype(str)):
        return {"ok": False, "gate": "gate1", "reason": "L2 代码非 6 位(前导零坑)"}
    from autoresearch.scan.menu import l4_budget, sentinel_advice

    level, _ = sentinel_advice(scan_dir)
    budget, _ = l4_budget(scan_dir)
    return {"ok": True, "gate": "gate1", "reason": "ok", "sentinel_level": level,
            "l4_budget": int(budget), "l2_n": int(len(df))}


def gate2(scan_dir: Path, budget: int = 30) -> dict:
    scan_dir = Path(scan_dir)
    fp = scan_dir / "finalists.csv"
    if not fp.exists():
        return {"ok": False, "gate": "gate2", "reason": "finalists.csv 缺失"}
    df = pd.read_csv(fp, dtype={"code": str, "ticker": str})
    if df.empty:
        return {"ok": False, "gate": "gate2", "reason": "finalists 空"}
    raw = df["code"].astype(str)          # dtype=str 读入原样保留(不 zfill)—— 与 gate1 同口径
    if not _codes_ok(raw):
        return {"ok": False, "gate": "gate2", "reason": "finalist 代码非 6 位(前导零坑)"}
    codes = df["code"].astype(str).tolist()
    # C-1 修复(final-review-l3-merge.md Critical-1):GATE2 的预算数的是「L3 finalist tier
    # 名额」,exempt lane(pinned 保送/carryover 菜单滞回/watchlist_trigger 观察单直通车,
    # `_EXEMPT_LANES`)即便已出现在 finalists.csv 里也不计入预算比较。铁律「pinned 强留不占
    # 名额」原实现只在"注入发生于 v3 cap 之后"生效、GATE2 记账却按全行数走,两者矛盾——满员日
    # (cap=10 是好日子的常态输出)+1 只 pinned 即确定性触发 GATE2 硬失败(见终审报告实证)。
    # exempt 行仍全额出现在 codes/n 里(它们确实会全部送 L4,只是不占『门』的坑)。
    # 时序核查(carryover/watchlist_trigger 是否也会在 GATE2 时出现在 finalists 里):workflow
    # scan-market.js 里 GATE2 先于 `l4_reuse --apply --carryover` 与 watchlist 直通车追加
    # (均在 L4 phase)执行——两者当前**晚于**本门,GATE2 见到的 finalists.csv 此刻不会含
    # 这两个 lane;但 pinned 由 `l3_select finalists`(GATE2 之前)注入,GATE2 时**已经在场**
    # ——这才是 C-1 的真实触发路径。此处仍一并排除 carryover/watchlist_trigger 是纵深防御:
    # 万一未来追加顺序提前,契约不因此漂移,不依赖"当前时序恰好安全"这一脆弱前提。
    n_counted = (len(df[~df["lane"].astype(str).isin(_EXEMPT_LANES)])
                 if "lane" in df.columns else len(df))
    if n_counted > budget:
        return {"ok": False, "gate": "gate2",
                "reason": f"finalists {n_counted} > budget {budget}(exempt 不计入)"}
    return {"ok": True, "gate": "gate2", "reason": "ok", "finalists": codes,
            "n": int(len(df))}


def gate4(scan_dir: Path) -> dict:
    scan_dir = Path(scan_dir)
    gf = scan_dir / "gate_fires.csv"
    if not gf.exists():
        return {"ok": False, "gate": "gate4", "reason": "gate_fires.csv 缺失(assemble 未跑?)"}
    with gf.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    fails = [r for r in rows if r.get("severity") == "fail"]
    if fails:
        detail = "; ".join(f"{r['check']}:{r['detail']}" for r in fails)
        return {"ok": False, "gate": "gate4", "reason": f"self_review fail×{len(fails)} — {detail}"}
    return {"ok": True, "gate": "gate4", "reason": "self_review 通过", "n_checks": len(rows)}


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="gates")
    ap.add_argument("gate", choices=["gate1", "gate2", "gate4"])
    ap.add_argument("date")
    ap.add_argument("--budget", type=int, default=30)
    ap.add_argument("--root", default=None)
    a = ap.parse_args(argv)
    base = Path(a.root) if a.root else Path("context/scan")
    scan_dir = base / a.date
    res = {"gate1": lambda: gate1(scan_dir),
           "gate2": lambda: gate2(scan_dir, budget=a.budget),
           "gate4": lambda: gate4(scan_dir)}[a.gate]()
    print(json.dumps(res, ensure_ascii=False))
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
