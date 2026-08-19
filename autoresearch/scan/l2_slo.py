#!/usr/bin/env python3
"""winner-capture SLO —— 菜单质量的主 SLO(确定性,零 LLM,不承诺 alpha)。

design: docs/specs/2026-08-03-scan-next-wave-brainstorm-design.md §3.1 O1

**先说天花板**(§3.0 的坦白):L2 全 zoo 模型 OOS rank-IC 负、composite-top200 四年 ≈0
→ L2 不预测、只做菜单。所以本模块是**守菜单质量**的工程,不承诺 alpha;0 买根因的修复
主战场在 L1 权重/召回线。

## 三件必须先钉死的事

1. **winner 定义**(§3.1 第一句)。主尺 = `ruler.MAIN_RULER`(现 `gap_c1_o2`;2026-08-05 换尺
   前是 `fwd_2_oc`),同时满足:全市场 top-decile、绝对收益阈、**入场腿**可买/可交易
   (旗随尺走,`entry_flag_for()` 单点选:`gap_c1_o2` → D+1 收盘 `buyable_c1`,`fwd_2_oc` →
   D+1 开盘 `buyable`)。并列按 `>=` 一并计入;停牌/封板不可买 → 不算 winner。
   落盘的 `winner_definition` 串由 `MAIN_RULER`/`entry_flag_for()` **现算**(见下方常量),
   所以重跑报表即自动跟随换尺——旧报表里的旧串是当时的真值,不改写。
   `pinned` 分列不混算 —— **不得**把 retro 的复合 winner 和「纯 top-decile」混叫一个标签,
   所以两套口径在这里各有各的 `winner_definition` 字符串,随读数一起落盘。

2. **端到端 vs 条件召回**(§3.1 第二段)。只报 `WC_L2_all` 会把 L0/L1 的漏失全部归责给
   L2。故三层同时出:

     WC_L1_all       = |L1 召回 ∩ winners| / |winners|          端到端
     WC_L2_all       = |L2 ∩ winners| / |winners|               端到端
     WC_L1_given_L0  = |L1 ∩ winners| / |L0 池内 winners|       条件
     WC_L2_given_L1  = |L2 ∩ winners| / |L1 召回内 winners|     条件

   **K 用实际值**(含/不含 pinned 分列),不写死 L1-1001/L2-203 —— 那两个数是某一天的
   快照,写死等于把当天的偶然当成契约。

3. **报警线**(§3.1 末段)。用当日**之前**的 expanding P25,不是全期分位 ——
   全期分位含未来,回看时永远「没报警」。历史不足 `min_history` → 不报警(而不是拿
   两个点定分位)。

## 一句必须跟着读数走的话

winner capture 是**主** SLO,不是唯一指标:扩大 K 或集中追热点都能被动做高它。
所以 `guards` 里同时给行业集中度、lane 覆盖、selection_reason 分布与日间稳定性,
`render` 把这句话印在表下面。

  uv run --no-sync python -m autoresearch.scan.l2_slo
"""
from __future__ import annotations

import argparse
import json
from itertools import combinations
from pathlib import Path

import pandas as pd

from autoresearch.common import stats as st, workspace as ws
from autoresearch.common.ruler import MAIN_RULER, entry_flag_for, entry_tradable

SCHEMA_VERSION = 1
DEFAULT_ROOT = ws.scan_root()
OUT_JSON = ws.reports_root() / "scan/l2_slo.json"
OUT_MD = ws.reports_root() / "scan/l2_slo.md"

TOP_DECILE = 0.90            # 全市场 top-decile(与 retro.attribute_frame 同源)
ABS_THRESHOLD = 0.02         # 绝对收益阈:光排进前 10% 但只涨 0.1% 不算赢
MIN_HISTORY = 10             # expanding P25 的最小历史日数

WINNER_DEFINITION = (
    f"{MAIN_RULER} ≥ 全市场可交易成熟票 P{int(TOP_DECILE * 100)} "
    f"∧ {MAIN_RULER} ≥ {ABS_THRESHOLD:+.2%} ∧ 入场腿可执行({entry_flag_for()})∧ 可交易(tradable);"
    "并列按 ≥ 一并计入。**不是** retro 的复合 winner,两者不得混叫一个标签")


def _read(path: Path) -> pd.DataFrame | None:
    if not path.exists():
        return None
    try:
        return pd.read_csv(path, dtype={"code": str})
    except Exception:  # noqa: BLE001
        return None


def _codes(frame: pd.DataFrame | None) -> set[str]:
    if frame is None or not len(frame) or "code" not in frame.columns:
        return set()
    return set(frame["code"].astype(str).str.split(".").str[0].str.zfill(6))


