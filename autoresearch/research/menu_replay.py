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

# ── B2 / Q1-b(2026-10-03):偏好做资格门、门内按低热度排(离线影子)──────────────────────
#: 热度四个代理:换手、RSI6、获利盘、当日涨幅。4.5 年日线代理里热度是隔夜收益头号负因子。
HEAT_COLUMNS = ("turnover", "rsi6", "winner_rate", "pct_1d")
#: 截面 IC 至少要这么多只可算的票(与 `scan.populations.MIN_IC_NAMES` 同口径)。
MIN_IC_NAMES = 8


def heat_score(frame: pd.DataFrame) -> pd.Series:
    """当日热度 = 四个代理在当日帧内百分位的均值(0 冷 → 1 热);四个都缺 → NaN。"""
    parts = [pd.to_numeric(frame[c], errors="coerce").rank(pct=True)
             for c in HEAT_COLUMNS if c in frame.columns]
    if not parts:
        return pd.Series(np.nan, index=frame.index)
    return pd.concat(parts, axis=1).mean(axis=1, skipna=True)


def lowheat_gate_score(composite: pd.Series, heat: pd.Series, *, gate_q: float) -> pd.Series:
    """偏好 composite 的当日分位 > `gate_q` 的票进门,门内按热度从冷到热排;门外整体排在门内之后。

    输出 0–100 的替身分,直接喂生产同一个 L1′/L2′ 重放(门内 50–100,门外 0–50)。热度缺失按最热处理
    (不让「没数据」变成「最冷」)。
    """
    pct = pd.to_numeric(composite, errors="coerce").rank(pct=True)
    eligible = pct > float(gate_q)
    inside = 50.0 + 50.0 * (1.0 - pd.to_numeric(heat, errors="coerce").fillna(1.0))
    outside = 50.0 * pct.fillna(0.0)
    return inside.where(eligible, outside).clip(lower=0, upper=100)


def outcome_ic(score: pd.Series, analysis_date: str, *, ledger_root: Path) -> float | None:
    """`score`(按 code 索引)对当日主尺 `gap_c1_o2` 的截面秩相关;标签读账本 universe 层。

    只用成熟 ∧ 买腿可执行 ∧ 有值的票;标签文件缺、可算票不足 `MIN_IC_NAMES` 或任一侧没有差异 → None。
    """
    path = Path(ledger_root) / "evaluations/outcome_labels.v2/universe" / f"{analysis_date}.parquet"
    if not path.is_file():
        return None
    labels = pd.read_parquet(path, columns=["code", "gap_c1_o2", "status_gap_c1_o2", "buyable_c1"])
    labels["code"] = labels["code"].astype(str).str.zfill(6)
    ok = (labels["status_gap_c1_o2"] == "MATURE") & labels["buyable_c1"].fillna(False).astype(bool)
    labels = labels[ok & labels["gap_c1_o2"].notna()].drop_duplicates("code").set_index("code")
    # 同一 code 多一行(tushare 盘后半截快照)→ 留第一行;静默去重会藏住数据问题,但这里只读排序信号。
    score = pd.to_numeric(score, errors="coerce")
    score = score[~score.index.duplicated(keep="first")]
    joined = pd.DataFrame({"x": score, "y": labels["gap_c1_o2"]}).dropna()
    if len(joined) < MIN_IC_NAMES or joined["x"].nunique() < 2 or joined["y"].nunique() < 2:
        return None
    return float(joined["x"].rank().corr(joined["y"].rank()))


def b2_gate(rows: list[dict]) -> dict:
    """B2 验收门(跨日合并):变体菜单的主尺 IC 均值 ≥ 0,且落刀占比不升、健康占比不降。

    没有任何一天有成熟标签 → `pass=None`(不判,不是不过)。
    """
    def mean(key):
        values = [r[key] for r in rows if r.get(key) is not None]
        return float(np.mean(values)) if values else None

    ic_variant, ic_current = mean("IC_main_variant"), mean("IC_main_current")
    knife_v, knife_c = mean("A4_l2_knife_variant"), mean("A4_l2_knife_new")
    healthy_v, healthy_c = mean("A5_l2_healthy_variant"), mean("A5_l2_healthy_new")
    if ic_variant is None:
        return {"pass": None, "reason": "no matured outcome labels"}
    checks = {
        "ic_non_negative": ic_variant >= 0,
        "knife_not_up": knife_v is None or knife_c is None or knife_v <= knife_c,
        "healthy_not_down": healthy_v is None or healthy_c is None or healthy_v >= healthy_c,
    }
    return {"pass": all(checks.values()), "checks": checks, "IC_main_variant": ic_variant,
            "IC_main_current": ic_current, "n_days": sum(r.get("IC_main_variant") is not None
                                                          for r in rows)}


