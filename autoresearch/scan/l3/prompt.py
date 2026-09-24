"""L3 compact-table, delta, terrain, and prompt preparation."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from autoresearch.common import workspace as ws
from autoresearch.scan.l3.evidence import harvest_l3_evidence, load_l3_input
from autoresearch.scan.l3.triage import pass1_meta, triage_l2_for_l3

_L3_COLS = ["code", "name", "pf", "industry", "composite", "gbdt_score",
            # pct_1d/dist_high_60(2026-08-22 批 B):L3 此前**看不见当日涨幅与距高**,却被要求
            # 替 L4 避开「涨停追高」。2026-08-21 实测 002716 当日 +10.0%、603209 rsi6 79 且距高
            # −0.9% 双双入围 → 两张卡都在 L4 早停「涨停追高」,2/9 席位(22% Opus 预算)白烧。
            "pct_1d", "pct_60d", "dist_high_60", "sector_mom", "vol_ratio", "cmf_20", "obv_mom_20",
            "main_net_ratio", "winner_rate", "rsi6", "pe", "pb", "np_yoy",
            "roe", "n_channels", "recall_channels", "news_sent", "news_head"]


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
    今日大涨(pct_1d≥9.5,仅达标才出现;=守卫⑦ 的剔除线,见 l3/merge.CHASE_1D_PCT)。
    贴顶(dist_high_60≥−2 且 pct_60d>0,仅达标才出现;⑤脆弱维输入,非硬约束)。
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

    # 今日大涨 / 贴顶(2026-08-22 批 B):守卫⑦ 会确定性剔掉「今日大涨」,pf 词让 L3 一眼看见
    # 而不是把一席浪费在必被 L4 否的票上;「贴顶」不是硬约束,是⑤脆弱维的输入。
    pct1 = _f("pct_1d")
    if pct1 is not None and pct1 >= 9.5:
        words.append("今日大涨")
    dist_hi = _f("dist_high_60")
    if dist_hi is not None and dist_hi >= -2 and (pct60 is not None and pct60 > 0):
        words.append("贴顶")
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

def _prev_l3_day(date: str, root: Path | None = None) -> Path | None:
    """最近一个有 L3 现场(L3_judged_full + L2)的更早 scan 日;无 → None。

    `root=None`(生产)→ 候选目录来自 `scan.published_days`(修 K4:run 分区下遍历
    `scan_root()` 兄弟目录只看得见本 run 自己的日期,Δ 模式因此**静默**退化成全量表)。
    显式传 `root` → 仍是该目录下的兄弟枚举(测试注入面)。**存在性判据一字未动**。
    """
    if root is None:
        from autoresearch.scan.published_days import previous_staging_dirs
        cands = [p for p in previous_staging_dirs(date)
                 if (p / "L3_judged_full.csv").exists()
                 and (p / "L2_gbdt_top200.csv").exists()]           # 已是新→旧
        return cands[0] if cands else None
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
                lane_blocks: bool = False, restrict_codes=None,
                lowturn_flag: bool = False, lowturn_cfg: dict | None = None) -> str:
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
    lowturn_flag=True(2026-08-21 低位转强波 §6.2):加 `lowturn` 低位转强旗列(谓词
    =`common.turnup.lowturn_flag` **单一事实源**,阈值 `lowturn_cfg` 覆盖 `LOWTURN_DEFAULTS`)
    + 图例(含今日旗亮只数;**旗亮票不算「下跌趋势票」,l3-rank 硬约束 B 对其不适用**);
    默认 False = 逐字 parity。证据边界见 scan_config.jsonc 的 `l3.lowturn` 块注释:
    该画像在决策尺 gap_c1_o2 上显著为负(−0.24pp t=−6.36),上线理由是「不比现任 healthy
    画像差(−0.17pp)+ 打开候选池形状」,**不是**「隔夜能赚」。
    rc_flag=True:加 `rc` 列(卖方一致预期 FY EPS 近窗修正 %,staging `consensus.csv`——
    `l4_card.fetch_consensus` 产出——在才生效)+ 图例禁则,镜像 cat_flag 接线;
    默认 False = 逐字 parity。
    pinned_flag=True:加 `pinned` 列(📌 标记该码;来自 `user_config.load_pinned(date,
    path=pinned_path)` 的 kept 集合,presence-gated——无 pinned.json / kept 全空(含全
    过期)→ 列不出现)+ 图例禁则——保送票 L1→L5 全程强留、L3 真判但不可淘汰(design
    2026-07-11-recall-gate-pinned-config-design.md §4.1);默认 False = 逐字 parity。
    pinned_path:`load_pinned` 的自定义路径(测试注入;生产默认
    `.claude/skills/scan-market/pinned.json`)。
    行业席位列(`seat`,2026-09-24 §2.3):无独立 flag 参数——`sector_seat` 布尔列本身就是
    presence-gate(`universe._inject_sector_seats_l1` 全程带下来,未开启 `l2.sector_seats`
    的日子该列不存在;镜像 `pf` 恒计算的写法,而非另开一个 `*_flag` 参数)。列存在且至少
    一行为真 → 加 `seat` 列(🏭 标记)+ 图例;图例只说明「行业席位=什么」与「B 条照常适用、
    不抬评级」,**不构成推荐**——与 pinned 图例同一纪律:被看见不等于被偏袒。
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
    # anns_d 已退役(2026-07-18,见 contracts.py):news_sent/news_head 列契约不变(冻结),
    # 但当日全票 news_n=0(端点断链/无权限)时不得留一整列 "—"/0.0 静默充数——恒检查(非
    # flag 位,同 pf 行语义指纹),整列全空才现身,避免真有公告的日子误报。
    if "news_n" in df.columns and len(df) and pd.to_numeric(
            df["news_n"], errors="coerce").fillna(0).eq(0).all():
        header += ["_(公告情感列不可用:anns_d 已退役,news_sent/news_head 本日全为缺省值,"
                   "详见 run_health)_", ""]
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
        day_dir = (root or ws.scan_root()) / date
        df["news_reg"] = [reg_hits_for_code(day_dir, c) for c in df["code"]]
        cols = [*cols, "news_reg"]
        header += ["_⚠监管旗(news_reg):近 10 日公告命中 立案/问询/关注函/处罚/违规/诉讼/"
                   "监管/证监会/交易所。旗票论点**必须显式回应监管事项**,不得无视;独立检测器,"
                   "情感列口径不变(非利空词表变更)。_", ""]
    if cat_flag and "code" in df.columns:
        catp = (root or ws.scan_root()) / date / "L3_catalyst.csv"
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
        rcp = (root or ws.scan_root()) / date / "consensus.csv"
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
    if "sector_seat" in df.columns and df["sector_seat"].fillna(False).astype(bool).any():
        df["seat"] = df["sector_seat"].map(lambda v: "🏭" if bool(v) else "")
        cols = [*cols, "seat"]
        header += ["_🏭(seat列):行业席位——当日 healthy top3 行业内的非落刀健康上涨成员,确定性直通到本表;"
                   "B 条照常适用,不因席位抬评级;它只保证「被看见」。_", ""]
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
    header.append(
        "pct_1d 当日涨幅 · dist_high_60 距 60 日高(%,≤0)。**当日 ≥9.5% 的票守卫⑦会确定性剔除**"
        "(隔夜主尺上追当日大涨整区间为负,docs/research/2026-08-08-overnight-evidence-gap.md ①)"
        " —— 选它 = 白丢一席,pf 词见「今日大涨」;「贴顶」是⑤脆弱维输入,不是硬约束。")
    if lowturn_flag:
        from autoresearch.common.turnup import LOWTURN_DEFAULTS, LOWTURN_LABEL, lowturn_label
        lt_cfg = {**LOWTURN_DEFAULTS, **(lowturn_cfg or {})}
        df["lowturn"] = df.apply(lambda r: lowturn_label(r, lt_cfg), axis=1)
        cols = [*cols, "lowturn"]
        n_lt = int((df["lowturn"] == LOWTURN_LABEL).sum())
        header.append(
            f"lowturn 低位转强(确定性旗):距 60 日高 ≥{abs(lt_cfg['max_dist_high_60']):g}% 且 60 日涨幅 "
            f"<{lt_cfg['max_pct_60d']:g} ∧ 站回 MA20 且 MA5>MA10 ∧ 近 5 日为正 ∧ vol_ratio_20≥"
            f"{lt_cfg['min_vol_ratio_20']:g} ∧ 主力或 CMF 转正,且非健康上涨。**旗亮票不算「下跌趋势票」,"
            f"硬约束 B 不适用**;仍须过②资金真与⑥兑现机制,thesis 写明『低位转强』并答 D+1 买家。"
            f"今日旗亮 {n_lt} 只。")
    if shuffle_seed is not None:
        df = df.sample(frac=1, random_state=int(shuffle_seed)).reset_index(drop=True)
    table = _render_lane_blocks(df, cols) if lane_blocks else compact_table(df, cols=cols)
    body = "\n".join([*header, table]) if header else table
    if sector_terrain:                      # Phase 3:全行业地形段前置(默认关 = 逐字 parity)
        try:
            from autoresearch.sector.pack import sector_terrain_md
            terr = sector_terrain_md((root or ws.scan_root()) / date, top200_only=True)
            if terr:
                body = terr + "\n\n" + body
        except Exception:  # noqa: BLE001 — 地形可选,缺 staging 不挡表
            pass
    return body

def prepare_l3_table(date: str, root: Path | None = None, delta: bool = True,
                     do_harvest: bool = True, pinned_path: Path | str | None = None,
                     two_pass: bool | None = None) -> dict:
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

    2026-08-21(用户裁定「整个 learning 层退役」):原 `calib_blocks` 参数与它注入的两个
    校准块(T+1 快环校准 / 经验注回)已删 —— 那正是「把历史账本学到的东西塞回今天的判断
    prompt」的闭环回注腿。L3 现在只看当日证据表,不再吃任何账本派生的先验。
    """
    base = Path(root) if root else ws.scan_root()
    scan_dir = base / date
    l2 = pd.read_csv(scan_dir / "L2_gbdt_top200.csv", dtype={"code": str})
    codes = l2["code"].astype(str).str.zfill(6).tolist()
    if do_harvest:
        harvest_l3_evidence(date, codes, root=base)
        from autoresearch.scan.agents.l3_news import harvest_l3_news
        harvest_l3_news(date, codes, root=base)   # anns_d 退役 → 一次性 stderr 告警(不逐日重试)

    l3_cfg: dict = {}
    lt_cfg: dict = {}
    lowturn_on = False
    if two_pass is not False:              # 显式 False = 纯回滚杆,连 load_user_config 都不碰
        from autoresearch.scan.user_config import load_user_config
        l3_cfg = load_user_config().get("l3") or {}
        if two_pass is None:
            two_pass = bool(l3_cfg.get("two_pass", True))
        lt_cfg = dict(l3_cfg.get("lowturn") or {})
        lowturn_on = bool(lt_cfg.get("enabled", False))

    restrict_codes = None
    pass1_header = ""
    pass1_counts: dict = {}
    if two_pass:
        pass1_target = int(l3_cfg.get("pass1_target", 60))
        df_full = load_l3_input(date, root=base)
        # composite 席位强留(2026-08-26 §3 路A):守卫⑨ 会把这几只强制送进 finalists,
        # pass1 必须先让 l3-rank 判到它们 —— 否则 finalists 里那几行只有 L2 展示字段、空 thesis
        # (pinned 当年踩过的同一个坑:「保送 ≠ 免判,更 ≠ 判了不要」)。
        from autoresearch.scan.l3.merge import composite_seat_cfg
        _seat_on, _seat_m = composite_seat_cfg(l3_cfg and {"l3": l3_cfg} or None)
        kept, cut = triage_l2_for_l3(
            df_full, target=pass1_target,
            lowturn_cap=int(lt_cfg.get("pass1_cap", 8)) if lowturn_on else 0,
            lowturn_cfg=lt_cfg,
            composite_seat_m=_seat_m if _seat_on else 0)
        cut.to_csv(scan_dir / "_l3_pass1_cut.csv", index=False)
        # `_l3_pass1_kept.csv` + meta(design 2026-08-03 §4.4):cut 只说"谁没进",
        # 而 tier-1 反事实要的是"进来的这 K 只各自凭什么进" —— 没有 selection_reason
        # 就无法在同一 choice set 内构造 lane/sector 匹配的 baseline。
        kept.to_csv(scan_dir / "_l3_pass1_kept.csv", index=False)
        meta = pass1_meta(df_full, kept, cut, pass1_target)
        (scan_dir / "_l3_pass1_meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        restrict_codes = set(kept["code"].astype(str).str.zfill(6))
        pass1_header = f"_pass1 分诊 {len(df_full)}→{len(kept)}(影子 `_l3_pass1_cut.csv`)_"
        pass1_counts = {"pass1_kept": len(kept), "pass1_cut": len(cut),
                        "pass1_meta": meta}

    # root 而非 base:显式 root(测试注入)照旧透传;生产的 root=None 必须**保持 None**,
    # 否则 `_prev_l3_day` 收到 `ws.scan_root()` 这个显式目录 → 又回到 K4 的兄弟目录枚举。
    # 其余读点都是 `root or ws.scan_root()`,与传 base 逐字等价。
    md = l3_table_md(date, root=Path(root) if root else None,
                     delta=delta, dist_flag=True, reg_flag=True,
                     cat_flag=True, sector_terrain=True, misread_flag=True,
                     pinned_flag=True, pinned_path=pinned_path, lane_blocks=True,
                     restrict_codes=restrict_codes,
                     lowturn_flag=lowturn_on, lowturn_cfg=lt_cfg)
    lowturn_counts: dict = {}
    if lowturn_on:
        # 旗亮只数**重算**,不从 markdown 里数字符串(图例/表头会误伤计数)
        from autoresearch.common.turnup import LOWTURN_DEFAULTS, lowturn_mask
        base_df = kept if two_pass else load_l3_input(date, root=base)
        lowturn_counts = {"lowturn_n": int(lowturn_mask(base_df, {**LOWTURN_DEFAULTS, **lt_cfg}).sum())}
    if pass1_header:
        md = pass1_header + "\n\n" + md

    (scan_dir / "_l3_table.md").write_text(md, encoding="utf-8")
    return {"codes": len(codes), "table_bytes": len(md), **pass1_counts, **lowturn_counts}