def day_winners(attr: pd.DataFrame) -> tuple[pd.DataFrame, str]:
    """当日 winner 集合(本模块自己的口径,不借用 attribution 的 `winner` 列)。

    为什么不复用那一列:`retro.attribute_frame` 的 winner 是**复合**定义(含 bucket 语义),
    §3.1 明令「不得把它和纯 top-decile 混叫一个标签」。这里重算,并把定义字符串带出去。
    """
    a = attr.copy()
    a["code"] = a["code"].astype(str).str.zfill(6)
    a[MAIN_RULER] = pd.to_numeric(a.get(MAIN_RULER), errors="coerce")
    # C1 修复(final-review 2026-08-08):入场腿旗跟随 MAIN_RULER 选(entry_tradable 单点),
    # 不是硬编码 "buyable"——换尺后 fwd_2_oc 的旧旗(D+1 开盘)与 gap_c1_o2 的入场腿(D+1
    # 收盘)不等价,旧旗会把「收盘封死买不进」的票误留在 winner 集合里。
    buyable = entry_tradable(a)
    tradable = a["tradable"].fillna(True).astype(bool) if "tradable" in a.columns \
        else pd.Series(True, index=a.index)
    eligible = buyable & tradable & a[MAIN_RULER].notna()
    if not eligible.any():
        return a.iloc[0:0], WINNER_DEFINITION
    cutoff = float(a.loc[eligible, MAIN_RULER].quantile(TOP_DECILE))
    is_winner = eligible & (a[MAIN_RULER] >= cutoff) & (a[MAIN_RULER] >= ABS_THRESHOLD)
    return a[is_winner], WINNER_DEFINITION


def _capture(numer: set[str], denom: set[str]) -> float | None:
    """分母为空 → `None`(**不是 0、也不是 1**)。那天没有赢家可捞,这个比率不存在。"""
    return round(len(numer & denom) / len(denom), 6) if denom else None


def day_slo(scan_dir: Path | str) -> dict | None:
    """单日三层 winner capture + 守卫。缺 attribution/L1 产物 → `None`(presence-gated)。"""
    day = Path(scan_dir)
    attr = _read(day / "retro" / "attribution.csv")
    if attr is None or not len(attr) or "code" not in attr.columns:
        return None
    winners_frame, definition = day_winners(attr)
    winners = _codes(winners_frame)

    l0 = _codes(_read(day / "L1_scored_full.csv"))
    l1 = _codes(_read(day / "L1_recall_top1000.csv"))
    l2_frame = _read(day / "L2_gbdt_top200.csv")
    l2 = _codes(l2_frame)
    if not l0 and not l1 and not l2:
        return None

    pinned: set[str] = set()
    if l2_frame is not None and "pinned" in l2_frame.columns:
        pinned = _codes(l2_frame[l2_frame["pinned"].fillna(False).astype(bool)])
    elif l2_frame is not None and "selection_reason" in l2_frame.columns:
        pinned = _codes(l2_frame[l2_frame["selection_reason"] == "pinned"])

    winners_in_l0 = winners & l0
    winners_in_l1 = winners & l1
    return {
        "date": day.name,
        "winner_definition": definition,
        "n_winners": len(winners),
        "n_winners_in_l0": len(winners_in_l0),
        "n_winners_in_l1": len(winners_in_l1),
        # K 用实际值,不写死 —— 含 pinned 与不含 pinned 分列
        "k_l0": len(l0), "k_l1": len(l1), "k_l2": len(l2),
        "k_l2_ex_pinned": len(l2 - pinned), "n_pinned": len(pinned),
        # 端到端(分母 = 全部 winner)
        "wc_l1_all": _capture(l1, winners),
        "wc_l2_all": _capture(l2, winners),
        "wc_l2_all_ex_pinned": _capture(l2 - pinned, winners),
        # 条件召回(分母 = 上一层池内的 winner)—— 避免把 L0/L1 的漏失归责给 L2
        "wc_l1_given_l0": _capture(l1, winners_in_l0),
        "wc_l2_given_l1": _capture(l2, winners_in_l1),
        "guards": _guards(l2_frame),
    }


def _guards(l2_frame: pd.DataFrame | None) -> dict:
    """守卫向量 —— winner capture 可以靠扩 K / 追热点被动做高,守卫是它的反面约束。"""
    if l2_frame is None or not len(l2_frame):
        return {"n": 0}
    out: dict = {"n": int(len(l2_frame))}
    if "industry" in l2_frame.columns:
        counts = l2_frame["industry"].astype(str).value_counts()
        out["top_industry"] = str(counts.index[0])
        out["top_industry_share"] = round(float(counts.iloc[0] / len(l2_frame)), 6)
        out["n_industries"] = int(counts.size)
    if "selection_reason" in l2_frame.columns:
        out["selection_reason"] = {
            str(k): int(v) for k, v in
            l2_frame["selection_reason"].value_counts().items()}
    if "recall_channels" in l2_frame.columns:
        lanes = set()
        for value in l2_frame["recall_channels"].fillna("").astype(str):
            lanes |= set(value.split("|")) - {"", "(backfill)", "pinned"}
        out["lane_coverage"] = sorted(lanes)
        out["n_lanes"] = len(lanes)
    return out


def collect(scan_root: Path | str | None = None) -> pd.DataFrame:
    root = Path(scan_root or DEFAULT_ROOT)
    if not root.exists():
        return pd.DataFrame()
    rows = [r for day in sorted(p for p in root.iterdir()
                                if p.is_dir() and p.name[:2] == "20")
            if (r := day_slo(day)) is not None]
    return pd.DataFrame(rows)