def _analysis_date(staging: Path) -> str | None:
    """staging 目录对应的分析日:run_contract(session_v1 staging / 已发布 trace)或报告 manifest。"""
    for path in (staging / "run_contract.json", staging.parent / "run_contract.json",
                 staging.parents[1] / "manifest.json" if len(staging.parents) > 1 else None):
        if path is not None and path.is_file():
            try:
                value = json.loads(path.read_text(encoding="utf-8")).get("analysis_date")
            except (OSError, json.JSONDecodeError):
                continue
            if value:
                return str(value)
    return None


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
    # M6(2026-09-25 终审):Gate A6 是两句——「席位≥2」∧「席位全部非落刀」。下面只答第一句
    # (COUNT);第二句此前无字段可核,读者只能信"pick_sector_seats 已经排除落刀"这句构造性
    # 承诺。缺 sector_seat 列(未启用席位)或缺 pct_60d(答不出落刀与否)→ None,不编 0。
    seat_mask = l2p["sector_seat"].fillna(False).astype(bool) if "sector_seat" in l2p.columns else None
    seat_knife_n = (int(falling_knife_mask(l2p[seat_mask]).fillna(False).sum())
                    if seat_mask is not None and "pct_60d" in l2p.columns else None)
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
        "A6_sector_seats": int(seat_mask.sum()) if seat_mask is not None else None,
        # A6 第二分句「席位全部非落刀」的可核字段:席位里落刀的行数。0 = 核过、真干净;
        # >0 = gate 真的破了;None = 缺列答不出(与上面 A6_sector_seats 的 None 同款语义)。
        "A6_sector_seats_knife": seat_knife_n,
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
            enabled_channels=None, sector_seats_cfg: dict | None = None,
            variant: str | None = None, gate_q: float = 0.4,
            ledger_root: Path | None = None) -> dict:
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
    row = {"staging": str(staging),
           **metrics(full, l1p, l2p, l1_old=l1_old, l2_old=l2_old,
                     merit_core_knife_uncapped=merit_core_knife_uncapped)}
    if variant == "lowheat_gate":
        # 同一份帧、同一个采样器,只把排序分换成「偏好门 + 门内低热度」;菜单形状与主尺 IC 并列记。
        score = lowheat_gate_score(comp_new, heat_score(full), gate_q=gate_q)
        l1v = replay_l1(full, data["channels"], score)
        l2v = replay_l2(l1v, floors=floors, knife_cap_share=share, enabled_channels=enabled_channels,
                        sector_seats=seats, full=full_new)
        row["A4_l2_knife_variant"] = _share(falling_knife_mask(l2v))
        row["A5_l2_healthy_variant"] = _share(healthy_riser_mask(l2v))
        date = _analysis_date(Path(staging))
        row["analysis_date"] = date
        if ledger_root is not None and date:
            codes = full["code"].astype(str).str.zfill(6)
            row["IC_main_current"] = outcome_ic(pd.Series(comp_new.to_numpy(), index=codes), date,
                                                ledger_root=ledger_root)
            row["IC_main_variant"] = outcome_ic(pd.Series(score.to_numpy(), index=codes), date,
                                                ledger_root=ledger_root)
    return row


def resolve_sector_seats_cfg(sector_seats_flag: bool, l2cfg: dict) -> dict | None:
    """`--sector-seats` 只是"要不要去读 `l2.sector_seats` 这块配置"的开关(M7,2026-09-25
    终审),不能越过块自己的 `enabled` 字段——production(`universe.py` 的
    `bool(l2_sector_seats.get("enabled", False))`)才是行业席位到底开没开的真相源;replay
    必须读同一个字段、同一个默认值(缺省 False),否则 production 关掉时 replay 还在用,
    两边对「这个功能今天生效了吗」能给出不同答案,而这原本该是同一件事。"""
    if not sector_seats_flag:
        return None
    block = dict(l2cfg.get("sector_seats") or {})
    return block if block.get("enabled") else None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="L1′/L2′ 离线重算(只读 staging;产物只落 --out)")
    ap.add_argument("--staging", nargs="+", required=True, help="staging 目录(含 L1_scored_full/L1_channels/L2_gbdt_top200)")
    ap.add_argument("--profile", choices=["preference", "calibrated"], default="preference")
    ap.add_argument("--config", default=".claude/skills/scan-market/scan_config.jsonc")
    ap.add_argument("--knife-cap", action="store_true")
    ap.add_argument("--sector-seats", action="store_true", help="读 l2.sector_seats 配置块现选当日行业席位;块自己的 enabled 仍须为 true(与 production 同口径,M7)")
    ap.add_argument("--variant", choices=["lowheat_gate"], default=None,
                    help="B2 影子变体:偏好 composite 当资格门(分位 > --gate-q),门内按低热度排")
    ap.add_argument("--gate-q", type=float, default=0.4, help="偏好门:composite 当日分位须高于此")
    ap.add_argument("--ledger-root", default=None,
                    help="结果账本根(读 universe 层主尺标签算 IC;缺省 = 当前引擎 reports/scan/_ledger)")
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
    sector_seats_cfg = resolve_sector_seats_cfg(args.sector_seats, l2cfg)
    ledger_root = None
    if args.variant:
        from autoresearch.scan.outcome import ledger_root as _ledger_root
        ledger_root = Path(args.ledger_root) if args.ledger_root else _ledger_root()
    rows = [run_one(Path(s), weights_doc, floors=l2cfg.get("floors"), knife_cap=args.knife_cap,
                    enabled_channels=funnel.get("recall_channels"),
                    sector_seats_cfg=sector_seats_cfg, variant=args.variant, gate_q=args.gate_q,
                    ledger_root=ledger_root) for s in args.staging]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(rows)
    frame.to_csv(out / "menu_replay.csv", index=False)
    gate_text = ""
    if args.variant:
        gate = b2_gate(rows)
        (out / "b2_gate.json").write_text(json.dumps(gate, ensure_ascii=False, indent=1), encoding="utf-8")
        gate_text = (f"\n\n## B2 验收门(variant={args.variant} · gate_q={args.gate_q})\n\n"
                     + json.dumps(gate, ensure_ascii=False) + "\n")
    (out / "menu_replay.md").write_text(
        f"# menu_replay · profile={args.profile} · knife_cap={args.knife_cap} · sector_seats={args.sector_seats}\n\n"
        + frame.to_markdown(index=False) + gate_text + "\n", encoding="utf-8")
    (out / "weights_doc.json").write_text(json.dumps(weights_doc, ensure_ascii=False, indent=1), encoding="utf-8")
    print(frame.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
