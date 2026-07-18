#!/usr/bin/env python3
"""scan-market · L3 精排的确定性 helper(紧凑表喂料 / 增量真证据 / finalists 合并)。

design: docs/specs/2026-06-22-autoresearch-arch-redesign-design.md §A/§D;Plan 4.1。

零 LLM。L3 holistic 选股(1 agent 通看 ~200 比较着选 30)由 skill 编排 subagent(见
screening-playbook.md);本模块只做**确定性喂料 + 取数 + 格式化**:把 ~200 只压成一张紧凑表、
对保留集补 L1 没有的真证据(龙虎榜/预告/快报)、把 holistic 入选排成 finalists(带趋势配额安全网)。
产物 staging 到 context/scan/<date>/。selftest 已迁 pytest(tests/scan/test_agents.py)。
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

import pandas as pd

# L3 holistic 选股 subagent 要看的紧凑列(GBDT 复合分/重排分 + 9 子分〔含 volprice 多日量价〕+ 关键原始
# 因子;量价位置/多日资金流/筹码/估值都在,够它一次通看 ~200 只比较着选 30)。
# 2026-07-06 瘦身:L3 max-effort 需更小输入才净提速 → 删 9 个 score_* 子分(composite 已含、与原始因子冗余)
# + retail_net_yi/chip_concentration/price_to_cost/hk_ratio(常 NaN)/dv_ratio/l2_lane_reserved/news_n·tags。
# 保留 = 5 维 rubric 真正读的原始因子 + composite/gbdt + 情感 sent/head(42→22 列)。旧宽表见 git 历史。
# 2026-07-11:加 `pf`(行语义指纹,见 row_profile)——07-08 诊断误读 22/31 纯表内可见,读定性词
# 比读裸浮点更不容易漏读/误读;确定性纯函数,l3_table_md 组表时对每行算,恒出现(非 flag 位)。
_L3_COLS = ["code", "name", "pf", "industry", "composite", "gbdt_score",
            "pct_60d", "sector_mom", "vol_ratio", "cmf_20", "obv_mom_20", "main_net_ratio", "winner_rate",
            "rsi6", "pe", "pb", "np_yoy", "roe",
            "n_channels", "recall_channels",                   # 召回 provenance(channel 共振)
            "news_sent", "news_head"]                          # 情感(净分 + 头条;counts/tags 已删;med_* 随 webnews 死链 2026-07-13 摘除)


# ───────────────────────── L3:紧凑表 + 增量真证据 + finalists 合并 ─────────────────────────


def _fmt(v) -> str:
    if isinstance(v, float):
        return (f"{v:.2f}".rstrip("0").rstrip(".")) if v == v else "—"
    return str(v)


def row_profile(r) -> str:
    """行语义指纹(确定性纯函数,`pf` 列):把该票关键因子压成一句 `·` 连接的定性短语——
    L3 通看 ~200 行×22 列裸浮点易误读(07-08 诊断 22/31 证据纯表内可见,读词比读裸浮点更
    不容易漏读)。`r` 支持 dict / `pd.Series`(`.get` 语义);字段缺失/NaN → 该维度不出现
    (不冤枉、不编造)。同输入同输出,禁 wall-clock/随机。词表固定(顺序即优先级):

    位置(pct_60d):高位≥40 / 中位≥10 / 低位>−10 / 深跌(其余,恒出现)。
    放量(vol_ratio≥2,仅达标才出现)。
    主力(main_net_ratio 与 cmf_20/obv_mom_20 同向判,main 有值即恒出现):main>0 且资金指标
      未反向 → 主力+;main<0 且资金指标未反向 → 主力−;main≈0 → 主力平;主力方向与
      cmf_20/obv_mom_20 明确相反 → 主力背离。
    估值(pe):PE负<0 / PE低<20 / PE中<60 / PE高(其余,恒出现)。
    筹码(winner_rate,仅两端极值出现):满盈利⚠≥90 / 深套牢<25。
    超买超卖(rsi6,仅两端极值出现):超买≥80 / 超卖≤20。
    """
    def _f(key):
        v = r.get(key) if hasattr(r, "get") else None
        try:
            v = float(v)
        except (TypeError, ValueError):
            return None
        return None if v != v else v          # NaN 自比不等

    words: list[str] = []

    pct60 = _f("pct_60d")
    if pct60 is not None:
        if pct60 >= 40:
            words.append("高位")
        elif pct60 >= 10:
            words.append("中位")
        elif pct60 > -10:
            words.append("低位")
        else:
            words.append("深跌")

    vol = _f("vol_ratio")
    if vol is not None and vol >= 2:
        words.append("放量")

    main, cmf, obv = _f("main_net_ratio"), _f("cmf_20"), _f("obv_mom_20")
    if main is not None:
        flow = [x for x in (cmf, obv) if x is not None]
        flow_pos = any(x > 0 for x in flow)
        flow_neg = any(x < 0 for x in flow)
        if main > 0:
            words.append("主力背离" if (flow_neg and not flow_pos) else "主力+")
        elif main < 0:
            words.append("主力背离" if (flow_pos and not flow_neg) else "主力−")
        else:
            words.append("主力平")

    pe = _f("pe")
    if pe is not None:
        if pe < 0:
            words.append("PE负")
        elif pe < 20:
            words.append("PE低")
        elif pe < 60:
            words.append("PE中")
        else:
            words.append("PE高")

    winner = _f("winner_rate")
    if winner is not None:
        if winner >= 90:
            words.append("满盈利⚠")
        elif winner < 25:
            words.append("深套牢")

    rsi = _f("rsi6")
    if rsi is not None:
        if rsi >= 80:
            words.append("超买")
        elif rsi <= 20:
            words.append("超卖")

    return "·".join(words)


def compact_table(df: pd.DataFrame, cols: list[str] | None = None) -> str:
    """子集 → markdown 紧凑表(喂 L3 holistic 选股 subagent;一行一只,~200 只一次通看、比较着选)。"""
    cols = [c for c in (cols or _L3_COLS) if c in df.columns]
    head = "| " + " | ".join(cols) + " |"
    sep = "|" + "|".join(["---"] * len(cols)) + "|"
    lines = [head, sep]
    for _, r in df[cols].iterrows():
        lines.append("| " + " | ".join(_fmt(r[c]) for c in cols) + " |")
    return "\n".join(lines)


def load_l3_input(date: str, root: Path | None = None) -> pd.DataFrame:
    """读 L2 粗排产物(L2_gbdt_top200.csv)+ 合并已 harvest 的 L3 增量证据摘要 → L3 选股输入帧。

    证据摘要列(表内一眼可见,不必逐 json 翻):lhb_n(龙虎榜上榜条数)、has_forecast/has_express
    (预告/快报有无)。证据未 harvest → 三列缺省 0/False。
    """
    import json
    root = root or Path("context/scan")
    df = pd.read_csv(root / date / "L2_gbdt_top200.csv", dtype={"code": str})
    df["code"] = df["code"].astype(str).str.zfill(6)
    ev_dir = root / date / "L3_evidence"
    if ev_dir.exists():
        rows = []
        for c in df["code"]:
            fp = ev_dir / f"{c}.json"
            if fp.exists():
                ev = json.loads(fp.read_text(encoding="utf-8"))
                rows.append({"code": c, "lhb_n": len(ev.get("longhu", [])),
                             "has_forecast": bool(ev.get("forecast")), "has_express": bool(ev.get("express"))})
            else:
                rows.append({"code": c, "lhb_n": 0, "has_forecast": False, "has_express": False})
        df = df.merge(pd.DataFrame(rows), on="code", how="left")
    # Phase 3:并入公告情感 digest(L3_news/<code>.json,缺则缺省 0/""/—)。
    from autoresearch.scan.agents.l3_news import news_digest
    news_dir = root / date / "L3_news"
    drows = []
    for c in df["code"]:
        fp = news_dir / f"{c}.json"
        anns = json.loads(fp.read_text(encoding="utf-8")) if fp.exists() else []
        drows.append({"code": c, **news_digest(anns)})
    df = df.merge(pd.DataFrame(drows), on="code", how="left")
    return df


def triage_l2_for_l3(df: pd.DataFrame, target: int = 60) -> tuple[pd.DataFrame, pd.DataFrame]:
    """pass1 确定性分诊(零 LLM):L2 ~200 行 → kept(进 pass2/l3-rank 深比较,~target 行)+
    cut(影子,写 `_l3_pass1_cut.csv`,供 attribution 证明分诊没吃掉赢家)。design: plan
    2026-07-12-l3-merge-plan.md Task 1。

    `df` 是 `load_l3_input(date)` 的输出(L2_gbdt_top200.csv + 证据/情感 merge 后的帧),
    **不是** `merge_l3_finalists_v2` 消费的 L3 judged 输出——后者才有 `lane` 列(L3 agent
    判断后才产生的字段),L2 输入帧本身没有 `lane`,故下面①③的判据全部改用 L2 真实
    provenance 列(`recall_channels`/`n_channels`,均在真实 `L2_gbdt_top200.csv` 里实测
    存在;`lenses` 是 L3 judged 输出列,L2 输入没有,不适用,略过)。

    kept 规则(按序 union、天然去重;结果按 `gbdt_score` 降序——缺列退化 `composite`——
    决定谁留谁走,超 target 截尾,截掉的并入 cut):

    ① pinned 全入:`pinned` 布尔列(`universe._inject_pinned_l1` 全程带下来,presence-gated,
       多数无保送的日子该列不存在)为真;该列不存在时退化检查 `recall_channels` 字面等于
       `"pinned"`(L1 强注新增行的哨兵值,见 `_inject_pinned_l1`)。两者都缺列 → 该规则
       贡献 0 行,不报错。
    ② 多路共振全入:`n_channels >= 3`(真实列,直接可用)。列缺失(如 `recall_mode="composite"`
       的 L2,无 provenance 列)→ 跳过本规则,不报错。
    ③ healthy lane 全入:`recall_channels` 按 `"|"` 拆分后的集合包含 `"healthy"`(已注册召回
       通道名,见 `recall/channels.py`;集合 membership 判定——镜像 `l2_stratify._style_masks`
       的写法,**不是** `_row_lane` 的"仅取首通道"渲染判据,那是防重复渲染用的、语义不同)。
       `recall_channels` 列缺失 → 跳过,不报错。
    ④ 剩余名额(target 减①②③后的 kept 数,若已 ≥target 则本规则不运行)按各召回通道内部
       名次(`gbdt_score` 降序,缺列退化 `composite`)轮询填满:每轮依次(通道名升序,确定性)
       从每个通道取队列里名次最高且未被选中的一行,直至填满 target 或全部通道队列耗尽——
       "K 自适应":不是固定分配每通道几个名额,是排到没有再转下一个通道。通道队列只统计
       **真实召回通道名**(`recall_channels` 按 `"|"` 拆分后排除 `""`/`"(backfill)"`/`"pinned"`
       三个哨兵 token,那两个不是通道名);轮询耗尽仍未填满 target 且还有剩余行(如
       `(backfill)` 补位票、或整列缺失 `recall_channels`)→ 末尾直接按 `gbdt_score`/`composite`
       分再补满,直至 target 或行数耗尽——这是"填满到 target"字面要求的延伸,不是独立的
       第 5 条规则。

    `cut = df − kept`(按原始行序稳定输出,不重排;`kept`/`cut` 都保留 `df` 的**全部原始列**,
    不裁列——`_l3_pass1_cut.csv`/下游 attribution 都可能要用到里面的列)。

    边界:`df` 为空 → 两个都空。`target >= len(df)` → kept=全量,cut=空。
    """
    if df.empty:
        return df.copy(), df.copy()

    d = df.reset_index(drop=True).copy()
    if "code" in d.columns:
        d["code"] = d["code"].astype(str).str.zfill(6)

    score_col = "gbdt_score" if "gbdt_score" in d.columns else ("composite" if "composite" in d.columns else None)
    order = (pd.to_numeric(d[score_col], errors="coerce").fillna(-1e18)
            if score_col else pd.Series(0.0, index=d.index))

    is_pinned = pd.Series(False, index=d.index)                          # ① pinned 全入
    if "pinned" in d.columns:
        is_pinned |= d["pinned"].map(lambda v: bool(v) if pd.notna(v) else False)
    elif "recall_channels" in d.columns:
        is_pinned |= d["recall_channels"].astype(str) == "pinned"
    mandatory = is_pinned.copy()

    if "n_channels" in d.columns:                                        # ② 多路共振全入
        mandatory |= pd.to_numeric(d["n_channels"], errors="coerce").fillna(0) >= 3

    chan_sets = None
    if "recall_channels" in d.columns:                                   # ③ healthy lane 全入
        chan_sets = d["recall_channels"].fillna("").astype(str).map(lambda s: set(s.split("|")) - {""})
        mandatory |= chan_sets.map(lambda s: "healthy" in s)

    mandatory_idx = list(d.index[mandatory])
    if len(mandatory_idx) > target:
        # M-2 修复(final-review-l3-merge.md):截尾只对**非 pinned**行进行——pinned 恒
        # kept(design 2026-07-11-recall-gate-pinned-config-design.md §4.1"L1→L5 全程
        # 强留"),不因 mandatory(pinned∪共振∪healthy)超 target 被误切进
        # `_l3_pass1_cut.csv`、丢失 L3 真判机会("L3 真判但不可淘汰"失守)。pinned 行数
        # 本身就超过 target 的极端情形(理论上用户 pinned 名单很小,不会发生)→ 全部保留,
        # kept 允许略超 target(强留优先级高于 target 硬性配额)。
        pinned_idx = [i for i in mandatory_idx if is_pinned.loc[i]]
        other_idx = [i for i in mandatory_idx if not is_pinned.loc[i]]
        ranked = sorted(other_idx, key=lambda i: order.loc[i], reverse=True)
        remaining_target = max(0, target - len(pinned_idx))
        kept_set: set[int] = set(pinned_idx) | set(ranked[:remaining_target])
    else:
        kept_set = set(mandatory_idx)

    remaining = target - len(kept_set)
    if remaining > 0 and chan_sets is not None:                          # ④ 每通道内名次轮询填满
        queues: dict[str, list[int]] = {}
        for i in d.index:
            if i in kept_set:
                continue
            for c in (chan_sets.loc[i] - {"(backfill)", "pinned"}):
                queues.setdefault(c, []).append(i)
        for c in queues:
            queues[c].sort(key=lambda i: order.loc[i], reverse=True)
        ptrs = dict.fromkeys(queues, 0)
        chan_names = sorted(queues)
        progressed = True
        while remaining > 0 and progressed:
            progressed = False
            for c in chan_names:
                if remaining <= 0:
                    break
                q, p = queues[c], ptrs[c]
                while p < len(q) and q[p] in kept_set:
                    p += 1
                if p < len(q):
                    kept_set.add(q[p])
                    remaining -= 1
                    progressed = True
                    p += 1
                ptrs[c] = p
    if remaining > 0:                                                     # 填满收尾(无通道/耗尽的剩余票)
        leftover = [i for i in d.index if i not in kept_set]
        leftover.sort(key=lambda i: order.loc[i], reverse=True)
        kept_set.update(leftover[:remaining])

    kept_idx = sorted(kept_set)
    cut_idx = [i for i in d.index if i not in kept_set]
    kept = d.loc[kept_idx].reset_index(drop=True)
    cut = d.loc[cut_idx].reset_index(drop=True)
    return kept, cut


def _prev_l3_day(date: str, root: Path | None = None) -> Path | None:
    """最近一个有 L3 现场(L3_judged_full + L2)的更早 scan 日;无 → None。"""
    root = root or Path("context/scan")
    if not root.exists():
        return None
    cands = sorted((p for p in root.iterdir()
                    if p.is_dir() and p.name[:2] == "20" and p.name < date
                    and (p / "L3_judged_full.csv").exists()
                    and (p / "L2_gbdt_top200.csv").exists()), reverse=True)
    return cands[0] if cands else None


def _delta_filter(df: pd.DataFrame, prev_dir: Path,
                  comp_tol: float = 2.0, mom_tol: float = 2.0) -> tuple[pd.DataFrame, list[str]]:
    """Δ模式过滤:略去「昨判弃 ∧ 今无变化」的行,保留行加 prev_l3 标记(选/弃)。

    变化 = |Δcomposite|>comp_tol ∨ |Δpct_60d|>mom_tol ∨ 今日有 lhb/预告/快报证据;
    prev 缺值 = 视为变(保守保留)。返回 (过滤后帧, 被略去 codes)。
    """
    jd = pd.read_csv(prev_dir / "L3_judged_full.csv", dtype={"code": str})
    judged = set(jd["code"].astype(str).str.zfill(6)) if "code" in jd.columns else set()
    fp = prev_dir / "finalists.csv"
    fin: set[str] = set()
    if fp.exists():
        fd = pd.read_csv(fp, dtype={"code": str})
        if "code" in fd.columns:
            fin = set(fd["code"].astype(str).str.zfill(6))
    rejected = judged - fin
    prev_l2 = pd.read_csv(prev_dir / "L2_gbdt_top200.csv", dtype={"code": str})
    prev_l2["code"] = prev_l2["code"].astype(str).str.zfill(6)
    keep_prev = [c for c in ("code", "composite", "pct_60d") if c in prev_l2.columns]
    m = df.merge(prev_l2[keep_prev], on="code", how="left", suffixes=("", "_prev"))
    dcomp = (pd.to_numeric(m.get("composite"), errors="coerce")
             - pd.to_numeric(m.get("composite_prev"), errors="coerce")).abs()
    dmom = (pd.to_numeric(m.get("pct_60d"), errors="coerce")
            - pd.to_numeric(m.get("pct_60d_prev"), errors="coerce")).abs()
    changed = dcomp.isna() | (dcomp > comp_tol) | dmom.isna() | (dmom > mom_tol)
    for c, is_bool in (("lhb_n", False), ("has_forecast", True), ("has_express", True)):
        if c in m.columns:
            v = m[c].fillna(False).astype(bool) if is_bool else pd.to_numeric(m[c], errors="coerce").fillna(0) > 0
            changed |= v
    is_rejected = m["code"].isin(rejected)
    drop = is_rejected & ~changed
    dropped = m.loc[drop, "code"].tolist()
    kept = df[~df["code"].isin(set(dropped))].copy()
    kept["prev_l3"] = kept["code"].map(
        lambda c: "选" if c in fin else ("弃" if c in rejected else ""))
    return kept, dropped


def _row_lane(row) -> str:
    """lane 分块渲染的分块键(确定性):`l2_lane_reserved` 非空真值(floor 救回——桶配额
    从线下补进来的,非有机进场)→ `"floor"`;否则取 `recall_channels`(如 `"momentum|value"`)
    首通道;两者皆缺/空 →`"other"`。缺列/坏值容错返回 `"other"`(不抛)。"""
    reserved = row.get("l2_lane_reserved") if hasattr(row, "get") else None
    is_reserved = False
    if isinstance(reserved, str):
        is_reserved = reserved.strip().lower() not in ("", "false", "nan", "0")
    elif reserved is not None:
        try:
            is_reserved = bool(reserved) and reserved == reserved   # NaN 自比不等
        except (TypeError, ValueError):
            is_reserved = False
    if is_reserved:
        return "floor"
    ch = row.get("recall_channels") if hasattr(row, "get") else None
    ch = "" if ch is None else str(ch)
    if not ch or ch.lower() == "nan" or ch == "(backfill)":
        return "other"
    return ch.split("|")[0].strip() or "other"


def _render_lane_blocks(df: pd.DataFrame, cols: list[str]) -> str:
    """按 lane 分块渲染表体(去 L3 通看单一大表时的位置偏差):每块一个 `### lane:<名>`
    小标题,块内按 composite 降序;块序 = 有机召回通道按名升序在前、`floor`(配额救回,非
    有机进场)殿后——同一票只出现在一个块。尾附 `_meta: render_order=lane_blocks_` 供
    下游识别渲染模式。"""
    d = df.copy()
    d["_lane"] = d.apply(_row_lane, axis=1)
    comp = pd.to_numeric(d["composite"], errors="coerce") if "composite" in d.columns else pd.Series(0.0, index=d.index)
    d["_sort"] = comp.fillna(-1e18)
    lanes = sorted(d["_lane"].unique(), key=lambda x: (x == "floor", x))
    parts: list[str] = []
    for lane in lanes:
        sub = d[d["_lane"] == lane].sort_values("_sort", ascending=False)
        parts.append(f"### lane:{lane}")
        parts.append(compact_table(sub, cols=cols))
    parts.append("_meta: render_order=lane_blocks_")
    return "\n\n".join(parts)


def l3_table_md(date: str, root: Path | None = None, delta: bool = False,
                shuffle_seed: int | None = None, sector_terrain: bool = False,
                dist_flag: bool = False, reg_flag: bool = False, cat_flag: bool = False,
                misread_flag: bool = False, rc_flag: bool = False,
                pinned_flag: bool = False, pinned_path: Path | str | None = None,
                lane_blocks: bool = False, restrict_codes=None) -> str:
    """L3 holistic 选股 subagent 的完整输入表(~200 行紧凑表 + 证据摘要列)。

    delta=True:略去「昨判弃 ∧ 今无变化」行 + prev_l3 标记(design: l4-economy §3;
    默认 False = 逐字 parity;无前日 L3 现场 → 自动回退全量)。
    shuffle_seed:确定性乱序行序(稳定性抽检用;同 seed 同输出)。
    sector_terrain=True:前置**全行业确定性地形段**(申万一级对称覆盖,防"有 brief 行业被高看";
    design: 2026-07-03 海拔重构 §5.5;默认 False = 逐字 parity,缺 staging 自动跳过)。
    dist_flag=True:加 `main_inflow_yi`(绝对净额,亿)+ `main_dist`(反号/微量,谓词
    = `scoring.main_net_distortion_label` 单一事实源)两列 + 图例禁则——07-03 病灶:L3 把
    失真占比读成"真主力"打 conv 60-82,L4 再花 ~15 卡逐一辟谣(默认 False = 逐字 parity)。
    reg_flag=True:加 `news_reg` 列(近 10 日公告标题命中监管事项词,`l3_news.reg_hits`
    独立检测器——**不动 _EVENT_TAGS/news_digest,情感列口径不变**)+ 图例禁则
    (默认 False = 逐字 parity)。spec 2026-07-05 §5.3。
    cat_flag=True:加 `cat` 列(近 10 日 回购/增持/调研/减持 事件计数徽标,staging
    `L3_catalyst.csv` 在才生效)+ 图例禁则——事件存在性≠方向确认(默认 False = 逐字 parity)。
    spec 2026-07-05 wave §B2。
    misread_flag=True:加 misread 预警列(低基/背离/套牢,谓词=scoring.l3_misread_flags
    单一事实源)+图例禁则;默认 False = 逐字 parity。
    rc_flag=True:加 `rc` 列(卖方一致预期 FY EPS 近窗修正 %,staging `consensus.csv`——
    `l4_card.fetch_consensus` 产出——在才生效)+ 图例禁则,镜像 cat_flag 接线;
    默认 False = 逐字 parity。
    pinned_flag=True:加 `pinned` 列(📌 标记该码;来自 `user_config.load_pinned(date,
    path=pinned_path)` 的 kept 集合,presence-gated——无 pinned.json / kept 全空(含全
    过期)→ 列不出现)+ 图例禁则——保送票 L1→L5 全程强留、L3 真判但不可淘汰(design
    2026-07-11-recall-gate-pinned-config-design.md §4.1);默认 False = 逐字 parity。
    pinned_path:`load_pinned` 的自定义路径(测试注入;生产默认
    `.claude/skills/scan-market/pinned.json`)。
    lane_blocks=True:表渲染从单一大表改为按 lane 分块(`_render_lane_blocks`——`### lane:<名>`
    小标题;`l2_lane_reserved` 真值行归 `floor` 块,其余行归 `recall_channels` 首通道块;
    块内按 composite 降序;块序=有机通道在前、`floor` 殿后)去位置偏差(07-08 诊断 22/31
    误读之一);默认 False = 逐字 parity。
    restrict_codes=None(默认,parity):非 None → 先按这些码(6 位,容忍未 zfill)过滤
    `load_l3_input` 的输出,再进入其余渲染(pf/dist/reg/cat/misread/pinned/delta/lane_blocks
    全部在过滤后的子集上跑,各自逻辑不变)——`prepare_l3_table` 的 pass1 分诊
    (`two_pass=True`,design: plan 2026-07-12-l3-merge-plan.md Task 1)用此参数把 ~200 行表
    收窄到 pass1 `kept` 的 ~60 行喂 l3-rank agent;`None` = 不过滤,现行为不变。
    """
    df = load_l3_input(date, root=root)
    if restrict_codes is not None:
        keep_codes = {str(c).zfill(6) for c in restrict_codes}
        df = df[df["code"].astype(str).str.zfill(6).isin(keep_codes)].reset_index(drop=True)
    df["pf"] = df.apply(row_profile, axis=1)   # 行语义指纹(确定性,恒计算——非 flag 位,见 row_profile)
    cols = [*_L3_COLS] + [c for c in ("lhb_n", "has_forecast", "has_express") if c in df.columns]
    header: list[str] = []
    if dist_flag and {"main_net_ratio", "main_inflow_yi"}.issubset(df.columns):
        from autoresearch.common.scoring import main_net_distortion_label
        df["main_dist"] = [main_net_distortion_label(r, a) for r, a in
                          zip(df["main_net_ratio"], df["main_inflow_yi"], strict=True)]
        cols = [*cols, "main_inflow_yi", "main_dist"]
        header += ["_⚠主力失真列(main_dist):**反号**=占比正而绝对净额(main_inflow_yi,亿)为负;"
                   "**微量**=占比≥2%而|绝对|<0.5亿(微盘/窗口放大)。两型下「主力净流入」"
                   "**不得单独作核心多头论点**,须绝对净额+cmf/obv 同向共振才算确认"
                   "(07-03 实证:该型 finalist 深核全数翻案)。_", ""]
    if reg_flag and "code" in df.columns:
        from autoresearch.scan.agents.l3_news import reg_hits_for_code
        day_dir = (root or Path("context/scan")) / date
        df["news_reg"] = [reg_hits_for_code(day_dir, c) for c in df["code"]]
        cols = [*cols, "news_reg"]
        header += ["_⚠监管旗(news_reg):近 10 日公告命中 立案/问询/关注函/处罚/违规/诉讼/"
                   "监管/证监会/交易所。旗票论点**必须显式回应监管事项**,不得无视;独立检测器,"
                   "情感列口径不变(非利空词表变更)。_", ""]
    if cat_flag and "code" in df.columns:
        catp = (root or Path("context/scan")) / date / "L3_catalyst.csv"
        if catp.exists():
            from autoresearch.scan.agents.l3_catalyst import cat_label
            try:
                cf = pd.read_csv(catp, dtype={"code": str})
                cf["code"] = cf["code"].astype(str).str.zfill(6)
                lab = {r["code"]: cat_label(r) for r in cf.to_dict("records")}
            except Exception:  # noqa: BLE001 — 坏 staging 降级不加列
                lab = None
            if lab is not None:
                df["cat"] = [lab.get(str(c).zfill(6), "") for c in df["code"]]
                cols = [*cols, "cat"]
                header += ["_📣催化列(cat):近 10 日 回购/增持/机构调研/减持 事件计数(存在性"
                           "≠方向确认)。催化须与资金/基本面共振才可作论点支柱;**减持≥2 的票"
                           "论点必须显式回应**。_", ""]
    if rc_flag and "code" in df.columns:
        rcp = (root or Path("context/scan")) / date / "consensus.csv"
        if rcp.exists():
            try:
                rf = pd.read_csv(rcp, dtype={"code": str})
                rf["code"] = rf["code"].astype(str).str.zfill(6)
                rf["_d"] = pd.to_numeric(rf.get("eps_delta_pct"), errors="coerce")
                lab = {r["code"]: (f"{r['_d']:+.0f}%" if pd.notna(r["_d"]) else "")
                       for r in rf.to_dict("records")}
            except Exception:  # noqa: BLE001 — 坏 staging 降级不加列
                lab = None
            if lab is not None:
                df["rc"] = [lab.get(str(c).zfill(6), "") for c in df["code"]]
                cols = [*cols, "rc"]
                header += ["_机构面列(rc):卖方一致预期 FY EPS 近窗修正(%,存在性≠方向确认,"
                           "advisory)。与资金/基本面共振才可作论点支柱。_", ""]
    if pinned_flag and "code" in df.columns:
        from autoresearch.scan.user_config import load_pinned
        kept = load_pinned(date, path=pinned_path)["kept"]
        if kept:
            pin_codes = {e["code"] for e in kept}
            df["pinned"] = df["code"].map(lambda c: "📌" if str(c).zfill(6) in pin_codes else "")
            cols = [*cols, "pinned"]
            header += ["_📌(pinned列):用户手工保送票——L1→L5 全程强留、不可淘汰;"
                       "仍须按下表真实证据独立评判,不因『保送』降低尽调标准。_", ""]
    if delta:
        prev = _prev_l3_day(date, root=root)
        if prev is None:
            header += ["_Δ模式:无前日 L3 现场 → 回退全量表_", ""]
        else:
            df, dropped = _delta_filter(df, prev)
            cols = [*cols, "prev_l3"]
            header += [f"_Δ模式(vs {prev.name}):略去 **{len(dropped)}** 只「昨判弃且无变化」票"
                       f"(重新入场条件:Δcomposite>2 或 Δpct60>2 或新 lhb/预告/快报);"
                       f"**今日仍须对下表独立重新比较,prev_l3 列仅供参考、严禁沿用昨日结论**;"
                       f"全量表每 ≤5 个 scan 日至少 1 次_", ""]
    if misread_flag:
        from autoresearch.common.scoring import l3_misread_flags
        df["misread"] = df.apply(l3_misread_flags, axis=1)
        cols = [*cols, "misread"]
        header.append(
            "misread 预警:低基=净利暴增但 ROE 极低(低基数幻觉,勿当真成长);背离=cmf/obv 正但当日主力净流出"
            "(拉高派发嫌疑);套牢=低获利盘·非多头排列·60日已涨(反弹撞套牢盘≠上行空间)。**旗亮仍以对应论点入选者,thesis 必须一句自证非陷阱**。")
    if shuffle_seed is not None:
        df = df.sample(frac=1, random_state=int(shuffle_seed)).reset_index(drop=True)
    table = _render_lane_blocks(df, cols) if lane_blocks else compact_table(df, cols=cols)
    body = "\n".join([*header, table]) if header else table
    if sector_terrain:                      # Phase 3:全行业地形段前置(默认关 = 逐字 parity)
        try:
            from autoresearch.sector.pack import sector_terrain_md
            terr = sector_terrain_md((root or Path("context/scan")) / date, top200_only=True)
            if terr:
                body = terr + "\n\n" + body
        except Exception:  # noqa: BLE001 — 地形可选,缺 staging 不挡表
            pass
    return body


def stability_overlap(codes_a, codes_b) -> dict:
    """两次 L3 选择的重叠度(稳定性抽检:同表乱序重跑,<70% = rubric 太松/噪声大)。"""
    a = {str(c).zfill(6) for c in codes_a}
    b = {str(c).zfill(6) for c in codes_b}
    inter = a & b
    denom = min(len(a), len(b))
    return {"n_a": len(a), "n_b": len(b), "n_common": len(inter),
            "overlap": round(len(inter) / denom, 3) if denom else 0.0}


def _period(date: str) -> str:
    from autoresearch.common.scoring import latest_reported_quarter
    return latest_reported_quarter(date)


def harvest_l3_evidence(date: str, codes: list[str], root: Path | None = None) -> dict:
    """对 L2 保留的 ~200 只补 L1 没有的真证据(龙虎榜/预告/快报)。bulk by date 一次拉、本地过滤;

    失败/无权限降级标注。产出 context/scan/<date>/L3_evidence/<code>.json,返回 {code: evidence}。
    2026-07-12 P2a:三端点改走 get_or_fetch(policy 早已注册)——已结算日湖命中零网络,预热(P1)可预拉。
    """
    import json

    from autoresearch.data import cache as _cache  # 经模块属性调用,测试可 monkeypatch
    from autoresearch.data.tushare_source import _code6, _pro, resolve_momentum_dates
    root = root or Path("context/scan")
    out_dir = root / date / "L3_evidence"
    out_dir.mkdir(parents=True, exist_ok=True)
    pro = _pro()
    last = resolve_momentum_dates(pro, date)[0]
    want = {str(c).zfill(6) for c in codes}
    ev: dict[str, dict] = {c: {"code": c} for c in want}

    def _bulk(label, fn, key_field="ts_code"):
        try:
            df = fn()
            if df is None or df.empty:
                return
            df = df.assign(_c=_code6(df[key_field]))
            for c, g in df[df["_c"].isin(want)].groupby("_c"):
                ev[c].setdefault(label, []).extend(g.drop(columns=["_c"]).to_dict("records"))  # 累积(可多日)
        except Exception as e:  # noqa: BLE001
            ev.setdefault("_errors", {}).setdefault(label, str(e))   # 端点级错误记一次,不污染每只

    _bulk("longhu", lambda: _cache.get_or_fetch("top_list", {"trade_date": last}, today=date))  # 龙虎榜席位(游资/机构)
    # forecast/express 需 ann_date 或 ts_code(period 单参不够)→ 扫最近 ~10 个交易日的公告
    from datetime import datetime, timedelta

    from autoresearch.data.tushare_source import _trade_days
    start = (datetime.strptime(last, "%Y%m%d") - timedelta(days=30)).strftime("%Y%m%d")
    for dd in _trade_days(pro, start, last)[-10:]:
        _bulk("forecast", lambda dd=dd: _cache.get_or_fetch("forecast", {"ann_date": dd}, today=date))   # 业绩预告
        _bulk("express", lambda dd=dd: _cache.get_or_fetch("express", {"ann_date": dd}, today=date))     # 快报
    for c in want:
        (out_dir / f"{c}.json").write_text(json.dumps(ev[c], ensure_ascii=False, default=str), encoding="utf-8")
    return ev


def _swap_lane_quota(m: pd.DataFrame, conv: pd.Series, fin_idx: set, lane_val: str,
                     target: int, guard_name: str, qualify_conv: float = 65.0,
                     protect_lanes: set[str] | None = None) -> set:
    """守卫④/⑤共用的尾部票置换:`fin_idx`(候选集索引)里 `lane==lane_val` 计数不足
    `target` → 从 bench(`m.index` 里不在 `fin_idx` 的行)找够格候选(`lane==lane_val`
    且 `conviction>=qualify_conv`,按 conviction 降序),换掉候选集里"非 `lane_val`、
    非 `protect_lanes`、且 `conviction<75`(受 ins75 保险保护的行不可被换出)"中
    conviction 最低的一个,双方都记 `guard=guard_name`(就地写回 `m`,不覆盖已有更具体
    的 guard)。**bench 无够格候选,或候选集里找不到可换的尾部票 → 提前 break,不硬凑**
    (用户裁定:有够格候选才凑,无则不硬凑)。返回置换后的 `fin_idx`(新 set,不改原对象)。

    `protect_lanes`(final-review-l3-merge.md Important-2):额外保护的 lane 集合,
    不作为本次置换的换出候选——`lane_val` 自身恒被保护(同 lane 不该自己换自己),
    `protect_lanes` 用于保护**别的**守卫刚满足的硬约束不被本次(通常是更靠后的 soft)
    置换击穿,例如守卫⑤(trend soft 2 席)不该吃掉守卫④(健康比例硬约束)刚配置好的
    healthy 行——即便该 healthy 行是候选集里 conviction 最低、原逻辑会选中的"最弱尾部票"。
    """
    if "lane" not in m.columns:
        return fin_idx
    lane = m["lane"].astype(str)
    have = sum(1 for i in fin_idx if lane.loc[i] == lane_val)
    deficit = target - have
    if deficit <= 0:
        return fin_idx
    bench_pool = [i for i in m.index if i not in fin_idx
                 and lane.loc[i] == lane_val and conv.loc[i] >= qualify_conv]
    bench_pool.sort(key=lambda i: conv.loc[i], reverse=True)
    protect = {lane_val} | (protect_lanes or set())
    fin_idx = set(fin_idx)
    for cand in bench_pool:
        if deficit <= 0:
            break
        removable = [i for i in fin_idx if lane.loc[i] not in protect and conv.loc[i] < 75]
        if not removable:
            break                                   # 无可换尾部票 → 不硬凑
        removable.sort(key=lambda i: conv.loc[i])    # 换掉候选集里最弱的
        victim = removable[0]
        fin_idx.discard(victim)
        fin_idx.add(cand)
        m.loc[cand, "guard"] = guard_name
        if not m.loc[victim, "guard"]:
            m.loc[victim, "guard"] = guard_name
        deficit -= 1
    return fin_idx


def merge_l3_finalists_v3(judged: pd.DataFrame, budget: int,
                          finalist_max: int = 10) -> tuple[pd.DataFrame, pd.DataFrame]:
    """L3 finalist tier 合并(design: plan 2026-07-12-l3-merge-plan.md Task 2;L3.5 的收窄职能
    并入本函数,取代 `merge_l3_finalists_v2` 的 target/trend_quota 硬配额)。

    l3-rank(`.claude/agents/l3-rank.md` v2)判断每票时写 `finalist` 布尔字段(True=finalist
    tier,7–10 只,数量看当天质量;False=**bench**,仍全字段判断、不是弃权)。本函数消费该
    标记 + 施加确定性守卫,产出 `(finalists, bench)` 两张表——**互斥、并集=`judged` 全量**
    (`bench` = `judged` − `finalists`)。

    `cap = min(finalist_max, budget)`(`budget` 通常是 workflow `--budget`,即当日 `l4_budget`;
    `finalist_max` 是本波新增上限,默认 10——两者取更严格的一个)。

    守卫序(按序应用,后一守卫在前一守卫处理后的候选集上运行):

    ① **ins75 保险**:候选集外(未标 `finalist=True`)但 `conviction>=75` 的行强制补入,
       `guard="ins75"`——用户裁定"conviction≥75 必须 finalist"是确定性硬约束,不能只靠
       l3-rank 人设自觉遵守(人设里也写了这条,这里是不依赖 agent 遵守的兜底)。已经在
       候选集里的 conviction≥75 行不需要这个标记(它本来就在,不算"被保险救回")。
    ② **lt55 拒绝**:候选集里 `conviction<55` 的行剔除(挪进 bench),`guard="lt55"`——
       即便 l3-rank 误标 `finalist=True` 也不该出现,同样是确定性硬约束,不靠自觉。
    ③ **cap 截尾**:候选集超过 `cap` → 按 `conviction` 降序保留前 `cap`,其余挪进 bench
       (`guard="cap"`,**无条件覆写**——即便该行先前已被①标过 `"ins75"`,只要它最终仍被
       cap 挤出候选集,guard 就该反映"真正原因是 cap 截尾",不留半真半假的旧标签;
       final-review-l3-merge.md Minor-3①)。
    ④ **健康比例守卫**(比例制,`ceil(n/3)`,`n`=当前候选集大小):候选集里 `lane=="healthy"`
       (v1 从简判定——只认 l3-rank 已写下的 `lane` 字段是否恰为 `"healthy"` 这一个字符串,
       不重算 pct_60d/main_net/cmf/obv 的组合读数;那套定性判断是 l3-rank rubric 硬约束 A
       的职责,确定性层这里只做"数够不够"的兜底,故意从简,更精细的健康画像判定留给
       将来版本)计数不足 → 见 `_swap_lane_quota`(target=`ceil(n/3)`、`guard="healthy_quota"`)。
    ⑤ **trend soft 2 席**:同④机制(`_swap_lane_quota`),`target=2`(固定,非比例)、
       `lane=="trend"`、`guard="trend_quota"`——L3.5 时代硬配额(`trend_quota=10`)降级为
       soft 下限,"有够格候选才凑,无则不硬凑"同样适用(不达标不强求)。**`protect_lanes=
       {"healthy"}`**:trend 换出尾部票时不得选中 healthy 行——健康比例是④刚满足的硬约束
       (Global Constraints A),trend 只是 soft 下限,soft 不能吃掉 hard(final-review-l3-merge.md
       Important-2;修复前:healthy 恰达标日,trend 缺口会把候选集里 conviction 最低的
       healthy 行当"最弱尾部票"换出,④白跑)。

    **缺 `finalist` 列**(向后兼容:T3 之前落的旧 `_l3_judged.json` 没有这个字段)→ 全体行
    视为初始候选(等价"先假设全选"),同样跑①–⑤(①在此情形恒无操作对象——全体已是候选;
    ②③④⑤照常运行),等效于"全体按 conviction 排序取 cap,同守卫"。

    返回 `(finalists, bench)`:
      - `finalists` 沿用 `merge_l3_finalists_v2` 的展示 schema(`ticker`/`code`/`name`/
        `sector`/`lenses`/`conviction`/`triage_lean`/`triage_reason`/`thesis`/`mechanism`/
        `risk`/`catalyst`/`lane`/`sentiment`)+ 本函数新增的 `guard` 列(`write_finalists`
        据此写 finalists.csv,格式"照旧"只加这一列)。
      - `bench` 保留 `judged` **全部原始列**(`code` 已 6 位零填、`conviction`/`fragility`/
        `pct_60d` 已转数值,与 `finalists` 同口径)+ `guard`(`write_finalists` 据此落
        `_l3_bench.csv`——账本要看到完整判断,不是展示裁剪后的字段)。

    两表按 `code` 互斥、按行索引并集覆盖 `judged` 全量(无遗漏无重复)。`judged` 为空 →
    两个都空(仍带 `guard` 列)。

    **去重(zfill 后,final-review-l3-merge.md Important-1)**:`_l3_judged.json` 是 LLM
    (l3-rank)写的,同码写两行是真实风险(v2 当年 `.drop_duplicates(subset="code")` 就是
    为此设防,v3 重写时漏掉)。同码(6 位零填后)只留第一次出现的一行走完整套守卫,其余
    整行直接归 `bench` 记 `guard="dup"`——账本留痕、不静默消失(不同于 v2 的"并集去重后
    直接从两表都消失",这里明确记为一种"被丢弃"原因)。
    """
    if judged.empty:
        empty = judged.copy()
        empty["guard"] = pd.Series(dtype=object)
        return empty.copy(), empty.copy()

    cap = max(0, min(int(finalist_max), int(budget)))
    m = judged.reset_index(drop=True).copy()
    m["code"] = m["code"].astype(str).str.zfill(6)
    if "conviction" not in m.columns:
        m["conviction"] = 0.0
    for c in ("conviction", "fragility", "pct_60d"):
        if c in m.columns:
            m[c] = pd.to_numeric(m[c], errors="coerce")
    m["guard"] = ""

    # I-1 去重:同码(zfill 后)只留第一次出现,其余整行摘出 → 落 bench 记 guard="dup"。
    # 摘出发生在 conviction/fragility/pct_60d 数值化**之后**,保证 dup_rows 与其余账本行
    # 同口径(数值列已转 float,非原始字符串/int)。
    dup_mask = m["code"].duplicated(keep="first")
    dup_rows = m.loc[dup_mask].copy()
    dup_rows["guard"] = "dup"
    m = m.loc[~dup_mask].reset_index(drop=True)

    conv = m["conviction"].fillna(0.0)

    if "finalist" in m.columns:
        sel = m["finalist"].fillna(False).astype(bool).copy()
    else:                                       # 缺列向后兼容:全体皆候选
        sel = pd.Series(True, index=m.index)

    ins75 = (conv >= 75) & (~sel)                # 守卫①:误杀保险强制补入
    m.loc[ins75, "guard"] = "ins75"
    sel = sel | ins75

    lt55 = sel & (conv < 55)                     # 守卫②:低于 55 禁止 finalist
    m.loc[lt55, "guard"] = "lt55"
    sel = sel & ~lt55

    order = list(m.index[sel])
    order.sort(key=lambda i: conv.loc[i], reverse=True)
    if len(order) > cap:                         # 守卫③:超 cap 按 conviction 截尾
        for i in order[cap:]:
            m.loc[i, "guard"] = "cap"             # 无条件覆写(M-3①:哪怕先前是 "ins75")
        order = order[:cap]

    fin_idx: set = set(order)
    n = len(fin_idx)
    fin_idx = _swap_lane_quota(m, conv, fin_idx, "healthy",             # 守卫④
                               math.ceil(n / 3) if n else 0, "healthy_quota")
    fin_idx = _swap_lane_quota(m, conv, fin_idx, "trend", 2, "trend_quota",   # 守卫⑤
                               protect_lanes={"healthy"})   # I-2:不可换出健康配额行

    fin_order = sorted(fin_idx, key=lambda i: conv.loc[i], reverse=True)
    fin = m.loc[fin_order].copy()
    fin["ticker"] = fin["code"]
    cols = ["ticker", "code", "name", "sector", "lenses", "conviction",
            "triage_lean", "triage_reason", "thesis", "mechanism", "risk", "catalyst",
            "lane", "sentiment", "guard"]
    fin = fin[[c for c in cols if c in fin.columns]].reset_index(drop=True)

    bench_idx = [i for i in m.index if i not in fin_idx]
    bench = m.loc[bench_idx].reset_index(drop=True)
    if len(dup_rows):                             # I-1:重复行归 bench,留痕不消失
        bench = pd.concat([bench, dup_rows], ignore_index=True)
    return fin, bench


def _inject_pinned_finalists(fin: pd.DataFrame, kept: list[dict],
                             lookup: pd.DataFrame | None = None,
                             judged: pd.DataFrame | None = None) -> pd.DataFrame:
    """finalists 强留(design 2026-07-11-recall-gate-pinned-config-design.md §4.1;plan Task 4)。

    pinned 每条(`user_config.load_pinned(...)["kept"]`):
      - 已在 `fin`(L3 holistic 真判已入选)→ 不重复行,只把 `lane` 强改判 `"pinned"` +
        落 `pinned_note`——**finalists.csv 里识别 pinned 行的单一信号**(A2-T4 的 L3.5
        闸据此构造 exempt 集;L5 assemble 的「📌 保送」节也按此在 finalists 里查评级)。
        conviction/thesis/risk/catalyst 等 L3 真判字段原样保留(判断记录在案,不因保送
        抹掉——design §4.1"L3 真判但不可淘汰")。**注**:本函数只改 finalists.csv,不碰
        `L3_judged_full.csv`(该文件仍留 L3 agent 自己判的原始 lane,如 trend/value——
        `learning.cross_calib.flip_stats` 的按 lane 翻案率读的正是那份原始记录,与此处
        finalists 层的"pinned"标记是两回事,故意不合并;`learning.channel_ledger` 的
        per-channel 账则完全在 L1 层(`recall_channels`,universe._inject_pinned_l1 已标
        `"pinned"`),同样与本函数无关——三层"pinned"标记互相独立、各自服务各自的下游);
      - 不在 `fin` 但**在 `judged` 里**(L3 判过、`finalist=false` → 落 bench)→ 从 `judged`
        取该行,**thesis/risk/catalyst/conviction/lenses/sentiment 等 L3 真判字段整段带过来**
        (只把 lane 改判 `"pinned"`、挂 note)。2026-07-12 生产实测:4/4 保送持仓走的都是这条
        路径,而修复前只查 `lookup`(L2 表**没有**这些列)→ L3 的判断被整段丢弃 → finalists.csv
        thesis 全空 → summary 渲染成「风险:;催化:」→ L4 prompt 告诉卡片「pinned 无 L3 前提
        清单」,卡片只好自己从 L1 重建前提。**保送 ≠ 免判,更 ≠ 判了不要。**
      - 既不在 `fin` 也不在 `judged`(L3 压根没见过它:pass1 切了 / 不在 L2)→ 从 `lookup`
        (通常是 `write_finalists` 已读入的 L2_gbdt_top200.csv)取真实行补 name/sector 等展示
        字段;`lookup` 无该码/未传 → 占位行(仅 code/ticker/lane/pinned_note,`data_missing=True`,
        不编数,镜像 `universe._inject_pinned_l1` 同一降级顺序)。

    lookup 优先级 = `fin` → `judged` → `lookup`(L2) → 占位行。

    本函数在 `merge_l3_finalists_v2` 已完成 `target` 截断排序**之后**调用(`write_finalists`
    编排),纯追加/打标——不占 target 名额、不挤他票(与 L1/L2 强留同一"全程直通"结构性
    保证)。`kept` 空 → 原样返回(presence-gated parity)。
    """
    if not kept:
        return fin
    out = fin.copy()
    if "code" in out.columns:
        out["code"] = out["code"].astype(str).str.zfill(6)
    have = set(out["code"]) if "code" in out.columns else set()
    if "lane" not in out.columns:
        out["lane"] = ""
    if "pinned_note" not in out.columns:
        out["pinned_note"] = ""
    if "data_missing" not in out.columns:
        out["data_missing"] = False

    lookup_z = None
    if lookup is not None and "code" in lookup.columns:
        lookup_z = lookup.assign(code=lookup["code"].astype(str).str.zfill(6))

    judged_z = None
    if judged is not None and not judged.empty and "code" in judged.columns:
        judged_z = judged.assign(code=judged["code"].astype(str).str.zfill(6))

    new_rows: list[pd.DataFrame] = []
    seen_new: set[str] = set()
    for entry in kept:
        code = str(entry["code"]).split(".")[0].zfill(6)
        note = entry.get("note", "")
        if code in have:                          # L3 真判已入选 → 只强改判 lane,不重复行
            m = out["code"] == code
            out.loc[m, "lane"] = "pinned"
            out.loc[m, "pinned_note"] = note
            continue
        if code in seen_new:                      # 同票重复 pin 条目(用户笔误)→ 只注一次
            continue
        seen_new.add(code)
        row_data: dict = {"code": code, "ticker": code, "lane": "pinned",
                          "pinned_note": note, "data_missing": True}
        # ① judged 优先:pinned 被 L3 判过但未入选(finalist=false → 落 bench,不在 `fin`)时,
        #    它的 thesis/risk/catalyst/conviction 就在 judged 帧里 —— 必须整段带过来。
        #    2026-07-12 生产实测:4/4 保送持仓都走这条路,此前只查 L2(无这些列)→ L3 判断被
        #    整段丢弃 → finalists.csv 空 thesis → summary 渲染「风险:;催化:」→ L4 prompt
        #    告诉卡片「pinned 无 L3 前提清单」,卡只好自己从 L1 重建前提。L3 的活白干。
        hit_j = judged_z[judged_z["code"] == code] if judged_z is not None else None
        if hit_j is not None and len(hit_j):
            r0 = hit_j.iloc[0].to_dict()
            r0.pop("finalist", None)              # finalist 是 judged 内部字段,不进 finalists.csv
            row_data = {**{k: v for k, v in r0.items() if pd.notna(v)}, **row_data}
            row_data["data_missing"] = False
        # ② L2 兜底:L3 压根没判过它(pass1 切了 / 不在 L2)→ 只能取展示字段,不编数。
        elif lookup_z is not None:
            hit = lookup_z[lookup_z["code"] == code]
            if len(hit):
                r0 = hit.iloc[0]
                row_data["data_missing"] = False
                if "name" in lookup_z.columns and pd.notna(r0.get("name")):
                    row_data["name"] = r0["name"]
                if "industry" in lookup_z.columns and pd.notna(r0.get("industry")):
                    row_data["sector"] = r0["industry"]
        new_rows.append(pd.DataFrame([row_data]))
    if new_rows:
        out = pd.concat([out, *new_rows], ignore_index=True, sort=False)
    return out


def write_finalists(date: str, budget: int = 30, root: Path | None = None,
                    pinned_path: Path | str | None = None) -> dict:
    """确定性写 finalists.csv + L3_judged_full.csv + `_l3_bench.csv`(workflow L3 后确定性入口,
    取代手工 glue)。

    读 l3-rank agent 落的 _l3_judged.json → 从 L2 回填 pct_60d(供缺 `finalist` 列时的旧
    judged 回退路径与 v2 兼容;v3 本身不需要 pct_60d)→ `merge_l3_finalists_v3`(消费
    `finalist` 标记 + 确定性守卫,design: plan 2026-07-12-l3-merge-plan.md Task 2)产出
    (finalists, bench)→ pinned 强留(`_inject_pinned_finalists`,design 2026-07-11 §4.1;
    plan Task 4;presence-gated:无 pinned.json/kept 全空 → 不变,**在 v3 之后、不占
    finalist 名额**)→ bench 落 `_l3_bench.csv` **之前**先摘掉已被 pinned 注入进
    finalists 的码(M-1 修复:防止同票双记 bench 与 finalists,见 `refine_l3_bucket`/
    `l3_bench_shadow` 消费方)→ 写盘。**全程 6 位零填**,修 000062→62 的 CSV 往返坑。

    `finalist_max`(v3 的 `min(finalist_max, budget)` 上限)从
    `load_user_config().get("l3", {}).get("finalist_max", 10)` 读(T1 已建白名单)。

    返回 dict:`judged_n`/`finalists_n` 语义不变(`finalists_n` = 写盘 finalists.csv 的最终
    行数,含 pinned 追加);新增 `finalist_n`(v3 产出的 finalist tier 行数,**pinned 注入前**,
    即当日 L3 finalist tier 的真实大小)、`bench_n`(bench 行数)。
    """
    base = Path(root) if root else Path("context/scan")
    scan_dir = base / date
    picks = json.loads((scan_dir / "_l3_judged.json").read_text(encoding="utf-8"))
    jd = pd.DataFrame(picks)
    if jd.empty or "code" not in jd.columns:
        raise ValueError(f"_l3_judged.json 空或缺 code 列:{scan_dir / '_l3_judged.json'}")
    jd["code"] = jd["code"].astype(str).str.zfill(6)
    l2p = scan_dir / "L2_gbdt_top200.csv"
    l2 = None
    if l2p.exists():
        l2 = pd.read_csv(l2p, dtype={"code": str})
        l2["code"] = l2["code"].astype(str).str.zfill(6)
        if "pct_60d" not in jd.columns and "pct_60d" in l2.columns:
            jd = jd.merge(l2[["code", "pct_60d"]], on="code", how="left")
    jd.to_csv(scan_dir / "L3_judged_full.csv", index=False)       # 全量判断(retro/assemble/trace)

    from autoresearch.scan.user_config import load_user_config
    finalist_max = int((load_user_config().get("l3") or {}).get("finalist_max", 10))
    fin, bench = merge_l3_finalists_v3(jd, budget=budget, finalist_max=finalist_max)
    finalist_n = int(len(fin))

    from autoresearch.scan.user_config import load_pinned
    kept = load_pinned(date, path=pinned_path)["kept"]
    if kept:
        # judged=jd:pinned 被 L3 判过但落 bench 时,把它的 L3 真判字段带进 finalists(不然
        # 只剩 L2 空行 → 下游 summary/L4 prompt 全以为"pinned 没有 L3 论点")。
        fin = _inject_pinned_finalists(fin, kept, lookup=l2, judged=jd)
        # M-1 修复(final-review-l3-merge.md Minor-1):pinned 码若被 l3-rank 判为 bench
        # (`finalist=False`)且此处被强留注入 finalists,不应再留在 bench 账本里双记——
        # 否则 `refine_l3_bucket` 会把明明已上 L4 的票误标 `l3_bench`,`l3_bench_shadow`
        # 读数被同一只票两侧重复计入,法庭读数掺噪。落盘前把已进 fin 的码从 bench 里摘掉。
        bench = bench[~bench["code"].astype(str).isin(set(fin["code"].astype(str)))
                     ].reset_index(drop=True)
    bench_n = int(len(bench))
    bench.to_csv(scan_dir / "_l3_bench.csv", index=False)
    fin.to_csv(scan_dir / "finalists.csv", index=False)
    return {"judged_n": int(len(jd)), "finalists_n": int(len(fin)),
            "finalist_n": finalist_n, "bench_n": bench_n}


def prepare_l3_table(date: str, root: Path | None = None, delta: bool = True,
                     do_harvest: bool = True, pinned_path: Path | str | None = None,
                     two_pass: bool | None = None,
                     calib_blocks: bool | None = None) -> dict:
    """L3 精排前的确定性件:harvest 证据/公告情感 + 构建紧凑表 → 写 _l3_table.md(l3-rank agent 读)。

    pinned_path 透传给 `l3_table_md`(测试注入;生产默认路径见 `user_config.load_pinned`)。

    two_pass(pass1 确定性分诊;design: plan 2026-07-12-l3-merge-plan.md Task 1):
    `None`(默认)→ 读 `user_config.load_user_config().get("l3", {})` 的 `two_pass`(缺配置
    默认 `True`,本波后新基线)与 `pass1_target`(缺配置默认 60)。`True`(现默认):先用
    `triage_l2_for_l3` 把 `load_l3_input` 的 ~200 行分诊出 kept(~pass1_target 行,喂
    `l3_table_md` 走现有 delta/lane 渲染管线不变)+ cut(落 `_l3_pass1_cut.csv` 全字段
    影子,供 attribution 证明分诊没吃赢家);`_l3_table.md` 表头追加一行「pass1 分诊
    {n_in}→{n_kept}(影子 `_l3_pass1_cut.csv`)」,返回 dict 追加 `pass1_kept`/`pass1_cut`
    两键。显式传 `False`(回滚杆):**现行为逐字节不变**——不 triage、不写 cut csv、不加
    表头行、且**完全不调用 `load_user_config`**(纯净回滚,哪怕 scan_config.json 本身写坏
    了也不受影响);返回 dict 形状与本 task 之前实现完全一致(仅 `codes`/`table_bytes`)。

    calib_blocks(2026-07-17 自我迭代腿,fb_20260717_001):表尾追加两个校准块——
    ① T+1 快环校准(`t1_review.render_t1_calibration_block`,账本派生数据,空账本=零字节);
    ② 经验校准(`feedback_store.render_calibration_block(regime, with_feedback=True)`,
      修 pr_20260716_005:此前「经验自动注回 L2/L3 prompt」有腿无接线)。
    `None`(默认)= 跟随 two_pass(two_pass 显式 False 时同关,保住上面「逐字节不变」的
    回滚承诺);显式 True/False 独立强制。两块注入各自 suppress:炸了只丢块不挡 L3。
    """
    base = Path(root) if root else Path("context/scan")
    scan_dir = base / date
    l2 = pd.read_csv(scan_dir / "L2_gbdt_top200.csv", dtype={"code": str})
    codes = l2["code"].astype(str).str.zfill(6).tolist()
    if do_harvest:
        harvest_l3_evidence(date, codes, root=base)
        from autoresearch.scan.agents.l3_news import harvest_l3_news
        harvest_l3_news(date, codes, root=base)

    l3_cfg: dict = {}
    if two_pass is not False:              # 显式 False = 纯回滚杆,连 load_user_config 都不碰
        from autoresearch.scan.user_config import load_user_config
        l3_cfg = load_user_config().get("l3") or {}
        if two_pass is None:
            two_pass = bool(l3_cfg.get("two_pass", True))

    restrict_codes = None
    pass1_header = ""
    pass1_counts: dict = {}
    if two_pass:
        pass1_target = int(l3_cfg.get("pass1_target", 60))
        df_full = load_l3_input(date, root=base)
        kept, cut = triage_l2_for_l3(df_full, target=pass1_target)
        cut.to_csv(scan_dir / "_l3_pass1_cut.csv", index=False)
        restrict_codes = set(kept["code"].astype(str).str.zfill(6))
        pass1_header = f"_pass1 分诊 {len(df_full)}→{len(kept)}(影子 `_l3_pass1_cut.csv`)_"
        pass1_counts = {"pass1_kept": len(kept), "pass1_cut": len(cut)}

    md = l3_table_md(date, root=base, delta=delta, dist_flag=True, reg_flag=True,
                     cat_flag=True, sector_terrain=True, misread_flag=True,
                     pinned_flag=True, pinned_path=pinned_path, lane_blocks=True,
                     restrict_codes=restrict_codes)
    if pass1_header:
        md = pass1_header + "\n\n" + md

    if calib_blocks is None:
        calib_blocks = two_pass is not False    # 跟随回滚杆:two_pass=False 承诺逐字节不变
    if calib_blocks:
        import contextlib
        with contextlib.suppress(Exception):    # 快环校准块(账本派生;空账本=零字节 parity)
            from autoresearch.learning.t1_review import render_t1_calibration_block
            blk = render_t1_calibration_block(stage="L3")   # 只带 L3/门/流程相关观察(ERL:相关性>数量)
            if blk:
                md = md + "\n\n" + blk
        with contextlib.suppress(Exception):    # 经验校准块(pr_20260716_005 接线;恒有基线)
            import json as _json

            from autoresearch.learning.feedback_store import render_calibration_block
            regime = None
            mp = scan_dir / "meta.json"
            if mp.exists():
                regime = _json.loads(mp.read_text(encoding="utf-8")).get("regime")
            md = md + "\n\n" + render_calibration_block(regime=regime, with_feedback=True)

    (scan_dir / "_l3_table.md").write_text(md, encoding="utf-8")
    return {"codes": len(codes), "table_bytes": len(md), **pass1_counts}


# ───────────────────────── L3:thesis 数字机检(lint_judged) ─────────────────────────

_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")
# 长的先试(YYYY-MM-DD 整段吃掉,防被短的 MM-DD 分支截成两段残留数字)。
_DATE_TOKEN_RE = re.compile(r"\d{4}-\d{1,2}-\d{1,2}|\d{1,2}-\d{1,2}")
_YEAR_TOKEN_RE = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")
_CODE_TOKEN_RE = re.compile(r"(?<!\d)\d{6}(?!\d)")
_PERIOD_SUFFIX = ("年", "月", "日", "周", "季")   # 数字紧跟这些字 = 窗口/周期标签(如"60日"),非数据主张
_IDENT_CHAR_RE = re.compile(r"[A-Za-z_]")        # 数字紧贴字母/下划线 = 列标识符内嵌窗口(如 pct_60d/rsi6),非数据主张


def _thesis_number_tokens(text: str) -> list[str]:
    r"""thesis 里「待核实」的数字 token:过滤 4 位年份 / 6 位代码 / `07-15`(或 `2026-07-15`)形
    日期 / `N年|月|日|周|季` 窗口标签(如"60日涨12%"的 60 是窗口)/ 紧贴字母或下划线的数字
    (如引用列名 `pct_60d`/`rsi6`/`cmf_20` 时嵌在标识符里的窗口数——07-08 真实数据冒烟逮到
    "pct_60d +21.95" 的 60、"rsi6 52.51" 的 6 被错当数字核对)后,
    `re.findall(r"-?\d+(?:\.\d+)?", ...)` 逐个取出。"""
    if not text:
        return []
    text = text.replace("−", "-")           # U+2212 全角负号归一(pf 列"主力−"同字形,防丢符号误报)
    t = _DATE_TOKEN_RE.sub(" ", text)
    t = _YEAR_TOKEN_RE.sub(" ", t)
    t = _CODE_TOKEN_RE.sub(" ", t)
    out: list[str] = []
    for m in _NUM_RE.finditer(t):
        if t[m.end():m.end() + 1] in _PERIOD_SUFFIX:
            continue
        before = t[m.start() - 1:m.start()] if m.start() > 0 else ""
        after = t[m.end():m.end() + 1]
        if _IDENT_CHAR_RE.match(before) or _IDENT_CHAR_RE.match(after):
            continue
        out.append(m.group())
    return out


def _row_numeric_pool(pick: dict, l2_row) -> list[float]:
    """该票行的数值集合:L2 csv 该码全部数值列(`code` 本身除外)+ judged 行自身
    pct_60d/conviction(容差匹配用)。坏值/非数容错跳过。"""
    pool: list[float] = []
    if l2_row is not None:
        for k, v in l2_row.items():
            if k == "code":
                continue
            try:
                fv = float(v)
            except (TypeError, ValueError):
                continue
            if fv == fv:
                pool.append(fv)
    for key in ("pct_60d", "conviction"):
        try:
            fv = float(pick.get(key))
        except (TypeError, ValueError):
            continue
        if fv == fv:
            pool.append(fv)
    return pool


def _approx_in_pool(token_val: float, pool: list[float]) -> bool:
    """±1% 相对或 ±0.1 绝对容差;百分数与小数互认(×100/÷100 都试一遍)。"""
    for v in pool:
        for scale in (1.0, 100.0, 0.01):
            cv = v * scale
            if abs(token_val - cv) <= 0.1:
                return True
            if cv and abs(token_val - cv) / abs(cv) <= 0.01:
                return True
    return False


def lint_judged(date: str, root: Path | None = None) -> dict:
    """thesis 数字机检(确定性 lint,零 LLM):每条 thesis 的数字 token(过滤年份/代码/日期/
    N日窗口标签)须能在该票 L2 数值列(±1% 相对或 ±0.1 绝对容差,百分数/小数互认)或
    `catalyst` 字段里找到,否则记 `code:数字` 入 reason → `ok=False`。workflow 据此打回一次
    自修(`gate('l3-lint', ...)`,一次打回上限,修复后不再二检)。

    CLI(GATE 惯例):`python -m autoresearch.scan.agents.l3_select lint <date>` 打一行 JSON。
    """
    base = Path(root) if root else Path("context/scan")
    scan_dir = base / date
    judged_path = scan_dir / "_l3_judged.json"
    if not judged_path.exists():
        return {"ok": False, "reason": f"{judged_path} 缺失"}
    picks = json.loads(judged_path.read_text(encoding="utf-8"))
    l2_by_code: dict[str, object] = {}
    l2p = scan_dir / "L2_gbdt_top200.csv"
    if l2p.exists():
        l2 = pd.read_csv(l2p, dtype={"code": str})
        l2["code"] = l2["code"].astype(str).str.zfill(6)
        l2_by_code = {r["code"]: r for _, r in l2.iterrows()}
    bad: list[str] = []
    for pick in picks:
        code = str(pick.get("code", "")).zfill(6)
        thesis = str(pick.get("thesis") or "")
        catalyst = str(pick.get("catalyst") or "")
        pool = _row_numeric_pool(pick, l2_by_code.get(code))
        for tok in _thesis_number_tokens(thesis):
            try:
                tv = float(tok)
            except ValueError:
                continue
            if _approx_in_pool(tv, pool) or tok in catalyst:
                continue
            bad.append(f"{code}:{tok}")
    if bad:
        return {"ok": False, "reason": "; ".join(bad)}
    return {"ok": True, "reason": "ok"}


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="l3_select")
    ap.add_argument("cmd", choices=["finalists", "prepare", "lint"])
    ap.add_argument("date")
    ap.add_argument("--budget", type=int, default=30)
    ap.add_argument("--root", default=None)
    a = ap.parse_args(argv)
    if a.cmd == "finalists":
        res = write_finalists(a.date, budget=a.budget, root=a.root)
        print(f"[l3_select finalists] judged {res['judged_n']} → finalist tier {res['finalist_n']}"
              f" + bench {res['bench_n']}(finalists.csv 共 {res['finalists_n']} 行,含 pinned 追加)")
    elif a.cmd == "prepare":
        res = prepare_l3_table(a.date, root=a.root)
        print(f"[l3_select prepare] codes {res['codes']} → _l3_table.md {res['table_bytes']}B")
    else:
        res = lint_judged(a.date, root=a.root)
        print(json.dumps(res, ensure_ascii=False))
        return 0 if res.get("ok") else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