# ═══════════════════════════════════════════════════════════════════════════
# T21(Wave12·F3):per-channel 端到端 capture 六跳漏斗
#
# 病灶:上面那三层曲线只回答「菜单整体漏没漏赢家」,答不出**哪一路**在哪一段掉链子。
# `channel_audit` 有各路的 T+2 超额账本,但它只看召回那一跳——一路召回得再准,如果它的
# 票全在 pass1 被切、或全在 L4 拿 Hold,那条路对最终决策的贡献仍然是 0,而现有仪器一个
# 字都不会说。六跳漏斗把「因何而来」一路量到「最后买没买」。
#
# ## 三个必须先钉死的口径
#
# 1. **存活集合逐跳嵌套**:`alive[k] = alive[k-1] ∩ survivors[k]`。不嵌套的话,某只票在
#    pass1 被切、却因为下游产物里偶尔出现的孤儿行(护照的 `orphans`,实测 3 天共 45 只)
#    重新"复活"进 finalist,漏斗会出现 n 变大的一跳 —— 那是数据异常,不是漏斗形态。
#
# 2. **条件 capture 的分母 = 上一跳存活集合**,不是端到端分母。端到端分母会把上游的漏失
#    一路归责给下游(这正是本模块开头 §3.1 第二段为三层曲线立的同一条规矩,这里逐跳复用)。
#    另给一个**相对赢家 capture**:分母 = **全部通道**在该跳存活的赢家 —— 回答"这一跳活
#    下来的赢家里,有多大一份是这条路的",与重叠矩阵一起读才知道那份是不是它独有的。
#
# 3. **winner 定义不另立一套**:仍是本模块顶上的 `WINNER_DEFINITION`(`MAIN_RULER` +
#    `entry_flag_for()` 现算)。六跳漏斗只换切片,不换尺。
#
# ## 数据源
#
# * 召回那一跳 = `L1_channels.csv`(唯一带"某路当日实际召回了哪些代码"的长表)。护照
#   **答不了这一跳**:它的行集合恒等于 L2 全集(`passport.build_passport` 断言①),
#   召回了却没进 L2 的票根本不在里面 —— 这是本 task 与任务书 Interfaces 的一处实测偏差。
# * L2/pass1/finalist/L4 四跳 = 候选护照(T19)。护照**现算**(`build_passport`),不读盘上
#   那份 `_candidate_passport.json`:同一函数的派生视图,现算才保证与当日真实产物同源
#   (与 `relative_buy.build_decision` 同一选择,理由见那边 docstring)。
# * E6 那一跳 = `_relative_buy_decision.json` 的 `buys[]`。**缺文件 → 该跳记 `—`
#   (status=ABSENT),不是 0**:「今天还没有这个产物」和「今天一只都没买」是两件事,
#   后者(文件在、`buys` 为空)才记 0。
# ═══════════════════════════════════════════════════════════════════════════

#: 六跳位置。名字即语义,渲染与 JSON 共用一套(不许两处各写一份词表)。
FUNNEL_HOPS = ("recall", "l2", "pass1", "finalist", "l4", "e6")
FUNNEL_HOP_LABELS = {
    "recall": "L1 召回",
    "l2": "L2 菜单",
    "pass1": "pass1 分诊留下",
    "finalist": "L3 finalist",
    "l4": "L4 出卡(qualified)",
    "e6": "E6 相对 BUY(影子)",
}
DECISION_FILENAME = "_relative_buy_decision.json"
OVERLAP_MIN_JACCARD = 0.30       # 重叠矩阵只点名高重叠对(阈值同 channel_audit)
#: 报警只挂在**前三跳**的条件 capture 上 —— 后三跳的分母是当日 6-10 只 finalist / 1 只 BUY,
#: 拿它定 P25 分位是在给噪声安警报器(「天天响 = 没有警报」的另一种死法)。
FUNNEL_ALARM_HOP = "l2"


def _ratio(numer: int, denom: int, *, as_of: str) -> dict:
    """每个比率同屏带**分子 / 分母 / as-of / ruler**(本仓库铁律)。分母 0 → 值 `None`。"""
    return {
        "value": round(numer / denom, 6) if denom else None,
        "numer": int(numer),
        "denom": int(denom),
        "as_of": as_of,
        "ruler": MAIN_RULER,
    }


def _channel_recall_sets(scan: Path) -> dict[str, set[str]] | None:
    """`L1_channels.csv` → {channel: {code6}};文件缺 / 无列 → `None`(presence-gated)。"""
    frame = _read(scan / "L1_channels.csv")
    if frame is None or not len(frame) or not {"channel", "code"}.issubset(frame.columns):
        return None
    out: dict[str, set[str]] = {}
    for channel, sub in frame.groupby("channel"):
        out[str(channel)] = _codes(sub)
    return out or None


