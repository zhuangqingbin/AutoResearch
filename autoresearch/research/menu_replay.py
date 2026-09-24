#!/usr/bin/env python3
"""L1′/L2′ 离线重算(2026-09-24 可买性对齐 §3.1;只读 staging,产物只落 `--out`)。

用已落盘的 `L1_scored_full.csv`(L0 全帧,含 `score_<group>` 列)+ `L1_channels.csv`(各路留底
名单)+ 生产同一段数学 `scoring.combine_group_scores` 重算 composite,再按 `select_l2` 重放菜单,
量 A1–A7。不改任何生产产物;不走网络。

批 1:`recompute_composite` / `replay_l1` / `replay_l2` / `metrics`(A1–A5,A6/A7 缺列先报
`None`)+ CLI;`replay_l2` 当时只在自己签名里占位收 `knife_cap_share`/`sector_seats`,不透传
给 `select_l2`(它还没有这两个形参)。

批 2(Task 16):`select_l2`/`stratified_l2` 已有 `knife_cap_share`(Task 11)与 `sector_seat`
presence-gated 强留(行业席位波,`autoresearch.scan.sector_seats` + `universe._inject_
sector_seats_l1`)——`replay_l2` 现在真透传两者:`knife_cap_share` 直接转给 `select_l2`;
`sector_seats` 先经 `_inject_sector_seats_l1` 把座位打进 `l1p` 再喂 `select_l2`(它自己认
`sector_seat` 列,按 pinned 同一套"先抽出、选完拼回末尾"语义处理)。`run_one` 新增
`sector_seats_cfg` 形参,用**重算过的新 composite**(不是落盘旧值,也不读任何已存的
`_sector_seats.json`)跑 `pick_sector_seats` 现选当日座位;CLI `--sector-seats` 读
`l2.sector_seats` 配置块(缺块 → `pick_sector_seats` 自带默认 per_sector=2/max_sectors=3)。
A6/A7 由此不再恒 `None`/`0`。

批 3 收尾(Task 23,2026-09-25 addendum §1,P39):A7(顶替次数)只回答"帽会不会顶替",答
不了"帽离咬下去还有多远"——`run_one` 现在**无论 `--knife-cap` 是否传**都额外跑一份帽关掉
的重放,`merit_core_knife_share` 只读其中 `selection_reason=="merit"` 的行,算出的份额与
`L0_knife` 的差记进 `A7b_margin`(`A7b_merit_core_knife_uncapped` 是分子本身,供人核)。
不读 `A4_l2_knife_new`(帽生效后的占比)做减法——那个数在帽真正咬下去时被压低,余量会在
最该报警的时刻显得最健康,见 `metrics` 与 `merit_core_knife_share` 各自的 docstring。
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
              enabled_channels=None, sector_seats: list[dict] | None = None,
              full: pd.DataFrame | None = None) -> pd.DataFrame:
    """L2′ = `select_l2` 重放(生产同一个采样器,零第二套实现)。

    `knife_cap_share` 直接转给 `select_l2`(→ `stratified_l2`;`None` = 不设帽 = parity)。

    `sector_seats`(非空)→ 先用 `_inject_sector_seats_l1` 把座位打进 `l1p`:座位码已在
    `l1p` 里 → 原地打 `sector_seat=True`;不在 → 去 `full`(缺省退回 `l1p` 自己——这条默认
    分支下,不在 `l1p` 里的座位码在"scored"里也找不到,会被静默丢弃;调用方想让"不在 l1p
    里的座位"也生效就必须显式传 `full`)按码取那一行真实数据补进来。再喂 `select_l2`——它
    自己认 `sector_seat` 列,按 pinned 同一套"先抽出、不进竞争、选完无条件拼回末尾"语义
    处理(`selection_reason="sector_seat"`)。`sector_seats` 为 `None`/空 →
    `_inject_sector_seats_l1` 原样返回 `l1p`(presence-gated parity,`select_l2` 也不会见到
    `sector_seat` 列)。
    """
    if sector_seats:
        from autoresearch.scan.universe import _inject_sector_seats_l1
        l1p = _inject_sector_seats_l1(l1p, full if full is not None else l1p, sector_seats)
    l2, _engine = select_l2(l1p, l2_n, floors=floors, sector_cap_frac=sector_cap,
                            enabled_channels=enabled_channels, knife_cap_share=knife_cap_share)
    return l2


def _share(mask: pd.Series | None) -> float | None:
    return None if mask is None or not len(mask) else float(mask.fillna(False).mean())


def merit_core_knife_share(l2_uncapped: pd.DataFrame) -> float | None:
    """A7b(2026-09-25 addendum §1,P39)分子:merit 核(② sector-neutral composite 排序、过
    sector cap、**不受落刀帽**豁免——它本身就不该被帽保护,帽是给它设的)在一份**帽关掉**
    (`knife_cap_share=None`)的重放里天然的落刀占比。只读 `selection_detail`/`selection_
    reason=="merit"` 的行——不是整个 L2′(floor 桶语义是"这类风格就该有 floor 保护"、回填/
    松cap 桶是"凑数",混进来会把"merit 核天然干不干净"这个问题答成另一个问题;经验值差异
    真实存在,见本函数调用点旁的探针)。调用方(`run_one`)负责保证传入的是一份关帽重放,
    本函数不重放、不检查这个前提,只做过滤 + 算份额。缺 `selection_reason` 列或无 merit
    行(如 `floors` 配置到 merit_need=0)→ `None`(降级,不编 0)。
    """
    if "selection_reason" not in l2_uncapped.columns:
        return None
    merit = l2_uncapped[l2_uncapped["selection_reason"] == "merit"]
    return _share(falling_knife_mask(merit)) if len(merit) else None


def metrics(full: pd.DataFrame, l1p: pd.DataFrame, l2p: pd.DataFrame, *,
            l1_old: pd.DataFrame, l2_old: pd.DataFrame,
            merit_core_knife_uncapped: float | None = None) -> dict:
    """A1–A5(§3.1)。A6/A7 由批 2 的席位/落刀帽列现算(缺列 → None)。

    A1 是**池内**(l1p,L1′的候选池)spearman,与 §3.1 表的定义逐字一致——不是整个 L0 帧;
    窄化种群会让相关系数系统性偏离全帧读数(range restriction),这是预期行为,不是 bug。

    `merit_core_knife_uncapped`(2026-09-25 addendum §1,P39,A7b 分子)= `merit_core_knife_
    share` 读一份调用方(`run_one`)另外产出的**帽关掉**重放算出的 merit 核落刀占比;本函数
    不重放,只做减法。`A7b_margin = L0_knife − merit_core_knife_uncapped`——**不是**
    `L0_knife − A4_l2_knife_new`:后者是帽**生效之后**的 L2 占比,帽真咬下去时它恰好被压低,
    余量会在最该报警的时刻读得最健康,方向与这道门的用途正相反(spec §3.1 A7b 明令禁止这个
    偷懒实现)。两个输入任一缺失 → `None`,不编数。
    """
    comp = pd.to_numeric(l1p["composite"], errors="coerce")
    a1 = float(comp.corr(pd.to_numeric(l1p["pct_20d"], errors="coerce"), method="spearman")) \
        if "pct_20d" in l1p.columns else None
    top20 = l1p.sort_values("composite", ascending=False).head(20)
    l0_knife = _share(falling_knife_mask(full))
    return {
        "n_l1_new": int(len(l1p)), "n_l1_old": int(len(l1_old)),
        "A1_spearman_composite_pct20d": a1,
        "A2_top20_knife": _share(falling_knife_mask(top20)),
        "A3_l1_knife_new": _share(falling_knife_mask(l1p)),
        "A3_l1_knife_old": _share(falling_knife_mask(l1_old)),
        "A4_l2_knife_new": _share(falling_knife_mask(l2p)),
        "A4_l2_knife_old": _share(falling_knife_mask(l2_old)),
        "L0_knife": l0_knife,
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
        "A7b_merit_core_knife_uncapped": merit_core_knife_uncapped,
        "A7b_margin": (
            None if merit_core_knife_uncapped is None or l0_knife is None
            else round(l0_knife - merit_core_knife_uncapped, 4)
        ),
    }


def run_one(staging: Path, weights_doc: dict, *, floors: dict | None, knife_cap: bool,
            enabled_channels=None, sector_seats_cfg: dict | None = None) -> dict:
    data = load_staging(staging)
    full, l1_old, l2_old = data["full"], data["full"][data["full"]["recalled"].astype(bool)], data["l2"]
    comp_new = recompute_composite(full, weights_doc)
    l1p = replay_l1(full, data["channels"], comp_new)
    share = _share(falling_knife_mask(full)) if knife_cap else None
    seats, full_new = None, None
    if sector_seats_cfg is not None:
        # 座位必须用**这次重算出来的新 composite**现选(不是落盘旧 composite,也不读任何
        # 已存的 `_sector_seats.json`)——回放要问的是"这套新配置今天会选谁",不是"当天生产
        # 选了谁"。`full` 是全帧(L0 全量,不是被召回收窄过的 `l1p`),与生产 `universe.run`
        # 喂 `pick_sector_seats(scored, ...)` 同一层帧。
        from autoresearch.scan.sector_seats import pick_sector_seats
        full_new = full.copy()
        full_new["composite"] = comp_new.to_numpy()
        seats = pick_sector_seats(full_new, per_sector=int(sector_seats_cfg.get("per_sector", 2)),
                                  max_sectors=int(sector_seats_cfg.get("max_sectors", 3)),
                                  exclude=sector_seats_cfg.get("exclude"))
    l2p = replay_l2(l1p, floors=floors, knife_cap_share=share, enabled_channels=enabled_channels,
                    sector_seats=seats, full=full_new)
    # A7b(2026-09-25 addendum §1):merit 核落刀占比必须来自**帽关掉**的独立重放,不能读
    # 上面这份 l2p——`knife_cap=False` 时 l2p 本身已经不设帽(share=None),但 `knife_cap=
    # True` 时 l2p 是帽生效**之后**的结果,不能拿来回答"帽如果关掉会怎样"。用同一份
    # l1p/floors/seats/full_new,只把 knife_cap_share 换成 None 单独重放一次(确定性
    # pandas,零 LLM,记账时"每场真跑记"不因 --knife-cap 是否传而跳过)。
    l2p_uncapped = replay_l2(l1p, floors=floors, knife_cap_share=None, enabled_channels=enabled_channels,
                             sector_seats=seats, full=full_new)
    merit_core_knife_uncapped = merit_core_knife_share(l2p_uncapped)
    return {"staging": str(staging),
           **metrics(full, l1p, l2p, l1_old=l1_old, l2_old=l2_old,
                     merit_core_knife_uncapped=merit_core_knife_uncapped)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="L1′/L2′ 离线重算(只读 staging;产物只落 --out)")
    ap.add_argument("--staging", nargs="+", required=True, help="staging 目录(含 L1_scored_full/L1_channels/L2_gbdt_top200)")
    ap.add_argument("--profile", choices=["preference", "calibrated"], default="preference")
    ap.add_argument("--config", default=".claude/skills/scan-market/scan_config.jsonc")
    ap.add_argument("--knife-cap", action="store_true")
    ap.add_argument("--sector-seats", action="store_true", help="读 l2.sector_seats 配置块(缺块用 pick_sector_seats 自带默认)现选当日行业席位")
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
    sector_seats_cfg = (l2cfg.get("sector_seats") or {}) if args.sector_seats else None
    rows = [run_one(Path(s), weights_doc, floors=l2cfg.get("floors"), knife_cap=args.knife_cap,
                    enabled_channels=funnel.get("recall_channels"),
                    sector_seats_cfg=sector_seats_cfg) for s in args.staging]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(rows)
    frame.to_csv(out / "menu_replay.csv", index=False)
    (out / "menu_replay.md").write_text(
        f"# menu_replay · profile={args.profile} · knife_cap={args.knife_cap} · sector_seats={args.sector_seats}\n\n"
        + frame.to_markdown(index=False) + "\n", encoding="utf-8")
    (out / "weights_doc.json").write_text(json.dumps(weights_doc, ensure_ascii=False, indent=1), encoding="utf-8")
    print(frame.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
