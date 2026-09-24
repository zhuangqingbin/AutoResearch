#!/usr/bin/env python3
"""L1′/L2′ 离线重算(2026-09-24 可买性对齐 §3.1;只读 staging,产物只落 `--out`)。

用已落盘的 `L1_scored_full.csv`(L0 全帧,含 `score_<group>` 列)+ `L1_channels.csv`(各路留底
名单)+ 生产同一段数学 `scoring.combine_group_scores` 重算 composite,再按 `select_l2` 重放菜单,
量 A1–A7。不改任何生产产物;不走网络。

批 1(本任务)范围:`recompute_composite` / `replay_l1` / `replay_l2` / `metrics`(A1–A5,
A6/A7 缺列先报 `None`)+ CLI。`select_l2` 今天还没有 `knife_cap_share` 形参(Task 11 才加,
Task 16 才在 `replay_l2` 里接线透传)——`replay_l2` 现在只在**自己的**签名里占位收
`knife_cap_share`/`sector_seats`,绝不透传给 `select_l2`(传了就是 TypeError)。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from autoresearch.common.scoring import (
    _GROUPS,
    combine_group_scores,
    falling_knife_mask,
    healthy_riser_mask,
    preference_weights_doc,
)
from autoresearch.scan.recall.l2_stratify import select_l2

COMPOSITE_QUOTA = 400          # `recall/channels.py` 的 composite 通道 quota(留底名单里没有 quota,这里显式给)


def load_staging(staging: Path) -> dict:
    s = Path(staging)
    return {"full": pd.read_csv(s / "L1_scored_full.csv", dtype={"code": str}),
            "channels": pd.read_csv(s / "L1_channels.csv", dtype={"code": str}),
            "l2": pd.read_csv(s / "L2_gbdt_top200.csv", dtype={"code": str})}


def recompute_composite(full: pd.DataFrame, weights_doc: dict) -> pd.Series:
    groups = {g: (pd.to_numeric(full[f"score_{g}"], errors="coerce") / 100.0
                  if f"score_{g}" in full.columns else pd.Series(np.nan, index=full.index))
              for g in _GROUPS}
    return combine_group_scores(full, groups, weights_doc).clip(lower=0, upper=100).round(1)


def replay_l1(full: pd.DataFrame, channels_long: pd.DataFrame, composite_new: pd.Series,
              *, composite_quota: int = COMPOSITE_QUOTA) -> pd.DataFrame:
    """L1′ = 其余各路留底名单 ∪ 新 composite 前 quota。不做 quota_union 的 floor 裁剪(近似:
    L1 是并集不是排序,份额类指标对规模不敏感;并集大小写进 metrics 供人看)。"""
    f = full.copy()
    f["code"] = f["code"].astype(str).str.zfill(6)
    f["composite"] = composite_new.to_numpy()
    ch = channels_long.copy()
    ch["code"] = ch["code"].astype(str).str.zfill(6)
    others = ch[ch["channel"].astype(str) != "composite"]
    chan_of: dict[str, set[str]] = {}
    for code, name in zip(others["code"], others["channel"].astype(str), strict=True):
        chan_of.setdefault(code, set()).add(name)
    top = f.sort_values(["composite", "code"], ascending=[False, True]).head(int(composite_quota))
    for code in top["code"]:
        chan_of.setdefault(code, set()).add("composite")
    keep = f[f["code"].isin(chan_of)].copy()
    keep["recall_channels"] = keep["code"].map(lambda c: "|".join(sorted(chan_of[c])))
    keep["n_channels"] = keep["code"].map(lambda c: len(chan_of[c]))
    return keep.sort_values(["composite", "code"], ascending=[False, True]).reset_index(drop=True)


def replay_l2(l1p: pd.DataFrame, *, l2_n: int = 200, floors: dict | None = None,
              sector_cap: float = 0.20, knife_cap_share: float | None = None,
              enabled_channels=None, sector_seats=None) -> pd.DataFrame:
    """L2′ = `select_l2` 重放(生产同一个采样器,零第二套实现)。

    `knife_cap_share`/`sector_seats` 只占位、**不透传**:`select_l2` 现在没有这两个形参
    (`knife_cap_share` 是 Task 11 给 `stratified_l2`/`select_l2` 加的;`sector_seats` 的行业席位
    注入是 Task 16 的事)——传了就是 TypeError。Task 16 会在这里把它们接进 `select_l2(...)` 调用。
    """
    l2, _engine = select_l2(l1p, l2_n, floors=floors, sector_cap_frac=sector_cap,
                            enabled_channels=enabled_channels)
    return l2


def _share(mask: pd.Series | None) -> float | None:
    return None if mask is None or not len(mask) else float(mask.fillna(False).mean())


def metrics(full: pd.DataFrame, l1p: pd.DataFrame, l2p: pd.DataFrame, *,
            l1_old: pd.DataFrame, l2_old: pd.DataFrame) -> dict:
    """A1–A5(§3.1)。A6/A7 由批 2 的席位/落刀帽列现算(缺列 → None)。

    A1 是**池内**(l1p,L1′的候选池)spearman,与 §3.1 表的定义逐字一致——不是整个 L0 帧;
    窄化种群会让相关系数系统性偏离全帧读数(range restriction),这是预期行为,不是 bug。
    """
    comp = pd.to_numeric(l1p["composite"], errors="coerce")
    a1 = float(comp.corr(pd.to_numeric(l1p["pct_20d"], errors="coerce"), method="spearman")) \
        if "pct_20d" in l1p.columns else None
    top20 = l1p.sort_values("composite", ascending=False).head(20)
    return {
        "n_l1_new": int(len(l1p)), "n_l1_old": int(len(l1_old)),
        "A1_spearman_composite_pct20d": a1,
        "A2_top20_knife": _share(falling_knife_mask(top20)),
        "A3_l1_knife_new": _share(falling_knife_mask(l1p)),
        "A3_l1_knife_old": _share(falling_knife_mask(l1_old)),
        "A4_l2_knife_new": _share(falling_knife_mask(l2p)),
        "A4_l2_knife_old": _share(falling_knife_mask(l2_old)),
        "L0_knife": _share(falling_knife_mask(full)),
        "A5_l2_healthy_new": _share(healthy_riser_mask(l2p)),
        "L0_healthy": _share(healthy_riser_mask(full)),
        "A6_sector_seats": int(l2p["sector_seat"].fillna(False).astype(bool).sum()) if "sector_seat" in l2p.columns else None,
        # 2026-09-25 fix round 1:floor 桶(lane)的顶替行只在 `knife_cap_swap` 列可见——
        # `selection_detail` 对 lane 行恒写桶名,从不是 "knife_cap"(那字符串只出现在
        # merit/backfill)。优先读新列;旧 CSV(round 1 之前落盘,没有这一列)才退回旧的
        # `selection_detail` 计数(仍能出数,只是漏计 floor 桶——诚实但不崩)。
        "A7_knife_cap_swaps": (
            int(l2p["knife_cap_swap"].fillna(False).astype(bool).sum())
            if "knife_cap_swap" in l2p.columns
            else int((l2p["selection_detail"].astype(str) == "knife_cap").sum())
            if "selection_detail" in l2p.columns else None
        ),
    }


def run_one(staging: Path, weights_doc: dict, *, floors: dict | None, knife_cap: bool,
            enabled_channels=None) -> dict:
    data = load_staging(staging)
    full, l1_old, l2_old = data["full"], data["full"][data["full"]["recalled"].astype(bool)], data["l2"]
    comp_new = recompute_composite(full, weights_doc)
    l1p = replay_l1(full, data["channels"], comp_new)
    share = _share(falling_knife_mask(full)) if knife_cap else None
    l2p = replay_l2(l1p, floors=floors, knife_cap_share=share, enabled_channels=enabled_channels)
    return {"staging": str(staging), **metrics(full, l1p, l2p, l1_old=l1_old, l2_old=l2_old)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="L1′/L2′ 离线重算(只读 staging;产物只落 --out)")
    ap.add_argument("--staging", nargs="+", required=True, help="staging 目录(含 L1_scored_full/L1_channels/L2_gbdt_top200)")
    ap.add_argument("--profile", choices=["preference", "calibrated"], default="preference")
    ap.add_argument("--config", default=".claude/skills/scan-market/scan_config.jsonc")
    ap.add_argument("--knife-cap", action="store_true")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    from autoresearch.scan.user_config import load_user_config
    cfg = load_user_config(args.config) or {}
    funnel, l2cfg = cfg.get("funnel") or {}, cfg.get("l2") or {}
    if args.profile == "preference":
        weights_doc = preference_weights_doc(funnel.get("preference_weights") or {})
    else:
        from autoresearch.common.scoring import _load_weights
        weights_doc = _load_weights(regime="range")
    rows = [run_one(Path(s), weights_doc, floors=l2cfg.get("floors"), knife_cap=args.knife_cap,
                    enabled_channels=funnel.get("recall_channels")) for s in args.staging]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(rows)
    frame.to_csv(out / "menu_replay.csv", index=False)
    (out / "menu_replay.md").write_text(
        f"# menu_replay · profile={args.profile} · knife_cap={args.knife_cap}\n\n"
        + frame.to_markdown(index=False) + "\n", encoding="utf-8")
    (out / "weights_doc.json").write_text(json.dumps(weights_doc, ensure_ascii=False, indent=1), encoding="utf-8")
    print(frame.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