def _e6_buys(scan: Path) -> tuple[set[str] | None, str]:
    """E6 影子决策 → (BUY 代码集, 状态)。缺文件/坏文件 → `(None, "ABSENT")`。"""
    path = scan / DECISION_FILENAME
    if not path.exists():
        return None, "ABSENT"
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, "ABSENT"
    buys = doc.get("buys")
    if not isinstance(buys, list):
        return None, "ABSENT"
    return {str(row.get("code") or "").split(".")[0].zfill(6)
            for row in buys if isinstance(row, dict) and row.get("code")}, "PRESENT"


def _stage_survivors(scan: Path) -> tuple[dict[str, set[str]], dict]:
    """护照 → 四跳存活集合 + 计数。护照**现算**,不读盘上那份(见本节 docstring)。"""
    from autoresearch.scan.passport import build_passport

    doc = build_passport(scan)
    entries = doc["candidates"].values()
    return {
        "l2": {e["code"] for e in entries},
        "pass1": {e["code"] for e in entries if e["pass1"]["kept"] is True},
        "finalist": {e["code"] for e in entries if e["l3"]["finalist"] is True},
        # L4-qualified = **真出了卡**(有评级),不是"派发过"。派了没回卡不算过这一跳
        # ——那正是 `l4_incomplete_n` 要盯的东西,混进来会让漏斗把失败当成功。
        "l4": {e["code"] for e in entries if e["l4"]["carded"]},
    }, doc["sources"]


def _hop_source_status(scan: Path, sources: dict, e6_status: str) -> dict[str, str]:
    """**每一跳各自**的源产物在场与否(修复轮 1,复核 C2)。

    首版把 ABSENT 纪律硬绑在 E6 一跳上(`absent = hop == "e6" and ...`),其余四跳一律
    `alive & survivors[hop]`。可是护照对"这一站的产物根本不存在"给的也是空集合
    (`pass1["kept"]` 全 `None` ⇒ `survivors["pass1"] = ∅`),于是**缺件被读成「全被切了」**,
    还级联把 finalist/l4/e6 一起清零。

    真数据实测(30 个可配对日):`pass1` **缺 15 天** —— 分界线正是 2026-07-10,two-pass
    分诊上线日,那 15 天这个阶段**压根不存在**。伪影因此被当成「pass1 是最陡的一跳」写进了
    T21 报告(已在报告里显式撤回)。

    `sources` 一直就在手上(`_stage_survivors` 早就返回它、`day_channel_funnel` 也早就把它
    写进输出),只是从没被用来 gate 任何一跳 —— 这正是「A 建的字段 B 没消费」的又一例。

    l4 那跳护照没有单独的 source 键(`read_final_ratings` 走 `decision_records.json` →
    `_final_ratings.json` 两级回退),故在这里按同一组文件的在场与否自己判。
    """
    return {
        "recall": "PRESENT",                       # 到这里必然在场(否则 day_channel_funnel 已返回 None)
        "l2": sources.get("l2", "ABSENT"),
        "pass1": sources.get("pass1", "ABSENT"),
        # finalist 跳的源 = `_l3_judged.json` ∪ `finalists.csv`,护照的 `l3_judged` 已合并两者
        "finalist": sources.get("l3_judged", "ABSENT"),
        "l4": ("PRESENT" if ((scan / "decision_records.json").exists()
                             or (scan / "_final_ratings.json").exists()) else "ABSENT"),
        "e6": e6_status,
    }


