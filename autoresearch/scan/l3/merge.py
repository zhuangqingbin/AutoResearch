"""L3 finalist merge, pinned injection, and artifact publication."""
from __future__ import annotations

import contextlib
import json
import math
from pathlib import Path

import pandas as pd

from autoresearch.common import workspace as ws

# 当日大涨阈(2026-08-22 批 B)。定义与 `docs/research/2026-08-08-overnight-evidence-gap.md` ①
# 逐字一致(当日 ≥9.5%),**不做板别感知**:创业板 +9.5% 不是涨停但同样是追高,主尺量的也不是
# 「打板当晚」而是第二个夜晚。读数:隔夜主尺最高桶 −0.96% [−1.06, −0.84] **整区间同号**。
# 回滚杆 = 改成 `float("inf")`(守卫⑦静默,列与 pf 词保留无害)。
CHASE_1D_PCT = 9.5
# L3 行业帽(2026-08-22 批 C):同 `sector` 至多几席。2026-08-21 实测贵金属(12 只成分)拿 4 席
# + 下游饰品 1 席 = 5/9,而 L3/merge 此前一个帽也没有(L2 有 sector_cap 20%)。
# 回滚杆 = 改成 99。
L3_SECTOR_CAP = 3
# healthy 配额分数(2026-08-22 批 (a),用户裁定「三处强制降为不强制」):守卫④ 的 target =
# ceil(n × HEALTHY_QUOTA_FRAC)。**0.0 = ④ 不动作**,且 ⑤⑥ 的 protect_lanes 与 ⑧ 的配额下限
# 同步不再含 healthy(三处由同一个常量驱动,见 `_healthy_quota` / `_lane_quota_floor`)。
# 证据:edge 普查(docs/research/2026-08-22-edge-census.md)—— healthy 画像三把尺全负
# (L1·healthy 隔夜 −0.37pp t=−5.58、fwd_10 −4.76pp;L3·lane·healthy −0.38 t=−3.56),
# 而守卫④ 曾强制把它凑到 finalist 的 1/3。回滚杆 = 改回 1/3(一行恢复 ④⑤⑥⑧ 四处旧行为)。
HEALTHY_QUOTA_FRAC = 0.0

# ── composite 席位(2026-08-26 §3 路A · 守卫⑨)────────────────────────────────
# **BUY 的所有权从判断层搬到证据层**。E6 此前只在 L3 finalist 里挑,而 finalist 这一族在
# 隔夜主尺上 40 日相对超额 **−0.27pp(t=−3.94)= 显著为负**;全表唯一的正证据是确定性
# `composite`(+0.14pp,t=3.05),L2 菜单内前 20/50 名同样为正(+0.14 t=1.96 / +0.17 t=2.78,
# 42 个扫描日实测,`docs/research/2026-08-26-buy-owner-spikes/`)。于是:每天把当日 L2 菜单
# 里 composite 最高的 M 只**强制送进 finalists**,让它们出卡、进 E6 候选池;判断层的活儿
# 改成「否决」——那才是它被证明会做的事(`STAGES.md` §二:已证 edge 在拒绝不在挑选)。
#
# **它承诺什么**:每天一个证据链自洽、与卡面不打架、可事后计量的 BUY。
# **它不承诺什么**:隔夜赚钱。+0.14~0.17pp 与一次 A 股往返成本同量级。
#
# 席位不受守卫②lt55/③cap 约束(与 📌 保送同级:它不是排序的产物,是证据层的直通车),
# 但**要过**追高剔除(pct_1d≥CHASE_1D_PCT)与 ST/📌 排除。
# 回滚杆:`scan_config.jsonc` 的 `l3.composite_seat.enabled=false`(一行,逐字 parity)。
COMPOSITE_SEAT_M = 3
COMPOSITE_SEAT_GUARD = "composite_seat"


def composite_seat_cfg(cfg: dict | None = None) -> tuple[bool, int]:
    """`(enabled, m)` —— 生效点唯一解析口。配置层故障 → 默认开、M=3(与常量一致)。"""
    from autoresearch.scan.user_config import knob
    block = knob("l3", "composite_seat", None, {}, cfg) or {}
    if not isinstance(block, dict):
        return True, COMPOSITE_SEAT_M
    enabled = block.get("enabled", True)
    m = block.get("m", COMPOSITE_SEAT_M)
    try:
        m = max(0, int(m))
    except (TypeError, ValueError):
        m = COMPOSITE_SEAT_M
    return bool(enabled), m


def _is_st(name: object) -> bool:
    s = str(name or "").upper().replace(" ", "")
    return "ST" in s or "退" in s


