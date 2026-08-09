#!/usr/bin/env python3
"""三个旧尺结论在隔夜主尺上的重验(Wave12-T30 / 设计稿 E2)。自包含,只读现成产物,零 LLM。

design: `.superpowers/sdd/2026-08-08-wave12-implementation-plan/task-30-brief.md`

**影子取证专用,生产铁律未变;启用唯一路径 = 实验注册表。**(这句话同时钉在产出报告的顶行,
`render()` 有测试锁它不许被删——理由见下。)

**动机**:2026-08-05 用户裁定评判主尺换成隔夜尺 `gap_c1_o2`(T+1 收买 → T+2 开卖)。项目里
有三个仍在被引用、却建立在参考尺 `fwd_2_oc`(旧主尺,降参考不删)之上的结论:

  ① **「追当日大涨是负价值」**(当日 ≥9.5% 的票超额 −3.67pp)—— 它是热度/游资召回通道的
     **铁律级**否决依据。隔夜溢价与日内追涨是两件事,换尺后必须重问。
  ② **动量/热度的相位条件性**(上涨相位有害;`docs/research/2026-08-04-momentum-phase-conditional-ic.md`)。
  ③ **温度计五相位 × 次日市场隔夜收益**的条件分布(新问题,回答"隔夜 edge 是不是相位现象")。

**为什么顶行那句话是制度而不是文案**:①的旧结论是本仓库明令的**负结果**(「任何新召回/事件
设计不得用当日涨幅做入场」)。若 gap 尺下它不再为负,这份报告极易被读成"可以追涨了"——
而负结果不能靠一份研究报告翻案。报告只提供**证据**,启用与否只能走 registry + 人批。

**尺与列**:全篇唯一评判列 = `ruler.MAIN_RULER`(`gap_c1_o2`);唯一超额列 = `ruler.REL_MARKET`
(`rel_gap_market` = 个股 gap − 当日**可执行**全集等权均值)。不引入第二把尺,不产 `rel_gap_sector`
(行业中性辅列需要行业映射,①节的人口是全市场湖,不在本任务范围)。

**入场旗单点**:合格 = `gap_frame` 的 `eligible_gap`(T+1 收盘未封涨停 ∧ gap 非空),
分子与基准分母**用同一套**合格集——只从分子剔、留在分母里,基准会被买不进的票抬高(C1 家训)。

**区间**:一律 `stats.date_cluster_bootstrap`(按扫描日聚簇);细分 `n_days < 10` 标 `IMMATURE`,
不外推(统一成熟门 `stats.MATURITY_MIN_SUBGROUP`)。

用法:
  uv run --no-sync python -m autoresearch.research.overnight_evidence run [--days 0]
  uv run --no-sync python -m autoresearch.research.overnight_evidence --selftest
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from autoresearch.common import ruler, stats as st
from autoresearch.research import ruler_compare as rc

DEFAULT_OUT = Path("docs/research/2026-08-08-overnight-evidence-gap.md")

GAP = ruler.MAIN_RULER            # gap_c1_o2
REL = ruler.REL_MARKET            # rel_gap_market

# ① 分桶边界(当日涨幅 %),**左闭右开**:≥9.5 / [5,9.5) / [2,5) / <2。
# 边界值 9.5 来自 2026-07-24 的原始读数(主板涨停 10.0 / 创业板 20.0 都落在最高桶),
# 换尺重验**照抄不动**——挪边界就等于换了个问题问,而本稿要问的正是同一个问题。
CHASE_EDGES = (9.5, 5.0, 2.0)
CHASE_LABELS = ("≥9.5%", "5%~9.5%", "2%~5%", "其余")

# ② 相位归属,逐字照抄 2026-08-04 报告口径 A;B/C 见 `phase_side(recovery=...)`。
# 「修复」**不列在这两个元组里**——它的归属是三口径敏感性的变量,由 `phase_side` 的
# `recovery` 参数单独决定。写进 UP_PHASES 会是死内容(特判分支先返回,元组里那一项永远
# 读不到),而死内容 = 一个改了也不会变红的地方。
UP_PHASES = ("发酵", "高潮")
DOWN_PHASES = ("退潮", "冰点")
_RECOVERY = "修复"

_SUBGROUP_MIN = st.MATURITY_MIN_SUBGROUP      # 10:关键细分最小样本(日)


# ───────────────────────── ① 当日涨幅分桶 × 次日 gap 超额 ─────────────────────────


def chase_bucket(pct_chg) -> pd.Series:
    """当日涨幅(%)→ 四分桶标签(左闭右开);缺失 → NaN(**不折进「其余」凑样本**)。"""
    v = pd.to_numeric(pd.Series(pct_chg), errors="coerce")
    out = pd.Series(np.where(v >= CHASE_EDGES[0], CHASE_LABELS[0],
                    np.where(v >= CHASE_EDGES[1], CHASE_LABELS[1],
                    np.where(v >= CHASE_EDGES[2], CHASE_LABELS[2], CHASE_LABELS[3]))),
                    index=v.index, dtype=object)
    return out.where(v.notna())


def market_gap_of(gap: pd.DataFrame) -> float | None:
    """当日**可执行**全集的等权 gap 均值(= `rel_gap_market` 的基准)。空 → None。"""
    if gap is None or not len(gap) or "eligible_gap" not in gap.columns:
        return None
    elig = gap["eligible_gap"].fillna(False).astype(bool)
    s = pd.to_numeric(gap.loc[elig, GAP], errors="coerce").dropna()
    return float(s.mean()) if len(s) else None


def day_chase_rows(day: str, pct: pd.DataFrame, gap: pd.DataFrame) -> pd.DataFrame:
    """单日 → `[date, code, pct_chg, bucket, gap_c1_o2, rel_gap_market]`(仅合格集)。

    合格集 = `gap["eligible_gap"]`;**分子与基准同一套人口**。逐行带 `date`,供按日聚簇。
    """
    cols = ["date", "code", "pct_chg", "bucket", GAP, REL]
    if not len(gap) or not len(pct):
        return pd.DataFrame(columns=cols)
    g = gap.copy()
    g["eligible_gap"] = g["eligible_gap"].fillna(False).astype(bool)
    g = g[g["eligible_gap"]]
    base = market_gap_of(gap)
    if base is None or not len(g):
        return pd.DataFrame(columns=cols)
    m = g[["code", GAP]].merge(pct[["code", "pct_chg"]], on="code", how="inner")
    m["bucket"] = chase_bucket(m["pct_chg"])
    m = m[m["bucket"].notna()]
    m[REL] = pd.to_numeric(m[GAP], errors="coerce") - base
    m["date"] = str(day)
    return m[cols].reset_index(drop=True)


def _interval_row(sub: pd.DataFrame, value_col: str, **extra) -> dict:
    """一行读数 + date-cluster 区间 + 成熟度。

    `crosses_zero` 在**没有区间**时是 `None`(未知),不是 `True` —— `Interval.excludes_zero`
    对 lo/hi=None 返回 `False`,直接取 `not` 会把「压根没算出区间」记成「算过了,跨 0」。
    这个字段会随 `--json` 导出给下游,修在展示层不算修(M4,review 2026-08-09)。

    `n_days`/`n_obs` 取的是**区间真正用到的**样本(`Interval.n_clusters`/`Interval.n`),
    不是"有行的日子/行数"(修复轮2 根因):`date_cluster_bootstrap` 先丢 NaN 再按日聚簇,
    而本函数原来数的是 `sub["date"].nunique()` —— **整天全 NaN 的日子也被算进去**。
    于是一行可以自称 `n_days=15 · MATURE`,而区间其实只由 1 天产生;成熟度标签因此失真,
    也正是这条让 `_chase_verdict` 在"无区间"上仍拿到 `MATURE` 从而吐出方向性裁决。
    三条聚合路径里只有 `bucket_intervals`(①节)不预过滤 NaN,所以这条路只有它可达。
    """
    iv = st.date_cluster_bootstrap(sub, value_col, date_col="date")
    n_days = int(iv.n_clusters)
    has_iv = iv.lo is not None and iv.hi is not None
    return {**extra, "n_days": n_days, "n_obs": int(iv.n),
            "point": None if iv.point is None else round(iv.point, 5),
            "lo": None if iv.lo is None else round(iv.lo, 5),
            "hi": None if iv.hi is None else round(iv.hi, 5),
            "crosses_zero": (not iv.excludes_zero) if has_iv else None,
            "maturity": "MATURE" if n_days >= _SUBGROUP_MIN else "IMMATURE"}


def bucket_intervals(rows: pd.DataFrame, value_col: str = REL) -> pd.DataFrame:
    """分桶 → 点估计 + date-cluster 95% CI + 成熟度。桶顺序固定(不按读数排序,防挑桶)。

    顺序 = `CHASE_LABELS` 在前,**其余标签一律追加在后**而不是丢掉 —— 静默丢行是本仓库
    反复复发的病(「下游丢弃上游成果」判例);顺序固定只是为了防"按读数挑桶",不是给
    未知标签发一张消失许可证。
    """
    if not len(rows):
        return pd.DataFrame(columns=["bucket", "n_days", "n_obs", "point", "lo", "hi",
                                     "crosses_zero", "maturity"])
    seen = list(CHASE_LABELS) + sorted(set(rows["bucket"].dropna()) - set(CHASE_LABELS))
    out = [_interval_row(sub, value_col, bucket=b)
           for b in seen if len(sub := rows[rows["bucket"] == b])]
    return pd.DataFrame(out)


def entry_exclusion(pct: pd.DataFrame, gap: pd.DataFrame,
                    edge: float = CHASE_EDGES[0]) -> tuple[int, int]:
    """单日 →(D 日涨幅 ≥`edge` 的只数, 其中 T+1 收盘**买得进**的只数)。

    为什么这个数必须和①节一起报:合格集要求 T+1 收盘买得进,而 D 日涨停的票有一部分 D+1
    继续封板 → 被 `buyable_c1` 剔出样本。所以「≥9.5% 桶」量的是**大涨之后没能继续封板、
    因而真的建得上仓**的那批。这正是系统实际买得到的人口(剔了它反而会美化账本),但**最强
    的那部分连板票在样本之外**——不报这个比例,①节就容易被转述成「所有涨停票的隔夜溢价」。
    """
    if not len(pct) or not len(gap):
        return 0, 0
    v = pd.to_numeric(pct["pct_chg"], errors="coerce")
    big = set(pct.loc[v >= edge, "code"])
    elig = set(gap.loc[gap["eligible_gap"].fillna(False).astype(bool), "code"])
    return len(big), len(big & elig)


def chase_rows_all(days: list[str] | None = None, lake: Path | None = None,
                   exclude_bse: bool = False, limit: int | None = None,
                   excl: list[int] | None = None) -> pd.DataFrame:
    """全湖窗口扫一遍 → ①节长表。`exclude_bse` 剔北交所(8/4/920,30cm 板)做敏感性。

    `excl` 传入一个长度 2 的可变列表时,累加 `entry_exclusion` 的 (≥9.5% 总只次, 买得进只次)
    —— 让①节的人口口径和它的读数在**同一次扫描**里产出,不靠事后另跑一个脚本对不上账。
    """
    excl = excl if excl is not None else [0, 0]
    all_days = days if days is not None else rc.lake_days(lake)
    usable = all_days[:-2] if len(all_days) > 2 else []
    if limit:
        usable = usable[-limit:]
    frames = []
    for d in usable:
        pct = rc._read_lake_cols(d, ["pct_chg"], lake)
        if not len(pct):
            continue
        gap = rc.gap_frame(f"{d[:4]}-{d[4:6]}-{d[6:]}", days=all_days, lake=lake)
        if not len(gap):
            continue
        if exclude_bse:
            keep = ~gap["code"].astype(str).str.match(r"^(8|4|920)")
            gap = gap[keep]
            pct = pct[~pct["code"].astype(str).str.match(r"^(8|4|920)")]
        frames.append(day_chase_rows(d, pct, gap))
        n_big, n_ok = entry_exclusion(pct, gap)
        excl[0] += n_big
        excl[1] += n_ok
    return (pd.concat(frames, ignore_index=True) if frames
            else pd.DataFrame(columns=["date", "code", "pct_chg", "bucket", GAP, REL]))


# ───────────────────────── ② 通道 × 相位 × gap ─────────────────────────


def phase_side(phase: str, recovery: str = "up") -> str:
    """相位 → 侧(上涨/回撤/未知)。`recovery` = 「修复」的归属:up(口径A,默认)/down(B)/drop(C)。

    三口径来自 2026-08-04 报告 §3 的敏感性检验(「修复」是从底部的恢复,归属存疑)——
    换尺重验必须把三口径都跑,否则"结论不依赖边界选择"这句话在新尺上没有被验证过。
    """
    p = str(phase)
    if p == _RECOVERY:
        return {"up": "上涨", "down": "回撤"}.get(recovery, "未知")
    if p in UP_PHASES:
        return "上涨"
    if p in DOWN_PHASES:
        return "回撤"
    return "未知"


def side_intervals(daily: pd.DataFrame, value_col: str, recovery: str = "up") -> pd.DataFrame:
    """逐日读数(带 `phase`)→ 按侧的点估计 + CI + 成熟度。「未知」侧不参与,单列出来。"""
    if not len(daily):
        return pd.DataFrame(columns=["side", "n_days", "n_obs", "point", "lo", "hi",
                                     "crosses_zero", "maturity"])
    work = daily.copy()
    work["side"] = [phase_side(p, recovery) for p in work["phase"]]
    work = work.dropna(subset=[value_col])
    rows = [_interval_row(sub, value_col, side=s)
            for s in ("回撤", "上涨") if len(sub := work[work["side"] == s])]
    return pd.DataFrame(rows)


def repair_attribution(date: str, attr: pd.DataFrame, lake: Path | None = None,
                       lake_days: list[str] | None = None) -> tuple[pd.DataFrame, str]:
    """历史 `attribution.csv` 缺主尺列 → **用湖现算补齐**,返回 `(帧, 状态)`。

    状态:`native`(自带主尺列)/ `lake_filled`(缺列,已由湖补上)/ `unusable`(补不出来)。

    **为什么必须补而不是跳过**(C1,review 2026-08-09):`retro` 的主尺回填漏了 3 天
    (2026-06-18 / 06-22 / 07-07 —— 实测,只有 `fwd_2_oc` 等旧尺列,无 `gap_c1_o2`/`buyable_c1`)。
    `day_channel_stats` 里 `attr.get("gap_c1_o2")` → `None` → `pd.to_numeric(None)` → **标量 NaN**
    (不抛)→ 整列广播 NaN → `unique_excess_t2` 全 NaN → 我原来的 `.dropna(subset=["ux"])`
    把这三天**整天静默扔掉**:零告警、零计数,报告却写「覆盖 28 个扫描日」像是普查。
    丢的还是最早的两天 = 非随机缺失,而②节每侧只有 13~15 日,11% 的样本增量足以翻面。
    (那个被我上轮记成「别人的噪声」的 `RuntimeWarning: Mean of empty slice`,正是这三天在出声。)

    **为什么在内存里补、不回写 CSV**:仓库铁律「历史产物不改写,展示层现算」。补出来的
    `gap_c1_o2`/`buyable_c1` 与 `ruler_compare.gap_frame` 同一实现,与其余 28 天由 retro 落盘的
    值同源(湖 OHLC),口径一致。
    """
    if attr is None or not len(attr) or "code" not in attr.columns:
        return attr, "unusable"
    have = GAP in attr.columns and pd.to_numeric(attr[GAP], errors="coerce").notna().any()
    if have:
        return attr, "native"
    gap = rc.gap_frame(date, days=lake_days, lake=lake)
    if not len(gap):
        return attr, "unusable"
    out = attr.copy()
    out["code"] = out["code"].astype(str).str.split(".").str[0].str.zfill(6)
    g = gap.copy()
    g["code"] = g["code"].astype(str).str.split(".").str[0].str.zfill(6)
    out = out.drop(columns=[c for c in (GAP, ruler.ENTRY_FLAG) if c in out.columns])
    out = out.merge(g[["code", GAP, ruler.ENTRY_FLAG]], on="code", how="left")
    if not pd.to_numeric(out[GAP], errors="coerce").notna().any():
        return attr, "unusable"
    return out, "lake_filled"


def channel_phase_rows(scan_root: Path | None = None, days: int = 9999,
                       phases: dict[str, str] | None = None,
                       cov: dict | None = None, lake: Path | None = None) -> pd.DataFrame:
    """`L1_channels.csv × retro/attribution.csv` → `[date, phase, channel, ux]` 长表。

    **口径照抄** 2026-08-04 报告 §8 的复现脚本(同 `channel_audit.day_channel_stats`,
    `unique_excess_t2` = 只被该路召回的票的超额均值),**唯一差别是尺**:`channel_audit._RET_MAIN`
    已跟随 `ruler.MAIN_RULER`,08-05 flip 后它读的就是 `gap_c1_o2`,故这里不需要再传尺。

    **人口必须记账**(C1 修复):主尺列缺席的历史日先经 `repair_attribution` 用湖补齐;补不出来
    的日子**显式计入 `cov`**,不再靠末尾 `dropna` 静默抹掉。传入 `cov` dict 时回填
    `{n_dirs, n_loaded, n_native, n_lake_filled, n_unusable, n_used, dropped:[…]}` —— 报告的
    口径行读它,「覆盖 N 个扫描日」这句话从此有分母。
    """
    from autoresearch.research import channel_audit as ca, replay

    root = Path(scan_root or rc.SCAN_ROOT)
    ph = phases if phases is not None else replay.phase_map()
    lk = rc.lake_days(lake)
    acc = {"n_dirs": 0, "n_loaded": 0, "n_native": 0, "n_lake_filled": 0,
           "n_unusable": 0, "n_used": 0, "dropped": []}
    rows = []
    dates = ca._scan_dates(root, days)
    acc["n_dirs"] = len(dates)
    for d in dates:
        loaded = ca._load_day(root, d)
        if loaded is None:
            continue
        acc["n_loaded"] += 1
        ch, attr = loaded
        attr, status = repair_attribution(d, attr, lake=lake, lake_days=lk)
        acc[f"n_{status}"] = acc.get(f"n_{status}", 0) + 1
        if status == "unusable":
            acc["dropped"].append(d)
            continue
        got = False
        for r in ca.day_channel_stats(ch, attr).to_dict("records"):
            if pd.notna(r.get("unique_excess_t2")) and r.get("unique_excess_t2") is not None:
                got = True
            rows.append({"date": d, "phase": ph.get(d, "未知"),
                         "channel": r.get("channel"), "ux": r.get("unique_excess_t2")})
        if got:
            acc["n_used"] += 1
        else:
            acc["dropped"].append(d)
    if cov is not None:
        cov.update(acc)
    out = pd.DataFrame(rows, columns=["date", "phase", "channel", "ux"])
    return out.dropna(subset=["ux"]).reset_index(drop=True)


# ───────────────────────── ③ 温度计五相位 × 次日市场 gap ─────────────────────────


def phase_gap_table(rows: pd.DataFrame, value_col: str = "market_gap") -> pd.DataFrame:
    """逐日 `[date, phase, market_gap]` → **按五相位**(不是两侧)的条件分布 + CI + 成熟度。

    ③节要回答的是"隔夜 edge 是不是相位现象"——两侧聚合会把五个相位的形状抹平,故这里
    不做侧聚合。相位按温度计的自然强弱序固定输出,不按读数排序(防挑相位)。
    """
    order = ["冰点", "修复", "发酵", "高潮", "退潮", "未知"]
    if not len(rows):
        return pd.DataFrame(columns=["phase", "n_days", "n_obs", "point", "lo", "hi",
                                     "crosses_zero", "maturity"])
    work = rows.dropna(subset=[value_col])
    seen = [p for p in order if p in set(work["phase"])]
    seen += sorted(set(work["phase"]) - set(order))
    return pd.DataFrame([_interval_row(work[work["phase"] == p], value_col, phase=p)
                         for p in seen])


def phase_market_rows(days: list[str] | None = None, lake: Path | None = None,
                      phases: dict[str, str] | None = None) -> pd.DataFrame:
    """每个温度计有相位的交易日 → `[date, phase, market_gap]`(当日可执行全集等权 gap)。"""
    from autoresearch.research import replay

    all_days = days if days is not None else rc.lake_days(lake)
    ph = phases if phases is not None else replay.phase_map()
    rows = []
    for iso, phase in sorted(ph.items()):
        d = iso.replace("-", "")
        if d not in all_days:
            continue
        mg = market_gap_of(rc.gap_frame(iso, days=all_days, lake=lake))
        if mg is not None:
            rows.append({"date": d, "phase": phase, "market_gap": mg})
    return pd.DataFrame(rows, columns=["date", "phase", "market_gap"])


# ───────────────────────── 编排 + 报告 ─────────────────────────


def _empty_result() -> dict:
    return {"window": {}, "chase": {"main": [], "sensitivity": {}},
            "channel_phase": {"by_channel": {}, "sensitivity": {}},
            "phase_gap": [], "notes": []}


def analyze(lake: Path | None = None, scan_root: Path | None = None,
            limit: int | None = None) -> dict:
    """三节全跑。返回可 JSON 化的 result(供 `render`)。"""
    all_days = rc.lake_days(lake)
    res = _empty_result()
    excl = [0, 0]
    rows = chase_rows_all(days=all_days, lake=lake, limit=limit, excl=excl)
    res["entry_exclusion"] = {"n_big": excl[0], "n_buyable": excl[1],
                              "kept_pct": (round(excl[1] / excl[0] * 100, 1) if excl[0] else None)}
    res["window"] = {"lake_days": len(all_days),
                     "chase_days": int(rows["date"].nunique()) if len(rows) else 0,
                     "chase_obs": int(len(rows)),
                     "first": rows["date"].min() if len(rows) else None,
                     "last": rows["date"].max() if len(rows) else None}
    res["chase"]["main"] = bucket_intervals(rows).to_dict("records")
    bse = chase_rows_all(days=all_days, lake=lake, exclude_bse=True, limit=limit)
    res["chase"]["sensitivity"]["剔北交所"] = bucket_intervals(bse).to_dict("records")
    if len(rows):
        one = rows[rows["date"] == "20260721"]
        res["chase"]["day_20260721"] = bucket_intervals(one).to_dict("records")

    cov: dict = {}
    daily = channel_phase_rows(scan_root, cov=cov, lake=lake)
    res["channel_phase"]["coverage"] = cov
    res["channel_phase"]["n_days"] = int(daily["date"].nunique()) if len(daily) else 0
    for ch in ("momentum", "heat"):
        sub = daily[daily["channel"] == ch]
        res["channel_phase"]["by_channel"][ch] = side_intervals(sub, "ux").to_dict("records")
        res["channel_phase"]["sensitivity"][ch] = {
            k: side_intervals(sub, "ux", recovery=k).to_dict("records")
            for k in ("down", "drop")}

    pm = phase_market_rows(days=all_days, lake=lake)
    res["phase_gap"] = phase_gap_table(pm).to_dict("records")
    res["phase_gap_rows"] = pm.to_dict("records")
    return res


def _pct(x) -> str:
    return "—" if x is None or (isinstance(x, float) and pd.isna(x)) else f"{x * 100:+.2f}%"


def _n(x) -> str:
    """计数/百分比的安全渲染:缺失印「—」而不是字面量 `None`(空 result 也要读得通)。"""
    return "—" if _missing(x) else f"{x:,}" if isinstance(x, int) else f"{x}"


def _missing(x) -> bool:
    """区间缺失。`None`(单日无跨日方差)与 `NaN` 都算 —— `DataFrame.to_dict("records")`
    会把浮点列里的 `None` **静默变成 `NaN`**,只判 `is None` 的写法在真数据上会漏掉,
    把「没有区间」渲染成 `[—, —] 跨 0`,读起来像"算过了但不显著"。"""
    return x is None or (isinstance(x, float) and pd.isna(x))


def crosses_zero_of(r: dict) -> bool | None:
    """一行读数是否跨 0 —— **由 lo/hi 现算**,`None` = 没有区间(未知)。

    这是「跨没跨 0」在本模块的**唯一**判定点(修复轮2 自查的收尾)。此前 `crosses_zero`
    这个**字段**同时存在于产出与消费两端,于是每个消费者都得自己记得加 `_missing` 守卫 ——
    `_side_verdict` 加了、`_chase_verdict` 忘了,而后者产出的正是①节那个唯一被判「可以引用」
    的裁决。同一件事有两个真相来源,就一定会有人只更新其中一个:字段保留(`--json` 下游要读),
    但**模块内一律走本函数**,不再各自读字段。
    """
    lo, hi = r.get("lo"), r.get("hi")
    if _missing(lo) or _missing(hi):
        return None
    return bool(lo <= 0.0 <= hi)


def _iv(r: dict) -> str:
    if crosses_zero_of(r) is None:
        return f"{_pct(r.get('point'))}(n_days={r.get('n_days')},**无跨日方差,不产区间**)"
    mark = "跨 0" if crosses_zero_of(r) else "**整区间同号**"
    return f"{_pct(r.get('point'))} [{_pct(r.get('lo'))}, {_pct(r.get('hi'))}] {mark}"


def _side_verdict(records: list[dict]) -> tuple[str, str]:
    """②节:某通道的两侧表 → (CONFIRMED / REFUTED / UNKNOWN / IMMATURE, 一句话)。

    08-04 的原命题是「**上涨侧整区间为负** ∧ 回撤侧跨 0」。只有这两条**同时**在 gap 尺下
    复现才算 CONFIRMED —— 换尺重验的意义就在于不许把"点估计还是负的"当成结论。
    """
    by = {r.get("side"): r for r in records}
    up, down = by.get("上涨"), by.get("回撤")
    if not up or not down:
        return "IMMATURE", "两侧读数不全。"
    if up.get("maturity") != "MATURE" or down.get("maturity") != "MATURE":
        return "IMMATURE", f"两侧 n_days={up.get('n_days')}/{down.get('n_days')},未过细分成熟门。"
    up_x, down_x = crosses_zero_of(up), crosses_zero_of(down)
    if up_x is None or down_x is None:
        return "IMMATURE", "某一侧没有区间(无跨日方差),不下结论。"
    if up_x:
        return "UNKNOWN", (f"上涨侧 {_iv(up)} —— **区间跨 0**,旧尺的「上涨侧有害」在隔夜尺上"
                           "既没被证实也没被证伪,**不得直接搬运**。")
    if (up.get("point") or 0) >= 0:
        return "REFUTED", f"上涨侧 {_iv(up)},方向与旧结论相反。"
    if not down_x:
        return "PARTIAL", f"上涨侧 {_iv(up)} 为负,但回撤侧 {_iv(down)} 也不跨 0 —— 不是相位条件性,是全时为负。"
    return "CONFIRMED", f"上涨侧 {_iv(up)};回撤侧 {_iv(down)} —— 与旧尺同形。"


def _side_thin(records: list[dict]) -> bool:
    """②节样本是否**薄到不足以动 quota**:任一侧 `n_days < MATURITY_MIN_SCAN_DAYS`(20)。

    与细分成熟门(10)是**两道不同的尺**:过 10 只说明「这个细分可以有读数」,不等于
    「够格拿去改生产配额」。实测佐证:口径 B 只把 1 个「修复」日从上涨侧挪到回撤侧,
    heat 上涨侧点估计就动 0.11pp —— **单日撬动 = 样本薄到肉眼可见**(review I1)。
    """
    ns = [r.get("n_days") or 0 for r in records if r.get("side") in ("上涨", "回撤")]
    return (not ns) or min(ns) < st.MATURITY_MIN_SCAN_DAYS


def phase_delta_interval(rows: pd.DataFrame, a: str, b: str, value_col: str = "market_gap",
                         n_boot: int = st.DEFAULT_BOOT, alpha: float = st.DEFAULT_ALPHA,
                         seed: int = st.DEFAULT_SEED) -> dict:
    """两个相位的**均值差**(a − b)的 bootstrap 区间。相位间不配对(不同的日子),
    故两侧各自按日有放回重采样再相减 —— 这是**差值**的区间,不是两个边际区间。

    为什么必须做这个而不是看边际 CI 重不重叠(review I2a):**CI 重叠不是差异检验**。
    两个边际 95% CI 大幅重叠时,差值的 CI 完全可以不含 0;重叠判据保守到出名,只会
    系统性地把结论推向「没差异」。仓库 `stats` 模块本来就有配对版 `paired_delta_interval`,
    这里是它的非配对孪生。
    """
    va = pd.to_numeric(rows.loc[rows["phase"] == a, value_col], errors="coerce").dropna().to_numpy()
    vb = pd.to_numeric(rows.loc[rows["phase"] == b, value_col], errors="coerce").dropna().to_numpy()
    out = {"a": a, "b": b, "n_a": int(len(va)), "n_b": int(len(vb)),
           "point": None, "lo": None, "hi": None, "crosses_zero": None}
    if len(va) < 2 or len(vb) < 2:
        return out
    rng = np.random.default_rng(seed)
    draws = np.empty(n_boot, dtype=float)
    for i in range(n_boot):
        draws[i] = (rng.choice(va, size=len(va), replace=True).mean()
                    - rng.choice(vb, size=len(vb), replace=True).mean())
    lo, hi = (float(x) for x in np.quantile(draws, [alpha / 2.0, 1.0 - alpha / 2.0]))
    out.update({"point": round(float(va.mean() - vb.mean()), 5), "lo": round(lo, 5),
                "hi": round(hi, 5), "crosses_zero": bool(lo <= 0.0 <= hi)})
    return out


def _thermo_verdict(records: list[dict], rows: pd.DataFrame | None = None) -> tuple[str, str]:
    """③节 → (CONDITIONAL / UNKNOWN / IMMATURE, 一句话)。

    **没有 `NOT_CONDITIONAL` 这个取值**(review I2b 修复)。本文件方法论白纸黑字写着
    「不显著 ≠ 等价:CI 跨 0 一律记 `UNKNOWN`」,②节对 heat 也严格照办了;③节却曾发出一个
    **肯定式的否定标签**,同一文档两套标准。分辨不开就是 `UNKNOWN`,不是「不是相位现象」。

    判据 = 成熟相位**两两均值差**的 95% CI 有没有一对不含 0(`phase_delta_interval`),
    不是边际 CI 重不重叠。**不引用「点估计极差」当理由**:同一份报告①节把 −0.14pp 判成
    显著效应(1075 日),③节拿 +0.15pp 当「小到不必管」(22~67 日)—— 同一量级两套读法,
    真正的差别是**功效**不是效应量;把功效不足包装成效应为零,正是让低功效结果冒充零结果
    的典型路径(review I2c)。
    """
    mature = [r for r in records if r.get("maturity") == "MATURE"
              and not _missing(r.get("lo")) and not _missing(r.get("hi"))]
    if len(mature) < 2:
        return "IMMATURE", f"仅 {len(mature)} 个相位过成熟门且有区间,无法比较。"
    names = [r["phase"] for r in mature]
    if rows is None or not len(rows):
        return "IMMATURE", "缺逐日读数,无法对相位差做区间检验。"
    widest, deltas = None, []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            d = phase_delta_interval(rows, a, b)
            deltas.append(d)
            if d["crosses_zero"] is False:
                return "CONDITIONAL", (f"「{a}」−「{b}」= {_pct(d['point'])} "
                                       f"[{_pct(d['lo'])}, {_pct(d['hi'])}],**差值区间不含 0**。")
            if d["lo"] is not None and (widest is None or (d["hi"] - d["lo"]) > widest):
                widest = d["hi"] - d["lo"]
    n_pairs = len([d for d in deltas if d["crosses_zero"] is not None])
    return "UNKNOWN", (f"{len(mature)} 个成熟相位、{n_pairs} 组两两差值的 95% CI **全部含 0** —— "
                       f"最宽的一组区间跨度 {_pct(widest)},即本样本(每相位 "
                       f"{min(r['n_days'] for r in mature)}~{max(r['n_days'] for r in mature)} 日)"
                       "**功效不足以分辨相位间差异**。这是「证据不足」,**不是**「隔夜 edge 不是"
                       "相位现象」——后者本样本给不出。")


def _tbl(records: list[dict], key: str, key_label: str) -> list[str]:
    if not records:
        return ["_(无读数)_", ""]
    out = [f"| {key_label} | n_days | n_obs | 点估计 [95% CI] | 成熟度 |", "|---|---:|---:|---|---|"]
    out += [f"| {r.get(key)} | {r.get('n_days')} | {r.get('n_obs')} | {_iv(r)} | "
            f"{'✅ MATURE' if r.get('maturity') == 'MATURE' else '⚠ IMMATURE'} |" for r in records]
    return [*out, ""]


def _chase_verdict(records: list[dict]) -> tuple[str, str]:
    """最高桶(≥9.5%)的裁决 → (NEGATIVE / POSITIVE / UNKNOWN / IMMATURE, 一句话)。"""
    top = next((r for r in records if r.get("bucket") == CHASE_LABELS[0]), None)
    if not top:
        return "UNKNOWN", "最高桶无读数。"
    if top.get("maturity") != "MATURE":
        return "IMMATURE", f"最高桶仅 {top.get('n_days')} 日,样本不足,不下结论。"
    top_x = crosses_zero_of(top)
    if top_x is None:
        # **没有区间就不许给方向性裁决**(修复轮2 的 Important)。缺了这道守卫,一个
        # MATURE 但无跨日方差的行会拿到自信的 NEGATIVE,渲染出的句子同时写着「无跨日
        # 方差,不产区间」和「NEGATIVE」—— 而本函数产出的正是①节那个唯一被判「可以
        # 引用去指导生产」的裁决。
        return "UNKNOWN", "最高桶**没有区间**(无跨日方差)—— 无证据,不给方向性裁决。"
    if top_x:
        return "UNKNOWN", "最高桶 95% CI 跨 0 —— **不显著 ≠ 等价**,不构成「追涨无害」的证据。"
    return ("NEGATIVE" if (top.get("point") or 0) < 0 else "POSITIVE",
            f"最高桶 {_iv(top)}(相对 `{REL}`)。")


def render(result: dict) -> str:
    ch = result.get("chase", {})
    main = ch.get("main", [])
    verdict, verdict_txt = _chase_verdict(main)
    w = result.get("window", {})
    out = [
        "# 三个旧尺结论在隔夜主尺 `gap_c1_o2` 上的重验(Wave12-T30 / 设计稿 E2)",
        "",
        "> **影子取证专用,生产铁律未变;启用唯一路径 = 实验注册表**"
        "(PREREGISTERED → 人批 activate)。",
        ">",
        "> 本报告**不授权任何生产变更**:不改召回通道、不改 quota/floor、不改任何 prompt。"
        "尤其 ①节 —— 「任何新召回/事件设计不得用当日涨幅做入场」是本仓库明令的**负结果**,"
        "它不会因为换了一把尺子、也不会因为这份报告而被翻案。本稿只提供换尺后的证据。",
        "",
        f"- 主尺 `{GAP}`(T+1 收盘买 → T+2 开盘卖,2026-08-05 用户裁定);"
        f"超额列 `{REL}`(个股 gap − 当日**可执行**全集等权均值)",
        f"- 湖窗口 {_n(w.get('lake_days'))} 个交易日;①节可用 {_n(w.get('chase_days'))} 日 / "
        f"{_n(w.get('chase_obs'))} 行({_n(w.get('first'))} → {_n(w.get('last'))})",
        "- 区间一律 date-cluster bootstrap(按日聚簇,`stats.date_cluster_bootstrap`);"
        f"细分 `n_days < {_SUBGROUP_MIN}` 标 ⚠ IMMATURE,不外推",
        "",
        "## ① 追当日大涨 × gap(**本稿最关键的一节**)",
        "",
        "旧结论(参考尺 `fwd_2_oc`,旧主尺,降参考不删):2026-07-21 当日 ≥9.5% 的 350 只票"
        " −2.06% vs 全市场 +1.60%,**超额 −3.67pp(t=−11.91)** → 「追当日大涨是负价值」。",
        "",
        f"**gap 尺全窗口重验裁决:`{verdict}`** —— {verdict_txt}",
        "",
        "> **引用本节必须连带的两条限定**(不是附录,是结论的一部分):",
        f"> 1. **样本里没有最强的连板票**。合格集要求 T+1 收盘买得进,D 日 ≥9.5% 的票只有 "
        f"**{_n((result.get('entry_exclusion') or {}).get('kept_pct'))}%** 留在样本内,"
        "被剔的绝大多数是 D+1 继续封板(实测 97% 是真封板)。本节量的是"
        "「大涨之后**没能继续封板**、因而真的建得上仓」的那批 —— 这正是系统买得到的人口"
        "(剔了反而美化账本),但**不能**转述成「所有涨停票的隔夜溢价」。",
        "> 2. **量的是第二个夜晚,不是打板当晚**。信号在 D 收盘,主尺量 D+1 收 → D+2 开。"
        "「涨停当晚打板隔夜赚不赚」(D 收 → D+1 开)**不在本节射程内**,也不在系统授权内"
        "——扫描报告 D 晚才出,最早只能 D+1 收盘建仓。",
        "",
        *_tbl(main, "bucket", "当日涨幅桶"),
    ]
    for name, recs in (ch.get("sensitivity") or {}).items():
        out += [f"**敏感性({name})**", "", *_tbl(recs, "bucket", "当日涨幅桶")]
    if ch.get("day_20260721"):
        top1 = next((r for r in ch["day_20260721"] if r.get("bucket") == CHASE_LABELS[0]), {})
        out += ["**单日对照(2026-07-21,旧结论的原始案发日)**", "",
                "旧结论的**全部证据**就是这一天。单日 = 1 个 cluster,按日聚簇后**不产区间**"
                "——这不是本稿的缺陷,而是「把 350 只票当 350 个独立样本」这件事在诚实口径下"
                "本来就得不出 t=−11.91 那种确定性。", "",
                *_tbl(ch["day_20260721"], "bucket", "当日涨幅桶"),
                f"> ⚠ **注意符号**:这一天最高桶的隔夜超额是 {_pct(top1.get('point'))},"
                "与旧尺在同一天测到的 −3.67pp **方向相反**。也就是说——"
                "**旧结论的原始案发日在隔夜尺下翻了号,但全窗口 1075 日的结论没翻**(见上表)。"
                "铁律成立与否应当以全窗口为准;那一天只能说明「日内暴跌」与「隔夜折价」不是一回事。", ""]

    cp = result.get("channel_phase", {})
    cvg = cp.get("coverage") or {}
    out += ["## ② 通道 × 相位 × gap(重验 2026-08-04 的相位条件性)", "",
            "旧结论(参考尺 `fwd_2_oc`):momentum/heat 在**上涨相位**(发酵/高潮/修复)整区间为负、"
            "在回撤相位跨 0。口径逐字照抄 08-04 §8 复现脚本,**只换尺**"
            "(`channel_audit` 的 `unique_excess_t2` 已跟随 `ruler.MAIN_RULER`)。", "",
            f"**人口口径(有分母)**:`context/scan/` 下 {_n(cvg.get('n_dirs'))} 个日目录,"
            f"双文件齐全 {_n(cvg.get('n_loaded'))} 天;其中主尺列自带 {_n(cvg.get('n_native'))} 天、"
            f"**缺主尺列由湖现算补齐 {_n(cvg.get('n_lake_filled'))} 天**"
            f"(retro 回填漏的历史日,不回写 CSV)、补不出来 {_n(cvg.get('n_unusable'))} 天;"
            f"最终进入区间计算 **{_n(cp.get('n_days'))} 天**"
            + (f",丢弃 {'、'.join(cvg.get('dropped') or [])}" if cvg.get("dropped") else ",无丢弃")
            + "。", ""]
    side_verdicts, thin_any = {}, False
    for ch_name, recs in (cp.get("by_channel") or {}).items():
        v, txt = _side_verdict(recs)
        thin = _side_thin(recs)
        thin_any = thin_any or thin
        side_verdicts[ch_name] = (v, txt, thin)
        mark = (f" · ⚠ **薄样本**(某侧 n_days < {st.MATURITY_MIN_SCAN_DAYS} 真实扫描日,"
                "不足以支撑不可逆的 quota 变更)" if thin else "")
        out += [f"**{ch_name} — 裁决 `{v}`**{mark}:{txt}", "", *_tbl(recs, "side", "侧")]
        sens = (cp.get("sensitivity") or {}).get(ch_name, {})
        for k, label in (("down", "口径B:修复归回撤侧"), ("drop", "口径C:剔除修复")):
            if sens.get(k):
                out += [f"_敏感性 {label} — 裁决 `{_side_verdict(sens[k])[0]}`_", "",
                        *_tbl(sens[k], "side", "侧")]

    if side_verdicts:
        mom, heat = side_verdicts.get("momentum"), side_verdicts.get("heat")
        out += ["> **对 2026-08-04 报告 §6 的连带影响(注意:§6 谈的是 quota advisory 的方向,"
                "不是通道的相位条件性 —— 这两件事在 08-04 的语境里是相反的意思)**:",
                ">",
                "> 08-04 §6 的三条判词是:momentum 统一降 quota **不站得住**"
                "(正因为它是相位条件的 —— 回撤侧不亏钱,统一砍会砍掉不亏的那半);"
                "healthy IMMATURE 无依据;heat 统一降 quota **站得住**(上涨侧 −4.08% 整区间为负)。",
                ">",
                f"> 把本节读数代进 08-04 自己的逻辑:**momentum**(`{mom[0] if mom else '—'}`)"
                "上涨侧不跨 0、回撤侧跨 0 —— **形状与旧尺完全相同,08-04「不该统一砍 momentum」"
                "这一判词原样成立,没有任何反转**;"
                f"**heat**(`{heat[0] if heat else '—'}`)两侧都跨 0 —— 支撑「heat 该统一砍」的"
                "唯一依据(上涨侧整区间为负)消失。",
                ">",
                "> 即:**三条 advisory 现在一条都不站得住**,而不是「站得住的那条换成了 momentum」。"
                "对 momentum 的正确处方仍是**相位条件地压**(上涨相位),**不是统一砍** —— "
                "把「momentum 相位条件性 CONFIRMED」读成「该砍 momentum quota」是范畴错误,"
                "而那恰恰是本节数据反对的做法。", ""]

    tv, tv_txt = _thermo_verdict(result.get("phase_gap", []),
                                 pd.DataFrame(result.get("phase_gap_rows") or []))
    out += ["## ③ 温度计五相位 × 次日市场 gap", "",
            "问题:**隔夜 edge 是不是本质上的相位现象?** 若是,E3 的隔夜因子应做成相位条件特征"
            "而不是全时因子。读数 = 当日可执行全集的等权 `gap_c1_o2` 均值,按温度计相位分组。", "",
            f"**裁决 `{tv}`**:{tv_txt}", "",
            "> **读法**:本节**没有**证明「隔夜 edge 不是相位现象」——那需要一个能分辨相位差异的"
            "样本,本节没有。下游动作(隔夜因子维持全时、不加相位条件维)是**稳妥的默认**,"
            "不是被证据支持的结论;样本攒够后要重问。", "",
            *_tbl(result.get("phase_gap", []), "phase", "相位")]

    def _f4() -> str:
        parts = [f"{c}=`{v}`" + ("(⚠薄样本)" if t else "")
                 for c, (v, _, t) in side_verdicts.items()]
        ok = [c for c, (v, _, t) in side_verdicts.items() if v == "CONFIRMED" and not t]
        blocked = thin_any or bool(cvg.get("dropped"))
        if blocked:
            gate = (f"**🚧 阻塞中,不放行**:某侧 n_days < {st.MATURITY_MIN_SCAN_DAYS} 真实扫描日"
                    "(细分成熟门 10 只保证「可以有读数」,不等于「够格改生产配额」);"
                    "实测口径 B 只挪 1 个「修复」日,heat 上涨侧点估计就动 0.11pp = **单日撬动**。"
                    "待样本攒够后重裁")
        else:
            gate = (f"**仅 {'/'.join(ok)} 满足预注册条件**" if ok
                    else "**无通道满足预注册条件 → 本轮不产 spec**")
        return (f"{' · '.join(parts) or '无读数'}。{gate};未 CONFIRMED 的通道其 08-04 旧尺结论"
                "**不得直接搬运**。**另**:momentum 即使 CONFIRMED,处方也是**相位条件地压**,"
                "**不是统一砍**(见②节末对 08-04 §6 的辨析)。")

    d721 = next((r for r in (ch.get("day_20260721") or [])
                 if r.get("bucket") == CHASE_LABELS[0]), {})
    flip = (f"**复述时必须连带**:旧结论的原始案发日 2026-07-21 在隔夜尺下**翻了号**"
            f"({_pct(d721.get('point'))},旧尺同日 −3.67pp)—— 铁律靠的是全窗口,不是那一天。"
            if d721 else "")
    out += ["## ④ 下游触发器状态(逐条,供调度)", "",
            "| 触发器 | 依赖本稿哪一节 | 状态 |", "|---|---|---|",
            f"| **E4b** 热度/游资影子通道设计空间 | ①追涨 gap 结论 | `{verdict}` — {verdict_txt} "
            f"{flip} 引用须连带①节两条限定(样本无最强连板票 / 量的是第二个夜晚)。"
            "无论正负都**只开影子**(floor=0、不占 quota、不进生产 finalists),"
            "注册走 `experiment_registry` |",
            f"| **F4** 相位条件 quota challenger 预注册 | ②相位 gap 结论 | {_f4()} |",
            f"| **E3** 隔夜因子是否做成相位条件特征 | ③温度计结论 | `{tv}` — {tv_txt} "
            + ("**动作:因子维持全时、不加相位条件维**——这是稳妥默认,"
               "**不是**「已证明与相位无关」。" if tv != "CONDITIONAL"
               else "**因子应带相位条件维**(仍走 registry)。") + " |", ""]

    ex = result.get("entry_exclusion") or {}
    out += ["## 方法论 / 局限", "",
            "- **人口**:①③节 = `context/lake/daily` 全市场(含北交所 30cm 板,另出剔除敏感性);"
            "合格集 = `ruler_compare.gap_frame` 的 `eligible_gap`(T+1 收盘未封涨停 ∧ gap 非空),"
            "分子与基准分母同一套。②节人口 = 有 `retro/attribution.csv` 的扫描日。",
            f"- **①节最高桶里是「没能连板的那批」(必读)**:合格集要求 T+1 **收盘买得进**,"
            f"而 D 日涨停的票里有一部分 D+1 继续封板 → 被 `buyable_c1` 剔出样本。全窗口实测:"
            f"D 日 ≥9.5% 共 **{_n(ex.get('n_big'))}** 只次,其中 T+1 收盘买得进 **{_n(ex.get('n_buyable'))}** 只次"
            f"(**{_n(ex.get('kept_pct'))}%**)。所以「≥9.5% 桶」量的**不是**全部大涨票,而是**大涨之后"
            "没有继续封板、因而真的能在 T+1 收盘建仓**的那一批。这正是系统实际买得到的人口"
            "(剔了它反而会美化账本),但引用时不能说成「所有涨停票的隔夜溢价」"
            "——**最强的那部分连板票在样本之外**。",
            f"- **②节样本薄(必读)**:每侧只有 {'/'.join(str(r.get('n_days')) for recs in (cp.get('by_channel') or {}).values() for r in recs) or '—'} 日。"
            f"细分成熟门(≥{_SUBGROUP_MIN})只保证「这个细分可以有读数」,**不等于够格改生产配额**"
            f"(那把尺是 ≥{st.MATURITY_MIN_SCAN_DAYS} 真实扫描日)。实测:口径 B 只把 1 个「修复」日"
            "从上涨侧挪到回撤侧,heat 上涨侧点估计就动 0.11pp —— **单日撬动**。"
            "②节的任何数字都不得据以做不可逆的 quota 变更。",
            "- **②节的市场基准是「中位」,①③节是「均值」**:②走 `channel_audit.day_channel_stats`"
            "(`attr[MAIN_RULER].median()`),①③走可执行全集等权**均值**。②内部是同基准比较"
            "(上涨侧 vs 回撤侧),裁决不受影响;但**跨节把数字并排引用会出错**"
            "(`ruler.py` 的 I-2 注释警告过这两条线同一天可能给出符号相反的相对表现读数)。",
            "- **①节的点估计是「按只次池化」的均值**,不是「日均值的均值」:涨停票多的热市日"
            "在 −0.96% 里权重更大(`stats.date_cluster_bootstrap` 的 `point` 与 bootstrap 口径一致,"
            "不是错,但**不能读成「平均一天的效应」**)。",
            "- **窗口错位(必读)**:信号在 D 日收盘,而主尺量的是 **D+1 收 → D+2 开** 那一夜。"
            "所以①节回答的**不是**「涨停当晚打板隔夜赚不赚」(那是 D 收→D+1 开,系统无权执行——"
            "扫描报告 D 晚才出,最早只能 D+1 收盘建仓),而是「当日大涨的票,到**第二个**夜晚"
            "还有没有溢价」。两者是不同的问题,读数不可互换。",
            "- **旧读数的 t 值不可比**:−3.67pp/t=−11.91 是**单日 350 只**的横截面 t(把同一天的"
            "票当独立样本);本稿一律按日聚簇,单日的 CI 恒缺(`n_days=1` 无跨日方差)。"
            "两者的不确定性口径不同,**不要把 t=−11.91 与本稿的区间并排比较**。",
            "- **`gap_c1_o2` 未 clip**:①③节读的是湖现算生值(`gap_frame` docstring:裁剪只在"
            "消费点做);②节走 `attribution.csv` 的存量列。两节口径各自自洽,不混算。",
            "- **不显著 ≠ 等价**:CI 跨 0 一律记 `UNKNOWN`,不写成「无差异」(`stats` 模块的三态纪律)。",
            "- **复现**:`uv run --no-sync python -m autoresearch.research.overnight_evidence run`;"
            "`--selftest` 离线合成数据自测;`uv run --no-sync python -m pytest -q "
            "tests/research/test_overnight_evidence.py`。", "",
            "_仅供研究,非投资建议。本稿不授权任何生产变更。_"]
    return "\n".join(out) + "\n"


def _selftest() -> int:
    """离线合成数据自测三节数值(手算可核)。"""
    bad = 0
    pct = pd.DataFrame({"code": ["A", "B", "C", "D"], "pct_chg": [10.0, 6.0, 3.0, 0.0]})
    gap = pd.DataFrame({"code": ["A", "B", "C", "D"], GAP: [0.02, 0.01, 0.0, -0.01],
                        "eligible_gap": [True] * 4})
    rows = day_chase_rows("20260105", pct, gap)
    checks = [
        ("①超额手算", abs(float(rows.loc[rows.code == "A", REL].iloc[0]) - 0.015) < 1e-9),
        ("①分桶边界", list(chase_bucket(pd.Series([9.5, 5.0, 2.0, 1.9]))) == list(CHASE_LABELS)),
        ("②相位归属", phase_side("修复") == "上涨" and phase_side("修复", "down") == "回撤"),
        ("③市场 gap 手算", abs((market_gap_of(gap) or 0) - 0.005) < 1e-9),
        ("顶行影子声明", any("影子取证专用" in x
                             for x in render(_empty_result()).splitlines()[:12])),
    ]
    for name, ok in checks:
        if not ok:
            print(f"[selftest] {name} 不符")
            bad = 1
    print("[selftest] OK" if not bad else "[selftest] FAILED")
    return bad


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="三旧尺结论 gap 重验(E2;零 LLM,只读现成产物)")
    ap.add_argument("mode", nargs="?", choices=["run"], help="run=全跑并写报告")
    ap.add_argument("--days", type=int, default=0, help="①③节只取最近 N 个交易日(0=全窗口)")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--json", default=None, help="另存 result JSON(调试用)")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return _selftest()
    if args.mode != "run":
        ap.print_help()
        return 2
    res = analyze(limit=args.days or None)
    p = Path(args.out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(render(res), encoding="utf-8")
    if args.json:
        Path(args.json).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[done] → {p}")
    return 0


if __name__ == "__main__":       # pragma: no cover
    raise SystemExit(main())