def day_channel_funnel(scan_dir: Path | str) -> dict | None:
    """单日 per-channel 六跳漏斗。缺 attribution / 缺 `L1_channels.csv` → `None`。"""
    scan = Path(scan_dir)
    attr = _read(scan / "retro" / "attribution.csv")
    if attr is None or not len(attr) or "code" not in attr.columns:
        return None
    recall_sets = _channel_recall_sets(scan)
    if recall_sets is None:
        return None

    date = scan.name
    winners_frame, definition = day_winners(attr)
    winners = _codes(winners_frame)

    survivors, sources = _stage_survivors(scan)
    buys, e6_status = _e6_buys(scan)
    survivors["e6"] = buys if buys is not None else set()
    hop_status = _hop_source_status(scan, sources, e6_status)

    # 各跳的**全通道**存活集合(相对赢家 capture 的分母)—— 只在被某路召回过的票里算,
    # 免得把 pinned 直注/backfill 补位这类"没走过通道召回"的票算进通道的分母。
    # ABSENT 的跳**原样穿过**(不与空集合相交),否则分母也跟着被清零(C2 同源)。
    recalled_all: set[str] = set().union(*recall_sets.values()) if recall_sets else set()
    all_alive: dict[str, set[str]] = {"recall": recalled_all}
    for i, hop in enumerate(FUNNEL_HOPS[1:], start=1):
        prev = all_alive[FUNNEL_HOPS[i - 1]]
        all_alive[hop] = prev if hop_status[hop] == "ABSENT" else prev & survivors[hop]

    channels: dict[str, dict] = {}
    for name in sorted(recall_sets):
        alive = recall_sets[name]
        prev_winners = len(winners & alive)
        hops = [{
            "hop": "recall",
            "label": FUNNEL_HOP_LABELS["recall"],
            "n": len(alive),
            "n_winners": prev_winners,
            "status": "PRESENT",
            # 召回是第一跳,没有"上一跳"——条件 capture 的分母取当日全部赢家(端到端起点)。
            "capture_cond": _ratio(prev_winners, len(winners), as_of=date),
            "capture_rel": _ratio(prev_winners, len(winners & all_alive["recall"]),
                                  as_of=date),
        }]
        for hop in FUNNEL_HOPS[1:]:
            # 修复轮 1(C2):ABSENT 判据泛化到**每一跳**,不再硬绑 e6。
            # 缺件那跳只记「量不到」(`—`),`alive` 原样穿过 —— 下游各跳因此仍以**最后
            # 一次真观测到**的存活集合为分母,不被级联清零。
            absent = hop_status[hop] == "ABSENT"
            nxt = alive & survivors[hop]
            here = len(winners & nxt)
            hops.append({
                "hop": hop,
                "label": FUNNEL_HOP_LABELS[hop],
                "n": None if absent else len(nxt),
                "n_winners": None if absent else here,
                "status": "ABSENT" if absent else "PRESENT",
                "capture_cond": (_ratio(0, 0, as_of=date) if absent
                                 else _ratio(here, prev_winners, as_of=date)),
                "capture_rel": (_ratio(0, 0, as_of=date) if absent
                                else _ratio(here, len(winners & all_alive[hop]), as_of=date)),
            })
            if not absent:
                alive, prev_winners = nxt, here
        channels[name] = {"channel": name, "hops": hops}

    return {
        "date": date,
        "ruler": MAIN_RULER,
        "winner_definition": definition,
        "n_winners": len(winners),
        "sources": {**sources, "l1_channels": "PRESENT", "e6": e6_status},
        # 逐跳源在场表 —— 与 `sources`(按**产物**列)不同,这张按**跳**列,是 ABSENT 纪律
        # 真正 gate 的那份;读表的人据此知道某一跳的 `—` 是"没这个产物"而不是"没赢家"。
        "hop_sources": hop_status,
        "channels": channels,
        "winner_overlap": _winner_overlap(recall_sets, winners, date),
    }


def _winner_overlap(recall_sets: dict[str, set[str]], winners: set[str],
                    date: str) -> list[dict]:
    """通道间**赢家**重叠矩阵(Jaccard,口径同 `channel_audit.jaccard_matrix` 但只数赢家)。

    重叠矩阵是相对赢家 capture 的必读伴侣:两路各占 60% 的赢家份额,可能是它们捞的是同
    一批票(冗余),也可能各捞各的(互补)—— 只看份额分不出这两种。
    """
    rows = []
    for a, b in combinations(sorted(recall_sets), 2):
        wa, wb = recall_sets[a] & winners, recall_sets[b] & winners
        union = len(wa | wb)
        rows.append({"channel_a": a, "channel_b": b, "common": len(wa & wb),
                     "union": union, "n_a": len(wa), "n_b": len(wb),
                     "jaccard": round(len(wa & wb) / union, 6) if union else 0.0,
                     "as_of": date, "ruler": MAIN_RULER})
    return rows


def _hop_of(day: dict, channel: str, hop: str) -> dict | None:
    entry = (day.get("channels") or {}).get(channel)
    if not entry:
        return None
    return next((h for h in entry["hops"] if h["hop"] == hop), None)


def channel_alarms(daily: list[dict], hop: str = FUNNEL_ALARM_HOP) -> list[dict]:
    """逐通道 × 逐日报警线 —— 当日**之前**的 expanding P25(全期分位含未来 → 回看永不报警)。

    每条通道**自己一条线**:各路的 capture 基线本来就不同(composite 覆盖面大、event 稀疏),
    合起来定一条线等于拿高覆盖路的分位去审稀疏路。历史不足 `MIN_HISTORY` → 不报警。
    """
    names = sorted({name for day in daily for name in (day.get("channels") or {})})
    out: list[dict] = []
    for name in names:
        series, dates = [], []
        for day in daily:
            got = _hop_of(day, name, hop)
            # 修复轮 1(复核 I1):**该路当日没跑 / 该跳缺件的日子直接剔除**,
            # 不再 `0.0 if value is None`。伪造 0 有两种失效,窗口内都真发生了:
            #   假阴 —— `accumulation` 只出现 14/30 天,16 个伪造的 0 把它自己的 expanding
            #           P25 线拉到 0.0 ⇒ `value < 0` 恒 False ⇒ 这条路的报警器永远不响;
            #   假阳 —— 一条平时 0.4–0.8 的路某天没跑 → 记 0.0、线在 0.4 → 报「跌破 P25」,
            #           可它那天压根没参赛。
            # 口径与 `build_channel_funnel` 的 `status != PRESENT → continue` 对齐:
            # 缺席不进历史、不判报警,而不是被当成一次「表现极差」的观测。
            if not got or got.get("status") != "PRESENT":
                continue
            value = (got.get("capture_cond") or {}).get("value")
            if value is None:            # 上一跳没有赢家 ⇒ 比率不存在,同样不是 0
                continue
            series.append(float(value))
            dates.append(day.get("date"))
        line = st.expanding_p25(series, min_history=MIN_HISTORY)
        for date, value, threshold in zip(dates, series, line, strict=True):
            out.append({"channel": name, "hop": hop, "date": date,
                        "capture_cond": value, "p25": threshold,
                        "alarm": None if threshold is None else bool(value < threshold),
                        "ruler": MAIN_RULER})
    return out