def pick_composite_seats(l2: pd.DataFrame | None, m: int,
                         exclude: set[str] | None = None) -> list[dict]:
    """当日 L2 菜单里 composite 最高的 ≤m 只(确定性;见 `COMPOSITE_SEAT_M` 旁注)。

    排序键 `gbdt_score`(= sector-neutral composite,实测与 `composite` 列逐值相等),缺列
    退化 `composite`;两列都缺 → 空(presence-gated,parity)。
    剔:📌 保送(它们走自己的直通车)/ ST·退 / 当日涨幅 ≥`CHASE_1D_PCT`(追高在隔夜尺上
    四年逐年为负)/ 调用方给的 `exclude`(通常是已在 finalists 的码 —— 已经在场就不必再占席)。
    """
    if l2 is None or not len(l2):
        return []
    col = "gbdt_score" if "gbdt_score" in l2.columns else (
        "composite" if "composite" in l2.columns else None)
    if col is None or m <= 0:
        return []
    d = l2.copy()
    d["code"] = d["code"].astype(str).str.zfill(6)
    score = pd.to_numeric(d[col], errors="coerce")
    keep = score.notna()
    if "pinned" in d.columns:
        keep &= ~d["pinned"].map(lambda v: bool(v) if pd.notna(v) else False)
    if "name" in d.columns:
        keep &= ~d["name"].map(_is_st)
    if "pct_1d" in d.columns:
        keep &= ~(pd.to_numeric(d["pct_1d"], errors="coerce") >= CHASE_1D_PCT)
    if exclude:
        keep &= ~d["code"].isin({str(c).zfill(6) for c in exclude})
    d = d.loc[keep].assign(_score=score.loc[keep])
    d = d.sort_values(["_score", "code"], ascending=[False, True]).head(int(m))
    out = []
    for _, r in d.iterrows():
        out.append({"code": r["code"], "name": r.get("name", ""),
                    "sector": r.get("industry", r.get("sector", "")),
                    "score": float(r["_score"])})
    return out


def _healthy_quota(n: int) -> int:
    """守卫④ 的 healthy 席位目标(ceil(n × frac));frac=0 → 0 = 不动作。"""
    return math.ceil(n * HEALTHY_QUOTA_FRAC) if (n and HEALTHY_QUOTA_FRAC > 0) else 0


def _guarded_lanes_before(step: str) -> set[str]:
    """在 `step`(⑤ trend / ⑥ lowturn)之前已配置好配额、须受保护的 lane 集。
    healthy 只在 HEALTHY_QUOTA_FRAC>0 时算(否则没有「④ 刚满足的硬约束」可保护)。"""
    lanes: set[str] = {"healthy"} if HEALTHY_QUOTA_FRAC > 0 else set()
    if step == "lowturn":
        lanes.add("trend")
    return lanes


def _drop_and_backfill(m: pd.DataFrame, conv: pd.Series, fin_idx: set, victims: list,
                       guard_name: str, backfill_guard: str, *,
                       qualify_conv: float = 55.0, sector_cap: int | None = None) -> set:
    """守卫⑦/⑧共用:剔掉 `victims` → 从 bench 按 conviction 降序**回填到原席位数**。

    与 `_swap_lane_quota`(按 lane 凑配额)的区别:那个是「缺某类就换进来」,这个是
    「这几只不该在场,踢掉但席位不能白丢」。用户 2026-08-22 裁定:**剔除并回填**——E6 候选池
    宽度不因剔除缩水;回填只收 `conviction >= qualify_conv`,**够格不足则不硬凑**(同④⑤⑥纪律)。

    `sector_cap` 给定时,回填还须保证补进来的票不把它自己所在 sector 顶破帽(守卫⑧用)。
    bench 池排除 `guard` 已被本轮标记的行(不把刚踢出去的再捡回来)。
    """
    if not victims:
        return fin_idx
    fin_idx = set(fin_idx)
    for i in victims:
        fin_idx.discard(i)
        m.loc[i, "guard"] = guard_name          # 无条件覆写:真正原因就是本守卫
    need = len(victims)
    sector = m["sector"].astype(str) if "sector" in m.columns else None
    # 回填池:bench 里够格的行。**排除失格 guard**(chase_1d 追高 / lt55 低确信 / dup 重复)——
    # 被 `cap` 截尾的行**不排除**:它正是「conviction 次高、只因名额满才没进」的那批,是最该
    # 补位的人;补进来时 guard 会被改写成 `backfill_guard`(真实原因)。
    _DISQUALIFIED = {"chase_1d", "lt55", "dup"}
    pool = [i for i in m.index
            if i not in fin_idx and i not in victims
            and str(m.loc[i, "guard"] or "") not in _DISQUALIFIED
            and conv.loc[i] >= qualify_conv]
    pool.sort(key=lambda i: conv.loc[i], reverse=True)
    for cand in pool:
        if need <= 0:
            break
        if sector_cap is not None and sector is not None:
            cur = sum(1 for i in fin_idx if sector.loc[i] == sector.loc[cand])
            if cur >= sector_cap:
                continue                         # 补进去就破帽 → 跳过,不制造新违规
        fin_idx.add(cand)
        m.loc[cand, "guard"] = backfill_guard
        need -= 1
    return fin_idx                               # 够格不足 → 席位少几个,不硬凑


