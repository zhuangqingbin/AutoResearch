"""L3 finalist merge, pinned injection, and artifact publication."""
from __future__ import annotations

import contextlib
import json
import math
from pathlib import Path

import pandas as pd


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
    from autoresearch.learning.l3_audit_ledger import write_audit_candidates

    audit_path = write_audit_candidates(
        scan_dir,
        finalist_max=finalist_max,
    )
    audit_n = len(pd.read_csv(audit_path, dtype={"code": str}))
    import contextlib
    with contextlib.suppress(Exception):
        from autoresearch.scan.stock_stage import record_l3_results

        record_l3_results(scan_dir)
    return {"judged_n": int(len(jd)), "finalists_n": int(len(fin)),
            "finalist_n": finalist_n, "bench_n": bench_n, "audit_n": audit_n}