def collect_channel_funnel(scan_root: Path | str | None = None) -> list[dict]:
    root = Path(scan_root or DEFAULT_ROOT)
    if not root.exists():
        return []
    return [r for day in sorted(p for p in root.iterdir()
                                if p.is_dir() and p.name[:2] == "20")
            if (r := day_channel_funnel(day)) is not None]


def build_channel_funnel(scan_root: Path | str | None = None) -> dict:
    """跨日汇总:各路逐跳的**分子/分母累加**(不是逐日比率再平均)。

    为什么累加而不是日均:后三跳每天只有个位数样本,日均会让"某天恰好 1/1"和"某天 8/40"
    等权,读数被稀疏日主导。累加口径下每个比率仍然同屏带分子/分母,读的人自己能看出薄不薄。
    """
    daily = collect_channel_funnel(scan_root)
    names = sorted({name for day in daily for name in (day.get("channels") or {})})
    channels: dict[str, dict] = {}
    for name in names:
        hops = []
        for hop in FUNNEL_HOPS:
            numer_c = denom_c = numer_r = denom_r = n = n_days = 0
            for day in daily:
                got = _hop_of(day, name, hop)
                if not got or got.get("status") != "PRESENT":
                    continue
                n_days += 1
                n += int(got["n"] or 0)
                numer_c += got["capture_cond"]["numer"]
                denom_c += got["capture_cond"]["denom"]
                numer_r += got["capture_rel"]["numer"]
                denom_r += got["capture_rel"]["denom"]
            hops.append({
                "hop": hop, "label": FUNNEL_HOP_LABELS[hop], "n_days": n_days, "n": n,
                # `n_days == 0` = 窗口内**没有任何一天**这一跳的产物在场 ⇒ 该格是「缺件」,
                # 与「上一跳没有赢家」(n_days>0 但 denom==0)是两回事(复核 M2)。
                "status": "PRESENT" if n_days else "ABSENT",
                "capture_cond": _ratio(numer_c, denom_c, as_of=f"{len(daily)}日累计"),
                "capture_rel": _ratio(numer_r, denom_r, as_of=f"{len(daily)}日累计"),
            })
        channels[name] = {"channel": name, "hops": hops}

    overlap: dict[tuple[str, str], dict] = {}
    for day in daily:
        for row in day.get("winner_overlap") or []:
            key = (row["channel_a"], row["channel_b"])
            slot = overlap.setdefault(key, {"channel_a": key[0], "channel_b": key[1],
                                            "common": 0, "union": 0})
            slot["common"] += row["common"]
            slot["union"] += row["union"]
    for slot in overlap.values():
        slot["jaccard"] = (round(slot["common"] / slot["union"], 6)
                           if slot["union"] else 0.0)
        slot["ruler"] = MAIN_RULER

    return {
        "n_days": len(daily),
        "hops": list(FUNNEL_HOPS),
        "ruler": MAIN_RULER,
        "alarm_hop": FUNNEL_ALARM_HOP,
        "channels": channels,
        "winner_overlap": sorted(overlap.values(),
                                 key=lambda r: (-r["jaccard"], r["channel_a"])),
        "alarms": channel_alarms(daily),
        "daily": daily,
    }