def _lane_quota_floor(m: pd.DataFrame, fin_idx: set) -> dict:
    """当前候选集应满足的 lane 配额下限(守卫④⑤⑥ 刚配置好的那些)。

    守卫⑧ 剔票时要保护它们**不被击穿**——但保护的是「配额」不是「每一行」:2026-08-21 贵金属
    4 席里 3 席 lane=healthy,若整个 healthy lane 免剔,行业帽永远咬不动。
    """
    n = len(fin_idx)
    floors = {"trend": 2, "lowturn": 1}
    if HEALTHY_QUOTA_FRAC > 0:                       # healthy 配额关了就没有「配额」可保护
        floors["healthy"] = _healthy_quota(n)
    return floors


def _apply_sector_cap(m: pd.DataFrame, conv: pd.Series, fin_idx: set, cap: int) -> set:
    """守卫⑧:同 `sector` >cap 席 → 剔最弱 + 从 bench 回填异行业(见调用点注释)。"""
    if "sector" not in m.columns or cap <= 0:
        return fin_idx
    fin_idx = set(fin_idx)
    sector = m["sector"].astype(str)
    lane = m["lane"].astype(str) if "lane" in m.columns else pd.Series("", index=m.index)
    floors = _lane_quota_floor(m, fin_idx)
    by_sector: dict[str, list] = {}
    for i in sorted(fin_idx):
        by_sector.setdefault(sector.loc[i], []).append(i)

    victims: list = []
    for _sec, group in sorted(by_sector.items()):
        over = len(group) - cap
        if over <= 0:
            continue
        removable = [i for i in group if conv.loc[i] < 75]     # ins75 行不可剔
        removable.sort(key=lambda i: conv.loc[i])       # 最弱先剔
        for i in removable:
            if over <= 0:
                break
            ln = lane.loc[i]
            if ln in floors:                            # lane 配额不可被击穿
                have = sum(1 for j in fin_idx if lane.loc[j] == ln and j not in victims)
                if have <= floors[ln]:
                    continue
            victims.append(i)
            over -= 1
    return _drop_and_backfill(m, conv, fin_idx, victims, "sector_cap", "sector_backfill",
                              sector_cap=cap)


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
    ④ **健康比例守卫**(比例制,`ceil(n × HEALTHY_QUOTA_FRAC)`;**2026-08-22 起 frac=0 = 不动作**,
       用户裁定「healthy 三处强制降为不强制」,证据见 edge 普查):候选集里 `lane=="healthy"`
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

    ⑥ **lowturn soft 1 席**(2026-08-21 低位转强波 §6.4):同④⑤机制(`_swap_lane_quota`),
       `lane=="lowturn"`、`target=1`(固定)、`guard="lowturn_quota"`、**`qualify_conv=55`**
       (与守卫②同阈,不用④⑤的默认 65 —— 低位转强票 conviction 天然偏低,65 会让本守卫恒
       空转,那是"探针死了也像活着"的同族)、`protect_lanes={"healthy","trend"}`(soft 不得
       吃掉④刚满足的健康硬约束,也不该击穿⑤)。prompt(l3-rank 硬约束 G)允许至多 2 席,
       确定性层只兜底 1 席;有够格候选才凑,无则 0。

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
    for c in ("conviction", "fragility", "pct_60d", "pct_1d"):
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

    # 守卫⑦ chase_1d(2026-08-22 批 B):当日涨幅 ≥CHASE_1D_PCT 的票不得 finalist,**剔 + 回填**。
    # 2026-08-21 实测:002716 湖南白银当日 +10.0%、603209 双双入围 → 两张卡都在 L4 早停
    # 「涨停追高」,2/9 席位(22% 的 L4 Opus 预算)花在 L4 按规则必否的票上。
    # **ins75 行也剔**——「高确信误杀保险」保的是「L3 判高分却没标 finalist」,不是「追高豁免」。
    # 放在 cap 之后:剔掉的席位由 bench 回填(用户 2026-08-22 裁定「剔除并回填」,E6 候选池
    # 宽度不因剔除缩水),故必须在「已经截到 cap」的集合上做,否则没有「席位数」可言。
    # 列缺 → victims 空 → 整段 no-op(逐字 parity)。
    if "pct_1d" in m.columns:
        _p1 = pd.to_numeric(m["pct_1d"], errors="coerce")
        # 回填也认行业帽:否则⑦ 补进来的票可能正好把某行业顶到 4 席,⑧ 随即再把它剔掉 ——
        # 净效果是白丢一席。2026-08-21 真数据实测到这个来回:⑦ 剔 002716(贵金属)后补入
        # 001337 四川黄金(**也是贵金属**),⑧ 再剔,席位 9→8。帽是全局不变量,越早认越省事。
        fin_idx = _drop_and_backfill(
            m, conv, fin_idx, [i for i in sorted(fin_idx) if _p1.loc[i] >= CHASE_1D_PCT],
            "chase_1d", "chase_backfill", sector_cap=L3_SECTOR_CAP)

    n = len(fin_idx)
    # 守卫④ healthy 配额:2026-08-22 起 HEALTHY_QUOTA_FRAC=0 → target 0 → 不动作(回滚改常量)。
    fin_idx = _swap_lane_quota(m, conv, fin_idx, "healthy",             # 守卫④
                               _healthy_quota(n), "healthy_quota")
    fin_idx = _swap_lane_quota(m, conv, fin_idx, "trend", 2, "trend_quota",   # 守卫⑤
                               protect_lanes=_guarded_lanes_before("trend"))   # I-2:不可换出已配置的配额行
    fin_idx = _swap_lane_quota(m, conv, fin_idx, "lowturn", 1, "lowturn_quota",   # 守卫⑥
                               qualify_conv=55.0, protect_lanes=_guarded_lanes_before("lowturn"))

    # 守卫⑧ sector_cap(2026-08-22 批 C):同 `sector` 至多 L3_SECTOR_CAP 席,超出剔最弱 + 回填异行业。
    # 2026-08-21:贵金属(12 只成分的申万二级)拿 4 席 + 下游饰品 1 席 = 5/9,而 L3/merge 此前
    # **一个行业帽也没有**(L2 有 sector_cap 20%)。L3 自己在每只的 risk 段都写了「同一动量被
    # 多路重复计数」,却没有任何机制阻止它;E6 于是在「金价 beta 里挑最不差的」。
    # 可剔判据:conviction<75(ins75 保护)∧ 剔掉后该行 lane 的配额仍满足 —— 保护的是**配额**
    # 不是每一行(当日贵金属 4 席里 3 席 lane=healthy,整 lane 免剔则帽子永远不咬)。
    # 回填还须不把补进来的票自己所在 sector 顶破帽。`sector` 列缺 → no-op(parity)。
    fin_idx = _apply_sector_cap(m, conv, fin_idx, L3_SECTOR_CAP)

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

