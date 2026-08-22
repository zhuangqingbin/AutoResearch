"""L3 deterministic pass-1 triage."""
from __future__ import annotations

import pandas as pd

# selection_reason 词表 —— **与 L2 共用一套**(design 2026-08-03 §3.1/§4.4)。
# 为什么共用:O1 的 SLO 守卫要按 selection_reason 切 L2,§4.4 的 tier-1 反事实要按同一套
# 语义在 pass1 选择集内构造 lane/sector 匹配的 baseline;两层各造一套词表 = 两边的
# 「lane」不是同一件事,反事实一开始就配错了对。
#
# 各值在本层的产生规则(L2 侧见 `recall/l2_stratify.py`):
#   pinned            规则① 保送(全程直通,不占竞争名额)
#   conviction_guard  规则② 多路共振(n_channels>=3)top-RESONANCE_CAP —— 强制补入,不是排序结果
#   lane              规则③ healthy 全入(默认关,HEALTHY_MANDATORY)/ 规则④ 通道轮询(detail 记具体通道名)
#   backfill          填满收尾(无通道 / 通道队列耗尽后按分捡回)
#   merit / sector    L2 侧才产生(sn-composite 核 / sector cap 回填),本层恒不出现
SELECTION_REASONS = ("merit", "lane", "sector", "pinned", "conviction_guard", "backfill")
PASS1_REASONS = ("pinned", "conviction_guard", "lane", "backfill")
RULE_VERSION = "pass1.v2"        # v2 = 本波新增 selection_reason/detail;规则本身未变


# 多路共振强留上限(2026-08-22 批 C)。规则② 原为「n_channels>=3 **全入**」,2026-08-21 实测
# 那是 10/40 席、且 5 只 finalist 出自其中。改 top-5 的理由是数学:L2 200 里 n_channels 与
# `dist_high_60` 的 spearman **0.34**、与 `pct_60d` 0.29 —— ≥3 路的 10 只中位 rsi6 72–76、
# pct_5d +12%,而 1 路的 129 只中位 pct_60d −20.2%。momentum/heat/healthy/main_fund/growth
# 五路**在强势上涨时同时亮**,低位票至多命中 value/reversal/composite 三路,所以「多路共振」
# 在数学上 ≈「这票已经涨起来了」,不是独立多因子确认。共振票不再免检,改为与其他 lane 竞争
# round-robin(不是被切,是不再免检)。回滚杆 = 改成一个很大的数(如 10**6)。
RESONANCE_CAP = 5
# healthy lane 是否在 pass1 强留(2026-08-22 批 (a),用户裁定「三处强制降为不强制」)。
# 证据:edge 普查(docs/research/2026-08-22-edge-census.md)—— healthy 画像在三把尺上全负
# (L1·healthy 隔夜 −0.37pp t=−5.58、fwd_10 −4.76pp t=−5.5;L3·lane·healthy −0.38 t=−3.56),
# 而它此前被三处强制(本规则③全入 / merge 守卫④ ceil(n/3) 配额 / l3-rank 硬约束 A ≥1/3 席)。
# 2026-08-21 实测规则③一项就占 14/40 席。关掉 = healthy 与其他 lane 一样走规则④通道轮询
# (L1 路 quota 112 / L2 健康桶 floor 15 不动,它仍「可选」,只是不再「必选」)。
# 回滚杆 = 改 True(一行)。
HEALTHY_MANDATORY = False