def render_channel_funnel(payload: dict) -> list[str]:
    """逐通道六跳漏斗三节 markdown(比率一律 `值 (分子/分母)`,不许只印比率)。"""
    lines = [
        "", "## 逐通道六跳漏斗(per-channel end-to-end capture,T21)", "",
        f"> 六跳:{' → '.join(FUNNEL_HOP_LABELS[h] for h in FUNNEL_HOPS)};"
        f"尺 `{payload['ruler']}`;窗口 **{payload['n_days']}** 日。",
        "> **条件 capture** 分母 = 上一跳存活的赢家(不是端到端分母);"
        "**相对 capture** 分母 = 全部通道在该跳存活的赢家。比率一律写成 `值 (分子/分母)`。",
        "> E6 那一跳缺 `_relative_buy_decision.json` 的日子记 `—`(不是 0),不进累计分母。",
        "> ⚠️ **两条读表须知**:①`L1 召回` 跳是各路**提交**的名单(`L1_channels.csv`,"
        "`quota_union` 裁到 `recall_n` **之前**),所以 `召回→L2` 这一跳同时含 union 裁剪与 L2 "
        "分层采样两道损耗,与上面三层曲线的 `wc_l1_all`(裁剪**后**的 `L1_recall_top1000.csv`)"
        "不是同一个量;②累计分母来自**各路各自出现过的日集合**(退役路如 `accumulation`/"
        "`northbound` 只在早期日出现),故横向比分母大小无意义 —— 比率要连 `日数` 一起读。",
        "",
    ]
    if not payload["n_days"] or not payload["channels"]:
        return lines + ["_窗口内无 `L1_channels.csv` × `retro/attribution.csv` 可配对数据_", ""]

    def cell(hop: dict, key: str) -> str:
        """`—(缺件)` = 这一跳的产物窗口内一天都不在场;`— (0/0)` = 在场但上一跳没有赢家。

        两者在 JSON 里靠 `status` 区分,展示层此前一律印 `— (0/0)`,读表的人分不开
        「没这个产物」和「没赢家活到这」(复核 M2)。
        """
        if hop.get("status") == "ABSENT":
            return "—(缺件)"
        ratio = hop[key]
        if ratio["denom"] == 0:
            return "— (0/0)"
        return f"{ratio['value']:.3f} ({ratio['numer']}/{ratio['denom']})"

    header = ("| 路 | 日数 | " + " | ".join(FUNNEL_HOP_LABELS[h] for h in FUNNEL_HOPS) + " |",
              "|---|---:|" + "---|" * len(FUNNEL_HOPS))
    lines += ["### ① 条件 capture(逐跳存活的赢家 / 上一跳存活的赢家)", "", *header]
    for name in sorted(payload["channels"]):
        hops = payload["channels"][name]["hops"]
        lines.append(f"| `{name}` | {hops[0]['n_days']} | "
                     + " | ".join(cell(h, "capture_cond") for h in hops) + " |")

    lines += ["", "### ② 相对赢家 capture(本路 / 全通道在该跳存活的赢家)+ 存活票数(累计)",
              "", *header]
    for name in sorted(payload["channels"]):
        hops = payload["channels"][name]["hops"]
        lines.append(f"| `{name}` | {hops[0]['n_days']} | "
                     + " | ".join(f"{cell(h, 'capture_rel')} · n={h['n']}" for h in hops) + " |")

    lines += ["", f"### ③ 通道间**赢家**重叠矩阵(Jaccard ≥ {OVERLAP_MIN_JACCARD} 才点名)", ""]
    hot = [r for r in payload.get("winner_overlap") or []
           if r["jaccard"] >= OVERLAP_MIN_JACCARD]
    if not hot:
        lines.append(f"- 无 Jaccard ≥ {OVERLAP_MIN_JACCARD} 的通道对 —— 各路捞的赢家基本不重合")
    else:
        lines += ["| 路A | 路B | 共同赢家 | 并集 | Jaccard |", "|---|---|---:|---:|---:|"]
        lines += [f"| {r['channel_a']} | {r['channel_b']} | {r['common']} | "
                  f"{r['union']} | {r['jaccard']:.2f} |" for r in hot]

    fired = [r for r in payload.get("alarms") or [] if r.get("alarm")]
    lines += ["", f"### ④ 报警(`{payload['alarm_hop']}` 跳条件 capture,当日**之前**的 "
                  f"expanding P25;历史 < {MIN_HISTORY} 日不报警)", ""]
    if not fired:
        lines.append("- 窗口内无通道跌破自己的 P25 线(或历史尚不足以定线)")
    else:
        lines += ["| 日期 | 路 | 条件 capture | P25 线 |", "|---|---|---:|---:|"]
        lines += [f"| {r['date']} | {r['channel']} | {r['capture_cond']:.3f} | "
                  f"{r['p25']:.3f} |" for r in fired]
    return lines + [""]


ALARM_METRICS = ("wc_l2_all", "wc_l2_given_l1", "wc_l1_given_l0")


def alarms(daily: pd.DataFrame) -> pd.DataFrame:
    """逐日报警线 —— 当日**之前**的 expanding P25(全期分位含未来,回看永不报警)。"""
    if not len(daily):
        return pd.DataFrame()
    out = daily[["date"]].copy()
    for metric in ALARM_METRICS:
        if metric not in daily.columns:
            continue
        series = daily[metric].tolist()
        line = st.expanding_p25(
            [0.0 if v is None or pd.isna(v) else float(v) for v in series],
            min_history=MIN_HISTORY)
        out[f"{metric}_p25"] = line
        out[f"{metric}_alarm"] = [
            (None if threshold is None or v is None or pd.isna(v)
             else bool(float(v) < threshold))
            for v, threshold in zip(series, line, strict=True)]
    return out


