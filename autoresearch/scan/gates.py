"""scan-market workflow 校验门(确定性,零 LLM)。workflow 经 Bash-agent 调,读 JSON 分支。

GATE1 = prelude 后数据体检(L2 非空 + 代码 6 位)+ 返回 sentinel/budget;
GATE2 = finalists 定稿后(代码 6 位 + count≤budget)+ 返回名单——**L3.5 可插拔闸**(design
2026-07-11-recall-gate-pinned-config-design.md §3;plan 2026-07-11-l35-gate-backtest-plan.md
Task 3/4)也插在这里(finalists 已落、L4 派发前):见 `apply_l35_gate`;
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

# L3.5 闸 exempt 契约(design §3/§4.1):保送(pinned)/菜单滞回(carryover)/观察单直通车
# (watchlist_trigger)三个 lane 恒直通、不占闸的配额——"不占 6~10 坑"。三者若已出现在
# finalists.csv 的 `lane` 列(pinned 由 l3_select._inject_pinned_finalists 写在 GATE2 之前;
# carryover/watchlist_trigger 目前在 workflow 里于 L4 phase 才追加,晚于本闸——届时它们天然
# 不经过闸;此处仍一并识别是防呆:万一未来追加顺序提前,契约不因此漂移)。
_L35_EXEMPT_LANES = {"pinned", "carryover", "watchlist_trigger"}


def _codes_ok(codes) -> bool:
    return len(codes) > 0 and all(bool(_CODE_RE.match(str(c))) for c in codes)


def _l35_regime(scan_dir: Path) -> str:
    """当日 regime 标签:`meta.json.regime` 优先(universe.run 落盘,与 L1 权重选择/`menu.l4_budget`
    同一标签,可复现);缺失(文件缺 / regime_aware 关的默认态 = null)→ `'range'` 兜底,与
    `classify_regime` 自身"安全退化 range"的惯例一致(design 已侦察:gates.py 现无 regime 读法,
    此处新增,不强算 classify_regime——finalists.csv 无 above_ma60/pct_60d 等分类列)。
    """
    p = scan_dir / "meta.json"
    if p.exists():
        try:
            r = json.loads(p.read_text(encoding="utf-8")).get("regime")
            if r:
                return str(r)
        except Exception:  # noqa: BLE001
            pass
    return "range"


def _l35_exempt(df: pd.DataFrame) -> set[str]:
    """exempt 集 = `lane` 命中 `_L35_EXEMPT_LANES` 的 code(A2-T1 的 exempt 恒直通契约,
    与 A3-T4 pinned 对齐)。`lane` 列缺失(退化态)→ 空集,不报错。"""
    if "lane" not in df.columns:
        return set()
    return set(df.loc[df["lane"].astype(str).isin(_L35_EXEMPT_LANES), "code"].astype(str))


def _l35_gate_choice(user_config_path: str | Path | None = None) -> tuple[str, dict]:
    """scan_config.json 的 `l4_gate:{name,params}`(经 `user_config.load_user_config` 白名单);
    缺文件/缺键 → `('passthrough', {})` = **parity**(当前默认,回测已证 conviction_floor_quota
    更差,不切换默认——见 plan Task 5)。坏键/坏闸名不吞:`load_user_config`/`l35_gate.build` 该
    raise 就 raise(防拼写错静默失效,与既有配置层哲学一致)。
    """
    from autoresearch.scan.user_config import load_user_config

    cfg = load_user_config(user_config_path)   # path=None → load_user_config 自身的默认路径解析
    l4_gate_cfg = cfg.get("l4_gate") or {}
    name = l4_gate_cfg.get("name") or "passthrough"
    params = dict(l4_gate_cfg.get("params") or {})
    return name, params


def apply_l35_gate(scan_dir: Path, df: pd.DataFrame,
                   user_config_path: str | Path | None = None) -> tuple[pd.DataFrame, dict]:
    """L3.5 可插拔闸接线(finalists 已落、L4 派发前;plan Task 4)。`df` = GATE2 已校验过 6 位
    代码的 finalists 帧。

    gate 选择见 `_l35_gate_choice`(缺配置 = passthrough = parity)。**预算旗收编为输入**
    (design §3"现有预算旗逻辑收编为闸的输入参数,一个机制不留两套"):算好
    `menu.l4_budget(scan_dir)` 填入 `params['l4_budget']`(用户在 scan_config.json 显式给的
    `l4_budget` 优先,不覆盖——`setdefault`)。exempt 见 `_l35_exempt`;regime 见 `_l35_regime`。

    **passthrough(默认)时 `cut` 恒空 → 直接原样返回 `df`,不碰任何文件**——与改动前逐字节
    parity(连 `finalists.csv` 的 mtime 都不动)。非 passthrough 且真产生 cut → `df` 收窄到
    `picked`(只删行、不改列/不改序)重写 `finalists.csv`(L4 派发/`dispatch_plan` 直接读此
    文件,是闸生效的唯一机制)+ 落 `_l35_cut.csv`(`GateResult.cut` 的 code/rank/conviction/
    lane/reason 列,T3 影子账本消费此文件)。

    返回 `(narrowed_df, info)`,`info = {"name": 闸名, "cut_n": 砍掉几只}` 供 GATE2 回显。
    """
    from autoresearch.scan.l35_gate import build
    from autoresearch.scan.menu import l4_budget as _menu_l4_budget

    name, params = _l35_gate_choice(user_config_path)
    budget_n, _ = _menu_l4_budget(scan_dir)
    params.setdefault("l4_budget", int(budget_n))

    gate_fn = build(name)
    regime = _l35_regime(scan_dir)
    exempt = _l35_exempt(df)
    result = gate_fn(df, regime=regime, exempt=exempt, params=params)
    if not result.cut:                # passthrough(默认)/ 该闸当日恰好全放行 → 零文件改动
        return df, {"name": name, "cut_n": 0}

    picked = {str(c) for c in result.picked}
    narrowed = df[df["code"].astype(str).isin(picked)].reset_index(drop=True)
    narrowed.to_csv(scan_dir / "finalists.csv", index=False)     # 收窄名单落盘(L4 派发读此文件)
    pd.DataFrame(result.cut).to_csv(scan_dir / "_l35_cut.csv", index=False)  # 影子账本(T3 消费)
    return narrowed, {"name": name, "cut_n": len(result.cut)}


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


def gate2(scan_dir: Path, budget: int = 30, user_config_path: str | Path | None = None) -> dict:
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
    # L3.5 可插拔闸(finalists 已落、L4 派发前;design §3;plan Task 3/4)。passthrough(默认,
    # 缺 scan_config.json 时)= parity:df/finalists.csv 原样不动,l35 info 恒 {name:"passthrough",
    # cut_n:0}。非默认才会收窄 df + 落 _l35_cut.csv(见 apply_l35_gate)。
    df, l35 = apply_l35_gate(scan_dir, df, user_config_path=user_config_path)
    codes = df["code"].astype(str).tolist()
    if len(df) > budget:
        return {"ok": False, "gate": "gate2", "reason": f"finalists {len(df)} > budget {budget}",
                "l4_gate": l35["name"], "l35_cut_n": l35["cut_n"]}
    return {"ok": True, "gate": "gate2", "reason": "ok", "finalists": codes,
            "n": int(len(df)), "l4_gate": l35["name"], "l35_cut_n": l35["cut_n"]}


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