def triage_l2_for_l3(df: pd.DataFrame, target: int = 60, *, lowturn_cap: int = 0,
                     lowturn_cfg: dict | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
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
    ② 多路共振**按 composite 取前 `RESONANCE_CAP` 只**强留(2026-08-22 由「全入」收窄,
       理由见该常量注释):`n_channels >= 3`(真实列,直接可用)。列缺失(如 `recall_mode="composite"`
       的 L2,无 provenance 列)→ 跳过本规则,不报错。
    ③b lowturn 强留(2026-08-21 低位转强波 §6.2):`lowturn_cap>0` 时,`turnup.lowturn_mask`
       为真且尚未 mandatory 的行按 `order` 降序取前 `cap` 入 mandatory(`selection_reason=lane`、
       `selection_detail=lowturn`);`cap<=0`(默认)= 现行为逐字 parity。谓词/阈值真身在
       `common/turnup.py`,不在这里重造判据。
    ③ healthy lane 全入(**默认关**,`HEALTHY_MANDATORY`;2026-08-22 起 healthy 走④轮询):
       `recall_channels` 按 `"|"` 拆分后的集合包含 `"healthy"`(已注册召回
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
    不裁列——`_l3_pass1_cut.csv`/下游 attribution 都可能要用到里面的列)—— **除了**
    `selection_reason`/`selection_detail` 这两列本身(见下段 I-5)。

    **`kept` 另加两列**(design 2026-08-03 §4.4;`cut` 不加,它没有"为什么被选中"可言):
    `selection_reason`(见 `PASS1_REASONS`)+ `selection_detail`(lane 记通道名、共振记
    n_channels)。为什么必须留:pass1 是 **union/floor/round-robin 的选择集,不是一条确定性
    排名** —— 只有 `_l3_pass1_cut.csv` 的话,根本无法构造"同样选 K 只、但按 lane/sector
    匹配"的反事实基线(§4.4「现有 cut 不能天然定义 top-K 反事实」)。多规则同时命中时按
    `pinned > conviction_guard > lane` 记**第一个**,理由与"占名额"的口径一致。

    I-5(final-review 2026-08-08/09):`df`(= `load_l3_input(date)` 的输出)T16 之后自带
    **L2 口径**的 `selection_reason`/`selection_detail`(`merit`/`sector` 等,L2 侧词表,
    见模块头注释)——若不处理,`kept` 侧会被本函数就地覆盖成 pass1 口径(`:167-168`,正确),
    但 `cut = d.loc[cut_idx]` 会**原样带出**L2 口径的旧值,与本段"cut 不加"的说明矛盾:
    `cut` 侧凭空多出一列 L2 语义的 reason,`pd.concat([kept, cut])` 按 `selection_reason`
    分组会把两层语义(`merit`/`sector` 只可能来自 L2、`pinned`/`conviction_guard`/`lane`/
    `backfill` 只可能来自 pass1)混成一个分布。修法:落盘前把这两列从 `cut` 显式 drop
    掉(不改名保留——L2 口径对 cut 侧消费者当前无场景价值,drop 让代码与本段文档字面
    一致,比新发明一套 `l2_selection_reason` 列名更省心)。

    边界:`df` 为空 → 两个都空(kept 仍带两列,免得下游按列名读时炸)。
    `target >= len(df)` → kept=全量,cut=空。
    """
    if df.empty:
        empty = df.copy()
        empty["selection_reason"] = pd.Series(dtype=str)
        empty["selection_detail"] = pd.Series(dtype=str)
        return empty, df.copy()

    d = df.reset_index(drop=True).copy()
    if "code" in d.columns:
        d["code"] = d["code"].astype(str).str.zfill(6)

    score_col = "gbdt_score" if "gbdt_score" in d.columns else ("composite" if "composite" in d.columns else None)
    order = (pd.to_numeric(d[score_col], errors="coerce").fillna(-1e18)
            if score_col else pd.Series(0.0, index=d.index))

    reasons: dict[int, tuple[str, str]] = {}     # 行 → (selection_reason, selection_detail)

    def _mark(idx, reason: str, detail: str = "") -> None:
        reasons.setdefault(idx, (reason, detail))   # 先到先得 = 优先级顺序,见 docstring

    is_pinned = pd.Series(False, index=d.index)                          # ① pinned 全入
    if "pinned" in d.columns:
        is_pinned |= d["pinned"].map(lambda v: bool(v) if pd.notna(v) else False)
    elif "recall_channels" in d.columns:
        is_pinned |= d["recall_channels"].astype(str) == "pinned"
    mandatory = is_pinned.copy()
    for i in d.index[is_pinned]:
        _mark(i, "pinned")

    if "n_channels" in d.columns:                        # ② 多路共振 top-RESONANCE_CAP 强留
        n_ch = pd.to_numeric(d["n_channels"], errors="coerce").fillna(0)
        resonant = [i for i in d.index[n_ch >= 3] if not mandatory.loc[i]]
        resonant.sort(key=lambda i: order.loc[i], reverse=True)
        for i in resonant[:RESONANCE_CAP]:
            mandatory.loc[i] = True
            _mark(i, "conviction_guard", f"n_channels={int(n_ch.loc[i])}")

    chan_sets = None
    if "recall_channels" in d.columns:
        chan_sets = d["recall_channels"].fillna("").astype(str).map(lambda s: set(s.split("|")) - {""})
        if HEALTHY_MANDATORY:                                            # ③ healthy lane 全入(2026-08-22 起默认关)
            healthy = chan_sets.map(lambda s: "healthy" in s)
            mandatory |= healthy
            for i in d.index[healthy]:
                _mark(i, "lane", "healthy")

    if lowturn_cap > 0:                                                  # ③b lowturn 强留(≤cap)
        from autoresearch.common.turnup import LOWTURN_DEFAULTS, lowturn_mask
        try:
            lt = lowturn_mask(d, {**LOWTURN_DEFAULTS, **(lowturn_cfg or {})})
        except Exception:  # noqa: BLE001 — 旗算不出(列缺/坏值)就不强留,不挡 pass1
            lt = pd.Series(False, index=d.index)
        cand = [i for i in d.index[lt.reindex(d.index).fillna(False).astype(bool)]
                if not mandatory.loc[i]]
        cand.sort(key=lambda i: order.loc[i], reverse=True)
        for i in cand[:int(lowturn_cap)]:
            mandatory.loc[i] = True
            _mark(i, "lane", "lowturn")

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
                    _mark(q[p], "lane", c)
                    remaining -= 1
                    progressed = True
                    p += 1
                ptrs[c] = p
    if remaining > 0:                                                     # 填满收尾(无通道/耗尽的剩余票)
        leftover = [i for i in d.index if i not in kept_set]
        leftover.sort(key=lambda i: order.loc[i], reverse=True)
        for i in leftover[:remaining]:
            kept_set.add(i)
            _mark(i, "backfill")

    kept_idx = sorted(kept_set)
    cut_idx = [i for i in d.index if i not in kept_set]
    kept = d.loc[kept_idx].reset_index(drop=True)
    # 理由按**原始行索引**取,不能按 reset 后的位置 —— 两者只在 kept 恰为前缀时才相等。
    kept["selection_reason"] = [reasons.get(i, ("backfill", ""))[0] for i in kept_idx]
    kept["selection_detail"] = [reasons.get(i, ("backfill", ""))[1] for i in kept_idx]
    # I-5(final-review 2026-08-08/09):`d`(= df)T16 之后可能自带 L2 口径的
    # selection_reason/selection_detail(见上方 docstring)——cut 侧显式 drop,不带出去,
    # 保持"cut 没有『为什么被选中』可言"这句文档字面为真。`errors="ignore"`:df 本身没有
    # 这两列(旧 load_l3_input / 合成测试帧)时不报错,drop 是纯粹的防御性清理。
    cut = d.loc[cut_idx].reset_index(drop=True).drop(
        columns=["selection_reason", "selection_detail"], errors="ignore")
    return kept, cut


def pass1_meta(df_in: pd.DataFrame, kept: pd.DataFrame, cut: pd.DataFrame,
               target: int) -> dict:
    """当日 pass1 的自描述元数据 —— §4.4「固定当日 K、quota、pinned 和强制补入语义」。

    `n_kept` 与 `target` 可以不等(mandatory 超 target 时 pinned 优先保留,kept 允许略超),
    所以两个都记:tier-1 反事实要用**实际 K**,不是配置里的 target。
    """
    counts = ({} if not len(kept) or "selection_reason" not in kept.columns
              else kept["selection_reason"].value_counts().to_dict())
    return {
        "rule_version": RULE_VERSION,
        "target": int(target),
        "n_in": int(len(df_in)),
        "n_kept": int(len(kept)),
        "n_cut": int(len(cut)),
        "reason_counts": {str(k): int(v) for k, v in counts.items()},
        "forced_in": int(sum(int(v) for k, v in counts.items()
                             if k in ("pinned", "conviction_guard"))),
    }