def inject_composite_seats(fin: pd.DataFrame, seats: list[dict],
                           judged: pd.DataFrame | None = None) -> pd.DataFrame:
    """把 composite 席位注入 finalists(守卫⑨;结构镜像 `_inject_pinned_finalists`)。

    - 已在 `fin`(L3 自己也选了它)→ **不重复行**,只打 `guard="composite_seat"` 留痕
      (L3 的 thesis/conviction 原样保留 —— 判断记录在案,不因证据层直通而抹掉);
    - 不在 `fin` 但**在 judged 里**(L3 判过、落 bench)→ 从 judged 取整行带过来
      (thesis/mechanism/risk/catalyst/conviction 全部保留),再打 guard;
      **这是 pinned 那次事故的同款教训**:只查 L2 会把 L3 的判断整段丢掉,下游 L4 prompt
      就会告诉卡片「本票无 L3 前提清单」,卡只好自己从 L1 重建。
    - 两处都没有(pass1 切了 / l3-rank 没判它)→ 用 L2 行的展示字段建占位行,`data_missing=False`
      (name/sector 是真数据),`conviction` 留空(**不编**:证据层直通不代表判断层给过分)。

    `seats` 空 → 原样返回(presence-gated parity)。
    """
    if not seats:
        return fin
    out = fin.copy()
    if "code" in out.columns:
        out["code"] = out["code"].astype(str).str.zfill(6)
    for col, default in (("lane", ""), ("guard", ""), ("data_missing", False)):
        if col not in out.columns:
            out[col] = default
    have = set(out["code"]) if "code" in out.columns else set()
    judged_z = None
    if judged is not None and not judged.empty and "code" in judged.columns:
        judged_z = judged.assign(code=judged["code"].astype(str).str.zfill(6))

    new_rows: list[pd.DataFrame] = []
    for seat in seats:
        code = str(seat["code"]).zfill(6)
        if code in have:
            m = out["code"] == code
            # guard 留痕但**不覆盖**已有的更具体标记(如 lowturn_quota/chase_backfill):
            # 那些说的是「它怎么进来的」,而席位说的是「它另外还占了一个证据席」。
            out.loc[m & (out["guard"].fillna("") == ""), "guard"] = COMPOSITE_SEAT_GUARD
            continue
        row: dict = {"code": code, "ticker": code, "lane": "composite",
                     "guard": COMPOSITE_SEAT_GUARD, "data_missing": False}
        hit = judged_z[judged_z["code"] == code] if judged_z is not None else None
        if hit is not None and len(hit):
            r0 = hit.iloc[0].to_dict()
            r0.pop("finalist", None)
            row = {**{k: v for k, v in r0.items() if pd.notna(v)}, **row}
        else:
            row["name"] = seat.get("name", "")
            row["sector"] = seat.get("sector", "")
        new_rows.append(pd.DataFrame([row]))
    if new_rows:
        out = pd.concat([out, *new_rows], ignore_index=True, sort=False)
    return out


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
        `L3_judged_full.csv`(该文件仍留 L3 agent 自己判的原始 lane,如 trend/value,
        与此处 finalists 层的"pinned"标记是两回事,故意不合并;L1 层 `recall_channels`
        的 `"pinned"` 标记(universe._inject_pinned_l1)又是第三处,同样与本函数无关
        ——三层"pinned"标记互相独立、各自服务各自的下游);
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
                    pinned_path: Path | str | None = None,
                    judged_path: Path | str | None = None) -> dict:
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
    base = Path(root) if root else ws.scan_root()
    scan_dir = base / date
    source = Path(judged_path) if judged_path is not None else scan_dir / "_l3_judged.json"
    picks = json.loads(source.read_text(encoding="utf-8"))
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
        # pct_1d 回填(2026-08-22 批 B,守卫⑦ 的输入):**无条件** merge —— agent 的 judged
        # schema 里没有这一列,不像 pct_60d 那样存在「v2 旧 judged 自带」的情形,故不加
        # `not in jd.columns` 前置条件;列缺 → 守卫⑦ no-op(parity),那正是 L2 表也缺时的行为。
        if "pct_1d" not in jd.columns and "pct_1d" in l2.columns:
            jd = jd.merge(l2[["code", "pct_1d"]], on="code", how="left")
    jd.to_csv(scan_dir / "L3_judged_full.csv", index=False)       # 全量判断(assemble/trace)

    from autoresearch.scan.user_config import load_user_config
    finalist_max = int((load_user_config().get("l3") or {}).get("finalist_max", 10))
    fin, bench = merge_l3_finalists_v3(jd, budget=budget, finalist_max=finalist_max)
    finalist_n = int(len(fin))

    # 守卫⑨ composite 席位(2026-08-26 §3 路A):在 v3 全部守卫**之后**、pinned 注入**之前**
    # 注入 —— 与 📌 同级的直通车,不占 finalist 名额、不参与 cap 截尾。放在 pinned 之前是为了
    # 让 pinned 的「已在场就只改判 lane」逻辑仍能覆盖同码情形(📌 优先级更高)。
    seats: list[dict] = []
    seat_enabled, seat_m = composite_seat_cfg()
    if seat_enabled and seat_m > 0:
        seats = pick_composite_seats(l2, seat_m,
                                     exclude={str(c) for c in fin.get("code", [])})
        fin = inject_composite_seats(fin, seats, judged=jd)
        if len(seats):
            bench = bench[~bench["code"].astype(str).isin({s["code"] for s in seats})
                          ].reset_index(drop=True)
    seat_n = int(len(seats))

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
    with contextlib.suppress(Exception):
        from autoresearch.scan.stock_stage import record_l3_results

        record_l3_results(scan_dir)
    return {"judged_n": int(len(jd)), "finalists_n": int(len(fin)),
            "finalist_n": finalist_n, "bench_n": bench_n,
            # 守卫⑨ 的**会变的量**:席位这条腿死了也像活着(同族配方:自动的腿必须有一个
            # 会变的量做断言)。0 = 关了、或当日 L2 表缺 composite 列、或全被追高/ST 剔光。
            "composite_seat_n": seat_n,
            "composite_seats": [s["code"] for s in seats]}
