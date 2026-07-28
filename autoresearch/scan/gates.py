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

# GATE2 exempt 记账契约(终审 C-1;原 L3.5 闸 exempt 契约收编,闸删记账留):这些 lane 不占
# finalist tier 预算名额。pinned 由 l3_select._inject_pinned_finalists 写在 GATE2 之前
# (GATE2 时已在场)——这是 C-1 的真实触发路径;watchlist_trigger 在 workflow 里于 L4 phase
# 才追加、晚于 GATE2,仍一并识别是防呆:万一未来追加顺序提前,契约不因此漂移。
#
# ⚠️ carryover 已于 2026-07-16 退役(用户裁定,依据见 pr_20260716_006):它自称是 token 经济件,
# 但账本读数 = 复用 7 次 vs 重研 11 次 = **净多烧 11 个 Opus**、18 只次 0 买单——保席从不省
# token(票本不在名单上=0 卡),只会 0 成本或 +1 Opus。故本集合不再含 "carryover"。
# 已知限制:2026-07-06~07-16 的历史 scan 目录里存有 lane=carryover 行,对这些老日期重跑
# gate2 会把它们计入预算(可能虚假失败)。生产不重跑历史 gate2,故接受;若将来要跑历史对拍,
# 用 --budget 放宽或按 lane 过滤,**勿为此把死 lane 加回契约**。
_EXEMPT_LANES = {"pinned", "watchlist_trigger"}


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
    # 名额」,exempt lane(pinned 保送 / watchlist_trigger 观察单直通车,见 `_EXEMPT_LANES`)
    # 即便已出现在 finalists.csv 里也不计入预算比较。铁律「pinned 强留不占名额」原实现只在
    # "注入发生于 v3 cap 之后"生效、GATE2 记账却按全行数走,两者矛盾——满员日(cap=10 是好日子
    # 的常态输出)+1 只 pinned 即确定性触发 GATE2 硬失败(见终审报告实证)。
    # exempt 行仍全额出现在 codes/n 里(它们确实会全部送 L4,只是不占『门』的坑)。
    # 时序核查:pinned 由 `l3_select finalists`(GATE2 之前)注入,GATE2 时**已经在场**——这
    # 才是 C-1 的真实触发路径;watchlist_trigger 在 L4 phase 才追加、晚于本门,一并排除是
    # 纵深防御:不依赖"当前时序恰好安全"这一脆弱前提。
    # (carryover 已于 2026-07-16 退役,不再是 exempt lane——理由见 `_EXEMPT_LANES` 上方注释。)
    n_counted = (len(df[~df["lane"].astype(str).isin(_EXEMPT_LANES)])
                 if "lane" in df.columns else len(df))
    if n_counted > budget:
        return {"ok": False, "gate": "gate2",
                "reason": f"finalists {n_counted} > budget {budget}(exempt 不计入)"}

    # P4a(task-7-brief.md):GATE2 成功分支为每只 finalist 回显 name/sector 元数据,
    # workflow(Task 9)据此在 GATE2 后立即派发 l4-intel,不必让每个 intel subagent 自己
    # 回查 finalists.csv。失败分支(上面的 return)不动,meta 只在成功分支纯新增。
    # NaN 防守:name/sector 列可能整列缺失(旧格式 finalists.csv,r.get 返回 None)或
    # 单元格是 float('nan')(pandas 对空字符串常见的读入结果,`v != v` 是 NaN 自反假的经典判据)
    # ——两种情况都归一成空串,不让 NaN 泄漏进 JSON(json.dumps(float('nan')) 输出非法 JSON)。
    def _s(r, col):
        v = r.get(col)
        return "" if v is None or (isinstance(v, float) and v != v) else str(v)

    meta = {str(r["code"]): {"name": _s(r, "name"), "sector": _s(r, "sector")}
            for _, r in df.iterrows()}
    return {"ok": True, "gate": "gate2", "reason": "ok", "finalists": codes,
            "n": int(len(df)), "meta": meta}


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


def record_gate_stage_result(scan_dir: Path, result: dict, *, budget: int | None = None):
    """把旧 gate 返回适配为 StageResult；不改变 gate dict 或退出码。"""
    from autoresearch.scan.stage_result import safe_record_stage_result

    gate = str(result.get("gate", ""))
    artifact_map = {
        "gate1": ("l2", "L2_gbdt_top200.csv"),
        "gate2": ("finalists", "finalists.csv"),
        "gate4": ("gate_fires", "gate_fires.csv"),
    }
    artifact_name, filename = artifact_map[gate]
    artifacts = [artifact_name] if (Path(scan_dir) / filename).exists() else []
    if gate == "gate1":
        metrics = {
            key: result[key]
            for key in ("sentinel_level", "l4_budget", "l2_n")
            if key in result
        }
    elif gate == "gate2":
        metrics = {"budget": int(budget if budget is not None else 30)}
        if "n" in result:
            metrics["n"] = int(result["n"])
        if "finalists" in result:
            metrics["finalists"] = list(result["finalists"])
        if "meta" in result:
            metrics["meta"] = dict(result["meta"])
    else:
        metrics = {"n_checks": int(result["n_checks"])} if "n_checks" in result else {}
    return safe_record_stage_result(
        scan_dir,
        stage=gate,
        status="SUCCEEDED" if result.get("ok") else "FAILED",
        artifacts=artifacts,
        metrics=metrics,
        warnings=[],
        error=None if result.get("ok") else str(result.get("reason", "gate failed")),
    )


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
    record_gate_stage_result(
        scan_dir,
        res,
        budget=a.budget if a.gate == "gate2" else None,
    )
    print(json.dumps(res, ensure_ascii=False))
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
