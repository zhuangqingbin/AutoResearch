#!/usr/bin/env python3
"""L2 菜单「落刀偏斜」归因取证(Wave9 A-3;确定性,零 LLM,**不改采样生产行为**)。

2026-07-29 实测:菜单落刀面(pct_60d < −20)75% vs 全市场 43% —— 分层采样把菜单
配得比市场更"向下"。但**立案时写的诊断动工一查 4/4 全错**是本 repo 的一等坑,所以
本模块只做一件事:把那个 75% **分解**成「主排序入选」与「风格桶 floor 救回」两桶,
让下一步的实验立案有真读数可依。

三必问(动手前自答,写在报表页首):
  1. 量错对象?—— 菜单落刀高会不会只是反映 risk_off 期 L1 召回池本身更落刀
     (故报表同时出 L1 池落刀率,不只对全市场比)。
  2. 时序不对?—— 菜单是当日切面,与同日 L1 对照,不跨日比。
  3. 已经有了?—— menu_health 已有菜单落刀读数;本模块只加**归因分解**,不重造。

现场核实记录(2026-07-30,真数据 `context/scan/2026-07-29/L2_gbdt_top200.csv`):
  - `l2_lane_reserved` 列真实形态 = 原生 pandas **bool dtype**(CSV 落盘字面量即 "True"/"False",
    `pd.read_csv` 自动推断为 bool 列)。`.astype(str).str.lower().isin(["true","1","yes"])` 桥接
    对该形态成立(`.astype(str)` 产出 "True"/"False" → `.str.lower()` → "true"/"false" 命中
    第一项;96/96 与原生布尔比较逐值一致)。部分早期 run(如 2026-06-22)整列缺失
    ——`knife_rates` 的缺列分支(全部计入 main、floor=None)覆盖这一真实场景,非纯假设测试。
  - 可用扫描日:`context/scan/<date>/` 下必须**同时**存在 `L1_recall_top1000.csv` 与
    `L2_gbdt_top200.csv` 才算可用(仅凭 L2 存在会在 `market` 列上读到 None,把「market 列缺失」
    误判成「当日 L1 池落刀率为 0/低」——即三必问 #1 的「量错对象」翻版)。CLI 的目录过滤器
    据此要求两文件都存在。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

_KNIFE = -20.0


def _rate(df: pd.DataFrame, thresh: float) -> float | None:
    if df is None or not len(df) or "pct_60d" not in df.columns:
        return None
    s = pd.to_numeric(df["pct_60d"], errors="coerce").dropna()
    return None if not len(s) else float((s < thresh).mean())


def knife_rates(l1_df: pd.DataFrame, l2_df: pd.DataFrame, *,
                thresh: float = _KNIFE) -> dict:
    """落刀率四联:全市场(=L1 池)/ 菜单 / 菜单-主排序 / 菜单-floor 救回。"""
    out = {"market": _rate(l1_df, thresh), "menu": _rate(l2_df, thresh),
           "main": None, "floor": None, "n_main": 0, "n_floor": 0}
    if l2_df is None or not len(l2_df):
        return out
    if "l2_lane_reserved" not in l2_df.columns:
        out["n_main"] = len(l2_df)
        out["main"] = out["menu"]
        return out
    flag = l2_df["l2_lane_reserved"].astype(str).str.lower().isin(["true", "1", "yes"])
    main, floor = l2_df[~flag], l2_df[flag]
    out.update(main=_rate(main, thresh), floor=_rate(floor, thresh),
               n_main=int(len(main)), n_floor=int(len(floor)))
    return out


def audit(dates: list[str], root: Path | str = "context/scan") -> pd.DataFrame:
    """逐日读 L1/L2 算落刀率四联。读失败的日子跳过**但必须记账,不许静默**(本模块自己就是
    为了消灭「静默偏差」而写的,不能自己留一条静默丢日子的口子):跳过原因存进返回帧的
    `.attrs["skipped"]`(`[{"date","reason"}, ...]`),请求的总天数存 `.attrs["requested"]`,
    供调用方(CLI/测试)核对「请求 vs 实际」是否有短缺。"""
    rows: list[dict] = []
    skipped: list[dict] = []
    for d in dates:
        sd = Path(root) / d
        try:
            l1 = pd.read_csv(sd / "L1_recall_top1000.csv", dtype={"code": str})
            l2 = pd.read_csv(sd / "L2_gbdt_top200.csv", dtype={"code": str})
        except Exception as exc:  # noqa: BLE001 — 读失败跳过,但记账进 skipped,见上
            skipped.append({"date": d, "reason": f"{type(exc).__name__}: {exc}"})
            continue
        rows.append({"date": d, **knife_rates(l1, l2)})
    out = pd.DataFrame(rows)
    out.attrs["requested"] = len(dates)
    out.attrs["skipped"] = skipped
    return out


def _skip_line(df: pd.DataFrame) -> str:
    skipped = df.attrs.get("skipped") or []
    if not skipped:
        return ""
    detail = "; ".join(f"{s['date']}({s['reason']})" for s in skipped)
    req = df.attrs.get("requested")
    prefix = f"请求 {req} 天," if req is not None else ""
    return (f"\n\n_{prefix}跳过 {len(skipped)} 个读取失败的扫描日(不计入上表,**非静默**,"
            f"原因逐条列出):{detail}_")


def render(df: pd.DataFrame) -> str:
    if not len(df):
        return "# L2 落刀归因\n\n_无可用扫描日。_" + _skip_line(df) + "\n"
    def _f(v):
        return "—" if v is None or pd.isna(v) else f"{float(v):.0%}"
    lines = ["# L2 菜单落刀归因(Wave9 A-3 取证 · 不改生产采样)", "",
             "> 三必问见模块 docstring。`market` = **L1 召回池**口径(非全 A),",
             "> 以避免拿池外票当对照(量错对象)。", "",
             "| 日期 | L1池 | 菜单 | 主排序 | floor救回 | n_main | n_floor |",
             "|---|---:|---:|---:|---:|---:|---:|"]
    for r in df.to_dict("records"):
        lines.append(f"| {r['date']} | {_f(r['market'])} | {_f(r['menu'])} | "
                     f"{_f(r['main'])} | {_f(r['floor'])} | {r['n_main']} | {r['n_floor']} |")
    med = {c: df[c].dropna().median() if df[c].notna().any() else None
           for c in ("market", "menu", "main", "floor")}
    lines += ["", f"**中位**:L1池 {_f(med['market'])} · 菜单 {_f(med['menu'])} · "
                  f"主排序 {_f(med['main'])} · floor {_f(med['floor'])}", "",
              "_读法:若 floor 桶落刀率显著高于主排序,偏斜来自风格桶定义;若两桶接近,",
              "则菜单落刀只是 L1 池本身的映射,**不构成对分层采样的指控**。_"]
    return "\n".join(lines) + _skip_line(df) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="L2 菜单落刀归因取证(只出报表,不改采样)")
    ap.add_argument("--days", type=int, default=20, help="回看最近 N 个有产物的扫描日")
    ap.add_argument("--root", default="context/scan")
    ap.add_argument("--out", default="reports/learning/l2_knife_audit.md")
    args = ap.parse_args(argv)

    root = Path(args.root)
    # 两个文件都存在才算可用日——仅凭 L2 存在会在 market 列读到 None(量错对象:
    # 见 2026-06-20 / 2026-07-25,两日 L1/L2 恒同生共死,但不可假设该相关性永远成立)。
    dates = sorted([p.name for p in root.iterdir()
                    if p.is_dir() and (p / "L2_gbdt_top200.csv").exists()
                    and (p / "L1_recall_top1000.csv").exists()])[-args.days:]
    df = audit(dates, root)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(df), encoding="utf-8")
    df.to_csv(out.with_suffix(".csv"), index=False)
    skipped = df.attrs.get("skipped", [])
    print(json.dumps({"ok": True, "requested": len(dates), "days": len(df),
                      "skipped": skipped, "out": str(out)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
