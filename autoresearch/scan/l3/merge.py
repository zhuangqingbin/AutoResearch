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

CONV_FORCE_IN = 75.0          # 守卫① ins75:确信 ≥ 此值强制补入
CONV_MIN = 55.0               # 守卫② lt55:确信 < 此值禁止 finalist


def guard_defaults() -> dict:
    """守卫阈的内建缺省 = 上面的模块常量(调用时现读,改常量仍是回滚杆)。"""
    return {"chase_1d_pct": CHASE_1D_PCT, "sector_cap": L3_SECTOR_CAP,
            "healthy_quota_frac": HEALTHY_QUOTA_FRAC, "conv_force_in": CONV_FORCE_IN, "conv_min": CONV_MIN,
            "qualify_conv": {"backfill": 55.0, "lane": 65.0, "lowturn": 55.0},
            "lane_floors": {"trend": 2, "lowturn": 1}}


def guards_cfg(cfg: dict | None = None) -> dict:
    """`scan_config.l3.guards` → 守卫阈(缺键 = guard_defaults(),逐字 parity;两个子字典各深合并一层)。"""
    from autoresearch.scan.user_config import knob
    user = knob("l3", "guards", None, {}, cfg) or {}
    if not isinstance(user, dict):
        user = {}
    base = guard_defaults()
    out = {**base, **{k: v for k, v in user.items() if not isinstance(v, dict)}}
    for k in ("qualify_conv", "lane_floors"):
        out[k] = {**base[k], **(user.get(k) or {})}
    return out

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
# 守卫⑩(2026-09-26 用户需求 l4.max_cards):非 📌 行(含 composite 席位)总数的截尾标记。
MAX_CARDS_GUARD = "max_cards"


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


def composite_seat_exclude_knife(cfg: dict | None = None) -> bool:
    """`l3.composite_seat.exclude_knife`:席位是否剔落刀(缺省 True = 现行为)。"""
    from autoresearch.scan.user_config import knob
    block = knob("l3", "composite_seat", None, {}, cfg) or {}
    return bool(block.get("exclude_knife", True)) if isinstance(block, dict) else True


def _is_st(name: object) -> bool:
    s = str(name or "").upper().replace(" ", "")
    return "ST" in s or "退" in s