def build(scan_root: Path | str | None = None) -> dict:
    daily = collect(scan_root)
    alarm = alarms(daily)
    summary: dict = {
        "schema_version": SCHEMA_VERSION,
        "winner_definition": WINNER_DEFINITION,
        "n_days": int(len(daily)),
        "min_history_for_alarm": MIN_HISTORY,
        "not_the_only_metric": (
            "winner capture 是主 SLO,不是唯一指标 —— 扩大 K 或集中追热点都能被动做高它;"
            "必须与 guards(行业集中度 / lane 覆盖 / selection_reason 分布)一起读"),
    }
    for metric in ("wc_l1_all", "wc_l2_all", "wc_l2_all_ex_pinned",
                   "wc_l1_given_l0", "wc_l2_given_l1"):
        if metric not in daily.columns or not len(daily):
            summary[metric] = None
            continue
        interval = st.date_cluster_bootstrap(daily, metric, date_col="date")
        summary[metric] = interval.as_dict()
    summary["daily"] = daily.to_dict("records") if len(daily) else []
    summary["alarms"] = alarm.to_dict("records") if len(alarm) else []
    summary["maturity"] = st.maturity_verdict(
        scan_days=int(len(daily)), subgroup_n=None, unique_n=None,
        regimes=None).as_dict()
    # T21:逐通道六跳漏斗。它比三层曲线多读 `L1_channels.csv` + 护照 + E6 决策文件,任一
    # 缺席都只让**它自己**降级(`n_days=0`),不牵连上面已经算好的三层曲线。
    summary["channel_funnel"] = build_channel_funnel(scan_root)
    return summary


def render(payload: dict) -> str:
    lines = [
        "# L2 winner-capture SLO(菜单质量,不承诺 alpha)",
        "",
        f"> **winner 定义**:{payload['winner_definition']}",
        "",
        f"- 成熟扫描日 **{payload['n_days']}** · 成熟度 "
        f"{payload['maturity']['status']}"
        + ("" if not payload["maturity"]["missing"]
           else "(缺:" + "、".join(payload["maturity"]["missing"]) + ")"),
        "",
        "## 三层曲线(端到端 vs 条件召回)",
        "",
        "| 指标 | 含义 | 均值 | 区间 | 聚簇 |",
        "|---|---|---:|---|---:|",
    ]
    labels = {
        "wc_l1_all": "端到端:L1 召回捞到的赢家占全部赢家",
        "wc_l2_all": "端到端:L2 菜单捞到的赢家占全部赢家",
        "wc_l2_all_ex_pinned": "同上,剔除 pinned(保送不算 L2 的功劳)",
        "wc_l1_given_l0": "条件:L0 池内赢家里 L1 召回捞到的",
        "wc_l2_given_l1": "条件:L1 召回内赢家里 L2 留下的",
    }
    for metric, label in labels.items():
        iv = payload.get(metric) or {}
        point = "—" if iv.get("point") is None else f"{iv['point']:.4f}"
        band = ("—" if iv.get("lo") is None
                else f"[{iv['lo']:.4f}, {iv['hi']:.4f}]")
        lines.append(f"| `{metric}` | {label} | {point} | {band} "
                     f"| {iv.get('n_clusters', 0)} |")
    lines += ["", f"> ⚠️ {payload['not_the_only_metric']}", "",
              "## 逐日(K 为实际值,不写死)", "",
              "| 日期 | 赢家 | K(L0/L1/L2) | L2 剔保送 | wc_l2_all | "
              "wc_l2_given_l1 | 报警线 | 报警 | 首行业占比 | lane 数 |",
              "|---|---:|---|---:|---:|---:|---:|---|---:|---:|"]
    alarm_by_date = {row["date"]: row for row in payload.get("alarms", [])}
    for row in payload.get("daily", []):
        alarm = alarm_by_date.get(row["date"], {})
        threshold = alarm.get("wc_l2_all_p25")
        fired = alarm.get("wc_l2_all_alarm")
        guards = row.get("guards") or {}
        share = guards.get("top_industry_share")
        lines.append(
            f"| {row['date']} | {row['n_winners']} | "
            f"{row['k_l0']}/{row['k_l1']}/{row['k_l2']} | {row['k_l2_ex_pinned']} | "
            + ("—" if row["wc_l2_all"] is None else f"{row['wc_l2_all']:.3f}") + " | "
            + ("—" if row["wc_l2_given_l1"] is None else f"{row['wc_l2_given_l1']:.3f}")
            + " | " + ("—" if threshold is None else f"{threshold:.3f}")
            + " | " + ("—" if fired is None else ("🚨" if fired else "·"))
            + " | " + ("—" if share is None else f"{share:.2f}")
            + f" | {guards.get('n_lanes', '—')} |")
    if not payload.get("daily"):
        lines.append("| — | — | — | — | — | — | — | — | — | — |")
    lines += render_channel_funnel(payload.get("channel_funnel")
                                   or {"n_days": 0, "channels": {}, "ruler": MAIN_RULER,
                                       "alarm_hop": FUNNEL_ALARM_HOP})
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="L2 winner-capture SLO(§3.1 O1)")
    ap.add_argument("--scan-root", default=None)
    ap.add_argument("--json-out", default=str(OUT_JSON))
    ap.add_argument("--md-out", default=str(OUT_MD))
    a = ap.parse_args(argv)

    payload = build(a.scan_root)
    for path, text in ((Path(a.json_out),
                        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n"),
                       (Path(a.md_out), render(payload))):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    fired = sum(1 for row in payload.get("alarms", []) if row.get("wc_l2_all_alarm"))
    print(f"[l2_slo] {payload['n_days']} 日 · 成熟度 {payload['maturity']['status']} "
          f"· 报警 {fired} 日 → {a.json_out} / {a.md_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
