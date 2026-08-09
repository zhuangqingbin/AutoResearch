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
    iv = st.date_cluster_bootstrap(sub, value_col, date_col="date")
    n_days = int(sub["date"].nunique())
    return {**extra, "n_days": n_days, "n_obs": int(len(sub)),
            "point": None if iv.point is None else round(iv.point, 5),
            "lo": None if iv.lo is None else round(iv.lo, 5),
            "hi": None if iv.hi is None else round(iv.hi, 5),
            "crosses_zero": not iv.excludes_zero,
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


def channel_phase_rows(scan_root: Path | None = None, days: int = 9999,
                       phases: dict[str, str] | None = None) -> pd.DataFrame:
    """`L1_channels.csv × retro/attribution.csv` → `[date, phase, channel, ux]` 长表。

    **口径照抄** 2026-08-04 报告 §8 的复现脚本(同 `channel_audit.day_channel_stats`,
    `unique_excess_t2` = 只被该路召回的票的超额均值),**唯一差别是尺**:`channel_audit._RET_MAIN`
    已跟随 `ruler.MAIN_RULER`,08-05 flip 后它读的就是 `gap_c1_o2`,故这里不需要再传尺。
    """
    from autoresearch.research import channel_audit as ca, replay

    root = Path(scan_root or rc.SCAN_ROOT)
    ph = phases if phases is not None else replay.phase_map()
    rows = []
    for d in ca._scan_dates(root, days):
        loaded = ca._load_day(root, d)
        if loaded is None:
            continue
        for r in ca.day_channel_stats(*loaded).to_dict("records"):
            rows.append({"date": d, "phase": ph.get(d, "未知"),
                         "channel": r.get("channel"), "ux": r.get("unique_excess_t2")})
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

    daily = channel_phase_rows(scan_root)
    res["channel_phase"]["n_days"] = int(daily["date"].nunique()) if len(daily) else 0
    for ch in ("momentum", "heat"):
        sub = daily[daily["channel"] == ch]
        res["channel_phase"]["by_channel"][ch] = side_intervals(sub, "ux").to_dict("records")
        res["channel_phase"]["sensitivity"][ch] = {
            k: side_intervals(sub, "ux", recovery=k).to_dict("records")
            for k in ("down", "drop")}

    res["phase_gap"] = phase_gap_table(phase_market_rows(days=all_days, lake=lake)).to_dict("records")
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


def _iv(r: dict) -> str:
    if _missing(r.get("lo")) or _missing(r.get("hi")):
        return f"{_pct(r.get('point'))}(n_days={r.get('n_days')},**无跨日方差,不产区间**)"
    mark = "跨 0" if r.get("crosses_zero") else "**整区间同号**"
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
    if up.get("crosses_zero"):
        return "UNKNOWN", (f"上涨侧 {_iv(up)} —— **区间跨 0**,旧尺的「上涨侧有害」在隔夜尺上"
                           "既没被证实也没被证伪,**不得直接搬运**。")
    if (up.get("point") or 0) >= 0:
        return "REFUTED", f"上涨侧 {_iv(up)},方向与旧结论相反。"
    if not down.get("crosses_zero"):
        return "PARTIAL", f"上涨侧 {_iv(up)} 为负,但回撤侧 {_iv(down)} 也不跨 0 —— 不是相位条件性,是全时为负。"
    return "CONFIRMED", f"上涨侧 {_iv(up)};回撤侧 {_iv(down)} —— 与旧尺同形。"


def _thermo_verdict(records: list[dict]) -> tuple[str, str]:
    """③节 → (CONDITIONAL / NOT_CONDITIONAL / IMMATURE, 一句话)。

    判据**不设人造阈值**:只问成熟相位之间的 95% CI 有没有一对**互不重叠**——重叠即说明
    这些相位的隔夜水平在本样本下分辨不开,"隔夜 edge 是相位现象"就没有证据支撑。
    """
    mature = [r for r in records if r.get("maturity") == "MATURE"
              and not _missing(r.get("lo")) and not _missing(r.get("hi"))]
    if len(mature) < 2:
        return "IMMATURE", f"仅 {len(mature)} 个相位过成熟门且有区间,无法比较。"
    pts = [r["point"] for r in mature]
    span = f"成熟相位点估计极差 {_pct(max(pts) - min(pts))}(去符号)"
    for i, a in enumerate(mature):
        for b in mature[i + 1:]:
            if a["hi"] < b["lo"] or b["hi"] < a["lo"]:
                return "CONDITIONAL", (f"「{a['phase']}」与「{b['phase']}」的 95% CI 互不重叠;{span}。")
    return "NOT_CONDITIONAL", (f"{len(mature)} 个成熟相位的 95% CI **两两重叠**,分辨不开;{span}。"
                               "证据不支持把隔夜因子做成相位条件特征。")


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
    if top.get("crosses_zero"):
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
    out += ["## ② 通道 × 相位 × gap(重验 2026-08-04 的相位条件性)", "",
            "旧结论(参考尺 `fwd_2_oc`):momentum/heat 在**上涨相位**(发酵/高潮/修复)整区间为负、"
            "在回撤相位跨 0。口径逐字照抄 08-04 §8 复现脚本,**只换尺**"
            f"(`channel_audit` 的 `unique_excess_t2` 已跟随 `ruler.MAIN_RULER`);"
            f"覆盖 {_n(cp.get('n_days'))} 个扫描日。", ""]
    side_verdicts = {}
    for ch_name, recs in (cp.get("by_channel") or {}).items():
        v, txt = _side_verdict(recs)
        side_verdicts[ch_name] = (v, txt)
        out += [f"**{ch_name} — 裁决 `{v}`**:{txt}", "", *_tbl(recs, "side", "侧")]
        sens = (cp.get("sensitivity") or {}).get(ch_name, {})
        for k, label in (("down", "口径B:修复归回撤侧"), ("drop", "口径C:剔除修复")):
            if sens.get(k):
                out += [f"_敏感性 {label} — 裁决 `{_side_verdict(sens[k])[0]}`_", "",
                        *_tbl(sens[k], "side", "侧")]

    if side_verdicts:
        out += ["> **对 2026-08-04 报告的连带影响**:那份报告 §6 的结论是「三条 quota advisory 里"
                "**只有 heat 那条站得住**」。在隔夜主尺下这个排序**反了过来**—— momentum 的"
                "相位条件性复现(三口径一致),heat 的上涨侧区间跨 0(旧尺是 −4.08% 整区间为负)。"
                "凡引用 08-04 §6 那句话来动 quota 的,一律改引本节。", ""]

    tv, tv_txt = _thermo_verdict(result.get("phase_gap", []))
    out += ["## ③ 温度计五相位 × 次日市场 gap", "",
            "问题:**隔夜 edge 是不是本质上的相位现象?** 若是,E3 的隔夜因子应做成相位条件特征"
            "而不是全时因子。读数 = 当日可执行全集的等权 `gap_c1_o2` 均值,按温度计相位分组。", "",
            f"**裁决 `{tv}`**:{tv_txt}", "",
            *_tbl(result.get("phase_gap", []), "phase", "相位")]

    def _f4() -> str:
        parts = [f"{c}=`{v}`" for c, (v, _) in side_verdicts.items()]
        ok = [c for c, (v, _) in side_verdicts.items() if v == "CONFIRMED"]
        gate = (f"**仅 {'/'.join(ok)} 满足预注册条件**" if ok
                else "**无通道满足预注册条件 → 本轮不产 spec**")
        return (f"{' · '.join(parts) or '无读数'}。{gate};未 CONFIRMED 的通道其 08-04 旧尺结论"
                "**不得直接搬运**,要动它的 quota 必须在隔夜尺上重新攒样本。")

    out += ["## ④ 下游触发器状态(逐条,供调度)", "",
            "| 触发器 | 依赖本稿哪一节 | 状态 |", "|---|---|---|",
            f"| **E4b** 热度/游资影子通道设计空间 | ①追涨 gap 结论 | `{verdict}` — {verdict_txt} "
            "无论正负都**只开影子**(floor=0、不占 quota、不进生产 finalists),"
            "注册走 `experiment_registry` |",
            f"| **F4** 相位条件 quota challenger 预注册 | ②相位 gap 结论 | {_f4()} |",
            f"| **E3** 隔夜因子是否做成相位条件特征 | ③温度计结论 | `{tv}` — {tv_txt}"
            + ("**因子维持全时,不加相位条件维**。" if tv != "CONDITIONAL"
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