def pick_composite_seats(l2: pd.DataFrame | None, m: int,
                         exclude: set[str] | None = None) -> list[dict]:
    """当日 L2 菜单里 composite 最高的 ≤m 只(确定性;见 `COMPOSITE_SEAT_M` 旁注)。

    排序键 `gbdt_score`(= sector-neutral composite,实测与 `composite` 列逐值相等),缺列
    退化 `composite`;两列都缺 → 空(presence-gated,parity)。
    剔:📌 保送(它们走自己的直通车)/ ST·退 / 当日涨幅 ≥`CHASE_1D_PCT`(追高在隔夜尺上
    四年逐年为负)/ 落刀(pct_60d<−20,`common.scoring.falling_knife_mask`)/ 调用方给的
    `exclude`(通常是已在 finalists 的码 —— 已经在场就不必再占席)。
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
        keep &= ~(pd.to_numeric(d["pct_1d"], errors="coerce") >= guards_cfg()["chase_1d_pct"])
    if composite_seat_exclude_knife():                   # 席位不接刀(l3.composite_seat.exclude_knife)
        from autoresearch.common.scoring import falling_knife_mask
        knife = falling_knife_mask(d)
        if knife is not None:
            keep &= ~knife.fillna(False)
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


def _healthy_quota(n: int, frac: float | None = None) -> int:
    """守卫④ 的 healthy 席位目标(ceil(n × frac));frac=0 → 0 = 不动作。"""
    frac = guards_cfg()["healthy_quota_frac"] if frac is None else frac
    return math.ceil(n * frac) if (n and frac > 0) else 0


def _guarded_lanes_before(step: str, frac: float | None = None) -> set[str]:
    """在 `step`(⑤ trend / ⑥ lowturn)之前已配置好配额、须受保护的 lane 集。
    healthy 只在 healthy_quota_frac>0 时算(否则没有「④ 刚满足的硬约束」可保护)。"""
    frac = guards_cfg()["healthy_quota_frac"] if frac is None else frac
    lanes: set[str] = {"healthy"} if frac > 0 else set()
    if step == "lowturn":
        lanes.add("trend")
    return lanes


_DISQUALIFIED_GUARDS = frozenset({
    "chase_1d", "lt55", "dup", "structural_veto", "pinned_research", "untradable", "invalid_identity",
})


def _present(value) -> bool:
    return isinstance(value, (list, dict)) or bool(pd.notna(value))


def _is_pinned(row) -> bool:
    value = row.get("pinned")
    return str(row.get("lane", "")) == "pinned" or (
        _present(value) and (value is True or str(value).lower() in {"true", "1"})
    )


def _exclusion_reason(row, g: dict) -> str:
    """Hard exclusions before seats; missing market fields retain existing quality gates."""
    from autoresearch.scan.l3.validation import l3_veto_status

    code = str(row.get("code", "")).strip()
    if not code or code.lower() in {"none", "nan", "<na>"}:
        return "invalid_identity"
    # New producer identities are strict; legacy helper fixtures/old rows keep their contract.
    if row.get("schema_version") == 2 and (len(code) != 6 or not code.isdigit()):
        return "invalid_identity"
    name = row.get("name")
    if _present(name) and _is_st(name):
        return "untradable"
    guard = row.get("guard", "")
    if _present(guard) and str(guard) in _DISQUALIFIED_GUARDS:
        return str(guard)
    pct = pd.to_numeric(row.get("pct_1d"), errors="coerce")
    if pd.notna(pct) and pct >= g["chase_1d_pct"]:
        return "chase_1d"
    if l3_veto_status(row) == "VETO":
        return "structural_veto"
    if _is_pinned(row):
        return "pinned_research"
    return ""


def candidate_eligible(row, *, qualify_conv: float | None = None, g: dict | None = None) -> bool:
    """One admission predicate for force-in, all replacements, and seat hard exclusions.

    Composite research seats retain their existing conviction exemption (`None`); they
    never bypass identity, tradability, chasing, B/E vetoes or the holding boundary.
    """
    g = guards_cfg() if g is None else g
    if _exclusion_reason(row, g):
        return False
    if qualify_conv is None:
        return True
    conviction = pd.to_numeric(row.get("conviction"), errors="coerce")
    return bool(pd.notna(conviction) and conviction >= qualify_conv)


def _drop_and_backfill(m: pd.DataFrame, conv: pd.Series, fin_idx: set, victims: list,
                       guard_name: str, backfill_guard: str, *,
                       qualify_conv: float | None = None, sector_cap: int | None = None) -> set:
    """守卫⑦/⑧共用:剔掉 `victims` → 从 bench 按 conviction 降序**回填到原席位数**。

    与 `_swap_lane_quota`(按 lane 凑配额)的区别:那个是「缺某类就换进来」,这个是
    「这几只不该在场,踢掉但席位不能白丢」。用户 2026-08-22 裁定:**剔除并回填**——E6 候选池
    宽度不因剔除缩水;回填只收 `conviction >= qualify_conv`,**够格不足则不硬凑**(同④⑤⑥纪律)。

    `sector_cap` 给定时,回填还须保证补进来的票不把它自己所在 sector 顶破帽(守卫⑧用)。
    bench 池排除 `guard` 已被本轮标记的行(不把刚踢出去的再捡回来)。
    """
    if qualify_conv is None:
        qualify_conv = guards_cfg()["qualify_conv"]["backfill"]
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
    g = guards_cfg()
    pool = [i for i in m.index
            if i not in fin_idx and i not in victims
            and candidate_eligible(m.loc[i], qualify_conv=qualify_conv, g=g)]
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
    g = guards_cfg()
    floors = dict(g["lane_floors"])
    if g["healthy_quota_frac"] > 0:                  # healthy 配额关了就没有「配额」可保护
        floors["healthy"] = _healthy_quota(n, g["healthy_quota_frac"])
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
        removable = [i for i in group if conv.loc[i] < guards_cfg()["conv_force_in"]]
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
    fin_idx = _drop_and_backfill(m, conv, fin_idx, victims, "sector_cap", "sector_backfill",
                                sector_cap=cap)
    # This is a soft diversity constraint: explain every surviving over-cap sector.
    for sec in by_sector:
        remaining = [i for i in fin_idx if sector.loc[i] == sec]
        if len(remaining) <= cap:
            continue
        for i in remaining:
            reasons = []
            if conv.loc[i] >= guards_cfg()["conv_force_in"]:
                reasons.append("conviction 保险保护")
            if lane.loc[i] in floors:
                have = sum(lane.loc[j] == lane.loc[i] for j in fin_idx)
                if have <= floors[lane.loc[i]]:
                    reasons.append(f"lane={lane.loc[i]} 软配额保护")
            m.loc[i, "sector_cap_exception"] = (
                f"{sec} {len(remaining)}>{cap}: " + "；".join(reasons)
            )
    return fin_idx


def _swap_lane_quota(m: pd.DataFrame, conv: pd.Series, fin_idx: set, lane_val: str,
                     target: int, guard_name: str, qualify_conv: float | None = None,
                     protect_lanes: set[str] | None = None, force_in: float | None = None) -> set:
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
    g = guards_cfg()
    qualify_conv = g["qualify_conv"]["lane"] if qualify_conv is None else qualify_conv
    force_in = g["conv_force_in"] if force_in is None else force_in
    lane = m["lane"].astype(str)
    have = sum(1 for i in fin_idx if lane.loc[i] == lane_val)
    deficit = target - have
    if deficit <= 0:
        return fin_idx
    bench_pool = [i for i in m.index if i not in fin_idx
                 and lane.loc[i] == lane_val
                 and candidate_eligible(m.loc[i], qualify_conv=qualify_conv, g=g)]
    bench_pool.sort(key=lambda i: conv.loc[i], reverse=True)
    protect = {lane_val} | (protect_lanes or set())
    fin_idx = set(fin_idx)
    for cand in bench_pool:
        if deficit <= 0:
            break
        removable = [i for i in fin_idx if lane.loc[i] not in protect and conv.loc[i] < force_in]
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
    """Merge nominated L3 rows after hard eligibility, then apply soft diversity.

    Order: identity/tradability -> current guard/chasing/structured B/E veto ->
    nonholding eligibility and conviction -> ins75 and cap -> eligible replacement,
    lane quotas and soft sector cap -> explicit explanation columns. The thresholds
    retain their configured values; high conviction/lane protections are soft sector
    exceptions and never hard-veto exemptions.

    Every admission path uses candidate_eligible. Nominated chasing slots may be
    filled from qualified bench rows; inadequate bench leaves slots empty. Historical
    rows without finalist retain the original all-nominated fallback, and missing
    v2 veto fields remain UNKNOWN without prose inference. Duplicate normalized codes
    keep the first row; later rows remain in bench with guard=dup.

    Returns finalists/bench preserving the row partition. Pinned mandatory research
    is injected separately by write_finalists and never consumes nonholding seats.
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
    if "guard" not in m.columns:
        m["guard"] = ""
    else:
        m["guard"] = m["guard"].fillna("")
    from autoresearch.scan.l3.validation import l3_veto_status
    m["veto_status"] = [l3_veto_status(row) for row in m.to_dict("records")]
    m["sector_cap_exception"] = ""

    # I-1 去重:同码(zfill 后)只留第一次出现,其余整行摘出 → 落 bench 记 guard="dup"。
    # 摘出发生在 conviction/fragility/pct_60d 数值化**之后**,保证 dup_rows 与其余账本行
    # 同口径(数值列已转 float,非原始字符串/int)。
    dup_mask = m["code"].duplicated(keep="first")
    dup_rows = m.loc[dup_mask].copy()
    dup_rows["guard"] = "dup"
    m = m.loc[~dup_mask].reset_index(drop=True)

    g = guards_cfg()
    conv = m["conviction"].fillna(0.0)

    if "finalist" in m.columns:
        sel = m["finalist"].fillna(False).astype(bool).copy()
    else:                                       # 缺列向后兼容:全体皆候选
        sel = pd.Series(True, index=m.index)

    exclusions = pd.Series([_exclusion_reason(m.loc[i], g) for i in m.index], index=m.index)
    m.loc[exclusions.ne(""), "guard"] = exclusions[exclusions.ne("")]
    eligible = pd.Series([
        candidate_eligible(m.loc[i], qualify_conv=g["conv_min"], g=g) for i in m.index
    ], index=m.index, dtype=bool)
    ins75 = (conv >= g["conv_force_in"]) & ~sel & eligible
    m.loc[ins75, "guard"] = "ins75"
    sel |= ins75
    lt55 = sel & (conv < g["conv_min"]) & exclusions.eq("")
    m.loc[lt55, "guard"] = "lt55"

    # Remember nominated chasing slots before excluding them, preserving the existing
    # drop-and-backfill behavior without allowing a hard reject through the seat cap.
    chase_victims = list(m.index[sel & exclusions.eq("chase_1d") & (conv >= g["conv_min"])])
    sel &= eligible
    order = sorted(m.index[sel], key=lambda i: conv.loc[i], reverse=True)
    for i in order[cap:]:
        m.loc[i, "guard"] = "cap"
    fin_idx = set(order[:cap])
    chase_victims = chase_victims[:max(0, cap - len(fin_idx))]
    fin_idx = _drop_and_backfill(m, conv, fin_idx, chase_victims,
                                "chase_1d", "chase_backfill", sector_cap=g["sector_cap"])

    n = len(fin_idx)
    # 守卫④ healthy 配额:2026-08-22 起 HEALTHY_QUOTA_FRAC=0 → target 0 → 不动作(回滚改常量)。
    fin_idx = _swap_lane_quota(m, conv, fin_idx, "healthy",             # 守卫④
                               _healthy_quota(n, g["healthy_quota_frac"]), "healthy_quota")
    fin_idx = _swap_lane_quota(m, conv, fin_idx, "trend", g["lane_floors"]["trend"], "trend_quota",   # 守卫⑤
                               protect_lanes=_guarded_lanes_before("trend", g["healthy_quota_frac"]))   # I-2:不可换出已配置的配额行
    fin_idx = _swap_lane_quota(m, conv, fin_idx, "lowturn", g["lane_floors"]["lowturn"], "lowturn_quota",   # 守卫⑥
                               qualify_conv=g["qualify_conv"]["lowturn"],
                               protect_lanes=_guarded_lanes_before("lowturn", g["healthy_quota_frac"]))

    # 守卫⑧ sector_cap(2026-08-22 批 C):同 `sector` 至多 L3_SECTOR_CAP 席,超出剔最弱 + 回填异行业。
    # 2026-08-21:贵金属(12 只成分的申万二级)拿 4 席 + 下游饰品 1 席 = 5/9,而 L3/merge 此前
    # **一个行业帽也没有**(L2 有 sector_cap 20%)。L3 自己在每只的 risk 段都写了「同一动量被
    # 多路重复计数」,却没有任何机制阻止它;E6 于是在「金价 beta 里挑最不差的」。
    # 可剔判据:conviction<75(ins75 保护)∧ 剔掉后该行 lane 的配额仍满足 —— 保护的是**配额**
    # 不是每一行(当日贵金属 4 席里 3 席 lane=healthy,整 lane 免剔则帽子永远不咬)。
    # 回填还须不把补进来的票自己所在 sector 顶破帽。`sector` 列缺 → no-op(parity)。
    fin_idx = _apply_sector_cap(m, conv, fin_idx, g["sector_cap"])

    m["new_buy_eligible"] = [
        candidate_eligible(m.loc[i], qualify_conv=g["conv_min"], g=g) for i in m.index
    ]
    fin_order = sorted(fin_idx, key=lambda i: conv.loc[i], reverse=True)
    fin = m.loc[fin_order].copy()
    fin["ticker"] = fin["code"]
    cols = ["ticker", "code", "name", "sector", "lenses", "conviction",
            "triage_lean", "triage_reason", "thesis", "mechanism", "risk", "catalyst",
            "lane", "sentiment", "guard", "schema_version", "veto_reasons", "veto_status",
            "sector_cap_exception", "new_buy_eligible"]
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
        hit = judged_z[judged_z["code"] == code] if judged_z is not None else None
        if hit is not None and len(hit) and not candidate_eligible(hit.iloc[0]):
            continue
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
            row = {**{k: v for k, v in r0.items() if _present(v)}, **row}
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
            out.loc[m, "new_buy_eligible"] = False
            continue
        if code in seen_new:                      # 同票重复 pin 条目(用户笔误)→ 只注一次
            continue
        seen_new.add(code)
        row_data: dict = {"code": code, "ticker": code, "lane": "pinned",
                          "pinned_note": note, "data_missing": True, "new_buy_eligible": False}
        # ① judged 优先:pinned 被 L3 判过但未入选(finalist=false → 落 bench,不在 `fin`)时,
        #    它的 thesis/risk/catalyst/conviction 就在 judged 帧里 —— 必须整段带过来。
        #    2026-07-12 生产实测:4/4 保送持仓都走这条路,此前只查 L2(无这些列)→ L3 判断被
        #    整段丢弃 → finalists.csv 空 thesis → summary 渲染「风险:;催化:」→ L4 prompt
        #    告诉卡片「pinned 无 L3 前提清单」,卡只好自己从 L1 重建前提。L3 的活白干。
        hit_j = judged_z[judged_z["code"] == code] if judged_z is not None else None
        if hit_j is not None and len(hit_j):
            r0 = hit_j.iloc[0].to_dict()
            r0.pop("finalist", None)              # finalist 是 judged 内部字段,不进 finalists.csv
            row_data = {**{k: v for k, v in r0.items() if _present(v)}, **row_data}
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
    (finalists, bench)→ 守卫⑨ composite 席位注入(`inject_composite_seats`)→ 行业席位
    guard 留痕(2026-09-24 §2.3;presence-gated:无 `_sector_seats.json` → 不变;只标记
    已在场的行,不注入新行、不占 finalist 名额)→ pinned 强留(`_inject_pinned_finalists`,design 2026-07-11 §4.1;
    plan Task 4;presence-gated:无 pinned.json/kept 全空 → 不变,**在 v3 之后、不占
    finalist 名额**)→ bench 落 `_l3_bench.csv` **之前**先摘掉已被 pinned 注入进
    finalists 的码(M-1 修复:防止同票双记 bench 与 finalists,见 `refine_l3_bucket`/
    `l3_bench_shadow` 消费方)→ 写盘。**全程 6 位零填**,修 000062→62 的 CSV 往返坑。

    finalist tier 上限 = `scan/l4/card_count.effective_caps` 的 `finalist_cap`(= l4.max_cards −
    composite m;原 l3.finalist_max 2026-09-26 退役),v3 内再与 `budget`(GATE1 回显的 l3cap)取小。
    最后守卫⑩ `apply_max_cards`:非 📌 行(含席位)总数 ≤ l4.max_cards,超出截进 bench。

    返回 dict:`judged_n`/`finalists_n` 语义不变(`finalists_n` = 写盘 finalists.csv 的最终
    行数,含 pinned 追加);新增 `finalist_n`(v3 产出的 finalist tier 行数,**pinned 注入前**,
    即当日 L3 finalist tier 的真实大小)、`bench_n`(bench 行数)。
    """
    base = Path(root) if root else ws.scan_root()
    scan_dir = base / date
    source = Path(judged_path) if judged_path is not None else scan_dir / "_l3_judged.json"
    picks = json.loads(source.read_text(encoding="utf-8"))
    from autoresearch.scan.l3.validation import l3_veto_status, validate_rank_artifact
    validate_rank_artifact(picks, scan_dir)
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
    from autoresearch.scan.l4.card_count import effective_caps
    from autoresearch.scan.user_config import load_pinned, load_user_config
    caps = effective_caps(load_user_config(), budget)
    kept = load_pinned(date, path=pinned_path)["kept"]
    pinned_codes = {str(p["code"]).zfill(6) for p in kept}
    # Holdings enter only the mandatory research path, before seats/caps are counted.
    jd["pinned"] = jd["code"].isin(pinned_codes) | jd.apply(_is_pinned, axis=1)
    jd["veto_status"] = [l3_veto_status(row) for row in jd.to_dict("records")]
    fin, bench = merge_l3_finalists_v3(jd, budget=budget, finalist_max=caps["finalist_cap"])
    finalist_n = int(len(fin))

    # 守卫⑨ composite 席位(2026-08-26 §3 路A):在 v3 全部守卫**之后**、pinned 注入**之前**
    # 注入 —— 与 📌 同级的直通车,不占 finalist 名额(但计入 l4.max_cards,守卫⑩)、不参与 v3 cap 截尾。放在 pinned 之前是为了
    # 让 pinned 的「已在场就只改判 lane」逻辑仍能覆盖同码情形(📌 优先级更高)。
    seats: list[dict] = []
    seat_m = caps["seat_m"]        # = composite m,卡数压到 ≤ m 时让位到 max_cards − 1(card_count)
    if seat_m > 0:
        hard_excluded = {str(row["code"]) for _, row in jd.iterrows()
                         if not candidate_eligible(row)}
        seats = pick_composite_seats(l2, seat_m,
                                     exclude={str(c) for c in fin.get("code", [])}
                                     | pinned_codes | hard_excluded)
        fin = inject_composite_seats(fin, seats, judged=jd)
        if len(seats):
            bench = bench[~bench["code"].astype(str).isin({s["code"] for s in seats})
                          ].reset_index(drop=True)
    seat_n = int(len(seats))

    # 行业席位 guard 留痕(2026-09-24 §2.3):与守卫⑨ composite 席位**不同**——这里不注入新行、
    # 不强制送进 finalists(①c pass1 强留只保证 l3-rank 判到它们,B 条照常适用、不抬评级)。
    # 只对**已经在场**(l3-rank 自己判了 finalist=True)的行打 `guard="sector_seat"` 留痕,
    # 供下游(账本 role/L4 prompt)识别「这只 finalist 恰好也是行业席位」。**不覆盖**已有的
    # 更具体标记(pinned 那次事故的同一条纪律:guard 说的是「它怎么进来的」,已有值优先)。
    # 局部导入(而非模块顶层):`sector_seats.py` 反向 `from autoresearch.scan.l3.merge import
    # CHASE_1D_PCT, _is_st`——模块顶层互相导入会循环失败,镜像 `universe._inject_sector_seats_l1`
    # 调用点同款局部导入(同一个循环边)。
    from autoresearch.scan.sector_seats import SECTOR_SEAT_GUARD, SECTOR_SEATS_FILENAME
    seats_doc = scan_dir / SECTOR_SEATS_FILENAME
    if seats_doc.exists():                                  # 行业席位 guard 留痕(2026-09-24 §2.3)
        with contextlib.suppress(Exception):
            sector_codes = {str(s["code"]).zfill(6) for s in
                            (json.loads(seats_doc.read_text(encoding="utf-8")).get("seats") or [])}
            if sector_codes and "code" in fin.columns:
                if "guard" not in fin.columns:
                    fin["guard"] = ""
                m = fin["code"].astype(str).str.zfill(6).isin(sector_codes) & (fin["guard"].fillna("") == "")
                fin.loc[m, "guard"] = SECTOR_SEAT_GUARD

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
    # 守卫⑩ max_cards(2026-09-26):席位与 📌 都注入之后、写盘之前 —— 最后一道,保证「最终进 L4
    # 卡的非 📌 票数」≤ l4.max_cards,无论前面哪条直通车加了行。
    fin, bench, max_cards_cut = apply_max_cards(fin, bench, caps["max_cards"])
    bench_n = int(len(bench))
    def write_csv(frame, path):
        output = frame.copy()
        if "veto_reasons" in output.columns:
            output["veto_reasons"] = output["veto_reasons"].map(
                lambda value: json.dumps(value, ensure_ascii=False, separators=(",", ":"))
                if isinstance(value, list) else value)
        output.to_csv(path, index=False)

    write_csv(jd, scan_dir / "L3_judged_full.csv")
    write_csv(bench, scan_dir / "_l3_bench.csv")
    write_csv(fin, scan_dir / "finalists.csv")
    with contextlib.suppress(Exception):
        from autoresearch.scan.stock_stage import record_l3_results

        record_l3_results(scan_dir)
    return {"judged_n": int(len(jd)), "finalists_n": int(len(fin)),
            "finalist_n": finalist_n, "bench_n": bench_n,
            # 守卫⑨ 的**会变的量**:席位这条腿死了也像活着(同族配方:自动的腿必须有一个
            # 会变的量做断言)。0 = 关了、或当日 L2 表缺 composite 列、或全被追高/ST 剔光。
            "composite_seat_n": seat_n,
            "composite_seats": [s["code"] for s in seats],
            # 守卫⑩ 的会变的量:配置的上限 + 本次截掉几只(0 = 没超,不是没跑)。
            "max_cards": caps["max_cards"], "max_cards_cut_n": len(max_cards_cut),
            "max_cards_cut": max_cards_cut}


def apply_max_cards(fin: pd.DataFrame, bench: pd.DataFrame, max_cards: int
                    ) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    """守卫⑩:非 📌 行(含 composite 席位)总数 ≤ `max_cards`;未超 → 原样返回(parity)。

    保留序:席位优先(证据层直通、E6 候选池依赖)→ conviction 降序(缺 → 最末)→ 原行序;
    被截行 `guard="max_cards"` 追加进 bench。📌 行恒保留、不占额。返回 (fin, bench, 截掉的码)。
    """
    if fin.empty or "code" not in fin.columns:
        return fin, bench, []
    lane = (fin["lane"].fillna("").astype(str) if "lane" in fin.columns
            else pd.Series("", index=fin.index))
    pinned = lane.eq("pinned")
    others = fin.index[~pinned]
    if len(others) <= int(max_cards):
        return fin, bench, []
    guard = (fin["guard"].fillna("").astype(str) if "guard" in fin.columns
             else pd.Series("", index=fin.index))
    conv = (pd.to_numeric(fin["conviction"], errors="coerce").fillna(-1.0)
            if "conviction" in fin.columns else pd.Series(-1.0, index=fin.index))
    order = pd.DataFrame({"seat": guard.eq(COMPOSITE_SEAT_GUARD).astype(int), "conv": conv,
                          "pos": range(len(fin))}, index=fin.index).loc[others]
    order = order.sort_values(["seat", "conv", "pos"], ascending=[False, False, True])
    keep = pinned | fin.index.isin(order.index[: int(max_cards)])
    cut = fin.loc[~keep].copy()
    cut["guard"] = MAX_CARDS_GUARD
    bench = cut.reset_index(drop=True) if bench.empty else pd.concat([bench, cut], ignore_index=True)
    return (fin.loc[keep].reset_index(drop=True), bench,
            [str(c).zfill(6) for c in cut["code"]])
